"""Closed-loop simulation of the controller with a synthetic source.

Plant: source plays at S LUFS; loopback = S + visible gain; perceived =
loopback + hidden gain (master boost when the master is not in the loopback).
"""
import numpy as np

from autoloud.core.controller import GainController, Targets, ControllerParams, Phase
from autoloud.dsp.loudness import StreamingLoudnessMeter


FS = 48000
CHUNK = 480  # 10 ms


def TP(**kw):
    """The tuning the simulations below were written for (the shipped defaults
    are the author's listening preferences and change more often)."""
    base = dict(step_up_db=0.5, step_down_db=2.0, deadband_db=1.0, snap_db=2.0,
                steady_trigger_db=5.0, steady_over_blocks=2, steady_under_db=-20.0, steady_under_blocks=4,
                safety_margin_db=10.0, safety_land_db=5.0, move_ramp_s=1.0, onset_below_b_db=5.0,
                work_silence_s=2.0)
    base.update(kw)
    return ControllerParams(**base)


def run_sim(source_lufs_fn, seconds, targets, params=None, master_visible=False, hook=None):
    targets.master_visible_in_loopback = master_visible
    params = params or TP()
    rng = np.random.default_rng(0)
    meter = StreamingLoudnessMeter(FS, 2, integration_window_s=60)
    ctl = GainController(targets, params)
    probe = StreamingLoudnessMeter(FS, 2, 1e9)
    probe.process(rng.standard_normal((FS * 3, 2)))
    ref = probe.integrated()
    log, cmd = [], None
    for i in range(int(seconds * FS / CHUNK)):
        t = i * CHUNK / FS
        if hook:
            hook(ctl, meter, t)
        s = source_lufs_fn(t)
        g = cmd.total_db if cmd else ctl.gain_db
        g_seen = g if master_visible else min(0.0, g)
        x = np.zeros((CHUNK, 2)) if s is None else rng.standard_normal((CHUNK, 2)) * 10 ** ((s - ref + g_seen) / 20)
        cmd = ctl.update(meter.process(x), t, meter)
        log.append((t, (s + cmd.total_db) if s is not None else None, cmd))
    return log


T = lambda: Targets(b_lufs=-22)


def test_onset_from_silence_is_held_near_A_then_reaches_B():
    log = run_sim(lambda t: -8.0 if t > 1.0 else None, 10.0, T())
    first = [p for t, p, c in log if 1.5 < t < 1.7]
    assert max(first) < -27 + 4, max(first)                # floor is B - 5 = -27
    end = [p for t, p, c in log if t > 7.0]
    assert abs(np.mean(end) - (-22)) < 1.5, np.mean(end)


def test_adjust_steps_are_small_up_and_larger_down():
    # start already in ADJUST at gain 0 (hook), source 4 dB too quiet -> +0.5 dB steps every ~7 s
    P = TP()
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.ADJUST; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
    log = run_sim(lambda t: -26.0, 55.0, T(), P, hook=hook)
    gains = [c.total_db for t, p, c in log]
    # raises are 0.5 dB each (10 s cycle): 2.5 dB after 55 s, then the snap finishes the last 2 dB in one step
    assert 2.0 <= max(gains) <= 4.3, max(gains)
    g12 = max(c.total_db for t, p, c in log if t < 20.5)
    assert g12 <= 1.1, g12                                # first two steps: 0.5 + 0.5
    steps = [abs(b - a) for a, b in zip(gains, gains[1:])]
    assert max(steps) < 2.0 * 0.01 / 5 * 1.5 + 1e-6      # largest single ramp <= 2 dB (the snap) over 5 s
    # too loud by 6 dB -> -2 dB steps, settles inside the +-1 dB dead band within ~35 s
    log = run_sim(lambda t: -16.0, 45.0, T(), P, hook=hook)
    end = [p for t, p, c in log if t > 40]
    assert abs(np.mean(end) - (-22)) < 1.3, np.mean(end)
    g = [c.total_db for t, p, c in log]
    assert min(g) >= -6.6 and all(abs(b - a) <= 2.0 * 0.01 / 5 + 1e-6 for a, b in zip(g, g[1:]))


def test_steady_ignores_quiet_passages_and_climaxes_under_trigger():
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.STEADY; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
    # dialogue drops 10 dB, then a climax +4 dB: gain must not move
    src = lambda t: -22.0 if t < 5 else (-32.0 if t < 15 else -18.0)
    log = run_sim(src, 25.0, T(), hook=hook)
    assert all(abs(c.total_db) < 1e-6 for t, p, c in log), max(abs(c.total_db) for t, p, c in log)
    assert all(c.phase == Phase.STEADY for t, p, c in log)


