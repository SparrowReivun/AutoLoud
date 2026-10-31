# Changelog

## Unreleased

- Work detection per app: a new song/video is recognised from Windows media-session metadata (title/artist/album change; pause, seek and artwork updates are ignored), or, for apps without metadata, after 15 s of near-silence followed by sound. Each new work in an auto app closes the current analysis window so it gets its own adjustment. The mixer shows the current work (title — artist, or "Work N").
- Per-app mixer is now a standing feature: ±40 dB faders and a per-app "Auto" switch, working whether the bus auto loudness is on or off. Faders above 0 dB raise the master and attenuate the other apps.
- Apps with Auto off are discounted from the loudness measurement (peak-meter share estimate) so they do not drag the bus gain.
- Mixer faders are compensated in the controller so a -6 dB fader really is 6 dB quieter.
- Calibration source can be any app that is playing; bundled tracks decode off the GUI thread.

## 0.1.0 — 2026-10-20

First public version.

- BS.1770-4 streaming loudness meter (momentary / short-term / gated integrated), verified against pyloudnorm.
- Gain controller: onset guard from silence (A → B over 2 s), 30 s window re-computation with 2 s ramps,
  fast safety limiter (B + 8 dB), explicit master-volume visibility handling.
- Windows backend without drivers: WASAPI loopback + per-app session volume + endpoint volume,
  all COM work confined to one thread.
- Calibration wizard: 2 kHz beep → A, music → B; source can be a bundled track or any app that is playing.
- Per-device profiles, tray, zh/en UI, software simulator for development on any OS.
- PyInstaller single-file exe, GitHub Actions build + release.
