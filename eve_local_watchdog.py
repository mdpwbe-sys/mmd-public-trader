"""One read-only Local chatlog tailer for positions and optional flood alerts."""
import codecs
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

from eve_map_runtime import local_intel_cache_path

DEFAULTS = {"enabled": False, "threshold": 5, "duration": 5, "cooldown": 15}
LINE = re.compile(r"^\[\s*(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s*\]\s*([^>]+?)\s*>\s*(.*)$")
SESSION = re.compile(r"^Local_\d{8}_\d{6}_(\d+)\.txt$", re.I)
SYSTEM_SENDERS = {"eve system", "eve système", "eve-system", "system"}


def timestamp(value):
    try:
        return datetime.strptime(value.strip(), "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, AttributeError):
        return None


def chatlog_directory():
    if os.name == "nt":
        path = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, path) == 0:
            return Path(path.value) / "EVE" / "logs" / "Chatlogs"
    return Path.home() / "Documents" / "EVE" / "logs" / "Chatlogs"


def local_client_names():
    """Read EVE window titles only; no input or window-state manipulation."""
    if os.name != "nt":
        return set()
    from ctypes import wintypes
    names, user32 = set(), ctypes.windll.user32
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback
    def visit(hwnd, _):
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, len(title))
        if title.value.startswith("EVE - "):
            names.add(title.value[6:].strip().casefold())
        return True
    user32.EnumWindows(visit, 0)
    return names


def repeat_ratio(messages):
    normalised = []
    for text in messages:
        text = re.sub(r"https?://\S+|www\.\S+", "<url>", text.lower())
        text = re.sub(r"\d+", "<n>", text)
        normalised.append(" ".join(text.split()))
    return round(100 * (len(normalised) - len(set(normalised))) / len(normalised)) if normalised else 0


