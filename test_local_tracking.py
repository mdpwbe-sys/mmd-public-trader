from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from eve_local_watchdog import FloodDetector, LocalChatWatchdog, repeat_ratio
from eve_map_intel_alert import EveMapIntelAlert
from eve_tracked_positions import TrackedPositions


class Map:
    systems = [{"id": 1, "name": "Jita"}, {"id": 2, "name": "Perimeter"}]
    def get_map_data(self): return {"systems": self.systems}
    def get_system(self, system_id): return next((row for row in self.systems if row["id"] == system_id), None)
    def systems_within_jumps(self, system_id, radius): return {row["id"]: abs(row["id"] - system_id) for row in self.systems if abs(row["id"] - system_id) <= radius}


class LocalTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)
        self.now = [datetime(2026, 9, 5, tzinfo=timezone.utc).timestamp()]
        self.fetch = Mock(return_value=1)
        self.store = TrackedPositions(map_service=Map(), characters=lambda: [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}], fetch_location=self.fetch, now=lambda: self.now[0])
        self.watcher = LocalChatWatchdog(self.store, directory=self.root, settings_path=self.root / "settings.json", now=lambda: self.now[0], active_clients=lambda: {"alpha", "beta"})

    def _clock(self): return datetime.fromtimestamp(self.now[0], timezone.utc).strftime("%Y.%m.%d %H:%M:%S")
    def _log(self, name="Alpha", identifier=1, system="Jita"):
        path = self.root / f"Local_20260905_000000_{identifier}.txt"
        path.write_text(f"Channel ID: local\nListener: {name}\nSession started: {self._clock()}\n[ {self._clock()} ] EVE System > Channel changed to Local : {system}\n", encoding="utf-16")
        return path
    def _append(self, path, message):
        with path.open("ab") as target: target.write(f"[ {self._clock()} ] EVE System > {message}\n".encode("utf-16-le"))

    def test_local_position_skips_esi_and_jump_updates_radius(self):
        path = self._log(); alert = EveMapIntelAlert(settings_path=self.root / "alert.json", map_service=Map(), now=lambda: self.now[0]); alert._settings.update(enabled=True, radius_jumps=0); alert.position_snapshot = self.store.snapshot; self.store.subscribe(alert.update_positions)
        self.watcher.poll_once(); self.store.refresh(); self.assertEqual(self.fetch.call_args_list[0].args, (2,)); self.assertIsNotNone(alert.on_marker({"killmail_id": 1, "system_id": 1}))
        self.now[0] += 1; self._append(path, "Channel changed to Local : Perimeter"); self.watcher.poll_once()
        self.assertEqual(self.store.snapshot()["positions"][0]["system_id"], 2); self.assertIsNone(alert.on_marker({"killmail_id": 2, "system_id": 1})); self.assertIsNotNone(alert.on_marker({"killmail_id": 3, "system_id": 2}))

    def test_stale_local_uses_esi_without_sound_eligibility(self):
        self._log(); self.watcher.poll_once(); self.now[0] += 31; self.store.refresh()
        self.assertEqual(self.store.snapshot()["positions"][0]["source"], "ESI")
        alert = EveMapIntelAlert(settings_path=self.root / "alert.json", map_service=Map(), now=lambda: self.now[0]); alert._settings.update(enabled=True); alert.position_snapshot = self.store.snapshot; self.store.subscribe(alert.update_positions); alert.update_positions(self.store.snapshot())
        self.assertIsNone(alert.on_marker({"killmail_id": 1, "system_id": 1}))

    def test_two_clients_unknown_system_and_old_session_are_safe(self):
        self._log(); self._log("Beta", 2, "Perimeter"); self.watcher.poll_once(); self.assertEqual(len(self.store.snapshot()["positions"]), 2)
        self.assertFalse(self.store.observe_local(character_id=999, character_name="Unknown", system_name="Jita", observed_at=self.now[0], session_source="x", session_started=self.now[0]))
        self.assertFalse(self.store.observe_local(character_id=1, character_name="Alpha", system_name="Unknown", observed_at=self.now[0], session_source="x", session_started=self.now[0]))
        self.assertTrue(self.store.observe_local(character_id=1, character_name="Alpha", system_name="Perimeter", observed_at=self.now[0], session_source="new", session_started=self.now[0]))
        self.assertFalse(self.store.observe_local(character_id=1, character_name="Alpha", system_name="Jita", observed_at=self.now[0], session_source="old", session_started=self.now[0] - 1))

class FloodTests(unittest.TestCase):
    def test_threshold_consecutive_minutes_cooldown_and_repeat(self):
        detector = FloodDetector();
        for minute in range(5):
            for _ in range(5): detector.add("Pilot", "Buy 123 https://example.invalid", minute * 60 + 1)
        self.assertFalse(detector.evaluate(300))
        detector = FloodDetector()
        for minute in range(4):
            for _ in range(6): detector.add("Pilot", "Buy 123 https://example.invalid", minute * 60 + 1)
        self.assertFalse(detector.evaluate(240))
        for _ in range(6): detector.add("Pilot", "Buy 456 https://example.invalid", 241)
        self.assertEqual(len(detector.evaluate(300)), 1); self.assertFalse(detector.evaluate(301))
        self.assertEqual(repeat_ratio(["Buy 1 https://x", " buy 2 https://y "]), 50)

if __name__ == "__main__": unittest.main()
