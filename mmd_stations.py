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

Portee BUY (CCP): range 0=region, 1=systeme, 2=constellation(5 sauts),
3=region, 4=region+5 sauts, 5=regionProfondeur(10 sauts).
Note: le SDE leger n'inclut pas les sauts inter-systemes ; les ranges
2/4/5 retombent sur une couverture region-wide (conservateur : on n'exclut
pas a tort). 1 et 0/3 sont exacts.
"""
import os, json

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

# caches
_sys_cache = {}
_region_cache = {}

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
    if location_id in _sys_cache:
        return _sys_cache[location_id]
    loc_id = int(location_id or 0)
    chain = _station_chain(loc_id) if loc_id else None
    if chain:
        res = (chain["solarSystemID"], chain["regionID"], resolve_name(loc_id))
        _sys_cache[location_id] = res
        return res
    # structure du .env
    env = env_stations()
    if loc_id in env:
        res = (None, None, env[loc_id]["name"])
        _sys_cache[location_id] = res
        return res
    res = (None, None, resolve_name(loc_id, str(loc_id)))
    _sys_cache[location_id] = res
    return res

def system_region(system_id):
    if system_id in _region_cache:
        return _region_cache[system_id]
    reg = None
    if _SDE and str(system_id) in _SDE["systems"]:
        reg = _SDE["systems"][str(system_id)].get("r")
    _region_cache[system_id] = reg
    return reg

# jumps inter-systemes absents du SDE leger -> BFS retourne ensemble vide.
# Les ranges 2/4/5 retombent sur region-wide dans covers().
def jumps_bfs(start, max_depth):
    return set()

# ranges CCP -> (type, profondeur_sauts)
# 0=region (toute la region), 1=systeme (0 saut), 2=constellation (<=5 sauts),
# 3=region, 4=region+5 sauts, 5=regionProfondeur (<=10 sauts)
_RANGE_DEPTH = {1: 0, 2: 5, 4: 5, 5: 10}

def covers(pub_order_location, pub_order_range, target_system, target_region):
    """True si un ordre public (location+range) couvre la station cible.
    - target_system/target_region: resolution de MA station
    - pub_order_range: range CCP (0..5) OU string ESI
      ("station","solar_system","region","constellation","region_boundary_1..5")
    """
    if target_region is None or target_system is None:
        return False
    pub_sys, pub_reg, _ = resolve(pub_order_location)
    r = pub_order_range
    if isinstance(r, str):
        if r in ("region", "constellation",
                 "region_boundary_1", "region_boundary_2", "region_boundary_3",
                 "region_boundary_4", "region_boundary_5"):
            return pub_reg == target_region
        if r in ("solar_system", "station"):
            return pub_sys == target_system
        return pub_reg == target_region
    # entier CCP: 0/3=region, 1=systeme, 2/4/5=a sauts (sauts absents -> region-wide)
    if r in (0, 3):
        return pub_reg == target_region
    if r == 1:
        return pub_sys == target_system
    # ranges avec sauts : SDE leger n'a pas les sauts -> on reste region-wide
    return pub_reg == target_region
