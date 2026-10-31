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
    onset_below_b_db: float = 10.0    # the onset floor "A" is this far below B
    onset_guard_lufs: float = -10.0  # assumed loudness of an unknown new source
    listen_s: float = 5.0            # listen this long before each decision; every step and the onset ramp take this long too
    step_up_db: float = 1.0          # max raise per step
    step_down_db: float = 0.8        # max cut per step
    deadband_up_db: float = 3.0      # a block up to this much ABOVE B still counts as comfortable
    deadband_down_db: float = 8.0    # a block up to this much BELOW B still counts as comfortable
    snap_db: float = 3.0             # |error| below this: one step exactly to B, ignoring the caps
    steady_trigger_db: float = 6.0   # steady: blocks above B + this ...
    steady_over_blocks: int = 2      # ... for this many consecutive blocks restart adjusting
    steady_under_db: float = -20.0   # steady: blocks below B + this (i.e. B - 20) ...
    steady_under_blocks: int = 3     # ... for this many consecutive blocks restart adjusting
    safety_margin_db: float = 13.0   # momentary above B + this = protection cut
    safety_land_db: float = 5.0      # ...which cuts the level down to B + this
    safety_ramp_s: float = 0.3       # the cut's ramp time (fast but not a click)
    move_ramp_s: float = 1.8         # ramp of a B-slider move and of a new work's jump to its remembered gain
    first_time_offset_db: float = -3.0   # unknown app's new work starts this much lower
    max_master_boost_db: float = 12.0
    min_session_gain_db: float = -120.0  # the gain floor; at the floor the session is muted outright
    work_silence_s: float = 15.0         # silence: per-app -> the work ended; whole mix -> IDLE (onset guard re-armed)
    work_silence_peak: float = 0.003     # near-silence threshold on the session peak meter (~-50 dBFS)
    tool_window_s: float = 60.0          # an app "active" this long while sounding < tool_max_duty of the time ...
    tool_max_duty: float = 0.10          # ... is a tool (key clicks, notifications): its mixer Auto is switched off


