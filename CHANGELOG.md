# Changelog

## Unreleased

- Dashboard activity card: what the controller is doing right now (ramp from → to over N s, listening progress, protection hold, idle) and a log of its last decisions, for tuning the rules.
- Calibration is one step: set external device and master volume to ~80 %, play music you like, raise the fader from 0 to comfortable → B. The harsh-beep "ceiling A" step and all A-related logic are gone; the onset guard now starts a fixed 10 dB below B (adjustable in Settings).
- Protection: a loud sound is now cut *fast* (0.15 s) down to B + 5 dB (adjustable), measured from the gain actually sounding, and re-corrected once the meter catches up; a cut also overrides a frozen fader hold. The notice stays 10 s, carries the comfort level B slider, and points to the dashboard for the trigger threshold, which now has its own slider there.
- Settings are reset to the new defaults once (config_version 4); profiles and app mixer settings are kept.
- Onset guard no longer boosts: during the 2 s onset the gain is capped at the app's remembered gain (0 dB for an unknown app), so a quiet intro is not pulled up to B only for the real content to blast in and trip the protection; boosting is left to the slow nudges.
- B slider: moving B now sends the gain straight to what the new B needs in one smooth ramp (2 s, adjustable in Settings) instead of the 5 s listen / 0.5 dB nudges, and the sudden-loudness protection is held off until the move has landed, so lowering B no longer pops the notice.

## 0.2.0 — 2026-10-31

- Dashboard "Comfort level B" slider: drag it like a volume knob, the sound follows at once and where it stops is the new B. The music step of the calibration wizard is now optional (B defaults to A + 6 dB until set).
- Controller rewritten around the listen-then-nudge rules: 5 s listening blocks, +0.5 / −2 dB steps with 2 s ramps, ±0.5 dB dead band, no chasing in steady state (re-adjust only when a 5 s block exceeds B + 5 dB), protection cut by the excess (+1.5 dB hysteresis) at B + 10 dB with an auto-closing notice and a threshold fader, faster recovery up to the pre-cut gain. New works start from the gain the app had last time (remembered per process name). Manual fader moves hold that app until its work ends; moving the Windows master volume re-bases B. Faders now run from mute to +15 dB.
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
