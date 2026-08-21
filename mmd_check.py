#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mmd Dashboard - ouvre Mmd, attend ton import, puis affiche en 1 bloc:
  1) DOUBLONS inter-personnages (meme item + meme station + meme prix sur >=2 persos)
  2) ORDRES ACTIFS (perso seul, vivants = last_seen IS NULL)
  3) PRIX DEPASSES (BUY: un autre offre + cher / SELL: un autre vend - cher)

Exclut volontairement corp_market_orders (doublon de tes ordres perso) et les
ordres morts (last_seen NON NULL = archive Mmd).

Usage: double-clique mmd_check.bat -> fais Import all (3 persos) + coche
'Show for all characters' + Import prices, puis appuie sur une touche.
"""
import os, sqlite3, sys, subprocess, datetime

LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
MAIN_DB = os.path.join(LOCAL, "mmd.com", "Mmd", "db", "main.db")
EVE_DB  = os.path.join(LOCAL, "mmd.com", "Mmd", "resources", "eve.db")
EXE     = r"E:\EVE\mmd\bin\Mmd.exe"


def open_mmd():
    if os.path.exists(EXE):
        try:
            subprocess.Popen([EXE]); return True
        except Exception as e:
            print(f"[!] impossible de lancer Mmd ({e})")
    return False


def main():
    if open_mmd():
        print("=" * 72)
        print("  MMD DASHBOARD - doublons + ordres a mettre a jour")
        print("=" * 72)
        print("  Mmd lance. Dans la fenetre:")
        print("   1) 'Import all' (3 persos)  + coche 'Show for all characters'")
        print("   2) 'Import prices' (tous persos / corporation orders)")
        print("  Puis reviens ici et appuie sur une touche.")
    else:
        print("  Mmd non trouve. Lance-le, fais les imports, puis appuie sur une touche.")
    print("-" * 72)
    input("  [appuie sur Entree quand les imports sont faits] ")

    if not os.path.exists(MAIN_DB):
        print("ERREUR: base introuvable:", MAIN_DB); return 1

    con = sqlite3.connect(MAIN_DB); cur = con.cursor()
    eve = sqlite3.connect(EVE_DB) if os.path.exists(EVE_DB) else None
    ecur = eve.cursor() if eve else None

    names = {r[0]: r[1] for r in cur.execute("SELECT id, name FROM characters")}
    def cname(cid): return names.get(cid, str(cid))
    def iname(tid):
        if ecur:
            r = ecur.execute("SELECT typeName FROM invTypes WHERE typeID=?", (tid,)).fetchone()
            if r: return r[0]
        return f"typeID {tid}"

    LIVE = "volume_remaining>0 AND last_seen IS NULL"  # ordres vivants, perso seul

    # ============ 1) DOUBLONS inter-persos ============
    # Meme item + meme station + meme sens, sur >=2 personnages DIFFERENTS.
    # On ignore le PRIX: meme si Mike et CHARACTER_ONE different de 1M, c'est quand meme
    # de l'auto-concurrence (ton ordre se fait concurrence a ton autre perso).
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
    dups = cur.execute(dup_q).fetchall()

    # ============ 2) ORDRES ACTIFS + 3) PRIX DEPASSES ============
    buy_tot = cur.execute(f"SELECT COUNT(*) FROM market_orders WHERE type=0 AND {LIVE}").fetchone()[0]
    sell_tot = cur.execute(f"SELECT COUNT(*) FROM market_orders WHERE type=1 AND {LIVE}").fetchone()[0]

    # mes ordres vivants comme ensemble a exclure (pour ne pas compter mes propres lignes)
    my_orders = set()
    for tid, loc, price, vol, typ in cur.execute(
        f"SELECT type_id, location_id, price, volume_remaining, type FROM market_orders WHERE {LIVE}"):
        my_orders.add((tid, loc, price, vol, typ))

    # livre public: pour chaque (item,station,sens,prix,vol) -> liste des timestamps 'issued'
    # (besoin du timestamp pour la regle EVE: prix EGAL + ordre public emis APRES le mien
    #  => il passe DEVANT mon ordre => je dois monter/baisser)
    ext = {}
    for tid, loc, typ, val, vol, iss in cur.execute(
        "SELECT type_id, location_id, type, value, volume_remaining, issued FROM external_orders"):
        ext.setdefault((tid, loc, typ, val, vol), []).append(iss or "")

    def others_better(tid, loc, typ, price, vol, my_issued):
        cnt = 0
        for (e_tid, e_loc, e_typ, e_val, e_vol), iss_list in ext.items():
            if e_tid != tid or e_loc != loc or e_typ != typ: continue
            if typ == 0:                       # BUY: devant moi si prix >  OU (prix == et emis apres)
                for iss in iss_list:
                    if e_val > price or (e_val == price and iss > my_issued):
                        cnt += 1; break
            else:                             # SELL: devant moi si prix <  OU (prix == et emis apres)
                for iss in iss_list:
                    if e_val < price or (e_val == price and iss > my_issued):
                        cnt += 1; break
        mine = 1 if (tid, loc, price, vol, typ) in my_orders else 0
        return max(0, cnt - mine)

    buy_pass = sell_pass = 0
    for tid, loc, price, vol, typ, iss in cur.execute(
        f"SELECT type_id, location_id, price, volume_remaining, type, issued FROM market_orders WHERE {LIVE}"):
        if others_better(tid, loc, typ, price, vol, iss or "") > 0:
            if typ == 0: buy_pass += 1
            else:        sell_pass += 1

    # ============ AFFICHAGE ============
    print("\n" + "=" * 72)
    print(f"  BILAN  ({datetime.datetime.now():%Y-%m-%d %H:%M})")
    print("=" * 72)
    print(f"  ORDRES ACTIFS  : BUY {buy_tot} | SELL {sell_tot} | TOTAL {buy_tot+sell_tot}")
    print(f"  A METTRE A JOUR: BUY {buy_pass} (prix depasse) | SELL {sell_pass} (prix depasse)")
    print(f"  DOUBLONS       : {len(dups)} (meme item+station+prix sur >=2 persos)")
    print("-" * 72)
    if dups:
        for r in dups:
            tid, loc, typ, nc, no, cids, prices, vr = r
            side = "ACHAT" if typ == 0 else "VENTE"
            persos = ", ".join(cname(int(c)) for c in cids.split(","))
            pr_list = ", ".join(f"{int(p)/1e6:.1f}M" for p in prices.split(","))
            tag = " <<< AUTO-CONCURRENCE" if typ == 0 else ""
            print(f"  [{side}] {iname(tid)}  prix: {pr_list}  {persos}  (vol {vr}){tag}")
    else:
        print("  Aucun doublon inter-personnage detecte.")
    print("=" * 72)
    print("  (prix depasse = acheteur offre + cher / vendeur vend - cher que toi)")
    print("=" * 72)

    con.close()
    if eve: eve.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
