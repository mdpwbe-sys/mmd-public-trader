#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Taux de transaction par station et profil, sans modifier mmd_margin."""
from decimal import Decimal, ROUND_HALF_UP

import mmd_margin as margin

RATE_SCALE = Decimal("1000000")


def _ppm(rate):
    return int((Decimal(rate) * RATE_SCALE).to_integral_value(rounding=ROUND_HALF_UP))


def rates_for_station(station_id, cfg=None):
    """Retourne courtage/taxe en millioniemes; zéro explicite si lieu inconnu."""
    try:
        sid = int(station_id or 0)
    except (TypeError, ValueError):
        sid = 0
    if not sid:
        return {"broker_rate_ppm": 0, "sales_tax_rate_ppm": 0,
                "fee_source": "location-unavailable"}
    cfg = cfg or margin.load_config()
    kind, faction, corporation = margin.station_kind(sid)
    if kind == "upwell":
        broker = margin.upwell_broker_rate(cfg.get("upwell_owner_fee", 0))
        source = "station-upwell-profile"
    elif sid in margin.NPC_STATIONS:
        broker = margin.rate_for_station(sid, cfg)
        source = "station-npc-standings"
    else:
        # Owner inconnu: profil BR applique, standings neutres (conservateur).
        broker = margin.npc_broker_rate(cfg.get("broker_relations", 0),
                                        Decimal("0"), Decimal("0"))
        source = "station-npc-owner-unresolved"
    tax = margin.sales_tax_rate(cfg.get("accounting", 0))
    return {"broker_rate_ppm": _ppm(broker),
            "sales_tax_rate_ppm": _ppm(tax),
            "station_kind": kind, "fee_source": source,
            "station_owner": "/".join(v for v in (faction, corporation) if v)}


def enrich(rows, cfg=None):
    """Copie les lignes et ajoute leurs taux calculés depuis location_id."""
    cfg = cfg or margin.load_config()
    out = []
    for row in rows or ():
        enriched = dict(row)
        enriched.update(rates_for_station(row.get("location_id"), cfg))
        out.append(enriched)
    return out
