"""Abstract audio backend.

A backend provides four things:

1. enumeration of output devices (headphones / speakers),
2. a loopback capture of the engine mix for one output device,
3. control of the endpoint master volume and of every application
   session's volume on that device (plus per-session peak meters),
4. a way to play calibration sounds through the normal path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol
import os

import numpy as np


@dataclass
class OutputDevice:
    id: str
    name: str
    is_default: bool = False
    fs: int = 48000
    channels: int = 2


@dataclass
class AudioSession:
    key: str                  # stable identifier (pid:name)
    name: str                 # process / display name
    pid: int
    volume: float             # 0..1 session volume scalar
    peak: float = 0.0         # 0..1 instantaneous peak
    active: bool = False      # session state == active
    is_self: bool = False     # our own process (never touched)
    icon_path: str | None = None


CaptureCallback = Callable[[np.ndarray], None]


class Backend(Protocol):
    name: str

    # devices -----------------------------------------------------------
    def list_output_devices(self) -> list[OutputDevice]: ...
    def default_output_device(self) -> OutputDevice | None: ...

    # loopback capture ---------------------------------------------------
    def start_capture(self, device: OutputDevice, cb: CaptureCallback,
                      block_ms: int = 20) -> tuple[int, int]:
        """Start delivering float32 (frames, ch) chunks. Returns (fs, ch)."""
        ...
    def stop_capture(self) -> None: ...

    # master volume ------------------------------------------------------
    def master_range_db(self, device: OutputDevice) -> tuple[float, float]: ...
    def get_master_db(self, device: OutputDevice) -> float: ...
    def set_master_db(self, device: OutputDevice, db: float) -> None: ...
    def get_master_scalar(self, device: OutputDevice) -> float: ...
    def set_master_scalar(self, device: OutputDevice, s: float) -> None: ...

    # sessions -----------------------------------------------------------
    def list_sessions(self, device: OutputDevice) -> list[AudioSession]: ...
    def set_session_volume(self, device: OutputDevice, key: str, scalar: float) -> None: ...

    # playback for calibration ------------------------------------------
    def play(self, device: OutputDevice, audio: np.ndarray, fs: int,
             gain_scalar: Callable[[], float], loop: bool = True) -> "Player": ...


class Player(Protocol):
    def stop(self) -> None: ...
    @property
    def playing(self) -> bool: ...


def own_pid() -> int:
    return os.getpid()


def make_backend() -> Backend:
    """Pick the real backend on Windows, the simulator elsewhere."""
    import sys
    if sys.platform.startswith("win") and not os.environ.get("AUTOLOUD_MOCK"):
        from autoloud.backends.windows import WindowsBackend
        return WindowsBackend()
    from autoloud.backends.mock import MockBackend
    return MockBackend()
