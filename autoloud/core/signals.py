"""Calibration signals: the "harsh beep" and the music library loader."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np

AUDIO_EXT = {".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif"}


def assets_dir() -> Path:
    """`assets/` next to the package, or next to the frozen exe."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2] / "assets"


def harsh_beep(fs: int = 48000, seconds: float = 2.0, level_dbfs: float = -6.0) -> np.ndarray:
    """A piercing 2000 Hz sine beep train (150 ms on / 150 ms off).

    Peak level is ``level_dbfs``; this is the reference digital level of the
    stimulus, the user's fader scales it during calibration.
    """
    t = np.arange(int(fs * seconds)) / fs
    tone = np.sin(2 * np.pi * 2000.0 * t)
    period, on = 0.30, 0.15
    env = ((t % period) < on).astype(np.float64)
    # 5 ms raised-cosine edges on each burst to avoid clicks
    ramp = int(0.005 * fs)
    edges = np.flatnonzero(np.diff(env) != 0) + 1
    fade = 0.5 * (1 - np.cos(np.linspace(0, np.pi, ramp)))
    for e in edges:
        if env[e] > 0:      # rising edge
            env[e:e + ramp] = fade[: len(env[e:e + ramp])]
        else:               # falling edge
            env[e - ramp:e] = fade[::-1][: len(env[e - ramp:e])]
    x = tone * env
    x *= 10 ** (level_dbfs / 20)
    return np.stack([x, x], axis=1).astype(np.float32)


def default_music_dir() -> Path:
    """Bundled calibration music (assets/music)."""
    return assets_dir() / "music"


def list_genres(music_dir: str | Path | None) -> dict[str, list[Path]]:
    """{genre: [files]} from ``music_dir/<genre>/*``. Empty if nothing there."""
    out: dict[str, list[Path]] = {}
    root = Path(music_dir) if music_dir else default_music_dir()
    if not root.is_dir():
        root = default_music_dir()
        if not root.is_dir():
            return out
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        files = sorted(f for f in sub.iterdir() if f.suffix.lower() in AUDIO_EXT)
        if files:
            out[sub.name] = files
    loose = sorted(f for f in root.iterdir() if f.suffix.lower() in AUDIO_EXT)
    if loose:
        out.setdefault("(unsorted)", []).extend(loose)
    return out


def load_audio(path: str | Path, target_fs: int) -> np.ndarray:
    """Load any supported file as float32 (frames, 2) at ``target_fs``."""
    import soundfile as sf
    data, fs = sf.read(str(path), dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    data = data[:, :2]
    if fs != target_fs:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(int(fs), int(target_fs))
        data = resample_poly(data, target_fs // g, fs // g, axis=0).astype(np.float32)
    return data


def fallback_music(fs: int = 48000, seconds: float = 12.0) -> np.ndarray:
    """Synthetic 'music' (chord pad + pulse) used only when no music files exist."""
    t = np.arange(int(fs * seconds)) / fs
    chord = sum(np.sin(2 * np.pi * f * t) / (i + 1) for i, f in enumerate([110, 165, 220, 330, 440]))
    pulse = (np.sin(2 * np.pi * 2.0 * t) > 0.8).astype(float)
    kick = np.sin(2 * np.pi * 60 * t) * np.exp(-((t % 0.5) * 12)) * 0.8
    x = 0.25 * chord * (0.6 + 0.4 * pulse) + kick
    x /= np.max(np.abs(x)) / 0.5
    return np.stack([x, x], axis=1).astype(np.float32)
