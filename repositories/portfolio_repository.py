#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistance locale du portefeuille, isolee par proprietaire/division."""
import json
import time
import uuid

import database as db
import mmd_price as prx


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _scope(kind, owner_id, division_id=0, wallet=False):
    kind, oid, div = str(kind), int(owner_id), int(division_id)
    if kind not in ("character", "corporation") or oid <= 0:
        raise ValueError("scope portefeuille invalide")
    valid = div == 0 if kind == "character" else (1 <= div <= 7 if wallet else 0 <= div <= 7)
    if not valid:
        raise ValueError("division incompatible avec le proprietaire")
    return kind, oid, div


def _cents(row, cents_key, value_key=None, default=0):
    value = row.get(cents_key)
    if value is not None:
        return int(value)
    value = row.get(value_key) if value_key else None
    return prx.to_cents(value) if value is not None else default


def save_asset_snapshot(character_id, container_item_id, items, *, snapshot_id=None,
                        captured_at=None, source_fetch_id=None, coherent=True):
    """Remplace un meme snapshot perso atomiquement; conserve tous les anciens."""
    kind, oid, div = _scope("character", character_id)
    sid, at = snapshot_id or f"assets_{time.time_ns()}_{uuid.uuid4().hex[:8]}", captured_at or _now()
    root = int(container_item_id) if container_item_id is not None else None
    rows = []
    for item in items or ():
        rows.append((kind, oid, div, sid, int(item["item_id"]), int(item["type_id"]),
                     int(item["quantity"]), int(item["location_id"]),
                     item.get("location_type"), item.get("location_flag"),
                     item.get("parent_item_id"), item.get("root_container_id", root),
                     int(item.get("hierarchy_depth", 0)),
                     int(bool(item.get("is_singleton", item.get("singleton", False)))),
                     int(bool(item.get("is_blueprint_copy", False))), item.get("item_name")))

    def _body(con):
        con.execute("INSERT INTO asset_snapshots(owner_kind,owner_id,division_id,"
                    "snapshot_id,captured_at,coherent,items_count,source_fetch_id) "
                    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(owner_kind,owner_id,division_id,"
                    "snapshot_id) DO UPDATE SET captured_at=excluded.captured_at,"
                    "coherent=excluded.coherent,items_count=excluded.items_count,"
                    "source_fetch_id=excluded.source_fetch_id",
                    (kind, oid, div, sid, at, int(bool(coherent)), len(rows), source_fetch_id))
        con.execute("DELETE FROM asset_snapshot_items WHERE owner_kind=? AND owner_id=? "
                    "AND division_id=? AND snapshot_id=?", (kind, oid, div, sid))
        con.executemany("INSERT INTO asset_snapshot_items(owner_kind,owner_id,division_id,"
                        "snapshot_id,item_id,type_id,quantity,location_id,location_type,"
                        "location_flag,parent_item_id,root_container_id,hierarchy_depth,"
                        "is_singleton,is_blueprint_copy,item_name) VALUES (?,?,?,?,?,?,?,?,"
                        "?,?,?,?,?,?,?,?)", rows)
        return sid
    return db.atomic(_body)


def upsert_transactions(rows):
    """Upsert atomique append-safe; un echec ne purge jamais l'historique."""
    params, now = [], _now()
    for row in rows or ():
        kind, oid, div = _scope(row["owner_kind"], row["owner_id"], row.get("division_id", 0), True)
        params.append((kind, oid, div, int(row["transaction_id"]),
                       row.get("occurred_at") or row["date"], int(row["type_id"]),
                       int(row["location_id"]), row.get("client_id"), row.get("journal_ref_id"),
                       int(bool(row["is_buy"])), int(bool(row.get("is_personal", False))),
                       int(row["quantity"]), _cents(row, "unit_price_cents", "unit_price"),
                       _cents(row, "broker_fee_cents"), _cents(row, "sales_tax_cents"),
                       row.get("fees_status", "missing"), row.get("source_fetch_id"),
                       row.get("ingested_at", now)))
    sql = """INSERT INTO trade_transactions(owner_kind,owner_id,division_id,transaction_id,
             occurred_at,type_id,location_id,client_id,journal_ref_id,is_buy,is_personal,
             quantity,unit_price_cents,broker_fee_cents,sales_tax_cents,fees_status,
             source_fetch_id,ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(owner_kind,owner_id,division_id,transaction_id) DO UPDATE SET
             occurred_at=excluded.occurred_at,type_id=excluded.type_id,
             location_id=excluded.location_id,client_id=excluded.client_id,
             journal_ref_id=excluded.journal_ref_id,is_buy=excluded.is_buy,
             is_personal=excluded.is_personal,quantity=excluded.quantity,
             unit_price_cents=excluded.unit_price_cents,
             broker_fee_cents=CASE WHEN trade_transactions.fees_status='actual'
               AND excluded.fees_status!='actual' THEN trade_transactions.broker_fee_cents
               ELSE excluded.broker_fee_cents END,
             sales_tax_cents=CASE WHEN trade_transactions.fees_status='actual'
               AND excluded.fees_status!='actual' THEN trade_transactions.sales_tax_cents
               ELSE excluded.sales_tax_cents END,
             fees_status=CASE WHEN trade_transactions.fees_status='actual'
               AND excluded.fees_status!='actual' THEN 'actual' ELSE excluded.fees_status END,
             source_fetch_id=excluded.source_fetch_id,ingested_at=excluded.ingested_at"""
    return db.atomic(lambda con: con.executemany(sql, params).rowcount) if params else 0


def save_fifo_results(owner_kind, owner_id, division_id, results, fifo_version):
    kind, oid, div = _scope(owner_kind, owner_id, division_id, True)
    params = [(int(r.get("matched_quantity", 0)), r.get("realized_cost_cents"),
               r.get("realized_profit_cents"), r.get("fifo_status", "pending"), fifo_version,
               kind, oid, div, int(r["transaction_id"])) for r in results or ()]
    sql = "UPDATE trade_transactions SET matched_quantity=?,realized_cost_cents=?,"
    sql += "realized_profit_cents=?,fifo_status=?,fifo_version=? WHERE owner_kind=? "
    sql += "AND owner_id=? AND division_id=? AND transaction_id=?"
    return db.atomic(lambda con: con.executemany(sql, params).rowcount) if params else 0


def upsert_contract_assets(rows):
    """Upsert le lot fourni sans supprimer les contrats absents ou en erreur."""
    params, now = [], _now()
    for row in rows or ():
        kind, oid, div = _scope(row["owner_kind"], row["owner_id"], row.get("division_id", 0))
        allocated = _cents(row, "allocated_cost_cents", "allocated_cost", None)
        params.append((kind, oid, div, int(row["contract_id"]), int(row["record_id"]),
                       int(row["type_id"]), int(row["quantity"]), row.get("raw_quantity"),
                       int(bool(row.get("is_included", True))), int(bool(row.get("is_singleton", False))),
                       int(bool(row.get("is_acquisition", False))), row.get("issuer_id"),
                       row.get("acceptor_id"), row.get("contract_type"), row.get("contract_status"),
                       row.get("issued_at"), row.get("completed_at"),
                       _cents(row, "contract_price_cents", "contract_price"), allocated,
                       row.get("allocation_method"), row.get("source_fetch_id"),
                       row.get("updated_at", now)))
    sql = """INSERT INTO contract_assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(owner_kind,owner_id,division_id,contract_id,record_id) DO UPDATE SET
             type_id=excluded.type_id,quantity=excluded.quantity,raw_quantity=excluded.raw_quantity,
             is_included=excluded.is_included,is_singleton=excluded.is_singleton,
             is_acquisition=excluded.is_acquisition,issuer_id=excluded.issuer_id,
             acceptor_id=excluded.acceptor_id,contract_type=excluded.contract_type,
             contract_status=excluded.contract_status,issued_at=excluded.issued_at,
             completed_at=excluded.completed_at,contract_price_cents=excluded.contract_price_cents,
             allocated_cost_cents=COALESCE(excluded.allocated_cost_cents,
                                           contract_assets.allocated_cost_cents),
             allocation_method=CASE WHEN excluded.allocated_cost_cents IS NULL AND
               contract_assets.allocated_cost_cents IS NOT NULL THEN contract_assets.allocation_method
               ELSE excluded.allocation_method END,source_fetch_id=excluded.source_fetch_id,
             updated_at=excluded.updated_at"""
    return db.atomic(lambda con: con.executemany(sql, params).rowcount) if params else 0


def save_setting(key, value, *, owner_kind="global", owner_id=0, division_id=0):
    kind, oid, div = str(owner_kind), int(owner_id), int(division_id)
    if kind != "global":
        kind, oid, div = _scope(kind, oid, div)
    elif oid or div:
        raise ValueError("le scope global doit etre (0,0)")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    sql = "INSERT INTO portfolio_settings VALUES (?,?,?,?,?,?) ON CONFLICT(owner_kind,"
    sql += "owner_id,division_id,setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_at=excluded.updated_at"
    return db.atomic(lambda con: con.execute(
        sql, (kind, oid, div, str(key), encoded, _now())).rowcount)


def get_settings(*, owner_kind="global", owner_id=0, division_id=0):
    with db.connection() as con:
        rows = con.execute("SELECT setting_key,setting_value FROM portfolio_settings WHERE "
                           "owner_kind=? AND owner_id=? AND division_id=?",
                           (owner_kind, int(owner_id), int(division_id))).fetchall()
    result = {}
    for row in rows:
        try:
            result[row["setting_key"]] = json.loads(row["setting_value"])
        except (TypeError, json.JSONDecodeError):
            result[row["setting_key"]] = row["setting_value"]
    return result


def load_transactions(owner_kind, owner_id, division_id):
    kind, oid, div = _scope(owner_kind, owner_id, division_id, True)
    with db.connection() as con:
        return [dict(r) for r in con.execute("SELECT * FROM trade_transactions WHERE "
                "owner_kind=? AND owner_id=? AND division_id=? ORDER BY occurred_at,transaction_id",
                (kind, oid, div)).fetchall()]


def load_latest_assets(character_id, container_item_id=None):
    with db.connection() as con:
        head = con.execute("SELECT * FROM asset_snapshots WHERE owner_kind='character' "
                           "AND owner_id=? AND division_id=0 AND coherent=1 "
                           "ORDER BY captured_at DESC,snapshot_id DESC LIMIT 1",
                           (int(character_id),)).fetchone()
        if not head:
            return {"snapshot": None, "items": []}
        sql = "SELECT * FROM asset_snapshot_items WHERE owner_kind='character' AND owner_id=? AND division_id=0 AND snapshot_id=?"
        args = [int(character_id), head["snapshot_id"]]
        if container_item_id is not None:
            sql += " AND root_container_id=?"; args.append(int(container_item_id))
        return {"snapshot": dict(head), "items": [dict(r) for r in con.execute(sql, args)]}


def load_contract_assets(owner_kind, owner_id, division_id=None):
    with db.connection() as con:
        sql, args = "SELECT * FROM contract_assets WHERE owner_kind=? AND owner_id=?", [owner_kind, int(owner_id)]
        if division_id is not None:
            sql += " AND division_id=?"; args.append(int(division_id))
        return [dict(r) for r in con.execute(sql + " ORDER BY completed_at,contract_id,record_id", args)]


def load_orders(character_id=None):
    where, args = "state='active'", []
    if character_id is not None:
        where += " AND character_id=?"; args.append(int(character_id))
    sql = f"""WITH batches AS (SELECT character_id,COALESCE(source_fetch_id,source_import_id,'') batch,
             MAX(last_seen_at) seen FROM character_orders WHERE {where} GROUP BY character_id,batch),
             latest AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY character_id ORDER BY seen DESC,batch DESC) rn FROM batches)
             SELECT o.* FROM character_orders o JOIN latest b ON b.character_id=o.character_id
             AND b.batch=COALESCE(o.source_fetch_id,o.source_import_id,'') AND b.rn=1
             WHERE o.state='active' ORDER BY o.character_id,o.type_id,o.is_buy_order"""
    with db.connection() as con:
        return [dict(r) for r in con.execute(sql, args)]


def load_history(type_ids, region_id=None, since=None):
    ids = sorted({int(t) for t in type_ids or ()})
    if not ids:
        return []
    marks, args = ",".join("?" for _ in ids), list(ids)
    sql = f"SELECT * FROM historical_market_daily WHERE type_id IN ({marks})"
    if region_id is not None:
        sql += " AND region_id=?"; args.append(int(region_id))
    if since is not None:
        sql += " AND date>=?"; args.append(str(since))
    with db.connection() as con:
        return [dict(r) for r in con.execute(sql + " ORDER BY type_id,date", args)]


def load_cached_bundle(character_id, container_item_id=None, *, wallet_owner_kind="character",
                       wallet_owner_id=None, wallet_division_id=0, contract_owner_kind=None,
                       contract_owner_id=None, contract_division_id=None):
    wallet_id = int(wallet_owner_id or character_id)
    contract_kind = contract_owner_kind or wallet_owner_kind
    contract_id = int(contract_owner_id or wallet_id)
    return {"assets": load_latest_assets(character_id, container_item_id),
            "transactions": load_transactions(wallet_owner_kind, wallet_id, wallet_division_id),
            "contracts": load_contract_assets(contract_kind, contract_id, contract_division_id),
            "orders": load_orders(character_id),
            "settings": get_settings()}
