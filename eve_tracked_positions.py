"""Shared solar-system position cache. No network calls on a kill event."""
from dataclasses import asdict, dataclass
import logging
import threading
import time


@dataclass(frozen=True)
class TrackedCharacterPosition:
    character_id: int
    character_name: str
    system_id: int
    system_name: str
    source: str
    observed_at: float
    session_source: str = ""
    session_started: float = 0

    def payload(self, now):
        return {**asdict(self), "name": self.character_name,
                "local_confirmed": self.source == "LOCAL_CHATLOG" and 0 <= now - self.observed_at <= 30}


class TrackedPositions:
    """Fresh Local chatlogs win; ESI fills in other and stale characters."""
    def __init__(self, *, map_service, characters, fetch_location, now=time.time):
        self.map_service, self.characters, self.fetch_location, self.now = map_service, characters, fetch_location, now
        self._lock = threading.RLock()
        self._local, self._esi, self._attempts, self._inflight = {}, {}, {}, set()
        self._listeners, self._published, self._names = [], None, None

    def subscribe(self, listener):
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def system_by_name(self, name):
        if self._names is None:
            self._names = {system["name"].casefold(): system for system in self.map_service.get_map_data()["systems"]}
        return self._names.get(str(name).strip().casefold())

    def observe_local(self, *, character_id, character_name, system_name, observed_at, session_source, session_started):
        system, now = self.system_by_name(system_name), self.now()
        if not system or not character_name or not 0 < int(character_id) or not 0 <= now - observed_at <= 30:
            return False
        # Known identities make the filename plus Listener an identity check,
        # rather than allowing an unrelated local client into tracking.
        known, matched = list(self.characters()), False
        for character in known:
            if int(character["id"]) == int(character_id) or character["name"].casefold() == character_name.casefold():
                if int(character["id"]) != int(character_id) or character["name"].casefold() != character_name.casefold():
                    return False
                matched = True
        if known and not matched:
            return False
        row = TrackedCharacterPosition(int(character_id), character_name, int(system["id"]), system["name"],
                                       "LOCAL_CHATLOG", observed_at, str(session_source), session_started)
        with self._lock:
            old = self._local.get(row.character_id)
            if old and (session_started < old.session_started or observed_at < old.observed_at):
                return False
            self._local[row.character_id] = row
        self.publish()
        return True

    def _effective(self):
        now, rows = self.now(), []
        for character_id in sorted(set(self._local) | set(self._esi)):
            local, esi = self._local.get(character_id), self._esi.get(character_id)
            if local and 0 <= now - local.observed_at <= 30:
                row = local
            elif esi and now - esi.observed_at <= 30:
                row = esi
            else:
                available = [row for row in (local, esi) if row]
                if not available:
                    continue
                last = max(available, key=lambda row: row.observed_at)
                if now - last.observed_at > 300:
                    continue
                row = TrackedCharacterPosition(last.character_id, last.character_name, last.system_id,
                                               last.system_name, "CACHE", last.observed_at)
            rows.append(row.payload(now))
        return {"ok": bool(rows), "positions": rows, "state": "live" if rows else "unavailable"}

    def snapshot(self):
        with self._lock:
            return self._effective()

    def publish(self):
        with self._lock:
            response = self._effective()
            key = tuple((row["character_id"], row["system_id"], row["source"], row["local_confirmed"])
                        for row in response["positions"])
            if key == self._published:
                return
            self._published, listeners = key, list(self._listeners)
        for listener in listeners:
            try:
                listener(response)
            except Exception:
                logging.getLogger(__name__).exception("PositionUpdate subscriber failed")

    def refresh(self):
        errors = []
        for character in self.characters():
            character_id, now = int(character["id"]), self.now()
            with self._lock:
                local = self._local.get(character_id)
                if local and 0 <= now - local.observed_at <= 30:
                    continue
                if character_id in self._inflight or now - self._attempts.get(character_id, -float("inf")) < 15:
                    continue
                self._inflight.add(character_id)
                self._attempts[character_id] = now
            try:
                system_id = self.fetch_location(character_id)
                system = self.map_service.get_system(system_id) if system_id else None
                if system:
                    with self._lock:
                        self._esi[character_id] = TrackedCharacterPosition(character_id, character["name"],
                            int(system["id"]), system["name"], "ESI", now)
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                with self._lock:
                    self._inflight.discard(character_id)
        self.publish()
        return {**self.snapshot(), "errors": errors}
