#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_esi_orders.py - lectures ESI authentifiees (1 token / perso).

Endpoints utilises:
  GET /characters/{char_id}/orders/          (esi-markets.read_character_orders.v1)
      -> tes ordres actifs (type_id, location_id, is_buy_order, price, volume_remain, issued)
  GET /characters/{char_id}/wallet/          (esi-wallet.read_character_wallet.v1)
  GET /corporations/{corp_id}/orders/        (esi-markets.read_corporation_orders.v1)
  GET /corporations/{corp_id}/wallets/{div}/journal/  (esi-wallet.read_corporation_wallet.v1)

Respect rate-limiting ESI: retry 429/Retry-After, 420/5xx backoff, throttle par perso.
Tout est en memoire, jamais de DB Mmd -> pas de lock.
"""
import json, urllib.request, urllib.error, time, random
import mmd_sso as sso
import mmd_ratelimit as rl
import mmd_esi as esi  # pour reutiliser COMPAT_HEADER + ESI_VERSION_DATE + UA

UA = esi.UA
COMPAT_HEADER = esi.COMPAT_HEADER


def _get(url, char_id, attempt=0):
    """GET authentifie avec cache ETag/Expires + retry ESI differencie.
    Retourne (data, headers) ou (None, None)."""
    at = sso._access_token(char_id)
    if not at:
        return None, None
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {at}", **UA, **COMPAT_HEADER})
    g = rl._group_key(char_id)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            rl.observe(g, r.headers)  # X-Ratelimit-* ET X-ESI-Error-Limit-*
            # log date compat demandee vs effective (pas une erreur si differente)
            eff = r.headers.get("X-Compatibility-Date")
            if esi._COMPAT_DATE and eff and eff != esi._COMPAT_DATE:
                esi._log.info("ESI compat: demandee=%s, effective=%s (route interne)",
                              esi._COMPAT_DATE, eff)
            rem = r.headers.get("X-Ratelimit-Remaining")
            lim = r.headers.get("X-Ratelimit-Limit")
            rl.throttle(g, int(rem) if rem else None,
                        int(lim.split("/")[0]) if lim else None)
            return json.loads(r.read().decode("utf-8")), r.headers
    except urllib.error.HTTPError as e:
        code = e.code
        rl.observe(g, e.headers)  # observe meme en erreur (4xx coutent 5 tokens)
        if code == 401:
            if sso._refresh(char_id):
                return _get(url, char_id, attempt)
            return None, None
        if code == 429:
            # respecte EXACTEMENT Retry-After (0 token de bucket)
            rl.wait_retry_after(e.headers.get("Retry-After", "5"))
            return _get(url, char_id, attempt + 1)
        if code == 420:
            # error-limit globale -> backoff
            if attempt < 4:
                time.sleep(min(2 ** attempt, 16) + random.uniform(0, 1.5))
                return _get(url, char_id, attempt + 1)
            return None, None
        if code in (500, 502, 503, 504):
            # 5xx = 0 token de bucket -> backoff + jitter
            if attempt < 4:
                time.sleep(min(2 ** attempt, 16) + random.uniform(0, 1.5))
                return _get(url, char_id, attempt + 1)
            return None, None
        # 403 = scope/access insuffisant -> arret net (pas de retry).
        # 404 sur structure = absente/access perdu -> arret net.
        # autres 4xx = 5 tokens -> pas de retry en chaine.
        return None, None
    except Exception:
        if attempt < 3:
            time.sleep(min(2 ** attempt, 16) + random.uniform(0, 1.5))
            return _get(url, char_id, attempt + 1)
        return None, None


def fetch_character_orders(char_id, region=10000002):
    """Renvoie la liste des ordres actifs du perso (memoire).
    Chaque dict: type_id, char_id, char_name, station_id, side(0/1),
                 price, vol_remaining, issued, order_id.
    Pagination ESI via X-Pages."""
    name = sso._chars().get(str(char_id), {}).get("name", str(char_id))
    out = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        url = f"https://esi.evetech.net/v2/characters/{char_id}/orders/?page={page}"
        data, headers = _get(url, char_id)
        if data is None:
            return None
        for o in data:
            out.append({
                "order_id": str(o.get("order_id")),
                "type_id": int(o["type_id"]),
                "char_id": int(char_id),
                "char_name": name,
                "station_id": int(o.get("location_id", 0)),
                "side": 0 if o.get("is_buy_order") else 1,
                "price": float(o["price"]),
                "vol_remaining": int(o.get("volume_remain", 0)),
                "issued": o.get("issued", ""),
                "range": o.get("range", "region"),
            })
        if headers and headers.get("X-Pages"):
            try:
                total_pages = int(headers.get("X-Pages"))
            except ValueError:
                total_pages = page
        else:
            total_pages = page
        page += 1
    return out


def fetch_all_orders():
    """Fusionne les ordres de tous les persos connectes.
    Retourne (orders, errors, synced_ids); un perso n'est synchronise
    qu'apres lecture atomique de toutes ses pages, meme avec zero ordre."""
    orders = []
    errors = []
    synced_ids = []
    for c in sso.connected_chars():
        cid = c["id"]
        res = fetch_character_orders(cid)
        if res is None:
            errors.append((c["name"], "token/inaccessible"))
        else:
            orders.extend(res)
            synced_ids.append(cid)
    return orders, errors, synced_ids