@dataclass
class Targets:
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
        self._safety_guard_until: float = 0.0        # no protection cuts before this (B shift landing)
        self._last_r: LoudnessReading | None = None
        self._last_extra: float = 0.0
        self._up_streak = 0                 # consecutive "too quiet" steps (raise cap grows with it)
        self._over_blocks = 0               # steady: consecutive blocks above B + steady_trigger_db
        self._under_blocks = 0              # steady: consecutive blocks below B + steady_under_db
        self._fresh = False                 # first decision of a new work / onset: a cut goes all the way
        self.steps_taken = 0
        self.events: deque[tuple[int, float, str, dict]] = deque(maxlen=12)   # (seq, now, kind, data)
        self._seq = 0
        self.enter_idle(0.0)

    # ------------------------------------------------------------------
    @property
    def gain_db(self) -> float:
        return self._ramp.end_db

    def _log(self, now: float, kind: str, **data) -> None:
        self._seq += 1
        self.events.append((self._seq, now, kind, data))

    def activity(self, now: float) -> dict:
        """What is happening right now, for the dashboard."""
        rp = self._ramp
        a = {"phase": self.phase.value, "gain": rp.value(now), "gain_end": rp.end_db}
        if not rp.done(now) and rp.dur > 0:
            a["ramp"] = {"from": rp.start_db, "to": rp.end_db, "dur": rp.dur,
                         "left": max(0.0, rp.t0 + rp.dur - now)}
        if self._block_start is not None:
            a["listen"] = {"elapsed": now - self._block_start, "total": self.params.listen_s}
        elif self._block_arm_t is not None and now < self._block_arm_t:
            a["listen_in"] = self._block_arm_t - now
        if self.phase == Phase.ONSET and self._onset_t is not None:
            a["onset"] = {"elapsed": now - self._onset_t, "total": self.params.listen_s}
        if self.phase == Phase.SAFETY and self._pre_safety_gain is not None:
            a["pre_safety_gain"] = self._pre_safety_gain
        return a

    def enter_idle(self, now: float) -> None:
        if self.phase != Phase.IDLE:
            self._log(now, "idle", gain=self._clamp(self.a_lufs - self.params.onset_guard_lufs))
        self.phase = Phase.IDLE
        g = self.a_lufs - self.params.onset_guard_lufs
        self._ramp.retarget(now, self._clamp(g), 0.0)
        self._source_est = MIN_LUFS
        self._block_start = self._block_arm_t = None

    def retune(self, targets: Targets) -> None:
        self.targets = targets

    @property
    def a_lufs(self) -> float:
        """The onset floor: a fixed distance below B (no separate calibration)."""
        return self.targets.b_lufs - self.params.onset_below_b_db

    def notify_new_work(self, now: float, meter=None, remembered_gain_db: float | None = None) -> None:
        """A new song / video started in an app that follows the bus gain:
        jump to the gain that app had last time (``remembered_gain_db``; a
        never-heard app starts a little lower than now). One app at a time
        is assumed; other apps sounding at the same moment are not considered.
        """
        if self.phase in (Phase.IDLE, Phase.ONSET):
            return                      # the onset guard already handles the start
        if remembered_gain_db is not None:
            self._ramp.retarget(now, self._clamp(remembered_gain_db), self.params.move_ramp_s)
            self._log(now, "new_work", mode="remembered", gain=self._ramp.end_db)
        else:
            self._ramp.retarget(now, self._clamp(self._ramp.value(now) + self.params.first_time_offset_db), self.params.move_ramp_s)
            self._log(now, "new_work", mode="first", gain=self._ramp.end_db)
        self.phase = Phase.ADJUST
        self._up_streak, self._fresh = 0, True
        self._arm_block(now + self.params.move_ramp_s, meter)

    def shift_target(self, delta_db: float, now: float, meter=None) -> None:
        """The user moved the comfort level B (dashboard slider).

        Not a nudge: the gain goes straight to what the new B needs, in one
        smooth ramp of ``move_ramp_s`` (no listen, no per-step limit).
        The needed gain is estimated from the last 3 s of measurement; if
        nothing is playing the gain simply moves by ``delta_db``. The sudden
        loudness protection is held off until the ramp has landed and the
        meter has caught up, so lowering B never pops the notice.
        """
        if self.phase == Phase.IDLE:
            self.enter_idle(now)           # re-park with the new A/B
            return
        p, T = self.params, self.targets
        g_now = self._ramp.value(now)
        new_gain = g_now + delta_db
        est = self._perceived_short_term(now)
        if self.phase in (Phase.ADJUST, Phase.STEADY) and est is not None:
            new_gain = g_now + (T.b_lufs - est)        # one step to the needed gain
        new_gain = self._clamp(new_gain)
        self._ramp.retarget(now, new_gain, p.move_ramp_s)
        self._pre_safety_gain = None                   # the user has set a new level
        self._up_streak, self._fresh = 0, False
        self._safety_guard_until = now + p.move_ramp_s + 1.0   # momentary lags ~0.4 s
        self._log(now, "b_shift", delta=delta_db, gain=new_gain, b=T.b_lufs, dur=p.move_ramp_s)
        if self.phase in (Phase.ADJUST, Phase.STEADY):
            self._arm_block(now + p.move_ramp_s, meter)

    def _perceived_short_term(self, now: float) -> float | None:
        """Perceived loudness over the last 3 s *as if* the current gain had
        applied throughout (so a ramp in progress does not skew it)."""
        r = self._last_r
        if r is None or r.short_term <= ABS_GATE_LUFS or not r.has_signal:
            return None
        g = self._ramp.value(now)
        return (r.short_term - self._mean_gain(now, 3.0)
                + self._visible_gain(g) + self._last_extra + self._hidden_gain(g))

    def resume_after_safety(self, now: float, meter=None) -> None:
        """The protection notice closed: re-adjust from the current gain."""
        if self.phase == Phase.SAFETY:
            self.phase = Phase.ADJUST
            self._log(now, "resume", gain=self._ramp.end_db)
            self._arm_block(now, meter)

    # ------------------------------------------------------------------
    boost_cap_db: float = 1e9     # set by the engine: the master volume's real headroom (<= max_master_boost_db)

    def _clamp(self, g: float) -> float:
        hi = min(self.params.max_master_boost_db, self.boost_cap_db)
        return max(self.params.min_session_gain_db, min(hi, g))

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
               extra_visible_db: float = 0.0, onset_cap_db: float = 0.0,
               onset_start_db: float | None = None) -> GainCommand:
        """Advance the controller by one tick.

        ``extra_visible_db``: gain the loopback already contains that is not
        ours (per-app faders, peak-weighted) so a -6 dB fader stays -6 dB.
        ``onset_cap_db``: the onset guard never raises the gain above this
        (the app's remembered gain, or 0 dB if unknown). A quiet intro must
        not be boosted to B within 2 s only for the real content to blast in;
        boosting is left to the slow rule 2.
        ``onset_start_db``: the sounding app's remembered gain, if any. Then
        there is no guard ramp at all: the gain goes to that value (over
        ``move_ramp_s``) and the normal listen-then-nudge rules take over,
        with the first decision allowed to cut all the way.
        """
        p, T = self.params, self.targets
        if meter is not None:
            self._meter = meter
        g_now = self._ramp.value(now)
        self._record_gain(now, self._visible_gain(g_now) + extra_visible_db)
        self._last_r, self._last_extra = r, extra_visible_db
        safety_now = False

        if r.has_signal:
            self._last_signal_t = now
            self._source_est = r.momentary - self._mean_gain(now, 0.4)
        silent_for = (now - self._last_signal_t) if self._last_signal_t is not None else 1e9

        # ---- silence / onset transitions -----------------------------------
        if self.phase == Phase.IDLE and r.has_signal:
            if onset_start_db is not None:
                # known app: resume where it left off, no guard ramp
                self.phase = Phase.ADJUST
                self._ramp.retarget(now, self._clamp(onset_start_db), p.move_ramp_s)
                self._up_streak, self._fresh = 0, True
                self._arm_block(now + p.move_ramp_s, meter)
                self._log(now, "onset_known", gain=self._ramp.end_db)
            else:
                self.phase = Phase.ONSET
                self._onset_t = now
                self._log(now, "onset", a=self.a_lufs, b=T.b_lufs)
        elif self.phase != Phase.IDLE and silent_for > p.work_silence_s:
            self.enter_idle(now)

        target_lufs = T.b_lufs
        perceived_now = (r.momentary + self._hidden_gain(g_now)) if r.has_signal else MIN_LUFS

        # ---- rule 4: sudden loudness, always ---------------------------------
        if self.phase in (Phase.ADJUST, Phase.STEADY, Phase.SAFETY) and r.has_signal:
            # trigger at B + margin; once cutting, keep correcting until the
            # level really sits at B + land (the momentary meter lags the jump)
            over = p.safety_margin_db if self.phase != Phase.SAFETY else p.safety_land_db + 1.0
            excess = perceived_now - (T.b_lufs + over)
            settled = self.phase != Phase.SAFETY or (
                self._ramp.done(now) and now - (self._safety_t or 0.0) > p.safety_ramp_s + 0.5)
            settled = settled and now >= self._safety_guard_until   # a B shift is still landing
            if excess > 0 and settled:   # momentary lags ~0.4 s: let the cut show before cutting again
                # cut from the gain that is *sounding now* (not a ramp's end point)
                # down to B + safety_land_db, fast
                cut = perceived_now - (T.b_lufs + p.safety_land_db)
                self._ramp.retarget(now, self._clamp(g_now - cut), p.safety_ramp_s)
                if self.phase != Phase.SAFETY:
                    safety_now = True
                    self._pre_safety_gain = g_now
                self._log(now, "safety", perceived=perceived_now, excess=excess,
                          cut=cut, gain=self._ramp.end_db)
                self.phase = Phase.SAFETY
                self._safety_t = now
                self._up_streak, self._fresh = 0, False
                self._block_start = self._block_arm_t = None

        if self.phase == Phase.ONSET:
            f = min(1.0, (now - self._onset_t) / max(1e-3, p.listen_s))
            target_lufs = self.a_lufs + (T.b_lufs - self.a_lufs) * f
            src = self._source_est if r.momentary > ABS_GATE_LUFS else p.onset_guard_lufs
            self._ramp.retarget(now, self._clamp(min(target_lufs - src, onset_cap_db)), 0.25)
            if f >= 1.0:
                self.phase = Phase.ADJUST
                self._up_streak, self._fresh = 0, True
                self._arm_block(now, meter)

        elif self.phase == Phase.ADJUST:
            self._tick_block(now)
            if self._block_done(now):
                self._decide(now, r)

        elif self.phase == Phase.STEADY:
            self._tick_block(now)
            if self._block_done(now):
                block = self._block_perceived(now, r)
                if block is None:
                    self._over_blocks = self._under_blocks = 0
                else:
                    over = block > T.b_lufs + p.steady_trigger_db
                    under = block < T.b_lufs + p.steady_under_db
                    self._over_blocks = self._over_blocks + 1 if over else 0
                    self._under_blocks = self._under_blocks + 1 if under else 0
                if block is not None and (self._over_blocks >= p.steady_over_blocks
                                          or self._under_blocks >= p.steady_under_blocks):
                    self.phase = Phase.ADJUST
                    self._log(now, "readjust", block=block, over=block - T.b_lufs,
                              blocks=self._over_blocks or self._under_blocks)
                    self._over_blocks = self._under_blocks = 0
                    self._decide(now, r)          # this block counts as the first listen
                else:
                    if block is not None:
                        self._log(now, "watch", block=block, rel=block - T.b_lufs,
                                  over_n=self._over_blocks, under_n=self._under_blocks)
                    self._arm_block(now, meter)    # keep watching, do not chase

        elif self.phase == Phase.SAFETY:
            # the cut has landed and the meter has caught up without a further cut:
            # back to the normal rules from here (no notice, no waiting)
            if self._ramp.done(now) and now - (self._safety_t or 0.0) > p.safety_ramp_s + 0.7:
                self.resume_after_safety(now, meter)

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
        comfortable = (-p.deadband_up_db <= error) if error < 0 else (error <= p.deadband_down_db)
        if comfortable:
            self.phase = Phase.STEADY
            self._up_streak, self._fresh = 0, False
            self._over_blocks = self._under_blocks = 0
            self._log(now, "steady", block=block, rel=-error)
            self._arm_block(now, getattr(self, "_meter", None))
            return
        g = self._ramp.end_db
        if abs(error) <= p.snap_db:
            # close to B: finish in one exact step instead of creeping around it
            self._up_streak = 0
            step = error
        elif error > 0:
            up = p.step_up_db
            pre = self._pre_safety_gain
            if pre is not None and g < pre:            # recovering from a protection cut
                up = max(up, min(p.step_down_db, pre - g))
            step = min(up, error)
        else:
            self._up_streak = 0
            # too loud right after a new work / onset: go all the way in one step
            step = error if self._fresh else max(-p.step_down_db, error)
        self._fresh = False
        if self._pre_safety_gain is not None and g + step >= self._pre_safety_gain:
            self._pre_safety_gain = None               # back where we were: normal rules again
        self._ramp.retarget(now, self._clamp(g + step), p.listen_s)
        self._log(now, "step", block=block, rel=-error, step=step, frm=g, to=self._ramp.end_db, dur=p.listen_s)
        self.steps_taken += 1
        self._arm_block(now + p.listen_s, getattr(self, "_meter", None))
