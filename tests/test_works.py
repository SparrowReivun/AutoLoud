from autoloud.backends.base import AudioSession
from autoloud.core.works import WorkTracker, MediaInfo, match_aumid


def S(name, peak, active=True):
    return AudioSession(key=f"1:{name}", name=name, pid=1, volume=1.0, peak=peak, active=active)


class FakeMedia:
    def __init__(self):
        self.infos = []

    def snapshot(self):
        return list(self.infos)


def test_silence_rule_counts_works():
    tr = WorkTracker(silence_s=15.0)
    ev = tr.update([S("vlc.exe", 0.3)], 0.0)
    assert [(a, w.index, w.source) for a, w in ev] == [("vlc.exe", 1, "silence")]
    # a short pause (10 s) is not a new work
    for t in range(1, 11):
        assert tr.update([S("vlc.exe", 0.0)], float(t)) == []
    assert tr.update([S("vlc.exe", 0.3)], 11.0) == []
    # 15 s of near-silence (low but non-zero level) then sound -> work 2
    for t in range(12, 28):
        assert tr.update([S("vlc.exe", 0.001)], float(t)) == []
    ev = tr.update([S("vlc.exe", 0.3)], 28.0)
    assert [(a, w.index) for a, w in ev] == [("vlc.exe", 2)]
    assert tr.current("vlc.exe").label() == ""      # no metadata -> index only


def test_media_rule_ignores_pause_and_uses_title_change():
    m = FakeMedia()
    tr = WorkTracker(media=m)
    m.infos = [MediaInfo("Spotify.exe", "Song A", "Artist", "Album", playing=True)]
    ev = tr.update([S("Spotify.exe", 0.3)], 0.0)
    assert [(a, w.title) for a, w in ev] == [("Spotify.exe", "Song A")]
    # pause: same metadata, app goes silent for 20 s -> NOT a new work (media rule owns this app)
    m.infos = [MediaInfo("Spotify.exe", "Song A", "Artist", "Album", playing=False)]
    for t in range(1, 22):
        assert tr.update([S("Spotify.exe", 0.0)], float(t)) == []
    m.infos = [MediaInfo("Spotify.exe", "Song A", "Artist", "Album", playing=True)]
    assert tr.update([S("Spotify.exe", 0.3)], 22.0) == []
    # track change -> work 2 with a label
    m.infos = [MediaInfo("Spotify.exe", "Song B", "Artist", "Album", playing=True)]
    ev = tr.update([S("Spotify.exe", 0.3)], 23.0)
    assert len(ev) == 1 and ev[0][1].index == 2 and ev[0][1].label() == "Song B — Artist"


def test_aumid_matching():
    names = ["Spotify.exe", "chrome.exe", "AMP.exe", "vlc.exe"]
    assert match_aumid("Spotify.exe", names) == "Spotify.exe"
    assert match_aumid("chrome.exe", names) == "chrome.exe"
    assert match_aumid("Microsoft.ZuneMusic_8wekyb3d8bbwe!App", names) is None
    assert match_aumid("org.videolan.vlc", names) == "vlc.exe"


def test_media_without_title_falls_back_to_silence_rule():
    m = FakeMedia()
    tr = WorkTracker(media=m, silence_s=15.0)
    m.infos = [MediaInfo("chrome.exe", "", "", "", playing=True)]   # browser with no metadata
    ev = tr.update([S("chrome.exe", 0.3)], 0.0)
    assert [(a, w.source) for a, w in ev] == [("chrome.exe", "silence")]
