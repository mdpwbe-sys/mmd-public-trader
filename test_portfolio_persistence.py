#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Intégration SQLite du FIFO et des sélections portefeuille hors ligne."""
import os
import tempfile
import unittest

import database
import migrations
import portfolio_service as service
import repositories.portfolio_repository as repo


class PortfolioPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = database.DB_PATH
        self.old_discovery = dict(service._DISCOVERY)
        database.DB_PATH = os.path.join(self.temp.name, "portfolio.db")
        migrations.migrate()

    def tearDown(self):
        service._DISCOVERY.clear()
        service._DISCOVERY.update(self.old_discovery)
        database.DB_PATH = self.old_db
        self.temp.cleanup()

    def test_workspace_persists_complete_and_unknown_fifo_results(self):
        common = {"owner_kind": "corporation", "owner_id": 202, "division_id": 4,
                  "location_id": 60_003_760, "is_personal": False, "quantity": 1}
        repo.upsert_transactions([
            {**common, "transaction_id": 1, "date": "2026-01-01", "type_id": 7,
             "is_buy": True, "unit_price_cents": 10_000},
            {**common, "transaction_id": 2, "date": "2026-01-02", "type_id": 7,
             "is_buy": False, "unit_price_cents": 20_000},
            {**common, "transaction_id": 3, "date": "2026-01-03", "type_id": 8,
             "is_buy": False, "unit_price_cents": 30_000},
        ])
        workspace = service._workspace({"key": "corp:202:division:4",
                                        "corporation_id": "202", "division_id": "4",
                                        "character_id": "101", "name": "Libre"}, None)
        rendered = {int(row["transaction_id"]): row for row in workspace["transactions"]}
        stored = {row["transaction_id"]: row for row in
                  repo.load_transactions("corporation", 202, 4)}
        self.assertEqual((0, None, None, "complete"),
                         tuple(stored[1][key] for key in ("matched_quantity",
                               "realized_cost_cents", "realized_profit_cents", "fifo_status")))
        self.assertEqual((1, int(rendered[2]["fifo_cost_cents"]),
                          int(rendered[2]["realized_pnl_cents"]), "complete"),
                         tuple(stored[2][key] for key in ("matched_quantity",
                               "realized_cost_cents", "realized_profit_cents", "fifo_status")))
        self.assertEqual((0, 0, None, "unknown_basis", "portfolio_fifo_v1"),
                         tuple(stored[3][key] for key in ("matched_quantity",
                               "realized_cost_cents", "realized_profit_cents",
                               "fifo_status", "fifo_version")))
        expected_return = int(rendered[2]["realized_pnl_cents"]) * 10_000 // int(rendered[2]["fifo_cost_cents"])
        self.assertEqual(expected_return, workspace["summary"]["monthly_return_bp"])

    def test_cached_quick_container_is_validated_and_applied(self):
        repo.save_setting("wallet_source", {"key": "corp:202:division:4",
                          "corporation_id": "202", "division_id": "4",
                          "character_id": "101", "name": "Libre"})
        repo.save_setting("asset_source", {"key": "char:101:container:303",
                          "character_id": "101", "item_id": "303", "name": "Alpha"})
        service._DISCOVERY["data"] = {
            "characters": [{"id": 101, "name": "Pilot"}],
            "containers": {"101": [{"item_id": 303, "name": "Alpha"},
                                      {"item_id": 404, "name": "Beta"}]}}
        _settings, selected, error = service._selection(
            {"container_key": "char:101:container:404"}, live=False)
        self.assertIsNone(error)
        self.assertEqual("404", selected[1]["item_id"])
        _settings, selected, error = service._selection(
            {"container_key": "char:101:container:999"}, live=False)
        self.assertIsNone(selected)
        self.assertFalse(error["ok"])

    def test_corrupt_cached_asset_source_is_not_an_empty_success(self):
        repo.save_setting("wallet_source", {"key": "corp:202:division:4",
                          "corporation_id": "202", "division_id": "4"})
        repo.save_setting("asset_source", {"character_id": "broken", "item_id": "303"})
        _settings, selected, error = service._selection(live=False)
        self.assertIsNone(selected)
        self.assertIn("invalide", error["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
