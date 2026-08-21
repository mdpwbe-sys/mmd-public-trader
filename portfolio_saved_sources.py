#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reconstruction offline des sources portefeuille validées puis persistées."""
import repositories.portfolio_repository as repo


def load(container_key=None, containers=()):
    saved = repo.get_settings()
    wallet = saved.get("wallet_source")
    if not isinstance(wallet, dict):
        return None, None, {"ok": False,
                            "error": "Choisissez une division corporation dans Settings."}
    try:
        corp, division = int(wallet["corporation_id"]), int(wallet["division_id"])
        if corp <= 0 or not 1 <= division <= 7:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return None, None, {"ok": False, "error": "Source wallet locale invalide."}
    wallet = dict(wallet, corporation_id=str(corp), division_id=str(division),
                  name=wallet.get("name") or f"Division {division}")
    asset = saved.get("asset_source")
    if asset is not None:
        try:
            asset = dict(asset, character_id=str(int(asset["character_id"])),
                         item_id=str(int(asset["item_id"])))
        except (KeyError, TypeError, ValueError):
            return wallet, None, {"ok": False, "error": "Source assets locale invalide."}
    requested = str(container_key or "")
    if requested and requested != str((asset or {}).get("key") or ""):
        asset = next((dict(row) for row in containers
                      if str(row.get("key")) == requested), None)
        if not asset:
            return wallet, None, {"ok": False, "error": "Conteneur assets invalide ou disparu."}
    return wallet, asset, None
