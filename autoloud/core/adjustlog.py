"""Per-run log of every loudness decision, in the same words as the dashboard.

One file per launch and per dashboard mode in the logs folder:
``adjust-YYYYMMDD-HHMMSS-<mode>.log`` (mode = monitor / default / strong / strong1 / …);
switching the mode closes the file and opens a new one.
Each line: wall time, the event as the activity card shows it, and which
apps were sounding at that moment (names and current work, no volumes).
Files are kept for good (nothing is pruned).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


class AdjustLog:
    def __init__(self, folder: Path, fmt: Callable[[str, dict], str], mode: str = ""):
        self.fmt = fmt
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.mode = mode
        self._open(mode)

    def _open(self, mode: str) -> None:
        tag = f"-{mode}" if mode else ""
        self.path = self.folder / time.strftime(f"adjust-%Y%m%d-%H%M%S{tag}.log")
        self._f = open(self.path, "a", encoding="utf-8", buffering=1)   # line-buffered
        self._f.write(f"# AutoLoud adjustment log, started {time.strftime('%Y-%m-%d %H:%M:%S')}"
                      + (f", mode {mode}" if mode else "") + "\n")

    def reopen(self, mode: str) -> None:
        """The dashboard mode changed: continue in a fresh file named after it."""
        with self._lock:
            try:
                self._f.close()
            except Exception:
                pass
            self.mode = mode
            self._open(mode)

    def __call__(self, when: float, kind: str, data: dict, apps: list[str]) -> None:
        try:
            ts = time.strftime("%H:%M:%S", time.localtime(when)) + f".{int((when % 1) * 10)}"
            txt = self.fmt(kind, data)
            who = ("    [" + ", ".join(apps) + "]") if apps else "    [—]"
            with self._lock:
                self._f.write(f"{ts}  {txt}{who}\n")
        except Exception:
            log.exception("adjust log")

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass
