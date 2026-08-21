"""Regression du yoyo Orders to Update lors d'un incident ESI transitoire."""
import time
import unittest
from unittest import mock

import mmd_core as core
import mmd_esi as esi


TYPE_ID = 34
STATION_ID = 60003760


def _my_order():
    return dict(
        order_id="mine-1", type_id=TYPE_ID, char_id=42, char_name="Test Pilot",
        station_id=STATION_ID, side=1, price=100.0, price_cents=10_000,
        vol_remaining=10, issued="2026-08-01T00:00:00Z", range="station")


def _esi_row():
    return {
        "order_id": 9001, "type_id": TYPE_ID, "location_id": STATION_ID,
        "is_buy_order": False, "price": 90.0, "volume_remain": 50,
        "issued": "2026-08-02T00:00:00Z", "range": "station",
    }


class SyncStabilityTests(unittest.TestCase):
    def test_expired_snapshot_survives_transient_esi_failure(self):
        url = (f"{esi.ESI}/markets/{esi.REGION_THE_FORGE}/orders/"
               f"?datasource=tranquility&order_type=all&type_id={TYPE_ID}")
        old_cache = esi._cache
        try:
            esi._cache = {
                url: {"data": [_esi_row()], "etag": "test", "expires": time.time() - 1}
            }
            with mock.patch.object(esi.urllib.request, "urlopen", side_effect=OSError("offline")):
                stale_rows, from_cache = esi._get(url, attempt=3)
            self.assertTrue(from_cache)
            self.assertEqual(stale_rows, [_esi_row()])
        finally:
            esi._cache = old_cache

    def test_orders_to_update_stays_equal_on_two_scans(self):
        first_public = [{
            "order_id": "9001", "type_id": TYPE_ID,
            "location_id": STATION_ID, "side": 1, "price": 90.0,
            "vol": 50, "issued": "2026-08-02T00:00:00Z",
        }]
        first = core._scan_core([_my_order()], first_public, "fresh ESI")

        url = (f"{esi.ESI}/markets/{esi.REGION_THE_FORGE}/orders/"
               f"?datasource=tranquility&order_type=all&type_id={TYPE_ID}")
        old_cache = esi._cache
        try:
            esi._cache = {
                url: {"data": [_esi_row()], "etag": "test", "expires": time.time() - 1}
            }
            with mock.patch.object(esi.time, "sleep", return_value=None):
                with mock.patch.object(esi.urllib.request, "urlopen", side_effect=OSError("offline")):
                    with mock.patch("repositories.snapshot_repository.save_esi_fetch"):
                        second_public = esi._fetch_one((TYPE_ID, esi.REGION_THE_FORGE))
        finally:
            esi._cache = old_cache

        second = core._scan_core([_my_order()], second_public, "stale ESI fallback")
        self.assertEqual(first["orders_to_update"], 1)
        self.assertEqual(second["orders_to_update"], 1)
        self.assertEqual(first["orders_to_update"], second["orders_to_update"])

    def test_valid_empty_book_is_distinct_from_failed_type(self):
        def fake_fetch(args):
            tid, _ = args
            return esi._FetchResult(valid=(tid == TYPE_ID))

        with mock.patch.object(esi, "_load_cache"), mock.patch.object(
                esi, "_fetch_one", side_effect=fake_fetch):
            ids, rows, _, failed = esi.get_live_public_for(
                [TYPE_ID, TYPE_ID + 1], include_failures=True)
        self.assertEqual((ids, rows), ([TYPE_ID, TYPE_ID + 1], []))
        self.assertEqual(failed, [TYPE_ID + 1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
