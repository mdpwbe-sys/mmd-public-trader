"""Local proximity alerts layered on the single R2Z2 map stream."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time

from eve_map_runtime import intel_alert_settings_path


POSITION_REFRESH_SECONDS = 15
DEFAULT_SETTINGS = {
    "enabled": False,
    "radius_jumps": 5,
    "sound_enabled": True,
    "tracked_character_ids": [],
}


def stream_required(map_visible: object, intel_alert_enabled: object) -> bool:
    """The sole lifecycle rule for the shared R2Z2 worker."""
    return bool(map_visible or intel_alert_enabled)


def _normalise_settings(values: object) -> dict:
    values = values if isinstance(values, dict) else {}
    tracked = sorted({int(value) for value in values.get("tracked_character_ids", []) if str(value).strip().isdigit() and int(value) > 0})
    try:
        radius = int(values.get("radius_jumps", DEFAULT_SETTINGS["radius_jumps"]))
    except (TypeError, ValueError):
        radius = DEFAULT_SETTINGS["radius_jumps"]
    return {
        "enabled": bool(values.get("enabled", DEFAULT_SETTINGS["enabled"])),
        "radius_jumps": min(20, max(0, radius)),
        "sound_enabled": bool(values.get("sound_enabled", DEFAULT_SETTINGS["sound_enabled"])),
        "tracked_character_ids": tracked,
    }


class EveMapIntelAlert:
    """Cache <= N gate hops for tracked ESI positions and match live markers."""

    def __init__(self, *, settings_path: Path | None = None, map_service=None, get_positions=None, now=time.time):
        self.settings_path = Path(settings_path or intel_alert_settings_path())
        self.map_service = map_service
        self.get_positions = get_positions
        self.now = now
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._settings = self._read_settings()
        self._reachable_by_character: dict[int, dict[int, int]] = {}
        self._positions_by_character: dict[int, dict] = {}
        self._position_key = None
        self._seen_killmail_ids: dict[int, float] = {}

    def _read_settings(self) -> dict:
        try:
            return _normalise_settings(json.loads(self.settings_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return dict(DEFAULT_SETTINGS)

    def _write_settings(self) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._settings, separators=(",", ":")), encoding="utf-8")
            temporary.replace(self.settings_path)
        except OSError:
            pass

    def settings(self) -> dict:
        with self._lock:
            return dict(self._settings)

    def configure(self, values: object) -> dict:
        with self._lock:
            self._settings = _normalise_settings({**self._settings, **(values if isinstance(values, dict) else {})})
            self._position_key = None
            self._reachable_by_character = {}
            self._write_settings()
            enabled = self._settings["enabled"]
        if enabled:
            self.start()
            self.refresh_positions()
        else:
            self.stop()
        return self.settings()

    def is_enabled(self) -> bool:
        with self._lock:
            return bool(self._settings["enabled"])

    def start(self) -> None:
        with self._lock:
            if not self._settings["enabled"] or (self._thread and self._thread.is_alive()):
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="mmd-intel-alert-positions", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _tracked_positions(self, response: object) -> list[dict]:
        positions = response.get("positions", []) if isinstance(response, dict) else []
        with self._lock:
            selected = set(self._settings["tracked_character_ids"])
        rows = []
        for row in positions if isinstance(positions, list) else []:
            try:
                character_id, system_id = int(row["character_id"]), int(row["system_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if selected and character_id not in selected:
                continue
            rows.append({"character_id": character_id, "name": str(row.get("name") or character_id), "system_id": system_id})
        return rows

    def refresh_positions(self) -> list[dict]:
        if not self.is_enabled() or not self.get_positions or not self.map_service:
            return []
        try:
            positions = self._tracked_positions(self.get_positions())
        except Exception:
            return []
        with self._lock:
            radius = self._settings["radius_jumps"]
            key = tuple(sorted((row["character_id"], row["system_id"]) for row in positions)) + ((-1, radius),)
            if key == self._position_key:
                return positions
        reachable = {row["character_id"]: self.map_service.systems_within_jumps(row["system_id"], radius) for row in positions}
        with self._lock:
            self._position_key, self._reachable_by_character = key, reachable
            self._positions_by_character = {row["character_id"]: row for row in positions}
        return positions

    def on_marker(self, marker: object) -> dict | None:
        if not self.is_enabled() or not isinstance(marker, dict):
            return None
        try:
            killmail_id, system_id = int(marker["killmail_id"]), int(marker["system_id"])
        except (KeyError, TypeError, ValueError):
            return None
        now = self.now()
        with self._lock:
            self._seen_killmail_ids = {key: value for key, value in self._seen_killmail_ids.items() if now - value <= 60 * 60}
            if killmail_id in self._seen_killmail_ids:
                return None
            candidates = [(distance, character_id) for character_id, distances in self._reachable_by_character.items() if (distance := distances.get(system_id)) is not None]
            if not candidates:
                return None
            distance, character_id = min(candidates)
            self._seen_killmail_ids[killmail_id] = now
            character = dict(self._positions_by_character.get(character_id) or {})
        if not character:
            return None
        return {
            "killmail_id": killmail_id,
            "kill_system_id": system_id,
            "nearest_character_id": character_id,
            "nearest_character_name": character["name"],
            "distance_jumps": distance,
            "victim_ship_type_id": marker.get("victim_ship_type_id"),
            "value": marker.get("value", 0),
            "attacker_count": marker.get("attacker_count", 0),
        }

    def _run(self) -> None:
        while not self._stop.is_set() and self.is_enabled():
            self.refresh_positions()
            self._stop.wait(POSITION_REFRESH_SECONDS)
        with self._lock:
            if self._thread is threading.current_thread():
                self._thread = None
