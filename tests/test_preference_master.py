"""Manual preference changes must not form a feedback loop with master writes."""
import pytest

from autoloud.backends.mock import MockBackend
from autoloud.core.controller import GainController, Phase
from autoloud.core.engine import Engine
from autoloud.core.profiles import AppConfig


def _engine(backend=None):
    cfg = AppConfig()
    cfg.ensure_presets()
    cfg.switch_preset(cfg.SOURCE_PRESET)
    cfg.startup_master_percent = 0
    backend = backend or MockBackend()
    eng = Engine(backend, cfg)
    eng.device = backend.list_output_devices()[0]
    eng.profile = cfg.profile_for(eng.device.id, eng.device.name)
    eng.controller = GainController(eng.profile.targets(), cfg.params)
    eng._running = True
    return eng


@pytest.mark.parametrize("parked", [None, 0.8])
@pytest.mark.parametrize("direction", [-1, 1])
def test_one_master_move_changes_p_once(parked, direction):
    eng = _engine()
    be, dev = eng.backend, eng.device
    if parked is not None:
        be.set_master_scalar(dev, parked)
        eng._master_parked = parked
    eng._applied_master_db = be.get_master_db(dev)
    eng.profile.master_ref_db = eng._applied_master_db
    before = eng.profile.b_lufs
    be.set_master_scalar(dev, be.get_master_scalar(dev) + direction * 0.02)
    for _ in range(50):
        eng._check_master_moved(be.get_master_db(dev))
        eng._apply(None, [])
    assert eng.profile.b_lufs == pytest.approx(before + direction * 0.4)


def test_failed_restore_does_not_reconsume_the_same_master_move(monkeypatch):
    eng = _engine()
    be, dev = eng.backend, eng.device
    eng._applied_master_db = be.get_master_db(dev)
    before = eng.profile.b_lufs
    be.set_master_db(dev, eng._applied_master_db - 2)
    def fail(*args):
        raise OSError("device unavailable")
    monkeypatch.setattr(be, "set_master_db", fail)
    for _ in range(50):
        eng._check_master_moved(be.get_master_db(dev))
    assert eng.profile.b_lufs == pytest.approx(before - 0.4)


def test_driver_rounding_is_not_misread_as_user_keys():
    class QuantizedBackend(MockBackend):
        def set_master_db(self, device, db):
            super().set_master_db(device, round(db / 2) * 2)
    eng = _engine(QuantizedBackend())
    eng.profile.master_ref_db = -13.1
    before = eng.profile.b_lufs
    for _ in range(50):
        eng._apply(None, [])
        eng._check_master_moved(eng.backend.get_master_db(eng.device))
    assert eng.profile.b_lufs == before
    assert eng._applied_master_db == -14


def test_adjusting_p_keeps_source_target_and_live_u():
    eng = _engine()
    ctl = eng.controller
    ctl.phase = Phase.STEADY
    ctl.set_source(1.0, -26.0, -30.0, -20.0)
    ctl._refresh_target(1.0)
    before_p, before_t = eng.profile.b_lufs, ctl.t_lufs
    for delta in (-5, 3, -2):
        eng.set_target_b(eng.profile.b_lufs + delta)
        assert ctl.params.target_mode == "source"
        assert ctl._u == pytest.approx(0.4)
        assert ctl.t_lufs - eng.profile.b_lufs == pytest.approx(before_t - before_p)
    ctl.set_source(2.0, -28.0, -30.0, -20.0)
    ctl._refresh_target(2.0)
    assert ctl._u == pytest.approx(0.2)
    assert ctl.t_lufs == pytest.approx(eng.profile.b_lufs - 11 * 0.8)


def test_config_saves_inside_test_storage(tmp_path):
    cfg = AppConfig()
    cfg.save()
    assert cfg.path().is_relative_to(tmp_path)
    assert cfg.path().is_file()
