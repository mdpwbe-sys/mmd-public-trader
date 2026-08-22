#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import du fichier d'export EVE « Export your market orders » (meme format
que les logs 'My Orders-*.txt').

Le fichier est un CSV avec entete :
  orderID,typeID,charID,charName,regionID,regionName,solarSystemID,
  solarSystemName,stationID,stationName,range,bid,price,volEntered,
  volRemaining,minVolume,issueDate,orderState,duration,escrow,isCorp,
  accountID,accountOwnerID,accountKey

Regles (identiques a Mmd) :
  - orderState = 0 -> ordre actif (on ignore les autres etats).
  - isCorp = True MEME pour les ordres personnels -> on ignore isCorp,
    on se fie a charID/charName.
  - bid = True -> BUY (side 0) ; bid = False -> SELL (side 1).
  - On charge les ordres pour le perso selectionne (ou tous si vue globale).
"""

import csv
import os
import sqlite3
import time
from platform_state import state_path

HERE = os.path.dirname(os.path.abspath(__file__))
# eve.db (SDE) is NOT bundled in the public build.
EVE_DB = None
import mmd_price as prx  # prix en Decimal/centiemes (jamais float)


def _iname(tid):
    try:
        con = sqlite3.connect(EVE_DB)
        r = con.execute("SELECT typeName FROM invTypes WHERE typeID=?", (tid,)).fetchone()
        con.close()
        return r[0] if r else f"typeID {tid}"
    except Exception:
        return f"typeID {tid}"


def parse_export(path):
    """Parse le fichier d'export EVE.
    Retourne (orders, chars_seen) ou lève ValueError si format inconnu.
      orders: liste de dicts pret pour _scan_core / renderScan
      chars_seen: {char_id: char_name} presents dans le fichier (ordres actifs)
    """
    if not os.path.exists(path):
        raise ValueError("fichier introuvable")
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "orderID" not in reader.fieldnames:
            raise ValueError("format de fichier inconnu (entete orderID absente)")
        orders = []
        chars_seen = {}
        for row in reader:
            try:
                state = int(row.get("orderState", "0") or 0)
            except ValueError:
                state = -1
            if state != 0:
                continue  # seul les ordres actifs
            try:
                tid = int(float(row["typeID"]))
                char_id = int(float(row["charID"]))
                station_id = int(float(row["stationID"]))
                price_cents = prx.to_cents(row["price"])  # centiemes d'ISK (entier)
                vol = int(float(row.get("volRemaining", "0") or 0))
                bid = str(row.get("bid", "")).strip().lower() == "true"
            except (KeyError, ValueError, TypeError):
                continue
            char_name = row.get("charName", "") or str(char_id)
            chars_seen[char_id] = char_name
            orders.append({
                "order_id": str(row.get("orderID", "")),
                "type_id": tid,
                "char_id": char_id,
                "char_name": char_name,
                "station_id": station_id,
                "station_name": row.get("stationName", "") or str(station_id),
                "side": 0 if bid else 1,
                "price_cents": price_cents,
                "price": prx.from_cents(price_cents),  # Decimal ISK exact (pour debug/affichage)
                "vol_remaining": vol,
                "issued": (row.get("issueDate", "") or "")[:19].replace("T", " "),
                "range": row.get("range", ""),
            })
    if not orders:
        raise ValueError("aucun ordre actif (orderState=0) dans le fichier")
    return orders, chars_seen


def verify_character(orders, chars_seen, sel_char_id, sso_chars):
    """Verifie que le fichier correspond au perso selectionne / connecte.
    sel_char_id: int ou None (vue globale)
    sso_chars: liste de {id, name} connectes
    Retourne (ok, reason, matched_char_id).
      - si sel_char_id fourni: le fichier doit contenir CE perso.
      - si vue globale: le fichier doit contenir au moins un perso connecte SSO
        (ou n'importe quel perso si aucun SSO).
    """
    file_char_ids = {int(c) for c in chars_seen}
    if sel_char_id is not None:
        if int(sel_char_id) not in file_char_ids:
            names = ", ".join(chars_seen.values()) or "(aucun perso dans le fichier)"
            return False, (
                f"Le fichier exporte les ordres de : {names}. "
                f"Mais le perso selectionne est {sel_char_id}. "
                f"Exporte les ordres du BON perso dans EVE, ou selectionne "
                f"le bon chip avant d'importer."), None
        return True, "", int(sel_char_id)
    # vue globale
    sso_ids = {int(c["id"]) for c in (sso_chars or [])}
    if sso_ids:
        common = file_char_ids & sso_ids
        if not common:
            names = ", ".join(chars_seen.values())
            sso_names = ", ".join(c["name"] for c in (sso_chars or []))
            return False, (
                f"Le fichier contient : {names}. Aucun ne correspond aux persos "
                f"connectes SSO ({sso_names}). Exporte les ordres du bon perso."), None
        # on garde le 1er perso commun comme reference
        return True, "", sorted(common)[0]
    return True, "", None


def merge_visible_orders(snapshots, visible_orders, imported_at=None):
    """Fusionne la derniere vue Refresh dans les snapshots par personnage.

    Les ordres de orders_full sont normalises vers le format attendu par
    build_payload. Un import ulterieur remplace ensuite uniquement le
    personnage present dans son fichier.
    """
    merged = {int(cid): dict(snap) for cid, snap in (snapshots or {}).items()}
    grouped = {}
    for raw in visible_orders or []:
        try:
            cid = int(raw.get("char_id"))
            tid = int(raw.get("type_id"))
            station_id = int(raw.get("station_id"))
        except (TypeError, ValueError):
            continue
        price_cents = raw.get("price_cents")
        if price_cents is None:
            price_cents = prx.to_cents(raw.get("price", 0))
        price_cents = int(price_cents)
        order = dict(raw)
        order.update({
            "order_id": str(raw.get("order_id", "")), "type_id": tid,
            "char_id": cid, "char_name": raw.get("char_name", str(cid)),
            "station_id": station_id, "side": int(raw.get("side", 1)),
            "price_cents": price_cents, "price": prx.from_cents(price_cents),
            "vol_remaining": int(raw.get("vol_remaining", raw.get("vol", 0)) or 0),
            "issued": raw.get("issued", ""), "range": raw.get("range", "region"),
        })
        grouped.setdefault(cid, []).append(order)
    if not grouped:
        return merged
    if imported_at is None:
        imported_at = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    for cid, orders in grouped.items():
        previous = merged.get(cid, {})
        merged[cid] = {
            "character_id": cid,
            "character_name": orders[0].get("char_name") or previous.get("character_name", str(cid)),
            "imported_at": imported_at, "source_file": "last_refresh", "orders": orders,
        }
    return merged


def build_payload(orders, sel_char_id, public_orders=None):
    """Construit le payload renderScan a partir des ordres importes.
    Filtre par sel_char_id si fourni. Calcule l'ecart via _scan_core si un
    livre public est fourni, sinon ecart = None (affichage -)."""
    import mmd_core as core
    if sel_char_id is not None:
        filt = [o for o in orders if int(o["char_id"]) == int(sel_char_id)]
    else:
        filt = orders
    if not filt:
        return {"ok": False, "error": "Aucun ordre pour ce perso dans le fichier"}
    # nom des persos pour les chips
    chars = {}
    for o in orders:
        chars[o["char_id"]] = o["char_name"]
    if public_orders is not None:
        data = core._scan_core(filt, public_orders, "Import fichier EVE")
        data["characters"] = list(chars.values())
        data["sso_chars"] = [{"id": cid, "name": n} for cid, n in chars.items()]
        return data
    # sans livre public: on affiche juste les ordres (pas d'ecart)
    names = {}
    for o in filt:
        names[o["char_id"]] = o["char_name"]
    orders_full = []
    for o in filt:
        orders_full.append({
            "type_id": o["type_id"], "name": _iname(o["type_id"]),
            "side": o["side"], "price": o["price"], "vol_remaining": o["vol_remaining"],
            "station_id": o["station_id"], "station_name": o.get("station_name", ""),
            "issued": o.get("issued", ""), "char_id": o["char_id"],
            "char_name": o["char_name"], "order_id": o["order_id"],
            "gap_m": None, "gap_isk": None, "new_price_m": None,
            "new_price_cents": o["price_cents"], "fifo_overtaken": False,
            "needs_update": False, "status": "OK", "competing_alt": False,
        })
    return {
        "ok": True, "timestamp": "import", "source": "Import fichier EVE",
        "counts_timestamp_ms": int(time.time() * 1000),
        "orders_to_update": 0, "buy_to_update": 0, "sell_to_update": 0,
        "orders_to_update_by_char": {
            str(cid): {"total": 0, "buy": 0, "sell": 0} for cid in names
        },
        "duplicates": 0, "orders_full": orders_full,
        "characters": list(names.values()),
        "sso_chars": [{"id": cid, "name": n} for cid, n in names.items()],
    }
