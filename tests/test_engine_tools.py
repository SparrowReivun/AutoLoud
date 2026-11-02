"""Engine-level check of the tool-app detector on the simulator backend."""
import os, time
import numpy as np

os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/autoloud-test-cfg")

from autoloud.backends.mock import MockBackend
from autoloud.core.engine import Engine
from autoloud.core.profiles import AppConfig


def test_silent_active_app_gets_auto_switched_off():
    cfg = AppConfig()
    cfg.params.tool_window_s = 3.0            # short window for the test
    be = MockBackend()
    be.add_tone_source("music.exe", lufs_amp=0.1)
    be.add_tone_source("ime.exe", lufs_amp=0.0)   # active session, never sounds
    eng = Engine(be, cfg)
    dev = be.list_output_devices()[0]
    eng.start(dev)
    try:
        t0 = time.time()
        while time.time() - t0 < 20.0:
            time.sleep(0.2)
            m = cfg.apps.get("ime.exe")
            if m is not None and not m.auto:
                break
        m = cfg.apps.get("ime.exe")
        assert m is not None and m.auto is False and m.auto_set_by == "tool"
        assert cfg.apps.get("music.exe") is None or cfg.apps["music.exe"].auto
    finally:
        eng.stop()


def test_meter_drops_garbage_blocks_and_starts_clean():
    import numpy as np
    from autoloud.dsp.loudness import StreamingLoudnessMeter
    m = StreamingLoudnessMeter(48000, 2, integration_window_s=10)
    rng = np.random.default_rng(1)
    for _ in range(50):
        m.process(rng.standard_normal((960, 2)) * 0.05)
    before = m.reading().short_term
    m.process(np.full((960, 2), 1e30))                 # an uninitialised buffer from a reopened stream
    assert m.garbage_blocks == 1
    r = m.reading()
    assert r.momentary <= -70 and not r.has_signal      # clean, silent, not +300 LUFS
    for _ in range(50):
        m.process(rng.standard_normal((960, 2)) * 0.05)
    assert abs(m.reading().short_term - before) < 1.0   # back to the same level, no ringing
