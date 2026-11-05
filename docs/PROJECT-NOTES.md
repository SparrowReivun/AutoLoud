# AutoLoud 项目备忘：背景、关键记忆与文档约定

> 给未来的自己和协作者（人或 AI）看的。新开一轮工作前先读这一页，再读 `CHANGELOG.md` 最上面一节。
> 最后更新：2026-11-04（0.4.3：内存泄漏修复与诊断）。

## 1. 这个项目是什么

AutoLoud 是 Windows 上的自动响度均衡器：监听系统回录（WASAPI loopback），按 BS.1770 实时测 LUFS，用 Windows 音频会话（ISimpleAudioVolume）和主音量（IAudioEndpointVolume）调各应用的音量，把听感稳定在用户舒适的区间，同时保留作品自身的强弱层次。

- 作者：J（jinjie.shi@qmul.ac.uk），QMUL C4DM（Centre for Digital Music）自动混音研究背景。
- 仓库：https://github.com/SparrowReivun/AutoLoud ，分支 `main`，许可 CC BY-NC 4.0。
- 本机：`C:\Users\sparrow\Documents\AutoLoud`（项目），`C:\Users\sparrow\Documents\AutoLoud\config.json`（配置，随程序目录；旧位置 `%APPDATA%\AutoLoud` 只作备份/回退）。
- 宣传语：「你调一次，剩下的交给 AutoLoud。」README 有 zh / en / ja / ko / ru 五个版本，顶部有语言栏。

## 2. 核心概念与命名（文档里必须统一）

| 符号 | 名称 | 含义 |
| --- | --- | --- |
| **P** | 舒适响度（human preference loudness） | 用户偏好。首页滑块、音量键/旋钮改的就是它。内部 −90…0 LUFS，界面显示 0–100 %（线性 dB，1 % = 0.9 dB），0 % = 静音。没有校准流程：新设备继承上次的 P。 |
| **T** | 目标响度（target loudness） | 控制器此刻追的目标。固定目标模式下 T = P；源跟踪模式下 T = (P − X) + X·u。 |
| X | 目标变化范围 | 强力1 的参数（当前 11 LU）。 |
| u | 源响度相对位置 | u = clip((L_src − C_low) / (C_high − C_low), 0, 1)。 |
| L_src | 源响度 | 唯一发声应用去掉全部增益后的响度（回录 − 可见主音量 − 会话音量）。 |
| C_low / C_high | 平台 | 每个应用历史源响度的 20 / 80 百分位，按发声时间加权，半衰期 20 min；混音台可看、可重学。 |
| 总增益 | bus gain | 控制器下发的总衰减（会话衰减 + 主音量提升）。 |

**约定**：死区、吸附、稳定态触发、强力拉推子写成「相对 T」；**保护阈值相对 P**（T 在安静段会比 P 低 X，若相对 T 会把略高于 P 的音效当成突响）；描述用户偏好时用 P。旧文档里的 B 一律改成 P。界面颜色：P 标记蓝色、T 标记绿色。

## 3. 模式与预设

- 首页模式框：`仅监测（不调整）` / `默认` / `强力` / `强力1`（以及用户自建预设）。混音台里每个应用还能单独指定预设（该应用独自发声时生效）。
- **仅监测**：不动任何音量，控制器完全不运行；首页 P 滑块变成 Windows 主音量 0–100%，音量键交回 Windows；所有设置原样保留。用于对照实验。
- **强力1**（作者日常使用、新装默认）：源跟踪目标 + 平台学习 + 前奏不抬升（源低于 C_low 6 LU 以上不升）+ 新作品直接进稳定态 + 比例步长（远离 T 时每步至少补一半误差）。
- **参数以作者本机为准**。作者说「同步参数」时：读本机 `config.json` 的 `presets`，把差异写进 `autoloud/core/profiles.py` 的 `BUILTIN_PRESETS`，更新 `tests/test_source_target.py` 的预期值和 CHANGELOG。已有用户的预设不会被覆盖，只影响新装。
- 其它由本机决定的默认：启动主音量 80%、退出/崩溃主音量 40%、音量键步长 0.4 dB、默认设备同步开、推子范围 +40 dB（0.4.3 起与作者本机一致）。
- 混音台默认推子：`AppConfig.DEFAULT_APP_OFFSETS`（目前 System sounds = −40 dB），首次见到该会话即建条目。

## 4. 代码地图

