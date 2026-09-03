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
BOOTSTRAP_SEQUENCES = 96
# Tactical combat remains visible for the last 30 minutes.  The renderer
# condenses concurrent kills per system, so a busier period costs no more draw calls.
MARKER_TTL_SECONDS = 30 * 60
EMPTY_SEQUENCE_DELAY_SECONDS = 6
SEQUENCE_DELAY_SECONDS = 0.14  # safely below R2Z2's documented 15 req/s limit
USER_AGENT = os.environ.get("MMD_MAP_USER_AGENT", "EVE-Market-Manager/1.0 (tactical combat stream)")


def _kill_time(value: object) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def marker_from_killmail(payload: dict, *, now: float) -> dict | None:
    """Keep only recent killmails with a locatable solar system."""
    killmail = payload.get("killmail") if isinstance(payload.get("killmail"), dict) else payload
    happened_at = _kill_time(killmail.get("killmail_time"))
    system_id = killmail.get("solar_system_id")
    killmail_id = killmail.get("killmail_id") or payload.get("killmail_id")
    if not happened_at or now - happened_at > MARKER_TTL_SECONDS or happened_at > now + 60:
        return None
    try:
        victim = killmail.get("victim") if isinstance(killmail.get("victim"), dict) else {}
        attackers = killmail.get("attackers") if isinstance(killmail.get("attackers"), list) else []
        return {
            "killmail_id": int(killmail_id),
            "system_id": int(system_id),
            "happened_at": happened_at,
            "value": max(0, int((payload.get("zkb") or {}).get("totalValue", 0))),
            "victim_ship_type_id": int(victim["ship_type_id"]) if victim.get("ship_type_id") else None,
            "attacker_count": len(attackers),
        }
    except (TypeError, ValueError):
        return None


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
        self._bootstrap_until: int | None = None
        self._state = "off"
        self._error: str | None = None

    @staticmethod
    def _fetch_json(url: str) -> tuple[int, dict | None]:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, None

    def activate(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
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
            self._markers = {kill_id: marker for kill_id, marker in self._markers.items() if now - marker["happened_at"] <= MARKER_TTL_SECONDS}
            result = {"ok": True, "state": self._state, "markers": sorted(self._markers.values(), key=lambda marker: marker["happened_at"], reverse=True)}
            if self._error:
                result["error"] = self._error
            return result

    def _bootstrap(self) -> bool:
        status, payload = self.fetch_json(f"{R2Z2_BASE}/sequence.json")
        try:
            latest = int((payload or {}).get("sequence"))
        except (TypeError, ValueError):
            latest = 0
        if status != 200 or latest <= 0:
            with self._lock:
                self._state, self._error = "unavailable", f"R2Z2 HTTP {status}"
            return False
        self._sequence = max(1, latest - BOOTSTRAP_SEQUENCES)
        self._bootstrap_until = latest
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

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._sequence is None and not self._bootstrap():
                self._stop.wait(EMPTY_SEQUENCE_DELAY_SECONDS)
                continue
            status, payload = self.fetch_json(f"{R2Z2_BASE}/{self._sequence}.json")
            if status == 200 and isinstance(payload, dict):
                publish = self._bootstrap_until is not None and self._sequence >= self._bootstrap_until
                self._accept(payload, publish=publish)
                self._sequence += 1
                with self._lock:
                    self._state, self._error = "live", None
                self._stop.wait(SEQUENCE_DELAY_SECONDS)
                continue
            if status == 404:
                with self._lock:
                    self._state, self._error = "live", None
                self._stop.wait(EMPTY_SEQUENCE_DELAY_SECONDS)
                continue
            with self._lock:
                self._state, self._error = "unavailable", f"R2Z2 HTTP {status}"
            self._stop.wait(EMPTY_SEQUENCE_DELAY_SECONDS)


_default_stream = EveMapKillStream()


def get_recent_markers() -> dict:
    return _default_stream.recent_markers()


def set_active(active: bool) -> None:
    if active:
        _default_stream.activate()
    else:
        _default_stream.deactivate()


def set_marker_handler(handler) -> None:
    _default_stream.set_marker_handler(handler)
