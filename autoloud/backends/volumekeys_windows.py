"""Take over the volume keys (keyboard F-keys with a speaker icon, USB /
Bluetooth volume knobs): Windows turns them all into VK_VOLUME_UP / DOWN key
events, which a low-level keyboard hook can see and swallow before the shell
changes the master volume. We then move the comfort level B instead.

The hook runs on its own thread with a message loop (a WH_KEYBOARD_LL hook
needs one). Mute is left to Windows. The hook cannot see keys while an
elevated (admin) window is in the foreground; those presses then do what
they always did.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAE, 0xAF
WM_QUIT = 0x0012


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


_HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class VolumeKeys:
    """``on_step(direction)`` is called with +1 / -1 on every volume key press
    (key repeat included). ``enabled`` can be flipped at any time."""

    def __init__(self, on_step: Callable[[int], None], enabled: Callable[[], bool] = lambda: True):
        self.on_step = on_step
        self.enabled = enabled           # read live, so a settings change applies at once
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._hook = None
        self._proc = None          # keep the CFUNCTYPE object alive

    @staticmethod
    def available() -> bool:
        return sys.platform.startswith("win")

    def start(self) -> None:
        if not self.available() or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="autoloud-volumekeys", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        try:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        except Exception:
            pass
        self._thread.join(timeout=1.0)
        self._thread = None

    # ------------------------------------------------------------------
    def _run(self) -> None:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.CallNextHookEx.restype = ctypes.c_long
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        self._tid = kernel32.GetCurrentThreadId()

        def proc(n_code, w_param, l_param):
            if n_code >= 0 and self.enabled():
                kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                if kb.vkCode in (VK_VOLUME_UP, VK_VOLUME_DOWN):
                    if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        try:
                            self.on_step(+1 if kb.vkCode == VK_VOLUME_UP else -1)
                        except Exception:
                            log.exception("volume key handler")
                    return 1                      # swallow both down and up: Windows never sees it
            return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

        self._proc = _HOOKPROC(proc)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            log.error("volume key hook failed: %s", ctypes.GetLastError())
            return
        log.info("volume keys hooked: F11/F12/knob now move comfort level B")
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            log.info("volume keys released")
