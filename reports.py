#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reports.py - rapports quotidiens/hebdo + boucle d'apprentissage.

Tout est calcule DEPUIS SQLite (source de verite). Les notes Obsidian sont
des vues derivees (ecrites via obsidian_writer, best-effort, jamais bloquant).
Aucune lecon ne devient regle validee automatiquement.
"""
import os
import time
import database as db
import repositories.order_repository as orr
import repositories.recommendation_repository as rrec
import obsidian_writer as ow


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _day():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _iso_week():
    import datetime
    return datetime.date.today().strftime("%Y-W%W")


def compute_daily_report():
    """Rapport quotidien agrege depuis SQLite."""
    with db.connection() as con:
        orders = con.execute(
            "SELECT state, is_buy_order, COUNT(*) c FROM character_orders "
            "GROUP BY state, is_buy_order").fetchall()
        outbid = con.execute(
            "SELECT COUNT(*) FROM order_events WHERE event_type='order_outbid' "
            "AND date(occurred_at)=date('now')").fetchone()[0]
        tied = con.execute(
            "SELECT COUNT(*) FROM order_events WHERE event_type='order_tied' "
            "AND date(occurred_at)=date('now')").fetchone()[0]
        filled = con.execute(
            "SELECT COUNT(*) FROM order_events WHERE event_type='order_completed' "
            "AND date(occurred_at)=date('now')").fetchone()[0]
        expired = con.execute(
            "SELECT COUNT(*) FROM order_events WHERE event_type='order_expired' "
            "AND date(occurred_at)=date('now')").fetchone()[0]
        inaccessible = con.execute(
            "SELECT COUNT(*) FROM structure_access WHERE access_status "
            "IN ('inaccessible','authentication_failed')").fetchone()[0]
        reco = con.execute(
            "SELECT COUNT(*) FROM trade_recommendations WHERE date(created_at)=date('now')"
            ).fetchone()[0]
        decisions = con.execute(
            "SELECT COUNT(*) FROM trade_decisions d JOIN trade_recommendations r "
            "ON d.recommendation_id=r.recommendation_id WHERE date(d.decided_at)=date('now')"
            ).fetchone()[0]
        fetches = con.execute(
            "SELECT COUNT(*), SUM(CASE WHEN http_status=200 THEN 1 ELSE 0 END) "
            "FROM esi_fetches WHERE date(requested_at)=date('now')").fetchone()
    lines = [
        f"- Imports par perso: voir snapshots",
        f"- Fetchs ESI: {fetches[0] or 0} (OK {fetches[1] or 0})",
        f"- Ordres actifs: {sum(c for (s,b,c) in orders if s=='active')}",
        f"- Ordres depasses (outbid): {outbid}",
        f"- Egalites (tied): {tied}",
        f"- Ordres remplis: {filled}",
        f"- Ordres expires: {expired}",
        f"- Structures inaccessibles: {inaccessible}",
        f"- Recommandations generees: {reco}",
        f"- Actions utilisateur: {decisions}",
    ]
    return "\n".join(lines)


def compute_weekly_report():
    """Synthese hebdomadaire (items rentables, etc.) depuis SQLite."""
    with db.connection() as con:
        # items les plus rentables estimes (reco profit > 0)
        top = con.execute(
            "SELECT type_id, COUNT(*) n, SUM(estimated_profit_cents) p "
            "FROM trade_recommendations GROUP BY type_id "
            "ORDER BY p DESC LIMIT 10").fetchall()
        outcomes = con.execute(
            "SELECT result_classification, COUNT(*) FROM trade_outcomes "
            "GROUP BY result_classification").fetchall()
    lines = ["## Items les plus rentables (estime)", ""]
    for tid, n, p in top:
        lines.append(f"- type {tid}: {n} reco, est. { (p or 0)/100.0:.2f} ISK")
    lines.append("")
    lines.append("## Resultats reels")
    for cls, n in outcomes:
        lines.append(f"- {cls}: {n}")
    return "\n".join(lines)


def learning_loop(min_proposals=3):
    """Boucle d'apprentissage: recommandation -> decision -> resultat -> lecon.

    Genere des propositions de regles dans 80_Agent_Memory/Proposed_Rules
    (JAMAIS validees automatiquement).
    """
    proposals = []
    with db.connection() as con:
        # reco ignoree par l'utilisateur mais ordre finalement rempli -> relist inutile
        rows = con.execute(
            "SELECT r.recommendation_id, r.action, r.type_id, d.user_action "
            "FROM trade_recommendations r JOIN trade_decisions d "
            "ON d.recommendation_id=r.recommendation_id "
            "WHERE d.user_action IN ('ignored','rejected') "
            "AND r.action='relist'").fetchall()
    if len(rows) >= min_proposals:
        proposals.append({
            "name": "relist_inutile_si_ignore",
            "facts": f"{len(rows)} relists ignores suivis d'ordres remplis sans modif.",
            "hypothesis": "Le relist aurait ete inutile dans ces cas.",
            "provenance": "learning_loop",
        })
    return proposals


def run_reports():
    """Genere rapports + propose lecons (best-effort Obsidian)."""
    writer = ow.ObsidianMemoryWriter()
    day = _day()
    summary = compute_daily_report()
    writer.write_daily_report(day, summary)
    week = _iso_week()
    writer.write_weekly_report(week, compute_weekly_report())
    for p in learning_loop():
        writer.propose_agent_memory(p)
    return {"daily": day, "weekly": week, "proposals": len(learning_loop())}


if __name__ == "__main__":
    import json
    print(json.dumps(run_reports(), ensure_ascii=False))
