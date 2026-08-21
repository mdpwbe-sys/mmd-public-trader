#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Régressions du client ESI portefeuille; aucun accès réseau réel."""
import unittest
from unittest.mock import patch

import mmd_asset_tree as tree
import mmd_contracts as contracts
import mmd_esi_auth as auth
import mmd_esi_portfolio as portfolio
import mmd_sso as sso


class PortfolioEsiTests(unittest.TestCase):
    def test_scopes_are_official_read_only_names(self):
        self.assertIn("esi-wallet.read_corporation_wallets.v1", sso.SCOPES)
        self.assertIn("esi-assets.read_assets.v1", sso.SCOPES)
        self.assertNotIn("esi-wallet.read_corporation_wallet.v1", sso.SCOPES)
        self.assertNotIn("esi-assets.read_character_assets.v1", sso.SCOPES)
        self.assertFalse(any(".write_" in scope for scope in sso.SCOPES))

    def test_transport_allowlist_has_personal_wallet_without_division(self):
        self.assertTrue(auth._allowed("GET", "/v1/characters/42/wallet/transactions/"))
        self.assertFalse(auth._allowed("GET", "/v1/characters/42/wallets/1/transactions/"))
        self.assertTrue(auth._allowed("GET", "/v4/characters/42/assets/"))
        self.assertFalse(auth._allowed("GET", "/v5/characters/42/assets/"))
        self.assertTrue(auth._allowed("GET", "/v1/corporations/88/divisions/"))
        self.assertFalse(auth._allowed("GET", "/v2/corporations/88/divisions/"))
        self.assertTrue(auth._allowed("POST", "/v1/characters/42/assets/names/"))
        self.assertFalse(auth._allowed("POST", "/v1/characters/42/orders/"))

    def test_transaction_cursor_is_descending_atomic_and_has_no_ref_type(self):
        calls = []
        pages = {
            None: [{"transaction_id": 300, "unit_price": "1.25", "type_id": 7,
                    "quantity": 1, "is_buy": True, "date": "2026-01-03",
                    "location_id": 1}],
            300: [{"transaction_id": 299, "unit_price": "2.50", "type_id": 7,
                   "quantity": 1, "is_buy": False, "date": "2026-01-02",
                   "location_id": 1}],
            299: [],
        }

        def fake_get(path, char_id, params=None):
            cursor = (params or {}).get("from_id")
            calls.append(cursor)
            return auth.EsiResult(pages[cursor], status=200)

        with patch.object(auth, "get_json", side_effect=fake_get):
            result = contracts.fetch_transactions("/v1/characters/42/wallet/transactions/", 42)
        self.assertTrue(result.ok)
        self.assertEqual([None, 300, 299], calls)
        self.assertEqual([125, 250], [row["unit_price_cents"] for row in result.data])
        self.assertTrue(all("ref_type" not in row for row in result.data))

    def test_transaction_high_water_mark_stops_without_duplicate(self):
        page = [{"transaction_id": 12, "unit_price": "1", "type_id": 7,
                 "quantity": 1, "is_buy": True, "date": "2026-01-02", "location_id": 1},
                {"transaction_id": 11, "unit_price": "1", "type_id": 7,
                 "quantity": 1, "is_buy": True, "date": "2026-01-01", "location_id": 1}]
        with patch.object(auth, "get_json", return_value=auth.EsiResult(page, status=200)) as get:
            result = contracts.fetch_transactions("/v1/characters/42/wallet/transactions/",
                                                  42, known_ids={11})
        self.assertEqual([12], [row["transaction_id"] for row in result.data])
        get.assert_called_once()

    def test_partial_xpages_is_rejected(self):
        first = auth.EsiResult([{"id": 1}], {"X-Pages": "2"}, status=200)
        failed = auth.failure("/v4/characters/42/assets/", "network", "down")
        with patch.object(auth, "get_json", side_effect=[first, failed]):
            result = auth.fetch_all_pages("/v4/characters/42/assets/", 42)
        self.assertFalse(result.ok)
        self.assertIsNone(result.data)

    def test_division_name_is_dynamic_and_can_be_id_four(self):
        response = auth.EsiResult({"wallet": [{"division": 4,
                                                "name": "Réserve & Projets"}]}, status=200)
        with patch.object(portfolio, "_member", return_value=None), \
             patch.object(portfolio, "_scope", return_value=None), \
             patch.object(auth, "get_json", return_value=response):
            result = portfolio.fetch_corporation_divisions(88, 42)
        selected = result.data[3]
        self.assertEqual(4, selected["division_id"])
        self.assertEqual("Réserve & Projets", selected["name"])

    def test_asset_tree_resolves_descendants_and_cycles(self):
        assets = [
            {"item_id": 1, "type_id": 10, "location_id": 60_003_760,
             "location_type": "station"},
            {"item_id": 2, "type_id": 11, "location_id": 1, "location_type": "item"},
            {"item_id": 3, "type_id": 12, "location_id": 2, "location_type": "item"},
            {"item_id": 4, "type_id": 13, "location_id": 5, "location_type": "item"},
            {"item_id": 5, "type_id": 14, "location_id": 4, "location_type": "item"},
        ]
        graph = tree.build_asset_tree(assets, {1: "Boîte libre"})
        self.assertEqual([2, 3], graph.descendant_ids(1))
        self.assertIn(4, graph.cyclic_ids)
        self.assertEqual("Boîte libre", graph.container_options()[0]["name"])

    def test_single_bpc_contract_keeps_250m_exactly(self):
        contract = {"contract_id": 90, "type": "item_exchange", "status": "finished",
                    "issuer_corporation_id": 77, "issuer_id": 1, "acceptor_id": 88,
                    "date_issued": "2026-01-01", "date_completed": "2026-01-02",
                    "price": "250000000.00"}
        item = {"record_id": 91, "type_id": 24689, "quantity": 1,
                "raw_quantity": -2, "is_included": True, "is_singleton": True}
        with patch.object(auth, "fetch_all_pages",
                          return_value=auth.EsiResult([contract], status=200)), \
             patch.object(auth, "get_json",
                          return_value=auth.EsiResult([item], status=200)):
            result = contracts.fetch_corporation_contract_assets(88, 42)
        self.assertEqual(25_000_000_000, result.data[0]["acquisition_cost_cents"])
        self.assertTrue(result.data[0]["is_blueprint_copy"])

    def test_personal_issuer_in_same_corp_does_not_invert_received_items(self):
        contract = {"contract_id": 92, "type": "item_exchange", "status": "finished",
                    "issuer_corporation_id": 88, "issuer_id": 1, "acceptor_id": 88,
                    "for_corporation": False, "date_completed": "2026-01-02",
                    "price": "250000000"}
        item = {"record_id": 93, "type_id": 24689, "quantity": 1,
                "raw_quantity": -2, "is_included": True, "is_singleton": True}
        with patch.object(auth, "fetch_all_pages",
                          return_value=auth.EsiResult([contract], status=200)), \
             patch.object(auth, "get_json",
                          return_value=auth.EsiResult([item], status=200)):
            result = contracts.fetch_corporation_contract_assets(88, 42)
        self.assertEqual(25_000_000_000, result.data[0]["acquisition_cost_cents"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
