"""Per-output-device profiles, persisted as JSON in the user's config dir."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

from autoloud.core.controller import ControllerParams, Targets


def default_language() -> str:
    """zh when the Windows UI (or POSIX locale) language is Chinese, else en."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            return "zh" if lang_id == 0x04 else "en"
    except Exception:
        pass
    import locale
    loc = (locale.getlocale()[0] or os.environ.get("LANG", "")).lower()
    return "zh" if loc.startswith("zh") else "en"


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "AutoLoud"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class DeviceProfile:
    device_id: str
    device_name: str
    calibrated: bool = False
    b_lufs: float = -22.0
    master_ref_db: float = -12.0
    genre: str = ""
    master_visible_in_loopback: bool = False
    music_fader: float = 0.0     # UI fader position, remembered for re-calibration (0 = bottom)

    def targets(self) -> Targets:
        return Targets(b_lufs=self.b_lufs, master_ref_db=self.master_ref_db,
                       master_visible_in_loopback=self.master_visible_in_loopback)


@dataclass
class AppMix:
    """Per-application mixer strip (keyed by process name)."""
    offset_db: float = 0.0        # user fader, -40..+40 dB, relative to the other apps
    auto: bool = True             # whether the bus auto-loudness gain applies to this app
    alias: str = ""               # user label (e.g. "Apple Music" for AMP)
    last_gain_db: float | None = None   # bus gain this app ended up with last time (rule 1)
    auto_set_by: str = ""         # "" | "tool" (auto switched off by the tool-app detector) | "user" (never touch again)


@dataclass
class AppConfig:
    enabled: bool = True
    selected_device_id: str = ""
    params: ControllerParams = field(default_factory=ControllerParams)
    profiles: dict[str, DeviceProfile] = field(default_factory=dict)
    music_dir: str = ""           # folder with <genre>/*.wav|mp3|flac for calibration
    start_minimized: bool = False
    sidebar_collapsed: bool = False
    startup_master_percent: float = 40.0   # set the Windows master volume to this on start (0 = leave it)
    volume_keys: bool = True      # volume keys / knob adjust B instead of the Windows master volume
    volume_key_step_db: float = 1.0
    language: str = ""            # "" = pick from the Windows UI language on first run
    config_version: int = 4       # bump when ControllerParams defaults change
    mixer_range_db: float = 15.0  # fader top (+dB); bottom is mute
    apps: dict[str, AppMix] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def path(cls) -> Path:
        return config_dir() / "config.json"

    @classmethod
    def load(cls) -> "AppConfig":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            cfg = cls()
            cfg.enabled = raw.get("enabled", True)
            cfg.selected_device_id = raw.get("selected_device_id", "")
            cfg.music_dir = raw.get("music_dir", "")
            cfg.start_minimized = raw.get("start_minimized", False)
            cfg.volume_keys = bool(raw.get("volume_keys", True))
            cfg.sidebar_collapsed = bool(raw.get("sidebar_collapsed", False))
            cfg.startup_master_percent = float(raw.get("startup_master_percent", 40.0))
            cfg.volume_key_step_db = float(raw.get("volume_key_step_db", 1.0))
            cfg.language = raw.get("language", "")
            pr = raw.get("params", {})
            if int(raw.get("config_version", 1)) >= cfg.config_version:
                internal = {"min_session_gain_db"}        # not user-facing: always the code's value
                cfg.params = ControllerParams(**{k: v for k, v in pr.items()
                                                 if k in ControllerParams.__dataclass_fields__ and k not in internal})
            # else: older config -> keep the new defaults for params
            cfg.mixer_range_db = float(raw.get("mixer_range_db", 40.0))
            for k, v in raw.get("apps", {}).items():
                cfg.apps[k] = AppMix(**{kk: vv for kk, vv in v.items() if kk in AppMix.__dataclass_fields__})
            for k, v in raw.get("profiles", {}).items():
                cfg.profiles[k] = DeviceProfile(**{kk: vv for kk, vv in v.items()
                                                   if kk in DeviceProfile.__dataclass_fields__})
            return cfg
        except Exception:
            return cls()

    def save(self) -> None:
        data = {
            "enabled": self.enabled,
            "selected_device_id": self.selected_device_id,
            "music_dir": self.music_dir,
            "start_minimized": self.start_minimized,
            "volume_keys": self.volume_keys,
            "sidebar_collapsed": self.sidebar_collapsed,
            "startup_master_percent": self.startup_master_percent,
            "volume_key_step_db": self.volume_key_step_db,
            "language": self.language,
            "config_version": self.config_version,
            "params": asdict(self.params),
            "profiles": {k: asdict(v) for k, v in self.profiles.items()},
            "mixer_range_db": self.mixer_range_db,
            "apps": {k: asdict(v) for k, v in self.apps.items()},
        }
        self.path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def display_name(self, app_name: str) -> str:
        m = self.apps.get(app_name)
        return (m.alias.strip() if m and m.alias.strip() else app_name)

    def mix_for(self, app_name: str) -> AppMix:
        if app_name not in self.apps:
            self.apps[app_name] = AppMix()
        return self.apps[app_name]

    def profile_for(self, device_id: str, device_name: str) -> DeviceProfile:
        if device_id not in self.profiles:
            self.profiles[device_id] = DeviceProfile(device_id=device_id, device_name=device_name)
        return self.profiles[device_id]
