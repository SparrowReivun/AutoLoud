<div align="center">

# AutoLoud

**You adjust once. We handle the rest.**

[简体中文](README.md) · [**English**](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Русский](README.ru.md)

</div>

AutoLoud redefines volume control for a new era. Nothing to learn: install it, keep adjusting the volume as you always do, and it learns the loudness you like and keeps every future sound inside your comfortable range — far fewer manual corrections, while the dynamics of well-made content are preserved.

![AutoLoud dashboard](assets/screenshot-dashboard.png)

## Why AutoLoud?

Have you ever counted how many times a day adjusting the volume interrupts what you were doing?

A new video opens and is suddenly loud; the next song is too quiet to hear. The dialogue in a game is finally at the right level — and the next fight is deafening.

We keep adjusting the volume, and it is almost always for the same goal: the sound should feel comfortable.

AutoLoud lets you state that goal directly. Through the volume adjustments you already make, you tell it "this is how loud I like it"; from then on it keeps learning and adjusting, so your attention stays on the content.

## Download and install

1. Download the latest `AutoLoud-windows.zip` from [Releases](https://github.com/SparrowReivun/AutoLoud/releases) (about 100 MB, no Python needed).
2. Unzip it anywhere and run `AutoLoud.exe` inside (keep the folder together: the files next to the exe are needed). On first launch Windows SmartScreen may say "Windows protected your PC" — click "More info → Run anyway".
3. The calibration wizard opens by itself and takes two minutes. AutoLoud then minimizes to the tray and works quietly.

**About antivirus warnings**: AutoLoud is not code-signed (certificates cost money), and it hooks the volume keys (a global keyboard hook), captures the system loopback and controls each application's volume — that combination, in a brand-new program with no reputation yet, is sometimes flagged as a trojan by the heuristics of Defender, 360 and others. It is open source: compare with the code or check the exe on VirusTotal; we submit a false-positive report to Microsoft for every release. If in doubt, run it from source as described below.

The interface follows the Windows display language (Chinese or English) and can be switched on the Settings page.

## Design principles

### 1. From action-driven to goal-driven

Traditional volume control is reactive: you hear that something is too loud or too quiet, then correct it by hand. Each correction usually solves only the problem in front of you.

AutoLoud treats a volume adjustment as information about your preference. Your action changes the current volume and also tells the system what you are trying to hear, and that goal is carried forward to everything that plays afterwards.

### 2. Stable overall loudness, preserved dynamics

Calm passages and climaxes, quiet dialogue and heavy fights are meant to differ in level. Pulling every sound to one loudness erases those differences and quickly sounds unnatural.

AutoLoud's goal is to remove the uncomfortable loudness differences while keeping the work's own dynamics:

- Quiet passages stay quiet.
- Intense scenes keep their impact.
- The whole lands in a more comfortable range.

### 3. Listen more, act less

Automatic control has to respond in time, and it also has to hold back. A short explosion or a line of quiet dialogue should not move the overall volume.

AutoLoud uses a steady-state mechanism: when new content starts, it finds a suitable gain quickly and smoothly; once steady, the bar for adjusting again is raised.

The current design analyses in blocks of about 2 s. Only when several consecutive blocks are all too loud or all too quiet does the system leave the steady state and adjust again.

Analyse constantly, adjust cautiously.

### 4. Learn the loudness distribution instead of applying one fixed target

Even a single application plays both calm and intense content. A fixed band around the user's preferred loudness still interferes with normal level changes.

Early versions tried to hold loudness inside a narrow fixed band. Real-world testing pushed the project towards control based on each application's loudness history, so that content of different intensity gets a different target loudness.

AutoLoud gradually learns each application's distribution of source loudness, then combines it with your preference to map the different loudness levels onto their own target range.

### 5. Hearing protection

Sounds that are too loud are pulled down automatically, protecting your hearing.

### 6. Per-application volume control

Take back control of every program's sound — above all programs such as WeChat whose notifications are far too loud yet offer no volume setting. Each application can be given its own permanent volume preference, so sudden ring-tone blasts are a thing of the past. Rich customisation hooks are there for advanced users.

### 7. Let everyone simply enjoy the sound

— and forget about volume adjustment, and about this program, altogether.

## How it works

### Learning each application's loudness profile

When one application is sounding on its own, the system can work back to its source loudness L_src (every gain removed) and update two reference values for that application:

| Parameter | Meaning |
| --- | --- |
| C_low | 20th percentile of the historical source loudness — the quieter level |
| C_high | 80th percentile of the historical source loudness — the louder level |

Learning is counted in effective sounding time, with a half-life of about 20 minutes. Both values are shown in the mixer and can be relearned by hand.

![AutoLoud mixer](assets/screenshot-mixer.png)

### Computing the target loudness

At the end of every listening block, the system computes where the current source loudness sits between the two references:

```
u = clip((L_src - C_low) / (C_high - C_low), 0, 1)
T = (P - X) + X × u
```

where:

| Parameter | Meaning |
| --- | --- |
| P | the user's preference loudness (human preference loudness) |
| X | the range over which the target may move |
| u | the position of the current source loudness within the learned range, clipped to 0–1 |
| T | the current target loudness |

Near the quiet reference the target approaches P − X; near the loud reference it approaches P. Above the loud reference the target stays at P.

This bounds the target loudness. How short sounds actually behave also depends on the controller's response and the steady-state mechanism.

### Fallbacks and relearning

To handle insufficient learning, several applications mixing, and in-app volume changes, the current implementation uses these rules:

| Situation | Handling |
| --- | --- |
| Less than 30 s of effective learning | T = P for now |
| The two references less than 1 LU apart | T = P for now |
| Several applications sounding at once | source-loudness learning pauses, the last target is kept |
| No valid source-loudness reading for 60 s | the target falls back to P |
| Source loudness more than 6 LU below C_low for two minutes | the references are rebuilt from those two minutes, to follow an in-app volume change |

## How the Windows audio side works (for developers)

```
Windows audio engine ──► per-app session mix ──► WASAPI loopback capture ──► BS.1770 LUFS, live
                           ▲                                                     │
                           │ ISimpleAudioVolume (each app's session volume, ≤ 0 dB)│
                           └──────────────── gain controller ◄───────────────────┘
                                                │
                     IAudioEndpointVolume (master volume; above the calibration reference only when a boost is needed)
```

- `autoloud/dsp/loudness.py` — streaming BS.1770-4: K-weighting, momentary / short-term / gated integrated loudness, within 0.2 LU of pyloudnorm.
- `autoloud/core/controller.py` — pure-logic gain controller (IDLE → ONSET → ADJUST → STEADY → SAFETY), simulatable offline.
- `autoloud/core/platforms.py` — per-application source-loudness distribution (C_low / C_high) learning.
- `autoloud/core/engine.py` — capture → measure → decide → apply volumes; master-volume visibility probe; source-loudness back-calculation.
- `autoloud/backends/windows.py` — PyAudioWPatch (loopback) + pycaw (volumes); all COM calls on one thread.
- `autoloud/backends/mock.py` — a software simulator with the same interface.
- `autoloud/ui/` — PySide6 interface: dashboard, calibration wizard, app mixer, settings, tray.

This "listen and control the volumes" route does not replace the audio stream, so it cannot do per-sample limiting or compression; in return it needs no driver, adds no latency, and always knows which application is sounding. The `Backend` interface is ready for a future virtual-sound-card backend.

Run from source: `pip install -r requirements.txt && python run.py`; tests: `pip install -r requirements-dev.txt && pytest`.

## Logs

All logs live in the `logs\` folder next to the program (`AutoLoud\logs\` after unzipping, or `logs\` in the project folder when run from source; the config folder `%APPDATA%\AutoLoud\` is used only when that folder is not writable). Every launch and every mode switch on the dashboard (monitor / default / strong / strong1 …) starts a fresh set of files, tagged with the mode in the name - `volume-20261103-121622-monitor.csv`, `meters-20261103-130501-strong1.csv` - so one file is always one condition. `autoloud.log` is the run log; `adjust-<timestamp>.log` records every volume decision of a run in the same words as the dashboard's activity card, each line with the apps sounding at that moment; `meters-<timestamp>.csv` (one row per 100 ms: momentary / short-term / window-integrated LUFS, sample peak, total gain, session gain, master boost, phase, P, master volume, the sole sounding app, target T) and `apps-<timestamp>.csv` (one row per sounding app per 0.5 s: peak meter, session volume, bus gain, fader, and the app's source loudness with every gain removed, `src_momentary_lufs` / `src_short_lufs`, while it sounds alone) are raw measurements for analysis. None of these files is pruned.

To see one application's history for a run: `pip install pandas matplotlib`, then `python tools\plot_app.py ZenlessZoneZero.exe` (latest run by default; a run timestamp can follow). It writes `<app>-<timestamp>.png` into the current folder, four panels on one time axis: the app's peak meter (dBFS) and session volume; the bus momentary / short-term / integrated perceived loudness with P, green where only this app was sounding (then the bus loudness is its loudness — the program measures the whole mix's LUFS, there is no per-app LUFS); the app's own source loudness (bus gain, fader and session volume removed, i.e. at 100 % session volume and the reference master; only where it sounded alone); bus gain, fader and session volume.

To compare how often the user moved the volume with and without AutoLoud: `python tools\plot_volume.py` (latest run by default) writes `volume-<ts>.png`. The top panel is the level the user controls — in monitor-only stretches the Windows master (blue) with every key press (red) / slider move (green), in automatic-preset stretches P (orange) with every P-slider, volume-key and fader move; the bottom panel is AutoLoud's own automatic gain (purple) and target T (green dashed), empty while monitoring because nothing is moved then. Under the axis are Chrome's tab titles at the time (`--titles` picks another app), so each burst of moves can be matched to the track that was playing.

## Development and testing status

AutoLoud is still being developed and tuned.

Over the past few weeks the project has had more than two hundred hours of development, testing and iteration, producing two candidate versions that were used for tens of hours in games, music and video. Both behave well in the scenarios tested so far.

In the developer's own use, some multi-hour sessions needed one volume adjustment at the start and none afterwards. That is a personal observation under the current test conditions, not yet representative of every device, application or user.

Upcoming validation will focus on:

- whether the number of manual volume adjustments goes down;
- how long it takes to reach the steady state after new content starts;
- whether abrupt or unnecessary gain changes occur;
- how much of the content's dynamics is preserved;
- control quality when several applications sound at once.

## License

[CC BY-NC 4.0](LICENSE) — free to use, modify and share with attribution; no commercial use. The bundled demo track `McG-pro1` is for calibration only.

AutoLoud builds on automatic-mixing research at QMUL C4DM (Centre for Digital Music).
