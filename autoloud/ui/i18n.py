"""Tiny string table (zh / en)."""
from __future__ import annotations

_T = {
    "app.title": ("AutoLoud 自动响度均衡", "AutoLoud – Automatic Loudness Leveler"),
    "nav.dashboard": ("总览", "Dashboard"),
    "nav.calibrate": ("校准", "Calibrate"),
    "nav.apps": ("应用混音", "Apps"),
    "nav.settings": ("设置", "Settings"),
    "device": ("输出设备", "Output device"),
    "enable": ("总线自动响度", "Bus auto loudness"),
    "status.idle": ("待机（无音频）", "Idle (no audio)"),
    "status.onset": ("起始保护中", "Onset guard"),
    "status.adjust": ("听 5 秒、拧一点", "Listening & stepping"),
    "status.steady": ("稳定（不追）", "Steady"),
    "status.safety": ("响度过高保护", "Loudness protection"),
    "status.calibrating": ("校准中（已暂停自动控制）", "Calibrating (auto control paused)"),
    "status.disabled": ("已关闭", "Disabled"),
    "status.uncal": ("此设备尚未校准 → 请先校准", "This device is not calibrated yet → run calibration"),
    "dash.b": ("舒适响度 B", "Comfort level B"),
    "dash.b.hint": ("像音量旋钮一样拖：声音立刻跟着变，停下的位置就是新的目标 B。拖到来源本身达不到的位置时，提升会停在主音量余量的上限（不会削波）。",
                    "Drag it like a volume knob: the sound follows at once and where you stop becomes the new target B. Beyond what the source can reach, the boost stops at the master-volume headroom (no clipping)."),
    "meter.momentary": ("瞬时 (400ms)", "Momentary (400 ms)"),
    "meter.short": ("短时 (3s)", "Short-term (3 s)"),
    "meter.integrated": ("窗口积分", "Window integrated"),
    "meter.target": ("目标", "Target"),
    "gain.session": ("应用衰减", "Session gain"),
    "gain.master": ("主音量提升", "Master boost"),
    "gain.total": ("总增益", "Total gain"),
    "a": ("A 下限", "A (floor)"),
    "b": ("B 舒适", "B (comfort)"),
    "recal": ("重新校准此设备", "Re-calibrate this device"),
    "cal.title": ("校准向导", "Calibration wizard"),
    "cal.intro.h": ("准备", "Before you start"),
    "cal.intro": (
        "1. 戴好耳机 / 打开音箱，选择要校准的输出设备。\n"
        "2. 把 Windows 主音量调到你平时的较高位置（建议 70%~100%），校准期间不要再动它。\n"
        "3. 暂停其它正在播放声音的程序，否则会影响测量。\n\n"
        "校准会先播放一段刺耳的提示音，让你找到「吵但还能忍受」的响度上限 A；\n"
        "然后播放你选择风格的音乐，让你找到「舒适」的响度 B。",
        "1. Put on your headphones / turn on your speakers and pick the device to calibrate.\n"
        "2. Set the Windows master volume to your usual high position (70–100 %) and leave it alone.\n"
        "3. Pause anything else that is playing.\n\n"
        "First a harsh beep lets you find the loudest level you can tolerate (A);\n"
        "then music of your chosen genre lets you find your comfortable level (B)."),
    "cal.beep.h": ("第 1 步 · 响度上限 A", "Step 1 · Loudness ceiling A"),
    "cal.beep": (
        "点击播放，然后把推子从 0 慢慢往上拉，直到声音「吵但可以心平气和地忍受」。\n"
        "这个值会成为所有新声音事件的起始上限：任何突然出现的声音都不会比它更响。",
        "Press play, then slowly raise the fader from 0 until the beep is loud but still tolerable.\n"
        "Every new sound event will start no louder than this."),
    "cal.music.h": ("第 2 步 · 舒适响度 B", "Step 2 · Comfort level B"),
    "cal.music": (
        "选择来源并播放，把推子调到最舒适的听音响度。\n"
        "这一步可以跳过：直接点「下一步」，之后在总览页用「舒适响度 B」滑块边听边定。",
        "Pick a source, play it and set the fader to the level you enjoy.\n"
        "This step is optional: press Next and set B later with the dashboard slider while listening to anything.\n"
        "Every source will be levelled to this loudness."),
    "cal.source": ("来源", "Source"),
    "cal.source.hint": ("可以选内置曲目，也可以先用任意应用（Spotify、浏览器…）播放你自己的音乐，再在这里选中它；推子会直接控制那个应用的音量。",
                        "Pick a bundled track, or start your own music in any app (Spotify, a browser...) and select it here; the fader then drives that app's volume."),
    "cal.builtin": ("内置", "Built-in"),
    "cal.app": ("应用", "App"),
    "cal.ext.active": ("正在测量该应用的输出；用推子调到舒适响度", "Measuring that app; set the fader to a comfortable level"),
    "cal.decoding": ("解码中…", "Decoding…"),
    "cal.music.none": ("未找到音乐文件：在「设置」中指定音乐文件夹（每个风格一个子文件夹）。现在将使用合成的测试音乐。",
                       "Music library is empty: set a music folder in Settings (one sub-folder per genre). Using synthetic test music for now."),
    "cal.active_apps": ("⚠ 仍有程序在发声，会干扰测量，请先暂停：", "⚠ Still playing audio (will disturb the measurement), please pause: "),
    "cal.vis": ("主音量位置", "Master volume stage"),
    "cal.vis.sw": ("软件音量（回录可见）", "software (visible in loopback)"),
    "cal.vis.hw": ("硬件/驱动音量（回录不可见）", "hardware/driver (hidden from loopback)"),
    "cal.done.h": ("完成", "Done"),
    "cal.done": ("校准结果已保存到此设备的配置。你可以随时重新校准。", "Saved for this device. You can re-calibrate any time."),
    "play": ("播放", "Play"),
    "stop": ("停止", "Stop"),
    "next": ("下一步", "Next"),
    "back": ("上一步", "Back"),
    "finish": ("保存并完成", "Save & finish"),
    "cancel": ("取消", "Cancel"),
    "measured": ("当前测得响度", "Measured loudness"),
    "genre": ("音乐风格", "Genre"),
    "track": ("曲目", "Track"),
    "apps.h": ("应用混音台", "App mixer"),
    "apps.hint": ("每个应用一条推子（最低静音，最高 +15 dB），总线自动响度开关的时候都有效。推到 0 dB 以上时，程序会抬高主音量并把其它应用压低相同的量来实现。\n"
                  "「自动」勾选 = 总线的自动增益作用于该应用；取消勾选 = 只按推子固定。你手动动过的推子，在该应用当前作品结束前自动增益不会再碰它。",
                  "One fader per app (mute at the bottom, +15 dB at the top), active whether the bus auto loudness is on or off. Above 0 dB the master is raised and the other apps are pulled down by the same amount.\n"
                  "\u201cAuto\u201d ticked = the bus auto gain applies to this app; unticked = the app stays at its fader only."),
    "apps.fader": ("推子", "Fader"),
    "apps.auto": ("自动", "Auto"),
    "apps.auto.tip": ("总线自动响度是否作用于这个应用", "Whether the bus auto loudness applies to this app"),
    "apps.reset": ("归零", "Reset to 0 dB"),
    "apps.alias.tip": ("给这个应用起个好认的名字（例如把 AMP 改成 Apple Music）", "Give this app a recognisable name (e.g. AMP → Apple Music)"),
    "apps.idle": ("未发声", "idle"),
    "apps.work_n": ("作品 {n}", "Work {n}"),
    "apps.work.tip": ("♪ 来自系统媒体信息（标题/作者）；▶ 按「静音 15 秒后再发声」计数", "♪ from system media info (title/artist); ▶ counted by the 15 s-silence rule"),
    "set.worksilence": ("作品切分：静音多少秒后视为新作品", "New work after this many seconds of silence"),
    "set.listen": ("每次决定前听多久 (s)", "Listen before each step (s)"),
    "set.stepup": ("每步最多调高 (dB)", "Max raise per step (dB)"),
    "set.stepdown": ("每步最多调低 (dB)", "Max cut per step (dB)"),
    "set.deadband": ("算作「已舒适」的误差 (±dB)", "Comfortable within (±dB)"),
    "set.steadytrig": ("稳定后整段 5 秒超过 B 多少才重调 (dB)", "Re-adjust when a 5 s block exceeds B by (dB)"),
    "set.safetyhold": ("保护提示自动关闭 (s)", "Protection notice auto-closes after (s)"),
    "safety.title": ("识别到巨大音量，已自动进入响度过高保护", "Very loud sound detected — loudness protection engaged"),
    "safety.body": ("已把超出的部分压下去。如果觉得保护过于频繁，请调大保护阈值。", "The excess has been cut. If this happens too often, raise the protection threshold."),
    "safety.threshold": ("当前阈值：你的听音偏好 B ({b:.1f} LUFS) + {m:.1f} dB = {v:.1f} LUFS", "Threshold: your comfort level B ({b:.1f} LUFS) + {m:.1f} dB = {v:.1f} LUFS"),
    "safety.count": ("本次运行第 {n} 次触发 · 5 秒内无操作自动关闭并从当前音量重新调整", "Cut #{n} this run · closes after 5 s without interaction, then re-adjusts from here"),
    "safety.close": ("知道了", "OK"),
    "set.ramp": ("渐变时间 (s)", "Ramp time (s)"),
    "set.onset_ramp": ("起始 A→B 渐变 (s)", "Onset A→B ramp (s)"),
    "set.window": ("分析窗口 (s)", "Analysis window (s)"),
    "set.first_window": ("起始后首个窗口 (s)", "First window after onset (s)"),
    "set.guard": ("起始保护假设响度 (LUFS)", "Onset guard assumed loudness (LUFS)"),
    "set.safety": ("安全余量 (dB)", "Safety margin (dB)"),
    "set.boost": ("最大主音量提升 (dB)", "Max master boost (dB)"),
    "set.silence": ("静音判定 (s)", "Silence timeout (s)"),
    "set.srcchange": ("新应用发声后快速重估 (s, 0=关)", "Quick re-estimate after new app (s, 0=off)"),
    "set.music_dir": ("校准音乐文件夹", "Calibration music folder"),
    "set.browse": ("浏览…", "Browse…"),
    "set.minimized": ("启动时最小化到托盘", "Start minimized to tray"),
    "set.lang": ("语言 / Language", "Language / 语言"),
    "set.save": ("保存设置", "Save settings"),
    "tray.show": ("显示窗口", "Show"),
    "tray.quit": ("退出", "Quit"),
    "error": ("错误", "Error"),
    "backend": ("后端", "Backend"),
}

_lang = "en"   # en-ui branch: English-only build


def set_language(lang: str) -> None:
    """English-only build: the language is fixed regardless of config."""
    global _lang
    _lang = "en"


def t(key: str) -> str:
    pair = _T.get(key)
    if pair is None:
        return key
    return pair[1] if _lang == "en" else pair[0]
