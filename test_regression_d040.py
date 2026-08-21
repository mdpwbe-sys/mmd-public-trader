"""Regressions post-d040fd1 : hotkeys natifs et merge Refresh/import."""
import json
import os
import tempfile
import unittest
from unittest import mock

import mmd_import as imp

try:
    import mmd_gui as gui
except Exception:
    gui = None


def _visible_order(cid, name, suffix):
    return {
        "order_id": "refresh-" + suffix, "type_id": 30 + cid,
        "char_id": cid, "char_name": name, "station_id": 60003760,
        "station_name": "Jita", "side": cid % 2, "price_cents": 10_000 + cid,
        "vol_remaining": 10, "issued": "2026-08-09T00:00:00Z",
    }


class RefreshImportRegressionTests(unittest.TestCase):
    def test_import_one_character_keeps_three_from_refresh(self):
        names = {1: "CHARACTER_THREE", 2: "Alt Deux", 3: "Alt Trois"}
        disk = {
            1: {
                "character_id": 1, "character_name": names[1],
                "source_file": "old.txt", "orders": [_visible_order(1, names[1], "old")],
            }
        }
        refreshed = [_visible_order(cid, name, str(cid)) for cid, name in names.items()]
        snapshots = imp.merge_visible_orders(
            disk, refreshed, imported_at="2026-08-09T06:00:00")

        imported = _visible_order(1, names[1], "new")
        imported["order_id"] = "imported-zylnarius"
        imported["price"] = imp.prx.from_cents(imported["price_cents"])
        snapshots[1] = {
            "character_id": 1, "character_name": names[1],
            "source_file": "My Orders-CHARACTER_THREE.txt", "orders": [imported],
        }

        all_orders = [o for snap in snapshots.values() for o in snap["orders"]]
        payload = imp.build_payload(all_orders, None, None)
        self.assertTrue(payload["ok"])
        self.assertEqual({o["char_id"] for o in payload["orders_full"]}, {1, 2, 3})
        self.assertEqual(
            {o["order_id"] for o in payload["orders_full"]},
            {"imported-zylnarius", "refresh-2", "refresh-3"})


@unittest.skipIf(gui is None, "pywebview absent de cet environnement")
class NativeHotkeyDispatchTests(unittest.TestCase):
    def test_transient_webview_error_does_not_break_next_dispatch(self):
        class FakeWindow:
            def __init__(self):
                self.calls = []
                self.fail = True

            def evaluate_js(self, code):
                self.calls.append(code)
                if self.fail:
                    self.fail = False
                    raise RuntimeError("WebView pas encore pret")

        window = FakeWindow()
        self.assertFalse(gui._dispatch_navigation(window, 1))
        self.assertTrue(gui._dispatch_navigation(window, -1))
        self.assertEqual(
            window.calls, ["window.navigateOrders(1)", "window.navigateOrders(-1)"])

    def test_real_import_flow_preserves_refreshed_characters_on_disk(self):
        import mmd_esi
        import mmd_sso
        import memory_store

        names = {1: "CHARACTER_THREE", 2: "Alt Deux", 3: "Alt Trois"}
        disk = {
            "1": {
                "character_id": 1, "character_name": names[1],
                "source_file": "old.txt",
                "orders": [_visible_order(1, names[1], "old")],
            }
        }
        refreshed = [_visible_order(cid, name, str(cid)) for cid, name in names.items()]
        imported = _visible_order(1, names[1], "new")
        imported["order_id"] = "imported-zylnarius"
        imported["price"] = imp.prx.from_cents(imported["price_cents"])

        with tempfile.TemporaryDirectory() as temp_dir:
            snap_path = os.path.join(temp_dir, "character_snapshots.json")
            with open(snap_path, "w", encoding="utf-8") as stream:
                json.dump(disk, stream)
            api = gui.Api()
            api.SNAP_PATH = snap_path
            api._last_orders = refreshed

            with mock.patch.object(imp, "parse_export", return_value=([imported], {1: names[1]})), \
                    mock.patch.object(mmd_sso, "is_connected", return_value=False), \
                    mock.patch.object(mmd_sso, "connected_chars", return_value=[]), \
                    mock.patch.object(mmd_esi, "get_live_public_for",
                                      return_value=([], [], 0.0, [])), \
                    mock.patch.object(memory_store, "persist_import", return_value=1), \
                    mock.patch.object(memory_store, "record_events_from_scan"), \
                    mock.patch.object(gui, "save_cache"):
                api.import_orders("My Orders-CHARACTER_THREE.txt")

            self.assertEqual({o["char_id"] for o in api._last_orders}, {1, 2, 3})
            self.assertEqual(
                {o["order_id"] for o in api._last_orders},
                {"imported-zylnarius", "refresh-2", "refresh-3"})
            with open(snap_path, encoding="utf-8") as stream:
                persisted = json.load(stream)
            self.assertEqual(set(persisted), {"1", "2", "3"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
