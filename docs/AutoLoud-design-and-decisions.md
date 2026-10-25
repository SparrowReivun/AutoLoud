# AutoLoud: Automatic Loudness Leveling — Design and Open Decisions

Meeting notes · 2026-10-22 · matches code v0.1.0 (branch main)

## 1. The problem

Loudness varies widely between videos and applications, so users keep adjusting the volume by hand; a notification popping up during a call is startling. The goal is a system-level tool: calibrate once, then never touch the volume again when switching sources. The next step builds on it: automatic mixing when several sources play at once (music, video, AI assistant, calls).

## 2. Technical route (revised)

The original idea was to "take over Windows audio output like FxSound". On inspection, FxSound itself relies on a virtual audio driver; an ordinary application cannot intercept and replace another application's audio stream. Three routes are viable: a virtual sound card, a custom APO, or "measure loudness and control volume".

We use the third. WASAPI loopback captures the system mix, BS.1770 loudness (LUFS) is computed in real time, and the result is steered onto the target through Windows' per-application session volumes and the master volume. No driver, no stream modification, zero latency, and we know by construction which application is producing sound — exactly what automatic mixing will need later. The cost: no per-sample limiting or compression. The backend interface is already designed to accept a virtual-sound-card implementation later.

Deliverables: a PySide6 GUI; one-click local run script; a single-file PyInstaller exe; GitHub Actions for tests, builds and Releases. Repository: github.com/SparrowReivun/AutoLoud.

## 3. Calibration: one profile per output device

Each headphone / speaker / Bluetooth device keeps its own calibration.

1. Beep → A (ceiling): a harsh 2 kHz sine pulse train plays; the user raises the fader from 0 until it is "loud but still tolerable".
2. Music → B (comfort): the user plays a bundled track, or starts their own music in any app (Spotify, a browser) and selects that app in the wizard; the fader then drives that app's volume. They set it to a comfortable level.

A and B are stored as LUFS in the loopback domain, not as fader positions. The master volume at calibration time is recorded as the reference and should not be moved afterwards. The wizard also probes whether the master volume is visible in the loopback (for Bluetooth devices it usually is not), which determines whether gain estimates could drift.

## 4. Runtime logic (revised)

**Onset guard**: triggers only when sound appears after complete silence. The target starts at A and ramps to B over 2 s while the source loudness is estimated online. A sudden loud notification is never louder than A.

**New source while audio is already playing**: no return to A (that would be audibly discontinuous) and no special handling; the next window recomputation takes care of it. The original idea of "one adjustment per song/video if the source can be identified" is not feasible yet — we can identify the process, not the track.

**Window recomputation**: at the end of each window, the window's gated integrated loudness (silence and quiet passages excluded) sets the gain for the next window with a 2 s ramp, then the integrator is reset. The first window after an onset is 12 s (the onset phase used only 2 s of momentary loudness, so an early correction is needed); afterwards 30 s per window (originally 3 min, now shortened). The window length balances "keeps up with source changes" against "preserves the dynamics inside music and films".

**Safety limiter**: when momentary loudness exceeds B + 8 dB, the gain is pulled down in 0.3 s and the analysis window is reset. This complements window recomputation: an ad cut into the middle of a window cannot wait for the window to end. The 8 dB margin avoids flattening normal dynamic contrast in music.

**Actuation**: attenuation goes through per-application session volumes (≤ 0 dB); a boost raises the master volume above the calibration reference, capped at +12 dB. Everything is restored on exit.

## 5. Decisions needed

| # | Question | Current value | Alternatives / considerations |
| --- | --- | --- | --- |
| 1 | Safety limiter margin | B + 8 dB | 6 dB is safer but may squash film explosions; 10 dB lets more ads through |
| 2 | Analysis window length | 30 s (first window 12 s) | Original 3 min preserves more dynamics but reacts slowly; 60 s is a middle ground |
| 3 | Onset A→B ramp | 2 s | 1 s settles faster, 3 s is gentler |
| 4 | Assumed worst-case loudness of an unknown source | −10 LUFS | Sets how deep the idle attenuation is; lower is safer but quiet sources start quieter |
| 5 | Maximum master boost | +12 dB | Prevents a very quiet source, noise floor included, from being pulled to maximum |
| 6 | Quick re-estimate when a new app starts | Off | If on, re-estimate from short-term loudness 3 s later; turned off in favour of window recomputation |
| 7 | Per-track / per-video source identification | Not implemented | Windows Media Session API could supply track titles; reset the window on track change; coverage to be evaluated |
| 8 | Automatic mixing scene rules | Not implemented | How to define "calls first, music yields" ratios; needs a classification table by process name |
| 9 | License | CC BY-NC 4.0 | CC is not designed for software (no patent clause); MIT / GPL if open collaboration is wanted |
| 10 | Policy when the master volume is hidden from loopback (Bluetooth) | Auto-detected, treated as hidden | Probe signal to be changed to a continuous sine for stability |

## 6. Verified and known issues

Verified: loudness meter within 0.2 LU of pyloudnorm; 14 closed-loop controller simulation tests pass (onset guard, convergence, ramp smoothness, safety limiter, no runaway drift, new source does not return to A); stable on a real machine; GitHub Actions build succeeds.

Known issues: mp3 decoding crashes in some environments (bundled track switched to FLAC as a workaround); occasional false readings in the Bluetooth master-volume probe; the exe is unsigned, so SmartScreen warns on first run.
