"""Per-app loudness platforms for the source-tracking target.

Each app gets a slowly learned picture of how loud *it* is (its own loudness
with every gain taken out, measured only while it sounds alone): a weighted
histogram of its short-term source loudness. The two "platforms" are the
20th and 80th percentiles - the level of its quiet stretches and of its loud
stretches. Learning is slow on purpose (half-life ~20 min of sounding time),
so a long fight does not become the new "normal"; the target itself follows
the 3 s short-term reading.

A shift of the whole distribution (the app's own volume setting changed) is
detected from a short window: when three quarters of the last two minutes
lie 6 LU or more *below* the quiet platform, the picture is rebuilt from
those two minutes. (A louder window is not treated as a shift: it may be the
loud platform met for the first time, and it is harmless - u clips at 1, so
T = B. The slow learner, or the mixer's Relearn button, covers that side.)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

LO, HI, STEP = -70.0, 0.0, 0.5
NBINS = int((HI - LO) / STEP)
GATE_LUFS = -40.0          # quieter source readings are not "a platform"
POLL_S = 0.5               # one sample per session poll
HALF_LIFE_S = 20 * 60.0    # memory of the long-term picture, in sounding time
DECAY = 0.5 ** (POLL_S / HALF_LIFE_S)
MIN_WEIGHT = 60.0          # 30 s of readings before the platforms count
MIN_SEP_DB = 1.0           # platforms closer than this: no range to track
RECENT_N = 240             # last 120 s for the shift check
SHIFT_DB = 6.0


def _bin(v: float) -> int:
    return max(0, min(NBINS - 1, int((v - LO) / STEP)))


def _percentile(w: list[float], q: float) -> float | None:
    tot = sum(w)
    if tot <= 0:
        return None
    acc = 0.0
    for i, x in enumerate(w):
        acc += x
        if acc >= tot * q:
            return LO + (i + 0.5) * STEP
    return HI


@dataclass
class PlatformModel:
    weights: list[float] = field(default_factory=lambda: [0.0] * NBINS)
    recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    resets: int = 0

    # ---- learning ---------------------------------------------------------
    def add(self, src_lufs: float) -> bool:
        """One attributable short-term reading. Returns True when the picture
        was rebuilt because the app's level shifted."""
        if src_lufs < GATE_LUFS:
            return False
        self.recent.append(src_lufs)
        rebuilt = False
        if len(self.recent) >= RECENT_N and self.weight >= MIN_WEIGHT:
            # the long-term picture *without* the window itself (it may already be bending it)
            w_long = list(self.weights)
            for v in self.recent:
                i = _bin(v)
                w_long[i] = max(0.0, w_long[i] - 1.0)
            if sum(w_long) >= MIN_WEIGHT:
                lo, hi = _percentile(w_long, 0.20), _percentile(w_long, 0.80)
            else:
                lo = hi = None
            rec = sorted(self.recent)
            q25, q75 = rec[len(rec) // 4], rec[(3 * len(rec)) // 4]
            # Only a *downward* shift is unambiguous: nothing but silence normally sits
            # far below the quiet platform. A louder window may just be the loud
            # platform being met for the first time (and is harmless: u is clipped at
            # 1, T = B), so the slow learner and the Relearn button cover that side.
            shifted = lo is not None and hi is not None and q75 <= lo - SHIFT_DB
            _ = q25
            if shifted:
                med = rec[len(rec) // 2]
                self.weights = [0.0] * NBINS
                for v in self.recent:
                    if abs(v - med) <= 2 * SHIFT_DB:      # the new level, not stragglers of the old one
                        self.weights[_bin(v)] += 1.0
                self.recent.clear()
                self.resets += 1
                rebuilt = True
        if not rebuilt:
            w = self.weights
            for i in range(NBINS):
                if w[i]:
                    w[i] *= DECAY
            w[_bin(src_lufs)] += 1.0
        return rebuilt

    # ---- reading ----------------------------------------------------------
    @property
    def weight(self) -> float:
        return sum(self.weights)

    @property
    def ready(self) -> bool:
        lo, hi = self.platforms
        return lo is not None and hi is not None and hi - lo >= MIN_SEP_DB

    @property
    def platforms(self) -> tuple[float | None, float | None]:
        if self.weight < MIN_WEIGHT:
            return None, None
        return _percentile(self.weights, 0.20), _percentile(self.weights, 0.80)

    # ---- persistence --------------------------------------------------------
    def to_dict(self) -> dict:
        nz = {str(i): round(w, 3) for i, w in enumerate(self.weights) if w >= 0.001}
        return {"w": nz, "resets": self.resets}

    @classmethod
    def from_dict(cls, d: dict | None) -> "PlatformModel":
        m = cls()
        if not d:
            return m
        try:
            for k, v in d.get("w", {}).items():
                i = int(k)
                if 0 <= i < NBINS:
                    m.weights[i] = float(v)
            m.resets = int(d.get("resets", 0))
        except Exception:
            return cls()
        return m
