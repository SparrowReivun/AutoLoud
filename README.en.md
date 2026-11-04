<div align="center">

# AutoLoud

### From constant volume adjustments to consistently comfortable listening.

**Stop adjusting the volume. Just enjoy the sound.**

*You adjust once. We handle the rest.*

[简体中文](README.md) · [**English**](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Русский](README.ru.md) · [Latest release](https://github.com/SparrowReivun/AutoLoud/releases) · [Report an issue](https://github.com/SparrowReivun/AutoLoud/issues)

</div>

## Why AutoLoud?

Have you ever wondered how many times a day you need to adjust the volume?

Every time we switch between videos and music, or move from one scene to another in a game, we seem to be caught between dialogue that is too quiet and music that is too loud. And then there are those WeChat notifications loud enough for the whole office to hear.

For over a century, we have relied on volume controls, repeatedly turning the sound up and down. Most of the time, we have only one goal:

**To make the sound comfortable to listen to.**

So why can't we simply tell our devices how loud we like things, and let them take care of the rest?

That is the problem AutoLoud sets out to solve.

You don't have to change your habits. Keep using the volume keys just as you always have. The difference is that this time, your adjustment does more than change the current volume: it also tells AutoLoud what you prefer.

> **“This is how loud I like it.”**

AutoLoud acts as your personal sound assistant: it continually learns and remembers your listening preferences, adapts to loudness differences across content, and adjusts the volume to help you stay immersed in what you are listening to.

*“Every volume adjustment teaches your device what you like.”*

---

## Download and installation

1. Visit [Releases](https://github.com/SparrowReivun/AutoLoud/releases) and download the latest `AutoLoud-windows.zip` (approximately 100 MB; Python is not required).
2. Extract the archive anywhere and run `AutoLoud.exe`. **Keep the extracted folder intact**: the executable depends on files alongside it.
3. Before your first launch, connect your headphones or speakers and make sure audio playback works. Follow the initial setup prompts to get started.

**First-run security notice:** Windows SmartScreen may display “Windows protected your PC.” First confirm that the file came from this project's official Releases page and verify its source. If you trust the file, choose “More info → Run anyway.”

**Antivirus alerts:** AutoLoud is currently applying for code signing. Because it listens for volume-key input, captures system loopback audio, and controls individual applications' volume, heuristic scanners such as Microsoft Defender or 360 may flag it incorrectly. The project is open source: you can inspect the code, check your download with [VirusTotal](https://www.virustotal.com/), or [run it from source](#running-from-source-developers) if you prefer.

**Output device not detected?** This can occur particularly with Bluetooth headphones. First switch to the intended device in Windows, then refresh the device list in AutoLoud. If necessary, restart AutoLoud.

**Language:** AutoLoud selects Chinese or English based on your Windows display language; you can also switch languages using the last option on the Settings page.

![Language selector in the Settings page](assets/readme-zh/language-setting.png)

## 1. Getting started

One of AutoLoud's design goals is **zero additional learning curve**.

After launch, AutoLoud takes over parts of the system volume-control logic without changing how you interact with it. You can still adjust the sound with a hardware volume knob, keyboard volume keys, or the sliders in the app.

AutoLoud records your adjustments as **preference loudness P**, and gradually learns the loudness characteristics of different applications.

Over time, it adapts to:

- Loudness differences between music and videos;
- Quiet dialogue versus intense combat in games;
- The typical loudness of individual applications;
- Natural dynamic changes within content;
- Notification sounds that are difficult to adjust independently in some apps.

Most importantly, AutoLoud **does not simply flatten every sound to the same level**.

Drawing on research in automatic music mixing, we aim to control overall loudness while respecting creators' intentions as much as possible: **quiet passages should stay quiet, and intense scenes should retain their impact**.

AutoLoud also provides a **per-application mixer**, giving you independent control over each application's volume.

![AutoLoud per-application mixer](assets/readme-intl/app-mixer-en.png)

*Figure: The application mixer offers independent faders and control presets.*

## 2. Background

### 2.1 Attempts to improve volume control

People have been trying to control playback volume for more than a century. As early as 1902, a gramophone patent described a mechanism for changing the volume of sound it produced [1].

More and more industries and companies now recognize the importance of reducing loudness differences and improving listening comfort.

Broadcasting, cinema, and streaming have gradually adopted loudness standards such as ITU-R BS.1770 and EBU R 128 [2][3]. Apple Music, YouTube, and Spotify also offer different forms of loudness normalization to reduce discrepancies between tracks or videos [4][5][6].

Yet these solutions cannot cover every listening situation. For example:

- **Differences between applications.** While listening to music at a comfortable volume, a WeChat notification may suddenly be much louder, and the application may not offer a convenient independent notification-volume setting.
- **Excessive dynamics within a work.** In some games, including *Zenless Zone Zero*, players may find dialogue too quiet but combat effects overwhelming. Similar problems can occur with videos or music.
- **Switching between apps and content.** Moving among music, videos, games, and other apps can still require frequent manual adjustments.

These issues rely heavily on users correcting them themselves. Delayed adjustments can interrupt activities, cause discomfort, and, at sufficiently high sound levels, may pose hearing risks [7].

### 2.2 Why not make every sound equally loud?

Some applications and audio plugins already address related problems using compressors or automatic gain control, such as Waves Vocal Rider [8].

In early versions of AutoLoud, we explored a similar approach: keeping loudness inside a fixed range chosen by the user.

Testing quickly revealed its limitations.

**Dynamics are part of the artistic expression.**

Music needs both calm passages and climaxes. Films need quiet dialogue as well as dramatic scenes. Games use changes in loudness to create immersion.

If every sound is forced into a narrow loudness interval, the system has to change gain frequently. This can damage the dynamics that the creator intended and make listening feel unnatural.

The real challenge is therefore not simply to make everything equally loud, but rather:

> **How can we preserve a work's intended dynamics while keeping different content as comfortable to listen to as possible?**

I explored several different designs, spent more than 200 hours developing, testing, and iterating on them, and eventually arrived at AutoLoud's current core approach.

It combines user-preference learning, per-application loudness-distribution modeling, dynamic target loudness, and steady-state control into one solution for everyday volume problems.

*You adjust once. We handle the rest.*

## 3. Core design principles

AutoLoud's long-term goal is to explore a new paradigm for volume control.

Conventional volume control requires the user to notice a problem and correct it. AutoLoud instead seeks to make volume control an ongoing process of learning and adapting to the user's listening goals.

The design centers on three principles.

### 3.1 From action-driven to goal-driven control

Traditional volume control is reactive: you notice the sound is too loud or too quiet, then manually correct it. Each adjustment usually solves only the immediate problem.

But every adjustment also contains information about your listening preferences.

AutoLoud treats adjustments not merely as commands, but as preference signals.

When you adjust the sound to a comfortable level, the system records the current audio and corresponding loudness, derives your **preference loudness P**, and uses that reference for subsequent playback.

Instead of repeatedly deciding “a little louder” or “a little quieter,” you are effectively telling your device:

> **“This is how I want things to sound.”**

### 3.2 Respecting creators' original intentions

**Preference loudness P does not mean that every sound should be adjusted to P.**

This was an important lesson from AutoLoud's early experiments.

Quiet passages and climaxes, soft dialogue and intense combat, are supposed to differ. A user's volume adjustment expresses a preference for **the content playing at that moment**; it does not mean that exact loudness is appropriate for the entire work.

Simply defining a fixed comfortable interval around P creates a difficult trade-off:

- **Too narrow:** The system constantly interferes with normal loudness changes, bringing quiet and loud passages closer together and undermining the intended dynamics.
- **Too wide:** It preserves the dynamics, but no longer effectively controls sounds that are much too loud or too quiet, leaving the user to make manual corrections.

To address this trade-off, AutoLoud introduces **dynamic target loudness T**.

Rather than forcing everything into one fixed interval, it calculates an appropriate target based on each application's historical loudness distribution, the current sound's relative position within that distribution, and the user's preference P.

The aim is to reduce manual volume adjustments while preserving the work's dynamic layers: **quiet passages remain quiet, dramatic moments retain their impact, and overall loudness remains as close as possible to the user's preferences.**

### 3.3 Steady-state control: listen more, adjust less

Automatic control must respond promptly, but it also needs restraint.

If the program changes gain whenever loudness changes, users hear frequent fluctuations, sometimes including an unnatural “pumping” effect.

That is why AutoLoud introduces a **steady-state mechanism**.

Its principle is:

> **Keep listening; adjust only when needed. Raise gently, lower faster.**

Within a reasonable range, temporarily quiet audio is often more tolerable than unexpectedly loud audio.

AutoLoud therefore responds more readily to excessive loudness, while allowing more latitude for quieter sounds.

Once playback is comfortable, it avoids unnecessary adjustments. Only a sustained mismatch triggers the search for a new steady state.

This is a key difference from simple loudness limiting.

## 4. Technical design

### 4.1 Learning per application rather than using a universal loudness model

Applications have different sonic characteristics. Music players, video platforms, games, and messaging apps can vary significantly in their loudness distributions and dynamic ranges.

Even within the same application, different works can sound completely different.

Ideally, we would learn the loudness distribution of each individual work. But this requires storing and managing a large amount of historical data, and some applications (such as Apple Music) do not currently expose reliable track information through the interfaces available to us.

For now, AutoLoud uses a more practical strategy:

**Learn the historical loudness distribution of each application, then combine it with the user's preference to calculate the current target.**

Where content metadata is available, it helps identify the beginning of a new work.

The per-application mixer also allows users to adjust applications independently, helping with unusually loud notifications and apps that lack convenient controls.

### 4.2 Learning the source-loudness distribution

When only one application is audibly active, AutoLoud can estimate its source loudness after undoing the effects of applied gain: `L_src`.

It gradually learns two reference points in that application's historical source-loudness distribution:

- **C_low:** The 20th percentile, representing relatively quiet passages.
- **C_high:** The 80th percentile, representing relatively loud passages.

These values are updated continuously as the application is used.

Learning is measured in active audio time, with a half-life of about 20 minutes. This balances historical information with adaptation to changes in how the application is used.

Both reference points are visible in the mixer, where they can also be reset and relearned.

To handle insufficient data or unstable distributions, AutoLoud uses several fallback rules:

- With less than **30 seconds** of valid learning data, temporarily use P as the target.
- If C_low and C_high differ by less than **1 LU**, also use P.
- When several applications are active and individual source loudness cannot be estimated reliably, retain the previous T; after **60 seconds** without a valid measurement, fall back to P.
- If the source loudness stays more than **6 LU below C_low** for two minutes (for example after reducing the volume inside an app), rebuild the reference distribution from those two minutes of data.

### 4.3 Calculating dynamic target loudness T

Once the historical distribution is available, AutoLoud finds the current source loudness's relative position between the two reference points:

```math
u=\mathrm{clip}\left(\frac{L_{\mathrm{src}}-C_{\mathrm{low}}}{C_{\mathrm{high}}-C_{\mathrm{low}}},0,1\right)
```

Here, `clip` limits the result to the range from 0 to 1.

It then calculates target loudness T from the user preference P and a dynamic-range parameter X:

```math
T=(P-X)+X\cdot u
```

| Parameter | Meaning |
| --- | --- |
| P | User preference loudness |
| X | Maximum amount by which the target can fall below P |
| L_src | Application source loudness with applied gain removed |
| C_low | 20th percentile of historical source loudness |
| C_high | 80th percentile of historical source loudness |
| u | Relative source-loudness position between the two reference points |
| T | Current target loudness |

Thus:

- Near the quiet reference point, T approaches P − X.
- Near the loud reference point, T approaches P.
- If source loudness increases further, T still cannot exceed P.

**This bounds the target, not the actual output level at every instant.** Actual gain changes are also governed by steady-state control.

#### Worked example

Suppose an application's historical reference values are:

```math
C_{\mathrm{low}}=-25\ \mathrm{LUFS}, \qquad C_{\mathrm{high}}=-17\ \mathrm{LUFS}
```

They differ by 8 LU, so:

```math
u=\mathrm{clip}\left(\frac{L_{\mathrm{src}}+25}{8},0,1\right)
```

If the user's preference and the dynamic-range parameter are:

```math
P=-25\ \mathrm{LUFS}, \qquad X=6\ \mathrm{LU}
```

then:

```math
T=-31+6u
```

| Current source loudness | Relative position u | Target T |
| --- | ---: | ---: |
| −25 LUFS | 0 | −31 LUFS |
| −23 LUFS | 0.25 | −29.5 LUFS |
| −21 LUFS | 0.5 | −28 LUFS |
| −19 LUFS | 0.75 | −26.5 LUFS |
| −17 LUFS | 1 | −25 LUFS |
| −5 LUFS | 1 | −25 LUFS |

Therefore, **the calculated T always lies in [P − X, P]**, regardless of how extreme the application's source loudness becomes.

This reduces unwanted loudness differences while retaining relative contrasts between quieter and louder passages.

### 4.4 Steady-state control

Dynamic target loudness T alone is not enough to ensure natural, stable listening.

A change in target loudness does not mean the system should always react immediately. Constantly adjusting gain to follow T—especially repeatedly raising and lowering it—can disrupt continuity and immersion.

So AutoLoud must distinguish between **normal artistic dynamics** and **a sustained loudness mismatch that requires intervention**.

That is what the steady-state mechanism does.

#### Steady state: listen more, adjust less

Steady state means the playback is already in a comfortable listening range and gain does not currently need to change.

AutoLoud keeps measuring loudness, but does not react to every fluctuation. Unless there is a sustained and noticeable mismatch, it holds the gain steady to preserve the content's natural dynamics.

In the current design, the system enters steady state by default after the user manually adjusts the volume or new content begins playing.

#### When does it adjust again?

AutoLoud continuously monitors momentary loudness (400 ms) and evaluates loudness in 2-second steps.

It leaves steady state and adjusts only under these conditions:

| Situation | Trigger |
| --- | --- |
| Sound persistently too loud | 2 consecutive steps above T + 2.5 dB |
| Sound persistently too quiet | 3 consecutive steps below T − 4 dB |

Two principles guide this behavior:

- **Distinguish sustained problems from brief dynamics.** A deviation must persist before it triggers an adjustment, so natural peaks and dips are less likely to be mistaken for volume problems.
- **Be more sensitive to excessive loudness than to quietness.** In our listening tests, temporarily quiet sound was often more tolerable than sudden excessive volume, within reasonable limits.

#### Adjusting after leaving steady state

After leaving steady state, AutoLoud compares current loudness with T, changes gain gradually and smoothly toward that target, and then enters steady state again.

This corrects persistent deviations without unnecessary abrupt or alternating changes.

![Illustration of steady-state thresholds, state changes, and smooth gain adjustment](assets/readme-intl/steady-state-control-en.png)

*Figure: The upper diagram shows how sustained excessive or insufficient loudness exits steady state; the lower diagram illustrates smooth gain adjustment and the return to steady state. Curves are illustrative, not measured data.*

**Dynamic target T determines how loud a sound should ideally be; steady-state control determines when an adjustment is actually necessary.**

Together, they follow a simple rule:

> **Step in when needed. Otherwise, stay out of the way.**

In my own testing, smooth gain increases of approximately **0.5 dB every 2 seconds**, and decreases of approximately **1 dB every 2 seconds**, generally did not cause noticeable disruption. These values remain subject to further testing and refinement.

### 4.5 Excessive-loudness protection

**Reducing the impact of potentially harmful loud sounds is a core design priority for AutoLoud.**

Many of us have experienced a notification suddenly blasting out while we were listening comfortably to music.

AutoLoud therefore includes a separate **excessive-loudness protection mechanism**, which takes priority over normal loudness-control rules.

When measured loudness exceeds a configured threshold, the system triggers protection and rapidly reduces gain in approximately **0.05 seconds**, keeping the output at a lower level afterward.

This does not completely eliminate the initial sound impulse or replace professional hearing protection. It is intended to reduce discomfort from unexpected loud sounds. A digital loudness threshold is also **not equivalent to a guaranteed safe sound-pressure level** at the ear.

The default threshold is relatively conservative. If it triggers too frequently, you can adjust it to suit your listening environment.

![Excessive-loudness protection setting on the AutoLoud overview page](assets/readme-intl/overview-en.png)

*Figure: Adjusting the protection threshold from the overview page.*

### 4.6 Further implementation details

Beyond these mechanisms, AutoLoud includes application-volume change detection, reset and recovery for learned loudness distributions, smooth transitions between states, and extensive parameter tuning informed by listening tests.

These details matter to the final experience, but are beyond the scope of this README.

## 5. Testing and real-world experience

Over the past few weeks, I have spent more than **200 hours** developing, testing, and refining different approaches to volume control.

Along the way, I abandoned ideas that looked promising but sounded unnatural in practice. I eventually arrived at two versions I am relatively happy with, and tested them for dozens of hours across music, video, and gaming.

**The change that makes me happiest is that I no longer need to keep reaching for the volume controls.**

In the past, I might adjust the volume hundreds of times over several hours at my computer. With AutoLoud, in the scenarios I have tested so far, I can often set it once at startup and continue for hours with almost no further manual intervention.

That is what I wanted AutoLoud to achieve:

**Less time adjusting the volume. More time enjoying the sound.**

In addition, in a small comparison test playing the **same content**, we recorded a drop in the number of manual volume adjustments from **24 to 6 — a 75% reduction**.

![Manual adjustments fell from 24 to 6 for the same content](assets/readme-intl/test-reduction-75-percent-en.png)

*Figure: Manual adjustments recorded in one small comparison test with the same content. 24 → 6, a 75% decrease. The result applies only to this test.*

We also logged volume actions and system gain in “monitor-only” and “automatic adjustment” modes. The following independent runs are provided for readers who want to inspect the behavior in more detail.

<details>
<summary><strong>View original test plots (expand)</strong></summary>

**Monitor-only mode:** Records the Windows master volume and the user's manual volume actions; AutoLoud does not apply gain automatically.

![Monitor-only session: volume-adjustment log](assets/readme-zh/test-monitor-log.png)

**AutoLoud automatic-adjustment mode:** Logs user actions together with automatic gain and target-loudness changes.

![Automatic-adjustment session: volume actions and gain log](assets/readme-zh/test-autoloud-log.png)

> Note: These plots show separate sessions. Their durations and counting conventions differ from the “24 → 6” summary above; the counts visible in these plots must not be used directly to derive the 75% figure.

</details>

So far, the evidence comes primarily from my own use and small-scale testing. I hope to validate and improve the approach with feedback from a wider range of users.

## 6. Known limitations and future plans

AutoLoud is still under active development and testing, with plenty of room to improve.

### 6.1 New applications need a short learning period

When you use a new application for the first time, AutoLoud does not yet know its loudness characteristics, so an initial manual adjustment may still be necessary.

After at least **30 seconds** of valid audio data, it can start using the learned distribution, and control typically improves over time.

### 6.2 Better handling of highly dynamic music

With works that have a wide dynamic range, such as classical symphonic music, the current version can sometimes over-adjust and interfere with intended dynamics.

Preserving these dynamics more faithfully is one of our next priorities.

Test reference: [Classical symphonic music example](https://www.bilibili.com/video/BV1Vg3uzoEK5?t=8.2).

### 6.3 Audio-device switching issues

Frequent output-device changes, especially involving Bluetooth headphones, can sometimes prevent AutoLoud from detecting the active device promptly. This may relate to Windows audio-device APIs and device-state updates.

Try selecting the Bluetooth device in Windows first and refreshing AutoLoud's device list; if that fails, restart the program.

### 6.4 Reducing adjustment latency further

AutoLoud currently analyzes audio that has already played, so a certain response delay is unavoidable.

In the future, we hope to explore obtaining buffered audio before playback—where technology and permissions allow—so that loudness changes can be predicted in advance.

This could make control faster, smoother, and potentially almost imperceptible.

### 6.5 Supporting more operating systems

AutoLoud is currently available **only for Windows**.

We are open to future macOS and Linux support and welcome discussions and collaboration with interested developers and researchers.

Contact:

- [jinjie.shi@qmul.ac.uk](mailto:jinjie.shi@qmul.ac.uk)
- [joshua.reiss@qmul.ac.uk](mailto:joshua.reiss@qmul.ac.uk)

## 7. Usage tips and feedback

If AutoLoud is useful in your daily routine, consider enabling **“Start AutoLoud with Windows”** on the overview page, so you don't have to launch it manually each time.

AutoLoud continues to evolve. We welcome reports about the experience, especially audio situations that it still handles poorly.

Whether it is a song, a game, a video, or an annoying notification, your feedback may reveal new problems and help improve the system.

Feel free to [email us](mailto:jinjie.shi@qmul.ac.uk) or open an issue on [GitHub Issues](https://github.com/SparrowReivun/AutoLoud/issues).

## Final thoughts

As someone who is often moved by music, and as a researcher in automatic mixing, I have long hoped that advances in the field could benefit not only professional audio production, but also everyone's everyday listening experience.

We have increasingly powerful audio-processing technology, yet something as fundamental as volume control still requires so much manual attention.

I hope AutoLoud can be one step toward changing that.

Let's focus on what really matters: **enjoying music, films, and games.**

**AutoLoud — Stop adjusting. Start listening.**

---

## Running from source (developers)

From the repository root:

```bash
pip install -r requirements.txt
python run.py
```

Run tests:

```bash
pip install -r requirements-dev.txt
pytest
```

AutoLoud uses Windows WASAPI loopback for system-audio analysis and Windows audio-session interfaces for volume control. For details, see the `autoloud/`, `tools/`, and `tests/` directories.

<details>
<summary><strong>Logs and data analysis (expand)</strong></summary>

- `autoloud.log`: Runtime log.
- `adjust-<time>.log`: Volume-control decisions.
- `meters-<time>.csv`: Real-time system loudness, gain, state, and target measurements.
- `apps-<time>.csv`: Session volume and source-loudness data for individual applications.
- `volume-<time>.csv`: User volume actions and active mode, useful for counting manual adjustments.

Logs are typically stored in the `logs/` folder alongside the application; if that folder is not writable, they may fall back to `%APPDATA%\AutoLoud\`.

Use `python tools/plot_volume.py` to plot user volume actions and automatic gain. Use `python tools/plot_app.py <process-name>` to inspect an application's history. Plotting requires `pandas` and `matplotlib`.

</details>

## References

[1] Berliner, E. (1902). *Gramophone*. U.S. Patent No. 692,502. [Library of Congress](https://www.loc.gov/resource/berl.11010127/).

[2] International Telecommunication Union. *Recommendation ITU-R BS.1770: Algorithms to Measure Audio Programme Loudness and True-Peak Audio Level*. [ITU](https://www.itu.int/rec/R-REC-BS.1770/en).

[3] European Broadcasting Union. *EBU R 128: Loudness Normalisation and Permitted Maximum Level of Audio Signals*. [EBU](https://tech.ebu.ch/publications/r128).

[4] Apple Support. *Use Sound Check in Apple Music*. [Apple](https://support.apple.com/en-us/109331).

[5] YouTube Help. *Control video volume — Stable volume*. [YouTube](https://support.google.com/youtube/answer/14106294).

[6] Spotify for Artists. *Loudness normalization on Spotify*. [Spotify](https://support.spotify.com/us/artists/article/loudness-normalization/).

[7] World Health Organization. *Deafness and hearing loss: Safe listening*. [WHO](https://www.who.int/news-room/questions-and-answers/item/deafness-and-hearing-loss-safe-listening).

[8] Waves Audio. *Riding Vocals with Vocal Rider*. [Waves](https://www.waves.com/riding-vocals-with-vocal-rider).

## License

[CC BY-NC 4.0](LICENSE) — Use, modification, and sharing are permitted under the license terms with attribution; commercial use requires separate permission. The sample audio included in the repository may have additional usage restrictions; consult the repository documentation.

AutoLoud builds on automatic-mixing research at QMUL C4DM (Centre for Digital Music).
