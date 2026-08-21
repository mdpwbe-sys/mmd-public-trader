#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
snapshot_repository.py - snapshots de marche, structures Upwell, fetchs ESI, imports.

save_market_snapshot est ATOMIQUE:
  BEGIN -> snapshot + ses ordres + etats structures + evenements -> COMMIT
  echec -> ROLLBACK -> ancien snapshot coherent intact (jamais purge).
Un 403/404/429/503 ne supprime rien: on marque stale, on conserve.
"""
import time
import json
import database as db
import mmd_price as prx


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def save_market_snapshot(snapshot_id, *, source_type, region_id=None,
                         structure_id=None, type_id=None, fetched_at=None,
                         expires_at=None, last_modified=None, pages_count=0,
                         orders_count=0, coherent=True, stale=False,
                         source_fetch_id=None, orders=None):
    """Ecrit le snapshot + ses ordres dans UNE transaction courte (atomique + retry).

    orders: liste de dicts du livre public (location_id, type_id, side,
    price, volume_remain, min_volume, issued_at, range, order_id).
    En cas d'erreur SUR un ordre, le ROLLBACK annule tout le snapshot.
    """
    fetched_at = fetched_at or _now()
    snap = (snapshot_id, source_type, region_id, structure_id, type_id,
            fetched_at, expires_at, last_modified, pages_count, orders_count,
            1 if coherent else 0, 1 if stale else 0, source_fetch_id)
    params = []
    if orders:
        for o in orders:
            params.append((
                snapshot_id, str(o.get("order_id")),
                int(o["type_id"]), int(o["location_id"]),
                o.get("system_id"), o.get("region_id"),
                1 if o.get("side") == 0 else 0,
                str(o.get("range", "region")),
                prx.to_cents(o["price"]),
                int(o.get("volume_remain", 0)),
                int(o.get("min_volume", 1)),
                o.get("issued_at")))

    def _body(con):
        con.execute(
            "INSERT OR REPLACE INTO market_snapshots("
            "snapshot_id, source_type, region_id, structure_id, type_id, "
            "fetched_at, expires_at, last_modified, pages_count, orders_count, "
            "coherent, stale, source_fetch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            snap)
        if params:
            con.executemany(
                "INSERT OR REPLACE INTO market_snapshot_orders("
                "snapshot_id, order_id, type_id, location_id, system_id, "
                "region_id, is_buy_order, range, price_cents, volume_remain, "
                "min_volume, issued_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                params)
    return db.atomic(_body)


def get_snapshot(snapshot_id):
    with db.connection() as con:
        s = con.execute("SELECT * FROM market_snapshots WHERE snapshot_id=?",
                        (snapshot_id,)).fetchone()
        if not s:
            return None
        orders = [dict(r) for r in con.execute(
            "SELECT * FROM market_snapshot_orders WHERE snapshot_id=?",
            (snapshot_id,)).fetchall()]
        d = dict(s)
        d["orders"] = orders
        return d


def save_esi_fetch(fetch_id, *, endpoint, character_id=None, requested_at=None,
                   completed_at=None, http_status=None, etag=None,
                   expires_at=None, last_modified=None, pages_expected=None,
                   pages_received=None, coherent=True, rate_limit_metadata=None,
                   error_message=None):
    f = (fetch_id, endpoint, character_id and int(character_id),
         requested_at or _now(), completed_at, http_status, etag,
         expires_at, last_modified, pages_expected, pages_received,
         1 if coherent else 0,
         json.dumps(rate_limit_metadata) if rate_limit_metadata else None,
         error_message)

    def _body(con):
        con.execute(
            "INSERT OR REPLACE INTO esi_fetches("
            "fetch_id, endpoint, character_id, requested_at, completed_at, "
            "http_status, etag, expires_at, last_modified, pages_expected, "
            "pages_received, coherent, rate_limit_metadata_json, error_message) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", f)
    return db.atomic(_body)


def save_structure(structure_id, *, name=None, solar_system_id=None,
                   region_id=None, owner_fee_rate=None, last_info_success_at=None):
    st = (int(structure_id), name, solar_system_id, region_id,
          owner_fee_rate, last_info_success_at or _now())

    def _body(con):
        con.execute(
            "INSERT OR REPLACE INTO structures(structure_id, structure_name, "
            "solar_system_id, region_id, owner_fee_rate, last_info_success_at) "
            "VALUES (?,?,?,?,?,?)", st)
    return db.atomic(_body)


def save_structure_access(structure_id, character_id, access_status,
                          http_status=None, success_at=None):
    """Etat d'acces PAR perso. Un seul 403/404/503 ne purge RIEN."""
    sa = (int(structure_id), int(character_id), access_status, _now(),
          success_at, http_status)

    def _body(con):
        con.execute(
            "INSERT OR REPLACE INTO structure_access("
            "structure_id, character_id, access_status, last_checked_at, "
            "last_success_at, last_http_status) VALUES (?,?,?,?,?,?)", sa)
    return db.atomic(_body)


def save_export(import_id, *, character_id, source_path, source_filename,
                file_size, modified_at, file_hash, imported_at=None,
                row_count=0, status="ok", error_message=None):
    ex = (import_id, character_id and int(character_id), source_path,
          source_filename, file_size, modified_at, file_hash,
          imported_at or _now(), row_count, status, error_message)

    def _body(con):
        con.execute(
            "INSERT OR REPLACE INTO market_exports("
            "import_id, character_id, source_path, source_filename, file_size, "
            "modified_at, file_hash, imported_at, row_count, status, "
            "error_message) VALUES (?,?,?,?,?,?,?,?,?,?,?)", ex)
    return db.atomic(_body)
