"""Streaming ITU-R BS.1770-4 loudness meter.

Implements the K-weighting pre-filter (high shelf + high-pass) for an
arbitrary sample rate, and a streaming measurement that yields:

* momentary loudness (400 ms window)
* short-term loudness (3 s window)
* integrated loudness over a rolling window (absolute + relative gating,
  as in BS.1770-4 / EBU R128) — used for the "3-minute window" decisions.

The meter works on float32/float64 arrays shaped (frames, channels), fed in
arbitrary chunk sizes, and keeps filter state between calls so it can run
on a live capture stream.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

import numpy as np
from scipy.signal import lfilter, lfilter_zi

ABS_GATE_LUFS = -70.0
REL_GATE_LU = -10.0
MIN_LUFS = -100.0


def _db_to_pow(db: float) -> float:
    return 10.0 ** (db / 10.0)


def _pow_to_lufs(p: float) -> float:
    if p <= 0.0:
        return MIN_LUFS
    return -0.691 + 10.0 * math.log10(p)


def k_weighting_coefficients(fs: float):
    """Return ((b1, a1), (b2, a2)) biquads for stage 1 (shelf) and 2 (HPF).

    Derived with the same analytic design pyloudnorm uses, which reproduces
    the BS.1770 reference coefficients at 48 kHz.
    """
    # Stage 1: high shelf, +4 dB above ~1.5 kHz
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / fs)
    Vh = 10.0 ** (G / 20.0)
    Vb = Vh ** 0.4996667741545416
    a0 = 1.0 + K / Q + K * K
    b1 = np.array([
        (Vh + Vb * K / Q + K * K) / a0,
        2.0 * (K * K - Vh) / a0,
        (Vh - Vb * K / Q + K * K) / a0,
    ])
    a1 = np.array([1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0])

    # Stage 2: high-pass at ~38 Hz
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / fs)
    a0 = 1.0 + K / Q + K * K
    b2 = np.array([1.0, -2.0, 1.0]) / a0
    a2 = np.array([1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0])
    return (b1, a1), (b2, a2)


def channel_weights(n_channels: int) -> np.ndarray:
    """BS.1770 channel weights. LFE (channel index 3 in 5.1) is ignored."""
    if n_channels <= 3:
        return np.ones(n_channels)
    if n_channels == 5:   # L R C Ls Rs
        return np.array([1.0, 1.0, 1.0, 1.41, 1.41])
    if n_channels == 6:   # L R C LFE Ls Rs
        return np.array([1.0, 1.0, 1.0, 0.0, 1.41, 1.41])
    w = np.ones(n_channels)
    if n_channels > 3:
        w[3] = 0.0
    return w


@dataclass
class LoudnessReading:
    momentary: float      # LUFS, 400 ms
    short_term: float     # LUFS, 3 s
    integrated: float     # LUFS, gated, over the rolling window
    peak_dbfs: float      # sample peak of the last chunk
    has_signal: bool      # momentary above absolute gate


class StreamingLoudnessMeter:
    """Feed audio chunks, get BS.1770 loudness values back.

    Internally the signal is K-weighted, then the mean square power is
    accumulated per 100 ms hop. Windows are built from hops:
    momentary = 4 hops, short-term = 30 hops, integrated = up to
    ``integration_window_s`` seconds of 400 ms blocks (75 % overlap).
    """

    HOP_S = 0.1

    def __init__(self, fs: int, channels: int, integration_window_s: float = 180.0):
        self.fs = int(fs)
        self.channels = int(channels)
        self.weights = channel_weights(self.channels)
        (self.b1, self.a1), (self.b2, self.a2) = k_weighting_coefficients(self.fs)
        zi1 = lfilter_zi(self.b1, self.a1)
        zi2 = lfilter_zi(self.b2, self.a2)
        self.z1 = np.zeros((2, self.channels))
        self.z2 = np.zeros((2, self.channels))
        _ = (zi1, zi2)  # start from rest (zeros) rather than steady-state

        self.hop_len = int(round(self.HOP_S * self.fs))
        self._hop_acc = 0.0       # weighted sum of squares in the current hop
        self._hop_count = 0       # samples accumulated in the current hop
        self.hops: deque[float] = deque(maxlen=30)   # mean power per hop (3 s)
        self.block_powers: deque[float] = deque(
            maxlen=max(1, int(integration_window_s / self.HOP_S)))
        self.last_peak = 0.0
        self.reset_integration()

    # ------------------------------------------------------------------
    def reset_integration(self) -> None:
        self.block_powers.clear()

    def reset(self) -> None:
        self.z1[:] = 0.0
        self.z2[:] = 0.0
        self._hop_acc = 0.0
        self._hop_count = 0
        self.hops.clear()
        self.reset_integration()

    # ------------------------------------------------------------------
    def process(self, x: np.ndarray) -> LoudnessReading:
        """Process a chunk shaped (frames, channels) (mono 1-D is accepted)."""
        if x.ndim == 1:
            x = x[:, None]
        if x.shape[1] != self.channels:
            # tolerate channel mismatch by truncating/padding
            if x.shape[1] > self.channels:
                x = x[:, : self.channels]
            else:
                pad = np.zeros((x.shape[0], self.channels - x.shape[1]))
                x = np.concatenate([x, pad], axis=1)
        x = x.astype(np.float64, copy=False)
        self.last_peak = float(np.max(np.abs(x))) if x.size else 0.0

        y, self.z1 = lfilter(self.b1, self.a1, x, axis=0, zi=self.z1)
        y, self.z2 = lfilter(self.b2, self.a2, y, axis=0, zi=self.z2)
        sq = (y * y) @ self.weights   # per-sample weighted power sum over channels

        i = 0
        n = sq.shape[0]
        while i < n:
            take = min(self.hop_len - self._hop_count, n - i)
            self._hop_acc += float(np.sum(sq[i : i + take]))
            self._hop_count += take
            i += take
            if self._hop_count >= self.hop_len:
                self._finish_hop()
        return self.reading()

    def _finish_hop(self) -> None:
        mean_p = self._hop_acc / max(1, self._hop_count)
        self.hops.append(mean_p)
        self._hop_acc = 0.0
        self._hop_count = 0
        if len(self.hops) >= 4:
            block_p = sum(list(self.hops)[-4:]) / 4.0
            self.block_powers.append(block_p)

    # ------------------------------------------------------------------
    def momentary(self) -> float:
        if len(self.hops) < 4:
            return MIN_LUFS
        return _pow_to_lufs(sum(list(self.hops)[-4:]) / 4.0)

    def short_term(self) -> float:
        if len(self.hops) < 4:
            return MIN_LUFS
        h = list(self.hops)
        return _pow_to_lufs(sum(h) / len(h))

    def integrated(self) -> float:
        return gated_loudness(self.block_powers)

    def reading(self) -> LoudnessReading:
        m = self.momentary()
        return LoudnessReading(
            momentary=m,
            short_term=self.short_term(),
            integrated=self.integrated(),
            peak_dbfs=20.0 * math.log10(self.last_peak) if self.last_peak > 0 else MIN_LUFS,
            has_signal=m > ABS_GATE_LUFS,
        )


def gated_loudness(block_powers) -> float:
    """BS.1770-4 gated integrated loudness from 400 ms block powers."""
    if not block_powers:
        return MIN_LUFS
    p = np.fromiter(block_powers, dtype=np.float64)
    abs_thr = _db_to_pow(ABS_GATE_LUFS + 0.691)
    p1 = p[p > abs_thr]
    if p1.size == 0:
        return MIN_LUFS
    rel_thr = p1.mean() * _db_to_pow(REL_GATE_LU)
    p2 = p1[p1 > rel_thr]
    if p2.size == 0:
        return MIN_LUFS
    return _pow_to_lufs(float(p2.mean()))


def integrated_loudness(x: np.ndarray, fs: int) -> float:
    """Offline helper: integrated loudness of a whole signal (frames, ch)."""
    if x.ndim == 1:
        x = x[:, None]
    m = StreamingLoudnessMeter(fs, x.shape[1], integration_window_s=1e9)
    m.process(x)
    return m.integrated()
