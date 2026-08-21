#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mmd - Detecteur de doublons inter-personnages
====================================================
Repère les ordres de marche POSANT LE MEME item + meme station + meme prix
sur AU MOINS 2 personnages differents de ton compte.
Cas typique vise : tu te surencheres toi-meme (ordre d'achat en double sur 2 de tes persos).

Usage :
  - double-clique dup_scan.bat  (ou: python dup_scan.py)
  - genere un rapport lisible + dup_orders.txt a cote.

Chemins DB par defaut (Mmd Windows) :
  main.db  -> %LOCALAPPDATA%/mmd.com/Mmd/db/main.db
  eve.db   -> %LOCALAPPDATA%/mmd.com/Mmd/resources/eve.db
"""
import os, sqlite3, sys

LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
MAIN_DB = os.path.join(LOCAL, "mmd.com", "Mmd", "db", "main.db")
EVE_DB  = os.path.join(LOCAL, "mmd.com", "Mmd", "resources", "eve.db")

def main():
    if not os.path.exists(MAIN_DB):
        print("ERREUR: base Mmd introuvable:\n  " + MAIN_DB)
        print("Lance Mmd une fois, ou edite MAIN_DB dans ce script.")
        return 1

    con = sqlite3.connect(MAIN_DB)
    cur = con.cursor()
    # noms des persos (id -> nom)
    names = {}
    try:
        for cid, nm in cur.execute("SELECT id, name FROM characters"):
            names[cid] = nm
    except Exception:
        pass

    eve_con = sqlite3.connect(EVE_DB) if os.path.exists(EVE_DB) else None
    ecur = eve_con.cursor() if eve_con else None

    def item_name(tid):
        if ecur:
            r = ecur.execute("SELECT typeName FROM invTypes WHERE typeID=?", (tid,)).fetchone()
            if r: return r[0]
        return f"typeID {tid}"

    def char_name(cid):
        return names.get(cid, str(cid))

    q = """
    SELECT mo.type_id, mo.location_id, mo.price, mo.type,
           COUNT(DISTINCT mo.character_id)               AS n_persos,
           COUNT(*)                                       AS n_ordres,
           GROUP_CONCAT(DISTINCT mo.character_id)         AS char_ids,
           SUM(mo.volume_remaining)                       AS vol_rest,
           SUM(mo.volume_entered)                         AS vol_ent
    FROM market_orders mo
    WHERE mo.volume_remaining > 0
    GROUP BY mo.type_id, mo.location_id, mo.price, mo.type
    HAVING n_persos >= 2
    ORDER BY n_ordres DESC, vol_rest DESC
    """
    rows = cur.execute(q).fetchall()

    lines = []
    lines.append("=" * 70)
    lines.append("  EVERNUS - DOUBLONS INTER-PERSONNAGES")
    lines.append("  (meme item + meme station + meme prix, >= 2 persos)")
    lines.append("=" * 70)
    lines.append(f"  Personnages connus: {', '.join(names.values()) if names else 'inconnus'}")
    lines.append(f"  Date scan         : {__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("")

    if not rows:
        lines.append("  AUCUN DOUBLON DETECTE. Tes persos ne se font pas concurrence.")
        lines.append("  (ordres actifs seulement / volume_remaining > 0)")
    else:
        for r in rows:
            tid, loc, price, typ, nc, no, cids, vr, ve = r
            side = "ACHAT (buy)" if typ == 0 else "VENTE (sell)"
            persos = [char_name(int(c)) for c in cids.split(",")]
            tag = "  <<< AUTO-CONCURRENCE (achat en double)" if typ == 0 else ""
            lines.append(f"  ITEM    : {item_name(tid)} (typeID {tid}){tag}")
            lines.append(f"  STATION : {loc}")
            lines.append(f"  PRIX    : {price:,} ISK  ({price/1e6:.2f} M)")
            lines.append(f"  SENS    : {side}")
            lines.append(f"  PERSOS  : {nc} -> {', '.join(persos)}")
            lines.append(f"  ORDRES  : {no}  | vol restante: {vr:,} / entree: {ve:,}")
            lines.append("-" * 70)

    report = "\n".join(lines) + "\n"
    print(report)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dup_orders.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[rapport ecrit] -> {out}")

    con.close()
    if eve_con: eve_con.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
