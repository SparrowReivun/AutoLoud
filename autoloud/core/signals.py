"""Calibration signals: the music library loader (and a synthetic fallback)."""
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
