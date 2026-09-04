"""Cached public tactical intel for the New Eden map.

Map topology is deliberately kept separate from this optional live layer: a
network failure must never make the static map or routing unavailable.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import gzip
import urllib.parse
import urllib.request
from pathlib import Path

from eve_map_runtime import esi, esi_auth, map_cache_path, sso


ESI_BASE = "https://esi.evetech.net/latest"
LIVE_TTL_SECONDS = 600
SOVEREIGNTY_TTL_SECONDS = 15 * 60
STALE_TTL_SECONDS = 24 * 60 * 60
ZKILL_TTL_SECONDS = 10 * 60
ZKILL_AREA_TTL_SECONDS = 10 * 60
ENTITY_NAME_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_KILL_ATTACKERS = 20
ZKILL_USER_AGENT = os.environ.get(
    "MMD_MAP_USER_AGENT", "EveMarketManager/1.0 (https://github.com/mdpwbe-sys/mmd-public-trader)"
)


def default_cache_path() -> Path:
    return map_cache_path()


def _stream_kills(system_id: int) -> list[dict]:
    """Read the in-memory R2Z2 window; no network request is made here."""
    try:
        import eve_map_kill_stream
        return eve_map_kill_stream.get_recent_kills(system_id)
    except Exception:
        return []


def _merge_kills(live_kills: list[dict], historical_kills: list[dict], limit: int = 5) -> list[dict]:
    """Prefer richer live rows while retaining lazy zKill history as a fallback."""
    merged = {}
    for row in historical_kills or []:
        killmail_id = row.get("killmail_id")
        if killmail_id is not None:
            merged[int(killmail_id)] = row
    for row in live_kills or []:
        killmail_id = row.get("killmail_id")
        if killmail_id is not None:
            merged[int(killmail_id)] = {**merged.get(int(killmail_id), {}), **row}
    return sorted(merged.values(), key=lambda row: row.get("time") or "", reverse=True)[:max(1, int(limit))]


def _normalise_zkill_kill(row: dict, fallback_system_id: int | None = None) -> dict:
    """Keep the compact kill format shared by system and area history."""
    attackers = [{
        key: attacker.get(key) for key in ("character_id", "corporation_id", "alliance_id", "ship_type_id", "final_blow", "damage_done")
        if attacker.get(key) is not None
    } for attacker in row.get("attackers", []) if isinstance(attacker, dict)]
    attackers.sort(key=lambda attacker: (not bool(attacker.get("final_blow")), -int(attacker.get("damage_done", 0))))
    killmail_id = int(row.get("killmail_id", 0) or 0)
    return {
        "killmail_id": killmail_id, "time": row.get("killmail_time"), "value": row.get("zkb", {}).get("totalValue", 0),
        "solar_system_id": int(row.get("solar_system_id") or fallback_system_id or 0), "ship_type_id": row.get("victim", {}).get("ship_type_id"),
        "url": f"https://zkillboard.com/kill/{killmail_id}/", "victim_character_id": row.get("victim", {}).get("character_id"),
        "victim_corporation_id": row.get("victim", {}).get("corporation_id"), "victim_alliance_id": row.get("victim", {}).get("alliance_id"),
        "attackers": attackers[:MAX_KILL_ATTACKERS], "attacker_count": len(attackers),
    }


def danger_score(ship_kills: int, pod_kills: int) -> int:
    """A bounded logarithmic score; pod losses carry a deliberately higher weight."""
    raw = max(0, int(ship_kills)) + max(0, int(pod_kills)) * 2.5
    return round(min(100, math.log1p(raw) / math.log1p(50) * 100))


def danger_band(score: int) -> str:
    if score >= 75:
        return "red"
    if score >= 50:
        return "orange"
    if score >= 20:
        return "yellow"
    return "normal"


class EveMapIntelService:
    def __init__(self, cache_path: Path | None = None, now=time.time, fetch_json=None, fetch_names=None, live_kills=None):
        self.cache_path = Path(cache_path or default_cache_path())
        self.now = now
        self.fetch_json = fetch_json or self._fetch_esi
        self.fetch_names = fetch_names or self._fetch_entity_names
        self.live_kills = live_kills or _stream_kills
        self._lock = threading.RLock()
        self._character_positions = None
        self._character_positions_at = 0

    def _read_cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_cache(self, payload: dict) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            temporary.replace(self.cache_path)
        except OSError:
            pass

    def _fetch_esi(self, endpoint: str):
        # Reuse the application's public ESI transport (ETag, CCP error-limit
        # handling, retry/backoff and its own stale snapshot behaviour).
        if endpoint == "/sovereignty/map/":
            # This newer public route returns 404 when the app-wide historical
            # X-Compatibility-Date is sent.  This service owns a 15-minute
            # cache, so a direct current-route read cannot become a polling bypass.
            request = urllib.request.Request(
                f"{ESI_BASE}{endpoint}",
                headers={"User-Agent": "EveMarketManager/1.0 (public sovereignty map)", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))
        esi._load_cache()
        data, state = esi._get(f"{ESI_BASE}{endpoint}", timeout=8, include_cache_state=True)
        if data is None:
            raise RuntimeError(f"ESI {state}")
        return data

    @staticmethod
    def _fetch_entity_names(ids: list[int]) -> list[dict]:
        """Resolve a small, user-selected set of public EVE entity IDs."""
        request = urllib.request.Request(
            f"{ESI_BASE}/universe/names/",
            data=json.dumps(ids).encode("utf-8"),
            method="POST",
            headers={
                "User-Agent": "EveMarketManager/1.0 (map entity names)",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _normalise(jumps: list, kills: list) -> dict:
        systems = {}
        for row in jumps:
            system_id = int(row.get("system_id", 0))
            if system_id:
                systems[system_id] = {"ship_jumps": max(0, int(row.get("ship_jumps", 0))), "ship_kills": 0, "pod_kills": 0, "npc_kills": 0}
        for row in kills:
            system_id = int(row.get("system_id", 0))
            if not system_id:
                continue
            values = systems.setdefault(system_id, {"ship_jumps": 0, "ship_kills": 0, "pod_kills": 0, "npc_kills": 0})
            values.update({key: max(0, int(row.get(key, 0))) for key in ("ship_kills", "pod_kills", "npc_kills")})
        for values in systems.values():
            values["danger"] = danger_score(values["ship_kills"], values["pod_kills"])
            values["danger_band"] = danger_band(values["danger"])
        return {str(system_id): values for system_id, values in systems.items()}

    def get_live_intel(self, force=False) -> dict:
        """Return fresh data where possible, otherwise last cache marked stale."""
        with self._lock:
            cached, now = self._read_cache(), self.now()
            age = max(0, int(now - cached.get("updated_at", 0))) if cached else None
            if cached.get("systems") and not force and age is not None and age < LIVE_TTL_SECONDS:
                return {"ok": True, "systems": cached["systems"], "updated_at": cached["updated_at"], "state": "fresh", "age_seconds": age}
            try:
                jumps = self.fetch_json("/universe/system_jumps/")
                kills = self.fetch_json("/universe/system_kills/")
                systems = self._normalise(jumps if isinstance(jumps, list) else [], kills if isinstance(kills, list) else [])
                snapshot = {"updated_at": now, "systems": systems}
                cached.update(snapshot)
                self._write_cache(cached)
                return {"ok": True, **snapshot, "state": "live", "age_seconds": 0}
            except Exception as exc:
                if cached.get("systems") and age is not None and age <= STALE_TTL_SECONDS:
                    return {"ok": True, "systems": cached["systems"], "updated_at": cached["updated_at"], "state": "stale", "age_seconds": age, "error": str(exc)}
                return {"ok": False, "systems": {}, "state": "unavailable", "error": str(exc)}

    def get_sovereignty(self, force=False) -> dict:
        """Cached public sovereignty overlay; it never blocks the base map."""
        with self._lock:
            cached, now = self._read_cache(), self.now()
            entry = cached.get("sovereignty", {})
            age = max(0, int(now - entry.get("updated_at", 0))) if entry else None
            if entry.get("systems") and not force and age is not None and age < SOVEREIGNTY_TTL_SECONDS:
                return {"ok": True, "systems": entry["systems"], "updated_at": entry["updated_at"], "state": "fresh", "age_seconds": age}
            try:
                rows = self.fetch_json("/sovereignty/map/")
                systems = {}
                for row in rows if isinstance(rows, list) else []:
                    system_id = int(row.get("system_id", 0))
                    if system_id:
                        systems[str(system_id)] = {key: int(row[key]) for key in ("alliance_id", "corporation_id", "faction_id") if row.get(key)}
                snapshot = {"updated_at": now, "systems": systems}
                cached["sovereignty"] = snapshot
                self._write_cache(cached)
                return {"ok": True, **snapshot, "state": "live", "age_seconds": 0}
            except Exception as exc:
                if entry.get("systems") and age is not None and age <= STALE_TTL_SECONDS:
                    return {"ok": True, "systems": entry["systems"], "updated_at": entry["updated_at"], "state": "stale", "age_seconds": age, "error": str(exc)}
                return {"ok": False, "systems": {}, "state": "unavailable", "error": str(exc)}

    def get_entity_names(self, ids) -> dict:
        """Resolve panel-only faction/alliance/corporation IDs with a long TTL."""
        requested = sorted({int(value) for value in ids or [] if str(value).isdigit() and int(value) > 0})
        if not requested:
            return {"ok": True, "names": {}, "state": "fresh"}
        with self._lock:
            cached, now = self._read_cache(), self.now()
            entries = cached.get("entity_names", {})
            fresh = {
                entity_id for entity_id in requested
                if entries.get(str(entity_id)) and now - entries[str(entity_id)].get("updated_at", 0) < ENTITY_NAME_TTL_SECONDS
            }
            missing = [entity_id for entity_id in requested if entity_id not in fresh]
            try:
                if missing:
                    for row in self.fetch_names(missing) or []:
                        entity_id = int(row.get("id", 0))
                        if entity_id:
                            entries[str(entity_id)] = {
                                "name": str(row.get("name", entity_id)),
                                "category": str(row.get("category", "entity")),
                                "updated_at": now,
                            }
                    cached["entity_names"] = entries
                    self._write_cache(cached)
                names = {str(entity_id): entries[str(entity_id)] for entity_id in requested if str(entity_id) in entries}
                return {"ok": True, "names": names, "state": "live" if missing else "fresh"}
            except Exception as exc:
                names = {str(entity_id): entries[str(entity_id)] for entity_id in requested if str(entity_id) in entries}
                return {"ok": bool(names), "names": names, "state": "stale" if names else "unavailable", "error": str(exc)}

    def get_recent_kills(self, system_id: int) -> dict:
        """Lazy zKill lookup: never called while loading the galaxy."""
        system_id = int(system_id)
        live_kills = self.live_kills(system_id) or []
        with self._lock:
            cache = self._read_cache()
            zkill = cache.get("zkill", {})
            entry, now = zkill.get(str(system_id)), self.now()
            if entry and now - entry.get("updated_at", 0) < ZKILL_TTL_SECONDS and all("attackers" in kill for kill in entry.get("kills", [])):
                return {"ok": True, "kills": _merge_kills(live_kills, entry.get("kills", [])), "updated_at": entry["updated_at"], "state": "fresh"}
            if live_kills:
                return {"ok": True, "kills": _merge_kills(live_kills, entry.get("kills", []) if entry else []), "updated_at": now, "state": "live"}
            try:
                kills = self._fetch_zkill_kills(f"https://zkillboard.com/api/kills/solarSystemID/{system_id}/", fallback_system_id=system_id)
                zkill[str(system_id)] = {"updated_at": now, "kills": kills}
                cache["zkill"] = zkill
                self._write_cache(cache)
                return {"ok": True, "kills": _merge_kills(live_kills, kills), "updated_at": now, "state": "live"}
            except Exception as exc:
                if entry:
                    return {"ok": True, "kills": _merge_kills(live_kills, entry.get("kills", [])), "updated_at": entry.get("updated_at"), "state": "stale", "error": str(exc)}
                return {"ok": False, "kills": live_kills, "state": "unavailable", "error": str(exc)}

    @staticmethod
    def _area_endpoint(kind: str, area_id: int) -> str:
        endpoint = {"region": "regionID", "constellation": "constellationID"}.get(str(kind).lower())
        if not endpoint:
            raise ValueError("unsupported map area")
        return f"https://zkillboard.com/api/kills/{endpoint}/{int(area_id)}/"

    @staticmethod
    def _area_cache_key(kind: str, area_id: int) -> str:
        return f"{str(kind).lower()}:{int(area_id)}"

    @staticmethod
    def _find_cached_kill(cache: dict, system_id: int, killmail_id: int) -> dict | None:
        entries = [cache.get("zkill", {}).get(str(system_id), {})]
        entries.extend((cache.get("zkill_areas", {}) or {}).values())
        for entry in entries:
            kill = next((row for row in entry.get("kills", []) if int(row.get("killmail_id", 0)) == killmail_id), None)
            if kill:
                return kill
        return None

    @staticmethod
    def _read_zkill_response(url: str) -> list:
        request = urllib.request.Request(url, headers={"User-Agent": ZKILL_USER_AGENT, "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, list) else []

    def _fetch_zkill_kills(self, url: str, fallback_system_id: int | None = None, limit: int = 5) -> list[dict]:
        return [_normalise_zkill_kill(row, fallback_system_id) for row in self._read_zkill_response(url)[:max(1, int(limit))] if isinstance(row, dict)]

    def get_recent_area_kills(self, kind: str, area_id: int) -> dict:
        """Lazy zKill history for a selected region/constellation, cached per area."""
        kind, area_id = str(kind).lower(), int(area_id)
        cache_key = self._area_cache_key(kind, area_id)
        with self._lock:
            cache, now = self._read_cache(), self.now()
            areas = cache.get("zkill_areas", {})
            entry = areas.get(cache_key)
            if entry and now - entry.get("updated_at", 0) < ZKILL_AREA_TTL_SECONDS:
                return {"ok": True, "kills": entry.get("kills", []), "updated_at": entry["updated_at"], "state": "fresh"}
            try:
                kills = self._fetch_zkill_kills(self._area_endpoint(kind, area_id))
                areas[cache_key] = {"updated_at": now, "kills": kills}
                cache["zkill_areas"] = areas
                self._write_cache(cache)
                return {"ok": True, "kills": kills, "updated_at": now, "state": "live"}
            except Exception as exc:
                if entry:
                    return {"ok": True, "kills": entry.get("kills", []), "updated_at": entry.get("updated_at"), "state": "stale", "error": str(exc)}
                return {"ok": False, "kills": [], "state": "unavailable", "error": str(exc)}

    def get_kill_attackers(self, system_id: int, killmail_id: int) -> dict:
        """Resolve attacker and ship names only when a cached kill is hovered."""
        system_id, killmail_id = int(system_id), int(killmail_id)
        with self._lock:
            kill = next((row for row in self.live_kills(system_id) or [] if int(row.get("killmail_id", 0)) == killmail_id), None)
            kill = kill or self._find_cached_kill(self._read_cache(), system_id, killmail_id)
            if not kill:
                return {"ok": False, "attackers": [], "state": "unavailable"}
            attackers = kill.get("attackers", [])[:MAX_KILL_ATTACKERS]
            ids = [attacker.get(key) for attacker in attackers for key in ("character_id", "corporation_id", "alliance_id", "ship_type_id") if attacker.get(key)]
            names = self.get_entity_names(ids).get("names", {})
            rows = []
            for attacker in attackers:
                character_id, corporation_id = attacker.get("character_id"), attacker.get("corporation_id")
                identity_id = character_id or corporation_id or attacker.get("alliance_id")
                ship_type_id = attacker.get("ship_type_id")
                rows.append({
                    **attacker,
                    "pilot_name": names.get(str(identity_id), {}).get("name", f"Pilot {identity_id}" if identity_id else "Unknown pilot"),
                    "ship_name": names.get(str(ship_type_id), {}).get("name", f"Ship {ship_type_id}" if ship_type_id else "Unknown ship"),
                })
            return {"ok": True, "attackers": rows, "total_attackers": int(kill.get("attacker_count", len(rows))), "state": "fresh"}

    def get_kill_attackers_intel(self, system_id: int, killmail_id: int) -> dict:
        """Add lazy Local-style zKill profiles to attackers already resolved for a kill.

        This intentionally runs only from a hover card.  The LocalAnalyzer owns
        its own ten-minute cache and request pacing, so a busy R2Z2 stream never
        turns into a per-kill burst of zKill statistics requests.
        """
        result = self.get_kill_attackers(system_id, killmail_id)
        if not result.get("ok"):
            return result
        identities = [
            (row["character_id"], row["pilot_name"])
            for row in result.get("attackers", [])
            if row.get("character_id") and row.get("pilot_name")
        ]
        if not identities:
            return result
        try:
            import eve_local_analyzer
            profiles = eve_local_analyzer.LocalAnalyzer().analyze_identities(identities)
            by_character_id = {
                int(profile["character_id"]): profile
                for profile in profiles.get("pilots", [])
                if profile.get("character_id")
            }
            for row in result["attackers"]:
                profile = by_character_id.get(int(row.get("character_id") or 0))
                if profile:
                    row.update({
                        key: profile.get(key)
                        for key in ("danger", "snuggly", "band", "average_gang", "solo_ratio", "gang_ratio", "corporation_name", "alliance_name", "zkill_url")
                    })
            result["intel_state"] = profiles.get("state", "unavailable")
        except Exception:
            result["intel_state"] = "unavailable"
        return result

    def get_kill_victim_intel(self, system_id: int, killmail_id: int) -> dict:
        """Resolve the victim only when its compact combat-log card is hovered."""
        system_id, killmail_id = int(system_id), int(killmail_id)
        with self._lock:
            kill = next((row for row in self.live_kills(system_id) or [] if int(row.get("killmail_id", 0)) == killmail_id), None)
            kill = kill or self._find_cached_kill(self._read_cache(), system_id, killmail_id)
        if not kill:
            return {"ok": False, "victim": {}, "state": "unavailable"}
        victim = {
            "character_id": kill.get("victim_character_id"),
            "corporation_id": kill.get("victim_corporation_id"),
            "alliance_id": kill.get("victim_alliance_id"),
            "ship_type_id": kill.get("ship_type_id") or kill.get("victim_ship_type_id"),
            "value": kill.get("value", 0), "time": kill.get("time") or kill.get("killmail_time"),
        }
        ids = [value for value in (victim["character_id"], victim["corporation_id"], victim["alliance_id"], victim["ship_type_id"]) if value]
        names = self.get_entity_names(ids).get("names", {})
        character_id = victim.get("character_id")
        victim.update({
            "pilot_name": names.get(str(character_id), {}).get("name", "Victime inconnue"),
            "corporation_name": names.get(str(victim.get("corporation_id")), {}).get("name"),
            "alliance_name": names.get(str(victim.get("alliance_id")), {}).get("name"),
            "ship_name": names.get(str(victim.get("ship_type_id")), {}).get("name", "Vaisseau inconnu"),
        })
        if character_id:
            try:
                import eve_local_analyzer
                profile = eve_local_analyzer.LocalAnalyzer().analyze_identities([(character_id, victim["pilot_name"])])
                victim.update((profile.get("pilots") or [{}])[0])
            except Exception:
                pass
        return {"ok": True, "victim": victim, "state": "fresh"}

    def get_character_positions(self) -> dict:
        """Return only opt-in SSO characters with the location capability."""
        with self._lock:
            now = self.now()
            if self._character_positions is not None and now - self._character_positions_at < 15:
                return {"ok": True, "positions": self._character_positions, "state": "fresh"}
            positions, errors = [], []
            for character in sso.connected_chars():
                character_id = character["id"]
                if not sso.character_capabilities(character_id).get("character_location"):
                    continue
                response = esi_auth.request_json("GET", f"/latest/characters/{character_id}/location/", character_id, timeout=8, max_attempts=2)
                system_id = response.data.get("solar_system_id") if response.ok and isinstance(response.data, dict) else None
                if system_id:
                    positions.append({"character_id": character_id, "name": character["name"], "system_id": int(system_id)})
                elif response.error:
                    errors.append(response.error.message)
            self._character_positions, self._character_positions_at = positions, now
            if positions:
                return {"ok": True, "positions": positions, "state": "live", "errors": errors}
            return {"ok": False, "positions": [], "state": "scope_required" if not errors else "unavailable", "errors": errors}


_default_service = EveMapIntelService()


def get_live_intel(force=False):
    return _default_service.get_live_intel(force)


def get_recent_kills(system_id):
    return _default_service.get_recent_kills(system_id)


def get_recent_area_kills(kind, area_id):
    return _default_service.get_recent_area_kills(kind, area_id)


def get_kill_attackers(system_id, killmail_id):
    return _default_service.get_kill_attackers(system_id, killmail_id)


def get_kill_attackers_intel(system_id, killmail_id):
    return _default_service.get_kill_attackers_intel(system_id, killmail_id)


def get_kill_victim_intel(system_id, killmail_id):
    return _default_service.get_kill_victim_intel(system_id, killmail_id)


def get_sovereignty(force=False):
    return _default_service.get_sovereignty(force)


def get_entity_names(ids):
    return _default_service.get_entity_names(ids)


def get_character_positions():
    return _default_service.get_character_positions()
