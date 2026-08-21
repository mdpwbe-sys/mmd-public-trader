#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalisation exacte des transactions et acquisitions par contrat ESI."""
from decimal import Decimal

import mmd_esi_auth as auth
import mmd_price as price

_FINISHED = {"finished", "finished_contractor", "finished_issuer"}


def to_cents(value):
    return price.to_cents(Decimal(str(value)))


def _positive(value, label):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} invalide") from exc
    if number <= 0:
        raise ValueError(f"{label} invalide")
    return number


def _success(source, data, headers=None):
    return auth.EsiResult(data, headers or source.headers, status=source.status,
                          from_cache=source.from_cache)


def _transaction_row(row):
    data = dict(row)
    data["transaction_id"] = _positive(data["transaction_id"], "transaction_id")
    data["unit_price_cents"] = to_cents(data.pop("unit_price"))
    return data


def fetch_transactions(path, char_id, from_id=None, known_ids=None, max_pages=1000):
    """Pagination atomique par ``from_id`` strictement decroissant."""
    try:
        cursor = _positive(from_id, "from_id") if from_id is not None else None
        known = {_positive(value, "transaction_id") for value in (known_ids or ())}
    except ValueError:
        return auth.failure(path, "invalid_cursor", "Curseur local invalide")
    rows, seen, first_headers = [], set(), None
    for page in range(1, max(1, int(max_pages)) + 1):
        result = auth.get_json(path, char_id,
                               params={"from_id": cursor} if cursor else None)
        if not result.ok:
            return result
        if first_headers is None:
            first_headers = result.headers
        if not isinstance(result.data, list):
            return auth.failure(path, "invalid_payload", "Transactions ESI invalides")
        if not result.data:
            headers = dict(first_headers or {})
            headers["X-Collected-Pages"] = str(page)
            return _success(result, rows, headers)
        page_ids, reached_known = [], False
        try:
            for raw in result.data:
                row = _transaction_row(raw)
                transaction_id = row["transaction_id"]
                page_ids.append(transaction_id)
                if transaction_id in known:
                    reached_known = True
                    break
                if transaction_id not in seen:
                    seen.add(transaction_id)
                    rows.append(row)
        except (KeyError, TypeError, ValueError):
            return auth.failure(path, "invalid_payload", "Transaction ESI invalide")
        if reached_known:
            return _success(result, rows)
        next_cursor = min(page_ids)
        # ESI renvoie parfois le meme transaction_id a la frontiere de pagination
        # (from_id traite de facon inclusive/exclusive non garantie). Si le curseur
        # ne decroit PAS strictement mais qu'on a deja collecte de nouvelles rows,
        # c'est une FIN NORMALE (tout vu) -> on termine, on ne leve PAS d'erreur.
        if cursor is not None and next_cursor >= cursor:
            if rows:
                return _success(result, rows)
            # aucune nouvelle row et curseur bloque -> vraie boucle infinie
            return auth.failure(path, "invalid_cursor", "Curseur ESI non decroissant")
        cursor = next_cursor
    return auth.failure(path, "page_limit", "Limite de pagination atteinte")


def fetch_corporation_contract_assets(corp_id, char_id):
    """Contrats termines; alloue le cout seulement aux contrats mono-ligne."""
    path = f"/v1/corporations/{corp_id}/contracts/"
    contracts = auth.fetch_all_pages(path, char_id)
    if not contracts.ok:
        return contracts
    acquired = []
    try:
        relevant = [dict(row) for row in contracts.data
                    if row.get("type") == "item_exchange"
                    and row.get("status") in _FINISHED]
        for contract in relevant:
            contract_id = _positive(contract["contract_id"], "contract_id")
            corp_acceptor = int(contract.get("acceptor_id") or 0) == int(corp_id)
            if not corp_acceptor:
                continue
            item_path = f"/v1/corporations/{corp_id}/contracts/{contract_id}/items/"
            items = auth.get_json(item_path, char_id)
            if not items.ok:
                return items
            received = [dict(row) for row in items.data
                        if bool(row.get("is_included"))]
            total_cents = to_cents(contract.get("price", 0))
            pure_purchase = not any(
                not bool(row.get("is_included")) for row in items.data)
            for row in received:
                row["record_id"] = _positive(row["record_id"], "record_id")
                row["type_id"] = _positive(row["type_id"], "type_id")
                row["quantity"] = _positive(row["quantity"], "quantity")
                row.update(
                    contract_id=contract_id,
                    issuer_id=contract.get("issuer_id"),
                    acceptor_id=contract.get("acceptor_id"),
                    contract_type=contract.get("type"),
                    contract_status=contract.get("status"),
                    issued_at=contract.get("date_issued"),
                    completed_at=contract.get("date_completed"),
                    contract_total_cents=total_cents,
                    acquisition_cost_cents=(total_cents if pure_purchase
                                            and len(received) == 1 else None),
                    allocation_required=pure_purchase and len(received) > 1,
                    acquisition_side="corporation_acceptor",
                    is_blueprint_copy=int(row.get("raw_quantity", 0)) == -2)
                acquired.append(row)
    except (KeyError, TypeError, ValueError):
        return auth.failure(path, "invalid_payload", "Contrats ESI invalides")
    return _success(contracts, acquired)
