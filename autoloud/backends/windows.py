"""Windows backend: WASAPI loopback (pyaudiowpatch) + Core Audio volume
control (pycaw / comtypes).

Nothing here needs a driver. Loopback gives us the engine mix *after*
session volumes, so the controller measures the result of its own actions.
"""
from __future__ import annotations

import logging
import queue
import threading
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
from comtypes import GUID  # type: ignore

BACKEND_VERSION = "0.3-comworker"

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

    def __init__(self):
        self._q: "queue.Queue[tuple[Callable, Future] | None]" = queue.Queue()
        self._t = threading.Thread(target=self._run, daemon=True, name="autoloud-com")
        self._t.start()

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
            try:
                fut.set_result(fn())
            except BaseException as e:  # noqa: BLE001
                fut.set_exception(e)
        import gc
        gc.collect()
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass

    def call(self, fn: Callable, timeout: float = 5.0):
        if threading.current_thread() is self._t:
            return fn()
        fut: Future = Future()
        self._q.put((fn, fut))
        return fut.result(timeout=timeout)

    def stop(self):
        self._q.put(None)
        self._t.join(timeout=2.0)


class WindowsBackend:
    name = "windows-wasapi"

    def __init__(self):
        log.info("WindowsBackend %s", BACKEND_VERSION)
        self._pa = pyaudio.PyAudio()
        self._stream = None
        self._cb = None
        self._com = _ComWorker()
        # --- everything below is touched ONLY on the COM worker thread ---
        self._session_cache: dict[str, object] = {}
        self._endpoint_cache: dict[str, object] = {}   # device id -> IAudioEndpointVolume
        self._mgr_cache: dict[str, object] = {}        # device id -> IAudioSessionManager2

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
            default_id = enum.GetDefaultAudioEndpoint(0, 1).GetId()   # eRender, eMultimedia
        except Exception:
            log.exception("GetDefaultAudioEndpoint failed")
        try:
            coll = enum.EnumAudioEndpoints(0, 1)                       # eRender, ACTIVE
            for i in range(coll.GetCount()):
                dev = coll.Item(i)
                if dev is None:
                    continue
                dev_id = dev.GetId()
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

    def start_capture(self, device: OutputDevice, cb: Callable[[np.ndarray], None],
                      block_ms: int = 20) -> tuple[int, int]:
        self.stop_capture()
        lb = self._find_loopback_index(device)
        fs = int(lb["defaultSampleRate"])
        ch = int(lb["maxInputChannels"])
        frames = max(64, int(fs * block_ms / 1000))
        self._cb = cb

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
            ep = comtypes.cast(iface, comtypes.POINTER(IAudioEndpointVolume))
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
        mgr = self._mgr_cache.get(device.id)
        if mgr is None:
            dev = self._get_imm_device(device.id)
            iface = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
            mgr = comtypes.cast(iface, comtypes.POINTER(IAudioSessionManager2))
            self._mgr_cache[device.id] = mgr
        return mgr

    def list_sessions(self, device: OutputDevice) -> list[AudioSession]:
        return self._com.call(lambda: self._list_sessions(device))

    def _list_sessions(self, device: OutputDevice) -> list[AudioSession]:
        result: list[AudioSession] = []
        me = own_pid()
        try:
            mgr = self._session_manager(device)
            enumerator = mgr.GetSessionEnumerator()
            count = enumerator.GetCount()
            new_cache: dict[str, object] = {}
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
                vol = self._session_cache.get(key) or ctl.QueryInterface(ISimpleAudioVolume)
                try:
                    meter = ctl.QueryInterface(IAudioMeterInformation)
                    peak = float(meter.GetPeakValue())
                except Exception:
                    peak = 0.0
                new_cache[key] = vol
                result.append(AudioSession(
                    key=key, name=name, pid=pid,
                    volume=float(vol.GetMasterVolume()), peak=peak,
                    active=(state == AudioSessionState.Active),
                    is_self=(pid == me),
                ))
            self._session_cache = new_cache
        except Exception as e:  # pragma: no cover
            log.exception("list_sessions failed: %s", e)
            self._mgr_cache.pop(device.id, None)
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
                return ctl2.GetDisplayName() or f"pid {pid}"
            except Exception:
                return f"pid {pid}"

    def set_session_volume(self, device: OutputDevice, key: str, scalar: float) -> None:
        def f():
            vol = self._session_cache.get(key)
            if vol is None:
                self._list_sessions(device)
                vol = self._session_cache.get(key)
            if vol is not None:
                try:
                    vol.SetMasterVolume(float(max(0.0, min(1.0, scalar))), None)
                except Exception:
                    log.exception("set_session_volume(%s) failed", key)
                    self._session_cache.pop(key, None)
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
            self._mgr_cache.clear()
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
