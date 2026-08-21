#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostic: compare les exports 'My Orders' des 3 persos vs le
'Corporation Orders' de la corp (qui ne contient que ces 3 persos).
But: trouver la correlation des ordres MANQUANTS dans l'export corp
(bug CCP connu: Corporation Orders- tronque/bug).

Agrège par (type_id, side) et compare:
- perso_total = somme des 3 fichiers My Orders
- corp = fichier Corporation Orders
Affiche les items dans perso mais PAS dans corp (manquants), et inversement.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mmd_import as imp

ML = os.path.expanduser("~/Documents/EVE/logs/Marketlogs")


def latest(pattern):
    cands = []
    if not os.path.isdir(ML):
        return None
    for fn in os.listdir(ML):
        if fn.startswith(pattern) and fn.endswith(".txt"):
            full = os.path.join(ML, fn)
            try:
                cands.append((os.path.getmtime(full), full))
            except OSError:
                pass
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][1]


def load(fn):
    if not fn or not os.path.exists(fn):
        return [], {}
    return imp.parse_export(fn)


def key(o):
    return (o["type_id"], int(o["side"]))


def agg(orders):
    d = {}
    for o in orders:
        k = key(o)
        e = d.setdefault(k, {"vol": 0, "prices": set(), "chars": set(),
                             "stations": set(), "name": o.get("name", str(k[0]))})
        e["vol"] += int(o.get("vol_remaining", 0) or 0)
        e["prices"].add(o.get("price"))
        e["chars"].add(o.get("char_name"))
        e["stations"].add(o.get("station_id"))
    return d


def main():
    my_files = []
    seen_chars = set()
    for fn in sorted(
        [os.path.join(ML, f) for f in os.listdir(ML)
         if f.startswith("My Orders-") and f.endswith(".txt")],
        key=os.path.getmtime, reverse=True
    ):
        try:
            _, chars = imp.parse_export(fn)
        except Exception:
            continue
        pk = frozenset(chars.values())
        if pk in seen_chars:
            continue
        seen_chars.add(pk)
        my_files.append(fn)
        if len(my_files) >= 3:
            break
    corp_file = latest("Corporation Orders-")

    print("=== Fichiers utilises ===")
    for f in my_files:
        print("  My Orders :", os.path.basename(f))
    print("  Corp      :", os.path.basename(corp_file) if corp_file else "(AUCUN)")

    if not my_files or not corp_file:
        print("ATTENDRE: exporte les 3 My Orders + 1 Corporation Orders, puis relance.")
        return

    # agrege les persos
    perso_orders = []
    perso_chars = set()
    for f in my_files:
        orders, chars_seen = load(f)
        perso_orders.extend(orders)
        perso_chars.update(chars_seen.values())
    print("\n=== Persos dans les exports ===")
    print("  chars vus:", sorted(perso_chars))
    print("  total ordres perso:", len(perso_orders))

    corp_orders, corp_chars = load(corp_file)
    print("  chars dans corp export:", sorted(corp_chars.values()))
    print("  total ordres corp:", len(corp_orders))

    pa = agg(perso_orders)
    ca = agg(corp_orders)

    # manquants: dans perso, pas dans corp
    missing = []
    for k, v in pa.items():
        if k not in ca:
            missing.append((k, v))
    # extra: dans corp, pas dans perso
    extra = []
    for k, v in ca.items():
        if k not in pa:
            extra.append((k, v))
    # volume different sur communs
    diff_vol = []
    for k in pa:
        if k in ca and pa[k]["vol"] != ca[k]["vol"]:
            diff_vol.append((k, pa[k]["vol"], ca[k]["vol"]))

    print("\n=== RESULTAT ===")
    print(f"Items (type,side) dans PERSO mais MANQUANTS du corp: {len(missing)}")
    for k, v in sorted(missing, key=lambda x: -x[1]["vol"])[:40]:
        print(f"  {v['name'][:32]:32} type={k[0]} side={'BUY' if k[1]==0 else 'SELL'} "
              f"vol_perso={v['vol']} chars={sorted(v['chars'])} stations={sorted(v['stations'])}")
    print(f"\nItems dans CORP mais PAS dans perso (extra/autres membres): {len(extra)}")
    for k, v in sorted(extra, key=lambda x: -x[1]["vol"])[:40]:
        print(f"  {v['name'][:32]:32} type={k[0]} side={'BUY' if k[1]==0 else 'SELL'} "
              f"vol_corp={v['vol']} chars={sorted(v['chars'])}")
    print(f"\nItems COMMUNS avec VOLUME DIFFERENT: {len(diff_vol)}")
    for k, vp, vc in sorted(diff_vol, key=lambda x: -(x[1]-x[2]))[:40]:
        print(f"  type={k[0]} side={'BUY' if k[1]==0 else 'SELL'} perso={vp} corp={vc} (delta={vp-vc})")

    # correlation: les manquants sont-ils lies a une station/region/precision ?
    if missing:
        stations = set()
        for _, v in missing:
            stations.update(v["stations"])
        print("\n=== CORRELATION (manquants) ===")
        print("  stations touchees:", sorted(stations))


if __name__ == "__main__":
    main()
