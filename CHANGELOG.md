# Changelog

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
