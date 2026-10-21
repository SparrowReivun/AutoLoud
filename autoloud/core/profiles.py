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
    a_lufs: float = -32.0
    b_lufs: float = -22.0
    master_ref_db: float = -12.0
    genre: str = ""
    master_visible_in_loopback: bool = False
    beep_fader: float = 0.3      # UI fader positions, remembered for re-calibration
    music_fader: float = 0.5

    def targets(self) -> Targets:
        return Targets(a_lufs=self.a_lufs, b_lufs=self.b_lufs, master_ref_db=self.master_ref_db,
                       master_visible_in_loopback=self.master_visible_in_loopback)


@dataclass
class AppConfig:
    enabled: bool = True
    selected_device_id: str = ""
    params: ControllerParams = field(default_factory=ControllerParams)
    profiles: dict[str, DeviceProfile] = field(default_factory=dict)
    music_dir: str = ""           # folder with <genre>/*.wav|mp3|flac for calibration
    start_minimized: bool = False
    language: str = "en"
    config_version: int = 2       # bump when ControllerParams defaults change

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
            cfg.language = "en"   # English-only build
            pr = raw.get("params", {})
            if int(raw.get("config_version", 1)) >= cfg.config_version:
                cfg.params = ControllerParams(**{k: v for k, v in pr.items()
                                                 if k in ControllerParams.__dataclass_fields__})
            # else: older config -> keep the new defaults for params
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
        }
        self.path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def profile_for(self, device_id: str, device_name: str) -> DeviceProfile:
        if device_id not in self.profiles:
            self.profiles[device_id] = DeviceProfile(device_id=device_id, device_name=device_name)
        return self.profiles[device_id]