def fetch_orders_for_char(char_id):
    """Ordres actifs d'UN SEUL perso (memoire). Reutilise fetch_character_orders.
    Retourne (orders, errors, synced_ids) comme fetch_all_orders mais filtre au perso."""
    orders, errors, synced_ids = [], [], []
    cid = str(char_id)
    res = fetch_character_orders(cid)
    if res is None:
        name = sso._chars().get(cid, {}).get("name", cid)
        errors.append((name, "token/inaccessible"))
    else:
        orders.extend(res)
        synced_ids.append(cid)
    return orders, errors, synced_ids


def fetch_corporation_orders(corp_id, char_id):
    """Ordres de la corp (via un perso qui a le scope corp)."""
    data, _ = _get(f"https://esi.evetech.net/v2/corporations/{corp_id}/orders/", char_id)
    if data is None:
        return None
    out = []
    for o in data:
        out.append({
            "order_id": str(o.get("order_id")),
            "type_id": int(o["type_id"]),
            "corp_id": int(corp_id),
            "station_id": int(o.get("location_id", 0)),
            "side": 0 if o.get("is_buy_order") else 1,
            "price": float(o["price"]),
            "vol_remaining": int(o.get("volume_remain", 0)),
            "issued": o.get("issued", ""),
        })
    return out


def fetch_structure_info(structure_id, char_id):
    """Nom + infos d'une citadelle (esi-universe.read_structures.v1).
    Retourne dict {name, solarSystemID, type_id, ...} ou None si 403/404/5xx."""
    data, _ = _get(f"https://esi.evetech.net/v1/universe/structures/{structure_id}/",
                   char_id)
    if data is None:
        return None
    return {
        "structure_id": int(structure_id),
        "name": data.get("name", str(structure_id)),
        "solarSystemID": int(data.get("solar_system_id", 0)),
        "type_id": int(data.get("type_id", 0)),
    }


def _state_from_status(code):
    """Mappe un code HTTP ESI vers un etat de structure (sans supposer de purge)."""
    if code in (500, 502, 503, 504):
        return "temporarily_unavailable"
    if code in (420, 429):
        return "rate_limited"
    if code == 401:
        return "authentication_failed"
    if code in (403, 404):
        return "inaccessible"
    # autre -> on traite comme indisponible temporairement (pas de purge)
    return "temporarily_unavailable"