def test_steady_readjusts_when_a_whole_block_is_too_loud():
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.STEADY; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
    log = run_sim(lambda t: -22.0 if t < 5 else -15.0, 70.0, T(), hook=hook)   # +7 dB > trigger 5
    assert any(c.phase == Phase.ADJUST for t, p, c in log if t > 10)
    end = [p for t, p, c in log if t > 60]
    assert abs(np.mean(end) - (-22)) < 1.3, np.mean(end)      # settles inside the +-1 dB dead band


def test_new_work_uses_remembered_gain_when_alone():
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.STEADY; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
        if t == 5.0:
            ctl.notify_new_work(t, meter, remembered_gain_db=-6.0, alone=True)
    log = run_sim(lambda t: -22.0 if t < 5 else -16.0, 12.0, T(), hook=hook)
    g6 = [c.total_db for t, p, c in log if 6.1 < t < 6.5][0]
    assert abs(g6 - (-6.0)) < 0.1, g6                      # moved to the remembered gain within move_ramp_s (1 s)
    assert all(c.phase in (Phase.ADJUST, Phase.STEADY) for t, p, c in log if t > 5.5)


def test_new_source_during_playback_does_not_restart_from_A():
    log = run_sim(lambda t: -22.0 if t < 8 else -16.0, 20.0, T())
    around = [p for t, p, c in log if 8.0 < t < 10.0]
    assert min(around) > -27 + 4, min(around)
    assert all(c.phase != Phase.ONSET for t, p, c in log if t > 6)


def test_returns_to_idle_and_prearms():
    P = TP(onset_guard_lufs=-10)
    log = run_sim(lambda t: -20.0 if t < 3 else None, 7.0, T(), P)
    end = log[-1][2]
    assert end.phase == Phase.IDLE
    assert abs(end.total_db - (-27 - (-10))) < 1e-6       # parked for a -10 LUFS source landing on B - 5


def test_boost_no_runaway_with_hidden_master():
    # a quiet source needs +8 dB; with a hidden master the controller must stop at ~+8, not the cap
    P = TP(max_master_boost_db=20)
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.ADJUST; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 4.0, 0.0); ctl._arm_block(0.0, meter)
    log = run_sim(lambda t: -30.0, 70.0, T(), P, master_visible=False, hook=hook)
    end = log[-1][2]
    assert abs(end.total_db - 8.0) < 1.0, end.total_db
    assert abs(log[-1][1] - (-22)) < 1.0
    log = run_sim(lambda t: -30.0, 70.0, T(), P, master_visible=True, hook=hook)
    assert abs(log[-1][2].total_db - 8.0) < 1.0, log[-1][2].total_db


def test_recovery_after_blast_is_faster_up_to_pre_cut_gain():
    # steady at 0 dB, blast +13 for 5 s, notice closes at t=12, then the source is normal again:
    # the gain must come back to ~0 within ~30 s (2 dB steps), not 0.5 dB steps (~70 s)
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.STEADY; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
        if t == 12.0:
            ctl.resume_after_safety(t, meter)
    log = run_sim(lambda t: -22.0 if (t < 3 or t > 8) else -9.0, 75.0, T(), hook=hook)
    g = [c.total_db for t, p, c in log]
    assert min(g) < -3.0                                   # the cut happened
    assert abs(g[-1]) < 0.75, g[-1]                        # and we are back near 0 dB by t=75
    assert abs(log[-1][1] - (-22)) < 1.0


def test_b_slider_moves_gain_in_one_ramp_without_protection():
    # steady at B = -22 with source -22; at t=6 the user drags B down by 15 dB
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.STEADY; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
        if abs(t - 6.0) < 1e-9:
            ctl.retune(Targets(b_lufs=-37)); ctl.shift_target(-15.0, t, meter)
    log = run_sim(lambda t: -22.0, 16.0, T(), hook=hook)
    assert not any(c.safety_triggered for t, p, c in log)
    assert all(c.phase != Phase.SAFETY for t, p, c in log)
    g = {round(t, 2): c.total_db for t, p, c in log}
    assert abs(g[7.5] - (-15.0)) < 1.5, g[7.5]            # landed within ~1.5 s, not 0.5 dB / 10 s
    assert -14.0 < g[6.5] < -5.0, g[6.5]                  # ramping, not a jump
    end = [p for t, p, c in log if t > 12]
    assert abs(np.mean(end) - (-37)) < 1.5, np.mean(end)


def test_onset_does_not_boost_a_quiet_intro_into_a_blast():
    # silence, then a 3 s quiet intro (-45 LUFS), then the real content (-14 LUFS, B+8)
    src = lambda t: None if t < 1 else (-45.0 if t < 4 else -14.0)
    log = run_sim(src, 8.0, T())
    assert max(c.total_db for t, p, c in log) <= 0.0 + 1e-6          # never boosted during the onset
    assert not any(c.safety_triggered for t, p, c in log)            # so the content does not trip protection


