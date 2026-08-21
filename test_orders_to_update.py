"""Non-regressions du compteur Orders to Update multicompte."""
import unittest
from unittest import mock

import mmd_core as core
import mmd_esi_orders as esi_orders
import mmd_import as imp

try:
    import mmd_gui as gui
except Exception:
    gui = None


STATION_ID = 60003760


def _order(order_id, type_id, char_id, side):
    return {
        "order_id": order_id, "type_id": type_id, "char_id": char_id,
        "char_name": f"Pilot {char_id}", "station_id": STATION_ID,
        "side": side, "price": 100.0, "price_cents": 10_000,
        "vol_remaining": 10, "issued": "2026-08-01T00:00:00Z",
        "range": "station",
    }


def _competitor(order_id, type_id, side):
    return {
        "order_id": order_id, "type_id": type_id,
        "location_id": STATION_ID, "side": side,
        "price": 110.0 if side == 0 else 90.0,
        "vol": 10, "issued": "2026-08-02T00:00:00Z",
        "range": "station",
    }


class OrdersToUpdateTests(unittest.TestCase):
    def test_two_identical_scans_keep_per_character_sum_stable(self):
        orders = [
            _order("mine-1", 34, 1, 0),
            _order("mine-2", 35, 2, 1),
            _order("mine-3", 36, 2, 1),
        ]
        public = [
            _competitor("ext-1", 34, 0),
            _competitor("ext-2", 35, 1),
            _competitor("ext-3", 36, 1),
        ]

        first = core._scan_core(orders, public, "sync 1")
        second = core._scan_core(orders, public, "sync 2")
        expected = {
            "1": {"total": 1, "buy": 1, "sell": 0},
            "2": {"total": 2, "buy": 0, "sell": 2},
        }

        self.assertEqual(first["orders_to_update_by_char"], expected)
        self.assertEqual(second["orders_to_update_by_char"], expected)
        self.assertEqual(first["orders_to_update"], 3)
        self.assertEqual(
            first["orders_to_update"],
            sum(item["total"] for item in expected.values()))
        self.assertTrue(all(row["needs_update"] for row in first["orders_full"]))

    def test_character_pagination_is_atomic(self):
        row = {
            "order_id": 1, "type_id": 34, "location_id": STATION_ID,
            "is_buy_order": True, "price": 100.0, "volume_remain": 1,
            "issued": "2026-08-01T00:00:00Z",
        }
        with mock.patch.object(esi_orders.sso, "_chars", return_value={}), \
                mock.patch.object(
                    esi_orders, "_get",
                    side_effect=[([row], {"X-Pages": "2"}), (None, None)]):
            self.assertIsNone(esi_orders.fetch_character_orders(1))

    def test_zero_orders_is_a_successful_character_sync(self):
        chars = [{"id": 1, "name": "One"}, {"id": 2, "name": "Two"}]
        with mock.patch.object(esi_orders.sso, "connected_chars", return_value=chars), \
                mock.patch.object(
                    esi_orders, "fetch_character_orders", side_effect=[[], None]):
            orders, errors, synced = esi_orders.fetch_all_orders()
        self.assertEqual(orders, [])
        self.assertEqual(errors, [("Two", "token/inaccessible")])
        self.assertEqual(synced, [1])

    def test_all_zero_snapshot_keeps_explicit_character_buckets(self):
        chars = [{"id": 1, "name": "One"}, {"id": 2, "name": "Two"}]
        with mock.patch.object(
                esi_orders, "fetch_all_orders", return_value=([], [], [1, 2])), \
                mock.patch.object(esi_orders.sso, "connected_chars", return_value=chars), \
                mock.patch.object(esi_orders, "_env_structure_ids", return_value=set()):
            data = esi_orders.scan_authed(order_books=[])
        zero = {"total": 0, "buy": 0, "sell": 0}
        self.assertTrue(data["ok"])
        self.assertEqual(data["synced_char_ids"], ["1", "2"])
        self.assertEqual(data["orders_to_update_by_char"], {"1": zero, "2": zero})

    def test_total_private_failure_is_not_published_as_zero(self):
        with mock.patch.object(
                esi_orders, "fetch_all_orders",
                return_value=([], [("One", "token/inaccessible")], [])):
            data = esi_orders.scan_authed(order_books=[])
        self.assertFalse(data["ok"])
        self.assertEqual(data["synced_char_ids"], [])

    def test_fifo_marker_and_count_are_preserved(self):
        mine = _order("mine-fifo", 37, 3, 1)
        newer = _competitor("ext-fifo", 37, 1)
        newer["price"] = 100.0
        data = core._scan_core([mine], [newer], "fifo")
        row = data["orders_full"][0]
        self.assertTrue(row["needs_update"])
        self.assertTrue(row["fifo_overtaken"])
        self.assertEqual(row["gap_cents"], 0)
        self.assertEqual(data["orders_to_update_by_char"]["3"]["total"], 1)

    def test_none_and_valid_empty_public_books_stay_distinct(self):
        orders = [_order("mine-offline", 38, 4, 0)]
        with mock.patch.object(core, "_scan_core", wraps=core._scan_core) as scan:
            offline = imp.build_payload(orders, None, None)
            scan.assert_not_called()
        with mock.patch.object(core, "_scan_core", wraps=core._scan_core) as scan:
            empty_book = imp.build_payload(orders, None, [])
            scan.assert_called_once()
        self.assertFalse(offline["orders_full"][0]["needs_update"])
        self.assertEqual(offline["orders_to_update_by_char"]["4"]["total"], 0)
        self.assertEqual(empty_book["orders_to_update_by_char"]["4"]["total"], 0)


@unittest.skipIf(gui is None, "pywebview absent de cet environnement")
class CountReliabilityMetadataTests(unittest.TestCase):
    def test_never_requested_type_is_not_marked_reliable(self):
        data = core._scan_core([_order("new-type", 999, 7, 1)], [], "new")
        gui._set_count_sync_metadata(
            data, failed_type_ids=[], public_orders=[], requested_type_ids=[34])
        self.assertEqual(data["synced_char_ids"], [])
        self.assertEqual(data["counts_unavailable_type_ids"], [999])

    def test_failed_type_with_fallback_remains_reliable(self):
        public = [_competitor("fallback", 34, 1)]
        data = core._scan_core([_order("mine", 34, 7, 1)], public, "fallback")
        gui._set_count_sync_metadata(
            data, failed_type_ids=[34], public_orders=public,
            requested_type_ids=[34])
        self.assertEqual(data["synced_char_ids"], ["7"])
        self.assertNotIn("counts_unavailable_type_ids", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
