import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from eve_map_service import EveMapService
from eve_map_intel_service import EveMapIntelService, default_cache_path
from tools.build_eve_map import build_dataset


class EveMapBuilderTests(unittest.TestCase):
    def test_build_dataset_keeps_physical_position_and_deduplicates_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "sde.zip"
            output = root / "eve_map.json"
            payloads = {
                "fsd/universe/regions.jsonl": [{"region_id": 10000002, "name": "The Forge", "faction_id": 500001}],
                "fsd/universe/constellations.jsonl": [{"constellation_id": 20000020, "name": "Kimotoro", "region_id": 10000002}],
                "fsd/universe/systems.jsonl": [
                    {"solar_system_id": 30000142, "name": "Jita", "security_status": 0.9, "faction_id": 500001, "constellation_id": 20000020, "region_id": 10000002, "position": {"x": 1.0, "y": 2.0, "z": 3.0}},
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
            self.assertEqual(dataset["systems"][0]["faction_id"], 500001)
            self.assertEqual(dataset["systems"][1]["faction_id"], 500001)
            self.assertEqual(len(dataset["gates"]), 1)


class EveMapServiceTests(unittest.TestCase):
    def test_raw_point_487_is_high_sec_and_allowed_by_a_high_sec_route(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eve_map.json"
            path.write_text(json.dumps({
                "systems": [
                    {"id": 1, "name": "Alpha", "security": 0.9, "position_m": {"x": 0, "y": 0, "z": 0}},
                    {"id": 2, "name": "Borderline", "security": 0.487, "position_m": {"x": 1, "y": 0, "z": 0}},
                ],
                "gates": [{"source": 1, "target": 2}],
            }), encoding="utf-8")
            service = EveMapService(path)

            self.assertEqual("high", service.security_class(0.487))
            self.assertEqual([1, 2], service.find_route(1, 2, min_security="high")["systems"])

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
            self.assertEqual(service.find_route(1, 2, min_security=0.5)["error"], "unsafe_endpoint")
            self.assertEqual(service.distance_m(1, 2), 5.0)
            self.assertIsNone(service.get_system(999))
            self.assertEqual(service.find_route(1, 999)["error"], "unknown_system")


class EveMapIntelServiceTests(unittest.TestCase):
    def test_default_map_intel_cache_uses_the_mmd_appdata_identity(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"APPDATA": directory}):
            self.assertEqual(Path(directory) / "MMD-Trader" / "cache" / "eve_map_intel.json", default_cache_path())

    def test_sovereignty_uses_current_public_transport_without_compatibility_header(self):
        class Response:
            def read(self):
                return b'[{"system_id":30000142,"alliance_id":99000001}]'

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with tempfile.TemporaryDirectory() as directory:
            service = EveMapIntelService(Path(directory) / "intel.json")
            with patch("eve_map_intel_service.urllib.request.urlopen", return_value=Response()) as fetch, patch("mmd_esi._get", side_effect=AssertionError("legacy compatibility transport must not serve sovereignty")):
                rows = service._fetch_esi("/sovereignty/map/")

        self.assertEqual(rows[0]["alliance_id"], 99000001)
        request = fetch.call_args.args[0]
        self.assertIsNone(request.get_header("X-compatibility-date"))

    def test_live_intel_is_cached_and_stale_cache_survives_esi_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "intel.json"
            payloads = {
                "/universe/system_jumps/": [{"system_id": 30000142, "ship_jumps": 42315}],
                "/universe/system_kills/": [{"system_id": 30000142, "ship_kills": 7, "pod_kills": 2, "npc_kills": 183}],
            }
            calls = []
            service = EveMapIntelService(cache_path, now=lambda: 1_000, fetch_json=lambda endpoint: calls.append(endpoint) or payloads[endpoint])
            live = service.get_live_intel()
            self.assertEqual(live["state"], "live")
            self.assertEqual(live["systems"]["30000142"]["ship_jumps"], 42315)
            self.assertGreater(live["systems"]["30000142"]["danger"], 0)
            self.assertEqual(len(calls), 2)
            cached = EveMapIntelService(cache_path, now=lambda: 1_300, fetch_json=lambda _: self.fail("fresh cache must not fetch"))
            self.assertEqual(cached.get_live_intel()["state"], "fresh")
            stale = EveMapIntelService(cache_path, now=lambda: 1_700, fetch_json=lambda _: (_ for _ in ()).throw(OSError("offline")))
            self.assertEqual(stale.get_live_intel()["state"], "stale")

    def test_sovereignty_is_cached_and_keeps_ownership_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "intel.json"
            calls = []
            payload = [{"system_id": 30000142, "alliance_id": 99000001, "corporation_id": 98000001}, {"system_id": 30000001, "faction_id": 500007}]
            service = EveMapIntelService(cache_path, now=lambda: 2_000, fetch_json=lambda endpoint: calls.append(endpoint) or payload)
            live = service.get_sovereignty()
            self.assertEqual(live["systems"]["30000142"]["alliance_id"], 99000001)
            self.assertEqual(live["systems"]["30000001"]["faction_id"], 500007)
            cached = EveMapIntelService(cache_path, now=lambda: 2_200, fetch_json=lambda _: self.fail("fresh sovereignty cache must not fetch"))
            self.assertEqual(cached.get_sovereignty()["state"], "fresh")
            self.assertEqual(calls, ["/sovereignty/map/"])

    def test_entity_names_are_resolved_lazily_and_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            service = EveMapIntelService(
                Path(directory) / "intel.json",
                now=lambda: 2_000,
                fetch_names=lambda ids: calls.append(ids) or [{"id": 99000001, "name": "Example Alliance", "category": "alliance"}],
            )
            live = service.get_entity_names([99000001])
            self.assertEqual(live["names"]["99000001"]["name"], "Example Alliance")
            cached = EveMapIntelService(Path(directory) / "intel.json", now=lambda: 2_100, fetch_names=lambda _: self.fail("fresh entity-name cache must not fetch"))
            self.assertEqual(cached.get_entity_names([99000001])["state"], "fresh")
            self.assertEqual(calls, [[99000001]])

    def test_kill_attackers_use_cached_kill_and_lazy_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "intel.json"
            service = EveMapIntelService(
                path,
                now=lambda: 2_000,
                fetch_names=lambda ids: [
                    {"id": 9001, "name": "Hunter", "category": "character"},
                    {"id": 587, "name": "Rifter", "category": "inventory_type"},
                ],
            )
            service._write_cache({"zkill": {"30000142": {"updated_at": 2_000, "kills": [{
                "killmail_id": 42, "attacker_count": 1,
                "attackers": [{"character_id": 9001, "ship_type_id": 587, "final_blow": True, "damage_done": 1234}],
            }]}}})
            detail = service.get_kill_attackers(30000142, 42)
            self.assertEqual(detail["attackers"][0]["pilot_name"], "Hunter")
            self.assertEqual(detail["attackers"][0]["ship_name"], "Rifter")
            self.assertTrue(detail["attackers"][0]["final_blow"])

    def test_recent_kills_merge_live_stream_with_cached_zkill_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "intel.json"
            live = [{
                "killmail_id": 42, "system_id": 30000142, "happened_at": 2_000,
                "killmail_time": "1970-01-01T00:33:20Z", "value": 612000000,
                "victim_ship_type_id": 670, "attackers": [], "attacker_count": 0,
            }]
            service = EveMapIntelService(path, now=lambda: 2_100, live_kills=lambda system_id: live if system_id == 30000142 else [])
            service._write_cache({"zkill": {"30000142": {"updated_at": 2_000, "kills": [
                {"killmail_id": 42, "time": "1970-01-01T00:30:00Z", "value": 1, "attackers": [], "attacker_count": 0},
                {"killmail_id": 41, "time": "1970-01-01T00:20:00Z", "value": 2, "attackers": [], "attacker_count": 0},
            ]}}})
            merged = service.get_recent_kills(30000142)
            self.assertEqual([42, 41], [kill["killmail_id"] for kill in merged["kills"]])
            self.assertEqual(612000000, merged["kills"][0]["value"])

    def test_recent_area_kills_uses_one_cached_zkill_area_request(self):
        class Response:
            headers = {"Content-Encoding": ""}

            def read(self):
                return json.dumps([{
                    "killmail_id": 84, "killmail_time": "1970-01-01T00:30:00Z", "solar_system_id": 30000142,
                    "victim": {"ship_type_id": 670}, "attackers": [], "zkb": {"totalValue": 1234567},
                }]).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with tempfile.TemporaryDirectory() as directory:
            service = EveMapIntelService(Path(directory) / "intel.json", now=lambda: 2_000)
            with patch("eve_map_intel_service.urllib.request.urlopen", return_value=Response()) as fetch:
                result = service.get_recent_area_kills("region", 10000002)
                cached = service.get_recent_area_kills("region", 10000002)

        self.assertTrue(result["ok"])
        self.assertEqual([84], [kill["killmail_id"] for kill in result["kills"]])
        self.assertEqual(1234567, result["kills"][0]["value"])
        self.assertEqual("fresh", cached["state"])
        self.assertEqual(1, fetch.call_count)
        self.assertIn("/api/kills/regionID/10000002/", fetch.call_args.args[0].full_url)

    def test_live_kill_attackers_are_available_without_a_zkill_cache_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            live = [{
                "killmail_id": 77, "time": "1970-01-01T00:33:20Z", "attackers": [{
                    "character_id": 9001, "ship_type_id": 587, "final_blow": True, "damage_done": 1234,
                }], "attacker_count": 1,
            }]
            service = EveMapIntelService(
                Path(directory) / "intel.json", now=lambda: 2_100,
                live_kills=lambda system_id: live if system_id == 30000142 else [],
                fetch_names=lambda ids: [
                    {"id": 9001, "name": "Hunter", "category": "character"},
                    {"id": 587, "name": "Rifter", "category": "inventory_type"},
                ],
            )
            detail = service.get_kill_attackers(30000142, 77)
            self.assertTrue(detail["ok"])
            self.assertEqual("Hunter", detail["attackers"][0]["pilot_name"])
            self.assertEqual("Rifter", detail["attackers"][0]["ship_name"])

    def test_hovered_attacker_intel_reuses_the_local_analyser_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            service = EveMapIntelService(
                Path(directory) / "intel.json", now=lambda: 2_100,
                live_kills=lambda _: [{
                    "killmail_id": 77, "attackers": [{"character_id": 9001, "ship_type_id": 587}], "attacker_count": 1,
                }],
                fetch_names=lambda _: [
                    {"id": 9001, "name": "Hunter", "category": "character"},
                    {"id": 587, "name": "Rifter", "category": "inventory_type"},
                ],
            )
            class Analyzer:
                def analyze_identities(self, identities):
                    self.identities = identities
                    return {"state": "ready", "pilots": [{"character_id": 9001, "danger": 81, "snuggly": 19, "band": "dangerous", "zkill_url": "https://zkillboard.com/character/9001/scanalyzer/"}]}
            analyzer = Analyzer()
            with patch("eve_local_analyzer.LocalAnalyzer", return_value=analyzer):
                detail = service.get_kill_attackers_intel(30000142, 77)
            self.assertEqual(analyzer.identities, [(9001, "Hunter")])
            self.assertEqual(detail["attackers"][0]["danger"], 81)
            self.assertEqual(detail["attackers"][0]["zkill_url"], "https://zkillboard.com/character/9001/scanalyzer/")

    def test_hovered_victim_has_ship_identity_and_lazy_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            service = EveMapIntelService(
                Path(directory) / "intel.json", now=lambda: 2_100,
                live_kills=lambda _: [{
                    "killmail_id": 78, "ship_type_id": 670, "value": 612000000,
                    "victim_character_id": 8001, "victim_corporation_id": 9001, "attackers": [],
                }],
                fetch_names=lambda _: [
                    {"id": 8001, "name": "Victim", "category": "character"},
                    {"id": 9001, "name": "Victim Corp", "category": "corporation"},
                    {"id": 670, "name": "Capsule", "category": "inventory_type"},
                ],
            )
            class Analyzer:
                def analyze_identities(self, identities):
                    self.identities = identities
                    return {"pilots": [{"character_id": 8001, "danger": 7, "snuggly": 93, "band": "snuggly", "zkill_url": "https://zkillboard.com/character/8001/scanalyzer/"}]}
            analyzer = Analyzer()
            with patch("eve_local_analyzer.LocalAnalyzer", return_value=analyzer):
                detail = service.get_kill_victim_intel(30000142, 78)
            self.assertEqual(analyzer.identities, [(8001, "Victim")])
            self.assertEqual(detail["victim"]["ship_name"], "Capsule")
            self.assertEqual(detail["victim"]["corporation_name"], "Victim Corp")
            self.assertEqual(detail["victim"]["danger"], 7)


if __name__ == "__main__":
    unittest.main()