def fetch_structure_orders(structure_id, char_id, max_workers=4):
    """Lit le livre d'une structure Upwell (esi-markets.structure_markets.v1).
    Le perso connecte doit avoir un droit de docking dans la structure.

    Retourne (snapshot, state) où snapshot est un dict {order_id: order} (deja
    deduplique par order_id) ou None si echec. state = etat ESI de la requete.

    Regles CCP respectees:
    - X-Pages + concurrence limitee.
    - Cohérence Last-Modified: toutes les pages doivent partager le meme timestamp.
    - Protection Expires: on memorise Expires de la page 1 ; si le telechargement
      risque de traverser cette expiration (pages lentes), on abandonne.
    - Dedup finale par order_id (protect contre retries partiels / chevauchements).
    - Remplacement atomique: le snapshot n'est renvoye qu'apres validation complete.
    - Etats: accessible / inaccessible / temporarily_unavailable / rate_limited /
      authentication_failed. AUCUN etat n'implique une purge de l'historique.
    """
    name = sso._chars().get(str(char_id), {}).get("name", str(char_id))
    url0 = f"https://esi.evetech.net/v1/markets/structures/{structure_id}/?page=1"
    try:
        data, headers = _get(url0, char_id)
    except urllib.error.HTTPError as e:
        return None, _state_from_status(e.code)
    except Exception:
        return None, "temporarily_unavailable"
    if data is None:
        return None, "temporarily_unavailable"
    # page 1 OK
    total_pages = 1
    expires_ts = None
    if headers:
        if headers.get("X-Pages"):
            try:
                total_pages = int(headers.get("X-Pages"))
            except ValueError:
                total_pages = 1
        exp = headers.get("Expires")
        if exp:
            try:
                import email.utils, time as _t
                parsed = _t.mktime(email.utils.parsedate(exp))
                if parsed > _t.time():
                    expires_ts = parsed
            except Exception:
                pass
    pages = {1: data}
    lastmods = {1: headers.get("Last-Modified") if headers else None}
    # pages suivantes en parallele (concurrence limitee)
    import concurrent.futures, time as _t2
    def _fetch_page(pg):
        u = f"https://esi.evetech.net/v1/markets/structures/{structure_id}/?page={pg}"
        try:
            d, h = _get(u, char_id)
        except urllib.error.HTTPError as e:
            return pg, None, None, _state_from_status(e.code)
        except Exception:
            return pg, None, None, "temporarily_unavailable"
        return pg, d, (h.get("Last-Modified") if h else None), None
    if total_pages > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for pg, d, lm, st in ex.map(_fetch_page, range(2, total_pages + 1)):
                # protection expiration: si on a depasse Expires en cours de route -> abandon
                if expires_ts and _t2.time() > expires_ts:
                    return None, "temporarily_unavailable"
                if d is None:
                    return None, (st or "temporarily_unavailable")
                pages[pg] = d
                lastmods[pg] = lm
    # coherence : tous les Last-Modified identiques ?
    mods = set(m for m in lastmods.values() if m)
    if len(mods) > 1:
        # timestamp a change pendant la collecte -> on jette tout le snapshot
        return None, "temporarily_unavailable"
    # dedup finale par order_id (atomique apres validation complete)
    snapshot = {}
    for pg in sorted(pages):
        for o in pages[pg]:
            oid = str(o.get("order_id"))
            snapshot[oid] = {
                "order_id": oid,
                "type_id": int(o["type_id"]),
                "char_id": int(char_id),
                "char_name": name,
                "station_id": int(structure_id),
                "side": 0 if o.get("is_buy_order") else 1,
                "price": float(o["price"]),
                "vol_remaining": int(o.get("volume_remain", 0)),
                "issued": o.get("issued", ""),
                "range": o.get("range", "region"),
                "location_id": int(structure_id),
            }
    return snapshot, "accessible"


