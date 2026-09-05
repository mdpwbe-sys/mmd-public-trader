"""Small native Windows soundtrack controller for optional New Eden music."""
from __future__ import annotations

import math
import os
import threading
import time
from pathlib import Path


class EveMapMusic:
    """MCI-backed, session-only music with fade and proximity ducking."""

    _ALIAS = "mmd_new_eden_music"

    def __init__(self, source=None, *, command_sender=None, clock=time.monotonic):
        self.source = Path(source) if source else None
        self._send_command = command_sender or self._windows_command
        self._clock = clock
        self._lock = threading.RLock()
        self._opened = self._playing = self._paused = self._visible = self._manually_paused = False
        self._volume, self._duck_multiplier, self._output_volume = 0.30, 1.0, 0.0
        self._duck_until = 0.0
        self._fade_generation = self._duck_generation = 0
        self._error = None

    @property
    def available(self):
        return bool(os.name == "nt" and self.source and self.source.is_file())

    @staticmethod
    def duck_multiplier(distance_jumps):
        distance = max(0, int(distance_jumps or 0))
        return 0.15 if distance <= 1 else 0.35 if distance <= 3 else 0.60

    def state(self):
        with self._lock:
            return {"ok": self.available and not self._error, "available": self.available, "playing": self._playing,
                    "visible": self._visible, "manually_paused": self._manually_paused, "volume": self._volume,
                    "duck_multiplier": self._duck_multiplier, "error": self._error}

    def set_visible(self, visible):
        with self._lock:
            self._visible = bool(visible)
            if self._visible and not self._manually_paused: self._start_locked(2500)
            elif not self._visible: self._fade_locked(0.0, 600, pause_after=True)
            return self.state()

    def toggle(self):
        with self._lock:
            if self._playing:
                self._manually_paused = True; self._fade_locked(0.0, 180, pause_after=True)
            else:
                self._manually_paused = False
                if self._visible: self._start_locked(180)
            return self.state()

    def set_volume(self, value):
        with self._lock:
            self._volume = max(0.0, min(1.0, float(value)))
            if self._playing: self._fade_locked(self._effective_volume_locked(), 120)
            return self.state()

    def duck(self, distance_jumps):
        with self._lock:
            if not self.available: return self.state()
            self._duck_multiplier = min(self._duck_multiplier, self.duck_multiplier(distance_jumps))
            self._duck_until = max(self._duck_until, self._clock() + 3.0)
            if self._playing: self._fade_locked(self._effective_volume_locked(), 250)
            self._duck_generation += 1; generation = self._duck_generation
            threading.Thread(target=self._restore_duck, args=(generation,), name="mmd-music-duck", daemon=True).start()
            return self.state()

    def _restore_duck(self, generation):
        while True:
            with self._lock: remaining = self._duck_until - self._clock()
            if remaining <= 0: break
            time.sleep(min(remaining, 0.25))
        with self._lock:
            if generation != self._duck_generation or self._clock() < self._duck_until: return
            self._duck_multiplier = 1.0
            if self._playing: self._fade_locked(self._effective_volume_locked(), 2000)

    def _start_locked(self, fade_ms):
        if not self.available: return False
        if not self._opened:
            if not self._command(f'open "{self.source}" type mpegvideo alias {self._ALIAS}'): return False
            self._opened = True
        if not self._playing:
            command = f"resume {self._ALIAS}" if self._paused else f"play {self._ALIAS} repeat"
            if not self._command(command): return False
            self._playing = True; self._paused = False
        self._set_output_volume_locked(0.0); self._fade_locked(self._effective_volume_locked(), fade_ms)
        return True

    def _effective_volume_locked(self): return self._volume * self._duck_multiplier

    def _fade_locked(self, target, duration_ms, pause_after=False):
        if not self._opened: return
        self._fade_generation += 1; generation = self._fade_generation; start = self._output_volume
        thread = threading.Thread(target=self._fade, args=(generation, start, max(0.0, min(1.0, float(target))), max(1, int(duration_ms)), pause_after), name="mmd-music-fade", daemon=True)
        thread.start()

    def _fade(self, generation, start, target, duration_ms, pause_after):
        steps = max(1, int(math.ceil(duration_ms / 50)))
        for step in range(steps + 1):
            with self._lock:
                if generation != self._fade_generation: return
                self._set_output_volume_locked(start + (target - start) * step / steps)
            if step < steps: time.sleep(duration_ms / steps / 1000)
        with self._lock:
            if generation != self._fade_generation: return
            if pause_after and not self._visible:
                self._command(f"pause {self._ALIAS}"); self._playing = False; self._paused = True

    def _set_output_volume_locked(self, volume):
        self._output_volume = max(0.0, min(1.0, volume))
        self._command(f"setaudio {self._ALIAS} volume to {round(self._output_volume * 1000)}")

    def _command(self, command):
        try:
            result = self._send_command(command)
            if result not in (None, 0): self._error = f"Windows audio error {result}"; return False
            return True
        except Exception as exc:
            self._error = f"Windows audio unavailable: {exc}"; return False

    @staticmethod
    def _windows_command(command):
        if os.name != "nt": return 1
        import ctypes
        return ctypes.windll.winmm.mciSendStringW(command, None, 0, 0)
