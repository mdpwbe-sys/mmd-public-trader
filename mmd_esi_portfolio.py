#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wrappers read-only ESI pour sources portefeuille, assets et contrats."""
import mmd_asset_tree as asset_tree
import mmd_contracts as contracts
import mmd_esi_auth as auth
import mmd_sso as sso


def _positive(value, label):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} invalide") from exc
    if number <= 0:
        raise ValueError(f"{label} invalide")
    return number


def _division(value):
    division_id = _positive(value, "division_id")
    if division_id > 7:
        raise ValueError("division_id invalide")
    return division_id


def _success(source, data, headers=None):
    return auth.EsiResult(data, headers or source.headers, status=source.status,
                          from_cache=source.from_cache)


def _scope(char_id, capability, path):
    if not sso.character_capabilities(char_id).get(capability, False):
        return auth.failure(path, "missing_scope",
                            f"Scope ESI manquant: {capability}", 403)
    return None


def _error_dict(result):
    err = result.error
    return {"kind": err.kind, "status": err.status,
            "message": err.message} if err else None


def fetch_character_info(char_id):
    cid = _positive(char_id, "character_id")
    path = f"/v5/characters/{cid}/"
    result = auth.get_json(path, cid)
    if not result.ok:
        return result
    try:
        raw = dict(result.data)
        data = {"character_id": cid, "name": str(raw["name"]),
                "corporation_id": _positive(raw["corporation_id"], "corporation_id")}
    except (TypeError, KeyError, ValueError):
        return auth.failure(path, "invalid_payload", "Identite ESI invalide")
    return _success(result, data)


def _member(corp_id, char_id, path):
    info = fetch_character_info(char_id)
    if not info.ok:
        return info
    if info.data["corporation_id"] != corp_id:
        return auth.failure(path, "corporation_mismatch",
                            "Le token n'appartient pas a cette corporation", 403)
    return None


def _fallback_divisions():
    return [{"division_id": number, "name": None,
             "label": f"Division {number}", "custom_name": False}
            for number in range(1, 8)]


def fetch_corporation_divisions(corp_id, char_id):
    corp_id = _positive(corp_id, "corporation_id")
    path = f"/v1/corporations/{corp_id}/divisions/"
    denied = _member(corp_id, char_id, path)
    if denied:
        return denied
    missing = _scope(char_id, "corporation_divisions", path)
    if missing:
        return auth.EsiResult(_fallback_divisions(),
                              {"X-Division-Names": "missing_scope"}, status=403)
    result = auth.get_json(path, char_id)
    if not result.ok:
        if result.error.kind == "forbidden":
            return auth.EsiResult(_fallback_divisions(),
                                  {"X-Division-Names": "forbidden"}, status=403)
        return result
    divisions = _fallback_divisions()
    try:
        for row in (result.data or {}).get("wallet", []):
            number = _division(row["division"])
            name = str(row.get("name") or "").strip() or None
            divisions[number - 1].update(
                name=name, label=name or f"Division {number}", custom_name=bool(name))
    except (AttributeError, KeyError, TypeError, ValueError):
        return auth.failure(path, "invalid_payload", "Divisions ESI invalides")
    return _success(result, divisions)


def fetch_corporation_balance(corp_id, char_id, division_id):
    corp_id, division_id = _positive(corp_id, "corporation_id"), _division(division_id)
    path = f"/v1/corporations/{corp_id}/wallets/"
    denied = _member(corp_id, char_id, path) or _scope(
        char_id, "corporation_wallet", path)
    if denied:
        return denied
    result = auth.get_json(path, char_id)
    if not result.ok:
        return result
    try:
        row = next(row for row in result.data
                   if int(row["division"]) == division_id)
        data = {"corporation_id": corp_id, "division_id": division_id,
                "balance_cents": contracts.to_cents(row["balance"])}
    except (KeyError, TypeError, ValueError, StopIteration):
        return auth.failure(path, "invalid_payload", "Solde de division absent")
    return _success(result, data)


