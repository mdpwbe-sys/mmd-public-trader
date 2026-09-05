"""Clipboard-driven Local intel, isolated from MMD's trading workflow."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
from ctypes import wintypes
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.request

from eve_map_runtime import local_intel_cache_path


ESI_BASE = "https://esi.evetech.net/latest"
ZKILL_BASE = "https://zkillboard.com/api"
CACHE_TTL_SECONDS = 10 * 60
REQUEST_SPACING_SECONDS = 0.25
MAX_LOCAL_PILOTS = 512
MIN_LOCAL_PILOTS = 2
USER_AGENT = os.environ.get("MMD_LOCAL_INTEL_USER_AGENT", "EveMarketManager/1.0 (local intel; https://github.com/mdpwbe-sys/mmd-public-trader)")
_NAME_RE = re.compile(r"^[^\r\n\[\]<>]{3,37}$")


def parse_local_names(text: object) -> list[str]:
    """Return a de-duplicated EVE Local candidate list, or an empty list."""
    if not isinstance(text, str):
        return []
    names, seen = [], set()
    for raw in text.replace("\r", "\n").split("\n"):
        name = " ".join(raw.strip().split())
        key = name.casefold()
        if not _NAME_RE.fullmatch(name) or key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) > MAX_LOCAL_PILOTS:
            return []
    return names if len(names) >= MIN_LOCAL_PILOTS else []


def local_fingerprint(names: list[str]) -> str:
    payload = "\n".join(name.casefold() for name in names).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def risk_band(danger: object) -> str:
    value = max(0, min(100, int(float(danger or 0))))
    if value >= 70:
        return "dangerous"
    if value >= 40:
        return "watch"
    return "snuggly"


def _number(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def profile_from_stats(character_id: int, name: str, stats: object) -> dict:
    stats = stats if isinstance(stats, dict) else {}
    info = stats.get("info") if isinstance(stats.get("info"), dict) else {}
    danger = max(0, min(100, _number(stats.get("dangerRatio"))))
    return {
        "character_id": int(character_id), "name": name,
        "danger": danger, "snuggly": 100 - danger, "band": risk_band(danger),
        "average_gang": round(float(stats.get("avgGangSize") or 0), 1),
        "solo_ratio": round(float(stats.get("soloRatio") or 0), 1),
        "gang_ratio": round(float(stats.get("gangRatio") or 0), 1),
        "ships_destroyed": _number(stats.get("shipsDestroyed")), "ships_lost": _number(stats.get("shipsLost")),
        "isk_destroyed": _number(stats.get("iskDestroyed")), "isk_lost": _number(stats.get("iskLost")),
        "corporation_id": _number(info.get("corporation_id") or info.get("corporationID")) or None,
        "alliance_id": _number(info.get("alliance_id") or info.get("allianceID")) or None,
        "zkill_url": f"https://zkillboard.com/character/{int(character_id)}/scanalyzer/",
    }


def summarize(pilots: list[dict], *, fingerprint: str, state: str) -> dict:
    rows = sorted(pilots, key=lambda row: (-int(row.get("danger") or 0), row.get("name", "").casefold()))
    counts = {band: sum(1 for row in rows if row.get("band") == band) for band in ("dangerous", "watch", "snuggly")}
    groups: dict[str, list[str]] = {}
    for row in rows:
        label = row.get("alliance_name") or row.get("corporation_name") or "Independent"
        groups.setdefault(label, []).append(row.get("name", "Unknown"))
    return {
        "ok": True, "state": state, "fingerprint": fingerprint, "pilots": rows,
        "total": len(rows), "resolved": sum(1 for row in rows if row.get("character_id")),
        "dangerous": counts["dangerous"], "watch": counts["watch"], "snuggly": counts["snuggly"],
        "groups": [{"name": name, "count": len(names), "pilots": names} for name, names in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0].casefold()))],
    }


class LocalAnalyzer:
    """Small interface around parsing, public enrichment, caching and scoring."""

    def __init__(self, cache_path: Path | None = None, *, now=time.time, resolve_ids=None, fetch_stats=None, resolve_names=None):
        self.cache_path = Path(cache_path or local_intel_cache_path())
        self.now = now
        self.resolve_ids = resolve_ids or self._resolve_ids
        self.fetch_stats = fetch_stats or self._fetch_stats
        self.resolve_names = resolve_names or self._resolve_names
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0

    def _read_cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"profiles": {}}

    def _write_cache(self, payload: dict) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, self.cache_path)
        except OSError:
            pass

    @staticmethod
    def _post_json(url: str, body: object) -> object:
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    @classmethod
    def _resolve_ids(cls, names: list[str]) -> dict[str, int]:
        payload = cls._post_json(f"{ESI_BASE}/universe/ids/", names)
        return {str(row.get("name")).casefold(): int(row["id"]) for row in (payload.get("characters", []) if isinstance(payload, dict) else []) if row.get("name") and row.get("id")}

    @classmethod
    def _resolve_names(cls, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        payload = cls._post_json(f"{ESI_BASE}/universe/names/", ids)
        return {int(row["id"]): str(row["name"]) for row in (payload if isinstance(payload, list) else []) if row.get("id") and row.get("name")}

    def _fetch_stats(self, character_id: int) -> dict:
        with self._request_lock:
            wait = REQUEST_SPACING_SECONDS - (self.now() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = self.now()
        request = urllib.request.Request(f"{ZKILL_BASE}/stats/characterID/{int(character_id)}/kills/", headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))

    def analyze(self, text: object, *, on_update=None) -> dict:
        names = parse_local_names(text)
        if not names:
            return {"ok": False, "reason": "Aucune liste Local valide détectée."}
        pending = summarize([{"name": name, "band": "unknown"} for name in names],
                            fingerprint=local_fingerprint(names), state="loading")
        if on_update:
            on_update(pending)
        try:
            ids = {str(name).casefold(): int(character_id) for name, character_id in self.resolve_ids(names).items()}
        except Exception as exc:
            result = {**pending, "ok": False, "state": "error",
                      "reason": f"Identity resolution unavailable ({type(exc).__name__}). Retry Analyze Clipboard Now."}
            if on_update:
                on_update(result)
            return result
        if not ids:
            result = {**pending, "ok": False, "state": "error", "reason": "No valid EVE pilots resolved."}
            if on_update:
                on_update(result)
            return result
        return self._analyze_resolved(names, ids, on_update=on_update)

    def analyze_identities(self, identities: list[tuple[int, str]], *, on_update=None) -> dict:
        """Enrich known character IDs without treating them as a clipboard Local list.

        The kill-log adapter uses this seam for one or more attackers.  It shares
        exactly the same zKill cache, pacing and risk calculation as Local.
        """
        names, ids, seen = [], {}, set()
        for character_id, name in identities:
            try:
                character_id, name = int(character_id), " ".join(str(name).split())
            except (TypeError, ValueError):
                continue
            if character_id <= 0 or not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            names.append(name)
            ids[name.casefold()] = character_id
        if not names:
            return {"ok": False, "reason": "Aucun pilote attaquant exploitable."}
        return self._analyze_resolved(names, ids, on_update=on_update)

    def _analyze_resolved(self, names: list[str], ids: dict[str, int], *, on_update=None) -> dict:
        """Shared implementation for clipboard Local and lazy kill-attacker intel."""
        fingerprint, now = local_fingerprint(names), self.now()
        pilots = [{"name": name, "character_id": ids.get(name.casefold()), "band": "unknown", "danger": 0, "snuggly": 0} for name in names]
        by_id = {int(row["character_id"]): row for row in pilots if row.get("character_id")}
        if on_update:
            on_update(summarize(pilots, fingerprint=fingerprint, state="loading"))
        cache = self._read_cache()
        profiles = cache.setdefault("profiles", {})
        missing = []
        for character_id, row in by_id.items():
            cached = profiles.get(str(character_id), {})
            if now - float(cached.get("updated_at", 0)) < CACHE_TTL_SECONDS and isinstance(cached.get("profile"), dict):
                row.update(cached["profile"])
            else:
                missing.append((character_id, row))
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="mmd-local-intel") as pool:
            futures = {pool.submit(self.fetch_stats, character_id): (character_id, row) for character_id, row in missing}
            for index, future in enumerate(as_completed(futures), start=1):
                character_id, row = futures[future]
                try:
                    profile = profile_from_stats(character_id, row["name"], future.result())
                    row.update(profile)
                    profiles[str(character_id)] = {"updated_at": now, "profile": profile}
                except Exception as exc:
                    row["band"] = "unknown"
                    row["error"] = f"zKill unavailable ({type(exc).__name__})"
                if on_update and (index == len(missing) or index % 4 == 0):
                    on_update(summarize(pilots, fingerprint=fingerprint, state="loading"))
        entity_ids = sorted({entity_id for row in pilots for entity_id in (row.get("corporation_id"), row.get("alliance_id")) if entity_id})
        try:
            entity_names = self.resolve_names(entity_ids)
        except Exception:
            entity_names = {}
        for row in pilots:
            row["corporation_name"] = entity_names.get(row.get("corporation_id"))
            row["alliance_name"] = entity_names.get(row.get("alliance_id"))
        self._write_cache(cache)
        result = summarize(pilots, fingerprint=fingerprint, state="ready")
        result["errors"] = [row["error"] for row in pilots if row.get("error")]
        if on_update:
            on_update(result)
        return result


def _clipboard_sequence() -> int:
    if os.name != "nt":
        return 0
    return int(ctypes.windll.user32.GetClipboardSequenceNumber())


CF_TEXT = 1
CF_OEMTEXT = 7
CF_UNICODETEXT = 13
_TEXT_FORMAT_NAMES = {"text", "unicode text", "text/unicode", "utf-8", "utf8"}


def _clipboard_format_name(user32, clipboard_format: int) -> str:
    """Return a registered clipboard format name without exposing its data."""
    if clipboard_format < 0xC000:
        return ""
    buffer = ctypes.create_unicode_buffer(128)
    length = user32.GetClipboardFormatNameW(clipboard_format, buffer, len(buffer))
    return buffer.value[:length].casefold() if length else ""


def _decode_clipboard_bytes(raw: bytes, clipboard_format: int, format_name: str = "") -> str:
    """Decode a text-like Win32 clipboard payload conservatively."""
    if not raw:
        return ""
    if clipboard_format == CF_UNICODETEXT or "unicode" in format_name:
        return raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]
    if clipboard_format in (CF_TEXT, CF_OEMTEXT):
        return raw.decode("oem" if clipboard_format == CF_OEMTEXT else "mbcs", errors="replace").split("\0", 1)[0]
    return raw.decode("utf-8-sig", errors="replace").split("\0", 1)[0]


def _clipboard_text_formats(user32) -> list[tuple[int, str]]:
    """List standard and registered text formats in clipboard order."""
    formats, seen = [], set()

    def add(clipboard_format: int, name: str = "") -> None:
        if clipboard_format and clipboard_format not in seen:
            formats.append((clipboard_format, name))
            seen.add(clipboard_format)

    for clipboard_format in (CF_UNICODETEXT, CF_TEXT, CF_OEMTEXT):
        add(clipboard_format)
    current = 0
    while True:
        current = int(user32.EnumClipboardFormats(current))
        if not current:
            break
        name = _clipboard_format_name(user32, current)
        # EVE clients and overlays can publish a registered Unicode/UTF-8
        # format instead of the legacy constants.  Do not decode arbitrary
        # binary application formats.
        if name in _TEXT_FORMAT_NAMES or "text" in name:
            add(current, name)
    return formats


def read_windows_clipboard() -> str:
    """Read EVE Local text from standard or registered Windows text formats."""
    if os.name != "nt":
        return ""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    if not user32.OpenClipboard(None):
        return ""
    try:
        for clipboard_format, format_name in _clipboard_text_formats(user32):
            handle = user32.GetClipboardData(clipboard_format)
            if not handle:
                continue
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                continue
            try:
                size = int(kernel32.GlobalSize(handle))
                if size:
                    text = _decode_clipboard_bytes(ctypes.string_at(pointer, size), clipboard_format, format_name)
                    if text:
                        return text
            finally:
                kernel32.GlobalUnlock(handle)
        return ""
    finally:
        user32.CloseClipboard()


class LocalClipboardWatcher:
    """Poll the Windows clipboard sequence number; never repeatedly parse its contents."""

    def __init__(self, analyzer: LocalAnalyzer, on_result, *, sequence=_clipboard_sequence, read_text=read_windows_clipboard, poll_seconds=.75, on_diagnostic=None, on_trace=None):
        self.analyzer, self.on_result = analyzer, on_result
        self.sequence, self.read_text, self.poll_seconds = sequence, read_text, poll_seconds
        self.on_diagnostic = on_diagnostic
        self._stop = threading.Event()
        self._last_sequence = None
        self._empty_retry_sequence = None
        self._empty_retry_count = 0
        self._last_fingerprint = None
        self._last_error_signature = None
        self._thread = None
        self.on_trace = on_trace
        self._analysis_lock = threading.Lock()
        self.last_event_at = None
        self.last_candidate_count = 0
        self.last_analysis_at = None
        self.last_error = None

    def status(self):
        return {"watcher_running": bool(self._thread and self._thread.is_alive()),
                "last_clipboard_sequence": self._last_sequence,
                "last_event_at": self.last_event_at, "last_candidate_count": self.last_candidate_count,
                "last_analysis_at": self.last_analysis_at, "last_error": self.last_error}

    def _trace(self, message):
        if self.on_trace:
            self.on_trace("LOCAL ANALYZER · " + message)

    def _deliver(self, result):
        self._trace("UI dispatch")
        self.on_result(result)

    def analyze_now(self):
        """Manual retry crosses the same parser/analyzer/callback seam."""
        for _ in range(24):
            if self.poll_once(force=True):
                return True
            if self._empty_retry_count == 0 or self._empty_retry_count >= 24:
                return False
            if self._stop.wait(self.poll_seconds):
                return False
        return False

    def _diagnose_error(self, error: Exception) -> None:
        """Report one Windows clipboard failure without leaking clipboard data."""
        signature = type(error).__name__
        if signature == self._last_error_signature:
            return
        self._last_error_signature = signature
        if self.on_diagnostic:
            self.on_diagnostic("clipboard_error", 0)

    def poll_once(self, force=False) -> bool:
        if not self._analysis_lock.acquire(blocking=False):
            return False
        try:
            return self._poll_once(force)
        finally:
            self._analysis_lock.release()

    def _poll_once(self, force=False) -> bool:
        try:
            sequence = self.sequence()
        except Exception as error:
            self._diagnose_error(error)
            return False
        if not force and (not sequence or sequence == self._last_sequence):
            return False
        if sequence != self._empty_retry_sequence:
            self.last_event_at = time.time()
            self._trace("clipboard sequence changed")
        try:
            text = self.read_text()
        except Exception as error:
            self._diagnose_error(error)
            return False
        self._last_error_signature = None
        # EVE can keep the clipboard locked for a short moment after its
        # "Copied N entries" toast.  Do not consume that sequence until text
        # has actually become readable; retry briefly without log spam.
        if not text:
            if self._empty_retry_sequence != sequence:
                self._empty_retry_sequence, self._empty_retry_count = sequence, 0
                if self.on_diagnostic:
                    self.on_diagnostic("clipboard_empty", 0)
            self._empty_retry_count += 1
            # Some EVE client/overlay combinations publish the sequence first
            # and make clipboard data available several seconds afterwards.
            # Keep one sequence eligible for a bounded 18 seconds.
            if self._empty_retry_count >= 24:
                self._last_sequence = sequence
                self.last_error = "Clipboard text unavailable after retries"
                self._trace("FAILED · clipboard · text unavailable after retries")
                if force:
                    self._deliver({"ok": False, "state": "error", "reason": self.last_error})
            return False
        self._empty_retry_sequence, self._empty_retry_count = None, 0
        self._last_sequence = sequence
        self._trace(f"text detected · {len(text)} chars")
        names = parse_local_names(text)
        self.last_candidate_count = len(names)
        self._trace(f"candidate pilots · {len(names)}")
        if not names:
            if self.on_diagnostic:
                rows = sum(1 for row in str(text).replace("\r", "\n").split("\n") if row.strip())
                self.on_diagnostic("clipboard_rejected", rows)
            self._trace("ignored · insufficient pilot list")
            if force:
                self._deliver({"ok": False, "state": "error", "reason": "Insufficient pilot list: copy at least two Local names."})
            return False
        fingerprint = local_fingerprint(names)
        if not force and fingerprint == self._last_fingerprint:
            return False
        if self.on_diagnostic:
            self.on_diagnostic("local_detected", len(names))
        self._trace("analysis started")
        self.last_error = None
        updates = []
        def deliver(result):
            updates.append(result)
            self._deliver(result)
        try:
            result = self.analyzer.analyze("\n".join(names), on_update=deliver)
            if isinstance(result, dict) and (not updates or result != updates[-1]):
                deliver(result)
            result = result if isinstance(result, dict) else (updates[-1] if updates else {})
            if not result.get("ok"):
                self.last_error = result.get("reason", "Analysis returned no result")
                self._trace("FAILED · analysis · " + self.last_error)
                return False
        except Exception as exc:
            self.last_error = f"Analysis unavailable ({type(exc).__name__})"
            self._trace("FAILED · analysis/callback · " + self.last_error)
            self._deliver({"ok": False, "state": "error", "reason": self.last_error})
            return False
        self._last_fingerprint = fingerprint
        self.last_analysis_at = time.time()
        self._trace(f"valid pilots · {result.get('resolved', len(names))}")
        self._trace(f"analysis complete · {result.get('total', len(names))} pilots")
        return True

    def run(self) -> None:
        try:
            self._last_sequence = self.sequence()
        except Exception as error:
            self._diagnose_error(error)
        while not self._stop.wait(self.poll_seconds):
            try:
                self.poll_once()
            except Exception as error:
                # Keep the worker alive after an unexpected Windows/ctypes
                # failure.  The UI receives one concise, redacted diagnostic.
                self._diagnose_error(error)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="mmd-local-clipboard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