class FloodDetector:
    def __init__(self, settings=None):
        self.settings, self.senders = {**DEFAULTS, **(settings or {})}, {}

    def add(self, sender, text, at):
        row = self.senders.setdefault(sender.casefold(), {"name": sender, "events": [], "latched": False,
                                                          "last_alert": -float("inf"), "below_since": None})
        row["events"].append((at, text))

    def evaluate(self, now):
        alerts = []
        duration, threshold = self.settings["duration"], self.settings["threshold"]
        end, start = int(now // 60) * 60, int(now // 60) * 60 - duration * 60
        for key, row in list(self.senders.items()):
            row["events"] = [(at, text) for at, text in row["events"] if at >= start]
            buckets = [sum(start + minute * 60 <= at < start + (minute + 1) * 60 for at, _ in row["events"])
                       for minute in range(duration)]
            flooding = all(count > threshold for count in buckets)
            if not flooding:
                row["below_since"] = row["below_since"] or now
                if now - row["below_since"] >= duration * 60:
                    row["latched"] = False
                continue
            row["below_since"] = None
            if row["latched"] or now - row["last_alert"] < self.settings["cooldown"] * 60:
                continue
            row["latched"], row["last_alert"] = True, now
            messages = [text for at, text in row["events"] if at < end]
            alerts.append({"sender": row["name"], "count": len(messages), "repeat_ratio": repeat_ratio(messages)})
        return alerts


class LocalChatWatchdog:
    def __init__(self, positions, *, directory=None, settings_path=None, on_log=None, now=time.time, active_clients=local_client_names):
        self.positions, self.now, self.active_clients = positions, now, active_clients
        self.directory = Path(directory) if directory is not None else chatlog_directory()
        self.settings_path = Path(settings_path or local_intel_cache_path().with_name("local_watchdog.json"))
        self.on_log = on_log or (lambda _: None)
        try:
            self._settings = self._normalise(json.loads(self.settings_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            self._settings = dict(DEFAULTS)
        self.flood, self._files, self._seen = FloodDetector(self._settings), {}, {}
        self._lock, self._stop, self._thread, self.last_error = threading.RLock(), threading.Event(), None, None

    @staticmethod
    def _normalise(values):
        values = values if isinstance(values, dict) else {}
        result = {"enabled": bool(values.get("enabled", False))}
        for key, upper in (("threshold", 1000), ("duration", 60), ("cooldown", 120)):
            try: result[key] = max(1, min(upper, int(values.get(key, DEFAULTS[key]))))
            except (TypeError, ValueError): result[key] = DEFAULTS[key]
        return result

    def settings(self):
        with self._lock: return dict(self._settings)

    def configure(self, values):
        with self._lock:
            self._settings, self.flood = self._normalise({**self._settings, **(values or {})}), FloodDetector(self._settings)
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._settings), encoding="utf-8")
            temporary.replace(self.settings_path)
        return self.settings()

    def status(self):
        return {"watcher_running": bool(self._thread and self._thread.is_alive()), "sessions": len(self._files), "last_error": self.last_error}

    def _line(self, state, line, now, clients):
        line = line.strip("\ufeff\r\n ")
        for label, field in (("Listener:", "name"), ("Session started:", "started"), ("Channel ID:", "channel")):
            if line.startswith(label):
                value = line[len(label):].strip()
                state[field] = timestamp(value) if field == "started" else value
                return
        match = LINE.match(line)
        if not match or not state.get("name") or state.get("channel", "").lower() != "local" or not state.get("started"):
            return
        at, sender, message = timestamp(match[1]), match[2].strip(), match[3]
        if at is None or at > now + 2: return
        if sender.casefold() in SYSTEM_SENDERS:
            changed = re.search(r"(?:Channel changed to Local|Canal chang[ée].*Local)\s*:\s*(.+)$", message, re.I)
            if changed: state["system"] = changed[1].strip()
        elif sender and len(sender) <= 37 and self._settings["enabled"] and 0 <= now - at <= self._settings["duration"] * 60 + 60:
            key = hashlib.sha256(f"{state.get('system')}|{line}".encode()).digest()
            if key not in self._seen:
                self._seen[key] = at; self.flood.add(sender, message, at)
        if state.get("system") and state["name"].casefold() in clients and 0 <= now - at <= 30:
            self.positions.observe_local(character_id=state["character_id"], character_name=state["name"], system_name=state["system"],
                observed_at=at, session_source=state["path"], session_started=state["started"])

    def poll_once(self):
        now, clients = self.now(), self.active_clients()
        with self._lock:
            active = set()
            for path in self.directory.glob("Local_*.txt") if self.directory.exists() else []:
                match = SESSION.match(path.name)
                if not match: continue
                try:
                    stat = path.stat()
                    if now - stat.st_mtime > max(360, self._settings["duration"] * 60 + 60): continue
                    active.add(str(path)); signature = (stat.st_dev, stat.st_ino, getattr(stat, "st_birthtime_ns", 0)); state = self._files.get(str(path))
                    fresh = state is None or state["signature"] != signature or stat.st_size < state["offset"]
                    with path.open("rb") as source:
                        if fresh:
                            header = source.read(4096); encoding = "utf-16-le" if header.startswith(b"\xff\xfe") else "utf-8-sig"
                            state = {"signature": signature, "offset": 0, "partial": "", "path": str(path), "character_id": int(match[1]), "decoder": codecs.getincrementaldecoder(encoding)(errors="replace")}
                            self._files[str(path)] = state
                            for line in header.decode(encoding, errors="replace").split("\n")[:-1]: self._line(state, line, now, clients)
                            if stat.st_size > 262144:
                                state["offset"] = stat.st_size - 262144 - ((stat.st_size - 262144) % 2 if encoding.startswith("utf-16") else 0); state["discard_first"] = True
                        source.seek(state["offset"]); raw = source.read(262144); state["offset"] = source.tell()
                    if raw:
                        lines = (state["partial"] + state["decoder"].decode(raw)).split("\n"); state["partial"] = lines.pop()
                        if state.pop("discard_first", False) and lines: lines.pop(0)
                        for line in lines: self._line(state, line, now, clients)
                except (OSError, ValueError, TypeError) as exc: self.last_error = type(exc).__name__
            self._files = {path: state for path, state in self._files.items() if path in active}
            self._seen = {key: at for key, at in self._seen.items() if now - at <= 3660}
            if self._settings["enabled"]:
                for alert in self.flood.evaluate(now): self.on_log(f"LOCAL · FLOOD · {alert['sender']} · {alert['count']} msgs/{self._settings['duration']}m · {alert['repeat_ratio']}% repeat")
        self.positions.publish()

    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._stop.clear(); self._thread = threading.Thread(target=self._run, name="mmd-local-chatlogs", daemon=True); self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try: self.poll_once()
            except Exception as exc:
                if self.last_error != type(exc).__name__: self.on_log(f"LOCAL WATCHDOG · unavailable ({type(exc).__name__})")
                self.last_error = type(exc).__name__
            self._stop.wait(1)

    def stop(self): self._stop.set()
