"""Contracts for the bounded zKillboard R2Z2 marker adapter."""
from datetime import datetime, timezone
import unittest

from eve_map_kill_stream import MARKER_TTL_SECONDS, EveMapKillStream, marker_from_killmail


def timestamp_text(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


class EveMapKillStreamTests(unittest.TestCase):
    def test_only_recent_locatable_kills_become_markers(self):
        now = 1_700_000_000
        payload = {"killmail": {"killmail_id": 42, "solar_system_id": 30_000_142, "killmail_time": timestamp_text(now - 120), "victim": {"ship_type_id": 670}, "attackers": [{"character_id": 1}, {"character_id": 2}]}, "zkb": {"totalValue": 612_000_000}}
        marker = marker_from_killmail(payload, now=now)
        self.assertEqual(42, marker["killmail_id"])
        self.assertEqual(30_000_142, marker["system_id"])
        self.assertEqual(now - 120, marker["happened_at"])
        self.assertEqual(612_000_000, marker["value"])
        self.assertEqual(670, marker["victim_ship_type_id"])
        self.assertEqual(2, marker["attacker_count"])
        self.assertIsNone(marker_from_killmail(payload, now=now + MARKER_TTL_SECONDS + 1))

    def test_recent_markers_are_bounded_and_deduplicated(self):
        now = 1_700_000_000
        stream = EveMapKillStream(now=lambda: now, fetch_json=lambda _url: (404, None))
        payload = {"killmail": {"killmail_id": 77, "solar_system_id": 30_000_142, "killmail_time": timestamp_text(now - 1)}}
        stream._accept(payload)
        stream._accept(payload)
        result = stream.recent_markers()
        self.assertEqual(1, len(result["markers"]))
        self.assertEqual(77, result["markers"][0]["killmail_id"])
        stream.deactivate()

    def test_only_new_live_kills_notify_the_registered_handler(self):
        now, received = 1_700_000_000, []
        stream = EveMapKillStream(now=lambda: now, on_marker=received.append)
        payload = {"killmail": {"killmail_id": 77, "solar_system_id": 30_000_142, "killmail_time": timestamp_text(now - 1)}}
        stream._accept(payload, publish=False)  # initial catch-up is never noisy
        stream._accept(payload, publish=True)   # duplicate: never log twice
        payload["killmail"]["killmail_id"] = 78
        stream._accept(payload, publish=True)
        self.assertEqual([78], [marker["killmail_id"] for marker in received])


if __name__ == "__main__":
    unittest.main()
