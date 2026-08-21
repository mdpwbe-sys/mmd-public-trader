#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
order_repository.py - ordres perso + evenements historises et bornes.

Regles conservees:
- identite = order_id (jamais prix/type seul)
- dedup stricte par order_id
- "Tous" = dernier snapshot disponible de CHAQUE perso
- import d'un perso ne touche PAS les autres
- snapshot partiellement invalide -> ROLLBACK, ancien snapshot intact
"""
import time
import database as db
import mmd_price as prx


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _cents(v):
    return prx.to_cents(v) if v is not None else None


def upsert_order(order, source_import_id=None, source_fetch_id=None, last_seen_at=None):
    """Insere ou maj un ordre (dedup par order_id).

    UPSERT via ON CONFLICT(order_id) DO UPDATE : preserve les metadonnees
    historiques (first_seen_at, issued_at, duration...) et ne touche QUE
    les champs volatils (prix, volume, etat, last_seen_at).
    price_cents = INTEGER centiemes d'ISK (jamais REAL/float).
    Ecriture atomique + retry sur 'database is locked' (0 perte sous concurrence).
    """
    oid = str(order.get("order_id"))
    cid = int(order.get("character_id") or order.get("char_id") or 0)
    lsa = last_seen_at or _now()

    def _body(con):
        # garantit le parent (character) pour respecter la FK
        con.execute(
            "INSERT OR IGNORE INTO characters(character_id, character_name, "
            "corporation_id, active, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?)",
            (cid, order.get("char_name"), None, 1, _now(), _now()))
        con.execute(
            "INSERT INTO character_orders("
            "order_id, character_id, type_id, location_id, region_id, "
            "is_buy_order, range, price_cents, volume_total, volume_remain, "
            "min_volume, issued_at, duration, state, first_seen_at, "
            "last_seen_at, source_import_id, source_fetch_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(order_id) DO UPDATE SET "
            "character_id=excluded.character_id, "
            "type_id=excluded.type_id, "
            "location_id=excluded.location_id, "
            "region_id=excluded.region_id, "
            "is_buy_order=excluded.is_buy_order, "
            "range=excluded.range, "
            "price_cents=excluded.price_cents, "
            "volume_total=excluded.volume_total, "
            "volume_remain=excluded.volume_remain, "
            "min_volume=excluded.min_volume, "
            "duration=excluded.duration, "
            "state=excluded.state, "
            "last_seen_at=excluded.last_seen_at, "
            "source_import_id=excluded.source_import_id, "
            "source_fetch_id=excluded.source_fetch_id "
            "WHERE character_orders.order_id=excluded.order_id",
            (oid, cid, int(order["type_id"]),
             int(order["station_id"]), order.get("region_id"),
             1 if order.get("side") == 0 else 0,
             str(order.get("range", "region")),
             _cents(order["price"]), int(order.get("vol_total", order.get("volume_remain", order.get("vol_remaining", 0)))),
             int(order.get("volume_remain", order.get("vol_remaining", 0))), int(order.get("min_volume", 1)),
             order.get("issued"), int(order.get("duration", 0)) if order.get("duration") else None,
             order.get("state", "active"),
             _now(), lsa, source_import_id, source_fetch_id))

    return db.atomic(_body)


def record_order_event(order_id, character_id, event_type, *,
                       previous_price=None, new_price=None,
                       previous_volume_remain=None, new_volume_remain=None,
                       snapshot_id=None, reason=None, metadata_json=None):
    """Ajoute un evenement, en dedupliquant les scans sans changement."""
    oid = str(order_id)
    new_price_cents = _cents(new_price)

    def _body(con):
        if reason == "scan":
            previous = con.execute(
                "SELECT event_type, new_price_cents, new_volume_remain "
                "FROM order_events WHERE order_id=? AND reason='scan' "
                "ORDER BY event_id DESC LIMIT 1", (oid,)).fetchone()
            signature = (event_type, new_price_cents, new_volume_remain)
            if previous and tuple(previous) == signature:
                return False
        con.execute(
            "INSERT INTO order_events(order_id, character_id, event_type, "
            "occurred_at, previous_price_cents, new_price_cents, "
            "previous_volume_remain, new_volume_remain, snapshot_id, "
            "reason, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (oid, character_id and int(character_id), event_type,
             _now(), _cents(previous_price), new_price_cents,
             previous_volume_remain, new_volume_remain, snapshot_id,
             reason, metadata_json))
        return True
    return db.atomic(_body)


def prune_order_events(retention_days=90, max_events=50_000):
    """Supprime les evenements trop anciens et borne la table globalement."""
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() - max(1, int(retention_days)) * 86400))
    limit = max(1, int(max_events))

    def _body(con):
        removed = con.execute(
            "DELETE FROM order_events WHERE occurred_at < ?", (cutoff,)).rowcount
        threshold = con.execute(
            "SELECT event_id FROM order_events ORDER BY event_id DESC "
            "LIMIT 1 OFFSET ?", (limit - 1,)).fetchone()
        if threshold:
            removed += con.execute(
                "DELETE FROM order_events WHERE event_id < ?",
                (threshold["event_id"],)).rowcount
        return removed
    return db.atomic(_body)


def get_latest_orders_by_character(character_id, state="active"):
    with db.connection() as con:
        rows = con.execute(
            "SELECT * FROM character_orders WHERE character_id=? AND state=? "
            "ORDER BY type_id, is_buy_order",
            (int(character_id), state)).fetchall()
        return [dict(r) for r in rows]


def get_all_latest_character_orders(state="active"):
    """Vue 'Tous' : dernier snapshot de CHAQUE perso (1 requete group-by)."""
    with db.connection() as con:
        rows = con.execute(
            "SELECT o.* FROM character_orders o "
            "JOIN (SELECT character_id, MAX(last_seen_at) mx FROM character_orders "
            "WHERE state=? GROUP BY character_id) m "
            "ON o.character_id=m.character_id AND o.last_seen_at=m.mx "
            "WHERE o.state=? ORDER BY o.character_id, o.type_id, o.is_buy_order",
            (state, state)).fetchall()
        return [dict(r) for r in rows]


def get_order_events(order_id, limit=50):
    with db.connection() as con:
        rows = con.execute(
            "SELECT * FROM order_events WHERE order_id=? ORDER BY occurred_at DESC LIMIT ?",
            (str(order_id), limit)).fetchall()
        return [dict(r) for r in rows]


def mark_stale_snapshot(snapshot_id):
    sid = snapshot_id

    def _body(con):
        con.execute("UPDATE market_snapshots SET stale=1 WHERE snapshot_id=?",
                    (sid,))
    return db.atomic(_body)


def dedupe_check(order_ids):
    """Retourne le set des order_id deja connus (pour eviter doublons)."""
    with db.connection() as con:
        known = set()
        for oid in order_ids:
            r = con.execute("SELECT 1 FROM character_orders WHERE order_id=?",
                            (str(oid),)).fetchone()
            if r:
                known.add(str(oid))
        return known
