# Changelog

## 0.3.0 — 2026-10-31

- Remembered gains are only taken from clean moments (steady, or adjusting with no protection cut in the last 20 s); gains bent by a cut were being replayed as the start point of the next sound. All remembered gains are forgotten once on this upgrade.
- The startup master-volume set now happens after the first successful session query (the two Core Audio hangs seen so far were right after it).
- Robustness: if a Core Audio call hangs (the audio service stops answering, e.g. around a device change or a stalled app), the COM worker thread is abandoned and a fresh one takes over with fresh objects, instead of every later call timing out; a single app whose volume call times out is skipped for that tick.
- Intrusions are cut per app: when the protection fires because an app that just started sounding barged in over content that was already playing (a notification over music), the bus cut is undone and only that app is pulled down by the same amount, until it goes quiet (for good if it is in strong mode). The music no longer drops with every ding.
- Modes instead of switches: the bus auto loudness is always on and the dashboard picks which parameter preset ("mode") it runs with; the per-app "Auto" tick is replaced by "strong mode" (every protection cut the app trips pulls its fader down by the excess over B − 5 dB, for good; ticking it does not move the fader). Tool apps are still detected and tagged.
- Parameter presets: the Settings page keeps named sets of all control parameters (new / rename / delete / switch); changes are stored in the active preset.
- Measurement log for analysis: `meters-<ts>.csv` (100 ms rows: momentary / short-term / integrated LUFS, sample peak, gains, phase, B, master) and `apps-<ts>.csv` (per sounding app per 0.5 s poll: peak meter, session volume, bus gain, fader). Nothing is pruned any more (the adjustment log included).
- Adjustment log: every decision the dashboard shows is also written, in the same words plus the apps sounding at that moment, to `adjust-<timestamp>.log` in the config folder (one file per launch, kept 14 days).
- On start the Windows master volume is set to 40 % (Settings, 0 = leave it) and taken as the calibration reference, so there is boost headroom every session.
- Dashboard meters and the listening line now show the perceived loudness (loopback plus the master boost the loopback cannot see), matching the decision log; the B slider bottom mutes.
- The "comfortable" dead band is now asymmetric: up to 3 dB above B and up to 8 dB below B (two settings) count as comfortable.
- New works always jump to the app's remembered gain (or −3 dB for a new app); the "other apps are playing, keep the gain" case is gone — one app at a time is assumed, and the gain is remembered for every sounding auto app.
- The controller now knows the master volume's real headroom (0 dB full scale minus the calibration reference) and never asks for more boost than that, so it no longer believes a quiet source is at B while the master is pinned at maximum; the dashboard marks the boost "(at the limit)".
- Settings apply and save as soon as they change (typed values on Enter / focus-out); the Save button is gone.
- Protection no longer shows a notice: a loud sound is eased down to B + 5 dB within "protection cut takes" (0.3 s, in Settings) and the normal rules resume by themselves once the cut has landed. The loud-sound trigger is one setting edited live from both the dashboard slider and the Settings page.
- Collapsible sidebar: the ‹ button folds the navigation to icons; the state is remembered.
- Volume keys and volume knobs (F11/F12-style media keys, USB/Bluetooth knobs) now move the comfort level B by 1 dB per press instead of the Windows master volume (Settings: on by default, step adjustable; mute is left to Windows; keys pressed while an admin window is in front still go to Windows).
- Comfort level B sliders (dashboard and protection notice) now move in 0.1 LUFS steps.
- One-step-to-B range default is 3 dB (must be larger than the ±2 dB dead band to matter).
- Defaults are now the author's tuned values (listen 5 s, +1 / −0.8 dB steps, ±2 dB dead band, steady re-adjust after 2 loud blocks over B + 6 or 3 quiet blocks under B − 20, protection at B + 13 → B + 5, 1.8 s moves).
- One exe for both languages: on first run the UI follows the Windows display language (Chinese → 中文, otherwise English); switch any time in Settings. The separate English build is no longer needed.
- Dashboard activity card: what the controller is doing right now (ramp from → to over N s, listening progress, protection hold, idle) and a log of its last decisions, for tuning the rules.
- Calibration is one step: set external device and master volume to ~80 %, play music you like, raise the fader from 0 to comfortable → B. The harsh-beep "ceiling A" step and all A-related logic are gone; the onset guard now starts a fixed 10 dB below B (adjustable in Settings).
- Protection: a loud sound is now cut *fast* (0.15 s) down to B + 5 dB (adjustable), measured from the gain actually sounding, and re-corrected once the meter catches up; a cut also overrides a frozen fader hold. The notice stays 10 s, carries the comfort level B slider, and points to the dashboard for the trigger threshold, which now has its own slider there.
- Settings are reset to the new defaults once (config_version 4); profiles and app mixer settings are kept.
- Tool-app detector: an app whose session stays active but sounds less than 10 % of the time over 60 s (IME key clicks, notification helpers) gets its mixer Auto switched off automatically and no longer spoils the "playing alone" test; ticking Auto back on makes that permanent.
- Remembered gains: an app's bus gain is saved continuously while it plays alone (not only once steady), follows B when B is moved, and a known app that starts after silence resumes at that gain directly (1 s move, first decision may cut all the way) instead of the guard ramp from B − 5; the guard ramp now only runs for apps never heard before.
- Timing: every step and the onset guard now ramp over one block length (the "listen" setting); a B-slider move and a new work's jump share one "move" ramp (1 s); the whole-mix silence timeout that re-arms the onset guard is the same 15 s as the per-app work-silence rule (was 2 s).
- Steady state leaves only after 2 consecutive blocks above B + trigger, or 4 consecutive blocks below B − 20 dB (all four numbers in Settings); one loud block alone no longer re-adjusts.
- Dashboard meters read as the distance to B ("B +1.3 dB") once calibrated.
- Settling: within 2 dB of B (adjustable) a step goes exactly to B instead of creeping by the step caps, and the dead band is now ±1 dB (was ±0.5).
- Nudges: the first decision after a new work or onset cuts all the way if too loud; raises stay capped at the step size (the escalating 1x/2x/3x raise was tried and dropped). Onset floor is now B − 5 dB.
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
