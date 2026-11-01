"""Raw measurement log for later analysis: everything the program measures,
as CSV, one pair of files per launch in the config folder. Never pruned.

``meters-<ts>.csv``  one row every 100 ms (the meter hop):
    time, momentary_lufs, short_term_lufs, integrated_lufs, peak_dbfs,
    gain_total_db, session_gain_db, master_boost_db, phase, b_lufs, master_db
    (loudness columns are the raw loopback values; add master_boost_db to get
    what the dashboard shows when the master is not in the loopback)

``apps-<ts>.csv``  one row per sounding-or-active app per session poll (0.5 s):
    time, app, key, active, peak, volume, bus_gain_db, fader_db
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


class MeterLog:
    FLUSH_S = 5.0

    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        self._fm = open(folder / f"meters-{ts}.csv", "a", encoding="utf-8")
        self._fa = open(folder / f"apps-{ts}.csv", "a", encoding="utf-8")
        self._fm.write("time,momentary_lufs,short_term_lufs,integrated_lufs,peak_dbfs,"
                       "gain_total_db,session_gain_db,master_boost_db,phase,b_lufs,master_db\n")
        self._fa.write("time,app,key,active,peak,volume,bus_gain_db,fader_db\n")
        self._last_flush = time.time()
        self._last_meter_row = 0.0

    @staticmethod
    def _ts() -> str:
        t = time.time()
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"

    def meters(self, r, cmd, b_lufs: float, master_db: float, now: float) -> None:
        """Called every control tick; writes at most one row per 100 ms."""
        if now - self._last_meter_row < 0.1:
            return
        self._last_meter_row = now
        try:
            if cmd is not None:
                g = f"{cmd.total_db:.2f},{cmd.session_gain_db:.2f},{cmd.master_offset_db:.2f},{cmd.phase.value}"
            else:
                g = ",,,"
            self._fm.write(f"{self._ts()},{r.momentary:.2f},{r.short_term:.2f},{r.integrated:.2f},"
                           f"{r.peak_dbfs:.2f},{g},{b_lufs:.2f},{master_db:.2f}\n")
            self._maybe_flush()
        except Exception:
            log.exception("meter log")

    def apps(self, sessions, bus_gain_db: float, faders: dict) -> None:
        """Called every session poll."""
        try:
            ts = self._ts()
            for s in sessions:
                if s.is_self or not (s.active or s.peak > 0.001):
                    continue
                name = s.name.replace(",", " ")
                self._fa.write(f"{ts},{name},{s.key},{int(s.active)},{s.peak:.4f},{s.volume:.3f},"
                               f"{bus_gain_db:.2f},{faders.get(s.name, 0.0):.1f}\n")
            self._maybe_flush()
        except Exception:
            log.exception("app log")

    def _maybe_flush(self) -> None:
        t = time.time()
        if t - self._last_flush > self.FLUSH_S:
            self._last_flush = t
            self._fm.flush(); self._fa.flush()

    def close(self) -> None:
        for f in (self._fm, self._fa):
            try:
                f.close()
            except Exception:
                pass
