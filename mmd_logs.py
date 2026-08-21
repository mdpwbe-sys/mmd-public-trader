#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_logs.py - lecture des exports natifs EVE (C:\\Users\\Admin\\Documents\\EVE\\logs\\Marketlogs).

Deux types de fichiers generes par le client EVE:
  - "My Orders-*.txt"       -> TES ordres actifs (tous persos). Colonnes:
      orderID,typeID,charID,charName,regionID,regionName,solarSystemID,
      solarSystemName,stationID,stationName,range,bid,price,volEntered,
      volRemaining,minVolume,issueDate,orderState,duration,escrow,isCorp,...
  - "The Forge-<item>.txt"   -> livre de marche public d'un item (prix,vol,bid,station)

On utilise "My Orders" comme source d'ORDRES (offline, sans DB verrouillee).
Le livre public vient du fetch ESI (mmd_esi) car "The Forge-*" n'est genere
que pour les items ouverts dans le client.
"""
import os, glob, csv, datetime

LOGS_DIR = r"C:\Users\Admin\Documents\EVE\logs\Marketlogs"
LIVE_STATES = {0}  # orderState 0 = active


def _read_csv(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f, delimiter=",")
        rows = list(reader)
    if not rows: return [], []
    header = [h.strip() for h in rows[0]]
    return header, rows[1:]


def load_my_orders():
    """Retourne la liste de TES ordres actifs (perso seul, isCorp=False).
    Chaque dict: type_id, char_id, char_name, station_id, station_name,
                 side(0 BUY/1 SELL), price, vol_remaining, vol_entered,
                 min_volume, issued, order_id."""
    orders = []
    files = sorted(glob.glob(os.path.join(LOGS_DIR, "My Orders-*.txt")), reverse=True)
    seen = set()  # dedup si plusieurs fichiers se chevauchent
    for fp in files:
        header, rows = _read_csv(fp)
        if not header: continue
        idx = {h: i for i, h in enumerate(header)}
        for r in rows:
            if len(r) <= idx.get("orderID", 0): continue
            try:
                oid = r[idx["orderID"]]
                if oid in seen: continue
                seen.add(oid)
                state = int(r[idx["orderState"]])
                if state not in LIVE_STATES: continue
                # NOTE: EVE marque isCorp=True meme pour les ordres perso
                # (le accountOwnerID est le compte corp). On ne filtre PAS sur isCorp,
                # on se base sur orderState=0 (actif) + dedup orderID.
                side = 0 if r[idx["bid"]].strip().lower() == "true" else 1
                orders.append({
                    "order_id": oid,
                    "type_id": int(r[idx["typeID"]]),
                    "char_id": int(r[idx["charID"]]),
                    "char_name": r[idx["charName"]],
                    "station_id": int(r[idx["stationID"]]),
                    "station_name": r[idx["stationName"]],
                    "side": side,
                    "price": float(r[idx["price"]]),
                    "vol_remaining": int(float(r[idx["volRemaining"]])),
                    "vol_entered": int(float(r[idx["volEntered"]])),
                    "min_volume": int(float(r[idx["minVolume"]])),
                    "issued": r[idx["issueDate"]].strip(),
                })
            except (ValueError, KeyError, IndexError):
                continue
    return orders


def latest_my_orders_file():
    files = sorted(glob.glob(os.path.join(LOGS_DIR, "My Orders-*.txt")), reverse=True)
    return files[0] if files else None


def load_market_file(item_name):
    """Lit un fichier 'The Forge-<item>.txt' si present -> liste d'ordres publics."""
    safe = item_name.replace("/", "_").replace("\\", "_")
    fp = os.path.join(LOGS_DIR, f"The Forge-{safe}.txt")
    if not os.path.exists(fp): return []
    header, rows = _read_csv(fp)
    if not header: return []
    idx = {h: i for i, h in enumerate(header)}
    out = []
    for r in rows:
        try:
            out.append({
                "type_id": int(r[idx["typeID"]]),
                "station_id": int(r[idx["stationID"]]),
                "side": 0 if r[idx["bid"]].strip().lower() == "true" else 1,
                "price": float(r[idx["price"]]),
                "issued": r[idx["issueDate"]].strip(),
                "vol": int(float(r[idx["volRemaining"]])),
            })
        except (ValueError, KeyError, IndexError):
            continue
    return out


if __name__ == "__main__":
    o = load_my_orders()
    print(f"Mes ordres actifs (tous persos, hors corp): {len(o)}")
    buys = sum(1 for x in o if x["side"] == 0)
    sells = sum(1 for x in o if x["side"] == 1)
    print(f"  BUY {buys} | SELL {sells} | TOTAL {len(o)}")
    from collections import Counter
    c = Counter(x["char_name"] for x in o)
    for name, n in c.items():
        print(f"  {name}: {n}")
