"""Plot one run's volume activity: what the USER did and what AUTOLOUD did.

    python tools/plot_volume.py                      # latest run
    python tools/plot_volume.py 20261103-121622      # a specific run
    python tools/plot_volume.py --titles chrome.exe  # which app's window titles to print under the axis

Reads volume-<ts>.csv, meters-<ts>.csv and adjust-<ts>.log from the AutoLoud
logs folder and writes volume-<ts>.png into the current folder. Two panels
over the same time axis, both in dB:

  1. the user's own volume moves - in monitor mode the Windows master (dB) with
     every key press / knob turn (red) and dashboard-slider move (green); in an
     automatic preset the comfort level P (LUFS, orange) with every P-slider move,
     volume-key press and fader move marked;
  2. AutoLoud's own work - the total automatic gain (purple) and the target T
     (green dashed); flat in monitor mode, where it touches nothing.

Automatic presets (强力1 etc.) are shaded; monitor mode is white. Under the
axis the window titles the chosen app showed (as the adjust log records them),
so each burst of moves can be matched to the track that was playing.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from matplotlib import font_manager

USER_KINDS = {               # kind in volume-<ts>.csv -> (label, colour, marker)
    "user_master":   ("音量键 / 旋钮（仅监测，Windows 主音量）", "#d62728", "v"),
    "master_slider": ("主音量滑块（仅监测）", "#2ca02c", "o"),
    "p_slider":      ("P 滑块", "#ff7f0e", "s"),
    "volkey":        ("音量键 / 旋钮 → P", "#9467bd", "^"),
    "volkey_master": ("音量键（全屏游戏里）→ P", "#8c564b", "^"),
    "fader":         ("应用推子", "#17becf", "D"),
}


def _cjk_font() -> None:
    for f in font_manager.findSystemFonts():
        n = Path(f).name.lower()
        if "cjk" in n or "msyh" in n or "simhei" in n or "yahei" in n:
            font_manager.fontManager.addfont(f)
    plt.rcParams["font.family"] = ["Microsoft YaHei", "Noto Sans CJK SC", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def config_dir() -> Path:
    """The logs folder (logs/ next to the program; older runs may be in the config folder)."""
    root = Path(__file__).resolve().parents[1]
    d = Path(os.environ.get("AUTOLOUD_LOG_DIR") or root / "logs")
    if any(d.glob("meters-*.csv")):
        return d
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) if sys.platform.startswith("win") \
        else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "AutoLoud"


def titles_from_log(path: Path, app: str, day: str, t_end: pd.Timestamp, min_s: float = 3.0):
    """[(start, end, title)] for ``app`` from the ``[app (title)]`` suffix of each adjust-log line."""
    pat = re.compile(r"^(\d\d:\d\d:\d\d\.\d).*" + re.escape(app) + r" \((.*?)\)[,\]]")
    raw = []
    for line in path.open(encoding="utf-8", errors="replace"):
        m = pat.match(line)
        if m:
            t, ti = pd.Timestamp(day + " " + m.group(1)), m.group(2)
            if not raw or raw[-1][1] != ti:
                raw.append((t, ti))
    segs = []
    for i, (t, ti) in enumerate(raw):                      # drop title flickers shorter than min_s
        end = raw[i + 1][0] if i + 1 < len(raw) else t_end
        if segs and ((end - t).total_seconds() < min_s or segs[-1][1] == ti):
            continue
        segs.append([t, ti])
    return [(s[0], e, s[1]) for s, e in zip(segs, [x[0] for x in segs[1:]] + [t_end])]


def short_title(ti: str, n: int = 22) -> str:
    ti = re.sub(r"_哔哩哔哩_bilibili$|_游戏热门视频$|_剧情$|（媒体信息）$", "", ti)
    ti = re.sub(r"^《(.*?)》", r"\1·", ti)
    return ti[:n] + ("…" if len(ti) > n else "")


def tight_ylim(series: pd.Series, lo_pct: float = 2.0, hi_pct: float = 99.5, min_span: float = 6.0):
    s = series.dropna()
    if s.empty:
        return (-30.0, 0.0)
    lo, hi = np.percentile(s, lo_pct), np.percentile(s, hi_pct)
    if hi - lo < min_span:
        mid = (hi + lo) / 2; lo, hi = mid - min_span / 2, mid + min_span / 2
    pad = (hi - lo) * 0.05
    return (lo - pad, hi + pad)


def mark_clipped(ax, t, y, floor, colour="#d62728"):
    """Values under the axis floor: a bar on the bottom edge with the minimum."""
    below = pd.DataFrame({"t": t, "y": y})[y < floor]
    if below.empty:
        return
    runs = (below["t"].diff() > pd.Timedelta("5s")).cumsum()
    for _, g in below.groupby(runs):
        val = g["y"].min()
        ax.plot(g["t"], np.full(len(g), floor), color=colour, lw=3, solid_capstyle="butt", zorder=4)
        ax.annotate(f"↓ 低至 {val:.0f} dB" + ("（静音）" if val < -60 else ""), (g["t"].iloc[0], floor),
                    xytext=(6, 3), textcoords="offset points", fontsize=8, color=colour, va="bottom")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", nargs="?", help="run timestamp YYYYMMDD-HHMMSS (default: latest)")
    ap.add_argument("--csv-dir", help="folder with the logs (default: logs folder)")
    ap.add_argument("--titles", default="chrome.exe", help="app whose window titles go under the axis")
    ap.add_argument("--out", help="output PNG (default: volume-<run>.png in the current folder)")
    ap.add_argument("--list", action="store_true", help="list the runs in the logs folder and exit")
    a = ap.parse_args()
    _cjk_font()

    folder = Path(a.csv_dir) if a.csv_dir else config_dir()
    runs = sorted(p.name[len("volume-"):-4] for p in folder.glob("volume-*.csv"))
    if not runs:
        print("no volume-*.csv in", folder); return 1
    if a.list:
        print(f"runs in {folder} (newest last):")
        for r in runs:
            v = folder / f"volume-{r}.csv"
            n = max(0, sum(1 for _ in v.open(encoding="utf-8")) - 1)
            m = folder / f"meters-{r}.csv"
            mins = 0.0
            if m.exists() and m.stat().st_size > 200:        # last row's time - the run's start
                with m.open("rb") as fh:
                    fh.seek(-200, 2); last = fh.read().decode("utf-8", "replace").strip().splitlines()[-1]
                try:
                    end = pd.Timestamp(last.split(",")[0]); start = pd.Timestamp(r[:4] + "-" + r[4:6] + "-" + r[6:8] + " " + r[9:11] + ":" + r[11:13] + ":" + r[13:15])
                    mins = (end - start).total_seconds() / 60
                except Exception:
                    pass
            print(f"  {r:32s}  {n:4d} user moves   {max(mins, 0):5.0f} min")
        return 0
    run = a.run or runs[-1]
    vol = pd.read_csv(folder / f"volume-{run}.csv", parse_dates=["time"]).sort_values("time")
    met = pd.read_csv(folder / f"meters-{run}.csv", parse_dates=["time"],
                      usecols=["time", "gain_total_db", "phase", "b_lufs", "master_db", "target_lufs"]).sort_values("time")
    if met.empty:
        print("empty meters file"); return 1
    t0, t1 = met["time"].iloc[0], met["time"].iloc[-1]
    day = t0.strftime("%Y-%m-%d")

    # ---- mode segments: monitor (white) vs automatic presets (shaded) ---------------------
    # the volume log carries the mode at each user action; between actions the mode is the
    # last one seen. Automatic gain is also visible in meters (phase != idle in monitor mode
    # never moves the gain), so the volume log is the authority.
    modes = vol[["time", "mode"]].copy() if not vol.empty else pd.DataFrame({"time": [t0], "mode": ["monitor"]})
    if modes["time"].iloc[0] > t0:
        modes = pd.concat([pd.DataFrame({"time": [t0], "mode": [modes["mode"].iloc[0]]}), modes])
    modes = modes[modes["mode"] != modes["mode"].shift()].reset_index(drop=True)
    mode_segs = [(modes["time"][i], modes["time"][i + 1] if i + 1 < len(modes) else t1, modes["mode"][i])
                 for i in range(len(modes))]

    has_auto = any(m != "monitor" for _, _, m in mode_segs)
    if has_auto:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(22, 11), dpi=150, sharex=True,
                                       gridspec_kw={"height_ratios": [3, 2], "hspace": 0.08})
    else:                                   # monitor only all through: nothing automatic to draw
        fig, ax1 = plt.subplots(1, 1, figsize=(22, 8), dpi=150)
        ax2 = ax1
    for ax in {ax1, ax2}:
        for s, e, mode in mode_segs:
            if mode != "monitor":
                ax.axvspan(s, e, color="#ffe6a8", alpha=0.55, lw=0, zorder=0)
        ax.grid(alpha=0.3)
    auto_label = "自动预设开启（" + "、".join(sorted({m for _, _, m in mode_segs if m != "monitor"})) + "）" \
        if any(m != "monitor" for _, _, m in mode_segs) else None

    # ---- panel 1: the user's moves ----------------------------------------------------------
    in_auto = pd.Series(False, index=met.index)
    for s, e, mode in mode_segs:
        if mode != "monitor":
            in_auto |= (met["time"] >= s) & (met["time"] <= e)
    master_mon = met["master_db"].where(~in_auto)
    p_auto = met["b_lufs"].where(in_auto)
    level = master_mon.fillna(p_auto)                     # the user-controlled level, whichever it is
    lo, hi = tight_ylim(level)
    ax1.plot(met["time"], master_mon.clip(lower=lo), color="#1f4e79", lw=1.2, label="Windows 主音量（仅监测，dB）")
    ax1.plot(met["time"], p_auto.clip(lower=lo), color="#ff7f0e", lw=1.6, label="舒适响度 P（自动预设，LUFS）")
    mark_clipped(ax1, met["time"], level, lo)
    lv = pd.merge_asof(vol, met[["time", "master_db", "b_lufs"]], on="time", direction="nearest") if not vol.empty else vol
    for kind, (lab, col, mk) in USER_KINDS.items():
        d = lv[lv["kind"] == kind] if not lv.empty else lv
        if d.empty:
            continue
        y = d["master_db"] if kind in ("user_master", "master_slider") else d["b_lufs"]
        ax1.scatter(d["time"], y.clip(lower=lo), s=24, c=col, marker=mk, zorder=5, label=f"{lab}（{len(d)} 次）")
    ax1.set_ylim(lo, hi)
    ax1.set_ylabel("用户控制的电平 (dB)")
    n_user = len(vol)
    ax1.set_title(f"{day} {t0:%H:%M}–{t1:%H:%M}  用户音量操作 {n_user} 次 与 AutoLoud 自动增益"
                  + (f"  ·  {auto_label}" if auto_label else "  ·  全程仅监测"), fontsize=12)
    h, l = ax1.get_legend_handles_labels()
    if auto_label:
        h.append(Rectangle((0, 0), 1, 1, fc="#ffe6a8")); l.append(auto_label)
    ax1.legend(h, l, loc="upper left", fontsize=8.5, framealpha=0.92, ncol=2)

    # ---- panel 2: AutoLoud's automatic gain --------------------------------------------------
    gain = met["gain_total_db"].where(in_auto)
    if not has_auto:
        gain = None
    tgt = met["target_lufs"].where(in_auto) if ("target_lufs" in met and has_auto) else None
    if has_auto:
        glo, ghi = tight_ylim(gain, 1.0, 99.5)
        ax2.plot(met["time"], gain.clip(lower=glo), color="#7b3fa0", lw=1.3, label="AutoLoud 自动总增益（dB）")
        mark_clipped(ax2, met["time"], gain, glo, colour="#7b3fa0")
        if (~in_auto).mean() > 0.01:
            ax2.text(0.99, 0.95, "仅监测时段：AutoLoud 不动任何音量，故无增益曲线", transform=ax2.transAxes,
                     ha="right", va="top", fontsize=8.5, color="grey")
        ax2.set_ylim(glo, ghi)
        ax2.set_ylabel("自动增益 (dB)")
    if not has_auto:
        pass
    elif tgt is not None and tgt.notna().any():
        axt = ax2.twinx()
        axt.plot(met["time"], tgt, color="#2ca02c", lw=1.0, ls="--", label="目标 T（LUFS，右轴）")
        axt.set_ylim(*tight_ylim(tgt, 1.0, 99.0))
        axt.set_ylabel("目标 T (LUFS)", color="#2ca02c")
        h2, l2 = ax2.get_legend_handles_labels(); h3, l3 = axt.get_legend_handles_labels()
        ax2.legend(h2 + h3, l2 + l3, loc="upper left", fontsize=8.5, framealpha=0.92)
    else:
        ax2.legend(loc="upper left", fontsize=8.5, framealpha=0.92)
    ax2.set_xlim(t0, t1)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax2.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))

    # ---- window titles under the axis -------------------------------------------------------
    log_path = folder / f"adjust-{run}.log"
    segs = titles_from_log(log_path, a.titles, day, t1) if log_path.exists() else []
    colours = ["#cfe2f3", "#d9ead3", "#fce5cd", "#ead1dc", "#fff2cc", "#d0e0e3"]
    trans = ax2.get_xaxis_transform()
    for i, (s, e, ti) in enumerate(segs):
        ax2.add_patch(Rectangle((mdates.date2num(s), -0.03), mdates.date2num(e) - mdates.date2num(s), 0.03,
                                transform=trans, clip_on=False, lw=0.8, ec="white", fc=colours[i % len(colours)]))
        for ax in (ax1, ax2):
            ax.axvline(s, color="grey", lw=0.5, alpha=0.6, ls=":")
        if (e - s).total_seconds() >= 25:
            ax2.text(s + (e - s) / 2, -0.045, short_title(ti), rotation=60, ha="right", va="top",
                     fontsize=7.5, transform=trans, clip_on=False)
    ax2.tick_params(axis="x", pad=12)
    if segs:
        ax2.text(0.0, -0.045, f"{a.titles} 标题 →", transform=ax2.transAxes, ha="right", va="top", fontsize=7.5, color="grey")

    fig.subplots_adjust(bottom=0.3 if has_auto else 0.4, top=0.95, left=0.05, right=0.96)
    out = Path(a.out) if a.out else Path.cwd() / f"volume-{run}.png"
    fig.savefig(out)
    print("wrote", out, "|", n_user, "user moves |", len(segs), "title segments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
