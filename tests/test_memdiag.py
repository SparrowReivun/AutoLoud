import logging

from autoloud.core.memdiag import MemSample, MemoryWatch, sample


def _s(private):
    return MemSample(pid=1, rss_mb=150, private_mb=private, py_blocks=10, gc_objects=20,
                     threads=5, commit_free_mb=1000, commit_limit_mb=32000)


def test_sample_runs_here():
    s = sample()
    assert s.pid > 0 and s.rss_mb > 0 and s.py_blocks > 0
    line = s.line()
    assert "MB private" in line and "pid" in line and "commit free" in line


def test_logs_first_tick_then_every_interval(caplog):
    w = MemoryWatch(interval_s=600, sampler=lambda: _s(200), tracemalloc_on=False)
    with caplog.at_level(logging.INFO):
        assert w.tick(0.0) is not None          # first call logs at once
        assert w.tick(300.0) is None
        assert w.tick(600.0) is not None
    assert sum("memory:" in r.message for r in caplog.records) == 2


def test_warns_on_threshold_and_steps():
    vals = iter([500, 1100, 1200, 1700, 1800, 2300])
    warned = []
    w = MemoryWatch(interval_s=0, warn_mb=1024, warn_step_mb=512,
                    sampler=lambda: _s(next(vals)), tracemalloc_on=False,
                    on_warn=lambda smp: warned.append(smp.private_mb))
    for t in range(6):
        w.tick(float(t))
    # 1100 crosses 1024; 1200 does not cross 1536; 1700 does; 1800 not (next 2048); 2300 does
    assert warned == [1100, 1700, 2300]


def test_unknown_private_never_warns():
    warned = []
    w = MemoryWatch(interval_s=0, sampler=lambda: _s(None), tracemalloc_on=False,
                    on_warn=lambda smp: warned.append(smp))
    w.tick(0.0)
    assert warned == [] and "? MB private" in w.last.line()


def test_tracemalloc_diff_logs(caplog):
    w = MemoryWatch(interval_s=0, sampler=lambda: _s(100), tracemalloc_on=True)
    keep = []
    with caplog.at_level(logging.INFO):
        w.tick(0.0)
        keep.append([bytearray(1000) for _ in range(200)])
        w.tick(1.0)
    import tracemalloc
    tracemalloc.stop()
    assert any("tracemalloc +" in r.message for r in caplog.records)


def test_engine_memory_watch_raises_activity_event(monkeypatch):
    import os
    os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/autoloud-test-cfg")
    import autoloud.core.memdiag as md
    from autoloud.backends.mock import MockBackend
    from autoloud.core.engine import Engine
    from autoloud.core.profiles import AppConfig
    monkeypatch.setattr(md, "sample", lambda: _s(1500))
    eng = Engine(MockBackend(), AppConfig())
    seen = []
    eng.on_event = lambda when, kind, data, apps: seen.append((kind, data))
    eng._memory_watch(1.0)
    assert ("memory_high", {"mb": 1500}) in seen
    from autoloud.ui.i18n import t
    assert "1500" in t("ev.memory_high").format(mb=1500)
