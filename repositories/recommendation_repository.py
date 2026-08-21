#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recommendation_repository.py - recommandations, decisions, resultats reels.

Boucle d'apprentissage:
  recommandation -> decision utilisateur -> evolution ordre -> resultat reel -> lecon.
Aucune lecon ne devient regle validee automatiquement (cf. Obsidian writer).
"""
import time
import database as db
import mmd_price as prx


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def save_recommendation(rec_id, *, character_id, type_id, action,
                        current_price, recommended_price, buy_loc=None,
                        sell_loc=None, order_id=None, snapshot_id=None,
                        est_broker_fee=None, est_relist_fee=None,
                        est_sales_tax=None, est_profit=None, confidence="medium",
                        reason=None, algorithm_version="1.0"):
    now = _now()
    cid = character_id and int(character_id)
    tid = int(type_id)
    rec = (rec_id, now, cid, order_id, tid, buy_loc, sell_loc, action,
           prx.to_cents(current_price), prx.to_cents(recommended_price),
           prx.to_cents(est_broker_fee or 0), prx.to_cents(est_relist_fee or 0),
           prx.to_cents(est_sales_tax or 0), prx.to_cents(est_profit or 0),
           confidence, reason, snapshot_id, algorithm_version)

    def _body(con):
        con.execute(
            "INSERT OR REPLACE INTO trade_recommendations("
            "recommendation_id, created_at, character_id, order_id, type_id, "
            "buy_location_id, sell_location_id, action, current_price_cents, "
            "recommended_price_cents, estimated_broker_fee_cents, "
            "estimated_relist_fee_cents, estimated_sales_tax_cents, "
            "estimated_profit_cents, confidence, reason, snapshot_id, "
            "algorithm_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rec)
    return db.atomic(_body)


def save_decision(decision_id, rec_id, *, user_action, actual_price=None,
                  notes=None):
    ap = prx.to_cents(actual_price) if actual_price is not None else None
    dec = (decision_id, rec_id, _now(), user_action, ap, notes)

    def _body(con):
        con.execute(
            "INSERT OR REPLACE INTO trade_decisions("
            "decision_id, recommendation_id, decided_at, user_action, "
            "actual_price_cents, notes) VALUES (?,?,?,?,?,?)", dec)
    return db.atomic(_body)


def save_outcome(outcome_id, decision_id, *, quantity_filled=0,
                 realized_revenue=None, realized_cost=None, realized_fees=None,
                 realized_profit=None, result_classification=None, lesson=None):
    out = (outcome_id, decision_id, _now(), quantity_filled,
           prx.to_cents(realized_revenue or 0),
           prx.to_cents(realized_cost or 0),
           prx.to_cents(realized_fees or 0),
           prx.to_cents(realized_profit or 0),
           result_classification, lesson)

    def _body(con):
        con.execute(
            "INSERT OR REPLACE INTO trade_outcomes("
            "outcome_id, decision_id, evaluated_at, quantity_filled, "
            "realized_revenue_cents, realized_cost_cents, realized_fees_cents, "
            "realized_profit_cents, result_classification, lesson) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", out)
    return db.atomic(_body)


def list_recommendations(character_id=None, limit=50):
    with db.connection() as con:
        if character_id:
            rows = con.execute(
                "SELECT * FROM trade_recommendations WHERE character_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (int(character_id), limit)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM trade_recommendations ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]
