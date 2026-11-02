"""The source-tracking target (preset 强力1): T = (B - X) + X * u."""
import os
os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/autoloud-test-cfg3")

from autoloud.core.controller import GainController, Targets, ControllerParams
from autoloud.core.platforms import PlatformModel, MIN_WEIGHT, RECENT_N
from autoloud.core.profiles import AppConfig, AppMix


def _ctl(**kw):
    p = ControllerParams(target_mode="source", source_range_db=6.0, source_hold_s=60.0, **kw)
    return GainController(Targets(b_lufs=-25.0), p)


def test_u_maps_the_source_between_the_platforms_into_B_minus_X_to_B():
    c = _ctl()
    for src, want in [(-35, -31.0), (-25, -31.0), (-23, -29.5), (-21, -28.0), (-19, -26.5), (-17, -25.0), (-10, -25.0)]:
        c.set_source(10.0, src, -25.0, -17.0)
        c._refresh_target(10.0)
        assert abs(c.t_lufs - want) < 1e-6, (src, c.t_lufs)


def test_target_is_B_in_fixed_mode_and_without_platforms():
    c = GainController(Targets(b_lufs=-25.0), ControllerParams())
    c.set_source(1.0, -10.0, -30.0, -20.0); c._refresh_target(1.0)
    assert c.t_lufs == -25.0
    c = _ctl()
    c.set_source(1.0, -10.0, None, None); c._refresh_target(1.0)      # platforms not learned yet
    assert c.t_lufs == -25.0
    c.set_source(1.0, -10.0, -22.0, -21.5); c._refresh_target(1.0)    # too close to be two platforms
    assert c.t_lufs == -25.0


def test_target_holds_while_unattributable_then_falls_back_to_B():
    c = _ctl()
    c.set_source(0.0, -21.0, -25.0, -17.0); c._refresh_target(0.0)
    assert abs(c.t_lufs + 28.0) < 1e-6
    c.set_source(30.0, None, None, None); c._refresh_target(30.0)     # two apps sounding: hold
    assert abs(c.t_lufs + 28.0) < 1e-6
    c.set_source(61.0, None, None, None); c._refresh_target(61.0)     # > source_hold_s: plain B
    assert c.t_lufs == -25.0


def test_platforms_learn_slowly_and_rebuild_on_a_level_shift():
    m = PlatformModel()
    for _ in range(int(MIN_WEIGHT) - 1):
        m.add(-25.0)
    assert m.platforms == (None, None)                 # not enough data yet
    for _ in range(300):                               # 2.5 min quiet platform
        m.add(-25.0)
    for _ in range(300):                               # 2.5 min loud platform
        m.add(-17.0)
    lo, hi = m.platforms
    assert abs(lo + 25) <= 0.5 and abs(hi + 17) <= 0.5 and m.ready
    # a short loud burst must not redefine the picture
    for _ in range(40):
        m.add(-12.0)
    lo2, hi2 = m.platforms
    assert abs(hi2 + 17) <= 0.5, hi2
    # the app's own volume dropped 12 dB for two minutes: rebuilt from those minutes
    for _ in range(RECENT_N):
        m.add(-37.0 if _ % 2 else -31.0)
    assert m.resets == 1
    lo3, hi3 = m.platforms
    assert hi3 < -28, (lo3, hi3)
    # silence is not a platform
    before = m.weight
    m.add(-50.0)
    assert m.weight <= before + 1e-9
    # round trip
    m2 = PlatformModel.from_dict(m.to_dict())
    assert m2.platforms == m.platforms and m2.resets == 1


def test_migration_adds_the_source_preset_and_moves_strong_ticks_to_a_preset():
    cfg = AppConfig()
    cfg.ensure_presets()
    cfg.apps["Weixin.exe"] = AppMix()
    cfg.migrate_presets(1, ["Weixin.exe"])
    assert cfg.SOURCE_PRESET in cfg.presets
    sp = cfg.presets[cfg.SOURCE_PRESET]
    assert sp["target_mode"] == "source" and sp["source_range_db"] == 5.0
    assert sp["deadband_up_db"] == 1.5 and sp["deadband_down_db"] == 1.5
    assert cfg.presets[cfg.STRONG_PRESET]["cut_pulls_fader"] is True
    assert cfg.apps["Weixin.exe"].preset == cfg.STRONG_PRESET
    assert cfg.params.target_mode == "fixed"           # the active preset is untouched
    assert cfg.params_for_preset(cfg.SOURCE_PRESET).target_mode == "source"


def test_sole_app_preset_drives_the_bus_and_source_is_fed():
    import time
    from autoloud.backends.mock import MockBackend
    from autoloud.core.engine import Engine
    cfg = AppConfig(); cfg.ensure_presets(); cfg.migrate_presets(1, [])
    cfg.params.listen_s = 2.0
    cfg.startup_master_percent = 0
    cfg.apps["music.exe"] = AppMix(preset=cfg.SOURCE_PRESET)
    be = MockBackend()
    be.add_tone_source("music.exe", lufs_amp=0.05)
    eng = Engine(be, cfg)
    eng.profile = None
    eng.start(be.list_output_devices()[0])
    try:
        time.sleep(3.0)
        assert eng.controller.params.target_mode == "source"
        assert eng.state.mode_name == cfg.SOURCE_PRESET
        sole, src, lo, hi = eng.state.source
        assert sole == "music.exe" and src is not None and src < -10
        assert eng.platform_for("music.exe").weight > 2
        eng.set_app_preset("music.exe", "")
        time.sleep(1.0)
        assert eng.controller.params.target_mode == "fixed"
        assert eng.state.mode_name == cfg.active_preset
    finally:
        eng.stop()
