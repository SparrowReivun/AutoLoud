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


def test_mode_slugs_and_log_rotation(tmp_path, monkeypatch):
    from autoloud.core.profiles import mode_slug, log_dir
    from autoloud.core.meterlog import MeterLog
    from autoloud.core.adjustlog import AdjustLog
    assert [mode_slug(m) for m in ("monitor", "默认", "强力", "强力1", "My Preset-2", "自定义")] == \
        ["monitor", "default", "strong", "strong1", "mypreset-2", "preset"]
    monkeypatch.setenv("AUTOLOUD_LOG_DIR", str(tmp_path / "logs"))
    assert log_dir() == tmp_path / "logs" and (tmp_path / "logs").is_dir()

    cfg = AppConfig(); cfg.ensure_presets(); cfg.startup_master_percent = 0; cfg.monitor_only = True
    be = MockBackend(); be.add_tone_source("music.exe", lufs_amp=0.05)
    eng = Engine(be, cfg); eng.profile = None
    eng.adjust_log = AdjustLog(tmp_path / "logs", lambda k, d: k, eng.dashboard_mode_slug())
    eng.on_event = eng.adjust_log
    eng.meter_log = MeterLog(tmp_path / "logs", eng.dashboard_mode_slug())
    eng.start(be.list_output_devices()[0])
    try:
        time.sleep(1.5)
        eng._log_volume("user_master", -1.0)
        first = eng.meter_log.run
        assert first.endswith("-monitor")
        eng.rotate_logs()                                   # same mode: nothing happens
        assert eng.meter_log.run == first
        time.sleep(1.1)                                     # a new second -> a new timestamp
        cfg.switch_preset(cfg.SOURCE_PRESET)
        eng.set_monitor_only(False)                         # rotates
        assert eng.meter_log.run.endswith("-strong1") and eng.meter_log.run != first
        time.sleep(1.5)
        eng._log_volume("p_slider", +2.0)
    finally:
        eng.stop(); eng.meter_log.close(); eng.adjust_log.close()
    names = sorted(p.name for p in (tmp_path / "logs").iterdir())
    assert any(n.startswith("volume-") and n.endswith("-monitor.csv") for n in names)
    assert any(n.startswith("adjust-") and n.endswith("-strong1.log") for n in names)
    assert len([n for n in names if n.startswith("meters-")]) == 2
    mon = next(p for p in (tmp_path / "logs").iterdir() if p.name.startswith("volume-") and "monitor" in p.name)
    s1 = next(p for p in (tmp_path / "logs").iterdir() if p.name.startswith("volume-") and "strong1" in p.name)
    assert "user_master" in mon.read_text() and "p_slider" not in mon.read_text()
    assert "p_slider" in s1.read_text()


def test_autostart_is_a_no_op_off_windows():
    from autoloud.core import autostart
    import sys
    if sys.platform.startswith("win"):
        return
    assert not autostart.available() and not autostart.enabled() and not autostart.set_enabled(True)
    assert "run.py" in autostart.command() or "AutoLoud" in autostart.command()
