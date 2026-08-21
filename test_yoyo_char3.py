"""Non-regression: un import partiel ne retire jamais le troisieme perso connu."""
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


def _order(cid, name, suffix):
    return {
        "order_id": suffix, "type_id": 40 + cid,
        "char_id": cid, "char_name": name, "station_id": 60003760,
        "station_name": "Jita", "side": cid % 2,
        "price_cents": 20_000 + cid, "vol_remaining": 10,
        "issued": "2026-08-09T00:00:00Z",
    }


@unittest.skipIf(gui is None, "pywebview absent de cet environnement")
class CharacterThreeYoyoTests(unittest.TestCase):
    def test_char3_survives_zyl_mike_and_repeated_zyl_imports(self):
        import mmd_esi
        import mmd_sso
        import memory_store

        names = {1: "CHARACTER_THREE", 2: "CHARACTER_TWO", 3: "CHARACTER_ONE"}
        refreshed = [
            _order(cid, name, f"refresh-{cid}") for cid, name in names.items()
        ]
        partial_disk = imp.merge_visible_orders(
            {}, refreshed[:2], imported_at="2026-08-09T08:00:00")

        with tempfile.TemporaryDirectory() as temp_dir:
            snap_path = os.path.join(temp_dir, "character_snapshots.json")
            with open(snap_path, "w", encoding="utf-8") as stream:
                json.dump(partial_disk, stream, default=str)

            api = gui.Api()
            api.SNAP_PATH = snap_path
            self.assertTrue(api._remember_visible_orders(refreshed))
            self.assertEqual(set(api._snapshots), {1, 2, 3})

            # Reproduit un JSON partiel alors que le Refresh memoire connait CHARACTER_ONE.
            with open(snap_path, "w", encoding="utf-8") as stream:
                json.dump(
                    {cid: snap for cid, snap in api._snapshots.items() if cid != 3},
                    stream, default=str)

            serial = [0]

            def parse_export(path):
                serial[0] += 1
                cid = 2 if "Mike" in path else 1
                order = _order(cid, names[cid], f"imported-{cid}-{serial[0]}")
                order["price"] = imp.prx.from_cents(order["price_cents"])
                return [order], {cid: names[cid]}

            patches = (
                mock.patch.object(imp, "parse_export", side_effect=parse_export),
                mock.patch.object(mmd_sso, "is_connected", return_value=False),
                mock.patch.object(mmd_sso, "connected_chars", return_value=[]),
                mock.patch.object(
                    mmd_esi, "get_live_public_for",
                    return_value=([], [], 0.0, [])),
                mock.patch.object(memory_store, "persist_import", return_value=1),
                mock.patch.object(memory_store, "record_events_from_scan"),
                mock.patch.object(gui, "save_cache"),
            )
            with patches[0], patches[1], patches[2], patches[3], \
                    patches[4], patches[5], patches[6]:
                for path in (
                        "My Orders-CHARACTER_THREE.txt",
                        "My Orders-CHARACTER_TWO.txt",
                        "My Orders-CHARACTER_THREE.txt",
                        "My Orders-CHARACTER_THREE.txt"):
                    # Meme si la vue volatile est partielle, le connu durable reste.
                    api._last_orders = refreshed[:2]
                    api.import_orders(path)
                    self.assertEqual(
                        {o["char_id"] for o in api._last_orders}, {1, 2, 3})
                    self.assertIn(
                        "refresh-3",
                        {o["order_id"] for o in api._last_orders})
                    with open(snap_path, encoding="utf-8") as stream:
                        self.assertEqual(set(json.load(stream)), {"1", "2", "3"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
