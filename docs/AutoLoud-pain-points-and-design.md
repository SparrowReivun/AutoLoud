# AutoLoud: What We Are Solving, and How the Program Thinks

2026-10-25

## 1. The pain points

**Loudness differs wildly between media.** The loudest material is usually well-produced music and games. Well-produced films and videos are deliberately much quieter for aesthetic reasons, and non-professional short videos are all over the place. The result: every time you open something new, you first reach for the volume, carefully. Switch from music to a video and you can't hear it; switch back and you get blasted.

**Many applications get their own volume logic wrong, popular games included.** Take *Zenless Zone Zero*: to watch a walkthrough video or play some background music while gaming, I set the game's master volume to half. Then every story cutscene with voice acting came in far too loud. It turned out the game's master volume knob does not control its in-game video audio at all. Users cannot fix this kind of thing from inside the application.

**Sudden loud sounds are not just annoying, they hurt.** With headphones on, an unexpected ringtone, ad or cutscene can be 20 dB or more above your normal listening level in an instant.

## 2. What we do about it

A small system-level tool: you calibrate once ("this is how loud I like it"), and from then on it watches everything the computer plays and keeps each application at a level that stays comfortable for you. It works globally and per application.

There are two layers. The first, which exists now, is **automatic loudness**: comfortable listening, sources balanced relative to each other, extreme peaks held off. The second, planned, is **smart mixing**: combine automatic mixing with knowledge of which program you are using right now, so you can focus on the scene in front of you while still hearing what is playing in the background — game effects clear, the walkthrough video audible but not in the way.

## 3. Every decision in the program, in plain words

**Ask your ears first, instead of applying one fixed standard.** Calibration has two steps. A harsh beep plays and you raise the fader to "loud but tolerable" — that is ceiling A. Then music plays (a bundled track, or your own in any app) and you set it to comfortable — that is target B. Each pair of headphones or speakers keeps its own pair, because the same digital level sounds completely different on different devices.

**We measure "how loud it sounds", not "how big the signal is".** The meter follows the loudness standard used by broadcasters and streaming services (BS.1770, in LUFS). It models how sensitive the ear is at different frequencies and automatically ignores silence and quiet passages, so a song with a soft intro is not judged "quiet overall" and pushed too loud.

**Don't touch the audio itself; turn the volume knobs.** The program takes a copy of what is playing from Windows for measurement, then adjusts each application's volume and the master volume. No driver, no added latency, the audio path is untouched. The trade-off is that millisecond-level peak limiting is impossible, so sudden loud sounds are handled by the rules below.

**The first sound after silence: better too quiet than too loud.** While nothing is playing, the program parks the volume at a conservative position: even a very loud ringtone will not exceed ceiling A. Once sound appears, it measures how loud the source really is and smoothly raises it to target B over two seconds.

**While you are listening, don't fiddle.** Music and films have their own loud and quiet moments; that is part of the work and must not be flattened. So during steady playback the program looks at 30-second stretches, asks "how loud was this stretch overall", and adjusts only between stretches — always as a two-second fade, never a jump. The first stretch is only 12 seconds, because the first two seconds' estimate is rough and needs an early correction.

**But if it is really too loud, act at once.** If a moment is more than 8 dB above the target (typically an ad cut into a video, or a game switching to a cutscene), the program does not wait for the 30 seconds to end; it pulls the level down within 0.3 seconds. The 8 dB threshold keeps normal musical contrast untouched.

**A new sound arriving while something is already playing does not restart the protection.** Say music is on and you open a video. The level is not dropped to A and raised again — that would be an audible dip — it is left to the next stretch adjustment. Only a start from complete silence goes through "conservative first, then raise".

**One fader per application, and they are relative.** On the mixer page every app that makes sound has a ±40 dB fader, active whether or not automatic loudness is on. Windows will not let a single app exceed 100 %, so pushing one app above 0 dB is realised by raising the master volume and lowering the others by the same amount. Settings are remembered by program name; a program with an unhelpful name (Apple Music shows up as AMP) can be given a label of your own.

**Automatic loudness can be switched off per application.** Each app has an "Auto" switch: on, the bus auto gain applies to it; off, it stays at its own fader. That lets a call keep a fixed level while only music and video take part in the levelling. Since the program hears one combined mix and cannot tell how loud each app is on its own, it uses each app's level meter to estimate what share of the mix comes from the auto apps and discounts the others, so a fixed-level call does not drag everything else down.

**Quit, and everything is back as it was.** All application volumes and the master volume are restored on exit.

## 4. Where the edges are today

Bluetooth headphones apply their volume inside the headset, so the program cannot hear master-volume changes; calibration detects this automatically and handles it, but the detection is not yet rock-solid. Levelling per song or per video is not possible yet — only per program; the next step is the Windows Media Session API. Smart mixing has not started; the per-app faders and Auto switches are its foundation.
