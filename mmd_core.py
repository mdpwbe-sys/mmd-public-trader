#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mmd Core - logique de scan (importable, testable).
Renvoie un dict JSON consommable par le GUI (pywebview) ou le .bat.

Regles:
  - ordres vivants = volume_remaining>0 AND last_seen IS NULL (perso seul,
    on EXCLUT corp_market_orders = doublon des ordres perso)
  - doublons inter-persos = meme item + meme station + meme sens, >=2 personnages
  - prix depasse (compare contre le livre PUBLIC en memoire, pas la DB Mmd):
      BUY  : un ordre public offre > mon prix  OU (== et emis apres) -> monter
      SELL : un ordre public vend < mon prix   OU (== et emis apres) -> baisser

Le livre public est fourni en parametre (liste d'ordres ESI) pour eviter
d'ecrire dans la DB Mmd (database is locked) et pour rester rapide.
"""
import os, sqlite3, datetime
from decimal import Decimal
import mmd_price  # prix en Decimal/centiemes (jamais float)
from platform_state import state_path

# Operational DB lives in the persistent app state dir (APPDATA/MMD-Trader),
# NOT the legacy %LOCALAPPDATA%/mmd.com/Mmd path. eve.db (SDE) is NOT bundled
# in the public build; item-name resolution falls back to "Item #<id>".
MAIN_DB = state_path("app_data.db")
EVE_DB = None

def iname(tid):
    """Retourne le nom d'un item par son type_id depuis eve.db (invTypes) ou sqlite3."""
    try:
        import sqlite3
        for db_path in ["eve.db", os.path.join(os.path.dirname(__file__), "eve.db")]:
            if db_path and os.path.exists(db_path):
                with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
                    r = con.execute("SELECT typeName FROM invTypes WHERE typeID=?", (int(tid),)).fetchone()
                    if r and r[0]:
                        return r[0]
    except Exception:
        pass
    return f"Item #{tid}"


def scan(public_orders=None):
    """public_orders: liste [{type_id, location_id, side(0/1), price, issued, vol}]
    Si None: lit external_orders de la DB (fallback offline)."""
    if not os.path.exists(MAIN_DB):
        return {"ok": False, "error": f"Base introuvable: {MAIN_DB}"}

    con = sqlite3.connect(MAIN_DB); cur = con.cursor()
    eve = sqlite3.connect(EVE_DB) if (EVE_DB and os.path.exists(EVE_DB)) else None
    ecur = eve.cursor() if eve else None

    names = {r[0]: r[1] for r in cur.execute("SELECT id, name FROM characters")}
    def cname(cid): return names.get(cid, str(cid))
    def iname(tid):
        if ecur:
            r = ecur.execute("SELECT typeName FROM invTypes WHERE typeID=?", (tid,)).fetchone()
            if r: return r[0]
        return f"typeID {tid}"

    # livre public: indexe par (type_id, location_id, side)
    ext = {}
    if public_orders is not None:
        for o in public_orders:
            ext.setdefault((o["type_id"], o["location_id"], o["side"]), []).append(o)
    else:
        for tid, loc, typ, val, vol, iss in cur.execute(
            "SELECT type_id, location_id, type, value, volume_remaining, issued FROM external_orders"):
            ext.setdefault((tid, loc, typ), []).append({"price": val, "issued": iss or "", "vol": vol})

    # ---- doublons inter-persos ----
    dup_q = f"""
    SELECT type_id, location_id, type,
           COUNT(DISTINCT character_id) AS n_persos,
           COUNT(*) AS n_ordres,
           GROUP_CONCAT(DISTINCT character_id) AS char_ids,
           GROUP_CONCAT(DISTINCT price) AS prices,
           SUM(volume_remaining) AS vol_rem
    FROM market_orders
    WHERE {LIVE}
    GROUP BY type_id, location_id, type
    HAVING n_persos >= 2
    ORDER BY n_ordres DESC, vol_rem DESC
    """
    dups = []
    for tid, loc, typ, nc, no, cids, prices, vr in cur.execute(dup_q).fetchall():
        dups.append({
            "type_id": tid, "name": iname(tid), "location_id": loc,
            "side": "BUY" if typ == 0 else "SELL",
            "chars": [cname(int(c)) for c in cids.split(",")],
            "prices_m": [round(int(p) / 1e6, 2) for p in prices.split(",")],
            "vol": vr,
        })

    # ---- ordres actifs ----
    buy_tot = cur.execute(f"SELECT COUNT(*) FROM market_orders WHERE type=0 AND {LIVE}").fetchone()[0]
    sell_tot = cur.execute(f"SELECT COUNT(*) FROM market_orders WHERE type=1 AND {LIVE}").fetchone()[0]

    my_orders = set()
    for tid, loc, price, vol, typ in cur.execute(
        f"SELECT type_id, location_id, price, volume_remaining, type FROM market_orders WHERE {LIVE}"):
        my_orders.add((tid, loc, price, vol, typ))

    def others_better(tid, loc, typ, price, vol, my_issued):
        cnt = 0
        for o in ext.get((tid, loc, typ), []):
            e_val = o["price"]
            e_iss = o.get("issued", "")
            if typ == 0:
                if e_val > price or (e_val == price and _iss_cmp(e_iss, my_issued) > 0):
                    cnt += 1; break
            else:
                if e_val < price or (e_val == price and _iss_cmp(e_iss, my_issued) > 0):
                    cnt += 1; break
        mine = 1 if (tid, loc, price, vol, typ) in my_orders else 0
        return max(0, cnt - mine)

    buy_pass = sell_pass = 0
    for tid, loc, price, vol, typ, iss in cur.execute(
        f"SELECT type_id, location_id, price, volume_remaining, type, issued FROM market_orders WHERE {LIVE}"):
        if others_better(tid, loc, typ, price, vol, iss or "") > 0:
            if typ == 0: buy_pass += 1
            else:        sell_pass += 1

    con.close()
    if eve: eve.close()

    return {
        "ok": True,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "buy_total": buy_tot,
        "sell_total": sell_tot,
        "total": buy_tot + sell_tot,
        "orders_to_update": buy_pass + sell_pass,
        "buy_to_update": buy_pass,
        "sell_to_update": sell_pass,
        "duplicates": len(dups),
        "dup_list": dups,
        "characters": list(names.values()),
    }


def _iss_cmp(a, b):
    """Compare deux dates 'issued' (formats ISO ESI ou 'YYYY-MM-DD HH:MM:SS').
    Retourne >0 si a est PLUS RECENT que b, <0 si plus ancien, 0 si egal.
    Robuste aux formats melanges (ESI: '2026-08-06T03:44:50Z', fichier:
    '2026-08-06 04:39:21')."""
    import datetime
    def parse(s):
        if not s:
            return datetime.datetime.min
        s = str(s).strip().replace("Z", "").replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(s[:19], fmt)
            except ValueError:
                continue
        return datetime.datetime.min
    pa, pb = parse(a), parse(b)
    return (pa > pb) - (pa < pb)


def _scan_core(orders, public_orders, source_label):
    """Logique de scan partagee (ordres quelconques + livre public en memoire).
    orders: liste de dicts {order_id, type_id, char_name, station_id, side, price, vol_remaining, issued}
    public_orders: liste ESI {type_id, location_id, side, price, issued, vol, order_id}
    Pas de DB Mmd -> jamais de 'database is locked'."""
    if not orders:
        return {"ok": False, "error": "Aucun ordre a scanner"}

    def iname(tid):
        try:
            con = sqlite3.connect(EVE_DB)
            r = con.execute("SELECT typeName FROM invTypes WHERE typeID=?", (tid,)).fetchone()
            con.close()
            return r[0] if r else f"typeID {tid}"
        except Exception:
            return f"typeID {tid}"

    _ship_cache = {}

    def _resolve_ship_id(name):
        if not name or "skin" not in name.lower():
            return None
        words = name.strip().split()
        if not words:
            return None
        possible_ship = words[0]
        if possible_ship in _ship_cache:
            return _ship_cache[possible_ship]
        try:
            import sqlite3, os
            local = os.environ.get('LOCALAPPDATA', '')
            db_path = os.path.join(local, 'mmd.com', 'Mmd', 'resources', 'eve.db')
            if os.path.exists(db_path):
                with sqlite3.connect(db_path) as con:
                    row = con.execute("SELECT typeID FROM invTypes WHERE typeName = ? AND groupID IN (SELECT groupID FROM invGroups WHERE categoryID = 6)", (possible_ship,)).fetchone()
                    if row:
                        _ship_cache[possible_ship] = row[0]
                        return row[0]
        except Exception:
            pass
        _ship_cache[possible_ship] = None
        return None

    def classify_duplicates(orders, public_orders=None):
        """Identifie les ordres en concurrence entre nos propres persos (doublons).
        Groupement strict par (type_id, side).
        Retourne la liste des doublons + totaux d'ordres."""
        from collections import defaultdict
        grp = defaultdict(set)
        grp_orders = defaultdict(list)
        for o in orders:
            if "price_cents" not in o and "price" in o:
                try:
                    o["price_cents"] = int(mmd_price.to_cents(o["price"]))
                except Exception:
                    o["price_cents"] = 0
            key = (o["type_id"], o["side"])
            grp[key].add(o["char_name"])
            grp_orders[key].append(o)
        dups = []
        for (tid, side), persos in grp.items():
            if len(persos) >= 2:
                stations = sorted(set(str(o.get("station_id")) for o in grp_orders[(tid, side)]))
                prices = sorted(set(mmd_price.fmt_cents(o["price_cents"]) for o in grp_orders[(tid, side)]))
                item_name = iname(tid)
                dups.append({
                    "type_id": tid, "name": item_name,
                    "ship_type_id": _resolve_ship_id(item_name),
                    "side": "BUY" if side == 0 else "SELL",
                    "chars": sorted(persos),
                    "stations": stations,
                    "prices_m": prices,
                    "vol": sum(o["vol_remaining"] for o in grp_orders[(tid, side)]),
                })
        return dups

    dups = classify_duplicates(orders)
    buy_tot = sum(1 for o in orders if o["side"] == 0)
    sell_tot = sum(1 for o in orders if o["side"] == 1)

    # livre public indexe (on exclut TOUS nos ordres du livre concurrent)
    # public_orders arrive deja filtre (competitors) mais on reverifie avec owned_ids
    owned_ids = set(str(o["order_id"]) for o in orders)
    owned_char_ids = {str(o.get("char_id")) for o in orders}
    # index par (type_id, side) -> ordres concurrents (hors nos persos)
    ext_by_type = {}
    if public_orders:
        for o in public_orders:
            if str(o.get("order_id")) in owned_ids:
                continue
            ext_by_type.setdefault((o["type_id"], o["side"]), []).append(o)

    import mmd_stations as stt

    def _my_loc(o):
        """Resolution (system, region) de MA station."""
        sysid, reg, _ = stt.resolve(o["station_id"])
        return sysid, reg

    def _candidates(o):
        """Tous les concurrents externes+internes pertinents pour l'ordre o."""
        sysid, reg = _my_loc(o)
        cands = ext_by_type.get((o["type_id"], o["side"]), [])
        out = []
        for pub in cands:
            if o["side"] == 0:  # BUY: ranges qui se chevauchent
                # Un concurrent compte si:
                #  - il couvre MA station (un vendeur chez moi le remplit), OU
                #  - je couvre SA station (un vendeur chez lui me remplit aussi,
                #    car mon ordre atteint sa station -> meme pool de vendeurs).
                # Ex: mon achat Perimeter (region/1 saut) vs concurrent achat
                # Jita station -> je couvre Jita -> concurrent direct. Symetrique.
                pub_sys, pub_reg, _ = stt.resolve(pub["location_id"])
                my_range = o.get("range", "region")
                if not (stt.covers(pub["location_id"], pub.get("range", "region"), sysid, reg)
                        or stt.covers(o["station_id"], my_range, pub_sys, pub_reg)):
                    continue
            else:  # SELL: meme station physique
                if pub["location_id"] != o["station_id"]:
                    continue
            out.append(pub)
        return out

    def classify(o, owned_char_ids, internal_orders):
        """Retourne (need_update, status, best_price, best_is_alt).
        status: OUTBID_EXTERNAL / COMPETING_ALT / DUPLICATE_SAME_CHARACTER /
                BEST_EXTERNAL_BUT_ALT_CONFLICT / OK.
        - external: concurrent dans le livre public (hors tous mes persos).
        - internal: autre ordre de MES persos (conflit interne)."""
        # 1. concurrents externes (livre public deja filtre de mes ordres)
        cands = _candidates(o)
        # prix de MON ordre en Decimal (les logs EVE donnent un float) ->
        # comparaison homogene avec e_val (Decimal) dans tout classify
        o_price = Decimal(str(o["price"]))
        # 2. concurrents internes (mes autres persos) — meme item + meme sens,
        # PEU IMPORTE la station (FIFO + doublons marchent entre stations
        # differentes : si un de mes persos a le meme objet, il est concurrent).
        my_cid = str(o.get("char_id"))
        for io in internal_orders:
            if str(io.get("char_id")) == my_cid:
                continue
            if (io["type_id"], io.get("side")) != (o["type_id"], o["side"]):
                continue
            cands.append(io)
        if not cands:
            return False, "OK", None, False, False, False
        best = None
        best_is_alt = False
        best_is_newer = False
        alt_conflict = False
        same_price_newer = False
        for pub in cands:
            pub_cid = str(pub.get("char_id"))
            is_own_alt = pub_cid in owned_char_ids and pub_cid != my_cid
            # pour les ordres internes, la station est station_id; pour le public, location_id
            pub_loc = pub.get("location_id", pub.get("station_id"))
            # prix en Decimal (le public vient en float depuis l'ESI, les ordres
            # internes en Decimal) -> comparaison homogene avec o["price"]
            e_val = Decimal(str(pub["price"])); e_iss = pub.get("issued", "")
            is_newer = _iss_cmp(e_iss, o["issued"]) > 0
            # FIFO outdated: un concurrent au MEME PRIX (meme tick) et PLUS RECENT
            # -> il passe devant en FIFO. Tolerance sub-centime (bruit float).
            if abs(e_val - o_price) < Decimal("0.001") and is_newer:
                same_price_newer = True
            better = False
            if o["side"] == 0:
                if e_val > o_price or (e_val == o_price and is_newer):
                    better = True
            else:
                if e_val < o_price or (e_val == o_price and is_newer):
                    better = True
            if not better:
                continue
            if is_own_alt:
                alt_conflict = True
            if best is None or (o["side"] == 0 and e_val > best) or (o["side"] == 1 and e_val < best):
                best = e_val
                best_is_alt = is_own_alt
                best_is_newer = is_newer
        if best is None:
            # L'ordre est gagnant (premier sur le marche).
            # On cherche le 2eme meilleur concurrent derriere nous pour calculer l'ecart (gap)
            next_best = None
            for pub in cands:
                pub_cid = str(pub.get("char_id"))
                if pub_cid in owned_char_ids and pub_cid == my_cid:
                    continue
                e_val = Decimal(str(pub["price"]))
                e_iss = pub.get("issued", "")
                is_newer = _iss_cmp(e_iss, o["issued"]) > 0
                if abs(e_val - o_price) < Decimal("0.001") and is_newer:
                    same_price_newer = True
                if o["side"] == 0:  # BUY: le meilleur prix concurrent <= o_price
                    if e_val <= o_price:
                        if next_best is None or e_val > next_best:
                            next_best = e_val
                else:  # SELL: le meilleur prix concurrent >= o_price
                    if e_val >= o_price:
                        if next_best is None or e_val < next_best:
                            next_best = e_val

            return False, "OK", next_best, False, False, same_price_newer
        if alt_conflict and not best_is_alt:
            return True, "BEST_EXTERNAL_BUT_ALT_CONFLICT", best, best_is_alt, best_is_newer, same_price_newer
        if best_is_alt:
            return True, "COMPETING_ALT", best, True, best_is_newer, same_price_newer
        return True, "OUTBID_EXTERNAL", best, False, best_is_newer, same_price_newer

    # liste de tous les ordres (pour les onglets Achats/Ventes) + ceux a MAJ
    to_update = []
    orders_full = []
    buy_pass = sell_pass = 0
    orders_to_update_by_char = {}
    import mmd_price as prx
    # Deduplication inter-persos (dups) & intra-perso (self_dup)
    dup_type_sides = set((d["type_id"], 0 if d["side"] == "BUY" else 1) for d in dups)
    char_type_counts = {}
    for o in orders:
        c_key = (str(o.get("char_id")), o.get("type_id"), o.get("side"))
        char_type_counts[c_key] = char_type_counts.get(c_key, 0) + 1
        count_id = o.get("char_id")
        if count_id is None:
            count_id = o.get("char_name") or "_unknown"
        orders_to_update_by_char.setdefault(
            str(count_id), {"total": 0, "buy": 0, "sell": 0})

    for o in orders:
        need_update, status, bp_raw, bp_is_alt, best_is_newer, same_price_newer = classify(o, owned_char_ids, orders)
        bp = Decimal(str(bp_raw)) if bp_raw is not None else None
        price = Decimal(str(o["price"]))
        price_cents = int(o["price_cents"]) if "price_cents" in o else prx.to_cents(price)
        if need_update and bp is not None:
            new_price = prx.next_price(bp, o["side"])
        elif bp is not None:
            new_price = price
        else:
            new_price = price
        new_cents = prx.to_cents(new_price)
        # Ecart = mon prix - meilleur concurrent.
        gap_cents = (price_cents - prx.to_cents(bp)) if bp is not None else None
        # FIFO overtaken (mauve): uniquement si le meilleur concurrent est AU MEME PRIX que moi (meme tick) ET PLUS RECENT.
        fifo_overtaken = bool(need_update) and (bp is not None) and (abs(bp - price) < Decimal("0.001")) and bool(same_price_newer)

        c_key = (str(o.get("char_id")), o.get("type_id"), o.get("side"))
        self_dup = char_type_counts.get(c_key, 0) >= 2
        is_dup = (o["type_id"], o.get("side")) in dup_type_sides

        orders_full.append({
            "type_id": o["type_id"], "name": iname(o["type_id"]),
            "side": 0 if o["side"] == 0 else 1,
            "price_cents": price_cents, "vol_remaining": o["vol_remaining"],
            "station_id": o["station_id"],
            "station_name": stt.resolve_name(o.get("station_id"), o.get("station_name", "")),
            "issued": o.get("issued", ""), "char_id": o.get("char_id"),
            "char_name": o.get("char_name", ""), "order_id": o.get("order_id"),
            "gap_cents": gap_cents, "new_price_cents": new_cents,
            "fifo_overtaken": fifo_overtaken, "needs_update": bool(need_update),
            "status": status, "competing_alt": bp_is_alt,
            "is_dup": is_dup, "self_dup": self_dup,
        })
        if need_update:
            to_update.append({
                "type_id": o["type_id"], "name": iname(o["type_id"]),
                "side": "BUY" if o["side"] == 0 else "SELL",
                "char_name": o.get("char_name", ""), "station_id": o["station_id"],
                "old_price_cents": price_cents, "new_price_cents": new_cents,
                "order_id": o["order_id"], "status": status,
            })
            if o["side"] == 0: buy_pass += 1
            else: sell_pass += 1
            count_id = o.get("char_id")
            if count_id is None:
                count_id = o.get("char_name") or "_unknown"
            bucket = orders_to_update_by_char[str(count_id)]
            bucket["total"] += 1
            bucket["buy" if o["side"] == 0 else "sell"] += 1

    chars = sorted(set(o["char_name"] for o in orders))
    return {
        "ok": True,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "counts_timestamp_ms": int(datetime.datetime.now().timestamp() * 1000),
        "source": source_label,
        "buy_total": buy_tot, "sell_total": sell_tot, "total": buy_tot + sell_tot,
        "orders_to_update": buy_pass + sell_pass,
        "buy_to_update": buy_pass, "sell_to_update": sell_pass,
        "orders_to_update_by_char": orders_to_update_by_char,
        "duplicates": len(dups), "dup_list": dups,
        "to_update_list": to_update,
        "orders_full": orders_full,
        "characters": chars,
    }


def scan_from_logs(public_orders=None):
    """Scan depuis les exports EVE 'My Orders-*.txt' (offline-friendly)."""
    import mmd_logs
    orders = mmd_logs.load_my_orders()
    return _scan_core(orders, public_orders, "EVE logs (My Orders) + ESI public")


def scan_authed(public_orders=None, order_books=None):
    """Scan depuis les ordres ESI authentifies (tous persos connectes SSO) +
    livres des structures Upwell ou le perso a docking.
    Delegue a mmd_esi_orders.scan_authed (qui fetch aussi les structures).
    Accepte 'public_orders' (legacy) ou 'order_books'."""
    import mmd_esi_orders as eo
    books = order_books if order_books is not None else public_orders
    return eo.scan_authed(order_books=books)

