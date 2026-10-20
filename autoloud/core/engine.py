"""The engine: capture → meter → controller → volume commands.

Runs two threads: the backend's capture callback (audio thread, meter only)
and a control loop (~50 Hz) that applies gain commands and polls sessions.
The UI reads an immutable ``EngineState`` snapshot.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from autoloud.backends.base import AudioSession, Backend, OutputDevice
from autoloud.core.controller import GainCommand, GainController
from autoloud.core.profiles import AppConfig, DeviceProfile
from autoloud.dsp.loudness import LoudnessReading, MIN_LUFS, StreamingLoudnessMeter

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EngineState:
    running: bool = False
    enabled: bool = False
    calibrating: bool = False
    device: OutputDevice | None = None
    profile: DeviceProfile | None = None
    reading: LoudnessReading = field(default_factory=lambda: LoudnessReading(
        MIN_LUFS, MIN_LUFS, MIN_LUFS, MIN_LUFS, False))
    command: GainCommand | None = None
    sessions: list[AudioSession] = field(default_factory=list)
    master_db: float = 0.0
    error: str = ""


class Engine:
    CONTROL_HZ = 50
    SESSION_POLL_S = 0.5

    def __init__(self, backend: Backend, config: AppConfig):
        self.backend = backend
        self.config = config
        self.device: OutputDevice | None = None
        self.profile: DeviceProfile | None = None
        self.meter: StreamingLoudnessMeter | None = None
        self.controller: GainController | None = None
        self._state = EngineState()
        self._state_lock = threading.Lock()
        self._reading = EngineState().reading
        self._reading_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._calibrating = False
        self._applied_session_db: dict[str, float] = {}
        self._applied_master_db: float | None = None
        self._known_active: set[str] = set()
        self.user_offsets_db: dict[str, float] = {}   # manual per-app offsets (mixer)
        self.listeners: list[Callable[[EngineState], None]] = []
        self._last_cmd: GainCommand | None = None

    # ------------------------------------------------------------------
    @property
    def state(self) -> EngineState:
        with self._state_lock:
            return self._state

    def _set_state(self, **kw) -> None:
        with self._state_lock:
            self._state = replace(self._state, **kw)
            s = self._state
        for cb in list(self.listeners):
            try:
                cb(s)
            except Exception:
                log.exception("listener failed")

    # ------------------------------------------------------------------
    def start(self, device: OutputDevice | None = None) -> None:
        self.stop()
        try:
            self.backend.thread_init()
        except Exception:
            pass
        if device is None:
            device = self._pick_device()
        if device is None:
            self._set_state(error="No output device found")
            return
        self.device = device
        self.profile = self.config.profile_for(device.id, device.name)
        self.config.selected_device_id = device.id
        try:
            fs, ch = self.backend.start_capture(device, self._on_audio, block_ms=20)
        except Exception as e:
            log.exception("capture failed")
            self._set_state(error=f"Capture failed: {e}", running=False)
            return
        self.meter = StreamingLoudnessMeter(fs, ch, integration_window_s=self.config.params.window_s)
        self.controller = GainController(self.profile.targets(), self.config.params)
        self._applied_session_db.clear()
        self._applied_master_db = None
        self._running = True
        self._thread = threading.Thread(target=self._control_loop, daemon=True, name="autoloud-control")
        self._thread.start()
        self._set_state(running=True, device=device, profile=self.profile,
                        enabled=self.config.enabled, error="")

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            self.backend.stop_capture()
        except Exception:
            pass
        self._restore_volumes()
        self._set_state(running=False)

    def _pick_device(self) -> OutputDevice | None:
        devs = self.backend.list_output_devices()
        for d in devs:
            if d.id == self.config.selected_device_id:
                return d
        return self.backend.default_output_device() or (devs[0] if devs else None)

    def set_enabled(self, on: bool) -> None:
        self.config.enabled = on
        if not on:
            self._restore_volumes()
        elif self.controller is not None:
            self.controller.enter_idle(time.perf_counter())
        self._set_state(enabled=on)

    def apply_profile(self, profile: DeviceProfile) -> None:
        self.profile = profile
        if self.controller is not None:
            self.controller.retune(profile.targets())
        self._set_state(profile=profile)

    def apply_params(self) -> None:
        if self.controller is not None:
            self.controller.params = self.config.params
        if self.meter is not None:
            self.meter.block_powers = type(self.meter.block_powers)(
                self.meter.block_powers, maxlen=max(1, int(self.config.params.window_s / 0.1)))

    # ------------------------------------------------------------------
    # audio thread
    def _on_audio(self, x: np.ndarray) -> None:
        if self.meter is None:
            return
        r = self.meter.process(x)
        with self._reading_lock:
            self._reading = r

    # ------------------------------------------------------------------
    # control thread
    def _control_loop(self) -> None:
        try:
            self.backend.thread_init()
        except Exception:
            pass
        period = 1.0 / self.CONTROL_HZ
        next_poll = 0.0
        sessions: list[AudioSession] = []
        while self._running:
            t0 = time.perf_counter()
            with self._reading_lock:
                r = self._reading
            now = t0
            try:
                if now >= next_poll:
                    next_poll = now + self.SESSION_POLL_S
                    sessions = self.backend.list_sessions(self.device)
                    active = {s.key for s in sessions if s.active and not s.is_self}
                    if active - self._known_active and self.controller is not None:
                        self.controller.notify_source_change(now)
                    self._known_active = active
                    try:
                        master_db = self.backend.get_master_db(self.device)
                    except Exception:
                        master_db = 0.0
                    self._set_state(sessions=sessions, master_db=master_db)

                cmd = None
                if self.controller is not None and self.config.enabled and not self._calibrating:
                    cmd = self.controller.update(r, now, self.meter)
                    self._apply(cmd, sessions)
                    self._last_cmd = cmd
                self._set_state(reading=r, command=cmd, calibrating=self._calibrating,
                                enabled=self.config.enabled)
            except Exception as e:
                log.exception("control loop")
                self._set_state(error=str(e))
            dt = period - (time.perf_counter() - t0)
            if dt > 0:
                time.sleep(dt)

    def _apply(self, cmd: GainCommand, sessions: list[AudioSession]) -> None:
        # per-session attenuation (+ manual mixer offsets)
        for s in sessions:
            if s.is_self:
                continue
            db = cmd.session_gain_db + self.user_offsets_db.get(s.name, 0.0)
            db = min(0.0, db)
            prev = self._applied_session_db.get(s.key)
            if prev is None or abs(prev - db) > 0.05:
                self.backend.set_session_volume(self.device, s.key, 10 ** (db / 20))
                self._applied_session_db[s.key] = db
        # master boost above the calibration reference
        target_master = self.profile.master_ref_db + cmd.master_offset_db
        if self._applied_master_db is None or abs(self._applied_master_db - target_master) > 0.05:
            try:
                lo, hi = self.backend.master_range_db(self.device)
                self.backend.set_master_db(self.device, max(lo, min(hi, target_master)))
                self._applied_master_db = target_master
            except Exception:
                log.exception("set master")

    def _restore_volumes(self) -> None:
        if self.device is None:
            return
        try:
            for s in self.backend.list_sessions(self.device):
                if not s.is_self:
                    self.backend.set_session_volume(self.device, s.key, 1.0)
            if self.profile is not None and self.profile.calibrated:
                self.backend.set_master_db(self.device, self.profile.master_ref_db)
        except Exception:
            log.exception("restore volumes")
        self._applied_session_db.clear()
        self._applied_master_db = None

    # ------------------------------------------------------------------
    # calibration support
    def begin_calibration(self) -> None:
        """Suspend automatic control and restore neutral volumes."""
        self._calibrating = True
        self._restore_volumes()
        if self.meter is not None:
            self.meter.reset()
        self._set_state(calibrating=True)

    def end_calibration(self) -> None:
        self._calibrating = False
        if self.controller is not None:
            self.controller.retune(self.profile.targets())
            self.controller.enter_idle(time.perf_counter())
        self._set_state(calibrating=False, profile=self.profile)

    def measured_lufs(self) -> float:
        """Short-term loopback loudness (used while the user holds a fader)."""
        with self._reading_lock:
            return self._reading.short_term

    def play(self, audio: np.ndarray, fs: int, gain_fn: Callable[[], float], loop: bool = True):
        return self.backend.play(self.device, audio, fs, gain_fn, loop)

    def probe_master_visibility(self, wait: Callable[[float], None] = time.sleep,
                                step_db: float = 6.0) -> bool | None:
        """While a steady signal plays: briefly move the master volume and see
        whether the loopback measurement follows. Returns True (software
        volume, visible), False (hardware volume, hidden) or None (unclear).
        Restores the master volume afterwards. Blocks ~2.5 s; ``wait`` can
        pump a GUI event loop."""
        dev = self.device
        try:
            m0 = self.backend.get_master_db(dev)
            lo, hi = self.backend.master_range_db(dev)
        except Exception:
            return None
        delta = -step_db if m0 - step_db >= lo else min(step_db, hi - m0)
        if abs(delta) < 2.0:
            return None

        def avg_momentary(seconds: float) -> float:
            vals = []
            t_end = time.perf_counter() + seconds
            while time.perf_counter() < t_end:
                with self._reading_lock:
                    vals.append(self._reading.momentary)
                wait(0.1)
            vals = [v for v in vals if v > -70]
            return sum(vals) / len(vals) if vals else MIN_LUFS

        before = avg_momentary(1.0)
        try:
            self.backend.set_master_db(dev, m0 + delta)
            wait(0.6)
            after = avg_momentary(1.0)
        finally:
            try:
                self.backend.set_master_db(dev, m0)
            except Exception:
                log.exception("restore master after probe")
        if before <= -70 or after <= -70:
            return None
        change = after - before
        log.info("master visibility probe: delta=%+.1f dB -> loopback changed %+.1f dB", delta, change)
        if abs(change - delta) < abs(delta) * 0.5:
            return True
        if abs(change) < abs(delta) * 0.25:
            return False
        return None
