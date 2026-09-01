import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from eve_map_service import EveMapService
from tools.build_eve_map import build_dataset


class EveMapBuilderTests(unittest.TestCase):
    def test_build_dataset_keeps_physical_position_and_deduplicates_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "sde.zip"
            output = root / "eve_map.json"
            payloads = {
                "fsd/universe/regions.jsonl": [{"region_id": 10000002, "name": "The Forge"}],
                "fsd/universe/constellations.jsonl": [{"constellation_id": 20000020, "name": "Kimotoro", "region_id": 10000002}],
                "fsd/universe/systems.jsonl": [
                    {"solar_system_id": 30000142, "name": "Jita", "security_status": 0.9, "constellation_id": 20000020, "region_id": 10000002, "position": {"x": 1.0, "y": 2.0, "z": 3.0}},
                    {"solar_system_id": 30000144, "name": "Perimeter", "security_status": 0.9, "constellation_id": 20000020, "region_id": 10000002, "position": {"x": 4.0, "y": 5.0, "z": 6.0}},
                ],
                "fsd/universe/stargates.jsonl": [
                    {"stargate_id": 500, "solar_system_id": 30000142, "destination_stargate_id": 600},
                    {"stargate_id": 600, "solar_system_id": 30000144, "destination_stargate_id": 500},
                    {"stargate_id": 501, "solar_system_id": 30000142, "destination_stargate_id": 600},
                ],
            }
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, rows in payloads.items():
                    bundle.writestr(name, "".join(json.dumps(row) + "\n" for row in rows))

            result = build_dataset(archive, output, source_url="fixture")
            dataset = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(result["systems"], 2)
            self.assertEqual(dataset["systems"][0]["position_m"], {"x": 1.0, "y": 2.0, "z": 3.0})
            self.assertEqual(len(dataset["gates"]), 1)


class EveMapServiceTests(unittest.TestCase):
    def test_find_route_and_unknown_system_are_defensive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eve_map.json"
            path.write_text(json.dumps({
                "meta": {"schema_version": 1},
                "systems": [
                    {"id": 1, "name": "Alpha", "security": 0.8, "position_m": {"x": 0, "y": 0, "z": 0}, "position": {"x": 0, "y": 0, "z": 0}},
                    {"id": 2, "name": "Beta", "security": 0.4, "position_m": {"x": 3, "y": 4, "z": 0}, "position": {"x": 1, "y": 1, "z": 0}},
                ],
                "gates": [{"source": 1, "target": 2}],
            }), encoding="utf-8")
            service = EveMapService(path)

            route = service.find_route(1, 2)

            self.assertEqual(route["systems"], [1, 2])
            self.assertEqual(route["jumps"], 1)
            self.assertEqual(service.distance_m(1, 2), 5.0)
            self.assertIsNone(service.get_system(999))
            self.assertEqual(service.find_route(1, 999)["error"], "unknown_system")


if __name__ == "__main__":
    unittest.main()
