"""Simulator backend for development on any OS (and for demos).

It models the Windows audio engine in software:

* "sessions" are file players (or synthetic sources) mixed together, each
  with its own session volume,
* the loopback capture is the post-session-volume mix,
* the master volume is a stored dB value (never applied to the loopback,
  exactly like the real engine),
* calibration playback is just another session owned by "us".

If a real sound device is available (sounddevice/PortAudio) the mix is also
sent to it so you can hear the result; otherwise it runs silently.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from autoloud.backends.base import AudioSession, OutputDevice, own_pid

log = logging.getLogger(__name__)

FS = 48000
CH = 2


class _Source:
    def __init__(self, key: str, name: str, audio: np.ndarray, loop: bool = True,
                 gain_fn: Callable[[], float] | None = None, is_self: bool = False):
        if audio.ndim == 1:
            audio = np.stack([audio, audio], axis=1)
        self.key, self.name = key, name
        self.audio = audio.astype(np.float32)
        self.pos = 0
        self.loop = loop
        self.gain_fn = gain_fn or (lambda: 1.0)
        self.volume = 1.0            # session volume (what the controller sets)
        self.playing = True
        self.peak = 0.0
        self.is_self = is_self

    def pull(self, n: int) -> np.ndarray:
        out = np.zeros((n, CH), dtype=np.float32)
        if not self.playing:
            return out
        i = 0
        while i < n:
            take = min(n - i, self.audio.shape[0] - self.pos)
            out[i:i + take] = self.audio[self.pos:self.pos + take]
            self.pos += take
            i += take
            if self.pos >= self.audio.shape[0]:
                if not self.loop:
                    self.playing = False
                    break
                self.pos = 0
        out *= float(self.gain_fn())
        self.peak = float(np.max(np.abs(out))) if out.size else 0.0
        return out


class _MockPlayer:
    def __init__(self, backend: "MockBackend", src: _Source):
        self._b, self._s = backend, src

    @property
    def playing(self) -> bool:
        return self._s.playing

    def stop(self) -> None:
        self._s.playing = False
        self._b.remove_source(self._s.key)


class MockBackend:
    name = "simulator"

    def __init__(self):
        self._devices = [
            OutputDevice("sim-headphones", "Simulated Headphones", True, FS, CH),
            OutputDevice("sim-speakers", "Simulated Speakers", False, FS, CH),
        ]
        self._master_db = {d.id: -12.0 for d in self._devices}
        self._sources: dict[str, _Source] = {}
        self._lock = threading.Lock()
        self._cb = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._out_stream = None

    def thread_init(self) -> None:
        pass

    # devices -----------------------------------------------------------
    def list_output_devices(self) -> list[OutputDevice]:
        return list(self._devices)

    def default_output_device(self) -> OutputDevice | None:
        return self._devices[0]

    # sources (simulated applications) -----------------------------------
    def add_source(self, key: str, name: str, audio: np.ndarray, loop=True,
                   gain_fn=None, is_self=False) -> _Source:
        s = _Source(key, name, audio, loop, gain_fn, is_self)
        with self._lock:
            self._sources[key] = s
        return s

    def add_file_source(self, path: str | Path, name: str | None = None, loop=True):
        import soundfile as sf
        data, fs = sf.read(str(path), dtype="float32", always_2d=True)
        if fs != FS:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(int(fs), FS)
            data = resample_poly(data, FS // g, fs // g, axis=0).astype(np.float32)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        key = f"{1000 + len(self._sources)}:{name or Path(path).name}"
        return self.add_source(key, name or Path(path).name, data[:, :2], loop)

    def add_tone_source(self, name="tone.exe", lufs_amp=0.1, freq=440.0, seconds=5.0):
        t = np.arange(int(FS * seconds)) / FS
        x = lufs_amp * np.sin(2 * np.pi * freq * t).astype(np.float32)
        key = f"{2000 + len(self._sources)}:{name}"
        return self.add_source(key, name, x, True)

    def remove_source(self, key: str) -> None:
        with self._lock:
            self._sources.pop(key, None)

    # capture -----------------------------------------------------------
    def start_capture(self, device: OutputDevice, cb, block_ms: int = 20) -> tuple[int, int]:
        self.stop_capture()
        self._cb = cb
        self._running = True
        n = int(FS * block_ms / 1000)

        try:
            import sounddevice as sd
            self._out_stream = sd.OutputStream(samplerate=FS, channels=CH, dtype="float32",
                                               blocksize=n)
            self._out_stream.start()
        except Exception:
            self._out_stream = None

        def run():
            period = n / FS
            next_t = time.perf_counter()
            while self._running:
                mix = np.zeros((n, CH), dtype=np.float32)
                with self._lock:
                    for s in list(self._sources.values()):
                        mix += s.pull(n) * s.volume
                try:
                    self._cb(mix)
                except Exception:
                    log.exception("mock capture callback")
                if self._out_stream is not None:
                    try:
                        g = 10 ** (self._master_db[device.id] / 20)
                        self._out_stream.write(np.clip(mix * g, -1, 1))
                        continue   # the device paces us
                    except Exception:
                        self._out_stream = None
                next_t += period
                dt = next_t - time.perf_counter()
                if dt > 0:
                    time.sleep(dt)
                else:
                    next_t = time.perf_counter()

        self._thread = threading.Thread(target=run, daemon=True, name="mock-capture")
        self._thread.start()
        return FS, CH

    def stop_capture(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._out_stream is not None:
            try:
                self._out_stream.stop(); self._out_stream.close()
            except Exception:
                pass
            self._out_stream = None

    # master ------------------------------------------------------------
    def master_range_db(self, device):
        return (-65.25, 0.0)

    def get_master_db(self, device):
        return self._master_db[device.id]

    def set_master_db(self, device, db):
        lo, hi = self.master_range_db(device)
        self._master_db[device.id] = max(lo, min(hi, db))

    def get_master_scalar(self, device):
        lo, hi = self.master_range_db(device)
        return (self._master_db[device.id] - lo) / (hi - lo)

    def set_master_scalar(self, device, s):
        lo, hi = self.master_range_db(device)
        self._master_db[device.id] = lo + (hi - lo) * max(0.0, min(1.0, s))

    # sessions ----------------------------------------------------------
    def list_sessions(self, device) -> list[AudioSession]:
        with self._lock:
            return [AudioSession(key=s.key, name=s.name, pid=int(s.key.split(":")[0]),
                                 volume=s.volume, peak=s.peak * s.volume,
                                 active=s.playing, is_self=s.is_self)
                    for s in self._sources.values()]

    def set_session_volume(self, device, key, scalar):
        with self._lock:
            s = self._sources.get(key)
            if s is not None:
                s.volume = max(0.0, min(1.0, scalar))

    # playback ----------------------------------------------------------
    def play(self, device, audio, fs, gain_scalar, loop=True):
        if fs != FS:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(int(fs), FS)
            audio = resample_poly(audio, FS // g, fs // g, axis=0)
        key = f"{own_pid()}:autoloud-cal-{int(time.time()*1000)}"
        src = self.add_source(key, "AutoLoud (calibration)", audio, loop, gain_scalar, is_self=True)
        return _MockPlayer(self, src)

    def close(self):
        self.stop_capture()


class MockMediaProvider:
    """Scriptable media metadata for the simulator / tests."""
    def __init__(self):
        self.infos = []

    def snapshot(self):
        return list(self.infos)
