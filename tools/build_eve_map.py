"""Build the compact offline New Eden map used by the desktop UI."""
import argparse
import json
import math
import os
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

LATEST_SDE_MANIFEST_URL = "https://developers.eveonline.com/static-data/tranquility/latest.jsonl"


def _rows(bundle, suffix):
    name = next((item for item in bundle.namelist() if item.lower().endswith(suffix.lower())), None)
    if not name:
        return []
    with bundle.open(name) as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _first(row, *names, default=None):
    for name in names:
        if name in row and row[name] is not None:
            value = row[name]
            if name == "name" and isinstance(value, dict):
                return value.get("en") or next(iter(value.values()), default)
            return value
    return default


def build_dataset(archive_path, output_path, source_url="official CCP SDE"):
    """Convert CCP JSONL SDE data to a browser-friendly system/gate graph."""
    archive_path, output_path = Path(archive_path), Path(output_path)
    with zipfile.ZipFile(archive_path) as bundle:
        regions = {int(_first(row, "region_id", "regionID", "_key")): row for row in _rows(bundle, "regions.jsonl")}
        constellations = {int(_first(row, "constellation_id", "constellationID", "_key")): row for row in _rows(bundle, "constellations.jsonl")}
        raw_systems = _rows(bundle, "solarsystems.jsonl") or _rows(bundle, "systems.jsonl")
        raw_gates = _rows(bundle, "stargates.jsonl")

    systems, positions, gate_systems = [], {}, {}
    for row in raw_systems:
        system_id = _first(row, "solar_system_id", "solarSystemID", "id", "_key")
        position = _first(row, "position", default={}) or {}
        if system_id is None or not {"x", "y", "z"}.issubset(position):
            continue
        system_id = int(system_id)
        point = {axis: float(position[axis]) for axis in ("x", "y", "z")}
        constellation_id = _first(row, "constellation_id", "constellationID")
        constellation = constellations.get(int(constellation_id)) if constellation_id is not None else None
        region_id = _first(row, "region_id", "regionID", default=_first(constellation or {}, "region_id", "regionID"))
        region = regions.get(int(region_id)) if region_id is not None else None
        faction_id = _first(row, "faction_id", "factionID", default=_first(constellation or {}, "faction_id", "factionID", default=_first(region or {}, "faction_id", "factionID")))
        systems.append({"id": system_id, "name": _first(row, "name", "solarSystemName", default=str(system_id)), "security": float(_first(row, "security_status", "securityStatus", default=0)), "faction_id": int(faction_id) if faction_id is not None else None, "region_id": region_id, "region": _first(region or {}, "name", default="Unknown"), "constellation_id": constellation_id, "constellation": _first(constellation or {}, "name", default="Unknown"), "position_m": point})
        positions[system_id] = point
        for gate_id in _first(row, "stargates", "stargate_ids", "stargateIDs", default=[]) or []:
            gate_systems[int(gate_id)] = system_id

    max_abs = max((abs(value) for point in positions.values() for value in point.values()), default=1.0)
    for system in systems:
        system["position"] = {axis: round(value / max_abs * 500, 6) for axis, value in system["position_m"].items()}

    for row in raw_gates:
        gate_id = _first(row, "stargate_id", "stargateID", "id", "_key")
        source = _first(row, "solar_system_id", "solarSystemID", default=gate_systems.get(int(gate_id)) if gate_id is not None else None)
        if gate_id is not None and source is not None:
            gate_systems[int(gate_id)] = int(source)

    seen, gates = set(), []
    for row in raw_gates:
        gate_id = _first(row, "stargate_id", "stargateID", "id", "_key")
        destination_gate = _first(row, "destination_stargate_id", "destinationStargateID", default=(row.get("destination") or {}).get("stargateID"))
        source = gate_systems.get(int(gate_id)) if gate_id is not None else None
        target = gate_systems.get(int(destination_gate)) if destination_gate is not None else None
        if source is None or target is None or source == target:
            continue
        key = tuple(sorted((source, target)))
        if key not in seen:
            seen.add(key)
            gates.append({"source": key[0], "target": key[1]})

    dataset = {"meta": {"schema_version": 1, "source": source_url, "generated_at": datetime.now(timezone.utc).isoformat()}, "systems": sorted(systems, key=lambda system: system["id"]), "gates": gates}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(dataset, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, output_path)
    return {"systems": len(systems), "gates": len(gates), "output": str(output_path)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path, default=Path("gui/data/eve_map.json"))
    parser.add_argument("--url", help="Explicit CCP JSONL SDE archive URL")
    args = parser.parse_args()
    archive = args.archive
    source_url = args.url
    if archive is None:
        if source_url is None:
            manifest_request = urllib.request.Request(LATEST_SDE_MANIFEST_URL, headers={"User-Agent": "EVE-Market-Manager/1.0"})
            with urllib.request.urlopen(manifest_request) as response:
                build_number = json.loads(response.read().decode("utf-8"))["buildNumber"]
            source_url = "https://developers.eveonline.com/static-data/tranquility/eve-online-static-data-{}-jsonl.zip".format(build_number)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as downloaded:
            archive = Path(downloaded.name)
        request = urllib.request.Request(source_url, headers={"User-Agent": "EVE-Market-Manager/1.0"})
        with urllib.request.urlopen(request) as response, archive.open("wb") as destination:
            destination.write(response.read())
    try:
        print(json.dumps(build_dataset(archive, args.output, source_url or "local archive")))
    finally:
        if args.archive is None:
            archive.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
