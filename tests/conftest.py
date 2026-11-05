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
