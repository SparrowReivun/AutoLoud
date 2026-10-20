"""Closed-loop simulation of the controller with a synthetic source.

The "plant": source plays at S LUFS; loopback measures S + session_gain;
perceived = loopback + master_offset.
"""
import numpy as np

from autoloud.core.controller import GainController, Targets, ControllerParams, Phase
from autoloud.dsp.loudness import StreamingLoudnessMeter


FS = 48000
CHUNK = 480  # 10 ms


def run_sim(source_lufs_fn, seconds, targets, params=None, master_visible=False):
    """source_lufs_fn(t) -> LUFS or None (silence).

    master_visible=True simulates a device whose master volume is applied
    in software before the loopback tap.
    """
    targets.master_visible_in_loopback = master_visible
    rng = np.random.default_rng(0)
    meter = StreamingLoudnessMeter(FS, 2, integration_window_s=params.window_s if params else 180)
    ctl = GainController(targets, params)
    log = []
    cmd = None
    n = int(seconds * FS / CHUNK)
    # pink-ish noise at a calibrated level: white noise amp 1.0 ≈ -0.7 LUFS (K-weighted ~ +0.7 dB),
    # we calibrate empirically with a probe
    probe = StreamingLoudnessMeter(FS, 2, 1e9)
    probe.process(rng.standard_normal((FS * 3, 2)))
    ref = probe.integrated()  # LUFS of unit white noise
    for i in range(n):
        t = i * CHUNK / FS
        s = source_lufs_fn(t)
        g = cmd.total_db if cmd else ctl.gain_db
        g_seen = g if master_visible else min(0.0, g)
        if s is None:
            x = np.zeros((CHUNK, 2))
        else:
            amp = 10 ** ((s - ref + g_seen) / 20)
            x = rng.standard_normal((CHUNK, 2)) * amp
        r = meter.process(x)
        cmd = ctl.update(r, t, meter)
        perceived = (s + cmd.total_db) if s is not None else None
        log.append((t, perceived, cmd))
    return log


def test_loud_onset_is_held_near_A_then_ramps_to_B():
    T = Targets(a_lufs=-32, b_lufs=-22)
    log = run_sim(lambda t: -8.0 if t > 1.0 else None, 8.0, T)
    first = [p for t, p, c in log if 1.5 < t < 1.7 and p is not None]
    # the first fraction of a second should be around A (not blasting at -8)
    assert max(first) < T.a_lufs + 4, max(first)
    end = [p for t, p, c in log if t > 5.0]
    assert abs(np.mean(end) - T.b_lufs) < 1.0, np.mean(end)
    assert log[-1][2].phase == Phase.STEADY


def test_quiet_source_is_boosted_within_limit():
    T = Targets(a_lufs=-32, b_lufs=-22)
    log = run_sim(lambda t: -40.0, 8.0, T, ControllerParams(max_master_boost_db=12))
    end = log[-1][2]
    assert end.master_offset_db > 10.0
    assert end.session_gain_db == 0.0
    assert end.total_db <= 12.0 + 1e-6


def test_window_recompute_is_a_smooth_ramp():
    T = Targets(a_lufs=-32, b_lufs=-22)
    P = ControllerParams(first_window_s=4.0, window_s=5.0, ramp_s=2.0)
    # source gets 6 dB louder at t=6 (within safety margin, so only the window handles it)
    log = run_sim(lambda t: -20.0 if t < 6 else -14.0, 16.0, T, P)
    gains = [(t, c.total_db) for t, p, c in log]
    # max gain change per 10 ms must be bounded by ramp rate (6 dB / 2 s = 0.03 dB per 10 ms) after onset
    steps = [abs(g2 - g1) for (t1, g1), (t2, g2) in zip(gains, gains[1:]) if t1 > 3.0]
    assert max(steps) < 0.08, max(steps)
    end = [p for t, p, c in log if t > 14.0]
    assert abs(np.mean(end) - T.b_lufs) < 1.5


def test_safety_reacts_fast_to_blast():
    T = Targets(a_lufs=-32, b_lufs=-22)
    P = ControllerParams(first_window_s=60, window_s=180)
    log = run_sim(lambda t: -22.0 if t < 5 else -6.0, 8.0, T, P)
    after = [p for t, p, c in log if 5.6 < t < 6.0]
    assert max(after) < T.b_lufs + 4, max(after)


def test_returns_to_idle_and_prearms():
    T = Targets(a_lufs=-32, b_lufs=-22)
    P = ControllerParams(onset_guard_lufs=-10)
    log = run_sim(lambda t: -20.0 if t < 3 else None, 7.0, T, P)
    end = log[-1][2]
    assert end.phase == Phase.IDLE
    assert abs(end.total_db - (T.a_lufs - P.onset_guard_lufs)) < 1e-6


def test_boost_converges_without_runaway_hidden_master():
    # source needs +8 dB; with a hidden master the controller must not keep
    # boosting up to the limit (it can't see its own master boost in loopback)
    T = Targets(a_lufs=-32, b_lufs=-22)
    P = ControllerParams(max_master_boost_db=20, first_window_s=3, window_s=3)
    log = run_sim(lambda t: -30.0, 14.0, T, P, master_visible=False)
    end = log[-1][2]
    assert abs(end.total_db - 8.0) < 1.0, end.total_db
    assert abs(log[-1][1] - T.b_lufs) < 1.0


def test_boost_converges_visible_master():
    T = Targets(a_lufs=-32, b_lufs=-22)
    P = ControllerParams(max_master_boost_db=20, first_window_s=3, window_s=3)
    log = run_sim(lambda t: -30.0, 14.0, T, P, master_visible=True)
    end = log[-1][2]
    assert abs(end.total_db - 8.0) < 1.0, end.total_db
    assert abs(log[-1][1] - T.b_lufs) < 1.0


def test_new_source_during_playback_does_not_restart_from_A():
    # music at -22 (already at B); at t=8 a louder app joins -> mix becomes -16.
    # No onset guard (no dip to A); the next window ramps it back to B in 2 s.
    T = Targets(a_lufs=-32, b_lufs=-22)
    P = ControllerParams(first_window_s=4.0, window_s=6.0, ramp_s=2.0)
    log = run_sim(lambda t: -22.0 if t < 8 else -16.0, 24.0, T, P)
    around = [p for t, p, c in log if 8.0 < t < 10.0]
    assert min(around) > T.a_lufs + 4, min(around)          # never pulled down to A
    assert all(c.phase == Phase.STEADY for t, p, c in log if t > 3)
    steps = [abs(c2.total_db - c1.total_db) for (t1, p1, c1), (t2, p2, c2) in zip(log, log[1:]) if t1 > 3]
    assert max(steps) < 0.08                                 # 6 dB / 2 s = 0.03 dB per 10 ms
    end = [p for t, p, c in log if t > 20]
    assert abs(np.mean(end) - T.b_lufs) < 1.5
