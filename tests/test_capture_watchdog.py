"""A loopback stream that dies quietly is treated as silence and reopened while an app sounds."""
import os, time
os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/autoloud-test-cfg4")
from autoloud.backends.mock import MockBackend
from autoloud.core.engine import Engine
from autoloud.core.profiles import AppConfig


def test_stalled_capture_is_silence_then_restarted():
    cfg = AppConfig(); cfg.params.listen_s = 2.0; cfg.startup_master_percent = 0
    be = MockBackend(); be.add_tone_source("music.exe", lufs_amp=0.05)
    eng = Engine(be, cfg); eng.profile = None
    eng.start(be.list_output_devices()[0])
    try:
        time.sleep(2.0)
        assert eng.state.reading.has_signal
        cb = be._cb
        be._cb = lambda x: None                     # the stream "dies": no more callbacks
        time.sleep(2.3)
        # while stalled the rules see silence, not the frozen last reading
        r = eng._capture_watch(eng.state.reading, eng._last_sessions, time.perf_counter() + 10.0)
        assert not r.has_signal
        t0 = time.time()
        while time.time() - t0 < 8.0 and not (be._cb is not None and be._cb != cb and eng.state.reading.has_signal):
            time.sleep(0.2)
        assert eng.state.reading.has_signal         # watchdog reopened the capture
        assert any(k == "capture_restart" for _, k, _ in eng.state.events)
        _ = cb
    finally:
        eng.stop()
