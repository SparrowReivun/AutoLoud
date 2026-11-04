"""Memory diagnostics for long runs.

Why this exists: on 2026-11-03 19:24 and 2026-11-04 18:03 Windows ran out of
commit memory (Resource-Exhaustion-Detector event 2004), the Windows Audio
service died, every output device vanished and the shell hung until a hard
reset. Event 2004 named python.exe (AutoLoud) with 1.8-1.9 GB of virtual memory
while autoloud.log said "157 MB rss": RSS is only the working set, and Windows
trims the working set of a process whose pages are not being touched - which is
exactly what leaked memory looks like. So the line written here carries the
numbers that matter for a leak:

- ``private``  - the process's private committed bytes (Windows: ``PagefileUsage``,
  what event 2004 counts). A steady climb here is a leak, whatever RSS says.
- ``py``       - memory held by Python's own allocator (``sys.getallocatedblocks``
  x an average block size is not exact, so the raw block count is logged). If
  ``private`` climbs and ``py`` does not, the leak is native (COM / PortAudio /
  WinRT), not Python objects.
- ``commit``   - the system's free commit / commit limit, to see how close the
  whole machine is to the edge.

Set the environment variable ``AUTOLOUD_TRACEMALLOC=1`` to also log, every
interval, the five source lines whose Python allocations grew the most
(``tracemalloc``; costs some CPU and memory, so it is off by default).
"""
from __future__ import annotations

import gc
import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("autoloud.core.engine")

MB = 1024 * 1024


@dataclass
class MemSample:
    pid: int
    rss_mb: float
    private_mb: float | None
    py_blocks: int
    gc_objects: int
    threads: int
    commit_free_mb: float | None
    commit_limit_mb: float | None

    def line(self) -> str:
        def f(v: float | None) -> str:
            return "?" if v is None else f"{v:.0f}"
        return (f"memory: {self.rss_mb:.0f} MB rss, {f(self.private_mb)} MB private, "
                f"py {self.py_blocks} blocks / {self.gc_objects} objects, {self.threads} threads, "
                f"commit free {f(self.commit_free_mb)} / {f(self.commit_limit_mb)} MB, pid {self.pid}")


def _system_commit_mb() -> tuple[float | None, float | None]:
    """(free commit, commit limit) in MB. Windows: GlobalMemoryStatusEx's page
    file fields are the commit charge numbers. Elsewhere: RAM + swap."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullAvailPageFile / MB, st.ullTotalPageFile / MB
        except Exception:
            pass
        return None, None
    try:
        import psutil
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        return (vm.available + sw.free) / MB, (vm.total + sw.total) / MB
    except Exception:
        return None, None


def sample() -> MemSample:
    rss = 0.0
    private: float | None = None
    try:
        import psutil
        mi = psutil.Process().memory_info()
        rss = mi.rss / MB
        # Windows: 'private' (= PagefileUsage, private committed bytes).
        # Elsewhere there is no cheap equivalent; 'vms' is an upper bound.
        p = getattr(mi, "private", None)
        if p is None:
            p = getattr(mi, "vms", None)
        private = None if p is None else p / MB
    except Exception:
        pass
    free, limit = _system_commit_mb()
    return MemSample(pid=os.getpid(), rss_mb=rss, private_mb=private,
                     py_blocks=sys.getallocatedblocks(), gc_objects=len(gc.get_objects()),
                     threads=threading.active_count(), commit_free_mb=free, commit_limit_mb=limit)


class MemoryWatch:
    """Call ``tick(now)`` from any periodic loop; it logs one line every
    ``interval_s`` (the first call logs at once) and warns when the private
    bytes cross ``warn_mb`` and every further ``warn_step_mb``."""

    def __init__(self, interval_s: float = 600.0, warn_mb: float = 1024.0, warn_step_mb: float = 512.0,
                 sampler: Callable[[], MemSample] | None = None, tracemalloc_on: bool | None = None,
                 on_warn: Callable[[MemSample], None] | None = None):
        self.interval_s = interval_s
        self.warn_mb = warn_mb
        self.warn_step_mb = warn_step_mb
        self._sampler = sampler
        self._on_warn = on_warn
        self._last_t: float | None = None
        self._next_warn_mb = warn_mb
        self.last: MemSample | None = None
        if tracemalloc_on is None:
            tracemalloc_on = os.environ.get("AUTOLOUD_TRACEMALLOC", "").strip() not in ("", "0")
        self._tm_snap = None
        self._tm = False
        if tracemalloc_on:
            try:
                import tracemalloc
                if not tracemalloc.is_tracing():
                    tracemalloc.start(1)
                self._tm = True
                log.info("memory: tracemalloc on (AUTOLOUD_TRACEMALLOC)")
            except Exception:
                log.exception("tracemalloc")

    def tick(self, now: float) -> MemSample | None:
        if self._last_t is not None and now - self._last_t < self.interval_s:
            return None
        self._last_t = now
        try:
            s = (self._sampler or sample)()
        except Exception:
            log.exception("memory sample")
            return None
        self.last = s
        log.info("%s", s.line())
        if s.private_mb is not None and s.private_mb >= self._next_warn_mb:
            log.warning("memory: private bytes %.0f MB - over %.0f MB; this looks like a leak. "
                        "Restarting AutoLoud frees it (please keep autoloud.log for the report).",
                        s.private_mb, self._next_warn_mb)
            while self._next_warn_mb <= s.private_mb:
                self._next_warn_mb += self.warn_step_mb
            if self._on_warn is not None:
                try:
                    self._on_warn(s)
                except Exception:
                    log.exception("memory warn callback")
        if self._tm:
            self._tracemalloc_diff()
        return s

    def _tracemalloc_diff(self) -> None:
        try:
            import tracemalloc
            snap = tracemalloc.take_snapshot().filter_traces((
                tracemalloc.Filter(False, tracemalloc.__file__),
                tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
            ))
            if self._tm_snap is not None:
                top = snap.compare_to(self._tm_snap, "lineno")[:5]
                for st in top:
                    log.info("memory: tracemalloc %+.1f KB (%+d blocks) %s",
                             st.size_diff / 1024, st.count_diff, st.traceback)
            self._tm_snap = snap
        except Exception:
            log.exception("tracemalloc diff")
