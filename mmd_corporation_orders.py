#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot read-only des ordres corporation, avec division wallet."""
import mmd_contracts as exact
import mmd_esi_auth as auth
import mmd_esi_portfolio as portfolio
import mmd_sso as sso


def fetch_corporation_orders(corporation_id, char_id):
    corp, cid = int(corporation_id), int(char_id)
    path = f"/v2/corporations/{corp}/orders/"
    info = portfolio.fetch_character_info(cid)
    if not info.ok:
        return info
    if int(info.data["corporation_id"]) != corp:
        return auth.failure(path, "corporation_mismatch",
                            "Le token n'appartient pas à cette corporation", 403)
    if not sso.character_capabilities(cid).get("corporation_orders", False):
        return auth.failure(path, "missing_scope",
                            "Scope ESI manquant: corporation_orders", 403)
    result = auth.fetch_all_pages(path, cid)
    if not result.ok:
        return result
    normalized = {}
    try:
        for raw in result.data:
            row = dict(raw)
            order_id, division = int(row["order_id"]), int(row["wallet_division"])
            if order_id <= 0 or not 1 <= division <= 7:
                raise ValueError
            row.update(order_id=order_id, division_id=division,
                       price_cents=exact.to_cents(row.pop("price")),
                       escrow_cents=(exact.to_cents(row.pop("escrow"))
                                     if row.get("escrow") is not None else None))
            normalized[order_id] = row
    except (KeyError, TypeError, ValueError):
        return auth.failure(path, "invalid_payload", "Ordres corporation ESI invalides")
    return auth.EsiResult(list(normalized.values()), result.headers,
                          status=result.status, from_cache=result.from_cache)