```
autoloud/dsp/loudness.py      流式 BS.1770-4；门限随回录内已含增益下移（最低 −105 LUFS）；丢弃垃圾块
autoloud/core/controller.py   纯逻辑增益控制器 IDLE→ONSET→ADJUST→STEADY→SAFETY，可离线仿真
autoloud/core/platforms.py    C_low / C_high 学习（加权直方图、下移重建）
autoloud/core/engine.py       回录→测量→决策→下发；源响度回推；主导应用判定；日志轮换；监测模式
autoloud/core/profiles.py     配置、内置预设、迁移、config_dir / log_dir / mode_slug
autoloud/core/meterlog.py     meters / apps / volume csv；adjustlog.py 决策日志
autoloud/core/autostart.py    开机自启动（HKCU Run）
autoloud/backends/windows.py  PyAudioWPatch + pycaw + IPolicyConfig；所有 COM 在一个线程
autoloud/backends/mock.py     同接口模拟器（测试与 Linux 开发用）
autoloud/ui/                  PySide6：总览 / 混音台 / 设置 / 托盘；i18n.py 中英文案
tools/plot_app.py             某应用一次运行的四幅图
tools/plot_volume.py          用户操作 vs 自动增益（--list 列运行）
```

重要不变量：
- 仅监测时控制器不被调用（否则日志里出现幽灵决策、退出监测时套用陈旧增益）。
- 「唯一发声」= **主导混音**：按 峰值×会话音量 最响的应用，其它都低 10 dB 以上。会话峰值表是会话音量**之前**的值。
- 工具应用（启动时不出声的会话，如 OBS、IME）auto 关、音量 100%。OBS 开着「音频监听」会把桌面声音再放一遍，让归属失效——这是用户设置问题，活动卡片会提示。
- 控制循环的状态快照只由界面每 100 ms 轮询，不要再加跨线程信号（曾导致 4 小时后 MemoryError）。
- 测试必须隔离 Windows 的 `APPDATA` 和 Linux 的 `XDG_CONFIG_HOME`（统一在 `tests/conftest.py`），引擎后台/退出都会保存配置；只设 XDG 会覆盖 Windows 用户真实配置，造成模式/校准/平台数据丢失。日志也必须隔离。
- 主音量外部变化只能消费一次：启动停靠关闭也必须恢复旧主音量并更新读取基准；驱动写入存在量化，外部变化基准用回读 dB，请求目标另外缓存，不能把自己的写入偏差每 0.5 s 当音量键再次修改 P。

## 5. 日志与分析