def fetch_corporation_transactions(corp_id, division_id, char_id, **options):
    corp_id, division_id = _positive(corp_id, "corporation_id"), _division(division_id)
    path = f"/v1/corporations/{corp_id}/wallets/{division_id}/transactions/"
    denied = _member(corp_id, char_id, path) or _scope(
        char_id, "corporation_wallet", path)
    return denied or contracts.fetch_transactions(path, char_id, **options)


def fetch_character_transactions(char_id, **options):
    cid = _positive(char_id, "character_id")
    path = f"/v1/characters/{cid}/wallet/transactions/"
    denied = _scope(cid, "character_wallet", path)
    return denied or contracts.fetch_transactions(path, cid, **options)


def fetch_character_assets(char_id):
    cid = _positive(char_id, "character_id")
    path = f"/v4/characters/{cid}/assets/"
    denied = _scope(cid, "character_assets", path)
    if denied:
        return denied
    result = auth.fetch_all_pages(path, cid)
    if not result.ok:
        return result
    try:
        rows = list({int(row["item_id"]): dict(row) for row in result.data}.values())
    except (KeyError, TypeError, ValueError):
        return auth.failure(path, "invalid_payload", "Assets ESI invalides")
    return _success(result, rows)


def fetch_asset_names(char_id, item_ids):
    cid = _positive(char_id, "character_id")
    path = f"/v1/characters/{cid}/assets/names/"
    denied = _scope(cid, "character_assets", path)
    if denied:
        return denied
    try:
        ids = list(dict.fromkeys(_positive(value, "item_id") for value in item_ids))
    except ValueError:
        return auth.failure(path, "invalid_body", "item_id invalide")
    if not ids:
        return auth.EsiResult([], status=200)
    rows = []
    for offset in range(0, len(ids), 1000):
        result = auth.post_asset_names(path, cid, ids[offset:offset + 1000])
        if not result.ok:
            return result
        rows.extend(result.data)
    return _success(result, rows)


def fetch_corporation_contract_assets(corp_id, char_id):
    corp_id = _positive(corp_id, "corporation_id")
    path = f"/v1/corporations/{corp_id}/contracts/"
    denied = _member(corp_id, char_id, path) or _scope(
        char_id, "corporation_contracts", path)
    if denied:
        return denied
    return denied or contracts.fetch_corporation_contract_assets(corp_id, char_id)


def discover_sources():
    discovered = {"divisions": {}, "assets_by_character": {}, "containers": {},
                  "characters": [], "errors": []}
    corp_operators = {}
    for char in sso.connected_chars():
        cid = char["id"]
        info = fetch_character_info(cid)
        capabilities = sso.character_capabilities(cid)
        discovered["characters"].append({**char, "capabilities": capabilities,
                                          "corporation_id": info.data.get("corporation_id")
                                          if info.ok else None})
        if not info.ok:
            discovered["errors"].append({"character_id": cid, "error": _error_dict(info)})
            continue
        corp_id = info.data["corporation_id"]
        if corp_id not in corp_operators or capabilities.get("corporation_divisions"):
            corp_operators[corp_id] = cid
        if not capabilities.get("character_assets"):
            continue
        assets = fetch_character_assets(cid)
        if not assets.ok:
            discovered["errors"].append({"character_id": cid, "error": _error_dict(assets)})
            continue
        bare_graph = asset_tree.build_asset_tree(assets.data)
        parent_ids = set(bare_graph.children)
        names = fetch_asset_names(cid, parent_ids)
        graph = asset_tree.build_asset_tree(assets.data, names.data if names.ok else None)
        discovered["assets_by_character"][str(cid)] = assets.data
        discovered["containers"][str(cid)] = graph.container_options(top_level_only=False)
        if not names.ok:
            discovered["errors"].append({"character_id": cid, "error": _error_dict(names)})
    for corp_id, cid in corp_operators.items():
        divisions = fetch_corporation_divisions(corp_id, cid)
        if divisions.ok:
            discovered["divisions"][str(corp_id)] = divisions.data
            if divisions.status == 403:
                discovered["errors"].append({"corporation_id": corp_id, "error": {
                    "kind": "degraded_divisions", "status": 403,
                    "message": "Noms de divisions indisponibles; IDs génériques utilisés."}})
        else:
            discovered["errors"].append({"corporation_id": corp_id,
                                          "error": _error_dict(divisions)})
    return discovered
