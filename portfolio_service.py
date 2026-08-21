#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestration read-only ESI -> SQLite -> moteur Asset/Transaction."""
from collections import defaultdict
import json
import threading
import time
import mmd_asset_tree as asset_tree
import mmd_corporation_orders as corporation_orders_esi
import mmd_core as core
import mmd_esi_portfolio as esi
import mmd_sso as sso
import migrations
import portfolio_engine as engine
import portfolio_asset_selection as asset_selection
import portfolio_fees as fees
import portfolio_saved_sources as saved_sources
import repositories.character_repository as characters
import repositories.corporation_order_repository as corporation_orders
import repositories.portfolio_repository as repo
_LOCK = threading.RLock()
_DISCOVERY = {"at": 0.0, "data": None}
_DISCOVERY_TTL = 300
def _discover(force=False):
    fingerprint = tuple((row["id"], tuple(sorted(sso.character_capabilities(row["id"]).items())))
                        for row in sso.connected_chars())
    with _LOCK:
        if not force and _DISCOVERY["data"] and _DISCOVERY.get("fingerprint") == fingerprint and time.time() - _DISCOVERY["at"] < _DISCOVERY_TTL:
            return _DISCOVERY["data"]
        data = esi.discover_sources()
        stamp = time.time() if not data.get("errors") else time.time() - (_DISCOVERY_TTL - 30)
        _DISCOVERY.update(at=stamp, data=data, fingerprint=fingerprint)
        for char in data.get("characters", []):
            if char.get("corporation_id"):
                characters.upsert_character(char["id"], char.get("name"),
                                            char["corporation_id"], 1)
        return data


def _options(discovered):
    chars = {str(row["id"]): row for row in discovered.get("characters", [])}
    operators = {}
    for row in chars.values():
        corp = row.get("corporation_id")
        if corp and row.get("capabilities", {}).get("corporation_wallet"):
            operators.setdefault(str(corp), row)
    divisions = []
    for corp_id, rows in discovered.get("divisions", {}).items():
        operator = operators.get(str(corp_id))
        if not operator:
            continue
        for row in rows:
            div = int(row["division_id"])
            name = row.get("name") or row.get("label") or f"Division {div}"
            divisions.append({"key": f"corp:{corp_id}:division:{div}",
                              "corporation_id": str(corp_id), "division_id": str(div),
                              "character_id": str(operator["id"]), "name": name,
                              "label": f"{name} · #{div}"})
    containers = []
    for char_id, rows in discovered.get("containers", {}).items():
        char_name = chars.get(str(char_id), {}).get("name") or f"Personnage {char_id}"
        for row in rows:
            item_id = str(row["item_id"])
            name = row.get("name") or row.get("label") or f"Conteneur {item_id}"
            containers.append({"key": f"char:{char_id}:container:{item_id}",
                               "character_id": str(char_id), "item_id": item_id,
                               "parent_item_id": None, "name": f"{char_name} · {name}",
                               "path": [char_name, name], "depth": int(row.get("depth") or 0),
                               "descendant_count": int(row.get("descendant_count") or 0)})
    return divisions, containers


def _match(options, source):
    key = str((source or {}).get("key") or "")
    return next((dict(row) for row in options if str(row["key"]) == key), None)


def _operator(corporation_id, capability):
    return next((int(row["id"]) for row in _discover(False).get("characters", [])
                 if int(row.get("corporation_id") or 0) == int(corporation_id)
                 and row.get("capabilities", {}).get(capability)), None)


def get_settings(force=False):
    migrations.migrate()
    try:
        discovered = _discover(force)
        divisions, containers = _options(discovered)
        saved = repo.get_settings()
        wallet = _match(divisions, saved.get("wallet_source"))
        assets = _match(containers, saved.get("asset_source"))
        return {"ok": True, "wallet_source": wallet, "asset_source": assets,
                "divisions": divisions, "containers": containers,
                "errors": discovered.get("errors", [])}
    except Exception as exc:
        return {"ok": False, "error": f"Découverte ESI impossible : {exc}",
                "divisions": [], "containers": []}


def save_settings(payload):
    migrations.migrate()
    try:
        value = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
        current = get_settings(False)
        if not current.get("ok"):
            return current
        wallet = _match(current["divisions"], value.get("wallet_source"))
        asset = _match(current["containers"], value.get("asset_source"))
        if not wallet:
            return {"ok": False, "error": "Division corporation invalide ou disparue."}
        if value.get("asset_source") and not asset:
            return {"ok": False, "error": "Conteneur personnel invalide ou disparu."}
        repo.save_setting("wallet_source", {k: wallet[k] for k in
                          ("key", "corporation_id", "division_id", "character_id", "name", "label")})
        repo.save_setting("asset_source", ({k: asset[k] for k in
                          ("key", "character_id", "item_id", "name", "path")} if asset else None))
        return {"ok": True, "wallet_source": wallet, "asset_source": asset}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _selection(filters=None, live=True):
    if not live:
        containers = _options(_DISCOVERY["data"])[1] if _DISCOVERY.get("data") else ()
        wallet, asset, error = saved_sources.load(
            (filters or {}).get("container_key"), containers)
        return {}, (None if error else (wallet, asset) if wallet else None), error
    settings = get_settings(live)
    if not settings.get("ok"):
        return settings, None, settings
    wallet = settings.get("wallet_source")
    if not wallet:
        return settings, None, {"ok": False, "error": "Choisissez une division corporation dans Settings."}
    requested = str((filters or {}).get("container_key") or "")
    asset = (next((row for row in settings["containers"] if row["key"] == requested), None)
             if requested else settings.get("asset_source"))
    return settings, (wallet, asset), None


