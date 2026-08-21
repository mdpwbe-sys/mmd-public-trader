#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_store.py - pont entre la logique metier (scan/import) et la couche
repository SQLite. Centralise les ecritures pour que GUI/ESI/Watchdog n'aient
pas de SQL disperse.

Strategie de migration progressive (aucune perte) :
- SQLite est la SOURCE DE VERITE durable.
- Le JSON (last_scan_cache.json) reste un miroir rapide de boot (fallback).
- On ecrit dans les DEUX au debut ; le JSON sera retire plus tard.
"""
import time
import json
import database as db
import migrations as mig
import repositories.character_repository as cr
import repositories.order_repository as orr
import repositories.snapshot_repository as sr
import repositories.recommendation_repository as rrec


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def persist_scan(data, source="scan", snapshot_id=None):
    """Persiste un payload renderScan dans SQLite (ordres + snapshot + persos).

    data: dict issu de _scan_core (orders_full, characters, ...).
    Atomique: snapshot + ordres dans une seule transaction courte.
    """
    mig.migrate()  # assure le schema (idempotent)
    if not data or not data.get("orders_full"):
        return None
    snap_id = snapshot_id or ("scan_" + _now_iso().replace(":", "").replace("-", ""))
    orders_full = data["orders_full"]
    # persos vus
    chars = {}
    snap_orders = []
    for o in orders_full:
        cid = int(o.get("char_id") or o.get("character_id") or 0)
        chars[cid] = o.get("char_name") or chars.get(cid)
        o2 = {
            "order_id": o.get("order_id"),
            "character_id": cid,
            "type_id": o.get("type_id"),
            "station_id": o.get("station_id"),
            "side": o.get("side"),
            "price": (o.get("price_cents", 0) / 100.0),
            "volume_remain": o.get("vol_remaining", 0),
            "issued": o.get("issued"),
        }
        if o.get("range"):
            o2["range"] = o["range"]
        orr.upsert_order(o2, source_import_id=snap_id, last_seen_at=_now_iso())
        snap_orders.append({
            "order_id": o.get("order_id"), "type_id": o.get("type_id"),
            "location_id": o.get("station_id"), "side": o.get("side"),
            "price": o2["price"], "volume_remain": o.get("vol_remaining", 0),
            "min_volume": 1, "issued_at": o.get("issued"),
            "range": o.get("range", "region")})
    for cid, name in chars.items():
        cr.upsert_character(cid, name, None, 1)
    # snapshot atomique (rollback -> ancien intact)
    sr.save_market_snapshot(
        snap_id, source_type=source, region_id=10000002,
        orders_count=len(snap_orders), orders=snap_orders,
        source_fetch_id=source)
    return snap_id


def persist_import(orders, characters, snapshot_id=None):
    """Persiste un import d'ordres (liste de dicts + persos)."""
    mig.migrate()
    snap_id = snapshot_id or ("import_" + _now_iso().replace(":", "").replace("-", ""))
    snap_orders = []
    for o in orders:
        cid = int(o.get("char_id") or o.get("character_id") or 0)
        orr.upsert_order(o, source_import_id=snap_id, last_seen_at=_now_iso())
        snap_orders.append({
            "order_id": o.get("order_id"), "type_id": o.get("type_id"),
            "location_id": o.get("station_id"), "side": o.get("side"),
            "price": o.get("price"), "volume_remain": o.get("volume_remain", 0),
            "min_volume": 1, "issued_at": o.get("issued"),
            "range": o.get("range", "region")})
    for cid, name in (characters or {}).items():
        cr.upsert_character(int(cid), name, None, 1)
    sr.save_market_snapshot(
        snap_id, source_type="import", region_id=10000002,
        orders_count=len(snap_orders), orders=snap_orders,
        source_fetch_id="import")
    return snap_id


def record_events_from_scan(data, snapshot_id):
    """Genere les evenements d'ordre (order_detected / order_outbid / tied...).

    Ne conserve que les changements d'etat/prix/volume, avec retention bornee.
    """
    recorded = 0
    for o in data.get("orders_full", []):
        cid = int(o.get("char_id") or o.get("character_id") or 0)
        etype = "order_detected"
        if o.get("fifo_overtaken"):
            etype = "order_tied"
        elif o.get("status") in ("OUTBID_EXTERNAL", "COMPETING_ALT",
                                  "BEST_EXTERNAL_BUT_ALT_CONFLICT"):
            etype = "order_outbid"
        if orr.record_order_event(
            o.get("order_id"), cid, etype,
            new_price=(o.get("price_cents", 0) / 100.0) if o.get("price_cents") else None,
            new_volume_remain=o.get("vol_remaining", 0),
            snapshot_id=snapshot_id, reason="scan"):
            recorded += 1
    orr.prune_order_events(retention_days=90, max_events=50_000)
    return recorded


def load_latest_orders():
    """Lit les derniers ordres de CHAQUE perso (vue 'Tous') depuis SQLite.
    Retourne [] si vide (fallback JSON cote appelant)."""
    try:
        return orr.get_all_latest_character_orders()
    except Exception:
        return []


def save_recommendation(*a, **k):
    return rrec.save_recommendation(*a, **k)


def save_decision(*a, **k):
    return rrec.save_decision(*a, **k)


def save_outcome(*a, **k):
    return rrec.save_outcome(*a, **k)
