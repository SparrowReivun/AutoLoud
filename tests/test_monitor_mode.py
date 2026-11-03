"""Monitor only: no automatic decision is made or recorded; leaving it starts clean."""
import os, time

os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/autoloud-test-cfg-mon")

from autoloud.backends.mock import MockBackend
from autoloud.core.engine import Engine
from autoloud.core.profiles import AppConfig


def test_monitor_only_consults_no_controller_and_leaves_it_fresh():
    cfg = AppConfig(); cfg.ensure_presets()
    cfg.params.listen_s = 1.0
    cfg.startup_master_percent = 0
    cfg.monitor_only = True
    be = MockBackend()
    be.add_tone_source("music.exe", lufs_amp=0.3)          # loud: an active controller would cut it
    eng = Engine(be, cfg)
    eng.profile = None
    eng.start(be.list_output_devices()[0])
    try:
        time.sleep(3.0)
        st = eng.state
        assert st.command is None                         # nothing decided, nothing logged as a decision
        assert eng.controller.phase.name == "IDLE"
        assert eng._last_cmd is None
        before = eng.controller
        eng.set_monitor_only(False)
        assert eng.controller is not before and eng.controller.phase.name == "IDLE"
        time.sleep(3.0)
        assert eng.state.command is not None              # adjusting again from a clean onset
    finally:
        eng.stop()
