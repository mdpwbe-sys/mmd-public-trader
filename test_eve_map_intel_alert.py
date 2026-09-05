from pathlib import Path
import tempfile
import unittest

from eve_map_intel_alert import EveMapIntelAlert, stream_required


class Graph:
    def __init__(self, distances):
        self.distances = distances
        self.calls = []

    def systems_within_jumps(self, source_id, radius):
        self.calls.append((source_id, radius))
        return {system_id: distance for system_id, distance in self.distances.get(source_id, {}).items() if distance <= radius}


class IntelAlertTests(unittest.TestCase):
    def setUp(self):
        self.positions = [{"character_id": 10, "name": "Zylnarius", "system_id": 1}]
        self.position_reads = 0
        self.graph = Graph({1: {1: 0, 2: 5, 3: 6}, 4: {4: 0, 2: 2}})
        self.directory = tempfile.TemporaryDirectory()
        self.alert = EveMapIntelAlert(settings_path=Path(self.directory.name) / "intel_alert.json", map_service=self.graph, get_positions=self.get_positions, now=lambda: 1000)
        self.alert.configure({"enabled": True, "radius_jumps": 5, "sound_enabled": True})
        self.alert.stop()
        self.alert.refresh_positions()

    def tearDown(self):
        self.alert.stop()
        self.directory.cleanup()

    def get_positions(self):
        self.position_reads += 1
        return {"ok": True, "positions": self.positions}

    @staticmethod
    def marker(killmail_id, system_id):
        return {"killmail_id": killmail_id, "system_id": system_id, "victim_ship_type_id": 587, "value": 12_345_678, "attacker_count": 3}

    def test_kill_at_zero_and_radius_boundary_alert(self):
        reads_before_marker = self.position_reads
        zero = self.alert.on_marker(self.marker(1, 1))
        boundary = self.alert.on_marker(self.marker(2, 2))
        self.assertEqual(zero["distance_jumps"], 0)
        self.assertEqual(boundary["distance_jumps"], 5)
        self.assertEqual(boundary["nearest_character_name"], "Zylnarius")
        self.assertEqual(boundary["victim_ship_type_id"], 587)
        self.assertEqual(boundary["attacker_count"], 3)
        self.assertEqual(self.position_reads, reads_before_marker, "un kill ne relit jamais ESI : il consulte seulement le cache de rayon")

    def test_kill_outside_radius_does_not_alert(self):
        self.assertIsNone(self.alert.on_marker(self.marker(3, 3)))

    def test_nearest_character_wins_and_same_kill_alerts_once(self):
        self.positions.append({"character_id": 11, "name": "Mike Craft", "system_id": 4})
        self.alert.refresh_positions()
        first = self.alert.on_marker(self.marker(4, 2))
        self.assertEqual(first["nearest_character_id"], 11)
        self.assertEqual(first["distance_jumps"], 2)
        self.assertIsNone(self.alert.on_marker(self.marker(4, 2)))

    def test_position_change_rebuilds_only_local_radius_cache(self):
        self.assertIn((1, 5), self.graph.calls)
        self.positions[0] = {"character_id": 10, "name": "Zylnarius", "system_id": 4}
        self.alert.refresh_positions()
        self.assertIn((4, 5), self.graph.calls)
        self.assertEqual(self.alert.on_marker(self.marker(5, 2))["distance_jumps"], 2)

    def test_stream_lifecycle_keeps_r2z2_only_for_visible_map_or_enabled_alert(self):
        self.assertTrue(stream_required(True, False))
        self.assertTrue(stream_required(False, True))
        self.assertFalse(stream_required(False, False))

    def test_settings_are_persisted(self):
        self.alert.configure({"enabled": True, "radius_jumps": 7, "sound_enabled": False, "tracked_character_ids": [10]})
        restored = EveMapIntelAlert(settings_path=self.alert.settings_path)
        self.assertEqual(restored.settings(), {"enabled": True, "radius_jumps": 7, "sound_enabled": False, "tracked_character_ids": [10]})


if __name__ == "__main__":
    unittest.main()
