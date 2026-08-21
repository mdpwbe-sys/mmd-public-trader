#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_stations.py - resolution location_id -> (solarSystemID, regionID, name)
+ graphe de sauts SDE pour le moteur de portee des BUY orders.

Deux sources:
  - staStations (stations NPC) -> solarSystemID direct
  - .env: Trading_Upwell_ID / Sell_Station_ID -> on mappe l'ID de structure
    vers le systeme grace au nom (ex: 'Perimeter' -> mapSolarSystems)
  - fallback: si inconnu, on garde l'ID brut (nom = str(id))

Portee BUY (CCP): range 0=region, 1=systeme, 2=constellation(5 sauts),
3=region, 4=region+5 sauts, 5=regionProfondeur(10 sauts).
"""
import os, sqlite3, json

LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
EVE_DB = os.path.join(LOCAL, "mmd.com", "Mmd", "resources", "eve.db")
HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, ".env")

# cache resolution
_sys_cache = {}
_region_cache = {}
_jumps = None  # dict fromSystem -> set(toSystem)


def _load_env_stations():
    """Mappe les structures du .env vers (location_id, systeme, nom)."""
    out = {}
    if not os.path.exists(ENV):
        return out
    cfg = {}
    for line in open(ENV, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip()
    import sqlite3 as sq
    c = sq.connect(EVE_DB)
    # Trading_Upwell_ID=35833//1044752365771  + Trading_Upwell_NAME=Perimeter ...
    if "Trading_Upwell_ID" in cfg and "Trading_Upwell_NAME" in cfg:
        loc = int(cfg["Trading_Upwell_ID"].split("//")[-1])
        name = cfg["Trading_Upwell_NAME"]
        sysid = _system_from_name(c, name)
        out[loc] = {"solarSystemID": sysid, "name": name}
    if "Sell_Station_ID" in cfg and "Sell_Station_NAME" in cfg:
        loc = int(cfg["Sell_Station_ID"].split("//")[-1])
        name = cfg["Sell_Station_NAME"]
        sysid = _system_from_name(c, name)
        out[loc] = {"solarSystemID": sysid, "name": name}
    c.close()
    return out


def _system_from_name(c, name):
    """Trouve le solarSystemID a partir du nom de station (ex: 'Perimeter')."""
    # prend le 1er mot significatif du nom de la structure
    token = name.split()[0].strip("-,.")
    try:
        r = c.execute("SELECT solarSystemID FROM mapSolarSystems WHERE solarSystemName LIKE ?",
                      (f"%{token}%",)).fetchone()
        return r[0] if r else None
    except Exception:
        return None


_ENV_STATIONS = None


def _env_stations():
    global _ENV_STATIONS
    if _ENV_STATIONS is None:
        _ENV_STATIONS = _load_env_stations()
    return _ENV_STATIONS


def resolve_name(location_id, fallback=""):
    """Retourne le nom lisible de la station (station NPC ou Upwell)."""
    loc_id = int(location_id or 0)
    if not loc_id:
        return fallback or "Inconnu"
    if loc_id == 60003760:
        return "Jita IV - Moon 4 - Caldari Navy Assembly Plant"
    if loc_id in (1044752365771, 35833):
        return "Perimeter - Tranquility Trading Tower"
    try:
        c = sqlite3.connect(EVE_DB)
        r = c.execute("SELECT stationName FROM staStations WHERE stationID=?", (loc_id,)).fetchone()
        c.close()
        if r and r[0]:
            return r[0]
    except Exception:
        pass
    env = _env_stations()
    if loc_id in env and env[loc_id].get("name"):
        return env[loc_id]["name"]
    return fallback or str(loc_id)


def resolve(location_id):
    """Retourne (solarSystemID, regionID, name) pour un location_id.
    None si inconnu."""
    if location_id in _sys_cache:
        return _sys_cache[location_id]
    c = sqlite3.connect(EVE_DB)
    # 1. station NPC
    try:
        r = c.execute("SELECT solarSystemID, stationName FROM staStations WHERE stationID=?",
                      (location_id,)).fetchone()
        if r and r[0]:
            s = r[0]
            st_name = r[1] if (len(r) > 1 and r[1]) else None
            r2 = c.execute("SELECT regionID, solarSystemName FROM mapSolarSystems WHERE solarSystemID=?",
                           (s,)).fetchone()
            name = st_name or (r2[1] if r2 else str(location_id))
            res = (s, r2[0] if r2 else None, name)
            _sys_cache[location_id] = res
            c.close()
            return res
    except Exception:
        pass
    # 2. structure du .env
    env = _env_stations()
    if location_id in env:
        sysid = env[location_id]["solarSystemID"]
        region = None
        if sysid:
            r3 = c.execute("SELECT regionID FROM mapSolarSystems WHERE solarSystemID=?",
                           (sysid,)).fetchone()
            region = r3[0] if r3 else None
        res = (sysid, region, env[location_id]["name"])
        _sys_cache[location_id] = res
        c.close()
        return res
    c.close()
    _sys_cache[location_id] = (None, None, resolve_name(location_id, str(location_id)))
    return _sys_cache[location_id]


def _load_jumps():
    global _jumps
    if _jumps is not None:
        return
    c = sqlite3.connect(EVE_DB)
    _jumps = {}
    for frm, to in c.execute("SELECT fromSolarSystemID, toSolarSystemID FROM mapSolarSystemJumps"):
        _jumps.setdefault(frm, set()).add(to)
    c.close()


def system_region(system_id):
    if system_id in _region_cache:
        return _region_cache[system_id]
    c = sqlite3.connect(EVE_DB)
    r = c.execute("SELECT regionID FROM mapSolarSystems WHERE solarSystemID=?",
                  (system_id,)).fetchone()
    c.close()
    reg = r[0] if r else None
    _region_cache[system_id] = reg
    return reg


def jumps_bfs(start, max_depth):
    """Ensemble des systemes a <= max_depth sauts de start (BFS)."""
    if start is None:
        return set()
    _load_jumps()
    seen = {start}
    frontier = {start}
    for _ in range(max_depth):
        nxt = set()
        for s in frontier:
            for t in _jumps.get(s, ()):
                if t not in seen:
                    seen.add(t)
                    nxt.add(t)
        frontier = nxt
    return seen


# ranges CCP -> (type, profondeur_sauts)
# 0=region (toute la region), 1=systeme (0 saut), 2=constellation (<=5 sauts),
# 3=region, 4=region+5 sauts, 5=regionProfondeur (<=10 sauts)
_RANGE_DEPTH = {1: 0, 2: 5, 4: 5, 5: 10}


def covers(pub_order_location, pub_order_range, target_system, target_region):
    """True si un ordre public (location+range) couvre la station cible.
    - target_system/target_region: resolution de MA station
    - pub_order_range: range de l'ordre public (0..5 entier CCP OU string ESI:
      "station","solar_system","region","constellation","region_boundary_1..5")
    """
    if target_region is None or target_system is None:
        return False
    pub_sys, pub_reg, _ = resolve(pub_order_location)
    # normalise range (string ESI ou entier CCP)
    r = pub_order_range
    if isinstance(r, str):
        # region-wide (couvre toute la region cible si meme region)
        if r in ("region", "constellation",
                 "region_boundary_1", "region_boundary_2", "region_boundary_3",
                 "region_boundary_4", "region_boundary_5"):
            return pub_reg == target_region
        # "solar_system"/"station" -> meme systeme (approx station-dans-systeme)
        if r in ("solar_system", "station"):
            return pub_sys == target_system
        # inconnu -> region-wide par defaut (ne pas exclure a tort)
        return pub_reg == target_region
    # entier CCP: 0/3=region, 1=systeme, 2/4/5=a sauts
    if r in (0, 3):
        return pub_reg == target_region
    if r == 1:
        return pub_sys == target_system
    if pub_reg != target_region:
        return False
    depth = _RANGE_DEPTH.get(r, 0)
    reached = jumps_bfs(pub_sys, depth)
    return target_system in reached
