"""Per-output-device profiles, persisted as JSON in the user's config dir."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

from autoloud.core.controller import ControllerParams, Targets


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


@dataclass
class AppConfig:
    enabled: bool = True
    selected_device_id: str = ""
    params: ControllerParams = field(default_factory=ControllerParams)
    profiles: dict[str, DeviceProfile] = field(default_factory=dict)
    music_dir: str = ""           # folder with <genre>/*.wav|mp3|flac for calibration
    start_minimized: bool = False
    language: str = "zh"
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
            cfg.language = raw.get("language", "zh")
            pr = raw.get("params", {})
            if int(raw.get("config_version", 1)) >= cfg.config_version:
                cfg.params = ControllerParams(**{k: v for k, v in pr.items()
                                                 if k in ControllerParams.__dataclass_fields__})
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
