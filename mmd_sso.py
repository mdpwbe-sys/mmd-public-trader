#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_sso.py - OAuth2 ESI multi-comptes (jusqu'a 3 persos) + esi-ui.open_window.v1.

Chaque personnage = 1 token (OAuth2 EVE = 1 character / token).
On stocke un dict {character_id: {name, access_token, refresh_token, expires_at}}
dans .env.cache. Au login, on appelle /verify pour recuperer le nom du perso
et on l'ajoute a la liste.

Scopes demandes (lecture seule, a enregistrer dans l'app CCP):
  esi-ui.open_window.v1
  esi-markets.read_character_orders.v1
  esi-markets.structure_markets.v1
  esi-universe.read_structures.v1
  esi-wallet.read_character_wallet.v1
  esi-markets.read_corporation_orders.v1
  esi-wallet.read_corporation_wallets.v1
  esi-assets.read_assets.v1
  esi-assets.read_corporation_assets.v1
  esi-contracts.read_character_contracts.v1
  esi-contracts.read_corporation_contracts.v1
  esi-corporations.read_divisions.v1
  esi-characters.read_blueprints.v1
  esi-characters.read_standings.v1
  esi-skills.read_skills.v1
  esi-location.read_location.v1
"""
import os, json, time, threading, base64, hashlib, html, secrets
import urllib.parse, urllib.request, urllib.error
import mmd_crypto
from http.server import BaseHTTPRequestHandler, HTTPServer
from platform_state import state_path

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = state_path(".env")
CACHE = state_path(".env.cache")

SCOPES = [
    "esi-ui.open_window.v1",
    "esi-markets.read_character_orders.v1",
    "esi-markets.structure_markets.v1",
    "esi-universe.read_structures.v1",
    "esi-wallet.read_character_wallet.v1",
    "esi-markets.read_corporation_orders.v1",
    "esi-wallet.read_corporation_wallets.v1",
    "esi-assets.read_assets.v1",
    "esi-assets.read_corporation_assets.v1",
    "esi-contracts.read_character_contracts.v1",
    "esi-contracts.read_corporation_contracts.v1",
    "esi-corporations.read_divisions.v1",
    "esi-characters.read_blueprints.v1",
    "esi-characters.read_standings.v1",
    "esi-skills.read_skills.v1",
    "esi-location.read_location.v1",
]

_login_result = {}
_server = None
_oauth_session = {}
_oauth_lock = threading.Lock()


_OAUTH_TTL_SECONDS = 600


def _load_env():
    cfg = {}
    if os.path.exists(ENV):
        with open(ENV, encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def _load_cache():
    if os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as cache_file:
                raw = cache_file.read()
            data, used_legacy_key = mmd_crypto.decrypt_json_with_status(raw)
            if used_legacy_key:
                _save_cache(data)
            return data
        except Exception:
            return {}
    return {}


def _save_cache(data):
    tmp = f"{CACHE}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        raw = mmd_crypto.encrypt_json(data)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CACHE)
    except Exception:
        pass
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _chars():
    return _load_cache().get("characters", {})


def is_connected(char_id=None):
    cs = _chars()
    if char_id is None:
        return bool(cs)
    return str(char_id) in cs and bool(cs[str(char_id)].get("access_token"))


REQUIRED_SCOPES = [
    "esi-ui.open_window.v1",
    "esi-markets.read_character_orders.v1",
    "esi-characters.read_standings.v1",
    "esi-skills.read_skills.v1",
]

# Les fonctions portefeuille sont optionnelles : un token peut rester valable
# pour le scan d'ordres meme si le personnage n'a pas les roles corporation.
CAPABILITY_SCOPES = {
    "character_wallet": {"esi-wallet.read_character_wallet.v1"},
    "corporation_wallet": {"esi-wallet.read_corporation_wallets.v1"},
    "corporation_orders": {"esi-markets.read_corporation_orders.v1"},
    "character_assets": {"esi-assets.read_assets.v1"},
    "corporation_assets": {"esi-assets.read_corporation_assets.v1"},
    "character_contracts": {"esi-contracts.read_character_contracts.v1"},
    "corporation_contracts": {"esi-contracts.read_corporation_contracts.v1"},
    "corporation_divisions": {"esi-corporations.read_divisions.v1"},
    "character_location": {"esi-location.read_location.v1"},
}


def _scope_set(char_data):
    """Normalise les scopes du cache SSO sans comparaison par sous-chaine."""
    if not char_data:
        return set()
    value = char_data.get("scopes", "")
    if isinstance(value, (list, tuple, set)):
        return {str(scope).strip() for scope in value if str(scope).strip()}
    return {scope for scope in str(value or "").split() if scope}


def scope_capabilities(char_data):
    """Retourne les capacites read-only effectivement consenties au token."""
    granted = _scope_set(char_data)
    return {
        name: required.issubset(granted)
        for name, required in CAPABILITY_SCOPES.items()
    }


def character_capabilities(char_id):
    """Capacites portefeuille d'un personnage present dans le cache SSO."""
    return scope_capabilities(_chars().get(str(char_id)))


def check_scopes_ok(char_data):
    """Verifie si les scopes necessaires sont presents et valides pour ce perso."""
    if not char_data:
        return False
    granted = _scope_set(char_data)
    if not granted:
        return bool(char_data.get("access_token"))
    return set(REQUIRED_SCOPES).issubset(granted)


def connected_chars():
    """Retourne [{id, name, scopes_ok}] des persos connectes."""
    cs = _chars()
    out = []
    for cid, c in cs.items():
        if not str(c.get("access_token") or "").strip():
            continue
        s_ok = check_scopes_ok(c)
        out.append({
            "id": int(cid),
            "name": c.get("name", f"Perso {cid}"),
            "scopes_ok": s_ok,
        })
    return out


def disconnect_char(char_id):
    """Deconnecte un seul personnage (supprime du cache local)."""
    cache = _load_cache()
    chars = cache.get("characters", {})
    cid_str = str(char_id)
    if cid_str in chars:
        del chars[cid_str]
        cache["characters"] = chars
        _save_cache(cache)
        return True
    return False


def revoke_char(char_id):
    """Revoque les acces ESI d'un seul personnage cote CCP puis supprime du cache local."""
    cfg = _load_env()
    cid = cfg.get("CLIENT_ID", "")
    secret = cfg.get("CLIENT_SECRET", "")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    chars = _chars()
    cid_str = str(char_id)
    c = chars.get(cid_str)
    if c:
        rt = c.get("refresh_token")
        if rt:
            data = urllib.parse.urlencode({
                "token": rt,
                "token_type_hint": "refresh_token",
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://login.eveonline.com/v2/oauth/revoke",
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {basic}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    pass
            except Exception:
                pass
    disconnect_char(char_id)
    return True


def disconnect_and_clear():
    """Supprime TOUTES les donnees SSO (tokens + refresh) du cache local.
    Force un re-consent complet au prochain login -> necessaire pour activer
    un nouveau scope (ex: esi-universe.read_structures.v1 pour les citadelles)."""
    global _login_result
    try:
        if os.path.exists(CACHE):
            os.remove(CACHE)
        return True
    except Exception:
        return False


def revoke_all():
    """Revoque COTE ESI tous les refresh tokens connectes (POST
    /v2/oauth/revoke), puis supprime le cache local. Retourne
    {"revoked": n, "failed": m}. Meme si ESI est injoignable, le cache local
    est TOUJOURS supprime (on ne veut plus de tokens valides nulle part)."""
    cfg = _load_env()
    cid = cfg.get("CLIENT_ID", "")
    secret = cfg.get("CLIENT_SECRET", "")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    chars = _chars()
    revoked = 0
    failed = 0
    for _cid_str, c in chars.items():
        rt = c.get("refresh_token")
        if not rt:
            continue
        data = urllib.parse.urlencode({
            "token": rt,
            "token_type_hint": "refresh_token",
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://login.eveonline.com/v2/oauth/revoke",
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200:
                    revoked += 1
                else:
                    failed += 1
        except Exception:
            failed += 1
    # toujours supprimer le cache local (meme si ESI down)
    disconnect_and_clear()
    return {"revoked": revoked, "failed": failed}


def _callback_url():
    cfg = _load_env()
    cb = cfg.get("CALLBACK_URL", "http://127.0.0.1:8766/callback")
    if "localhost" in cb:
        cb = cb.replace("localhost", "127.0.0.1")
    return cb


def _new_oauth_session():
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    callback_url = _callback_url()
    with _oauth_lock:
        _oauth_session.clear()
        _oauth_session.update({
            "state": state,
            "code_verifier": verifier,
            "callback_url": callback_url,
            "created_at": time.time(),
        })
    return state, challenge, callback_url


def _consume_oauth_session(received_state):
    if not received_state:
        return None
    with _oauth_lock:
        expected = _oauth_session.get("state")
        created_at = float(_oauth_session.get("created_at", 0))
        if (not expected or time.time() - created_at > _OAUTH_TTL_SECONDS
                or not secrets.compare_digest(received_state, expected)):
            return None
        verifier = _oauth_session.get("code_verifier")
        callback_url = _oauth_session.get("callback_url")
        _oauth_session.clear()
    return verifier, callback_url


def get_login_url():
    cfg = _load_env()
    cid = cfg.get("CLIENT_ID", "")
    if not cid:
        raise RuntimeError(
            "CLIENT_ID manquant. Copie .env.example -> .env et renseigne "
            "CLIENT_ID / CLIENT_SECRET depuis https://developers.eveonline.com "
            "(cree une application, callback http://127.0.0.1:8766/callback).")
    state, challenge, cb = _new_oauth_session()
    params = {
        "response_type": "code",
        "redirect_uri": cb,
        "client_id": cid,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return ("https://login.eveonline.com/v2/oauth/authorize/?" +
            urllib.parse.urlencode(params))


def _post_form(url, data, headers=None):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode("utf-8"),
        headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _verify(access_token):
    if not access_token:
        raise RuntimeError("OAuth verify: access token absent")
    req = urllib.request.Request(
        "https://login.eveonline.com/v2/oauth/verify",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def exchange_code(code, code_verifier, callback_url):
    cfg = _load_env()
    cid = cfg.get("CLIENT_ID", "")
    secret = cfg.get("CLIENT_SECRET", "")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    tok = _post_form(
        "https://login.eveonline.com/v2/oauth/token",
        {"grant_type": "authorization_code", "code": code,
         "redirect_uri": callback_url, "code_verifier": code_verifier},
        {"Authorization": f"Basic {basic}"})
    at = tok.get("access_token")
    if not at:
        raise RuntimeError("EVE n'a pas renvoye d'access_token: " + str(tok)[:200])
    info = _verify(at)
    char_id = str(info["CharacterID"])
    name = info["CharacterName"]
    chars = _chars()
    chars[char_id] = {
        "name": name,
        "access_token": at,
        "refresh_token": tok.get("refresh_token"),
        "expires_at": int(time.time()) + int(tok.get("expires_in", 3600)),
        "scopes": info.get("Scopes", ""),
    }
    cache = _load_cache()
    cache["characters"] = chars
    _save_cache(cache)
    return name


def _refresh(char_id):
    cfg = _load_env()
    cs = _chars()
    c = cs.get(str(char_id))
    if not c or not c.get("refresh_token"):
        return False
    cid = cfg.get("CLIENT_ID", "")
    secret = cfg.get("CLIENT_SECRET", "")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    try:
        tok = _post_form(
            "https://login.eveonline.com/v2/oauth/token",
            {"grant_type": "refresh_token", "refresh_token": c["refresh_token"]},
            {"Authorization": f"Basic {basic}"})
        c["access_token"] = tok.get("access_token", c["access_token"])
        c["refresh_token"] = tok.get("refresh_token", c["refresh_token"])
        c["expires_at"] = int(time.time()) + int(tok.get("expires_in", 3600))
        cs[str(char_id)] = c
        cache = _load_cache()
        cache["characters"] = cs
        _save_cache(cache)
        return True
    except Exception:
        return False


def _access_token(char_id):
    cs = _chars()
    c = cs.get(str(char_id))
    if not c:
        return None
    if int(c.get("expires_at", 0)) - 60 < int(time.time()):
        if not _refresh(char_id):
            return None
        c = _chars().get(str(char_id), {})
    return c.get("access_token")


def open_in_client(type_id, char_id=None):
    """Ouvre la fenetre market de l'item DANS le client EVE via ESI openwindow.
    char_id: perso a utiliser (le 1er connecte valide si None/invalide)."""
    cs = _chars()
    if not cs:
        return False, "not_connected"

    # Sanitise char_id (traite 'null', 'undefined', int vs str)
    target_cid = None
    if char_id is not None:
        cid_str = str(char_id).strip()
        if cid_str and cid_str not in ("null", "undefined", "None", "0"):
            if cid_str in cs:
                target_cid = cid_str
            else:
                # Cherche par nom ou sous-chaine
                for k, c in cs.items():
                    if str(k) == cid_str or c.get("name", "").lower() == cid_str.lower():
                        target_cid = str(k)
                        break

    if not target_cid:
        # Fallback sur le premier perso SSO connecte
        target_cid = next(iter(cs.keys()))

    at = _access_token(target_cid)
    if not at:
        # Si le token a echoue, tente un autre perso connecte s'il en existe
        for k in cs.keys():
            if str(k) != str(target_cid):
                at = _access_token(k)
                if at:
                    target_cid = str(k)
                    break

    if not at:
        return False, "token_expired"

    url = f"https://esi.evetech.net/v1/ui/openwindow/marketdetails/?type_id={int(type_id)}"
    req = urllib.request.Request(url, data=b"", headers={"Authorization": f"Bearer {at}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return True, f"HTTP {r.status} (perso {target_cid})"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            if _refresh(target_cid):
                return open_in_client(type_id, target_cid)
            return False, "token_expired"
        return False, f"HTTP {e.code}"


# ---- mini serveur local pour capturer le ?code= ----
class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        expected_path = urllib.parse.urlparse(_callback_url()).path
        if parsed.path != expected_path:
            self.send_error(404)
            return
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        err = params.get("error", [None])[0]
        oauth_data = _consume_oauth_session(params.get("state", [None])[0])
        status = 200
        if oauth_data is None:
            _login_result["ok"] = False
            _login_result["error"] = "invalid_state"
            status = 400
            body = (b"<html><body style='font-family:sans-serif;color:#ff6b6b'>"
                    b"<h2>Reponse OAuth refusee</h2></body></html>")
        elif code:
            try:
                verifier, callback_url = oauth_data
                name = exchange_code(code, verifier, callback_url)
                _login_result["ok"] = True
                _login_result["name"] = name
                body = (f"<html><body style='font-family:sans-serif;background:#05070d;"
                        f"color:#2ee6e6;text-align:center;padding-top:80px'>"
                        f"<h2>Connexion reussie : {html.escape(name)}</h2>"
                        f"<p>Vous pouvez fermer cet onglet et retourner dans Mmd Order Manager.</p>"
                        f"</body></html>").encode()
            except Exception as e:
                _login_result["ok"] = False
                _login_result["error"] = str(e)
                safe_error = html.escape(str(e))
                body = f"<html><body style='font-family:sans-serif;color:#ff6b6b'><h2>Erreur</h2><pre>{safe_error}</pre></body></html>".encode()
        else:
            _login_result["ok"] = False
            _login_result["error"] = err or "no_code"
            body = b"<html><body style='font-family:sans-serif;color:#ff6b6b'><h2>Connexion annulee</h2></body></html>"
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if oauth_data is not None:
            threading.Thread(target=_stop_server, daemon=True).start()

    def log_message(self, *a):
        pass


def _stop_server():
    time.sleep(0.3)
    if _server:
        try: _server.shutdown()
        except Exception: pass


def start_login_server():
    global _server
    cb = _callback_url()
    port = int(urllib.parse.urlparse(cb).port or 8766)
    _login_result.clear()
    try:
        auth_url = get_login_url()
    except RuntimeError as e:
        # pas de CLIENT_ID configure -> on n'ouvre PAS d'URL morte
        return False, str(e)
    _server = HTTPServer(("127.0.0.1", port), _Handler)
    import webbrowser
    webbrowser.open(auth_url)
    _server.serve_forever()
    return _login_result.get("ok", False), _login_result.get("error", "done")


if __name__ == "__main__":
    print("connectes:", [c["name"] for c in _chars().values()])
    if not _chars():
        print("login url:", get_login_url())
        ok, msg = start_login_server()
        print("login:", ok, msg)
