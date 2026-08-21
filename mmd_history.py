#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mmd -> Vault Obsidian (Memorie)
====================================
Lit la base Mmd et alimente le vault avec:
  1) Historique par item vendu (prix min/max/moyen, derniere vente)
  2) Alertes "ventes a part" = prix de vente hors de l'historique (scoop/perte)
  3) Bilan doublons + prix depasses (delegue a la logique de mmd_check)

Vault: Mmd Memorie (chemin raw dans VAULT ci-dessous)
Base : %LOCALAPPDATA%/mmd.com/Mmd/db/main.db
"""
import os, sqlite3, sys, datetime, re

LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
MAIN_DB = os.path.join(LOCAL, "mmd.com", "Mmd", "db", "main.db")
EVE_DB  = os.path.join(LOCAL, "mmd.com", "Mmd", "resources", "eve.db")
VAULT   = r"E:\EVE\mmd\Memorie\Mmd"
HIST_DIR = os.path.join(VAULT, "Historique")

# Seuil d'anomalie: prix de vente > 2x ou < 0.5x la moyenne historique de l'item
MULT_HI = 2.0
MULT_LO = 0.5

def safe_name(nm):
    return re.sub(r'[\\/:*?"<>|#]', '_', nm)[:60]

def main():
    if not os.path.exists(MAIN_DB):
        print("ERREUR base:", MAIN_DB); return 1
    os.makedirs(HIST_DIR, exist_ok=True)

    con = sqlite3.connect(MAIN_DB); cur = con.cursor()
    eve = sqlite3.connect(EVE_DB) if os.path.exists(EVE_DB) else None
    ecur = eve.cursor() if eve else None
    def iname(tid):
        if ecur:
            r = ecur.execute("SELECT typeName FROM invTypes WHERE typeID=?", (tid,)).fetchone()
            if r: return r[0]
        return f"typeID_{tid}"

    # ---- historique ventes par item ----
    hist = cur.execute("""
        SELECT type_id, COUNT(*) n, MIN(price) pmin, MAX(price) pmax, AVG(price) pavg,
               MAX(timestamp) last_sale, SUM(quantity) qty
        FROM wallet_transactions WHERE type=1 GROUP BY type_id
    """).fetchall()

    alerts = []
    written = 0
    for tid, n, pmin, pmax, pavg, last, qty in hist:
        nm = iname(tid)
        fn = safe_name(nm)
        path = os.path.join(HIST_DIR, f"{fn}.md")
        content = f"""# {nm}

typeID: {tid}
Ventes totales: {n} | Quantite: {qty:,}
Prix historique: min={pmin:,.0f}  max={pmax:,.0f}  moyen={pavg:,.0f}
Derniere vente: {last}

## Alertes ventes a part (vs moyenne {pavg:,.0f})
"""
        # lignes individuelles anormales
        anom = cur.execute("""
            SELECT timestamp, price, quantity, location_id
            FROM wallet_transactions
            WHERE type_id=? AND type=1 AND (price > ? OR price < ?)
            ORDER BY timestamp DESC
        """, (tid, pavg*MULT_HI, pavg*MULT_LO)).fetchall()
        if anom:
            for ts, pr, q, loc in anom:
                kind = "HAUT (scoop?)" if pr > pavg*MULT_HI else "BAS (perte?)"
                content += f"- {ts} : {pr:,.0f} x{q:,} @ {loc}  -> {kind} (moy {pavg:,.0f})\n"
                alerts.append((nm, ts, pr, q, kind, pavg))
        else:
            content += "_Aucune vente a part detectee._\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written += 1

    # ---- note d'alertes globale ----
    acontent = f"""# Alertes - Ventes a part

Genere le {datetime.datetime.now():%Y-%m-%d %H:%M}
Seuil: prix > {MULT_HI:.0f}x ou < {MULT_LO:.1f}x la moyenne historique de l'item.
Nb items historises: {len(hist)}  |  Nb alertes: {len(alerts)}

## {len(alerts)} ventes a part
"""
    if alerts:
        for nm, ts, pr, q, kind, pavg in sorted(alerts, key=lambda x: x[2], reverse=True):
            fn = safe_name(nm)
            acontent += f"- [[Historique/{fn}|{nm}]] : {ts} -> {pr:,.0f} x{q:,} **{kind}** (moy {pavg:,.0f})\n"
    else:
        acontent += "_Aucune vente a part sur la periode analyse._\n"
    with open(os.path.join(VAULT, "1. Alertes - Ventes a part.md"), "w", encoding="utf-8") as f:
        f.write(acontent)

    print(f"[vault] {written} notes d'historique ecrites dans {HIST_DIR}")
    print(f"[vault] {len(alerts)} alertes ventes a part -> '1. Alertes - Ventes a part.md'")

    con.close()
    if eve: eve.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
