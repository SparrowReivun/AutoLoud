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

**Ask your ears first, instead of applying one fixed standard.** Calibration is one step. You put your external device and the Windows master volume at about 80 %, play music you like (a bundled track, or your own in any app) and slowly raise the fader from 0 until it feels comfortable — that is target B. (An earlier version also measured a "ceiling A" with a harsh beep; it was dropped: the floor is now simply B − 5 dB.) Each pair of headphones or speakers keeps its own pair, because the same digital level sounds completely different on different devices.

**We measure "how loud it sounds", not "how big the signal is".** The meter follows the loudness standard used by broadcasters and streaming services (BS.1770, in LUFS). It models how sensitive the ear is at different frequencies and automatically ignores silence and quiet passages, so a song with a soft intro is not judged "quiet overall" and pushed too loud.

**Don't touch the audio itself; turn the volume knobs.** The program takes a copy of what is playing from Windows for measurement, then adjusts each application's volume and the master volume. No driver, no added latency, the audio path is untouched. The trade-off is that millisecond-level peak limiting is impossible, so sudden loud sounds are handled by the rules below.

**The first sound after silence: better too quiet than too loud.** While nothing is playing, the program parks the volume at a conservative position: even a very loud ringtone will not exceed B − 5 dB. Once sound appears, it measures how loud the source really is and smoothly raises it to around target B over one block length (5 s by default).

**What counts as a new work, and starting over.** When the player reports track or video changes (title / artist / album in the system media session), follow them; pause, resume, seeking and artwork updates do not count. Without that, an app that has been near-silent for 15 s and then sounds again has started a new work. A new work begins at the gain that app had last time; an app heard for the first time starts a little quiet (3 dB below the current gain).

**Getting comfortable: listen, nudge, listen again.** Listen for a full 5 s before deciding. Too quiet: raise by at most 1 dB, ramped over one block length (5 s by default). Too loud: lower by at most 0.8 dB over the same ramp, except the first decision after a new work, which cuts all the way. Within 2 dB of B: one exact step to B, no creeping. Up to 3 dB above B, or up to 8 dB below: comfortable, stop (lenient downwards, strict upwards). After every step, listen another 5 s before the next. Not one pull to the target: careful on the way up, quicker on the way down.

**Once comfortable, stop chasing.** Quiet dialogue, interludes and fade-outs are left to get quieter; a climax may get louder. Only when two consecutive blocks are above B + 6 dB (one loud block does not count), or three consecutive blocks are below B − 20 dB (it really is too quiet), does the adjustment start again, with the same method, until the level is back near B.

**Sudden loudness.** This rule is always active, no 5 s wait. Momentary loudness above B + 13 dB (threshold adjustable on the dashboard, −5 to +25 dB) is eased down within 0.3 s (adjustable) to B + 5 dB — from the gain actually sounding at that moment — and touched up once the meter has caught up; never straight back to B. The program hears first and then cuts, so the first instant of a blast cannot be taken back.

**After a cut, no bouncing back.** Early versions showed a notice; it was dropped: once the cut has landed and the meter caught up (about a second), the program re-adjusts from the current level with "listen, nudge, listen" on its own, without bothering anyone. If it fires too often, raise the loud-sound trigger on the dashboard or in Settings — both edit the same number. One relief: if the loud passage is over and the sound is now clearly too quiet, the way back up to the pre-cut gain may use the larger 2 dB steps, otherwise one ad would leave a film whispering for minutes.

**When you adjust, you win.** Move an app's fader and the automatic gain leaves that app alone until its current work ends. Move the Windows master volume and the new overall level becomes your new comfort level B; the program continues from that reference.

**One fader per application, and they are relative.** On the mixer page every app that makes sound has a fader (mute at the bottom, +15 dB at the top), active whether or not automatic loudness is on. Windows will not let a single app exceed 100 %, so pushing one app above 0 dB is realised by raising the master volume and lowering the others by the same amount. Settings are remembered by program name; a program with an unhelpful name (Apple Music shows up as AMP) can be given a label of your own.

**Modes, and strong mode per application.** The bus always runs; the dashboard picks which parameter preset ("mode") it runs with. Each app additionally has a "strong mode" tick: every protection cut it trips then pulls its fader down by the excess over B − 5 dB, for good. Tool apps (active but hardly ever sounding) are detected and left out. Since the program hears one combined mix and cannot tell how loud each app is on its own, it uses each app's level meter to estimate what share of the mix comes from the auto apps and discounts the others, so a fixed-level call does not drag everything else down.

**Quit, and everything is back as it was.** All application volumes and the master volume are restored on exit.

## 4. Where the edges are today

Bluetooth headphones apply their volume inside the headset, so the program cannot hear master-volume changes; calibration detects this automatically and handles it, but the detection is not yet rock-solid. Levelling per song or per video is not possible yet — only per program; the next step is the Windows Media Session API. Smart mixing has not started; the per-app faders and Auto switches are its foundation.
