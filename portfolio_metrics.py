#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Indicateurs de performance exacts calculés depuis les ventes FIFO."""
from datetime import datetime, timedelta


def trailing_realized_return_bp(rows, days=30):
    """Rendement réalisé glissant, rapporté au coût FIFO des ventes couvertes."""
    observations = []
    for row in rows or ():
        if row.get("side") != "SELL" or not row.get("cost_known"):
            continue
        try:
            day = datetime.strptime(str(row.get("date") or "")[:10], "%Y-%m-%d").date()
            cost = int(row.get("realized_cost_cents") or 0)
            profit = int(row.get("realized_profit_cents") or 0)
        except (TypeError, ValueError):
            continue
        if cost > 0:
            observations.append((day, cost, profit))
    if not observations:
        return 0
    cutoff = max(row[0] for row in observations) - timedelta(days=max(1, int(days)) - 1)
    recent = [row for row in observations if row[0] >= cutoff]
    cost = sum(row[1] for row in recent)
    return sum(row[2] for row in recent) * 10_000 // cost if cost else 0
