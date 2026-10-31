"""Detect "works" (a song, a video, an episode) per application.

Two sources, in order of preference:

1. **Media session metadata** (Windows GSMTC): title / artist / album of what
   an app reports it is playing. A change in that tuple is a new work.
   Pause, resume, seeking and thumbnail updates do not change the tuple and
   therefore do not count.
2. **Silence fallback** for apps without usable metadata: when an app has
   been near-silent for ``silence_s`` (default 15 s) and then produces sound
   again, that is a new work. Accepted limits: a long quiet passage in a film
   can look like a new work; back-to-back tracks with no gap are not seen.

The tracker itself is platform-neutral; the media provider is injected.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, Protocol

from autoloud.backends.base import AudioSession

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaInfo:
    """What a media session reports. ``aumid`` is the app's AUMID / exe name."""
    aumid: str
    title: str = ""
    artist: str = ""
    album: str = ""
    playing: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.title.strip(), self.artist.strip(), self.album.strip())

    @property
    def usable(self) -> bool:
        return bool(self.title.strip())


@dataclass(frozen=True)
class WorkInfo:
    index: int                      # 1-based, per app, per run of AutoLoud
    source: str                     # "media" | "silence"
    title: str = ""
    artist: str = ""
    album: str = ""

    def label(self) -> str:
        """Human-readable label; empty when only the index is known."""
        if self.title:
            return f"{self.title} — {self.artist}" if self.artist else self.title
        return ""


class MediaProvider(Protocol):
    def snapshot(self) -> list[MediaInfo]: ...


@dataclass
class _AppState:
    works: int = 0
    current: WorkInfo | None = None
    last_media_key: tuple | None = None
    silent_since: float | None = None     # None = currently sounding
    ever_sounded: bool = False
    has_media: bool = False


_NONWORD = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _NONWORD.sub("", s.lower())


def match_aumid(aumid: str, process_names: Iterable[str]) -> str | None:
    """Map a media-session AUMID to one of the audio session process names.

    AUMIDs look like ``Spotify.exe``, ``chrome.exe``,
    ``Microsoft.ZuneMusic_8wekyb3d8bbwe!App`` or
    ``AppleInc.AppleMusicWin_nzyj5cx40ttqa!App``. Exact exe match first, then
    substring on normalised names in both directions.
    """
    names = list(process_names)
    a = aumid.lower()
    for n in names:
        if n.lower() == a:
            return n
    stem_a = _norm(a.split("!")[0].split("_")[0].rsplit(".exe", 1)[0])
    best = None
    for n in names:
        stem_n = _norm(n.lower().rsplit(".exe", 1)[0])
        if not stem_n or not stem_a:
            continue
        if stem_n in stem_a or stem_a in stem_n:
            if best is None or len(stem_n) > len(_norm(best)):
                best = n
    return best


class WorkTracker:
    def __init__(self, silence_s: float = 15.0, silence_peak: float = 0.003,
                 media: MediaProvider | None = None, aliases: dict[str, str] | None = None):
        self.silence_s = silence_s
        self.silence_peak = silence_peak
        self.media = media
        self.aliases = aliases or {}       # aumid -> process name, user overrides
        self._apps: dict[str, _AppState] = {}
        self.media_map: dict[str, str] = {}   # aumid -> process name (last resolved)
        self.last_media: list[MediaInfo] = []

    # ------------------------------------------------------------------
    def current(self, app_name: str) -> WorkInfo | None:
        st = self._apps.get(app_name)
        return st.current if st else None

    def all_current(self) -> dict[str, WorkInfo]:
        return {k: v.current for k, v in self._apps.items() if v.current is not None}

    # ------------------------------------------------------------------
    def update(self, sessions: list[AudioSession], now: float) -> list[tuple[str, WorkInfo]]:
        """Advance with the latest audio sessions. Returns new-work events."""
        events: list[tuple[str, WorkInfo]] = []
        names = [s.name for s in sessions if not s.is_self]
        by_name: dict[str, AudioSession] = {}
        for s in sessions:
            if s.is_self:
                continue
            # merge several sessions of one process: loudest wins
            cur = by_name.get(s.name)
            if cur is None or (s.active and s.peak > cur.peak):
                by_name[s.name] = s

        # ---- 1. media metadata -------------------------------------------
        media_apps: set[str] = set()
        if self.media is not None:
            try:
                infos = self.media.snapshot()
            except Exception:
                log.exception("media snapshot failed")
                infos = []
            self.last_media = infos
            for info in infos:
                target = self.aliases.get(info.aumid) or match_aumid(info.aumid, names)
                if target is None:
                    continue
                self.media_map[info.aumid] = target
                if not info.usable:
                    continue
                media_apps.add(target)
                st = self._apps.setdefault(target, _AppState())
                st.has_media = True
                if info.key != st.last_media_key:
                    st.last_media_key = info.key
                    st.works += 1
                    st.current = WorkInfo(st.works, "media", info.title.strip(),
                                          info.artist.strip(), info.album.strip())
                    events.append((target, st.current))

        # ---- 2. silence fallback -----------------------------------------
        for name, s in by_name.items():
            st = self._apps.setdefault(name, _AppState())
            sounding = s.active and s.peak > self.silence_peak
            if name in media_apps:
                st.silent_since = None if sounding else (st.silent_since or now)
                continue
            if sounding:
                gap = (now - st.silent_since) if st.silent_since is not None else None
                if not st.ever_sounded or (gap is not None and gap >= self.silence_s):
                    st.works += 1
                    st.current = WorkInfo(st.works, "silence")
                    events.append((name, st.current))
                st.ever_sounded = True
                st.silent_since = None
            else:
                if st.silent_since is None:
                    st.silent_since = now
        # apps that disappeared keep their state (they may come back)
        return events
