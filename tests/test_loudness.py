import numpy as np
import pyloudnorm as pyln
import pytest

from autoloud.dsp.loudness import StreamingLoudnessMeter, integrated_loudness


def _sine(fs, seconds, freq=1000.0, amp=0.5, ch=2):
    t = np.arange(int(fs * seconds)) / fs
    s = amp * np.sin(2 * np.pi * freq * t)
    return np.stack([s] * ch, axis=1)


@pytest.mark.parametrize("fs", [44100, 48000])
def test_reference_997hz_tone(fs):
    # BS.1770: 997 Hz sine at -20 dBFS (amplitude 0.1) in both channels → -20.0 LUFS (+/-0.1)
    x = _sine(fs, 5.0, freq=997.0, amp=0.1)
    assert abs(integrated_loudness(x, fs) - (-20.0)) < 0.15


@pytest.mark.parametrize("fs", [44100, 48000])
def test_matches_pyloudnorm_on_noise(fs):
    rng = np.random.default_rng(0)
    x = rng.standard_normal((fs * 6, 2)) * 0.05
    # add a quiet section to exercise gating
    x[fs * 2 : fs * 3] *= 0.001
    ref = pyln.Meter(fs).integrated_loudness(x)
    ours = integrated_loudness(x, fs)
    assert abs(ref - ours) < 0.2


def test_streaming_equals_batch():
    fs = 48000
    rng = np.random.default_rng(1)
    x = rng.standard_normal((fs * 4, 2)) * 0.1
    batch = StreamingLoudnessMeter(fs, 2)
    batch.process(x)
    stream = StreamingLoudnessMeter(fs, 2)
    for i in range(0, x.shape[0], 480):
        stream.process(x[i : i + 480])
    assert abs(batch.integrated() - stream.integrated()) < 1e-6
    assert abs(batch.short_term() - stream.short_term()) < 1e-6


def test_silence_reports_no_signal():
    m = StreamingLoudnessMeter(48000, 2)
    r = m.process(np.zeros((48000, 2)))
    assert not r.has_signal
    assert r.integrated <= -70


def test_gate_follows_the_gain_inside_the_loopback():
    """A -50 dB session cut puts ordinary content at -85 LUFS in the loopback:
    with the plain BS.1770 gate that is 'silence'; with the gate lowered by
    the cut it is measured like any other signal."""
    fs = 48000
    quiet = _sine(fs, 4.0, amp=0.5 * 10 ** (-80 / 20))        # ~ -86 LUFS (stereo 1 kHz, 0.5 = -6 LUFS)
    m = StreamingLoudnessMeter(fs, 2)
    r = m.process(quiet)
    assert not r.has_signal and r.integrated <= -70 and r.gate_lufs == -70.0
    m = StreamingLoudnessMeter(fs, 2)
    m.gate_offset_db = -50.0
    r = m.process(quiet)
    assert r.gate_lufs == -105.0                                 # -70 - 50 floored at -105
    assert r.has_signal and -90 < r.integrated < -80 and -90 < r.short_term < -80
    m.gate_offset_db = +10.0                                    # a boost never raises the gate
    assert m.abs_gate == -70.0
