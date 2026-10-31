# AutoLoud

**Calibrate once; never touch the volume again when switching videos or apps.**

AutoLoud levels every sound on Windows to the comfortable loudness you calibrated. No driver, no audio-stream interception: it measures how loud the system output is in real time (ITU-R BS.1770 LUFS) and quietly adjusts each application's volume so the result lands on your target.

![AutoLoud dashboard](assets/screenshot-dashboard.png)

## Install

1. Download the latest `AutoLoud.exe` from [Releases](../../releases) (single file, ~100 MB, no Python needed).
2. Run it. On first launch SmartScreen may warn — choose "More info → Run anyway". The exe is not code-signed.
3. The calibration wizard opens automatically; it takes about two minutes. AutoLoud then lives in the tray.

## What it does

- **Onset guard**: when sound appears after silence, playback starts 10 dB under your comfort level B and ramps up to B over 2 s. A sudden loud notification never blasts you.
- **Listen, then nudge**: after that, every 5 s of listening leads to one decision: too quiet → up by at most 0.5 dB, too loud → down by at most 2 dB, each over 2 s; within B ± 0.5 dB it stops.
- **Comfortable means hands off**: quiet dialogue, interludes and fade-outs are left alone; only a whole 5 s block above B + 5 dB restarts the adjustment.
- **New work, fresh start**: a track or video change (system media info, or sound after 15 s of silence) starts from the gain that app had last time, then nudges from there.
- **Sudden loudness**: momentary loudness above B + 10 dB (adjustable on the dashboard) is cut fast down to B + 5 dB and a notice appears (with a B slider right in it); it closes after 10 s without interaction, then the fine adjustment resumes from there.
- **Per-app mixer**: every app that produces sound gets a fader (mute to +15 dB) and an "Auto" switch, active even when the bus auto loudness is off; a fader above 0 dB is realised by raising the master and pulling the other apps down.
- **Restores everything**: on exit all app volumes and the master volume return to where they were.

## Calibration

One step: set your external device (headphone amp / speakers / Bluetooth headset) and the Windows master volume to about 80 %, then play music you like — a bundled track, or your own in any app (Spotify, a browser…) selected in the wizard — and slowly raise the fader from 0 to the level you find comfortable. That is your comfort level B.

Each output device keeps its own calibration. Leave the external device and the master volume alone afterwards — AutoLoud uses them as the reference; B itself can be fine-tuned any time with the dashboard slider.

## Run from source / build

Python 3.10+. In PowerShell:

```
.\dev.bat                # venv, dependencies, run from source
.\build.bat              # build dist\AutoLoud.exe
python run.py --mock     # any OS: run the UI on a software simulator
python -m pytest -q      # tests: LUFS vs pyloudnorm, closed-loop controller simulation
```

`.github/workflows/build.yml` runs the tests and builds the exe on every push; a `v*` tag publishes a Release.

## How it works

Windows audio engine → per-app session mix → WASAPI loopback capture → BS.1770 meter → gain controller → per-app session volume (≤ 0 dB) and endpoint volume (only above the calibration reference when a boost is needed). See `autoloud/dsp/loudness.py`, `autoloud/core/controller.py`, `autoloud/core/engine.py`, `autoloud/backends/windows.py`.

## License

[CC BY-NC 4.0](LICENSE) — free to use, modify and share with attribution; no commercial use.
