"""Per-output-device profiles, persisted as JSON in the user's config dir."""
from __future__ import annotations

import json
import os
import sys
from typing import ClassVar
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


INTERNAL_PARAMS = {"min_session_gain_db"}        # not user-facing: always the code's value


def params_dict(p: ControllerParams) -> dict:
    return {k: v for k, v in asdict(p).items() if k not in INTERNAL_PARAMS}


def params_from_dict(d: dict) -> ControllerParams:
    return ControllerParams(**{k: v for k, v in d.items()
                               if k in ControllerParams.__dataclass_fields__ and k not in INTERNAL_PARAMS})


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
    preset: str = ""              # parameter preset the bus runs with while this app sounds alone ("" = the dashboard mode)
    peak_src_lufs: float | None = None   # loudest this app has been at 0 dB gain (its intrinsic peak), when sounding alone
    platforms: dict | None = None        # learned source-loudness picture (core.platforms.PlatformModel), for the source target


@dataclass
class AppConfig:
    enabled: bool = True
    selected_device_id: str = ""
    params: ControllerParams = field(default_factory=ControllerParams)
    profiles: dict[str, DeviceProfile] = field(default_factory=dict)
    music_dir: str = ""           # folder with <genre>/*.wav|mp3|flac for calibration
    start_minimized: bool = False
    sidebar_collapsed: bool = False
    startup_master_percent: float = 80.0   # set the Windows master volume to this on start (0 = leave it); restored on exit.
                                           # Not 100: a volume-up press behind the hook (elevated game in front) must still move the master so it can be read
    presets: dict[str, dict] = field(default_factory=dict)   # name -> ControllerParams as a dict
    active_preset: str = ""
    volume_keys: bool = True      # volume keys / knob adjust B instead of the Windows master volume
    sync_default_device: bool = True   # picking a device on the dashboard also makes it the Windows default output
    exit_master_percent: float = 1.0   # master volume to leave on exit / crash (-1 = what it was before start-up; unreliable, off by default)
    master_before_percent: float = -1.0   # the master level found at start-up (persisted so a crash does not lose it)
    volume_key_step_db: float = 0.4
    language: str = ""            # "" = pick from the Windows UI language on first run
    config_version: int = 4       # bump when ControllerParams defaults change
    memory_version: int = 2       # bump to forget every app's remembered gain once
    presets_version: int = 7      # bump to add new built-in presets once (2: 强力1; 3: steady triggers; 4/5/6: startup master -> 80 %, key step 0.5; 7: exit master 1 %)
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
            cfg.sync_default_device = bool(raw.get("sync_default_device", True))
            cfg.exit_master_percent = float(raw.get("exit_master_percent", 1.0))
            if int(raw.get("presets_version", 1)) < 7 and cfg.exit_master_percent < 0:
                cfg.exit_master_percent = 1.0               # exit level: 1 % by default (restore-before dropped)
            cfg.master_before_percent = float(raw.get("master_before_percent", -1.0))
            cfg.sidebar_collapsed = bool(raw.get("sidebar_collapsed", False))
            cfg.startup_master_percent = float(raw.get("startup_master_percent", 80.0))
            pv = int(raw.get("presets_version", 1))
            if (pv < 4 and cfg.startup_master_percent == 40.0) or (pv < 5 and cfg.startup_master_percent == 50.0) \
                    or (pv < 6 and cfg.startup_master_percent == 100.0):
                cfg.startup_master_percent = 80.0           # the old defaults (40 / 50 / 100 %) moved to 80 % once
            cfg.presets = {str(k): dict(v) for k, v in raw.get("presets", {}).items() if isinstance(v, dict)}
            cfg.active_preset = str(raw.get("active_preset", ""))
            cfg.volume_key_step_db = float(raw.get("volume_key_step_db", 0.4))
            if int(raw.get("presets_version", 1)) < 6 and cfg.volume_key_step_db == 1.0:
                cfg.volume_key_step_db = 0.4                # the old default moved to 0.4 dB once
            cfg.language = raw.get("language", "")
            pr = raw.get("params", {})
            if int(raw.get("config_version", 1)) >= cfg.config_version:
                cfg.params = params_from_dict(pr)
            # else: older config -> keep the new defaults for params
            cfg.mixer_range_db = float(raw.get("mixer_range_db", 40.0))
            forget = int(raw.get("memory_version", 1)) < cfg.memory_version
            strong_apps = []
            for k, v in raw.get("apps", {}).items():
                cfg.apps[k] = AppMix(**{kk: vv for kk, vv in v.items() if kk in AppMix.__dataclass_fields__})
                if forget:                                  # old memories were bent by protection cuts
                    cfg.apps[k].last_gain_db = None
                if v.get("strong") and not cfg.apps[k].preset:
                    strong_apps.append(k)
            for k, v in raw.get("profiles", {}).items():
                cfg.profiles[k] = DeviceProfile(**{kk: vv for kk, vv in v.items()
                                                   if kk in DeviceProfile.__dataclass_fields__})
            cfg.ensure_presets()
            cfg.migrate_presets(int(raw.get("presets_version", 1)), strong_apps)
            return cfg
        except Exception:
            cfg = cls(); cfg.ensure_presets(); return cfg

    def save(self) -> None:
        self.store_active_preset()
        data = {
            "enabled": self.enabled,
            "selected_device_id": self.selected_device_id,
            "music_dir": self.music_dir,
            "start_minimized": self.start_minimized,
            "volume_keys": self.volume_keys,
            "sync_default_device": self.sync_default_device,
            "exit_master_percent": self.exit_master_percent,
            "master_before_percent": self.master_before_percent,
            "sidebar_collapsed": self.sidebar_collapsed,
            "startup_master_percent": self.startup_master_percent,
            "presets": self.presets,
            "active_preset": self.active_preset,
            "volume_key_step_db": self.volume_key_step_db,
            "language": self.language,
            "config_version": self.config_version,
            "memory_version": self.memory_version,
            "presets_version": self.presets_version,
            "params": asdict(self.params),
            "profiles": {k: asdict(v) for k, v in self.profiles.items()},
            "mixer_range_db": self.mixer_range_db,
            "apps": {k: asdict(v) for k, v in self.apps.items()},
        }
        self.path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- parameter presets ----------------------------------------------
    DEFAULT_PRESET = "默认"
    # The author's tuned presets, shipped with a fresh config (the ControllerParams
    # defaults are the 默认 preset). 强力 = faster, bolder steps; 强力1 = source-tracking target.
    BUILTIN_PRESETS: ClassVar[dict] = {
        "默认": {},
        "强力": dict(onset_below_b_db=7.0, step_up_db=2.0, deadband_down_db=6.0, steady_trigger_db=4.0,
                   steady_under_db=-15.0, safety_margin_db=12.0, safety_land_db=5.0, move_ramp_s=0.2),
        "强力1": dict(onset_below_b_db=7.0, step_up_db=2.0, deadband_up_db=3.0, deadband_down_db=3.0,
                    steady_trigger_db=5.0, steady_over_blocks=2, steady_under_db=-6.0, steady_under_blocks=4,
                    safety_margin_db=16.5, safety_land_db=4.0, move_ramp_s=0.4, work_silence_s=10.0,
                    target_mode="source", source_range_db=10.0, source_hold_s=60.0),
    }

    @classmethod
    def builtin_preset(cls, name: str) -> dict:
        d = params_dict(ControllerParams())
        d.update(cls.BUILTIN_PRESETS.get(name, {}))
        return d

    def ensure_presets(self) -> None:
        """Make sure the active preset exists and mirrors the live params."""
        if not self.presets:
            for name in self.BUILTIN_PRESETS:
                self.presets[name] = self.builtin_preset(name)
            self.active_preset = self.SOURCE_PRESET          # a fresh config starts in 强力1, the author's mode
            self.params = params_from_dict(self.presets[self.active_preset])
        if self.active_preset not in self.presets:
            self.active_preset = next(iter(self.presets))
            self.params = params_from_dict(self.presets[self.active_preset])

    STRONG_PRESET = "强力"          # the old per-app "strong mode" tick becomes this preset (cut_pulls_fader)
    SOURCE_PRESET = "强力1"         # built-in: strong + the source-tracking target

    def migrate_presets(self, old_version: int, strong_apps: list[str]) -> None:
        """One-time additions: the old per-app strong tick -> a preset with
        cut_pulls_fader, and the built-in 强力1 (source-tracking) preset."""
        if old_version >= self.presets_version and not strong_apps:
            return
        self.store_active_preset()
        if strong_apps:
            if self.STRONG_PRESET not in self.presets:
                base = dict(self.presets[self.active_preset])
                base.update(safety_margin_db=min(base.get("safety_margin_db", 13.0), 12.0))
                self.presets[self.STRONG_PRESET] = base
            self.presets[self.STRONG_PRESET]["cut_pulls_fader"] = True
            for k in strong_apps:
                self.mix_for(k).preset = self.STRONG_PRESET
        if old_version < 2 and self.SOURCE_PRESET not in self.presets:
            self.presets[self.SOURCE_PRESET] = self.builtin_preset(self.SOURCE_PRESET)
        elif old_version < 3 and self.SOURCE_PRESET in self.presets:
            # a sliding target must be followed out of steady too: the copied ±4/−15 steady
            # triggers swallowed the whole X range (patched only if still at the copied values)
            sp = self.presets[self.SOURCE_PRESET]
            src = self.presets.get(self.STRONG_PRESET) or {}
            if sp.get("steady_trigger_db") == src.get("steady_trigger_db") and \
                    sp.get("steady_under_db") == src.get("steady_under_db"):
                sp["steady_trigger_db"] = 3.0; sp["steady_under_db"] = -3.0
        self.params = params_from_dict(self.presets[self.active_preset])

    def params_for_preset(self, name: str) -> ControllerParams:
        """The parameters of a named preset (the active one if unknown)."""
        self.store_active_preset()
        return params_from_dict(self.presets.get(name) or self.presets[self.active_preset])

    def store_active_preset(self) -> None:
        if self.active_preset:
            self.presets[self.active_preset] = params_dict(self.params)

    def switch_preset(self, name: str) -> None:
        if name in self.presets:
            self.store_active_preset()
            self.active_preset = name
            self.params = params_from_dict(self.presets[name])

    def add_preset(self, name: str) -> None:
        self.store_active_preset()
        self.presets[name] = params_dict(self.params)
        self.active_preset = name

    def rename_preset(self, old: str, new: str) -> None:
        if old in self.presets and new and new not in self.presets:
            self.presets[new] = self.presets.pop(old)
            if self.active_preset == old:
                self.active_preset = new

    def delete_preset(self, name: str) -> None:
        if name in self.presets and len(self.presets) > 1:
            del self.presets[name]
            if self.active_preset == name:
                self.active_preset = next(iter(self.presets))
                self.params = params_from_dict(self.presets[self.active_preset])

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
