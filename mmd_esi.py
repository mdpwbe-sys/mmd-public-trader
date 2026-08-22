#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_esi.py - acces direct a l'ESI publique (sans token).

Regles CCP 2026 (developers.eveonline.com/docs/services/esi/rate-limiting):
  - User-Agent OBLIGATOIRE avec contact (sinon risque de ban)
  - ETag / If-None-Match + header Expires -> ne repas repoller un cache frais
  - Cooldown 5 min (cache ESI des order books) -> pas de fetch inutile
  - 429 -> Retry-After ; 420/5xx -> backoff exponentiel + jitter
  - pas de burst : threads limites + throttle par bucket

Endpoints:
  GET /latest/markets/{region}/orders/?type_id=X&order_type=all
"""
import urllib.request, urllib.error, json, sqlite3, os, time, random, threading
from concurrent.futures import ThreadPoolExecutor
from platform_state import state_path

# Operational DB lives in the persistent app state dir (APPDATA/MMD-Trader),
# not the legacy %LOCALAPPDATA%/mmd.com/Mmd path.
MAIN_DB = state_path("app_data.db")
REGION_THE_FORGE = 10000002
ESI = "https://esi.evetech.net/latest"

def _load_esi_env():
    cfg = {}
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except Exception:
        pass
    return cfg

_ESI_ENV = _load_esi_env()
# User-Agent CCP: nom + version + contact (obligatoire en pratique).
# The default is neutral; users set their own via ESI_USER_AGENT in .env.
UA = {"User-Agent": _ESI_ENV.get(
    "ESI_USER_AGENT",
    "EVE-Market-Manager/0.1.3 (contact: configure-your-user-agent)")}

# X-Compatibility-Date: date a laquelle l'appli a ete testee/revisee.
# CCP peut repondre avec la date demandee OU une date anterieure (selon la
# version interne applicable a la route) -> ce n'est PAS une erreur. On logge
# les deux separement. Le header est envoye des que ESI_VERSION_DATE est present
# dans .env ; on ne le desactive que si la variable est absente.
_COMPAT_DATE = _ESI_ENV.get("ESI_VERSION_DATE")
COMPAT_HEADER = {"X-Compatibility-Date": _COMPAT_DATE} if _COMPAT_DATE else {}

import logging
_log = logging.getLogger("mmd_esi")
if not _log.handlers:
    _log.addHandler(logging.NullHandler())

LIVE = "volume_remaining>0 AND last_seen IS NULL"
_MAX_WORKERS = 4  # conservateur: pas de burst vers le bucket public
_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".esi_cache.json")
_CACHE_TTL = 300  # 5 min: fraicheur min des order books ESI
_CACHE_STALE_TTL = 24 * 3600  # un livre ancien vaut mieux qu'un faux marche vide
_cache = {}
_cache_lock = threading.Lock()


def _load_cache():
    global _cache
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    # Garde 24 h de secours : une entree expiree n'est servie que si ESI est
    # temporairement indisponible, jamais a la place d'une reponse live vide.
    now = time.time()
    _cache = {k: v for k, v in _cache.items()
              if v.get("expires", 0) > now - _CACHE_STALE_TTL}


def _save_cache():
    tmp = f"{_CACHE_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with _cache_lock:
            snapshot = dict(_cache)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _CACHE_FILE)
    except Exception:
        pass
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _stale_or_unavailable(cached, url, reason):
    """Sert le dernier snapshot connu sur incident transitoire uniquement."""
    if cached and cached.get("data") is not None:
        _log.warning("ESI indisponible (%s), snapshot expire conserve: %s", reason, url)
        return cached.get("data"), True
    return None, False


def _get(url, timeout=20, attempt=0):
    """GET avec cache ETag/Expires + retry ESI.
    Retourne (data, from_cache) ou (None, False)."""
    import mmd_ratelimit as rl
    now = time.time()
    with _cache_lock:
        cached = _cache.get(url)
    # cache frais (Expires dans le futur ET dans la fenetre 5 min) -> on sert direct
    if cached and cached.get("expires", 0) > now:
        return cached.get("data"), True

    headers = dict(UA)
    headers.update(COMPAT_HEADER)  # X-Compatibility-Date fige la version ESI
    if cached and cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            # met a jour le cache avec ETag + Expires
            etag = r.headers.get("ETag")
            exp = r.headers.get("Expires")
            expires_ts = now + _CACHE_TTL
            if exp:
                try:
                    import email.utils
                    parsed = time.mktime(email.utils.parsedate(exp))
                    # CCP renvoie souvent Expires dans le passe -> on force 5 min mini
                    if parsed > now:
                        expires_ts = parsed
                except Exception:
                    pass
            with _cache_lock:
                _cache[url] = {"data": data, "etag": etag, "expires": expires_ts}
            _save_cache()
            # log date compat demandee vs effective (pas une erreur si differente)
            eff = r.headers.get("X-Compatibility-Date")
            if _COMPAT_DATE and eff and eff != _COMPAT_DATE:
                _log.info("ESI compat: demandee=%s, effective=%s (route interne)",
                          _COMPAT_DATE, eff)
            rl.observe("public", r.headers)  # lit X-Ratelimit-* ET X-ESI-Error-Limit-*
            rem = r.headers.get("X-Ratelimit-Remaining")
            lim = r.headers.get("X-Ratelimit-Limit")
            rl.throttle("public", int(rem) if rem else None,
                        int(lim.split("/")[0]) if lim else None)
            return data, False
    except urllib.error.HTTPError as e:
        code = e.code
        rl.observe("public", e.headers)  # observe meme en erreur (erreurs coutent des tokens)
        if code == 304:  # Not Modified -> on sert le cache
            if cached:
                return cached.get("data"), True
            return None, False
        if code == 429:
            # respecte EXACTEMENT Retry-After (coute 0 token de bucket)
            rl.wait_retry_after(e.headers.get("Retry-After", "5"))
            return _get(url, timeout, attempt + 1)
        if code == 420:
            # error-limit globale atteinte -> backoff
            if attempt < 4:
                time.sleep(min(2 ** attempt, 16) + random.uniform(0, 1.5))
                return _get(url, timeout, attempt + 1)
            return _stale_or_unavailable(cached, url, "HTTP 420")
        if code in (500, 502, 503, 504):
            # 5xx = 0 token de bucket -> backoff + jitter
            if attempt < 4:
                time.sleep(min(2 ** attempt, 16) + random.uniform(0, 1.5))
                return _get(url, timeout, attempt + 1)
            return _stale_or_unavailable(cached, url, f"HTTP {code}")
        # 401/403/404 et autres 4xx: PAS de retry automatique.
        # 4xx coutent 5 tokens -> on n'enchaune pas les requetes pourries.
        # 403 = scope/access insuffisant -> arret net.
        # 404 sur structure = structure absente/access perdu -> arret net.
        return None, False
    except Exception:
        if attempt < 3:
            time.sleep(min(2 ** attempt, 16) + random.uniform(0, 1.5))
            return _get(url, timeout, attempt + 1)
        return _stale_or_unavailable(cached, url, "erreur reseau")


def live_type_ids():
    con = sqlite3.connect(MAIN_DB); cur = con.cursor()
    ids = [r[0] for r in cur.execute(
        f"SELECT DISTINCT type_id FROM market_orders WHERE {LIVE}")]
    con.close()
    return ids


class _FetchResult(list):
    def __init__(self, rows=(), valid=True):
        super().__init__(rows)
        self.valid = valid


def _fetch_one(args):
    tid, region = args
    url = (f"{ESI}/markets/{region}/orders/?datasource=tranquility"
           f"&order_type=all&type_id={tid}")
    status = None
    err = None
    try:
        rows, _ = _get(url)
        if rows is None:
            return _FetchResult(valid=False)
        res = []
        for o in rows:
            side = 0 if o.get("is_buy_order") else 1
            res.append({
                "type_id": tid,
                "location_id": int(o["location_id"]),
                "side": side,
                "price": float(o["price"]),
                "issued": o.get("issued", ""),
                "vol": int(o.get("volume_remain", 0)),
                "order_id": str(o.get("order_id", "")),
            })
        status = 200
        return _FetchResult(res)
    except Exception as e:
        err = str(e)
        return _FetchResult(valid=False)
    finally:
        # journalise le fetch ESI (ne doit jamais casser le fetch)
        try:
            import repositories.snapshot_repository as _sr
            import time as _t
            fid = "esi_" + str(tid) + "_" + _t.strftime("%Y%m%d%H%M%S", _t.gmtime())
            _sr.save_esi_fetch(fid, endpoint=url, requested_at=_t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
                               completed_at=_t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
                               http_status=status, coherent=(status == 200), error_message=err)
        except Exception:
            pass


def fetch_public_orders(type_ids, region=REGION_THE_FORGE, max_workers=_MAX_WORKERS):
    """Fetch parallele du livre public pour chaque type_id.
    Retourne une LISTE d'ordres publics (memoire uniquement)."""
    out = []
    tasks = [(tid, region) for tid in type_ids]
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for res in ex.map(_fetch_one, tasks):
            out.extend(res)
    return out


