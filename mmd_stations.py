#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_stations.py - resolution location_id -> (solarSystemID, regionID, name)
+ graphe de portee pour le moteur de BUY orders.

Source: un SDE leger EMBARQUE (reference/sde/sde_light.json, ~0.6 MB), genere
a partir du dump statique CCP (npcStations / mapSolarSystems / mapConstellations).
Plus besoin de eve.db (239 Mo) ni d'appel ESI : tout est resolvable hors-ligne.

Chaines de resolution:
  - station NPC -> solarSystemID (sde_light.stations)
  - solarSystemID -> constellationID, regionID, name (sde_light.systems)
  - constellationID -> factionID (sde_light.constellations)
  - citadelle Upwell (id >= 1e12) : pas de faction SDE -> ID brut affiche
  - fallback: si inconnu, on garde l'ID brut (nom = str(id))

La portee BUY est normalisee ici, une seule fois.  Une portee absente ou
inconnue est volontairement rejetee: elle ne devient jamais regionale.
La topologie des sauts vient du dataset de carte deja embarque; aucun appel
reseau n'est necessaire.
"""
import os, json, logging

HERE = os.path.dirname(os.path.abspath(__file__))
# sde_light.json embarque via --add-data (build_exe.py) et present a cote du .py
_SDE_PATHS = [
    os.path.join(HERE, "reference", "sde", "sde_light.json"),
    os.path.join(HERE, "sde_light.json"),
]

def _load_sde():
    for p in _SDE_PATHS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return None

_SDE = _load_sde()
_LOG = logging.getLogger(__name__)
_INVALID_BUY_RANGES_LOGGED = set()

# caches
_sys_cache = {}
_region_cache = {}
_runtime_structures = {}


def _invalid_buy_range(value):
    """Log once and keep an unknown BUY range outside the candidate universe."""
    marker = repr(value)
    if marker not in _INVALID_BUY_RANGES_LOGGED:
        _INVALID_BUY_RANGES_LOGGED.add(marker)
        _LOG.warning("BUY range invalid or missing; ignoring order conservatively: %r", value)
    return None


def normalize_buy_range(value):
    """Return (kind, depth) for the ESI/MMD BUY-range forms, or None.

    STATION and SYSTEM deliberately stay different.  Integers and numeric
    strings denote their literal jump radius; no legacy numeric value may
    silently widen an order to the entire region.
    """
    if value is None or isinstance(value, bool):
        return _invalid_buy_range(value)
    if isinstance(value, int):
        return ("JUMPS", value) if value >= 0 else _invalid_buy_range(value)
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "station": "STATION",
            "solar_system": "SYSTEM",
            "solarsystem": "SYSTEM",
            "system": "SYSTEM",
            "region": "REGION",
        }
        if normalized in aliases:
            return (aliases[normalized], None)
        try:
            depth = int(normalized)
        except ValueError:
            return _invalid_buy_range(value)
        return ("JUMPS", depth) if depth >= 0 else _invalid_buy_range(value)
    return _invalid_buy_range(value)

def _env_stations():
    """Stations perso depuis .env (Trading_Upwell_ID / Sell_Station_ID)."""
    out = {}
    env_path = os.path.join(HERE, ".env")
    if not os.path.exists(env_path):
        return out
    cfg = {}
    try:
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    except Exception:
        return out
    for key in ("Trading_Upwell_ID", "Sell_Station_ID"):
        if key in cfg and cfg.get(key + "_NAME"):
            try:
                loc = int(cfg[key].split("//")[-1])
                out[loc] = {"name": cfg[key + "_NAME"]}
            except Exception:
                pass
    return out

_ENV_STATIONS = None
def env_stations():
    global _ENV_STATIONS
    if _ENV_STATIONS is None:
        _ENV_STATIONS = _env_stations()
    return _ENV_STATIONS

def _configured_trading_upwell_id():
    """Return the configured buy Upwell ID without baking a structure into code."""
    try:
        with open(os.path.join(HERE, ".env"), encoding="utf-8") as env_file:
            for line in env_file:
                if line.strip().startswith("Trading_Upwell_ID="):
                    return int(line.split("=", 1)[1].strip().split("//")[-1])
    except Exception:
        pass
    return None

def buy_hub_priority(location_id):
    """BUY tie-break hub priority: Jita before the configured Perimeter Upwell."""
    try:
        location_id = int(location_id)
    except (TypeError, ValueError):
        return 0
    if location_id == 60003760:
        return 2
    return 1 if location_id == _configured_trading_upwell_id() else 0

def _station_chain(station_id):
    """Retourne {solarSystemID, regionID, constellationID, factionID, systemName}
    via le SDE leger, ou None si station inconnue du SDE.
    Note: sde_light stocke tous les ID en string (JSON)."""
    if not _SDE:
        return None
    sysid = _SDE["stations"].get(str(station_id))
    if sysid is None:
        return None
    s = _SDE["systems"].get(str(sysid))
    if not s:
        return None
    cid = s.get("c")
    fac = None
    if str(cid) in _SDE["constellations"]:
        fac = _SDE["constellations"][str(cid)].get("f")
    return {
        "solarSystemID": sysid,
        "regionID": s.get("r"),
        "constellationID": cid,
        "factionID": fac,
        "systemName": s.get("n"),
    }

def resolve_name(location_id, fallback=""):
    """Nom lisible de la station (NPC ou Upwell)."""
    loc_id = int(location_id or 0)
    if not loc_id:
        return fallback or "Inconnu"
    runtime = _runtime_structures.get(loc_id)
    if runtime and runtime[2]:
        return runtime[2]
    chain = _station_chain(loc_id)
    if chain and chain.get("systemName"):
        return f"{chain['systemName']} ({loc_id})"
    env = env_stations()
    if loc_id in env and env[loc_id].get("name"):
        return env[loc_id]["name"]
    return fallback or str(loc_id)

def faction_for_station(station_id):
    """factionID proprietaire de la station via SDE (station->system->constellation->faction).
    None si inconnu (citadelle non-resolue, ou SDE absent)."""
    loc_id = int(station_id or 0)
    if not loc_id:
        return None
    if loc_id >= 1000000000000:   # citadelle Upwell : pas de faction SDE directe
        return None
    chain = _station_chain(loc_id)
    return chain["factionID"] if chain else None

def resolve(location_id):
    """Retourne (solarSystemID, regionID, name) pour un location_id."""
    try:
        loc_id = int(location_id or 0)
    except (TypeError, ValueError):
        return None, None, resolve_name(location_id, str(location_id))
    if loc_id in _runtime_structures:
        return _runtime_structures[loc_id]
    if loc_id in _sys_cache:
        return _sys_cache[loc_id]
    chain = _station_chain(loc_id) if loc_id else None
    if chain:
        res = (chain["solarSystemID"], chain["regionID"], resolve_name(loc_id))
        _sys_cache[loc_id] = res
        return res
    # structure du .env
    env = env_stations()
    if loc_id in env:
        res = (None, None, env[loc_id]["name"])
        _sys_cache[loc_id] = res
        return res
    res = (None, None, resolve_name(loc_id, str(loc_id)))
    _sys_cache[loc_id] = res
    return res

def system_region(system_id):
    if system_id in _region_cache:
        return _region_cache[system_id]
    reg = None
    if _SDE and str(system_id) in _SDE["systems"]:
        reg = _SDE["systems"][str(system_id)].get("r")
    _region_cache[system_id] = reg
    return reg

def _region_for_system(system_id):
    return system_region(system_id)

def register_structure(structure_id, solar_system_id, *, name=""):
    """Cache an accessible Upwell structure with its authoritative ESI system."""
    try:
        structure_id, solar_system_id = int(structure_id), int(solar_system_id)
    except (TypeError, ValueError):
        return None
    if structure_id <= 0 or solar_system_id <= 0:
        return None
    region_id = _region_for_system(solar_system_id)
    if region_id is None:
        return None
    resolved = (solar_system_id, region_id, str(name or structure_id))
    _runtime_structures[structure_id] = resolved
    _sys_cache[structure_id] = resolved
    return resolved

_MAP_PATHS = [
    os.path.join(HERE, "gui", "data", "eve_map.json"),
    os.path.join(HERE, "eve_map.json"),
]
_JUMP_GRAPH = None


def _jump_graph():
    """Load the already-packaged New Eden gate topology once, offline."""
    global _JUMP_GRAPH
    if _JUMP_GRAPH is not None:
        return _JUMP_GRAPH
    graph = {}
    for path in _MAP_PATHS:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as source:
                gates = json.load(source).get("gates", [])
            for gate in gates:
                left, right = int(gate["source"]), int(gate["target"])
                graph.setdefault(left, set()).add(right)
                graph.setdefault(right, set()).add(left)
            break
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _LOG.warning("Unable to load offline stargate topology: %s", exc)
    _JUMP_GRAPH = graph
    return graph


def jumps_bfs(start, max_depth):
    """Systems reachable from *start* in at most ``max_depth`` stargate jumps."""
    try:
        start, max_depth = int(start), int(max_depth)
    except (TypeError, ValueError):
        return set()
    if max_depth < 0:
        return set()
    seen, frontier = {start}, {start}
    graph = _jump_graph()
    for _ in range(max_depth):
        frontier = {neighbor for node in frontier for neighbor in graph.get(node, ())
                    if neighbor not in seen}
        if not frontier:
            break
        seen.update(frontier)
    return seen


def covers(pub_order_location, pub_order_range, target_system, target_region,
           target_location=None):
    """Whether one BUY order reaches one exact sale location.

    ``STATION`` only covers the matching location ID.  ``SYSTEM`` and
    ``REGION`` compare their corresponding SDE IDs; jump ranges use the local
    stargate graph.  Invalid ranges are conservative and never become region.
    """
    normalized = normalize_buy_range(pub_order_range)
    if normalized is None or target_system is None or target_region is None:
        return False
    pub_sys, pub_reg, _ = resolve(pub_order_location)
    if pub_sys is None or pub_reg is None:
        return False
    kind, depth = normalized
    if kind == "STATION":
        try:
            return target_location is not None and int(pub_order_location) == int(target_location)
        except (TypeError, ValueError):
            return False
    if kind == "SYSTEM":
        return int(pub_sys) == int(target_system)
    if kind == "REGION":
        return int(pub_reg) == int(target_region)
    return int(target_system) in jumps_bfs(pub_sys, depth)


def _order_location_id(order):
    return order.get("location_id", order.get("station_id"))


def _covered_systems(location_id, normalized_range, system_id):
    kind, depth = normalized_range
    if kind == "SYSTEM":
        return {int(system_id)}
    if kind == "JUMPS":
        return jumps_bfs(system_id, depth)
    return set()


def buy_ranges_overlap(my_order, competitor):
    """Whether two BUY orders can reach at least one common sale location."""
    mine_location = _order_location_id(my_order)
    competitor_location = _order_location_id(competitor)
    mine_range = normalize_buy_range(my_order.get("range"))
    competitor_range = normalize_buy_range(competitor.get("range"))
    if mine_location is None or competitor_location is None:
        return False
    if mine_range is None or competitor_range is None:
        return False
    mine_system, mine_region, _ = resolve(mine_location)
    competitor_system, competitor_region, _ = resolve(competitor_location)
    if None in (mine_system, mine_region, competitor_system, competitor_region):
        return False
    if mine_range[0] == "REGION" or competitor_range[0] == "REGION":
        return int(mine_region) == int(competitor_region)
    if mine_range[0] == "STATION" or competitor_range[0] == "STATION":
        return (
            covers(mine_location, my_order.get("range"), competitor_system,
                   competitor_region, competitor_location) or
            covers(competitor_location, competitor.get("range"), mine_system,
                   mine_region, mine_location)
        )
    return bool(_covered_systems(mine_location, mine_range, mine_system) &
                _covered_systems(competitor_location, competitor_range,
                                 competitor_system))