- 位置：程序旁的 `logs\`（源码运行即项目目录 `logs\`；不可写时退回 `%APPDATA%\AutoLoud`）。`.gitignore` 已排除。
- 每次启动和每次切换模式新开一组：`adjust-/meters-/apps-/volume-<YYYYMMDD-HHMMSS>-<mode>.*`，mode ∈ monitor / default / strong / strong1 / 自定义 ASCII。一个文件 = 一种实验条件。
- `volume-*.csv`：用户每次音量操作（volkey / volkey_master / user_master / master_slider / p_slider / fader）及当时模式——有无 AutoLoud 的对照指标。
- `autoloud.log` 每 10 min 记一行 `memory: … MB rss, … MB private, py … blocks / … objects, … threads, commit free … / … MB, pid …`。**看 private，不看 rss**（rss 是工作集，会被 Windows 裁掉）；private 涨而 py 不涨 = 原生/COM 泄漏。private 过 1 GB 起每 +512 MB 告警一次。`AUTOLOUD_TRACEMALLOC=1` 时额外记 Python 分配增长最多的 5 行。
- 系统级证据：PowerShell `Get-WinEvent` 查 System 日志的 2004（虚拟内存不足，列出占用最多的进程）、7031（服务意外终止）、Kernel-Power 41（强制断电）。
- 画图：`.venv\Scripts\python tools\plot_volume.py --list` 挑运行，`… plot_volume.py <运行id>` 出 `volume-<id>.png`；`--titles` 换标题来源应用，`--csv-dir` 读别处。

## 6. 文档约定

- `README.md` 是源头，改动后同步到 `README.en/ja/ko/ru.md`（同一段落、同一位置）。
- `CHANGELOG.md`：最上面是待发布版本节；每条写清现象→原因→做法，必要时带日志时间点。
- `docs/RELEASING.md`：发布 = 提交 `Release x.y.z` + 打 `vx.y.z` 标签推送，Actions 自动测试、PyInstaller 目录版打包、建 Release 附 `AutoLoud-windows.zip`；之后向 Microsoft 等报误报。
- Claude 文档「AutoLoud 规则与数值说明」（claude.ai 上）是规则的长文版，更新慢；以代码和 CHANGELOG 为准。
- 首页文案：不要用警示图标或橙色强调普通说明（作者明确要求）；默认中文优先，英文同步。
- 版本号：修 bug 加修订号，加功能加次版本号。

## 7. 协作流程（AI 助手工作方式）

1. 云端工作副本 `/home/claude/auto-loudness` 改代码，`pytest`（目前 45 个测试）全过。
2. 通过设备桥把改动文件写回本机项目目录，**写回后用 md5sum 核对**（挂载偶尔会落下旧版本）。
3. 本机 `git add … && git commit`（作者身份 sparrow / jinjie.shi@qmul.ac.uk）；需要时重打 `vX.Y.Z` 标签。作者自己 push（助手没有凭据）。
4. git 会留下 `.lock` / `tmp_obj_*`，删除前需要作者批准该目录的删除权限（每次重新连接后要再批一次）。
5. `.github/workflows/build.yml` 是受保护文件，只能在本机 shell 里用脚本改。

## 8. 已踩过的坑（别再踩）

- 回录门限固定 −70 LUFS → 深度压低时正常内容被当静音，增益升不回去（已改为随增益下移）。
- 会话音量对象按进程缓存 → 切设备/蓝牙重连后操控死对象（已按会话实例缓存并校验漂移）。
- 新打开的回录流前几块是垃圾（1e30）→ 读数 −2000 LUFS、总线切到 −116（已丢弃并重置）。
- 退出/切设备爆音：顺序必须是「先把变轻的那一步做掉」——主音量先降，再把会话放回 100%。
- 全屏反作弊游戏里键盘钩子看不到音量键，Windows 自己动主音量 → 把主音量位移换算回按键，再把主音量停回 80%。
- 安静前奏被抬升后正片过载 → 前奏规则；新作品直接进稳定态。
- svchost / audiodg / 系统声音不算发声应用。
- 微信通知在音乐后面触发「新作品」把总线拉走 → 只有主导应用的新作品才重定向。
- pycaw 里 `[out] LPWSTR` 的方法（GetSessionInstanceIdentifier / GetId / GetDisplayName / GetIconPath / GetSessionIdentifier）经 comtypes 调用**不会 CoTaskMemFree**，每次调用原生泄漏一个字符串 → 一律用 `windows._com_str(obj, 接口类, "方法名")`。曾在约 7 小时内漏到 1.9 GB，耗尽系统提交内存，Windows Audio 崩溃、设备全部消失、关机卡死（11-03 19:24、11-04 18:04）。

## 9. 待办 / 开放问题

- 0.4.3 Release：推 `v0.4.3` 标签后看 Actions 是否完整打包（0.4.2 起的目录版）。
- 启动提交量（2026-11-05 实测）：本机仅导入 NumPy + SciPy 即从约 9 MiB private 升至 1293 MiB；导入前设 `OPENBLAS_NUM_THREADS=1` 后约 71 MiB。`autoloud/__init__.py` 现在默认设置单线程，保留显式环境变量；必须在首次导入数值库前生效。这解释了启动即约 1.4 GiB 的大额提交，不能把高 private 快照直接认定为持续泄漏。
- 会话轮询仍有原生增长（2026-11-05 实测）：修复字符串释放后，长期缓存的 `IAudioSessionManager2` 每 10,000 次枚举仍保留约 30 MiB。现在每轮创建并释放 manager，连续批次占用保持约 23 MiB；会话音量/峰值接口仍按实例缓存。pycaw 把 `Activate` 返回值声明成 `IUnknown`，转换接口用 `QueryInterface` 获取独立引用，避免裸 cast 导致所有权不清。
- 慢泄漏：0.4.3 修了 LPWSTR 字符串泄漏（见第 8 节）。仍需看新版 `memory:` 行的 private 趋势；若仍涨，看 py 是否同涨（Python 侧 → 开 AUTOLOUD_TRACEMALLOC）还是只有 private 涨（再查 winsdk 媒体会话、PortAudio）。`tools/com_leak_check.py` 可对比新旧路径。
- 对照实验设计：每位受试者每种条件各开一次（或切一次模式），用 `plot_volume.py` 对比操作次数与幅度。
