"""Gain controller: turns loudness readings into volume commands.

All loudness values are LUFS as measured at the WASAPI loopback, i.e. the
engine mix *after* per-session volume and *before* the endpoint (master)
volume. The calibration stores the targets A (lower bound) and B (comfort)
in that same domain, together with the master volume that was in place
during calibration (``master_ref_db``). Perceived loudness is therefore::

    perceived = L_loopback + (master_db - master_ref_db)

The controller never changes the hardware chain; it only decides a total
gain in dB and splits it into a session attenuation (<= 0 dB, applied to
every foreign audio session) and a master boost (>= 0 dB, applied on top of
the reference master volume, bounded by ``max_master_boost_db``).

Behaviour (as specified):

* IDLE  (no audio): pre-arm the gain so that an unexpectedly loud source
  (``onset_guard_lufs``) would land exactly on A.
* ONSET (first ``onset_ramp_s`` after audio returns): the target loudness
  ramps from A to B while the source loudness is being measured.
* STEADY: the gain is recomputed from the integrated loudness of the past
  window (``window_s``, default 30 s; the first window after an onset is
  shorter), and every change is a smooth ramp of ``ramp_s`` seconds.
  A new source joining while audio is already playing does NOT restart
  from A (that would be audibly discontinuous); the next window handles it.
* SAFETY: if momentary perceived loudness exceeds B by more than
  ``safety_margin_db`` the gain is pulled down quickly (``safety_ramp_s``).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from autoloud.dsp.loudness import LoudnessReading, MIN_LUFS, ABS_GATE_LUFS


class Phase(str, Enum):
    IDLE = "idle"
    ONSET = "onset"
    STEADY = "steady"


@dataclass
class ControllerParams:
    onset_ramp_s: float = 2.0        # A -> B ramp at the start of an event
    onset_guard_lufs: float = -10.0  # assumed loudness of an unknown new source
    ramp_s: float = 2.0              # ramp time for window-based changes
    window_s: float = 30.0           # steady-state analysis window
    first_window_s: float = 12.0     # shorter first window after an onset
    safety_margin_db: float = 8.0    # momentary > B + margin triggers safety
    safety_ramp_s: float = 0.3
    silence_timeout_s: float = 2.0   # no signal for this long -> IDLE
    max_master_boost_db: float = 12.0
    min_session_gain_db: float = -40.0
    source_change_window_s: float = 0.0  # >0: quick re-evaluation N s after a new app starts (0 = off)


@dataclass
class Targets:
    a_lufs: float = -30.0            # lower bound (beep calibration)
    b_lufs: float = -22.0            # comfort target (music calibration)
    master_ref_db: float = 0.0       # endpoint master volume during calibration
    # Whether the loopback capture already reflects the endpoint (master)
    # volume. True for devices whose volume is applied in software by the
    # audio engine, False when the driver/hardware applies it. Detected
    # during calibration; it decides how much of our own gain the meter sees.
    master_visible_in_loopback: bool = False


@dataclass
class GainCommand:
    session_gain_db: float           # <= 0
    master_offset_db: float          # >= 0, added to master_ref_db
    total_db: float
    target_lufs: float
    source_lufs: float
    phase: Phase
    perceived_lufs: float


@dataclass
class _Ramp:
    start_db: float = 0.0
    end_db: float = 0.0
    t0: float = 0.0
    dur: float = 0.0

    def value(self, now: float) -> float:
        if self.dur <= 0 or now >= self.t0 + self.dur:
            return self.end_db
        f = (now - self.t0) / self.dur
        return self.start_db + (self.end_db - self.start_db) * f

    def retarget(self, now: float, end_db: float, dur: float) -> None:
        self.start_db = self.value(now)
        self.end_db = end_db
        self.t0 = now
        self.dur = dur


class GainController:
    def __init__(self, targets: Targets, params: ControllerParams | None = None):
        self.targets = targets
        self.params = params or ControllerParams()
        self.phase = Phase.IDLE
        self._ramp = _Ramp()
        self._last_signal_t: float | None = None
        self._onset_t: float | None = None
        self._window_start_t: float = 0.0
        self._window_len: float = self.params.first_window_s
        self._gain_hist: deque[tuple[float, float]] = deque()  # (t, gain_db)
        self._source_est: float = MIN_LUFS
        self._last_cmd: GainCommand | None = None
        self._pending_quick_eval: float | None = None
        self.enter_idle(0.0)

    # ------------------------------------------------------------------
    @property
    def gain_db(self) -> float:
        return self._ramp.end_db

    def enter_idle(self, now: float) -> None:
        self.phase = Phase.IDLE
        g = self.targets.a_lufs - self.params.onset_guard_lufs
        self._ramp.retarget(now, self._clamp_total(g), 0.0)   # instant while silent
        self._source_est = MIN_LUFS

    def notify_source_change(self, now: float) -> None:
        """Call when the set of playing applications changes."""
        if self.phase == Phase.STEADY and self.params.source_change_window_s > 0:
            self._pending_quick_eval = now + self.params.source_change_window_s

    def retune(self, targets: Targets) -> None:
        self.targets = targets

    # ------------------------------------------------------------------
    def _clamp_total(self, g: float) -> float:
        lo = self.params.min_session_gain_db
        hi = self.params.max_master_boost_db
        return max(lo, min(hi, g))

    def _visible_gain(self, total_db: float) -> float:
        """Part of our gain that the loopback meter can see."""
        if self.targets.master_visible_in_loopback:
            return total_db
        return min(0.0, total_db)

    def _hidden_gain(self, total_db: float) -> float:
        """Part of our gain applied after the loopback tap (master boost)."""
        return total_db - self._visible_gain(total_db)

    def _mean_gain(self, now: float, span: float) -> float:
        pts = [g for t, g in self._gain_hist if t >= now - span]
        if not pts:
            return self._visible_gain(self._ramp.value(now))
        return sum(pts) / len(pts)

    def _record_gain(self, now: float, g: float) -> None:
        self._gain_hist.append((now, g))
        horizon = now - max(self.params.window_s, 10.0) - 5.0
        while self._gain_hist and self._gain_hist[0][0] < horizon:
            self._gain_hist.popleft()

    # ------------------------------------------------------------------
    def update(self, r: LoudnessReading, now: float, meter=None) -> GainCommand:
        """Advance the controller. ``meter`` (optional) gets its integration
        reset at window boundaries so the next window starts clean."""
        p, T = self.params, self.targets
        g_now = self._ramp.value(now)
        self._record_gain(now, self._visible_gain(g_now))

        if r.has_signal:
            self._last_signal_t = now
        silent_for = (now - self._last_signal_t) if self._last_signal_t is not None else 1e9

        # ---- phase transitions ------------------------------------------
        if self.phase == Phase.IDLE and r.has_signal:
            self.phase = Phase.ONSET
            self._onset_t = now
            self._window_start_t = now
            self._window_len = p.first_window_s
            if meter is not None:
                meter.reset_integration()
        elif self.phase != Phase.IDLE and silent_for > p.silence_timeout_s:
            self.enter_idle(now)

        target_lufs = T.b_lufs
        # source estimate from the most responsive measure available
        if r.has_signal:
            self._source_est = r.momentary - self._mean_gain(now, 0.4)

        if self.phase == Phase.ONSET:
            f = min(1.0, (now - self._onset_t) / max(1e-3, p.onset_ramp_s))
            target_lufs = T.a_lufs + (T.b_lufs - T.a_lufs) * f
            if r.momentary > ABS_GATE_LUFS:
                src = self._source_est
            else:
                src = p.onset_guard_lufs
            self._ramp.retarget(now, self._clamp_total(target_lufs - src), 0.25)
            if f >= 1.0:
                self.phase = Phase.STEADY

        elif self.phase == Phase.STEADY:
            # loopback includes the visible part of our gain; add the hidden part
            perceived_now = r.momentary + self._hidden_gain(g_now)
            # SAFETY: momentary way above target -> pull down fast
            if r.has_signal and perceived_now > T.b_lufs + p.safety_margin_db:
                src = r.momentary - self._mean_gain(now, 0.4)
                self._ramp.retarget(now, self._clamp_total(T.b_lufs - src), p.safety_ramp_s)
                self._window_start_t = now
                self._window_len = p.first_window_s
                if meter is not None:
                    meter.reset_integration()
                self._pending_quick_eval = None

            # QUICK re-evaluation after an app change (uses short-term)
            elif self._pending_quick_eval is not None and now >= self._pending_quick_eval:
                self._pending_quick_eval = None
                if r.short_term > ABS_GATE_LUFS:
                    src = r.short_term - self._mean_gain(now, 3.0)
                    self._ramp.retarget(now, self._clamp_total(T.b_lufs - src), p.ramp_s)

            # WINDOW boundary: integrated loudness of the past window
            elif now - self._window_start_t >= self._window_len:
                self._window_start_t = now
                self._window_len = p.window_s
                if r.integrated > ABS_GATE_LUFS:
                    src = r.integrated - self._mean_gain(now, self._window_len)
                    self._ramp.retarget(now, self._clamp_total(T.b_lufs - src), p.ramp_s)
                if meter is not None:
                    meter.reset_integration()

        total = self._ramp.value(now)
        session = min(0.0, total)
        master = max(0.0, total)
        cmd = GainCommand(
            session_gain_db=session,
            master_offset_db=master,
            total_db=total,
            target_lufs=target_lufs,
            source_lufs=self._source_est,
            phase=self.phase,
            perceived_lufs=(r.momentary + self._hidden_gain(total)) if r.has_signal else MIN_LUFS,
        )
        self._last_cmd = cmd
        return cmd
