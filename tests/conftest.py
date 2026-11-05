"""Keep engine tests and their child processes out of the user's settings."""
import pytest


@pytest.fixture(autouse=True)
def isolated_app_storage(tmp_path, monkeypatch):
    # XDG_CONFIG_HOME alone does not isolate Windows: config_dir uses APPDATA.
    # Engines persist preferences from their background loop and on shutdown.
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("AUTOLOUD_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("AUTOLOUD_MOCK", "1")


@pytest.fixture(autouse=True)
def moderate_default_p(monkeypatch):
    # the shipped default P is the author's quiet listening level; the simulator
    # scenarios were written for a moderate one
    from autoloud.core.profiles import AppConfig
    monkeypatch.setattr(AppConfig, "DEFAULT_P_LUFS", -30.0)
