"""A loud sound from a second app over playing content is cut on that app, not on the bus."""
import os, time
os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/autoloud-test-cfg2")
from autoloud.backends.mock import MockBackend
from autoloud.core.engine import Engine
from autoloud.core.profiles import AppConfig


def test_intrusion_is_cut_per_app_not_on_the_bus():
    cfg = AppConfig()
    cfg.params.listen_s = 2.0
    cfg.startup_master_percent = 0
    be = MockBackend()
    be.add_tone_source("music.exe", lufs_amp=0.05)
    eng = Engine(be, cfg)
    dev = be.list_output_devices()[0]
    eng.profile = None
    eng.start(dev)
    try:
        time.sleep(6.0)                              # settle on the music
        g_before = eng.state.command.total_db
        key = be.add_tone_source("ding.exe", lufs_amp=0.9, freq=880.0)   # ~25 dB louder: intrusion
        t0 = time.time(); cut = None
        while time.time() - t0 < 4.0:
            time.sleep(0.1)
            if eng._app_cut.get("ding.exe"):
                cut = eng._app_cut["ding.exe"]; break
        assert cut and cut > 3.0, cut                 # the ding alone was cut
        time.sleep(0.8)
        g_after = eng.state.command.total_db
        assert g_after > g_before - 3.0, (g_before, g_after)   # the bus did not collapse
        be.remove_source(key.key)
        t0 = time.time()
        while time.time() - t0 < 5.0 and "ding.exe" in eng._app_cut:
            time.sleep(0.2)
        assert "ding.exe" not in eng._app_cut          # lifted once it went quiet
    finally:
        eng.stop()
