"""The engine: capture → meter → controller → volume commands.

Runs two threads: the backend's capture callback (audio thread, meter only)
and a control loop (~50 Hz) that applies gain commands and polls sessions.
The UI reads an immutable ``EngineState`` snapshot.
"""
from __future__ import annotations

import logging
import math
import os
from collections import deque
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from autoloud.backends.base import AudioSession, Backend, OutputDevice
from autoloud.core.controller import ControllerParams, GainCommand, GainController, Phase
from autoloud.core.profiles import AppConfig, DeviceProfile
from autoloud.dsp.loudness import LoudnessReading, MIN_LUFS, StreamingLoudnessMeter
from autoloud.core.platforms import PlatformModel
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
    activity: dict = field(default_factory=dict)       # controller.activity(): ramp / listen progress
    events: list = field(default_factory=list)         # [(wall_time, kind, data)] newest last
    safety_active: bool = False      # protection cut in force (notice should be showing)
    safety_count: int = 0            # increments on every new cut (UI shows the notice on change)
    error: str = ""
    source: tuple = ("", None, None, None)   # (sole app, its source LUFS, platform lo, platform hi)
    mode_name: str = ""                       # preset the bus is running with right now


B_MUTE_LUFS = -90.0   # the P slider's bottom: auto apps are muted outright


