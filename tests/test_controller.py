"""Closed-loop simulation of the controller with a synthetic source.

Plant: source plays at S LUFS; loopback = S + visible gain; perceived =
loopback + hidden gain (master boost when the master is not in the loopback).
"""
import numpy as np

from autoloud.core.controller import GainController, Targets, ControllerParams, Phase
from autoloud.dsp.loudness import StreamingLoudnessMeter


FS = 48000
CHUNK = 480  # 10 ms


def run_sim(source_lufs_fn, seconds, targets, params=None, master_visible=False, hook=None):
    targets.master_visible_in_loopback = master_visible
    params = params or ControllerParams()
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
    assert max(first) < -32 + 4, max(first)
    end = [p for t, p, c in log if t > 5.0]
    assert abs(np.mean(end) - (-22)) < 1.5, np.mean(end)


def test_adjust_steps_are_small_up_and_larger_down():
    # start already in ADJUST at gain 0 (hook), source 4 dB too quiet -> +0.5 dB steps every ~7 s
    P = ControllerParams()
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.ADJUST; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
    log = run_sim(lambda t: -26.0, 40.0, T(), P, hook=hook)
    gains = [c.total_db for t, p, c in log]
    assert 0.4 <= max(gains) <= 2.6, max(gains)           # ~5 steps of 0.5 in 40 s, never more
    steps = [abs(b - a) for a, b in zip(gains, gains[1:])]
    assert max(steps) < 0.5 * 0.01 / 2 * 1.5 + 1e-6      # 0.5 dB over 2 s -> 0.0025 dB per 10 ms
    # too loud by 6 dB -> -2 dB steps, converges within ~25 s
    log = run_sim(lambda t: -16.0, 30.0, T(), P, hook=hook)
    end = [p for t, p, c in log if t > 25]
    assert abs(np.mean(end) - (-22)) < 1.0, np.mean(end)
    g = [c.total_db for t, p, c in log]
    assert min(g) >= -6.6 and all(abs(b - a) <= 2.0 * 0.01 / 2 + 1e-6 for a, b in zip(g, g[1:]))


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
    log = run_sim(lambda t: -22.0 if t < 5 else -15.0, 40.0, T(), hook=hook)   # +7 dB > trigger 5
    assert any(c.phase == Phase.ADJUST for t, p, c in log if t > 10)
    end = [p for t, p, c in log if t > 35]
    assert abs(np.mean(end) - (-22)) < 1.0, np.mean(end)


def test_safety_cuts_only_the_excess_and_holds():
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.STEADY; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
    log = run_sim(lambda t: -22.0 if t < 3 else -9.0, 10.0, T(), hook=hook)   # B+13
    trig = [t for t, p, c in log if c.safety_triggered]
    assert trig and 3.0 < trig[0] < 3.6, trig
    after = [c for t, p, c in log if t > 4.0]
    assert all(c.phase == Phase.SAFETY for c in after)
    g = after[-1].total_db
    assert -9.5 <= g <= -6.5, g                           # source B+13 -> land on B+5: ~ -8 dB, not back to B (-13)
    # perceived after the cut ~ B + 5, and it gets there fast (< 0.5 s after the trigger)
    assert abs(log[-1][1] - (-17.0)) < 1.5
    landed = [t for t, p, c in log if t > trig[0] and c.total_db < -6.5]
    assert landed and landed[0] - trig[0] < 1.0, landed[:1]      # first cut in 0.15 s, touch-up once the meter catches up


def test_resume_after_safety_readjusts_to_B():
    state = {}
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.STEADY; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
        if t == 8.0:
            ctl.resume_after_safety(t, meter)
    log = run_sim(lambda t: -22.0 if t < 3 else -9.0, 60.0, T(), hook=hook)
    end = [p for t, p, c in log if t > 55]
    assert abs(np.mean(end) - (-22)) < 1.0, np.mean(end)


def test_new_work_uses_remembered_gain_when_alone():
    def hook(ctl, meter, t):
        if t == 0.0:
            ctl.phase = Phase.STEADY; ctl._last_signal_t = 0.0; ctl._ramp.retarget(0.0, 0.0, 0.0); ctl._arm_block(0.0, meter)
        if t == 5.0:
            ctl.notify_new_work(t, meter, remembered_gain_db=-6.0, alone=True)
    log = run_sim(lambda t: -22.0 if t < 5 else -16.0, 12.0, T(), hook=hook)
    g6 = [c.total_db for t, p, c in log if 5.5 < t < 6.0][0]
    assert abs(g6 - (-6.0)) < 0.1, g6                      # jumped to the remembered gain
    assert all(c.phase in (Phase.ADJUST, Phase.STEADY) for t, p, c in log if t > 5.5)


def test_new_source_during_playback_does_not_restart_from_A():
    log = run_sim(lambda t: -22.0 if t < 8 else -16.0, 20.0, T())
    around = [p for t, p, c in log if 8.0 < t < 10.0]
    assert min(around) > -32 + 4, min(around)
    assert all(c.phase != Phase.ONSET for t, p, c in log if t > 4)


def test_returns_to_idle_and_prearms():
    P = ControllerParams(onset_guard_lufs=-10)
    log = run_sim(lambda t: -20.0 if t < 3 else None, 7.0, T(), P)
    end = log[-1][2]
    assert end.phase == Phase.IDLE
    assert abs(end.total_db - (-32 - (-10))) < 1e-6


def test_boost_no_runaway_with_hidden_master():
    # a quiet source needs +8 dB; with a hidden master the controller must stop at ~+8, not the cap
    P = ControllerParams(max_master_boost_db=20)
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
    log = run_sim(lambda t: -22.0 if (t < 3 or t > 8) else -9.0, 45.0, T(), hook=hook)
    g = [c.total_db for t, p, c in log]
    assert min(g) < -3.0                                   # the cut happened
    assert abs(g[-1]) < 0.75, g[-1]                        # and we are back near 0 dB by t=45
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
    assert abs(g[8.5] - (-15.0)) < 1.5, g[8.5]            # landed within ~2.5 s, not 0.5 dB / 7 s
    assert -14.0 < g[7.0] < -5.0, g[7.0]                  # ramping, not a jump
    end = [p for t, p, c in log if t > 12]
    assert abs(np.mean(end) - (-37)) < 1.5, np.mean(end)
