#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dernier snapshot complet des ordres corporation, isolé par division."""
import time

import database as db


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def replace_orders(corporation_id, rows, captured_at=None):
    corp, captured, params = int(corporation_id), captured_at or _now(), []
    if corp <= 0:
        raise ValueError("corporation_id invalide")
    for row in rows or ():
        division = int(row["division_id"])
        if not 1 <= division <= 7:
            raise ValueError("division_id invalide")
        params.append((corp, int(row["order_id"]), division, row.get("issued_by"),
                       int(row["type_id"]), int(row["location_id"]), row.get("region_id"),
                       int(bool(row.get("is_buy_order"))), int(row["price_cents"]),
                       row.get("escrow_cents"), int(row.get("volume_total") or 0),
                       int(row.get("volume_remain") or 0), int(row.get("min_volume") or 1),
                       row.get("range"), row.get("issued") or row.get("issued_at"),
                       row.get("duration"), captured))

    def _body(con):
        con.execute("DELETE FROM corporation_orders WHERE corporation_id=?", (corp,))
        con.executemany("INSERT INTO corporation_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        params)
        return len(params)
    return db.atomic(_body)


def load_orders(corporation_id, division_id):
    corp, division = int(corporation_id), int(division_id)
    if corp <= 0 or not 1 <= division <= 7:
        raise ValueError("scope ordre corporation invalide")
    with db.connection() as con:
        return [dict(row) for row in con.execute(
            "SELECT * FROM corporation_orders WHERE corporation_id=? AND division_id=? "
            "ORDER BY type_id,is_buy_order,order_id", (corp, division)).fetchall()]
