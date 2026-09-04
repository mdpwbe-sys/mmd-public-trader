"""Polite, bounded R2Z2 combat feed for the New Eden tactical map."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import threading
import time
import urllib.error
import urllib.request


R2Z2_BASE = "https://r2z2.zkillboard.com/ephemeral"
MARKER_TTL_SECONDS = 30 * 60
HISTORY_TTL_SECONDS = 60 * 60
BOOTSTRAP_MAX_REQUESTS = 240
STREAM_RESUME_STALE_SECONDS = 120
EMPTY_SEQUENCE_DELAY_SECONDS = 6
SEQUENCE_DELAY_SECONDS = 0.14  # safely below R2Z2's documented 15 req/s limit
BACKOFF_INITIAL_SECONDS = 2
BACKOFF_MAX_SECONDS = 30
MAX_STORED_ATTACKERS = 20
USER_AGENT = os.environ.get("MMD_MAP_USER_AGENT", "EVE-Market-Manager/1.0 (tactical combat stream)")


def _kill_time(value: object) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _attacker_rows(values: object) -> tuple[list[dict], int]:
    attackers = values if isinstance(values, list) else []
    useful = ("character_id", "corporation_id", "alliance_id", "ship_type_id", "final_blow", "damage_done")
    rows = [{key: row.get(key) for key in useful if row.get(key) is not None}
            for row in attackers if isinstance(row, dict)]
    rows.sort(key=lambda row: (not bool(row.get("final_blow")), -int(row.get("damage_done", 0))))
    return rows[:MAX_STORED_ATTACKERS], len(rows)


def _payload_killmail(payload: dict) -> dict:
    """Return the ESI killmail embedded by either supported R2Z2 envelope."""
    for key in ("esi", "killmail"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return candidate
    return payload


def marker_from_killmail(payload: dict, *, now: float) -> dict | None:
    """Keep only recent killmails with a locatable solar system."""
    killmail = _payload_killmail(payload)
    happened_at = _kill_time(killmail.get("killmail_time"))
    system_id = _int(killmail.get("solar_system_id"))
    killmail_id = _int(killmail.get("killmail_id") or payload.get("killmail_id"))
    if not happened_at or not system_id or not killmail_id or now - happened_at > HISTORY_TTL_SECONDS or happened_at > now + 60:
        return None
    victim = killmail.get("victim") if isinstance(killmail.get("victim"), dict) else {}
    attackers, attacker_count = _attacker_rows(killmail.get("attackers"))
    zkb = payload.get("zkb") if isinstance(payload.get("zkb"), dict) else {}
    return {
        "killmail_id": killmail_id, "system_id": system_id,
        "killmail_time": str(killmail.get("killmail_time") or ""),
        "happened_at": happened_at, "received_at": now,
        "value": max(0, _int(zkb.get("totalValue")) or 0),
        "zkb_hash": str(zkb.get("hash") or "") or None,
        "victim_ship_type_id": _int(victim.get("ship_type_id")),
        "victim_character_id": _int(victim.get("character_id")),
        "victim_corporation_id": _int(victim.get("corporation_id")),
        "victim_alliance_id": _int(victim.get("alliance_id")),
        "attackers": attackers, "attacker_count": attacker_count,
    }


def marker_to_recent_kill(marker: dict) -> dict:
    """Adapt an in-memory R2Z2 marker for ``get_recent_kills`` consumers."""
    killmail_id = int(marker["killmail_id"])
    return {
        "killmail_id": killmail_id, "time": marker.get("killmail_time"),
        "value": marker.get("value", 0), "solar_system_id": marker.get("system_id"),
        "ship_type_id": marker.get("victim_ship_type_id"),
        "victim_character_id": marker.get("victim_character_id"),
        "victim_corporation_id": marker.get("victim_corporation_id"),
        "victim_alliance_id": marker.get("victim_alliance_id"),
        "attackers": list(marker.get("attackers") or []),
        "attacker_count": marker.get("attacker_count", 0), "zkb_hash": marker.get("zkb_hash"),
        "received_at": marker.get("received_at"),
        "url": f"https://zkillboard.com/kill/{killmail_id}/",
    }


class EveMapKillStream:
    """A single background consumer with a five-minute read-only interface."""

    def __init__(self, *, fetch_json=None, now=time.time, on_marker=None):
        self.fetch_json = fetch_json or self._fetch_json
        self.now = now
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sequence: int | None = None
        self._markers: dict[int, dict] = {}
        self._on_marker = on_marker
        self._bootstrap_cursor: int | None = None
        self._bootstrap_head: int | None = None
        self._bootstrap_requests = 0
        self._state = "off"
        self._error: str | None = None
        self._backoff_seconds = BACKOFF_INITIAL_SECONDS
        self._last_success_at = 0.0

    @staticmethod
    def _fetch_json(url: str) -> tuple[int, dict | None]:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, None

    def _fetch(self, url: str) -> tuple[int, dict | None, str | None]:
        try:
            status, payload = self.fetch_json(url)
            return int(status), payload if isinstance(payload, dict) else None, None
        except urllib.error.HTTPError as exc:
            return exc.code, None, f"R2Z2 HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return 0, None, f"R2Z2 network error: {exc}"
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return 0, None, f"R2Z2 invalid JSON: {exc}"
        except Exception as exc:
            return 0, None, f"R2Z2 unexpected error: {type(exc).__name__}"

    def should_restart_from_head(self) -> bool:
        return self._sequence is not None and self.now() - self._last_success_at > STREAM_RESUME_STALE_SECONDS

    def _reset_to_head_locked(self) -> None:
        self._sequence = None
        self._bootstrap_cursor = None
        self._bootstrap_head = None
        self._bootstrap_requests = 0

    def activate(self) -> None:
        with self._lock:
            if self.should_restart_from_head():
                self._reset_to_head_locked()
            self._stop.clear()
            if self._thread and self._thread.is_alive():
                self._state, self._error = "starting", None
                return
            self._state, self._error = "starting", None
            self._thread = threading.Thread(target=self._run, name="mmd-r2z2", daemon=True)
            self._thread.start()

    def deactivate(self) -> None:
        self._stop.set()
        with self._lock:
            self._state = "off"

    def set_marker_handler(self, handler) -> None:
        """Install the UI notification sink without coupling this worker to pywebview."""
        with self._lock:
            self._on_marker = handler

    def recent_markers(self) -> dict:
        self.activate()
        now = self.now()
        with self._lock:
            self._markers = {kill_id: marker for kill_id, marker in self._markers.items() if now - marker["happened_at"] <= HISTORY_TTL_SECONDS}
            markers = [marker for marker in self._markers.values() if now - marker["happened_at"] <= MARKER_TTL_SECONDS]
            result = {"ok": True, "state": self._state, "markers": sorted(markers, key=lambda marker: marker["happened_at"], reverse=True)}
            if self._error:
                result["error"] = self._error
            return result

    def recent_kills(self, system_id: int, *, limit: int = 5) -> list[dict]:
        now, system_id = self.now(), int(system_id)
        with self._lock:
            self._markers = {kill_id: marker for kill_id, marker in self._markers.items() if now - marker["happened_at"] <= HISTORY_TTL_SECONDS}
            rows = [marker_to_recent_kill(marker) for marker in self._markers.values() if marker["system_id"] == system_id]
        return sorted(rows, key=lambda row: row.get("time") or "", reverse=True)[:max(1, int(limit))]

    def _bootstrap(self) -> bool:
        status, payload, error = self._fetch(f"{R2Z2_BASE}/sequence.json")
        latest = _int((payload or {}).get("sequence"))
        if status != 200 or not latest or latest <= 0:
            with self._lock:
                self._state, self._error = "unavailable", error or f"R2Z2 HTTP {status}"
            return False
        self._bootstrap_head = latest
        self._bootstrap_cursor = latest
        self._bootstrap_requests = 0
        return True

    def _accept(self, payload: dict, *, publish: bool = False) -> None:
        marker = marker_from_killmail(payload, now=self.now())
        if marker:
            handler = None
            with self._lock:
                is_new = marker["killmail_id"] not in self._markers
                self._markers[marker["killmail_id"]] = marker
                if publish and is_new:
                    handler = self._on_marker
            if handler:
                try:
                    handler(marker)
                except Exception:
                    # A UI notification cannot compromise the bounded feed.
                    pass

    def _bootstrap_step(self) -> bool:
        cursor = self._bootstrap_cursor
        if cursor is None:
            return True
        status, payload, error = self._fetch(f"{R2Z2_BASE}/{cursor}.json")
        if status not in (200, 404):
            with self._lock:
                self._error = error or f"R2Z2 HTTP {status}"
            return False
        happened_at = None
        if payload:
            self._accept(payload, publish=False)
            happened_at = _kill_time(_payload_killmail(payload).get("killmail_time"))
        self._bootstrap_cursor -= 1
        self._bootstrap_requests += 1
        if (happened_at and self.now() - happened_at >= HISTORY_TTL_SECONDS) or self._bootstrap_requests >= BOOTSTRAP_MAX_REQUESTS or self._bootstrap_cursor < 1:
            self._sequence = int(self._bootstrap_head or 0) + 1
            self._bootstrap_cursor = self._bootstrap_head = None
            self._last_success_at = self.now()
        return True

    def _failure_wait(self, error: str) -> None:
        with self._lock:
            delay = self._backoff_seconds
            self._backoff_seconds = min(BACKOFF_MAX_SECONDS, max(BACKOFF_INITIAL_SECONDS, delay * 2))
            self._state, self._error = "unavailable", error
        self._stop.wait(delay)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    if self._sequence is None:
                        if self._bootstrap_cursor is None:
                            if not self._bootstrap():
                                self._failure_wait(self._error or "R2Z2 unavailable")
                                continue
                        elif not self._bootstrap_step():
                            self._failure_wait(self._error or "R2Z2 unavailable")
                            continue
                        self._stop.wait(SEQUENCE_DELAY_SECONDS)
                        continue
                    status, payload, error = self._fetch(f"{R2Z2_BASE}/{self._sequence}.json")
                    if status == 200 and payload:
                        self._accept(payload, publish=True)
                        self._sequence += 1
                        with self._lock:
                            self._last_success_at = self.now()
                            self._backoff_seconds = BACKOFF_INITIAL_SECONDS
                            self._state, self._error = "live", None
                        self._stop.wait(SEQUENCE_DELAY_SECONDS)
                    elif status == 404:
                        with self._lock:
                            self._last_success_at = self.now()
                            self._backoff_seconds = BACKOFF_INITIAL_SECONDS
                            self._state, self._error = "live", None
                        self._stop.wait(EMPTY_SEQUENCE_DELAY_SECONDS)
                    else:
                        self._failure_wait(error or f"R2Z2 HTTP {status}")
                except Exception as exc:
                    self._failure_wait(f"R2Z2 unexpected worker error: {type(exc).__name__}")
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None


_default_stream = EveMapKillStream()


def get_recent_markers() -> dict:
    return _default_stream.recent_markers()


def get_recent_kills(system_id: int, limit: int = 5) -> list[dict]:
    return _default_stream.recent_kills(system_id, limit=limit)


def set_active(active: bool) -> None:
    if active:
        _default_stream.activate()
    else:
        _default_stream.deactivate()


def set_marker_handler(handler) -> None:
    _default_stream.set_marker_handler(handler)
