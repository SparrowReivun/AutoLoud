"""Start with Windows: one value under HKCU\\...\\Run (per user, no admin).

The value is the command that launches this very installation: the frozen
AutoLoud.exe, or ``pythonw run.py`` from source. Reading the registry is the
truth - nothing is kept in config.json - so moving the folder or switching
between a zip and a source checkout simply shows the box unticked again.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "AutoLoud"


def available() -> bool:
    return sys.platform.startswith("win")


def command() -> str:
    """The command the Run entry should hold for this installation."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    root = Path(__file__).resolve().parents[2]
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")           # no console window at logon
    return f'"{pyw if pyw.exists() else py}" "{root / "run.py"}"'


def enabled() -> bool:
    if not available():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, VALUE)
        return str(val).strip().lower() == command().strip().lower()
    except FileNotFoundError:
        return False
    except Exception:
        log.exception("autostart read")
        return False


def set_enabled(on: bool) -> bool:
    """Write or remove the Run entry; True when the registry now matches ``on``."""
    if not available():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, VALUE)
                except FileNotFoundError:
                    pass
        log.info("autostart %s: %s", "on" if on else "off", command() if on else "-")
        return enabled() == on
    except Exception:
        log.exception("autostart write")
        return False
