"""Per-run log of every loudness decision, in the same words as the dashboard.

One file per launch in the config folder: ``adjust-YYYYMMDD-HHMMSS.log``.
Each line: wall time, the event as the activity card shows it, and which
apps were sounding at that moment (names and current work, no volumes).
Files older than ``KEEP_DAYS`` are removed at startup.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)
KEEP_DAYS = 14


class AdjustLog:
    def __init__(self, folder: Path, fmt: Callable[[str, dict], str]):
        self.fmt = fmt
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / time.strftime("adjust-%Y%m%d-%H%M%S.log")
        self._f = open(self.path, "a", encoding="utf-8", buffering=1)   # line-buffered
        self._f.write(f"# AutoLoud adjustment log, started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self._prune(folder)

    def __call__(self, when: float, kind: str, data: dict, apps: list[str]) -> None:
        try:
            ts = time.strftime("%H:%M:%S", time.localtime(when)) + f".{int((when % 1) * 10)}"
            txt = self.fmt(kind, data)
            who = ("    [" + ", ".join(apps) + "]") if apps else "    [—]"
            self._f.write(f"{ts}  {txt}{who}\n")
        except Exception:
            log.exception("adjust log")

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass

    @staticmethod
    def _prune(folder: Path) -> None:
        cutoff = time.time() - KEEP_DAYS * 86400
        for p in folder.glob("adjust-*.log"):
            try:
                if p.stat().st_mtime < cutoff:
                    os.remove(p)
            except Exception:
                pass
