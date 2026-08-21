#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Régressions du moteur Asset/Transaction (réseau toujours mocké/absent)."""
import os
import tempfile
import unittest
import database
import migrations
import portfolio_engine as engine
import portfolio_fees as fees
import portfolio_service as service
import repositories.character_repository as character_repo
import repositories.corporation_order_repository as corp_order_repo
import repositories.portfolio_repository as portfolio_repo
CFG = {
    "broker_relations": 5,
    "accounting": 5,
    "standings": {"Caldari State": 5, "Caldari Navy": 8},
    "upwell_owner_fee": 0,
}

class AssetTransactionTests(unittest.TestCase):
    def test_hakims_regression_perimeter_280m_vs_jita_1_5m(self):
        perimeter = fees.rates_for_station(1_000_000_000_001, CFG)
        jita = fees.rates_for_station(60_003_760, CFG)
        self.assertEqual(5_000, perimeter["broker_rate_ppm"])
        self.assertEqual(11_900, jita["broker_rate_ppm"])
        self.assertEqual(33_750, jita["sales_tax_rate_ppm"])
        rows = fees.enrich([
            {"transaction_id": "1", "date": "2026-01-01", "type_id": 1,
             "is_buy": True, "quantity": 1, "unit_price_cents": 28_000_000_000,
             "location_id": 1_000_000_000_001},
            {"transaction_id": "2", "date": "2026-01-02", "type_id": 2,
             "is_buy": True, "quantity": 1, "unit_price_cents": 150_000_000,
             "location_id": 60_003_760},
        ], CFG)
        result = engine.build_workspace(transactions=rows)
        by_id = {row["transaction_id"]: row for row in result["transactions"]}
        self.assertEqual("140000000", by_id["1"]["fees_cents"])
        self.assertEqual("1785000", by_id["2"]["fees_cents"])
    def test_fifo_realized_profit_and_consumed_quantity(self):
        tx = fees.enrich([
            {"transaction_id": "buy", "date": "2026-01-01", "type_id": 42,
             "name": "Test Item", "is_buy": True, "quantity": 2,
             "unit_price_cents": 10_000_000_000, "location_id": 1_000_000_000_001},
            {"transaction_id": "sell", "date": "2026-01-02", "type_id": 42,
             "name": "Test Item", "is_buy": False, "quantity": 1,
             "unit_price_cents": 13_000_000_000, "location_id": 60_003_760},
        ], CFG)
        result = engine.build_workspace(transactions=tx)
        sale = next(row for row in result["transactions"] if row["side"] == "SELL")
        buy_unit_with_fee = 10_000_000_000 + 50_000_000
        sell_fees = (13_000_000_000 * (11_900 + 33_750) + 500_000) // 1_000_000
        self.assertEqual(str(buy_unit_with_fee), sale["fifo_cost_cents"])
        self.assertEqual(str(13_000_000_000 - sell_fees - buy_unit_with_fee),
                         sale["realized_pnl_cents"])
        self.assertEqual(1, sale["consumed_quantity"])
        self.assertTrue(sale["cost_known"])
        self.assertEqual("estimated", sale["fee_status"])
    def test_actual_journal_fee_takes_priority_over_station_estimate(self):
        tx = [{"transaction_id": "buy", "date": "2026-01-01", "type_id": 8,
               "is_buy": True, "quantity": 1, "unit_price_cents": 1_000_000,
               "location_id": 60_003_760, "broker_rate_ppm": 99_999,
               "actual_fee_cents": 12_345}]
        result = engine.build_workspace(transactions=tx)
        row = result["transactions"][0]
        self.assertEqual("12345", row["fees_cents"])
        self.assertEqual("actual", row["fee_status"])
        self.assertEqual("journal", row["fee_source"])
    def test_contract_bpc_uses_exact_cost_when_market_is_empty(self):
        contract = [{"contract_id": "9001", "type_id": 24689,
                     "name": "Victory Luxury Yacht Blueprint", "quantity": 1,
                     "allocated_cost_cents": 25_000_000_000,
                     "acquired_at": "2026-01-01"}]
        assets = [{"item_id": "7", "type_id": 24689, "quantity": 1,
                   "name": "Victory Luxury Yacht Blueprint"}]
        result = engine.build_workspace(assets=assets, contracts=contract, history={})
        row = result["assets"][0]
        self.assertEqual("25000000000", row["avg_cost_cents"])
        self.assertEqual("25000000000", row["inventory_value_cents"])
        self.assertEqual("25000000000", result["summary"]["inventory_value_cents"])
        self.assertIn("REVALUE", {alert["action"] for alert in result["alerts"]})

    def test_alerts_and_empty_history_are_defensive(self):
        tx = [{"transaction_id": "buy", "date": "2026-01-01", "type_id": 7,
               "is_buy": True, "quantity": 2, "unit_price_cents": 10_000,
               "location_id": 0, "broker_rate_ppm": 0, "sales_tax_rate_ppm": 0}]
        assets = [{"item_id": "1", "type_id": 7, "quantity": 2, "name": "Rare"}]
        orders = [{"type_id": 7, "is_buy_order": 0, "volume_remain": 2,
                   "price_cents": 9_000, "name": "Rare"}]
        result = engine.build_workspace(transactions=tx, assets=assets,
                                        orders=orders, history={})
        actions = {alert["action"] for alert in result["alerts"]}
        self.assertIn("HOLD", actions)
        self.assertIn("RISK", actions)
        self.assertEqual("HOLD", result["assets"][0]["action"])

    def test_hold_uses_sell_price_net_of_station_fees(self):
        tx = [{"transaction_id": "buy", "date": "2026-01-01", "type_id": 70,
               "is_buy": True, "quantity": 1, "unit_price_cents": 1_000_000,
               "broker_rate_ppm": 10_000, "location_id": 60_003_760}]
        orders = [{"type_id": 70, "is_buy_order": 0, "volume_remain": 1,
                   "price_cents": 1_030_000, "broker_rate_ppm": 11_900,
                   "sales_tax_rate_ppm": 33_750}]
        result = engine.build_workspace(transactions=tx, orders=orders)
        self.assertEqual("HOLD", result["assets"][0]["action"])
        self.assertIn("HOLD", {row["action"] for row in result["alerts"]})
        self.assertLess(int(result["assets"][0]["projected_profit_cents"]), 0)

    def test_history_median_and_percentile(self):
        history = {9: [{"avg_cents": value, "volume": 100}
                       for value in (100, 200, 300, 400)]}
        result = engine.build_workspace(
            assets=[{"type_id": 9, "quantity": 1, "name": "Liquid"}],
            history=history)
        row = result["assets"][0]
        self.assertEqual("250", row["market_median_cents"])
        self.assertEqual("300", row["market_p75_cents"])
class GenericSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = database.DB_PATH
        self.old_discovery = dict(service._DISCOVERY)
        self.old_discover = service._discover
        service._discover = lambda force=False: service._DISCOVERY["data"]
        database.DB_PATH = os.path.join(self.temp.name, "portfolio.db")
        migrations.migrate()
        character_repo.upsert_character(101, "Pilot", 202, 1)
        service._DISCOVERY.update(at=10 ** 20, data={
            "characters": [{"id": 101, "name": "Pilot", "corporation_id": 202,
                            "scopes_ok": True,
                            "capabilities": {"corporation_wallet": True}}],
            "divisions": {"202": [{"division_id": 4, "name": "Réserve libre",
                                      "label": "Réserve libre"}]},
            "containers": {"101": [{"item_id": 303, "name": "Caisse alpha",
                                       "label": "Caisse alpha", "depth": 0,
                                       "descendant_count": 1}]},
            "assets_by_character": {}, "errors": []})

    def tearDown(self):
        service._DISCOVERY.clear()
        service._DISCOVERY.update(self.old_discovery)
        service._discover = self.old_discover
        database.DB_PATH = self.old_db
        self.temp.cleanup()

    def test_division_is_selected_by_dynamic_corp_and_id(self):
        settings = service.get_settings()
        division, container = settings["divisions"][0], settings["containers"][0]
        self.assertEqual("4", division["division_id"])
        self.assertEqual("Réserve libre", division["name"])
        saved = service.save_settings({"wallet_source": {"key": division["key"]},
                                       "asset_source": {"key": container["key"]}})
        self.assertTrue(saved["ok"])
        stored = portfolio_repo.get_settings()["wallet_source"]
        self.assertEqual({"key": "corp:202:division:4", "corporation_id": "202",
                          "division_id": "4"}, {key: stored[key] for key in
                                                ("key", "corporation_id", "division_id")})

    def test_stale_or_forged_division_never_falls_back_to_one(self):
        result = service.save_settings({"wallet_source": {
            "key": "corp:202:division:1", "corporation_id": "202", "division_id": "1"}})
        self.assertFalse(result["ok"])
        self.assertNotIn("wallet_source", portfolio_repo.get_settings())

    def test_same_transaction_id_is_isolated_between_divisions(self):
        base = {"owner_kind": "corporation", "owner_id": 202,
                "transaction_id": 900, "date": "2026-01-01", "type_id": 7,
                "location_id": 60_003_760, "is_buy": True, "quantity": 1}
        portfolio_repo.upsert_transactions([
            {**base, "division_id": 2, "unit_price_cents": 100},
            {**base, "division_id": 4, "unit_price_cents": 200},
        ])
        self.assertEqual(100, portfolio_repo.load_transactions(
            "corporation", 202, 2)[0]["unit_price_cents"])
        self.assertEqual(200, portfolio_repo.load_transactions(
            "corporation", 202, 4)[0]["unit_price_cents"])

    def test_refresh_preserves_actual_fees_and_allocated_contract_cost(self):
        tx = {"owner_kind": "corporation", "owner_id": 202, "division_id": 4,
              "transaction_id": 901, "date": "2026-01-01", "type_id": 7,
              "location_id": 60_003_760, "is_buy": False, "quantity": 1,
              "unit_price_cents": 10_000, "broker_fee_cents": 120,
              "sales_tax_cents": 330, "fees_status": "actual"}
        portfolio_repo.upsert_transactions([tx])
        portfolio_repo.upsert_transactions([{**tx, "broker_fee_cents": 0,
                                              "sales_tax_cents": 0,
                                              "fees_status": "missing"}])
        stored = portfolio_repo.load_transactions("corporation", 202, 4)[0]
        self.assertEqual((120, 330, "actual"),
                         (stored["broker_fee_cents"], stored["sales_tax_cents"],
                          stored["fees_status"]))
        contract = {"owner_kind": "corporation", "owner_id": 202, "division_id": 0,
                    "contract_id": 8, "record_id": 9, "type_id": 7, "quantity": 1,
                    "allocated_cost_cents": 25_000_000_000,
                    "allocation_method": "single_item"}
        portfolio_repo.upsert_contract_assets([contract])
        portfolio_repo.upsert_contract_assets([{**contract,
                                                 "allocated_cost_cents": None,
                                                 "allocation_method": "unallocated"}])
        refreshed = portfolio_repo.load_contract_assets("corporation", 202)[0]
        self.assertEqual((25_000_000_000, "single_item"),
                         (refreshed["allocated_cost_cents"], refreshed["allocation_method"]))

    def test_cached_workspace_resolves_selected_container_descendants(self):
        settings = service.get_settings()
        service.save_settings({"wallet_source": {"key": settings["divisions"][0]["key"]},
                               "asset_source": {"key": settings["containers"][0]["key"]}})
        portfolio_repo.save_asset_snapshot(101, None, [
            {"item_id": 303, "type_id": 100, "quantity": 1,
             "location_id": 60_003_760, "location_type": "station",
             "root_container_id": 303, "item_name": "Box"},
            {"item_id": 304, "type_id": 200, "quantity": 2,
             "location_id": 303, "location_type": "item", "parent_item_id": 303,
             "root_container_id": 303, "hierarchy_depth": 1, "item_name": "Goods"},
        ])
        workspace = service.get_workspace()
        self.assertTrue(workspace["ok"])
        self.assertEqual("4", workspace["wallet_source"]["division_id"])
        self.assertEqual(["200"], [row["type_id"] for row in workspace["assets"]])

    def test_workspace_uses_only_corp_orders_from_selected_division(self):
        settings = service.get_settings()
        service.save_settings({"wallet_source": {"key": settings["divisions"][0]["key"]}})
        base = {"location_id": 60_003_760, "is_buy_order": True,
                "price_cents": 100, "volume_total": 5, "volume_remain": 5}
        corp_order_repo.replace_orders(202, [
            {**base, "order_id": 1, "division_id": 2, "type_id": 701,
             "escrow_cents": 111},
            {**base, "order_id": 2, "division_id": 4, "type_id": 702,
             "escrow_cents": 333},
        ])
        with database.connection() as con:
            con.execute("INSERT INTO character_orders(order_id,character_id,type_id,"
                        "location_id,is_buy_order,price_cents,volume_total,volume_remain,state) "
                        "VALUES ('personal',101,999,60003760,1,100,1,1,'active')")
        workspace = service.get_workspace()
        self.assertEqual(["702"], [row["type_id"] for row in workspace["assets"]])
        self.assertEqual("333", workspace["summary"]["buy_escrow_cents"])
        with self.assertRaises(ValueError):
            corp_order_repo.replace_orders(202, [{**base, "order_id": 3,
                                                   "division_id": 9, "type_id": 703}])
        self.assertEqual([2], [row["order_id"] for row in
                              corp_order_repo.load_orders(202, 4)])
if __name__ == "__main__":
    unittest.main(verbosity=2)
