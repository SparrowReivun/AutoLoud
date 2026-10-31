"""The engine: capture → meter → controller → volume commands.

Runs two threads: the backend's capture callback (audio thread, meter only)
and a control loop (~50 Hz) that applies gain commands and polls sessions.
The UI reads an immutable ``EngineState`` snapshot.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from autoloud.backends.base import AudioSession, Backend, OutputDevice
from autoloud.core.controller import GainCommand, GainController
from autoloud.core.profiles import AppConfig, DeviceProfile
from autoloud.dsp.loudness import LoudnessReading, MIN_LUFS, StreamingLoudnessMeter
from autoloud.core.works import WorkTracker, WorkInfo

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
    works: dict[str, WorkInfo] = field(default_factory=dict)   # app name -> current work
    media_available: bool = False
    safety_active: bool = False      # protection cut in force (notice should be showing)
    safety_count: int = 0            # increments on every new cut (UI shows the notice on change)
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
        self._extra_visible_db = 0.0                   # mixer gain the loopback contains
        self._auto_share_db = 0.0                      # 10*log10(power share of auto apps)
        self._holds: dict[str, float] = {}             # app -> bus gain frozen by a manual fader move
        self._safety_count = 0
        self.media = self._make_media_provider()
        self.works = WorkTracker(silence_s=config.params.work_silence_s,
                                 silence_peak=config.params.work_silence_peak,
                                 media=self.media)
        self.listeners: list[Callable[[EngineState], None]] = []
        self._last_cmd: GainCommand | None = None

    @staticmethod
    def _make_media_provider():
        import sys
        if not sys.platform.startswith("win") or os.environ.get("AUTOLOUD_MOCK"):
            return None
        try:
            from autoloud.backends.media_windows import WindowsMediaProvider
            p = WindowsMediaProvider()
            return p if p.available else None
        except Exception:
            log.exception("media provider init failed")
            return None

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
        self.meter = StreamingLoudnessMeter(fs, ch, integration_window_s=60.0)
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
        """Bus auto-loudness switch. The per-app mixer keeps working either way."""
        self.config.enabled = on
        if on and self.controller is not None:
            self.controller.enter_idle(time.perf_counter())
        self._set_state(enabled=on)

    def mixer_changed(self) -> None:
        """Called by the UI after a fader / per-app auto switch changed."""
        self._applied_session_db.clear()   # force a re-apply on the next tick

    def apply_profile(self, profile: DeviceProfile) -> None:
        self.profile = profile
        if self.controller is not None:
            self.controller.retune(profile.targets())
        self._set_state(profile=profile)

    def apply_params(self) -> None:
        if self.controller is not None:
            self.controller.params = self.config.params
        self.works.silence_s = self.config.params.work_silence_s
        self.works.silence_peak = self.config.params.work_silence_peak

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
                    self._known_active = active
                    self._update_auto_share(sessions)
                    auto_active = [s.name for s in sessions if s.active and not s.is_self
                                   and self._follows_bus(s.name)]
                    for app_name, work in self.works.update(sessions, now):
                        log.info("new work in %s: #%d %s (%s)", app_name, work.index,
                                 work.label() or "-", work.source)
                        self._holds.pop(app_name, None)        # a manual hold ends with the work
                        if self.controller is not None and self.config.enabled and self._follows_bus(app_name):
                            mix = self.config.mix_for(app_name)
                            alone = set(auto_active) <= {app_name}
                            self.controller.notify_new_work(now, self.meter, mix.last_gain_db, alone)
                    # remember the bus gain an app ends up with when it plays alone
                    if (self._last_cmd is not None and self._last_cmd.phase.value == "steady"
                            and len(auto_active) == 1 and self.config.enabled):
                        mix = self.config.mix_for(auto_active[0])
                        if mix.last_gain_db is None or abs(mix.last_gain_db - self._last_cmd.total_db) > 0.25:
                            mix.last_gain_db = round(self._last_cmd.total_db, 2)
                            self._dirty = True
                    try:
                        master_db = self.backend.get_master_db(self.device)
                    except Exception:
                        master_db = 0.0
                    self._check_master_moved(master_db)
                    self._set_state(sessions=sessions, master_db=master_db,
                                    works=self.works.all_current(),
                                    media_available=self.media is not None)

                cmd = None
                if not self._calibrating:
                    if self.controller is not None and self.config.enabled:
                        rr = self._reading_for_auto_apps(r)
                        cmd = self.controller.update(rr, now, self.meter, self._extra_visible_db)
                        self._last_cmd = cmd
                        if cmd.safety_triggered:
                            self._safety_count += 1
                            log.info("safety cut #%d: perceived %.1f LUFS", self._safety_count, cmd.perceived_lufs)
                    self._apply(cmd, sessions)
                if getattr(self, "_dirty", False) and now - getattr(self, "_last_save", 0.0) > 5.0:
                    self._dirty = False; self._last_save = now
                    try:
                        self.config.save()
                    except Exception:
                        log.exception("config save")
                self._set_state(reading=r, command=cmd, calibrating=self._calibrating,
                                enabled=self.config.enabled,
                                safety_active=(cmd is not None and cmd.phase.value == "safety"),
                                safety_count=self._safety_count)
            except Exception as e:
                log.exception("control loop")
                self._set_state(error=str(e))
            dt = period - (time.perf_counter() - t0)
            if dt > 0:
                time.sleep(dt)

    def _follows_bus(self, app_name: str) -> bool:
        """Does the bus auto gain currently apply to this app?"""
        mix = self.config.apps.get(app_name)
        return (mix is None or mix.auto) and app_name not in self._holds

    def user_touched_fader(self, app_name: str) -> None:
        """Rule 7: a manual fader move freezes that app's auto gain until its
        current work ends."""
        if self.config.enabled and self._last_cmd is not None and self._follows_bus(app_name):
            self._holds[app_name] = self._last_cmd.total_db
        self.mixer_changed()

    def resume_after_safety(self) -> None:
        """The protection notice closed (or 5 s passed): re-adjust from here."""
        if self.controller is not None:
            self.controller.resume_after_safety(time.perf_counter(), self.meter)

    def _check_master_moved(self, master_db: float) -> None:
        """Rule 7: the user moved the Windows master volume -> that is the new
        comfort level. Re-base the calibration reference so the controller
        does not fight it (and does not need to re-learn anything)."""
        if self._calibrating or self._applied_master_db is None or self.profile is None:
            return
        delta = master_db - self._applied_master_db
        if abs(delta) < 0.5:
            return
        p = self.profile
        p.master_ref_db += delta
        if p.master_visible_in_loopback:      # loopback moved too: targets move with it
            p.a_lufs += delta; p.b_lufs += delta
        self._applied_master_db = master_db
        if self.controller is not None:
            self.controller.retune(p.targets())
        self._dirty = True
        log.info("master volume moved by user (%+.1f dB): new reference %.1f dB", delta, p.master_ref_db)
        self._set_state(profile=p)

    def _update_auto_share(self, sessions: list[AudioSession]) -> None:
        """Estimate which share of the mix comes from apps that follow the bus
        auto gain, from the per-session peak meters (power-weighted, smoothed).
        Apps with auto switched off are then discounted from the measurement so
        a loud call with auto off does not drag the bus gain down."""
        p_auto = p_all = 0.0
        for s in sessions:
            if s.is_self or not s.active:
                continue
            pw = float(s.peak) ** 2
            p_all += pw
            if self._follows_bus(s.name):
                p_auto += pw
        if p_all <= 1e-9:
            target = 0.0
        else:
            target = 10.0 * math.log10(max(1e-4, p_auto / p_all))   # <= 0 dB, floor -40
        # smooth over ~1.5 s (polled every 0.5 s)
        self._auto_share_db += (target - self._auto_share_db) * 0.3

    def _reading_for_auto_apps(self, r: LoudnessReading) -> LoudnessReading:
        d = self._auto_share_db
        if abs(d) < 0.05:
            return r
        from dataclasses import replace as _rep
        m = r.momentary + d
        return _rep(r, momentary=m, short_term=r.short_term + d, integrated=r.integrated + d,
                    has_signal=(m > -70.0))

    def _apply(self, cmd: GainCommand | None, sessions: list[AudioSession]) -> None:
        """Turn (bus auto gain, per-app faders, per-app auto switches) into
        session volumes (<= 0 dB each) and one master offset (>= 0 dB).

        desired_i = (G if bus auto on and app.auto else 0) + fader_i
        P         = max(0, max_i desired_i), capped by the master headroom
        session_i = min(0, desired_i - P);   master = reference + P
        So a fader above 0 dB is realised by raising the master and pulling
        the other apps down by the same amount.
        """
        G = cmd.total_db if cmd is not None else 0.0
        cfg = self.config
        apps = [s for s in sessions if not s.is_self]
        desired: dict[str, float] = {}
        for s in apps:
            mix = cfg.apps.get(s.name)
            off = mix.offset_db if mix else 0.0
            if s.name in self._holds:
                base = self._holds[s.name] if cmd is not None else 0.0
            else:
                base = G if (cmd is not None and self._follows_bus(s.name)) else 0.0
            desired[s.key] = base + off
        active_desired = [desired[s.key] for s in apps if s.active] or [G]
        P = max(0.0, max(active_desired))
        try:
            lo, hi = self.backend.master_range_db(self.device)
        except Exception:
            lo, hi = -65.0, 0.0
        P = min(P, cfg.params.max_master_boost_db, max(0.0, hi - self.profile.master_ref_db))

        # loopback-visible part of the gain that is NOT the controller's own,
        # peak-weighted over the active auto apps -> fed back to the controller
        own_visible = G if self.profile.master_visible_in_loopback else min(0.0, G)
        wsum = xsum = 0.0
        for s in apps:
            if not s.active or not self._follows_bus(s.name) or cmd is None:
                continue
            applied = min(0.0, desired[s.key] - P) + (P if self.profile.master_visible_in_loopback else 0.0)
            w = max(1e-3, s.peak)
            wsum += w; xsum += w * (applied - own_visible)
        self._extra_visible_db = (xsum / wsum) if wsum > 0 else 0.0

        for s in apps:
            db = min(0.0, desired[s.key] - P)
            prev = self._applied_session_db.get(s.key)
            if prev is None or abs(prev - db) > 0.05:
                self.backend.set_session_volume(self.device, s.key, 10 ** (db / 20))
                self._applied_session_db[s.key] = db
        target_master = self.profile.master_ref_db + P
        if self._applied_master_db is None or abs(self._applied_master_db - target_master) > 0.05:
            try:
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