def scan_authed(order_books=None):
    """Scan authentifie SSO (tous persos) + livre PUBLIC (region) + livres des
    structures Upwell ou un perso a docking.

    - Le SNAPSHOT d'une structure est partage en memoire des qu'UN perso autorise
      l'a obtenu (tous les persos y ont acces pour la concurrence).
    - L'ETAT d'acces reste DISTINCT par perso (A accessible, B inaccessible).
    - Les ordres de TOUS les persos sont exclus du livre concurrent.

    Retourne le dict _scan_core, avec 'structures' = etats par structure_id.
    """
    import mmd_core as core
    import time as _t
    orders, errs, synced_ids = fetch_all_orders()
    if errs and not synced_ids:
        return {
            "ok": False, "error": "Synchronisation ESI privée indisponible",
            "synced_char_ids": [],
            "sso_errors": [{"char": n, "msg": m} for n, m in errs],
        }
    # livres publics (region) -> deja fournis ou on les fetch
    if order_books is None:
        import mmd_esi as ei
        ids = list({o["type_id"] for o in orders})
        order_books = ei.fetch_public_orders(ids) if ids else []
    # deduplication universelle par order_id sur le livre public fourni
    pub_by_id = {}
    for o in order_books:
        pub_by_id[str(o.get("order_id"))] = o
    # livres des structures: deduit via .env (citadelles) + ordres perso en citadelle
    import mmd_stations as stt
    structures_state = {}
    env_struct = _env_structure_ids()
    struct_ids = set(env_struct)
    for o in orders:
        sid = o.get("station_id")
        # citadelle = station non resolvable en NPC (staStations) -> inconnue du SDE
        if sid and stt.resolve(sid)[0] is None and sid not in env_struct:
            struct_ids.add(sid)
    # fetch par perso: etat distinct + snapshot partage
    chars = sso.connected_chars()
    for sid in struct_ids:
        access_by_char = {}
        shared_snapshot = None
        last_success_at = None
        agg = "inaccessible"
        for c in chars:
            snap, state = fetch_structure_orders(sid, c["id"])
            access_by_char[str(c["id"])] = state
            if state == "accessible" and snap is not None:
                shared_snapshot = snap  # premier perso autorise -> snapshot partage
                last_success_at = _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime())
                agg = "accessible"
            elif state in ("temporarily_unavailable", "rate_limited") and agg == "inaccessible":
                agg = state
        structures_state[sid] = {
            "aggregate_status": agg,
            "last_success_at": last_success_at,
            "access_by_character": access_by_char,
        }
        # ajoute le snapshot partage au livre public memoire (hors ordres de TOUS les persos)
        if shared_snapshot is not None:
            for oid, so in shared_snapshot.items():
                pub_by_id[oid] = so  # dedup universelle par order_id
    # livre concurrent = toutes les sources, MOINS les ordres de TOUS les persos
    all_owned_order_ids = {str(o["order_id"]) for o in orders}
    competitors = [o for o in pub_by_id.values() if o["order_id"] not in all_owned_order_ids]
    source = "ESI authenticated (SSO) + public + structures"
    if orders:
        data = core._scan_core(orders, competitors, source)
    else:
        zero_counts = {
            str(cid): {"total": 0, "buy": 0, "sell": 0}
            for cid in synced_ids
        }
        data = {
            "ok": True, "timestamp": time.strftime("%Y-%m-%d %H:%M"),
            "counts_timestamp_ms": int(time.time() * 1000), "source": source,
            "buy_total": 0, "sell_total": 0, "total": 0,
            "orders_to_update": 0, "buy_to_update": 0, "sell_to_update": 0,
            "orders_to_update_by_char": zero_counts,
            "duplicates": 0, "dup_list": [], "to_update_list": [],
            "orders_full": [], "characters": [c["name"] for c in chars],
        }
    counts = data.setdefault("orders_to_update_by_char", {})
    for cid in synced_ids:
        counts.setdefault(str(cid), {"total": 0, "buy": 0, "sell": 0})
    data["synced_char_ids"] = [] if errs else [str(cid) for cid in synced_ids]
    if errs:
        data["sso_errors"] = [{"char": n, "msg": m} for n, m in errs]
    data["structures"] = structures_state
    return data


def _env_structure_ids():
    """IDs de citadelles Upwell declarees dans le .env (Trading_Upwell_ID).
    Ces structures necessitent docking + scope structure_markets pour lire leur livre."""
    import os
    out = set()
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line.startswith("Trading_Upwell_ID="):
                v = line.split("=", 1)[1].strip()
                out.add(int(v.split("//")[-1]))
    except Exception:
        pass
    return out


def _env_station_ids():
    """IDs de stations NPC declarees dans le .env (Sell_Station_ID, ex: Jita).
    Resolues via staStations, pas de fetch de livre de structure."""
    import os
    out = set()
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line.startswith("Sell_Station_ID="):
                v = line.split("=", 1)[1].strip()
                out.add(int(v.split("//")[-1]))
    except Exception:
        pass
    return out


if __name__ == "__main__":
    orders, errs, _ = fetch_all_orders()
    if errs:
        print("erreurs:", errs)
    print(f"ordres lus (tous persos connectes): {len(orders)}")
    from collections import Counter
    c = Counter(o["char_name"] for o in orders)
    for n, k in c.items():
        print(f"  {n}: {k}")
