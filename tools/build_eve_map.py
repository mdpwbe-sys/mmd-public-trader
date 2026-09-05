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


def _entry_name(bundle, suffix):
    suffix = suffix.lower()
    return next((item for item in bundle.namelist() if Path(item).name.lower() == suffix), next((item for item in bundle.namelist() if item.lower().endswith(suffix)), None))


def _rows(bundle, suffix):
    name = _entry_name(bundle, suffix)
    if not name:
        return []
    with bundle.open(name) as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _iter_rows(bundle, suffix):
    """Stream a JSONL table when only a small subset of it is needed."""
    name = _entry_name(bundle, suffix)
    if not name:
        return
    with bundle.open(name) as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _localized(value, default=None):
    if isinstance(value, dict):
        return value.get("en") or next(iter(value.values()), default)
    return value if value is not None else default


def _first(row, *names, default=None):
    for name in names:
        if name in row and row[name] is not None:
            value = row[name]
            return _localized(value, default) if name == "name" or name.endswith("Name") or name.endswith("_name") else value
    return default


def _row_id(row):
    return _first(row, "id", "_key")


def _roman(value):
    numerals = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    value, output = max(1, int(value or 1)), []
    for amount, symbol in numerals:
        count, value = divmod(value, amount)
        output.append(symbol * count)
    return "".join(output)


def _planet_type_name(value):
    """Keep only the PI-relevant part of an SDE planet type name."""
    name = str(value or "Unknown")
    for prefix in ("Planet (", "Planet - "):
        if name.startswith(prefix) and name.endswith(")"):
            return name[len(prefix):-1]
    return name.replace(" Planet", "").strip()


def build_dataset(archive_path, output_path, source_url="official CCP SDE"):
    """Convert CCP JSONL SDE data to a browser-friendly system/gate graph."""
    archive_path, output_path = Path(archive_path), Path(output_path)
    with zipfile.ZipFile(archive_path) as bundle:
        regions = {int(_first(row, "region_id", "regionID", "_key")): row for row in _rows(bundle, "regions.jsonl")}
        constellations = {int(_first(row, "constellation_id", "constellationID", "_key")): row for row in _rows(bundle, "constellations.jsonl")}
        raw_systems = _rows(bundle, "solarsystems.jsonl") or _rows(bundle, "systems.jsonl")
        raw_gates = _rows(bundle, "stargates.jsonl")
        raw_planets = _rows(bundle, "mapplanets.jsonl")
        raw_belts = _rows(bundle, "mapasteroidbelts.jsonl")
        raw_stations = _rows(bundle, "npcstations.jsonl")
        operations = {int(_row_id(row)): row for row in _rows(bundle, "stationoperations.jsonl") if _row_id(row) is not None}
        service_names = {int(_row_id(row)): _first(row, "service_name", "serviceName", "name") for row in _rows(bundle, "stationservices.jsonl") if _row_id(row) is not None}
        corporation_names = {int(_row_id(row)): _first(row, "name") for row in _rows(bundle, "npccorporations.jsonl") if _row_id(row) is not None}
        needed_type_ids = {
            int(type_id)
            for row in [*raw_planets, *raw_belts, *raw_stations]
            if (type_id := _first(row, "type_id", "typeID")) is not None
        }
        type_names = {
            int(type_id): _first(row, "name")
            for row in _iter_rows(bundle, "types.jsonl")
            if (type_id := _row_id(row)) is not None and int(type_id) in needed_type_ids
        }

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

    systems_by_id = {system["id"]: system for system in systems}
    belts_by_id = {int(_row_id(row)): row for row in raw_belts if _row_id(row) is not None}
    planets_by_system = {}
    for row in raw_planets:
        system_id = _first(row, "solar_system_id", "solarSystemID")
        planet_id = _row_id(row)
        if system_id is None or planet_id is None or int(system_id) not in systems_by_id:
            continue
        system = systems_by_id[int(system_id)]
        index = _first(row, "celestial_index", "celestialIndex", default=len(planets_by_system.get(int(system_id), [])) + 1)
        type_id = _first(row, "type_id", "typeID")
        moon_ids = _first(row, "moon_ids", "moonIDs", default=[]) or []
        belt_ids = _first(row, "asteroid_belt_ids", "asteroidBeltIDs", default=[]) or []
        planet_name = _first(row, "name", "planetName", default=f"{system['name']} {_roman(index)}")
        belts = []
        for belt_index, belt_id in enumerate(belt_ids, start=1):
            belt = belts_by_id.get(int(belt_id), {})
            belt_type_id = _first(belt, "type_id", "typeID")
            belts.append({
                "id": int(belt_id),
                "name": _first(belt, "name", "asteroidBeltName", default=f"{planet_name} - Asteroid Belt {belt_index}"),
                "type_id": int(belt_type_id) if belt_type_id is not None else None,
            })
        planets_by_system.setdefault(int(system_id), []).append({
            "id": int(planet_id), "name": planet_name,
            "type_id": int(type_id) if type_id is not None else None,
            "type_name": _planet_type_name(type_names.get(int(type_id))) if type_id is not None else "Unknown",
            "moon_count": len(moon_ids), "belts": belts,
        })

    stations_by_system = {}
    for row in raw_stations:
        system_id, station_id = _first(row, "solar_system_id", "solarSystemID"), _row_id(row)
        if system_id is None or station_id is None or int(system_id) not in systems_by_id:
            continue
        owner_id, type_id = _first(row, "owner_id", "ownerID"), _first(row, "type_id", "typeID")
        operation = operations.get(int(_first(row, "operation_id", "operationID", default=-1)), {})
        service_ids = _first(row, "services", default=_first(operation, "services", default=[])) or []
        services = [service_names[int(service_id)] for service_id in service_ids if int(service_id) in service_names]
        system = systems_by_id[int(system_id)]
        owner_name = corporation_names.get(int(owner_id)) if owner_id is not None else None
        type_name = type_names.get(int(type_id)) if type_id is not None else None
        stations_by_system.setdefault(int(system_id), []).append({
            "id": int(station_id),
            "name": _first(row, "name", "stationName", default=f"{system['name']} - {owner_name or type_name or 'NPC Station'}"),
            "owner_id": int(owner_id) if owner_id is not None else None,
            "owner_name": owner_name,
            "services": sorted(set(filter(None, services))),
        })

    for system in systems:
        planets = planets_by_system.get(system["id"], [])
        belts = [belt for planet in planets for belt in planet.pop("belts")]
        stations = stations_by_system.get(system["id"], [])
        system.update({
            "planet_count": len(planets), "planets": planets,
            "moon_count": sum(planet["moon_count"] for planet in planets),
            "belt_count": len(belts), "belts": belts,
            "npc_station_count": len(stations), "npc_stations": stations,
        })

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

    dataset = {"meta": {"schema_version": 2, "source": source_url, "generated_at": datetime.now(timezone.utc).isoformat()}, "systems": sorted(systems, key=lambda system: system["id"]), "gates": gates}
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
