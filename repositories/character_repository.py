#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
character_repository.py - personnages + profils commerciaux par perso.

Standings Jita = standings BRUTS de CHARACTER_THREE envers Caldari State /
Caldari Navy (jamais modifies par Connections/Diplomacy).
"""
import time
import database as db
import mmd_price as prx  # to_cents/from_cents (Decimal <-> centiemes)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def upsert_character(character_id, name=None, corporation_id=None, active=1):
    cid = int(character_id)

    def _body(con):
        existing = con.execute(
            "SELECT first_seen_at FROM characters WHERE character_id=?",
            (cid,)).fetchone()
        if existing:
            con.execute(
                "UPDATE characters SET character_name=?, "
                "corporation_id=COALESCE(?, corporation_id), active=?, "
                "last_seen_at=? WHERE character_id=?",
                (name, corporation_id, active, _now(), cid))
        else:
            con.execute(
                "INSERT INTO characters(character_id, character_name, "
                "corporation_id, active, first_seen_at, last_seen_at) "
                "VALUES (?,?,?,?,?,?)",
                (cid, name, corporation_id, active, _now(), _now()))
    return db.atomic(_body)


def save_trade_profile(character_id, *, broker_relations=0, adv_broker=0,
                       accounting=0, faction_standing_raw=0.0,
                       corp_standing_raw=0.0, faction_id=None,
                       npc_corp_id=None, buy_loc=None, sell_loc=None):
    """Persiste le profil commercial (standings BRUTS uniquement)."""
    cid = int(character_id)
    now = _now()

    def _body(con):
        # garantit le parent (character) pour respecter la FK
        con.execute(
            "INSERT OR IGNORE INTO characters(character_id, character_name, "
            "corporation_id, active, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?)",
            (cid, None, None, 1, now, now))
        con.execute(
            "INSERT OR REPLACE INTO character_trade_profiles("
            "character_id, broker_relations_level, advanced_broker_relations_level, "
            "accounting_level, faction_standing_raw, corporation_standing_raw, "
            "faction_id, npc_corporation_id, preferred_buy_location_id, "
            "preferred_sell_location_id, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, broker_relations, adv_broker, accounting,
             faction_standing_raw, corp_standing_raw, faction_id, npc_corp_id,
             buy_loc, sell_loc, now))
    return db.atomic(_body)


def get_trade_profile(character_id):
    with db.connection() as con:
        r = con.execute(
            "SELECT * FROM character_trade_profiles WHERE character_id=?",
            (character_id,)).fetchone()
        return dict(r) if r else None


def list_characters(active_only=False):
    with db.connection() as con:
        sql = "SELECT * FROM characters"
        if active_only:
            sql += " WHERE active=1"
        return [dict(r) for r in con.execute(sql).fetchall()]


def get_character(character_id):
    with db.connection() as con:
        r = con.execute("SELECT * FROM characters WHERE character_id=?",
                        (character_id,)).fetchone()
        return dict(r) if r else None