def test_new_work_too_loud_is_cut_in_one_step():
    # ADJUST fresh (as after a new work), source 9 dB too loud -> one full -9 dB step
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.ADJUST; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0)
            ctl._up_streak, ctl._fresh = 0, True; ctl._arm_block(0.0, meter)
    log = run_sim(lambda t: -13.0, 14.0, T(), hook=hook)
    g = [c.total_db for t, p, c in log if t > 10.5]
    assert all(abs(x - (-9.0)) < 0.3 for x in g), (min(g), max(g))
    # not fresh: the same cut is capped at step_down_db (2 dB)
    def hook2(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.ADJUST; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0)
            ctl._up_streak, ctl._fresh = 0, False; ctl._arm_block(0.0, meter)
    log = run_sim(lambda t: -13.0, 14.0, T(), hook=hook2)
    g = [c.total_db for t, p, c in log if t > 10.5]
    assert min(g) >= -2.1, min(g)


def test_near_B_one_exact_step_then_steady():
    # 1.8 dB too quiet with a 1 dB first-step cap: snap_db=2 -> one exact +1.8 step, then steady
    P = TP(step_up_db=1.0)
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.ADJUST; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
    log = run_sim(lambda t: -23.8, 25.0, T(), P, hook=hook)
    g = [c.total_db for t, p, c in log]
    assert abs(max(g) - 1.8) < 0.4, max(g)
    assert log[-1][2].phase == Phase.STEADY
    steps = [c for t, p, c in log if c.phase == Phase.ADJUST and t > 16]
    assert not steps            # no creeping after the exact step


def test_steady_needs_two_loud_blocks_or_four_quiet_blocks():
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.STEADY; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
    # one loud block (B+8 for 5 s) then back to B: no re-adjust
    log = run_sim(lambda t: -14.0 if 0.5 < t < 5.5 else -22.0, 20.0, T(), hook=hook)
    assert all(c.phase == Phase.STEADY for t, p, c in log)
    # two loud blocks in a row: re-adjust
    log = run_sim(lambda t: -14.0 if t > 0.5 else -22.0, 20.0, T(), hook=hook)
    assert any(c.phase == Phase.ADJUST for t, p, c in log if t > 10.5)
    assert all(c.phase == Phase.STEADY for t, p, c in log if t < 9.5)       # not after the first block
    # quiet (B-25): three blocks are tolerated, the fourth re-adjusts
    log = run_sim(lambda t: -47.0, 30.0, T(), hook=hook)
    assert all(c.phase == Phase.STEADY for t, p, c in log if t < 19.5)
    assert any(c.phase == Phase.ADJUST for t, p, c in log if t > 20.5)


def test_onset_resumes_at_remembered_gain_for_a_known_app():
    # silence, then a known app (remembered -8 dB) starts: no guard ramp, straight to -8 within move_ramp_s
    meter_box = {}
    def run(start):
        rng = np.random.default_rng(0)
        meter = StreamingLoudnessMeter(FS, 2, integration_window_s=60)
        P = TP(work_silence_s=2.0)
        ctl = GainController(T(), P)
        probe = StreamingLoudnessMeter(FS, 2, 1e9); probe.process(rng.standard_normal((FS * 3, 2))); ref = probe.integrated()
        out = []
        cmd = None
        for i in range(int(6.0 * FS / CHUNK)):
            t = i * CHUNK / FS
            s = None if t < 1.0 else -14.0
            g = cmd.total_db if cmd else ctl.gain_db
            x = np.zeros((CHUNK, 2)) if s is None else rng.standard_normal((CHUNK, 2)) * 10 ** ((s - ref + min(0.0, g)) / 20)
            cmd = ctl.update(meter.process(x), t, meter, 0.0, min(0.0, start) if start is not None else 0.0, start)
            out.append((t, cmd))
        return out
    known = run(-8.0)
    g2 = [c.total_db for t, c in known if 2.1 < t < 2.3][0]
    assert abs(g2 - (-8.0)) < 0.2, g2
    assert all(c.phase != Phase.ONSET for t, c in known)
    unknown = run(None)
    assert any(c.phase == Phase.ONSET for t, c in unknown)


def test_protection_cuts_to_B_plus_land_then_resumes_by_itself():
    # steady at B, then the source jumps to B+13: cut to ~B+5 within safety_ramp_s,
    # no notice: the normal rules resume on their own and walk the rest down
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.STEADY; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
    log = run_sim(lambda t: -22.0 if t < 3 else -9.0, 60.0, T(), hook=hook)
    trig = [t for t, p, c in log if c.safety_triggered]
    assert trig and 3.0 < trig[0] < 3.6, trig
    landed = [t for t, p, c in log if t > trig[0] and c.total_db < -6.5]
    assert landed and landed[0] - trig[0] < 1.2, landed[:1]          # ~B+5 quickly
    assert any(c.phase == Phase.ADJUST for t, p, c in log if trig[0] + 1.0 < t < trig[0] + 3.0)   # resumed by itself
    end = [p for t, p, c in log if t > 50]
    assert abs(np.mean(end) - (-22)) < 2.5, np.mean(end)             # and kept going down towards B