def get_live_public(region=REGION_THE_FORGE):
    """Raccourci: renvoie (ids, liste_ordres_publics, duree_sec).
    Lit les ids 'live' depuis le db Mmd (verrouille potentiellement).
    Preferer get_live_public_for() si on connait deja les type_ids."""
    _load_cache()
    t0 = time.time()
    ids = live_type_ids()
    pub = fetch_public_orders(ids, region)
    return ids, pub, round(time.time() - t0, 1)


def get_live_public_for(type_ids, region=REGION_THE_FORGE, progress=None, include_failures=False):
    """Comme get_live_public mais pour un SOUS-ENSEMBLE de type_ids donne.
    Ne lit PAS le db Mmd (pas de verrou). Sert pour l'Import: on ne
    recupere le livre public QUE pour les items qu'on a reellement en ordre.
    progress(processe, total) optionnel pour suivre l'avancement."""
    _load_cache()
    t0 = time.time()
    ids = sorted(set(int(t) for t in type_ids))
    out = []
    failed = []
    total = len(ids)
    done = 0
    # limite le parallele pour respecter le bucket public (4 threads ok)
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        for tid, res in zip(ids, ex.map(_fetch_one, [(tid, region) for tid in ids])):
            out.extend(res)
            if not res.valid:
                failed.append(tid)
            done += 1
            if progress and total:
                progress(done, total)
    result = (ids, out, round(time.time() - t0, 1))
    return (*result, failed) if include_failures else result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        ids, pub, sec = get_live_public()
        print(f"ESI parallel: {len(ids)} items, {len(pub)} ordres publics, {sec}s")
