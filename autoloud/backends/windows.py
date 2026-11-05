"""Windows backend: WASAPI loopback (pyaudiowpatch) + Core Audio volume
control (pycaw / comtypes).

Nothing here needs a driver. Loopback gives us the engine mix *after*
session volumes, so the controller measures the result of its own actions.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import Future
from typing import Callable

import numpy as np

from autoloud.backends.base import AudioSession, OutputDevice, own_pid

log = logging.getLogger(__name__)

try:
    import pyaudiowpatch as pyaudio
except ImportError as e:  # pragma: no cover
    raise ImportError("pyaudiowpatch is required on Windows: pip install PyAudioWPatch") from e

import comtypes  # type: ignore
from comtypes import CLSCTX_ALL  # type: ignore
from pycaw.pycaw import (  # type: ignore
    AudioUtilities, IAudioEndpointVolume, IAudioSessionManager2,
    IAudioMeterInformation, ISimpleAudioVolume, IAudioSessionControl2,
)
from pycaw.constants import AudioSessionState  # type: ignore
from pycaw.api.mmdeviceapi.depend.structures import PROPERTYKEY  # type: ignore
from pycaw.api.mmdeviceapi import IMMDevice  # type: ignore
from pycaw.api.audiopolicy import IAudioSessionControl, IAudioSessionNotification  # type: ignore
from comtypes import COMObject  # type: ignore
from comtypes import GUID  # type: ignore

BACKEND_VERSION = "0.9-session-manager-lifetime"

# ---- IPolicyConfig (undocumented, what the Sound control panel uses to set the default device) ----
from ctypes import c_void_p, c_longlong, POINTER as _PTR  # noqa: E402
from ctypes.wintypes import LPCWSTR, DWORD, INT  # noqa: E402
from comtypes import IUnknown, COMMETHOD, HRESULT  # noqa: E402

CLSID_PolicyConfigClient = GUID("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}")


class IPolicyConfig(IUnknown):
    """Windows 7+ layout (12 methods). Only SetDefaultEndpoint is used; the
    others are listed so the vtable slot is right."""
    _iid_ = GUID("{F8679F50-850A-41CF-9C72-430F290290C8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetMixFormat", (["in"], LPCWSTR), (["out"], _PTR(c_void_p))),
        COMMETHOD([], HRESULT, "GetDeviceFormat", (["in"], LPCWSTR), (["in"], INT), (["out"], _PTR(c_void_p))),
        COMMETHOD([], HRESULT, "ResetDeviceFormat", (["in"], LPCWSTR)),
        COMMETHOD([], HRESULT, "SetDeviceFormat", (["in"], LPCWSTR), (["in"], c_void_p), (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "GetProcessingPeriod", (["in"], LPCWSTR), (["in"], INT),
                  (["out"], _PTR(c_longlong)), (["out"], _PTR(c_longlong))),
        COMMETHOD([], HRESULT, "SetProcessingPeriod", (["in"], LPCWSTR), (["in"], _PTR(c_longlong))),
        COMMETHOD([], HRESULT, "GetShareMode", (["in"], LPCWSTR), (["out"], _PTR(c_void_p))),
        COMMETHOD([], HRESULT, "SetShareMode", (["in"], LPCWSTR), (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "GetPropertyValue", (["in"], LPCWSTR), (["in"], INT), (["in"], c_void_p),
                  (["out"], _PTR(c_void_p))),
        COMMETHOD([], HRESULT, "SetPropertyValue", (["in"], LPCWSTR), (["in"], INT), (["in"], c_void_p),
                  (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], LPCWSTR), (["in"], DWORD)),
        COMMETHOD([], HRESULT, "SetEndpointVisibility", (["in"], LPCWSTR), (["in"], INT)),
    ]

# ---- COM methods that hand back a CoTaskMemAlloc'd string ----------------
# pycaw declares GetId / GetDisplayName / GetSessionInstanceIdentifier... as
# [out] LPWSTR. comtypes turns that into a Python str and never calls
# CoTaskMemFree on the buffer, so every call leaks the string natively - it
# never shows in Python's allocator nor (once Windows trims the working set) in
# RSS. _list_sessions asked every session for its instance id twice a second;
# with a dozen sessions (Chrome alone holds several) that is a steady native
# leak until Windows runs out of commit memory (event 2004 named python.exe
# with 1.8-1.9 GB on 2026-11-03 19:24 and 2026-11-04 18:03; Windows Audio died
# seconds later). Call the raw vtable slot instead and free the buffer.
from ctypes import byref as _byref, cast as _cast, windll as _windll  # noqa: E402
from ctypes.wintypes import LPWSTR as _LPWSTR  # noqa: E402

_CoTaskMemFree = _windll.ole32.CoTaskMemFree
_CoTaskMemFree.argtypes = [c_void_p]
_CoTaskMemFree.restype = None


def _com_str(obj, iface: type, method: str) -> str:
    """Call ``iface.method([out] LPWSTR*)`` on ``obj`` and free the returned
    buffer. Falls back to the comtypes wrapper (leaking, as before) if the raw
    slot is not reachable under this comtypes version."""
    raw = getattr(obj, f"_{iface.__name__}__com_{method}", None)
    if raw is None:
        return getattr(obj, method)() or ""
    p = _LPWSTR()
    raw(_byref(p))                        # HRESULT failures raise COMError
    addr = _cast(p, c_void_p).value
    try:
        return p.value or ""
    finally:
        if addr:
            _CoTaskMemFree(addr)


# DEVPKEY_Device_FriendlyName = {a45c254e-df1c-4efd-8020-67d146a850e0}, 14
_PKEY_FRIENDLY_NAME = PROPERTYKEY()
_PKEY_FRIENDLY_NAME.fmtid = GUID("{a45c254e-df1c-4efd-8020-67d146a850e0}")
_PKEY_FRIENDLY_NAME.pid = 14


class _ComWorker:
    """One thread that owns every Core Audio COM object.

    comtypes interface pointers must be used *and released* on the thread
    (apartment) that created them; letting Python's GC release them from a
    random thread produces access violations. So all COM work is funnelled
    through this single MTA thread, and no COM object ever leaves it.
    """

    def __init__(self, on_restart: Callable[[], None] | None = None):
        self._on_restart = on_restart
        self._lock = threading.Lock()
        self._restarts = 0
        self._busy_since = 0.0        # monotonic time the worker took its current call
        self._last_slow_log = 0.0
        self._spawn()

    def _spawn(self):
        self._q: "queue.Queue[tuple[Callable, Future] | None]" = queue.Queue()
        self._t = threading.Thread(target=self._run, daemon=True, name=f"autoloud-com-{self._restarts}")
        self._t.start()

    def restart(self, q=None):
        """A COM call hung (the audio service did not answer): abandon that
        thread - it cannot be killed, it stays blocked inside Windows - and
        serve all further calls from a fresh one with fresh COM objects."""
        with self._lock:
            if q is not None and q is not self._q:
                return                # another caller already restarted this one
            self._restarts += 1
            self._busy_since = 0.0
            log.error("COM worker hung; starting worker #%d (the stuck thread is abandoned)", self._restarts)
            old_q = self._q
            self._spawn()
            try:                      # fail whatever was still queued behind the hung call
                while True:
                    item = old_q.get_nowait()
                    if item is not None:
                        item[1].set_exception(TimeoutError("COM worker restarted"))
            except queue.Empty:
                pass
        if self._on_restart is not None:
            try:
                self._on_restart()
            except Exception:
                log.exception("on_restart")

    def _run(self):
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
        while True:
            item = self._q.get()
            if item is None:
                break
            fn, fut = item
            self._busy_since = time.monotonic()
            try:
                fut.set_result(fn())
            except BaseException as e:  # noqa: BLE001
                fut.set_exception(e)
            self._busy_since = 0.0
        import gc
        gc.collect()
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass

    HANG_S = 20.0     # a call older than this is a hang, not a slow audio service

    def call(self, fn: Callable, timeout: float = 5.0):
        if threading.current_thread() is self._t:
            return fn()
        fut: Future = Future()
        q = self._q
        q.put((fn, fut))
        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            # The audio service can stall for a while (device change, a virtual
            # audio driver reconfiguring); the call may still complete. Only a
            # worker stuck in ONE call for HANG_S is abandoned - and once.
            with self._lock:
                stuck = (q is self._q and self._busy_since
                         and time.monotonic() - self._busy_since >= self.HANG_S)
            if stuck:
                self.restart(q)
            else:
                now = time.monotonic()
                if now - self._last_slow_log > 10.0:
                    self._last_slow_log = now
                    log.warning("audio service slow: a Core Audio call did not answer within %.0f s "
                                "(skipping this tick; the worker is kept)", timeout)
            raise

    def stop(self):
        self._q.put(None)
        self._t.join(timeout=2.0)


class _SessionCreated(COMObject):
    """IAudioSessionNotification: Windows calls OnSessionCreated the moment an
    app opens a new audio session - before it has played a single buffer. The
    handler returns the volume scalar the session should start at (or None),
    and it is applied right here, on Windows' own callback thread, so the
    session never plays at 100 % while we wait for the next poll."""
    _com_interfaces_ = (IAudioSessionNotification,)

    def __init__(self, backend: "WindowsBackend", handler):
        super().__init__()
        self._backend = backend
        self._handler = handler

    def OnSessionCreated(self, new_session):
        try:
            ctl2 = new_session.QueryInterface(IAudioSessionControl2)
            pid = int(ctl2.GetProcessId())
            name = self._backend._process_name(pid, ctl2)
            scalar = self._handler(f"{pid}:{name}", name, pid)
            if scalar is not None:
                vol = new_session.QueryInterface(ISimpleAudioVolume)
                vol.SetMasterVolume(float(max(0.0, min(1.0, scalar))), None)
        except Exception:
            log.exception("session created callback")
        return 0


class WindowsBackend:
    name = "windows-wasapi"

    def __init__(self):
        log.info("WindowsBackend %s", BACKEND_VERSION)
        self._pa = pyaudio.PyAudio()
        self._stream = None
        self._cb = None
        # --- everything below is touched ONLY on the COM worker thread ---
        self._session_cache: dict[str, dict] = {}      # key -> {session instance: ISimpleAudioVolume}
        self._endpoint_cache: dict[str, object] = {}   # device id -> IAudioEndpointVolume
        self._meter_cache: dict[str, object] = {}      # session instance -> IAudioMeterInformation
        self._com = _ComWorker(on_restart=self._drop_caches)

    def _drop_caches(self) -> None:
        # the old COM objects belong to the abandoned thread: never touch them again
        self._session_cache = {}
        self._endpoint_cache = {}
        self._meter_cache = {}

    def thread_init(self) -> None:
        """Kept for interface compatibility; COM lives on its own thread now."""

    # ---- helpers that run on the COM thread --------------------------------
    @staticmethod
    def _get_imm_device(device_id: str):
        enum = AudioUtilities.GetDeviceEnumerator()
        return enum.GetDevice(device_id)

    # ------------------------------------------------------------------
    # devices
    def list_output_devices(self) -> list[OutputDevice]:
        return self._com.call(self._list_output_devices)

    def _list_output_devices(self) -> list[OutputDevice]:
        """Minimal enumeration: id + friendly name only.

        pycaw's GetAllDevices() walks *every* property of every device and
        crashes with access violations on some Windows 11 systems while
        clearing PROPVARIANTs; we read exactly one property instead.
        """
        out: list[OutputDevice] = []
        enum = AudioUtilities.GetDeviceEnumerator()
        default_id = None
        try:
            default_id = _com_str(enum.GetDefaultAudioEndpoint(0, 1), IMMDevice, "GetId")   # eRender, eMultimedia
        except Exception:
            log.exception("GetDefaultAudioEndpoint failed")
        try:
            coll = enum.EnumAudioEndpoints(0, 1)                       # eRender, ACTIVE
            for i in range(coll.GetCount()):
                dev = coll.Item(i)
                if dev is None:
                    continue
                dev_id = _com_str(dev, IMMDevice, "GetId")
                name = self._friendly_name(dev) or f"Output device {i + 1}"
                out.append(OutputDevice(id=dev_id, name=name, is_default=(dev_id == default_id)))
        except Exception:
            log.exception("device enumeration failed")
        if not out and default_id:
            out.append(OutputDevice(id=default_id, name="Default output", is_default=True))
        log.info("output devices: %s", [(d.name, d.is_default) for d in out])
        return out

    @staticmethod
    def _friendly_name(dev) -> str:
        try:
            store = dev.OpenPropertyStore(0)                            # STGM_READ
            pv = store.GetValue(_PKEY_FRIENDLY_NAME)
            try:
                val = pv.GetValue()
                return str(val) if isinstance(val, str) else ""
            finally:
                try:
                    pv.clear()
                except Exception:
                    pass
        except Exception:
            log.exception("friendly name failed")
            return ""

    def set_default_output(self, device: OutputDevice) -> bool:
        """Make this device the Windows default output (all three roles), the
        way the Sound control panel does. Returns True when Windows confirms."""
        def f():
            pc = comtypes.CoCreateInstance(CLSID_PolicyConfigClient, IPolicyConfig, CLSCTX_ALL)
            for role in (0, 1, 2):          # eConsole, eMultimedia, eCommunications
                pc.SetDefaultEndpoint(device.id, role)
            enum = AudioUtilities.GetDeviceEnumerator()
            now_id = _com_str(enum.GetDefaultAudioEndpoint(0, 1), IMMDevice, "GetId")
            ok = now_id == device.id
            log.info("Windows default output -> %r: %s", device.name, "ok" if ok else f"not applied (now {now_id})")
            return ok
        try:
            return bool(self._com.call(f))
        except Exception:
            log.exception("set_default_output failed")
            return False

    def default_output_device(self) -> OutputDevice | None:
        for d in self.list_output_devices():
            if d.is_default:
                return d
        devs = self.list_output_devices()
        return devs[0] if devs else None

    # ------------------------------------------------------------------
    # loopback capture
    def _find_loopback_index(self, device: OutputDevice) -> dict:
        """Find the pyaudio WASAPI loopback device matching an output device."""
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        target = device.name.lower()
        best = None
        for lb in self._pa.get_loopback_device_info_generator():
            n = lb["name"].lower().replace(" [loopback]", "")
            if n == target or n in target or target in n:
                best = lb
                break
        if best is None:
            if device.is_default:
                best = self._pa.get_default_wasapi_loopback()
            else:
                # fall back to the default loopback; log it
                log.warning("No loopback device matched %r; using default", device.name)
                best = self._pa.get_default_wasapi_loopback()
        _ = wasapi
        return best

    def _reset_pa(self) -> None:
        """PortAudio enumerates devices once, at init. After a Bluetooth headset
        re-pairs (or any device change) its cached device list is stale and
        opening the loopback fails with 'Invalid sample rate' / 'Invalid
        device' for ever. A fresh PyAudio instance re-enumerates."""
        self.stop_capture()
        try:
            self._pa.terminate()
        except Exception:
            pass
        self._pa = pyaudio.PyAudio()
        log.info("PortAudio re-initialised (device list refreshed)")

    def start_capture(self, device: OutputDevice, cb: Callable[[np.ndarray], None],
                      block_ms: int = 20) -> tuple[int, int]:
        self.stop_capture()
        self._cb = cb
        try:
            return self._open_capture(device, block_ms)
        except OSError as e:
            log.warning("loopback open failed (%s); refreshing the device list and retrying", e)
            self._reset_pa()
            return self._open_capture(device, block_ms)

    def _open_capture(self, device: OutputDevice, block_ms: int) -> tuple[int, int]:
        lb = self._find_loopback_index(device)
        fs = int(lb["defaultSampleRate"])
        ch = int(lb["maxInputChannels"])
        frames = max(64, int(fs * block_ms / 1000))

        def _callback(in_data, frame_count, time_info, status):
            try:
                x = np.frombuffer(in_data, dtype=np.float32).reshape(-1, ch)
                self._cb(x)
            except Exception:
                log.exception("capture callback error")
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(
            format=pyaudio.paFloat32, channels=ch, rate=fs, input=True,
            input_device_index=lb["index"], frames_per_buffer=frames,
            stream_callback=_callback,
        )
        self._stream.start_stream()
        device.fs, device.channels = fs, ch
        return fs, ch

    def stop_capture(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ------------------------------------------------------------------
    # master volume
    def _endpoint_volume(self, device: OutputDevice):
        ep = self._endpoint_cache.get(device.id)
        if ep is None:
            dev = self._get_imm_device(device.id)
            iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            ep = iface.QueryInterface(IAudioEndpointVolume)
            self._endpoint_cache[device.id] = ep
        return ep

    def master_range_db(self, device: OutputDevice) -> tuple[float, float]:
        def f():
            lo, hi, _step = self._endpoint_volume(device).GetVolumeRange()
            return float(lo), float(hi)
        return self._com.call(f)

    def get_master_db(self, device: OutputDevice) -> float:
        return self._com.call(lambda: float(self._endpoint_volume(device).GetMasterVolumeLevel()))

    def set_master_db(self, device: OutputDevice, db: float) -> None:
        def f():
            lo, hi, _ = self._endpoint_volume(device).GetVolumeRange()
            self._endpoint_volume(device).SetMasterVolumeLevel(float(max(lo, min(hi, db))), None)
        self._com.call(f)

    def get_master_scalar(self, device: OutputDevice) -> float:
        return self._com.call(lambda: float(self._endpoint_volume(device).GetMasterVolumeLevelScalar()))

    def set_master_scalar(self, device: OutputDevice, s: float) -> None:
        self._com.call(lambda: self._endpoint_volume(device).SetMasterVolumeLevelScalar(
            float(max(0.0, min(1.0, s))), None))

    # ------------------------------------------------------------------
    # sessions
    def _session_manager(self, device: OutputDevice):
        # Keep the manager local to one enumeration. On Windows 11, repeatedly
        # calling GetSessionEnumerator on a long-lived manager retained about
        # 3 KiB per poll even after releasing each enumerator. Releasing the
        # manager after each poll bounds those native allocations.
        dev = self._get_imm_device(device.id)
        iface = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
        # Activate is declared as returning IUnknown by pycaw. QueryInterface
        # gives this pointer its own COM reference; a ctypes cast does not.
        return iface.QueryInterface(IAudioSessionManager2)

    def list_sessions(self, device: OutputDevice) -> list[AudioSession]:
        return self._com.call(lambda: self._list_sessions(device))

    # ------------------------------------------------------------------
    # new-session notifications (see _SessionCreated)
    _watch: tuple | None = None       # (device id, manager, callback object)

    def start_session_watch(self, device: OutputDevice, handler) -> bool:
        """Call ``handler(key, name, pid) -> scalar | None`` the instant a new
        session appears on ``device`` and apply the scalar to it at once."""
        def f():
            self._stop_watch()
            mgr = self._session_manager(device)          # a long-lived manager of its own
            cb = _SessionCreated(self, handler)
            mgr.RegisterSessionNotification(cb)
            mgr.GetSessionEnumerator()                   # Windows only starts notifying after one enumeration
            self._watch = (device.id, mgr, cb)
            log.info("session watch on %s", device.name)
            return True
        try:
            return bool(self._com.call(f))
        except Exception:
            log.exception("session watch")
            return False

    def _stop_watch(self) -> None:
        w = self._watch
        self._watch = None
        if w is None:
            return
        try:
            w[1].UnregisterSessionNotification(w[2])
        except Exception:
            pass

    def stop_session_watch(self) -> None:
        try:
            self._com.call(self._stop_watch)
        except Exception:
            pass

    def _list_sessions(self, device: OutputDevice) -> list[AudioSession]:
        result: list[AudioSession] = []
        me = own_pid()
        try:
            mgr = self._session_manager(device)
            enumerator = mgr.GetSessionEnumerator()
            count = enumerator.GetCount()
            # cache: key -> {session instance id: ISimpleAudioVolume}. The instance id
            # matters: after a default-device switch Windows re-creates the sessions,
            # and a volume object of the old instance still answers (reads back what
            # we set) while controlling nothing - the bus then runs to its floor or
            # ceiling chasing a level that never moves. One process can also hold
            # several sessions (Chrome does): every one of them gets the volume.
            old_cache: dict[str, dict] = self._session_cache
            new_cache: dict[str, dict] = {}
            old_meters = self._meter_cache
            new_meters: dict[str, object] = {}
            by_key: dict[str, AudioSession] = {}
            for i in range(count):
                ctl = enumerator.GetSession(i)
                if not ctl:
                    continue
                ctl2 = ctl.QueryInterface(IAudioSessionControl2)
                pid = int(ctl2.GetProcessId())
                state = ctl2.GetState()
                if state == AudioSessionState.Expired:
                    continue
                name = self._process_name(pid, ctl2)
                key = f"{pid}:{name}"
                try:
                    inst = _com_str(ctl2, IAudioSessionControl2, "GetSessionInstanceIdentifier")
                except Exception:
                    inst = f"#{i}"
                inst = f"{device.id}|{inst}"
                vol = old_cache.get(key, {}).get(inst) or ctl.QueryInterface(ISimpleAudioVolume)
                try:
                    meter = old_meters.get(inst) or ctl.QueryInterface(IAudioMeterInformation)
                    peak = float(meter.GetPeakValue())
                    new_meters[inst] = meter
                except Exception:
                    peak = 0.0
                new_cache.setdefault(key, {})[inst] = vol
                active = (state == AudioSessionState.Active)
                prev = by_key.get(key)
                if prev is None:
                    by_key[key] = AudioSession(
                        key=key, name=name, pid=pid,
                        volume=float(vol.GetMasterVolume()), peak=peak,
                        active=active, is_self=(pid == me),
                    )
                else:                      # same process, another session: one row, loudest meter
                    by_key[key] = AudioSession(
                        key=key, name=name, pid=pid,
                        volume=prev.volume, peak=max(prev.peak, peak),
                        active=prev.active or active, is_self=prev.is_self,
                    )
            result = list(by_key.values())
            self._session_cache = new_cache
            self._meter_cache = new_meters
        except Exception as e:  # pragma: no cover
            log.exception("list_sessions failed: %s", e)
        return result

    @staticmethod
    def _process_name(pid: int, ctl2) -> str:
        if pid == 0:
            return "System sounds"
        try:
            import psutil  # type: ignore
            return psutil.Process(pid).name()
        except Exception:
            try:
                return _com_str(ctl2, IAudioSessionControl, "GetDisplayName") or f"pid {pid}"
            except Exception:
                return f"pid {pid}"

    def set_session_volume(self, device: OutputDevice, key: str, scalar: float) -> None:
        def f():
            vols = self._session_cache.get(key)
            if not vols:
                self._list_sessions(device)
                vols = self._session_cache.get(key)
            for inst, vol in list((vols or {}).items()):
                try:
                    vol.SetMasterVolume(float(max(0.0, min(1.0, scalar))), None)
                except Exception:
                    log.exception("set_session_volume(%s) failed", key)
                    self._session_cache.get(key, {}).pop(inst, None)
        self._com.call(f)

    # ------------------------------------------------------------------
    # playback for calibration
    def _find_output_index(self, device: OutputDevice) -> int:
        target = device.name.lower()
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        cands = [self._pa.get_device_info_by_index(i) for i in range(self._pa.get_device_count())]
        cands = [c for c in cands if c.get("maxOutputChannels", 0) > 0 and not c["name"].lower().endswith("[loopback]")]
        for c in cands:   # exact WASAPI name first
            if c["hostApi"] == wasapi and c["name"].lower() == target:
                return c["index"]
        for c in cands:   # then any WASAPI partial match
            n = c["name"].lower()
            if c["hostApi"] == wasapi and (n in target or target in n):
                return c["index"]
        try:
            return self._pa.get_default_wasapi_device()["index"]
        except Exception:
            return self._pa.get_default_output_device_info()["index"]

    def play(self, device: OutputDevice, audio: np.ndarray, fs: int,
             gain_scalar: Callable[[], float], loop: bool = True):
        return _PaPlayer(self._pa, self._find_output_index(device), audio, fs, gain_scalar, loop)

    def close(self):
        self.stop_capture()
        def release():
            self._session_cache.clear()
            self._endpoint_cache.clear()
            self._meter_cache.clear()
        try:
            self._com.call(release)
        except Exception:
            pass
        self._com.stop()
        try:
            self._pa.terminate()
        except Exception:
            pass


class _PaPlayer:
    def __init__(self, pa, index, audio, fs, gain_scalar, loop):
        if audio.ndim == 1:
            audio = np.stack([audio, audio], axis=1)
        self.audio = audio.astype(np.float32)
        self.pos = 0
        self.loop = loop
        self.gain = gain_scalar
        self._playing = True

        def cb(in_data, frame_count, time_info, status):
            out = np.zeros((frame_count, self.audio.shape[1]), dtype=np.float32)
            try:
                n = 0
                while n < frame_count:
                    take = min(frame_count - n, self.audio.shape[0] - self.pos)
                    out[n:n + take] = self.audio[self.pos:self.pos + take]
                    self.pos += take
                    n += take
                    if self.pos >= self.audio.shape[0]:
                        if not self.loop:
                            self._playing = False
                            break
                        self.pos = 0
                out *= float(self.gain())
            except Exception:
                log.exception("player callback")
                self._playing = False
            return (out.tobytes(), pyaudio.paContinue if self._playing else pyaudio.paComplete)

        info = pa.get_device_info_by_index(index)
        log.info("calibration playback on #%s %r (host api %s) at %d Hz", index, info["name"],
                 info["hostApi"], fs)
        self.stream = pa.open(format=pyaudio.paFloat32, channels=self.audio.shape[1],
                              rate=int(fs), output=True, output_device_index=index,
                              frames_per_buffer=1024, stream_callback=cb)
        self.stream.start_stream()

    @property
    def playing(self) -> bool:
        return self._playing and self.stream.is_active()

    def stop(self) -> None:
        self._playing = False
        try:
            self.stream.stop_stream()
            self.stream.close()
        except Exception:
            pass
