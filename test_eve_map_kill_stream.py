from datetime import datetime, timezone
import json
import unittest

from eve_map_kill_stream import (
    BOOTSTRAP_MAX_REQUESTS,
    MARKER_TTL_SECONDS,
    STREAM_RESUME_STALE_SECONDS,
    EveMapKillStream,
    marker_from_killmail,
    marker_to_recent_kill,
)
from urllib.error import URLError


class EveMapKillStreamTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 3, 10, 5, tzinfo=timezone.utc).timestamp()

    def test_recent_locatable_kill_becomes_compact_marker(self):
        marker = marker_from_killmail({
            "killmail_id": 42,
            "solar_system_id": 30000142,
            "killmail_time": "2026-09-03T10:03:00Z",
            "victim": {"ship_type_id": 670},
            "attackers": [{"character_id": 1}, {"character_id": 2}],
            "zkb": {"totalValue": 123456789},
        }, now=self.now)
        self.assertEqual(marker["killmail_id"], 42)
        self.assertEqual(marker["system_id"], 30000142)
        self.assertEqual(marker["value"], 123456789)
        self.assertEqual(marker["victim_ship_type_id"], 670)
        self.assertEqual(marker["attacker_count"], 2)

    def test_live_marker_retains_killmail_details_for_the_system_panel(self):
        marker = marker_from_killmail({
            "killmail_id": 43,
            "solar_system_id": 30000142,
            "killmail_time": "2026-09-03T10:03:00Z",
            "victim": {"ship_type_id": 670, "character_id": 10, "corporation_id": 20, "alliance_id": 30},
            "attackers": [{"character_id": 1, "ship_type_id": 587, "damage_done": 100, "final_blow": True}],
            "zkb": {"totalValue": 612000000, "hash": "abc123"},
        }, now=self.now)
        recent = marker_to_recent_kill(marker)
        self.assertEqual(recent["ship_type_id"], 670)
        self.assertEqual(recent["victim_character_id"], 10)
        self.assertEqual(recent["attacker_count"], 1)
        self.assertEqual(recent["attackers"][0]["ship_type_id"], 587)
        self.assertEqual(recent["zkb_hash"], "abc123")

    def test_r2z2_esi_envelope_is_normalized_like_a_regular_killmail(self):
        marker = marker_from_killmail({
            "killmail_id": 44,
            "esi": {
                "killmail_id": 44,
                "solar_system_id": 30000142,
                "killmail_time": "2026-09-03T10:03:00Z",
                "victim": {"ship_type_id": 670},
                "attackers": [{"character_id": 1, "ship_type_id": 587}],
            },
            "zkb": {"totalValue": 42, "hash": "r2z2-hash"},
        }, now=self.now)
        self.assertEqual(marker["killmail_id"], 44)
        self.assertEqual(marker["system_id"], 30000142)
        self.assertEqual(marker["value"], 42)
        self.assertEqual(marker["zkb_hash"], "r2z2-hash")

    def test_only_new_live_kills_notify_the_registered_handler(self):
        received = []
        stream = EveMapKillStream(now=lambda: self.now, on_marker=received.append)
        payload = {
            "killmail_id": 42,
            "solar_system_id": 30000142,
            "killmail_time": "2026-09-03T10:03:00Z",
            "victim": {"ship_type_id": 670},
            "attackers": [{"character_id": 1}],
        }
        stream._accept(payload, publish=False)  # historical bootstrap marker
        stream._accept(payload, publish=True)   # duplicate: never log twice
        self.assertEqual(received, [])

        payload["killmail_id"] = 43
        stream._accept(payload, publish=True)
        self.assertEqual([marker["killmail_id"] for marker in received], [43])

    def test_old_or_unlocatable_kill_is_not_exposed(self):
        old = {
            "killmail_id": 42,
            "solar_system_id": 30000142,
            "killmail_time": "2026-09-03T09:03:00Z",
        }
        self.assertIsNone(marker_from_killmail(old, now=self.now))
        self.assertGreater(self.now - datetime(2026, 9, 3, 9, 3, tzinfo=timezone.utc).timestamp(), MARKER_TTL_SECONDS)

    def test_stale_resume_restarts_from_the_current_head(self):
        stream = EveMapKillStream(now=lambda: self.now)
        stream._sequence = 123
        stream._last_success_at = self.now - STREAM_RESUME_STALE_SECONDS - 1
        self.assertTrue(stream.should_restart_from_head())

    def test_quick_reopen_keeps_sequence_and_clears_stop_signal(self):
        class AliveThread:
            def is_alive(self):
                return True
        stream = EveMapKillStream(now=lambda: self.now)
        stream._thread = AliveThread()
        stream._sequence = 123
        stream._last_success_at = self.now
        stream._stop.set()
        stream.activate()
        self.assertFalse(stream._stop.is_set())
        self.assertEqual(123, stream._sequence)

    def test_network_exception_becomes_a_bounded_retry_signal(self):
        stream = EveMapKillStream(now=lambda: self.now, fetch_json=lambda _: (_ for _ in ()).throw(URLError("reset")))
        status, payload, error = stream._fetch("https://example.invalid")
        self.assertEqual(status, 0)
        self.assertIsNone(payload)
        self.assertIn("network", error.lower())

    def test_bootstrap_is_hard_bounded_and_switches_to_the_head(self):
        stream = EveMapKillStream(now=lambda: self.now, fetch_json=lambda _: (404, None))
        stream._bootstrap_head = 500
        stream._bootstrap_cursor = 300
        stream._bootstrap_requests = BOOTSTRAP_MAX_REQUESTS - 1
        self.assertTrue(stream._bootstrap_step())
        self.assertEqual(501, stream._sequence)
        self.assertIsNone(stream._bootstrap_cursor)

    def test_worker_handles_invalid_json_without_escaping_its_loop(self):
        stream = EveMapKillStream(now=lambda: self.now, fetch_json=lambda _: (_ for _ in ()).throw(json.JSONDecodeError("bad", "{", 0)))
        errors = []
        def stop_after_failure(error):
            errors.append(error)
            stream._stop.set()
        stream._failure_wait = stop_after_failure
        stream._run()
        self.assertEqual(1, len(errors))
        self.assertIn("invalid JSON", errors[0])

    def test_history_outlives_canvas_marker_window(self):
        class AliveThread:
            def is_alive(self):
                return True
        stream = EveMapKillStream(now=lambda: self.now)
        stream._thread = AliveThread()
        marker = marker_from_killmail({
            "killmail_id": 99, "solar_system_id": 30000142,
            "killmail_time": "2026-09-03T09:30:00Z",
        }, now=self.now)
        stream._markers[99] = marker
        with stream._lock:
            stream._state = "live"
        self.assertEqual([], stream.recent_markers()["markers"])
        self.assertEqual([99], [row["killmail_id"] for row in stream.recent_kills(30000142)])

    def test_canvas_marker_window_keeps_twenty_minute_combat_visible(self):
        stream = EveMapKillStream(now=lambda: self.now)
        marker = marker_from_killmail({
            "killmail_id": 100, "solar_system_id": 30000142,
            "killmail_time": "2026-09-03T09:45:00Z",
        }, now=self.now)
        stream._markers[100] = marker
        self.assertEqual([100], [row["killmail_id"] for row in stream.recent_markers()["markers"]])


if __name__ == "__main__":
    unittest.main()
