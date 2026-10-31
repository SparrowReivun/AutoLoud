"""Windows media-session metadata (GSMTC) as a ``MediaProvider``.

Uses the Windows Runtime API
``Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager``
through the ``winsdk`` package (or the newer ``winrt-*`` packages). Runs its
own thread with an asyncio loop, polls every second, and exposes the latest
snapshot. If the runtime is unavailable the provider is simply inactive and
the silence fallback is used for every app.

Which apps publish metadata here varies: Spotify, Apple Music, Windows Media
Player and most browsers (for pages that use the Media Session API, e.g.
YouTube) usually do; many games and players do not. Observed AUMIDs are
logged so coverage can be checked per app.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

from autoloud.core.works import MediaInfo

log = logging.getLogger(__name__)

_GSMTC = None
_STATUS_PLAYING = None


def _load_runtime() -> bool:
    global _GSMTC, _STATUS_PLAYING
    if _GSMTC is not None:
        return True
    for mod in ("winsdk.windows.media.control", "winrt.windows.media.control"):
        try:
            m = __import__(mod, fromlist=["x"])
            _GSMTC = m.GlobalSystemMediaTransportControlsSessionManager
            status = m.GlobalSystemMediaTransportControlsSessionPlaybackStatus
            _STATUS_PLAYING = getattr(status, "PLAYING", None) or getattr(status, "Playing", None)
            log.info("media sessions: using %s", mod)
            return True
        except Exception:
            continue
    log.info("media sessions: no Windows Runtime binding (winsdk / winrt) available; "
             "silence fallback only")
    return False


class WindowsMediaProvider:
    POLL_S = 1.0

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot: list[MediaInfo] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._seen: set[str] = set()
        self.available = _load_runtime()
        if self.available:
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True, name="autoloud-media")
            self._thread.start()

    def snapshot(self) -> list[MediaInfo]:
        with self._lock:
            return list(self._snapshot)

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    def _run(self):
        try:
            asyncio.run(self._loop())
        except Exception:
            log.exception("media session loop died")

    async def _loop(self):
        mgr = None
        while self._running:
            t0 = time.monotonic()
            try:
                if mgr is None:
                    mgr = await _GSMTC.request_async()
                infos = []
                for s in mgr.get_sessions():
                    try:
                        infos.append(await self._read(s))
                    except Exception as e:  # noqa: BLE001
                        log.debug("media session read failed: %s", e)
                with self._lock:
                    self._snapshot = infos
            except Exception as e:  # noqa: BLE001
                log.warning("media session manager error: %s", e)
                mgr = None
            dt = self.POLL_S - (time.monotonic() - t0)
            await asyncio.sleep(max(0.1, dt))

    async def _read(self, s) -> MediaInfo:
        aumid = str(getattr(s, "source_app_user_model_id", "") or "")
        props = await s.try_get_media_properties_async()
        title = str(getattr(props, "title", "") or "")
        artist = str(getattr(props, "artist", "") or getattr(props, "album_artist", "") or "")
        album = str(getattr(props, "album_title", "") or "")
        playing = False
        try:
            st = s.get_playback_info().playback_status
            playing = (st == _STATUS_PLAYING) if _STATUS_PLAYING is not None else False
        except Exception:
            pass
        if aumid not in self._seen:
            self._seen.add(aumid)
            log.info("media session seen: aumid=%r title=%r artist=%r", aumid, title, artist)
        return MediaInfo(aumid=aumid, title=title, artist=artist, album=album, playing=playing)
