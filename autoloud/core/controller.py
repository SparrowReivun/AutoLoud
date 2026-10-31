"""Gain controller: turns loudness readings into one bus gain (dB).

Everything is measured at the WASAPI loopback (after per-app session
volumes, before the endpoint master). Calibration gives A (ceiling) and B
(comfort) in that domain plus the master volume that was in place
(``master_ref_db``). The controller decides a total gain; the engine splits
it into session attenuation (<= 0 dB) and a master boost (>= 0 dB).

The behaviour, in the words of the design notes:

1. **When to start over.** A new work (song / video) in an app: follow the
   player's metadata when available, otherwise 15 s of silence followed by
   sound. A new work starts from the gain that app had last time; an app we
   have never heard starts a little quiet. (The engine detects works and
   calls ``notify_new_work``.)
2. **Getting comfortable.** Listen for 5 s, then decide: too quiet -> raise
   by at most 0.5 dB over 2 s; too loud -> lower by at most 2 dB over 2 s;
   within +/-0.5 dB of B -> done. Listen another 5 s before the next step.
3. **Once comfortable, stop chasing.** Quiet dialogue, interludes and fade-
   outs are left alone; a climax may get louder. Only a whole 5 s block
   above B + 5 dB restarts the adjustment.
4. **Sudden loudness.** Always active: momentary loudness above B + margin
   (default 10 dB, user-adjustable -5..25) is pulled down within 0.3 s, by
   the excess only (B+13 -> about -3 dB), never straight back to B.
5. **After a protection cut, don't bounce back.** Hold the cut while the
   notice is shown; when it closes (5 s without interaction) re-adjust from
   the current gain with rule 2 — with one relief: while the gain is still
   below what it was before the cut, and the sound is now clearly too quiet,
   the way back *up to that pre-cut gain* may use the larger (down-sized)
   step, so a loud ad does not leave the film whispering for minutes.

From complete silence the first sound is still guarded: the gain is parked
so that a loud unknown source lands on A, then target rises A -> B over 2 s
while the source is measured (then rule 2 takes over).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from autoloud.dsp.loudness import LoudnessReading, MIN_LUFS, ABS_GATE_LUFS


class Phase(str, Enum):
    IDLE = "idle"        # no audio: gain parked for a safe first sound
    ONSET = "onset"      # first 2 s after silence: A -> B
    ADJUST = "adjust"    # listen 5 s, step, listen 5 s ...
    STEADY = "steady"    # comfortable: only react to a loud 5 s block
    SAFETY = "safety"    # protection cut held until the notice closes


@dataclass
class ControllerParams:
    onset_ramp_s: float = 2.0        # A -> B ramp after silence
    onset_guard_lufs: float = -10.0  # assumed loudness of an unknown new source
    ramp_s: float = 2.0              # ramp time of every adjustment step
    listen_s: float = 5.0            # listen this long before each decision
    step_up_db: float = 0.5          # max raise per step
    step_down_db: float = 2.0        # max cut per step
    deadband_db: float = 0.5         # |error| below this = comfortable
    steady_trigger_db: float = 5.0   # a 5 s block above B + this restarts adjusting
    safety_margin_db: float = 10.0   # momentary above B + this = protection cut
    safety_ramp_s: float = 0.3
    safety_hysteresis_db: float = 1.5  # cut the excess plus this, so the level does not sit on the trigger
    safety_hold_s: float = 5.0       # notice auto-closes after this; engine resumes us
    silence_timeout_s: float = 2.0   # no signal at all for this long -> IDLE
    first_time_offset_db: float = -3.0   # unknown app's new work starts this much lower
    max_master_boost_db: float = 12.0
    min_session_gain_db: float = -40.0
    work_silence_s: float = 15.0         # per-app silence that ends a "work" (fallback rule)
    work_silence_peak: float = 0.003     # near-silence threshold on the session peak meter (~-50 dBFS)


@dataclass
class Targets:
    a_lufs: float = -30.0
    b_lufs: float = -22.0
    master_ref_db: float = 0.0
    master_visible_in_loopback: bool = False


@dataclass
class GainCommand:
    session_gain_db: float
    master_offset_db: float
    total_db: float
    target_lufs: float
    source_lufs: float
    phase: Phase
    perceived_lufs: float
    safety_triggered: bool = False   # True on the tick a protection cut starts


@dataclass
class _Ramp:
    start_db: float = 0.0
    end_db: float = 0.0
    t0: float = 0.0
    dur: float = 0.0

    def value(self, now: float) -> float:
        if self.dur <= 0 or now >= self.t0 + self.dur:
            return self.end_db
        return self.start_db + (self.end_db - self.start_db) * (now - self.t0) / self.dur

    def done(self, now: float) -> bool:
        return now >= self.t0 + self.dur

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
        self._block_start: float | None = None   # start of the current listening block
        self._block_arm_t: float | None = None   # block starts (meter reset) at this time
        self._gain_hist: deque[tuple[float, float]] = deque()
        self._source_est: float = MIN_LUFS
        self._safety_t: float | None = None
        self._pre_safety_gain: float | None = None   # gain before the last protection cut
        self.steps_taken = 0
        self.enter_idle(0.0)

    # ------------------------------------------------------------------
    @property
    def gain_db(self) -> float:
        return self._ramp.end_db

    def enter_idle(self, now: float) -> None:
        self.phase = Phase.IDLE
        g = self.targets.a_lufs - self.params.onset_guard_lufs
        self._ramp.retarget(now, self._clamp(g), 0.0)
        self._source_est = MIN_LUFS
        self._block_start = self._block_arm_t = None

    def retune(self, targets: Targets) -> None:
        self.targets = targets

    def notify_new_work(self, now: float, meter=None, remembered_gain_db: float | None = None,
                        alone: bool = True) -> None:
        """A new song / video started in an app that follows the bus gain.

        ``remembered_gain_db``: the bus gain that app had last time (None if
        never heard). ``alone``: it is the only app sounding, so the bus gain
        may jump to that app's remembered value.
        """
        if self.phase in (Phase.IDLE, Phase.ONSET):
            return                      # the onset guard already handles the start
        if alone:
            if remembered_gain_db is not None:
                self._ramp.retarget(now, self._clamp(remembered_gain_db), 0.3)
            else:
                self._ramp.retarget(now, self._clamp(self._ramp.value(now) + self.params.first_time_offset_db), 0.3)
        self.phase = Phase.ADJUST
        self._arm_block(now + 0.3, meter)

    def resume_after_safety(self, now: float, meter=None) -> None:
        """The protection notice closed: re-adjust from the current gain."""
        if self.phase == Phase.SAFETY:
            self.phase = Phase.ADJUST
            self._arm_block(now, meter)

    # ------------------------------------------------------------------
    def _clamp(self, g: float) -> float:
        return max(self.params.min_session_gain_db, min(self.params.max_master_boost_db, g))

    def _visible_gain(self, total_db: float) -> float:
        return total_db if self.targets.master_visible_in_loopback else min(0.0, total_db)

    def _hidden_gain(self, total_db: float) -> float:
        return total_db - self._visible_gain(total_db)

    def _mean_gain(self, now: float, span: float) -> float:
        pts = [g for t, g in self._gain_hist if t >= now - span]
        if not pts:
            return self._visible_gain(self._ramp.value(now))
        return sum(pts) / len(pts)

    def _record_gain(self, now: float, g: float) -> None:
        self._gain_hist.append((now, g))
        horizon = now - 30.0
        while self._gain_hist and self._gain_hist[0][0] < horizon:
            self._gain_hist.popleft()

    def _arm_block(self, at: float, meter) -> None:
        self._block_arm_t = at
        self._block_start = None
        self._meter = meter

    def _tick_block(self, now: float) -> None:
        if self._block_start is None and self._block_arm_t is not None and now >= self._block_arm_t:
            self._block_start = now
            self._block_arm_t = None
            if getattr(self, "_meter", None) is not None:
                self._meter.reset_integration()

    def _block_done(self, now: float) -> bool:
        return self._block_start is not None and now - self._block_start >= self.params.listen_s

    # ------------------------------------------------------------------
    def update(self, r: LoudnessReading, now: float, meter=None,
               extra_visible_db: float = 0.0) -> GainCommand:
        """Advance the controller by one tick.

        ``extra_visible_db``: gain the loopback already contains that is not
        ours (per-app faders, peak-weighted) so a -6 dB fader stays -6 dB.
        """
        p, T = self.params, self.targets
        if meter is not None:
            self._meter = meter
        g_now = self._ramp.value(now)
        self._record_gain(now, self._visible_gain(g_now) + extra_visible_db)
        safety_now = False

        if r.has_signal:
            self._last_signal_t = now
            self._source_est = r.momentary - self._mean_gain(now, 0.4)
        silent_for = (now - self._last_signal_t) if self._last_signal_t is not None else 1e9

        # ---- silence / onset transitions -----------------------------------
        if self.phase == Phase.IDLE and r.has_signal:
            self.phase = Phase.ONSET
            self._onset_t = now
        elif self.phase != Phase.IDLE and silent_for > p.silence_timeout_s:
            self.enter_idle(now)

        target_lufs = T.b_lufs
        perceived_now = (r.momentary + self._hidden_gain(g_now)) if r.has_signal else MIN_LUFS

        # ---- rule 4: sudden loudness, always ---------------------------------
        if self.phase in (Phase.ADJUST, Phase.STEADY, Phase.SAFETY) and r.has_signal:
            excess = perceived_now - (T.b_lufs + p.safety_margin_db)
            settled = self.phase != Phase.SAFETY or (
                self._ramp.done(now) and now - (self._safety_t or 0.0) > p.safety_ramp_s + 0.5)
            if excess > 0 and settled:   # momentary lags ~0.4 s: let the cut show before cutting again
                self._ramp.retarget(now, self._clamp(self._ramp.end_db - excess - p.safety_hysteresis_db),
                                    p.safety_ramp_s)
                if self.phase != Phase.SAFETY:
                    safety_now = True
                    self._pre_safety_gain = g_now
                self.phase = Phase.SAFETY
                self._safety_t = now
                self._block_start = self._block_arm_t = None

        if self.phase == Phase.ONSET:
            f = min(1.0, (now - self._onset_t) / max(1e-3, p.onset_ramp_s))
            target_lufs = T.a_lufs + (T.b_lufs - T.a_lufs) * f
            src = self._source_est if r.momentary > ABS_GATE_LUFS else p.onset_guard_lufs
            self._ramp.retarget(now, self._clamp(target_lufs - src), 0.25)
            if f >= 1.0:
                self.phase = Phase.ADJUST
                self._arm_block(now, meter)

        elif self.phase == Phase.ADJUST:
            self._tick_block(now)
            if self._block_done(now):
                self._decide(now, r)

        elif self.phase == Phase.STEADY:
            self._tick_block(now)
            if self._block_done(now):
                block = self._block_perceived(now, r)
                if block is not None and block > T.b_lufs + p.steady_trigger_db:
                    self.phase = Phase.ADJUST
                    self._decide(now, r)          # this block counts as the first listen
                else:
                    self._arm_block(now, meter)    # keep watching, do not chase

        # SAFETY: hold; the engine calls resume_after_safety() when the notice closes

        total = self._ramp.value(now)
        return GainCommand(
            session_gain_db=min(0.0, total), master_offset_db=max(0.0, total), total_db=total,
            target_lufs=target_lufs, source_lufs=self._source_est, phase=self.phase,
            perceived_lufs=perceived_now, safety_triggered=safety_now,
        )

    # ------------------------------------------------------------------
    def _block_perceived(self, now: float, r: LoudnessReading) -> float | None:
        """Perceived loudness of the listening block that just ended.

        Blocks start only after a ramp has finished, so the gain is constant
        during a block and the hidden part (master boost the loopback cannot
        see) is simply that of the current gain.
        """
        if r.integrated <= ABS_GATE_LUFS:
            return None
        return r.integrated + self._hidden_gain(self._ramp.value(now))

    def _decide(self, now: float, r: LoudnessReading) -> None:
        """Rule 2: one step towards B, then listen again."""
        p, T = self.params, self.targets
        block = self._block_perceived(now, r)
        if block is None:                 # block was (gated) silence: listen again
            self._arm_block(now, getattr(self, "_meter", None))
            return
        error = T.b_lufs - block          # >0 too quiet, <0 too loud
        if abs(error) <= p.deadband_db:
            self.phase = Phase.STEADY
            self._arm_block(now, getattr(self, "_meter", None))
            return
        g = self._ramp.end_db
        if error > 0:
            up = p.step_up_db
            pre = self._pre_safety_gain
            if pre is not None and g < pre:            # recovering from a protection cut
                up = max(up, min(p.step_down_db, pre - g))
            step = min(up, error)
        else:
            step = max(-p.step_down_db, error)
        if self._pre_safety_gain is not None and g + step >= self._pre_safety_gain:
            self._pre_safety_gain = None               # back where we were: normal rules again
        self._ramp.retarget(now, self._clamp(g + step), p.ramp_s)
        self.steps_taken += 1
        self._arm_block(now + p.ramp_s, getattr(self, "_meter", None))