def _sync(wallet, asset):
    corp, div, operator = int(wallet["corporation_id"]), int(wallet["division_id"]), int(wallet["character_id"])
    errors = []
    known = {row["transaction_id"] for row in repo.load_transactions("corporation", corp, div)}
    result = esi.fetch_corporation_transactions(corp, div, operator, known_ids=known)
    if result.ok:
        rows = [dict(row, owner_kind="corporation", owner_id=corp, division_id=div,
                     occurred_at=row.get("date"), is_personal=False) for row in result.data]
        repo.upsert_transactions(rows)
    else:
        errors.append(result.error.message)
    order_operator = _operator(corp, "corporation_orders")
    if order_operator:
        order_result = corporation_orders_esi.fetch_corporation_orders(corp, order_operator)
        if order_result.ok:
            corporation_orders.replace_orders(corp, order_result.data)
        else:
            errors.append(order_result.error.message)
    else:
        errors.append("Aucun token habilité aux ordres corporation")
    balance = esi.fetch_corporation_balance(corp, operator, div)
    if balance.ok:
        cash = str(balance.data["balance_cents"])
        balances = dict(repo.get_settings().get("cash_balances") or {})
        balances[wallet["key"]] = cash
        repo.save_setting("cash_balances", balances)
    else:
        errors.append(balance.error.message)
    if asset:
        cid = int(asset["character_id"])
        fetched = esi.fetch_character_assets(cid)
        if fetched.ok:
            parent_ids = {int(row["location_id"]) for row in fetched.data
                          if row.get("location_type") == "item"}
            named = esi.fetch_asset_names(cid, parent_ids)
            graph = asset_tree.build_asset_tree(fetched.data, named.data if named.ok else None)
            items = []
            for raw in fetched.data:
                item = dict(raw)
                iid, tid = int(item["item_id"]), int(item["type_id"])
                item.update(parent_item_id=graph.parent.get(iid), root_container_id=graph.root_id(iid),
                            hierarchy_depth=graph.depth(iid), item_name=core.iname(tid),
                            is_blueprint_copy=bool(item.get("is_blueprint_copy", False)))
                items.append(item)
            repo.save_asset_snapshot(cid, None, items)
        else:
            errors.append(fetched.error.message)
    contract_operator = _operator(corp, "corporation_contracts")
    contracts = (esi.fetch_corporation_contract_assets(corp, contract_operator)
                 if contract_operator else None)
    if contracts and contracts.ok:
        normalized = []
        for raw in contracts.data:
            row = dict(raw, owner_kind="corporation", owner_id=corp, division_id=0,
                       is_acquisition=True, allocated_cost_cents=raw.get("acquisition_cost_cents"),
                       contract_price_cents=raw.get("contract_total_cents", 0),
                       allocation_method="single_item" if raw.get("acquisition_cost_cents") is not None else "unallocated")
            normalized.append(row)
        repo.upsert_contract_assets(normalized)
    else:
        errors.append(contracts.error.message if contracts else
                      "Aucun token habilité aux contrats corporation")
    return errors


def _workspace(wallet, asset, errors=()):
    corp, div = int(wallet["corporation_id"]), int(wallet["division_id"])
    assets = asset_selection.load_selected(int(asset["character_id"]), int(asset["item_id"]))["items"] if asset else []
    parent_ids = {int(row["parent_item_id"]) for row in assets if row.get("parent_item_id")}
    for row in assets:
        row["is_container"] = int(row["item_id"]) in parent_ids or int(row["item_id"]) == int(asset["item_id"])
        row["name"] = row.get("item_name") or core.iname(row["type_id"])
    transactions = repo.load_transactions("corporation", corp, div)
    source = f"Corporation {corp} · {wallet['name']} (#{div})"
    for row in transactions:
        row.update(date=row.get("occurred_at"), name=core.iname(row["type_id"]), source_label=source)
        if row.get("fees_status") == "actual":
            row["actual_fee_cents"] = int(row.get("broker_fee_cents") or 0) + int(row.get("sales_tax_cents") or 0)
    transactions = fees.enrich(transactions)
    orders = fees.enrich(corporation_orders.load_orders(corp, div))
    for row in orders:
        row["name"] = core.iname(row["type_id"])
    contracts = repo.load_contract_assets("corporation", corp)
    for row in contracts:
        row.update(name=core.iname(row["type_id"]), acquired_at=row.get("completed_at") or row.get("issued_at") or "")
    type_ids = {int(row["type_id"]) for row in assets + transactions + orders + contracts}
    grouped = defaultdict(list)
    for row in repo.load_history(type_ids, region_id=10000002):
        grouped[int(row["type_id"])].append(row)
    cash = (repo.get_settings().get("cash_balances") or {}).get(wallet["key"], "0")
    data = engine.build_workspace(transactions=transactions, assets=assets, orders=orders,
                                  contracts=contracts, history=grouped, cash_cents=int(cash),
                                  source_label=source)
    repo.save_fifo_results("corporation", corp, div, data["transactions"], "portfolio_fifo_v1")
    data.update(stale=bool(errors), sync_errors=list(errors),
                wallet_source={k: wallet[k] for k in ("key", "corporation_id", "division_id")},
                asset_source=asset)
    return data


def get_workspace(filters=None, refresh=False):
    migrations.migrate()
    _settings, selected, error = _selection(filters, refresh)
    if error:
        return error
    wallet, asset = selected
    errors = _sync(wallet, asset) if refresh else []
    data = _workspace(wallet, asset, errors)
    data["cached"] = not refresh
    return data