class Engine:
    CONTROL_HZ = 50
    SESSION_POLL_S = 0.25   # a session Windows just created plays at 100 % until the next poll: keep that short

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
        self._last_audio_t = time.perf_counter()
        self._last_capture_retry = -1e9
        self._capture_failures = 0
        self._thread: threading.Thread | None = None
        self._running = False
        self._calibrating = False
        self._applied_session_db: dict[str, float] = {}
        self._applied_master_db: float | None = None
        self._known_active: set[str] = set()
        self._extra_visible_db = 0.0                   # mixer gain the loopback contains
        self._auto_share_db = 0.0                      # 10*log10(power share of auto apps)
        self._holds: dict[str, float] = {}             # app -> bus gain frozen by a manual fader move
        self._duty: dict[str, deque] = {}              # app -> (t, sounding) samples while active (tool detector)
        self._boost_cap = 1e9                          # master headroom above the reference, refreshed each poll
        self._last_sessions: list[AudioSession] = []
        self._sound_since: dict[str, float] = {}        # app -> when it started sounding (this stretch)
        self._sound_last: dict[str, float] = {}         # app -> last time it was sounding
        self._app_cut: dict[str, float] = {}            # app -> transient attenuation for a loud intrusion (dB > 0)
        self._last_safety_t = -1e9
        self._events: deque = deque(maxlen=14)         # engine-level + controller events, wall clock
        self._ctl_events_seen = 0
        self._safety_count = 0
        self.media = self._make_media_provider()
        self.works = WorkTracker(silence_s=config.params.work_silence_s,
                                 silence_peak=config.params.work_silence_peak,
                                 media=self.media)
        self.listeners: list[Callable[[EngineState], None]] = []
        self._last_cmd: GainCommand | None = None
        self._platforms: dict[str, PlatformModel] = {}   # app -> learned source-loudness picture
        self._param_override: str = ""                   # preset name the sole app switched the bus to ("" = dashboard mode)
        self._source_now: tuple[str, float | None, float | None, float | None] = ("", None, None, None)

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
        # Switching devices: do not blast every session back to 100 % on the way
        # (that is the pop people hear); the new device's sessions get the parked
        # gain on the first tick instead.
        self.stop(restore=self._thread is None)
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
        self.config.enabled = True            # the bus is always on; "modes" are presets
        try:
            fs, ch = self.backend.start_capture(device, self._on_audio, block_ms=20)
        except Exception as e:
            log.exception("capture failed")
            self._set_state(error=f"Capture failed: {e}", running=False)
            return
        self.meter = StreamingLoudnessMeter(fs, ch, integration_window_s=60.0)
        self._last_audio_t = time.perf_counter()
        self.controller = GainController(self.profile.targets(), self.config.params)
        self._applied_session_db.clear()
        self._applied_master_db = None
        self._sessions_parked = False
        self._running = True
        self._thread = threading.Thread(target=self._control_loop, daemon=True, name="autoloud-control")
        self._thread.start()
        self._set_state(running=True, device=device, profile=self.profile,
                        enabled=self.config.enabled, error="")

    def stop(self, restore: bool = True) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=6.0)       # a tick can sit in a 5 s COM timeout
            if self._thread.is_alive():
                log.warning("control thread still busy at stop; volumes restored anyway (ticks are now no-ops)")
            self._thread = None
        try:
            self.backend.stop_capture()
        except Exception:
            pass
        if restore:
            self._restore_volumes(leaving=True)
        else:
            self._applied_session_db.clear()
            self._applied_master_db = None
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
            self.controller.params = (self.config.params_for_preset(self._param_override)
                                      if self._param_override else self.config.params)
        self.works.silence_s = self.config.params.work_silence_s
        self.works.silence_peak = self.config.params.work_silence_peak

    # ------------------------------------------------------------------
    # audio thread
    def _on_audio(self, x: np.ndarray) -> None:
        if self.meter is None:
            return
        # the gate follows the gain the loopback already contains (session cut, visible master)
        cmd0 = self._last_cmd
        if cmd0 is not None and not self.config.monitor_only:
            vis = cmd0.master_offset_db if (self.profile is not None and self.profile.master_visible_in_loopback) else 0.0
            self.meter.gate_offset_db = min(0.0, cmd0.session_gain_db + vis)
        else:
            self.meter.gate_offset_db = 0.0
        r = self.meter.process(x)
        with self._reading_lock:
            self._reading = r
            self._last_audio_t = time.perf_counter()

    # ------------------------------------------------------------------
    # capture watchdog
    CAPTURE_STALL_S = 2.0       # no loopback data this long while an app sounds = the stream died
    CAPTURE_RETRY_S = 5.0
    _SILENT = LoudnessReading(momentary=MIN_LUFS, short_term=MIN_LUFS, integrated=MIN_LUFS,
                              peak_dbfs=-100.0, has_signal=False)

    def _memory_watch(self, now: float) -> None:
        """Every 10 min: the process's memory in autoloud.log - private bytes,
        not just RSS (see core/memdiag.py for why) - and a warning in the
        activity log once the private bytes look like a leak."""
        w = getattr(self, "_memwatch", None)
        if w is None:
            from autoloud.core.memdiag import MemoryWatch
            w = self._memwatch = MemoryWatch(
                on_warn=lambda smp: self._event("memory_high", mb=smp.private_mb))
        w.tick(now)

    def _capture_watch(self, r: LoudnessReading, sessions: list[AudioSession], now: float) -> LoudnessReading:
        """The WASAPI loopback stream can die quietly (a Bluetooth headset
        re-negotiating, a device change): the callback just stops and the
        last reading would stay on screen - and in the rules - for ever.
        A stale reading is treated as silence, and while some app's own
        meter says it is sounding, the capture is restarted."""
        stale = now - self._last_audio_t > self.CAPTURE_STALL_S
        if not stale:
            return r
        if self._sounding(sessions) and now - self._last_capture_retry > self.CAPTURE_RETRY_S:
            self._last_capture_retry = now
            log.warning("loopback capture stalled for %.1f s while apps sound: restarting it", now - self._last_audio_t)
            self._event("capture_restart")
            try:
                if self._capture_failures >= 1 and hasattr(self.backend, "_reset_pa"):
                    self.backend._reset_pa()          # the last reopen failed: the device list may be stale
                dev = self._pick_device() or self.device
                fs, ch = self.backend.start_capture(dev, self._on_audio, block_ms=20)
                self._capture_failures = 0
                if self.meter is None or self.meter.fs != fs or self.meter.channels != ch:
                    self.meter = StreamingLoudnessMeter(fs, ch, integration_window_s=60.0)
                else:
                    self.meter.reset()
                if dev.id != self.device.id:
                    log.info("capture moved to %r", dev.name)
                    self.device = dev
                    self.profile = self.config.profile_for(dev.id, dev.name)
                    if self.controller is not None:
                        self.controller.retune(self.profile.targets())
                    self._set_state(device=dev, profile=self.profile)
                self._last_audio_t = now          # give the new stream its grace period
            except Exception:
                self._capture_failures += 1
                log.exception("capture restart failed (%d in a row)", self._capture_failures)
        return self._SILENT

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
            r = self._capture_watch(r, sessions, now)
            self._memory_watch(now)
            try:
                if now >= next_poll:
                    next_poll = now + self.SESSION_POLL_S
                    sessions = self.backend.list_sessions(self.device)
                    for s_ in sessions:            # apps with a default fader (System sounds) get it on first sight
                        if s_.name in self.config.DEFAULT_APP_OFFSETS and s_.name not in self.config.apps:
                            self.config.mix_for(s_.name); self._dirty = True
                    self._last_sessions = sessions
                    if self.meter_log is not None:
                        sole, sm, ss = self.source_loudness(sessions, r, self._last_cmd)
                        self.meter_log.apps(sessions, self._last_cmd.total_db if self._last_cmd else 0.0,
                                            {k: v.offset_db for k, v in self.config.apps.items()},
                                            sole, sm, ss)
                    self._source_poll(sessions, r, now)
                    self._startup_master()
                    for s in self._sounding(sessions):
                        if now - self._sound_last.get(s.name, -1e9) > 1.0:
                            self._sound_since[s.name] = now          # a new stretch of sound
                        self._sound_last[s.name] = now
                    for name in list(self._app_cut):                 # intrusion over: let the app back up
                        if now - self._sound_last.get(name, -1e9) > 2.0:
                            self._app_cut.pop(name, None)
                            self._event("app_cut_end", app=self.config.display_name(name))
                            self.mixer_changed()
                    active = {s.key for s in sessions if s.active and not s.is_self}
                    self._known_active = active
                    self._update_auto_share(sessions)
                    self._detect_tool_apps(sessions, now)
                    self._boost_cap = self._boost_cap_db()           # one COM call per poll, not per tick
                    auto_active = [s.name for s in sessions if s.active and not s.is_self
                                   and self._follows_bus(s.name)]
                    for app_name, work in self.works.update(
                            [x for x in sessions if x.name not in self.SYSTEM_APPS], now):
                        log.info("new work in %s: #%d %s (%s)", app_name, work.index,
                                 work.label() or "-", work.source)
                        self._event("work", app=self.config.display_name(app_name), index=work.index,
                                    label=work.label(), source=work.source)
                        self._holds.pop(app_name, None)        # a manual hold ends with the work
                        # Only a work that is actually sounding re-targets the bus. A media-info
                        # change in a silent app (a browser tab opened behind a game) used to jump
                        # the bus to that app's remembered gain and bury whatever was playing.
                        snd_now = {x.name for x in self._sounding(sessions)}
                        if app_name not in snd_now:
                            self._event("work_silent", app=self.config.display_name(app_name))
                            continue
                        # ... and only the app that dominates the mix: a notification blip in a
                        # chat app behind the music must not re-target the bus to that app's gain
                        dom = self._dominant(sessions)
                        if dom is not None and dom.name != app_name:
                            self._event("work_behind", app=self.config.display_name(app_name),
                                        loud=self.config.display_name(dom.name))
                            continue
                        if self.controller is not None and self.config.enabled and self._follows_bus(app_name):
                            mix = self.config.mix_for(app_name)
                            self.controller.notify_new_work(now, self.meter, mix.last_gain_db)
                    # remember the bus gain every sounding auto app has (latest value wins, so a
                    # work that ends before settling still leaves its best estimate)
                    clean = (self._last_cmd is not None and self.config.enabled and not self.config.monitor_only and (
                        self._last_cmd.phase.value == "steady" or
                        (self._last_cmd.phase.value == "adjust" and now - self._last_safety_t > 20.0)))
                    if clean:                      # never a gain still bent by a protection cut
                        for name in auto_active:
                            mix = self.config.mix_for(name)
                            if mix.last_gain_db is None or abs(mix.last_gain_db - self._last_cmd.total_db) > 0.25:
                                mix.last_gain_db = round(self._last_cmd.total_db, 2)
                                self._dirty = True
                    try:
                        master_db = self.backend.get_master_db(self.device)
                    except Exception:
                        master_db = 0.0
                    self._last_master_db = master_db
                    try:
                        self._master_pct = float(self.backend.get_master_scalar(self.device)) * 100
                    except Exception:
                        pass
                    self._check_master_moved(master_db)
                    self._set_state(sessions=sessions, master_db=master_db,
                                    works=self.works.all_current(),
                                    media_available=self.media is not None)

                cmd = None
                if not self._calibrating:
                    if self.controller is not None and self.config.enabled and not self.config.monitor_only:
                        # (monitor only: the controller is not even consulted, so the adjust log
                        # and meters.csv carry no phantom decisions and the user's own moves are
                        # the only thing in the record)
                        rr = self._reading_for_auto_apps(r)
                        cap, start = self._onset_gains(sessions)
                        self.controller.boost_cap_db = self._boost_cap
                        cmd = self.controller.update(rr, now, self.meter, self._extra_visible_db,
                                                     cap, start)
                        self._last_cmd = cmd
                        if cmd.safety_triggered:
                            self._last_safety_t = now
                            self._safety_count += 1
                            log.info("safety cut #%d: perceived %.1f LUFS", self._safety_count, cmd.perceived_lufs)
                            self._attribute_cut(sessions, cmd, now)
                    self._apply(cmd, sessions)
                if getattr(self, "_dirty", False) and now - getattr(self, "_last_save", 0.0) > 5.0:
                    self._dirty = False; self._last_save = now
                    try:
                        self.config.save()
                    except Exception:
                        log.exception("config save")
                activity = {}
                if self.controller is not None and cmd is not None:
                    activity = self.controller.activity(now)
                    self._drain_controller_events(now)
                if self.meter_log is not None:
                    dom = self._dominant(sessions)
                    self.meter_log.meters(r, cmd, self.profile.b_lufs if self.profile else 0.0,
                                          getattr(self, "_last_master_db", 0.0), now,
                                          dom.name if dom is not None else "")
                self._set_state(reading=r, command=cmd, calibrating=self._calibrating,
                                enabled=self.config.enabled, activity=activity,
                                events=list(self._events), error="",
                                source=self._source_now,
                                mode_name=self._param_override or self.config.active_preset,
                                safety_active=(cmd is not None and cmd.phase.value == "safety"),
                                safety_count=self._safety_count)
            except TimeoutError:
                # the audio service did not answer in time: skip this tick, keep running
                self._set_state(error="audio service not answering")
            except Exception as e:
                log.exception("control loop")
                self._set_state(error=str(e))
            dt = period - (time.perf_counter() - t0)
            if dt > 0:
                time.sleep(dt)

    on_event = None      # optional sink(wall_time, kind, data, sounding_apps) -> None, e.g. the adjustment log
    meter_log = None     # optional MeterLog: raw measurements for analysis
    adjust_log = None    # optional AdjustLog (also wired as on_event)

    def dashboard_mode_slug(self) -> str:
        """ASCII tag of the dashboard mode for log file names (monitor / default / strong1 …)."""
        from autoloud.core.profiles import mode_slug
        return mode_slug("monitor" if self.config.monitor_only else self.config.active_preset)

    def rotate_logs(self) -> None:
        """The dashboard mode changed: every per-run log (adjust / meters / apps /
        volume) continues in a fresh file named after the new mode, so one file
        is always one condition. No-op when the mode tag did not change."""
        slug = self.dashboard_mode_slug()
        for lg in (self.meter_log, self.adjust_log):
            if lg is not None and getattr(lg, "mode", None) != slug:
                try:
                    lg.reopen(slug)
                except Exception:
                    log.exception("log rotate")
        self._event("logs_rotated", mode=slug)

    def _sounding_apps(self) -> list[str]:
        """Short description of the apps making sound right now (no volumes)."""
        out = []
        for s in self._last_sessions:
            if s.is_self or not s.active or s.peak <= 0.001:
                continue
            w = self.works.all_current().get(s.name)
            lab = (w.label() or f"#{w.index}") if w is not None else ""
            out.append(self.config.display_name(s.name) + (f" ({lab})" if lab else ""))
        return out

    def _emit(self, when: float, kind: str, data: dict) -> None:
        self._events.append((when, kind, data))
        if self.on_event is not None:
            try:
                self.on_event(when, kind, data, self._sounding_apps())
            except Exception:
                log.exception("event sink")

    def _event(self, kind: str, **data) -> None:
        self._emit(time.time(), kind, data)

    def _drain_controller_events(self, now: float) -> None:
        for seq, t_mono, kind, data in list(self.controller.events):
            if seq > self._ctl_events_seen:
                self._ctl_events_seen = seq
                self._emit(time.time() - (now - t_mono), kind, dict(data))

    def _detect_tool_apps(self, sessions: list[AudioSession], now: float) -> None:
        """An app whose session stays active but which actually sounds less
        than ``tool_max_duty`` of the time over ``tool_window_s`` (IME key
        clicks, notification helpers...) gets its mixer Auto switched off, so
        it neither follows the bus gain nor gets a remembered gain of its own
        for the real players. A user who ticks Auto back on wins for good."""
        p = self.config.params
        for s in sessions:
            if s.is_self:
                continue
            mix = self.config.apps.get(s.name)
            if mix is not None and (not mix.auto or mix.auto_set_by == "user"):
                self._duty.pop(s.name, None)
                continue
            if not s.active:
                self._duty.pop(s.name, None)          # the window counts continuous activity only
                continue
            d = self._duty.setdefault(s.name, deque())
            d.append((now, s.peak > p.work_silence_peak))
            while d and d[0][0] < now - p.tool_window_s:
                d.popleft()
            if len(d) < 4 or now - d[0][0] < p.tool_window_s - 2 * self.SESSION_POLL_S:
                continue
            duty = sum(1 for _t, on in d if on) / len(d)
            if duty < p.tool_max_duty:
                mix = self.config.mix_for(s.name)
                mix.auto, mix.auto_set_by = False, "tool"
                self._dirty = True
                self._duty.pop(s.name, None)
                log.info("tool app detected: %s sounds %.0f%% of the time -> Auto off", s.name, duty * 100)
                self._event("tool_app", app=self.config.display_name(s.name), duty=duty * 100)
                self.mixer_changed()

    def _startup_master(self) -> None:
        """Once per launch, after the audio service has answered our first
        session query: park the Windows master at the configured position so
        there is boost headroom, and make it the calibration reference."""
        pct = self.config.startup_master_percent
        done = getattr(self, "_startup_master_done", set())
        if pct <= 0 or self.profile is None or self.device is None or self.device.id in done:
            return
        if not getattr(self, "_sessions_parked", False) or not self._running:
            return          # sessions first (attenuated to the parked gain), master second: no blast
        done.add(self.device.id); self._startup_master_done = done     # once per device per launch
        try:
            parked = max(0.0, min(1.0, pct / 100.0))
            try:                                   # remembered so exit can put it back
                now_sc = float(self.backend.get_master_scalar(self.device))
            except Exception:
                now_sc = None
            stored = self.config.master_before_percent
            if now_sc is not None and abs(now_sc - parked) > 0.015:
                self._master_before = now_sc          # a level the user set: remember it (also on disk)
                self.config.master_before_percent = round(now_sc * 100, 1); self._dirty = True
            elif stored is not None and stored >= 0:
                self._master_before = stored / 100.0  # the master is still where we parked it last
                                                      # time (a crash, or the previous exit): keep the original
            else:
                self._master_before = now_sc
            self.backend.set_master_scalar(self.device, parked)
            self._master_parked = parked
            self.profile.master_ref_db = self.backend.get_master_db(self.device)
            self._applied_master_db = None
            log.info("startup: master volume set to %.0f%% (%.1f dB) and taken as the reference", pct, self.profile.master_ref_db)
        except Exception:
            log.exception("startup master volume")

    # ---- strong mode --------------------------------------------------------
    STRONG_FADER_DB = -10.0       # default fader for an app switched to strong mode
    STRONG_HEADROOM_DB = 5.0      # a strong app's peaks must stay this far under B

    # Windows service sessions that carry other apps' audio (Bluetooth / AVRCP, the audio
    # engine itself): never "an app sounding" for attribution, works or presets
    SYSTEM_APPS = {"svchost.exe", "audiodg.exe", "System sounds"}

    def _sounding(self, sessions: list[AudioSession]) -> list[AudioSession]:
        return [s for s in sessions if s.active and not s.is_self and s.peak > 0.01
                and s.name not in self.SYSTEM_APPS]

    DOMINANT_RATIO = 0.3     # others must stay ~10 dB under the loudest (after session volumes) to be ignored
                             # (a -10 dB second source moves the mix loudness by < 0.5 dB)

    def _dominant(self, sessions: list[AudioSession]) -> AudioSession | None:
        """The one app that is effectively alone in the loopback: the loudest
        sounding app by peak x session volume (what reaches the mix), when
        every other sounding app is at least ~10 dB under it there. An idle
        session hovering at the peak threshold (OBS monitoring, a chat app's
        hiss) must not break single-app attribution; a real second voice does."""
        snd = self._sounding(sessions)
        if not snd:
            return None
        eff = [(s.peak * max(s.volume, 0.0), s) for s in snd]
        eff.sort(key=lambda x: x[0], reverse=True)
        top, best = eff[0]
        if top <= 0.0:
            return None
        if any(e > top * self.DOMINANT_RATIO for e, _ in eff[1:]):
            return None
        return best

    def source_loudness(self, sessions: list[AudioSession], r, cmd: GainCommand | None
                        ) -> tuple[str, float | None, float | None]:
        """The loudness of the one app that is sounding, with every gain taken
        out: (app name, momentary, short-term) as the app would measure at
        100 % session volume and the reference master. The loopback measures
        the whole mix after the session volumes, so this is exact only when
        exactly one app sounds; otherwise ("", None, None)."""
        s = self._dominant(sessions)
        if s is None or cmd is None or r.short_term <= r.gate_lufs:
            return "", None, None
        vol_db = 20.0 * math.log10(max(s.volume, 1e-6))
        # loopback = source + session volume (+ master, only if the master is in the loopback)
        seen_master = cmd.master_offset_db if (self.profile is not None and
                                               self.profile.master_visible_in_loopback) else 0.0
        return (s.name,
                r.momentary - seen_master - vol_db if r.momentary > r.gate_lufs else None,
                r.short_term - seen_master - vol_db)

    def _track_peaks(self, sessions: list[AudioSession], cmd: GainCommand) -> None:
        """When exactly one app is sounding, what we hear is that app alone:
        remember the loudest it has been *at 0 dB gain* (bus gain and its own
        fader taken out), so strong mode can size its fader from history."""
        s = self._dominant(sessions)
        if s is None or cmd.perceived_lufs <= -90:
            return
        mix = self.config.mix_for(s.name)
        src = cmd.perceived_lufs - cmd.total_db - mix.offset_db
        if mix.peak_src_lufs is None or src > mix.peak_src_lufs + 0.1:
            mix.peak_src_lufs = round(src, 1)
            self._dirty = True

    def _attribute_cut(self, sessions: list[AudioSession], cmd: GainCommand, now: float) -> None:
        """A protection cut fired. If the loud sound is an *intrusion* - an app
        that started sounding in the last second while another app was
        already playing - the bus cut is undone and that app alone is pulled
        down by the same amount (until it stops sounding; permanently if it is
        in strong mode). Otherwise (the playing content itself got loud) the
        bus cut stands, and a strong-mode app is still pulled down."""
        if self.config.monitor_only:
            return                      # nothing is moved, so nothing is attributed or pulled
        snd = self._sounding(sessions)
        fades = getattr(self, "_session_fade", {})
        ends = getattr(self, "_fade_end", {})
        fade_s = self.config.params.new_work_fade_s
        def started(s):
            t0 = self._sound_since.get(s.name, -1e9)
            if s.key in fades:                      # still fading in: its real level is only now arriving
                t0 = max(t0, fades[s.key] + fade_s)
            elif s.key in ends:
                t0 = max(t0, ends[s.key])
            return t0
        new = [s for s in snd if now - started(s) < 1.2]
        old = [s for s in snd if s.name not in {n.name for n in new}]
        if new and old and self.controller is not None:
            undone = self.controller.move_cut_to_app(now, self.meter)
            cut = max(undone, 0.0)
            if cut > 0.1:
                for s in new:
                    self._app_cut[s.name] = round(self._app_cut.get(s.name, 0.0) + cut, 1)
                    log.info("loud intrusion from %s: bus cut undone, app cut %.1f dB", s.name, self._app_cut[s.name])
                    self._event("app_cut", app=self.config.display_name(s.name), cut=self._app_cut[s.name])
                self.mixer_changed()
        self._strong_after_cut(sessions, cmd)

    def _strong_after_cut(self, sessions: list[AudioSession], cmd: GainCommand) -> None:
        """A protection cut fired: if the loudest app is in strong mode, its
        own fader goes down by the excess over B - headroom, for good."""
        if self.profile is None:
            return
        snd = self._sounding(sessions)
        if not snd:
            return
        s = max(snd, key=lambda x: x.peak)
        mix = self.config.mix_for(s.name)
        if not self.params_for_app(s.name).cut_pulls_fader:
            return
        t_now = self.controller.t_lufs if self.controller is not None else self.profile.b_lufs
        excess = cmd.perceived_lufs - (t_now - self.STRONG_HEADROOM_DB)
        if excess <= 0.5:
            return
        mix.offset_db = max(-40.0, round(mix.offset_db - excess, 1))
        self._dirty = True
        log.info("strong mode: %s still %.1f dB over B-%g -> fader %+.1f dB", s.name, excess, self.STRONG_HEADROOM_DB, mix.offset_db)
        self._event("strong_cut", app=self.config.display_name(s.name), excess=excess, off=mix.offset_db)
        self.mixer_changed()

    def set_monitor_only(self, on: bool, rotate: bool = True) -> None:
        """Dashboard mode "monitor only": keep measuring, learning and showing,
        but put every session back to 100 % and touch no volume until it is
        switched off again. Settings, faders and presets are left untouched."""
        if self.config.monitor_only == on:
            return
        self.config.monitor_only = on
        if on:
            self._restore_volumes()               # neutral volumes while we only watch
            try:
                self._applied_master_db = float(self.backend.get_master_db(self.device))   # baseline for counting the user's moves
            except Exception:
                pass
        else:
            self._applied_session_db.clear(); self._applied_master_db = None   # re-apply on the next tick
            if self.controller is not None and self.profile is not None:
                # start the controller afresh (IDLE -> onset) instead of from whatever
                # gain it held when monitoring began
                self.controller = GainController(self.profile.targets(), self.controller.params)
                self._last_cmd = None
        self._event("monitor_on" if on else "monitor_off")
        self._dirty = True
        if rotate:
            self.rotate_logs()

    def set_app_preset(self, app_name: str, preset: str) -> None:
        """Mixer drop-down: the parameter preset the bus runs with while this
        app sounds alone ("" = whatever the dashboard mode is)."""
        mix = self.config.mix_for(app_name)
        if preset and preset not in self.config.presets:
            preset = ""
        mix.preset = preset
        self._event("app_preset", app=self.config.display_name(app_name), name=preset or "—")
        self._dirty = True
        self.mixer_changed()

    def params_for_app(self, app_name: str) -> ControllerParams:
        mix = self.config.apps.get(app_name)
        if mix is not None and mix.preset and mix.preset in self.config.presets:
            return self.config.params_for_preset(mix.preset)
        return self.config.params

    def platform_for(self, app_name: str) -> PlatformModel:
        m = self._platforms.get(app_name)
        if m is None:
            m = PlatformModel.from_dict(self.config.mix_for(app_name).platforms)
            self._platforms[app_name] = m
        return m

    def reset_platforms(self, app_name: str) -> None:
        """Mixer button: forget the app's learned platforms (e.g. its own volume setting changed)."""
        self._platforms[app_name] = PlatformModel()
        self.config.mix_for(app_name).platforms = None
        self._dirty = True
        self._event("platform_reset", app=self.config.display_name(app_name))

    def _source_poll(self, sessions: list[AudioSession], r, now: float) -> None:
        """Every session poll: learn the sole sounding app's platforms, feed
        the controller its position for the source-tracking target, and run
        the bus with that app's own preset if it has one."""
        sole, _sm, ss = self.source_loudness(sessions, r, self._last_cmd)
        lo = hi = None
        if sole and ss is not None:
            m = self.platform_for(sole)
            if m.add(ss):
                log.info("%s: source level shifted; platforms rebuilt from the last two minutes", sole)
                self._event("platform_shift", app=self.config.display_name(sole))
            lo, hi = m.platforms
            if not m.ready:
                lo = hi = None
            if now - getattr(self, "_last_platform_store", 0.0) > 30.0:   # persist slowly (every 30 s)
                self._last_platform_store = now
                for name, pm in self._platforms.items():
                    if pm.weight > 0:
                        self.config.mix_for(name).platforms = pm.to_dict()
                self._dirty = True
        self._source_now = (sole, ss, lo, hi)
        if self.controller is not None:
            self.controller.set_source(now, ss if sole else None, lo, hi)
        # Several apps comparably loud in the mix for a while: say so once a minute, naming
        # the ones the bus does not control (OBS monitoring the desktop at 100 % is the classic
        # case - a copy of the music that AutoLoud neither attenuates nor can attribute).
        snd_all = self._sounding(sessions)
        if not sole and len(snd_all) >= 2:
            self._shared_since = getattr(self, "_shared_since", None) or now
            if now - self._shared_since > 10.0 and now - getattr(self, "_shared_told", -1e9) > 60.0:
                self._shared_told = now
                free = [self.config.display_name(x.name) for x in snd_all if not self._follows_bus(x.name)]
                self._event("mix_shared", apps="、".join(self.config.display_name(x.name) for x in snd_all),
                            free="、".join(free) if free else "—")
        else:
            self._shared_since = None
        # the sole app's own preset drives the bus while it sounds alone
        dom = self._dominant(sessions)
        want = ""
        if dom is not None:
            mix = self.config.apps.get(dom.name)
            if mix is not None and mix.preset in self.config.presets and mix.preset != self.config.active_preset:
                want = mix.preset
        if want != self._param_override:
            self._param_override = want
            self.apply_params()
            self._event("mode_app", name=want or self.config.active_preset,
                        app=self.config.display_name(dom.name) if dom is not None else "")

    def _boost_cap_db(self) -> float:
        """How far the master volume can really be raised above the calibration
        reference (0 dB full scale is the end of the road), so the controller
        never asks for a boost that cannot be applied and never believes the
        sound is louder than it is."""
        try:
            lo, hi = self.backend.master_range_db(self.device)
        except Exception:
            lo, hi = -65.0, 0.0
        ref = self.profile.master_ref_db if self.profile is not None else 0.0
        return max(0.0, hi - ref)

    def _onset_gains(self, sessions: list[AudioSession]) -> tuple[float, float | None]:
        """(cap, start) for the onset after silence.
        cap: highest gain the guard may ramp to = the remembered gain of the
        sounding auto app(s) (lowest if several), 0 dB for apps never heard.
        start: the remembered gain to resume at when every sounding auto app
        is known (None if any is unknown -> the guard ramp runs instead)."""
        active = [s for s in sessions if s.active and not s.is_self and self._follows_bus(s.name)]
        known = [self.config.apps[s.name].last_gain_db for s in active
                 if s.name in self.config.apps and self.config.apps[s.name].last_gain_db is not None]
        cap = min(known) if known else 0.0
        start = min(known) if known and len(known) == len(active) else None
        return cap, start

    def _follows_bus(self, app_name: str) -> bool:
        """Does the bus auto gain currently apply to this app?"""
        mix = self.config.apps.get(app_name)
        return (mix is None or mix.auto) and app_name not in self._holds

    def user_touched_fader(self, app_name: str) -> None:
        """Rule 7: a manual fader move freezes that app's auto gain until its
        current work ends."""
        if self.config.enabled and self._last_cmd is not None and self._follows_bus(app_name):
            self._holds[app_name] = self._last_cmd.total_db
            self._event("hold", app=self.config.display_name(app_name), gain=self._last_cmd.total_db)
        self.mixer_changed()

    def set_target_b(self, b_lufs: float) -> None:
        """Dashboard slider: the loudness you set is the new comfort level B."""
        if self.profile is None:
            return
        delta = b_lufs - self.profile.b_lufs
        if abs(delta) < 0.05:
            return
        self.profile.b_lufs = float(b_lufs)
        self.profile.calibrated = True
        if self.controller is not None:
            self.controller.retune(self.profile.targets())
            self.controller.shift_target(delta, time.perf_counter(), self.meter)
        for mix in self.config.apps.values():          # remembered gains follow B
            if mix.last_gain_db is not None:
                mix.last_gain_db = round(mix.last_gain_db + delta, 2)
        self._dirty = True
        self._set_state(profile=self.profile)

    def _mode_name(self) -> str:
        return "monitor" if self.config.monitor_only else (self._param_override or self.config.active_preset)

    def _log_volume(self, kind: str, delta_db: float, app: str = "") -> None:
        if self.meter_log is None:
            return
        try:
            pct = float(self.backend.get_master_scalar(self.device)) * 100 if self.device is not None else -1.0
        except Exception:
            pct = -1.0
        self.meter_log.volume(self._mode_name(), kind, delta_db,
                              self.profile.b_lufs if self.profile is not None else 0.0, pct, app)

    # ---- monitor only: plain master volume control ---------------------------
    def master_percent(self) -> float:
        return getattr(self, "_master_pct", -1.0)

    def set_master_percent(self, pct: float) -> None:
        """Monitor only: the dashboard slider sets the Windows master volume directly."""
        if self.device is None:
            return
        pct = max(0.0, min(100.0, pct))
        try:
            before = self.master_percent()
            self.backend.set_master_scalar(self.device, pct / 100.0)
            self._master_pct = pct
            self._applied_master_db = float(self.backend.get_master_db(self.device))   # our move, not the user's
            self._event("master_slider", pct=pct)
            if self.meter_log is not None:
                self.meter_log.volume(self._mode_name(), "master_slider", pct - before, 
                                      self.profile.b_lufs if self.profile is not None else 0.0, pct)
        except Exception:
            log.exception("set master percent")

    def nudge_b(self, delta_db: float, kind: str = "volkey") -> None:
        """Volume keys / knob: move the comfort level B by ``delta_db``."""
        if self.profile is None:
            return
        self.set_target_b(max(B_MUTE_LUFS, min(0.0, self.profile.b_lufs + delta_db)))
        log.info("volume key: B %+.1f dB -> %.1f LUFS", delta_db, self.profile.b_lufs)
        self._event("volkey", delta=delta_db, b=self.profile.b_lufs)
        self._log_volume(kind, delta_db)

    def resume_after_safety(self) -> None:
        """The protection notice closed (or 5 s passed): re-adjust from here."""
        if self.controller is not None:
            self.controller.resume_after_safety(time.perf_counter(), self.meter)

    def _check_master_moved(self, master_db: float) -> None:
        """Rule 7: the user moved the Windows master volume -> that is the new
        comfort level. Re-base the calibration reference so the controller
        does not fight it (and does not need to re-learn anything)."""
        if self._calibrating or self._applied_master_db is None or self.profile is None or not self._running:
            return
        delta = master_db - self._applied_master_db
        if abs(delta) < 0.5:
            return
        p = self.profile
        if self.config.monitor_only:
            # monitor only: the user's own master moves are counted, not converted or undone
            self._applied_master_db = master_db
            try:
                pct = float(self.backend.get_master_scalar(self.device)) * 100
            except Exception:
                pct = -1.0
            log.info("monitor: user moved the master by %+.1f dB (now %.0f%%)", delta, pct)
            self._event("user_master", delta=delta, pct=pct)
            self._log_volume("user_master", delta)
            return
        if self.config.volume_keys:
            # The keys / knob mean "P", but the hook cannot see them while an elevated
            # window (most games with anti-cheat) is in front: Windows then moves the
            # master itself. Read that move as the key presses it was, nudge P by the
            # configured step per press (Windows steps the master 2 % per press), and
            # park the master again so the next press still has somewhere to go.
            try:
                sc = float(self.backend.get_master_scalar(self.device))
            except Exception:
                sc = None
            parked = getattr(self, "_master_parked", None)
            presses = max(1, int(round(abs(sc - parked) / 0.02))) if (sc is not None and parked is not None) else 1
            presses = min(presses, 10)
            step = self.config.volume_key_step_db * presses * (1 if delta > 0 else -1)
            # Consume this observed change exactly once, even if restoring the
            # master fails. Without a configured startup park, the old baseline
            # was never updated, turning one move into another P nudge per poll.
            previous_master = self._applied_master_db
            self._applied_master_db = master_db
            try:
                if parked is not None:
                    self.backend.set_master_scalar(self.device, parked)
                else:
                    self.backend.set_master_db(self.device, previous_master)
                self._applied_master_db = float(self.backend.get_master_db(self.device))
            except Exception:
                log.exception("re-park master")
                self._master_target_db = None     # retry our write, without repeating the P nudge
            log.info("master moved by %+.1f dB behind the hook (%d press(es)): P %+.1f dB, master parked again", delta, presses, step)
            self._event("volkey_master", delta=step, presses=presses)
            self.nudge_b(step, kind="volkey_master")
            return
        p.master_ref_db += delta
        if p.master_visible_in_loopback:      # loopback moved too: targets move with it
            p.b_lufs += delta
        self._applied_master_db = master_db
        if self.controller is not None:
            self.controller.retune(p.targets())
        self._dirty = True
        log.info("master volume moved by user (%+.1f dB): new reference %.1f dB", delta, p.master_ref_db)
        self._event("master_moved", delta=delta, ref=p.master_ref_db)
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
        if not self._running:
            return          # stopping: a tick still in flight must not touch the volumes again
        if self.config.monitor_only:
            return          # monitor only: everything is measured and shown, nothing is moved
        G = cmd.total_db if cmd is not None else 0.0
        cfg = self.config
        # B slider at its very bottom = mute every app that follows the bus
        if cmd is not None and self.profile is not None and self.profile.b_lufs <= B_MUTE_LUFS + 0.05:
            G = -120.0
        apps = [s for s in sessions if not s.is_self]
        desired: dict[str, float] = {}
        for s in apps:
            mix = cfg.apps.get(s.name)
            off = mix.offset_db if mix else 0.0
            if s.name in self._holds:
                base = self._holds[s.name] if cmd is not None else 0.0
                if cmd is not None and cmd.phase == Phase.SAFETY and self._follows_bus(s.name):
                    base = min(base, G)     # a protection cut overrides a frozen fader hold
            else:
                base = G if (cmd is not None and self._follows_bus(s.name)) else 0.0
            desired[s.key] = base + off - self._app_cut.get(s.name, 0.0)
        active_desired = [desired[s.key] for s in apps if s.active] or [G]
        P = max(0.0, max(active_desired))
        try:
            lo, hi = self.backend.master_range_db(self.device)
        except Exception:
            lo, hi = -65.0, 0.0
        P = min(P, cfg.params.max_master_boost_db, max(0.0, hi - self.profile.master_ref_db))
        if P > 2.0 and not self.profile.master_probed:
            self._auto_probe_master()           # once per device: does the loopback see the master?

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

        # A session we have never set (just created by Windows, at 100 %) is not put straight
        # at its gain but a fade below it, rising over new_work_fade_s: the first second of a
        # new player / tab / notification is the classic blast.
        fade_db, fade_s = cfg.params.new_work_fade_db, cfg.params.new_work_fade_s
        now_t = time.perf_counter()
        fades = getattr(self, "_session_fade", None)
        if fades is None:
            fades = self._session_fade = {}
        for s in apps:
            db = min(0.0, desired[s.key] - P)
            prev = self._applied_session_db.get(s.key)
            if prev is None and fade_db > 0 and fade_s > 0 and s.key not in fades:
                fades[s.key] = now_t
            t_f = fades.get(s.key)
            if t_f is not None:
                left = 1.0 - (now_t - t_f) / fade_s
                if left <= 0:
                    fades.pop(s.key, None)
                    ends = getattr(self, "_fade_end", None)
                    if ends is None:
                        ends = self._fade_end = {}
                    ends[s.key] = t_f + fade_s
                else:
                    db = max(-120.0, db - fade_db * left)
            want = 0.0 if db <= -119.9 else 10 ** (db / 20)
            # Windows re-creates a session (default-device switch, a new stream) at its own
            # stored volume: if what the session reports drifts from what we set, set it again
            drifted = prev is not None and abs(s.volume - want) > 0.02
            if prev is None or abs(prev - db) > 0.05 or drifted:
                try:
                    # at the floor the session is muted outright (0 %), i.e. -inf dB
                    self.backend.set_session_volume(self.device, s.key, 0.0 if db <= -119.9 else 10 ** (db / 20))
                    self._applied_session_db[s.key] = db
                except TimeoutError:
                    log.warning("set volume for %s timed out; skipping it this tick", s.name)
        self._sessions_parked = True
        target_master = self.profile.master_ref_db + P
        target_master = max(lo, min(hi, target_master))
        last_target = getattr(self, "_master_target_db", self._applied_master_db)
        if self._applied_master_db is None or last_target is None or abs(last_target - target_master) > 0.05:
            try:
                self.backend.set_master_db(self.device, target_master)
                self._master_target_db = target_master
                # Drivers can quantize/clamp a successful write. Compare future
                # external moves with readback, not an unattainable requested dB.
                self._applied_master_db = float(self.backend.get_master_db(self.device))
            except Exception:
                log.exception("set master")
                self._applied_master_db = None     # no trustworthy baseline until readback succeeds

    def emergency_exit(self) -> None:
        """Called from the crash handlers: put the volumes back as a normal exit would."""
        try:
            self._running = False
            self._restore_volumes(leaving=True)
        except Exception:
            log.exception("emergency exit")

    def _restore_volumes(self, leaving: bool = False) -> None:
        """Sessions back to 100 %. The master goes back to the calibration
        reference - or, when the program is leaving, to where it was before
        start-up: with the master managed at 100 % a full-volume restore would
        otherwise blast the headphones."""
        if self.device is None:
            return
        try:
            before = getattr(self, "_master_before", None)
            parked = getattr(self, "_master_parked", None)
            want = None
            if leaving:
                want = self.config.exit_master_percent / 100.0 if self.config.exit_master_percent >= 0 else before
            # the master goes first whenever it is coming down (or staying): sessions back
            # to 100 % under a parked-high master is the exit pop
            master_first = leaving and (want is None or parked is None or want <= parked + 1e-6)
            def put_master():
                if leaving:
                    if want is None:
                        return
                    self.backend.set_master_scalar(self.device, max(0.0, min(1.0, want)))
                    self.config.master_before_percent = -1.0      # handed back: nothing to keep
                    try:
                        self.config.save()
                    except Exception:
                        pass
                    log.info("exit: master volume set to %.0f%%", want * 100)
                elif self.profile is not None and self.profile.calibrated:
                    self.backend.set_master_db(self.device, self.profile.master_ref_db)
            # order matters for the ears: whichever move makes things quieter goes first
            if master_first:
                put_master()
            for s in self.backend.list_sessions(self.device):
                if not s.is_self:
                    self.backend.set_session_volume(self.device, s.key, 1.0)
            if not master_first:
                put_master()
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

    def _auto_probe_master(self) -> None:
        """The calibration wizard used to ask whether the master volume is inside
        the loopback (software volume) or behind it (hardware volume); that
        decides how a master boost is accounted for. Without a wizard, probe it
        the first time a boost is actually needed on this device, while
        something is playing: a 6 dB dip for ~1.6 s, then back. Once per device."""
        if self.profile is None or self.profile.master_probed or self.config.monitor_only:
            return
        with self._reading_lock:
            r = self._reading
        if r is None or not r.has_signal or r.short_term <= r.gate_lufs:
            return                              # nothing steady to measure against: try again later
        self.profile.master_probed = True       # one attempt is all a user should ever hear
        vis = None
        try:
            vis = self.probe_master_visibility()
        except Exception:
            log.exception("master visibility probe")
        if vis is not None:
            self.profile.master_visible_in_loopback = vis
            if self.controller is not None:
                self.controller.retune(self.profile.targets())
        self._event("master_probe", visible=vis)
        self._dirty = True

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
