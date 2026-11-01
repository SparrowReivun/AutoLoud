"""Plot one app's history from a run's measurement logs.

    python tools/plot_app.py ZenlessZoneZero.exe                 # latest run
    python tools/plot_app.py ZenlessZoneZero.exe 20261101-141026  # a specific run
    python tools/plot_app.py ZenlessZoneZero.exe --csv-dir "C:\\Users\\me\\AppData\\Roaming\\AutoLoud"

Reads apps-<ts>.csv and meters-<ts>.csv from the AutoLoud config folder and
writes <app>-<ts>.png into the current folder, three panels over the same time axis:

  1. the app's own peak meter (Windows IAudioMeterInformation, as dBFS) and
     the session volume AutoLoud gave it;
  2. the bus loudness (momentary 400 ms / short-term 3 s / block integrated,
     perceived = loopback + master boost) with B; the stretches where this app
     was the ONLY app sounding are shaded - only there is the bus loudness
     really this app's loudness;
  3. the app's own loudness with every gain taken out ("source", what it would
     measure at 100 % session volume and the reference master) - only for the
     stretches where it sounded alone, derived from the bus loudness and the
     session volume (or read from the src_ columns of newer logs);
  4. the bus gain and the app's fader.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def config_dir() -> Path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from autoloud.core.profiles import config_dir as _cd
    return _cd()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app", help="process name as shown in the mixer, e.g. ZenlessZoneZero.exe")
    ap.add_argument("run", nargs="?", help="run timestamp YYYYMMDD-HHMMSS (default: latest)")
    ap.add_argument("--csv-dir", help="folder with the meters-/apps-*.csv files (default: config folder)")
    ap.add_argument("--out", help="output PNG (default: <app>-<run>.png in the current folder)")
    a = ap.parse_args()

    folder = Path(a.csv_dir) if a.csv_dir else config_dir()
    runs = sorted(p.name[len("apps-"):-4] for p in folder.glob("apps-*.csv"))
    if not runs:
        print("no apps-*.csv in", folder); return 1
    run = a.run or runs[-1]
    apps = pd.read_csv(folder / f"apps-{run}.csv", parse_dates=["time"])
    meters = pd.read_csv(folder / f"meters-{run}.csv", parse_dates=["time"])
    mine = apps[apps["app"] == a.app].copy()
    if mine.empty:
        print(f"{a.app} never sounded in run {run}; apps seen:", ", ".join(sorted(apps["app"].unique()))); return 1

    # dBFS from the 0..1 peak meter; silence -> NaN so it does not draw a floor line
    mine["peak_dbfs"] = mine["peak"].apply(lambda p: 20 * math.log10(p) if p > 1e-4 else float("nan"))
    # stretches where this app was the only one sounding (peak > 0.01)
    snd = apps[apps["peak"] > 0.01].groupby("time")["app"].apply(set)
    alone_times = [t for t, s in snd.items() if s == {a.app}]
    # perceived bus loudness = loopback + master boost (the loopback usually does not contain the master)
    boost = meters["master_boost_db"].fillna(0.0)
    for col in ("momentary_lufs", "short_term_lufs", "integrated_lufs"):
        meters[col + "_p"] = meters[col].where(meters[col] > -90) + boost

    # the app's own loudness: bus loudness (raw loopback) minus its session volume in dB,
    # valid only while it sounds alone. Newer logs carry it as src_short_lufs / src_momentary_lufs.
    alone = mine[mine["time"].isin(alone_times)].copy()
    if "src_short_lufs" in alone.columns and alone["src_short_lufs"].notna().any():
        alone["src_short"] = alone["src_short_lufs"]
        alone["src_mom"] = alone.get("src_momentary_lufs")
    else:
        m_idx = meters.drop_duplicates("time").set_index("time").sort_index()
        near = m_idx.reindex(alone["time"], method="nearest", tolerance=pd.Timedelta(seconds=0.3))
        vol_db = alone["volume"].clip(lower=1e-6).apply(lambda v: 20 * math.log10(v)).values
        alone["src_short"] = near["short_term_lufs"].where(near["short_term_lufs"] > -90).values - vol_db
        alone["src_mom"] = near["momentary_lufs"].where(near["momentary_lufs"] > -90).values - vol_db

    fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(14, 13), sharex=True,
                                             gridspec_kw={"height_ratios": [3, 3, 3, 2]})
    fig.suptitle(f"{a.app} — run {run}")

    ax1.plot(mine["time"], mine["peak_dbfs"], ".", ms=3, label="app peak meter (dBFS)")
    ax1b = ax1.twinx()
    ax1b.plot(mine["time"], mine["volume"] * 100, color="tab:orange", lw=1, label="session volume (%)")
    ax1b.set_ylim(0, 105); ax1b.set_ylabel("session volume %")
    ax1.set_ylabel("dBFS"); ax1.set_ylim(-70, 0); ax1.grid(alpha=.3)
    ax1.legend(loc="upper left"); ax1b.legend(loc="upper right")

    ax2.plot(meters["time"], meters["momentary_lufs_p"], lw=.6, alpha=.6, label="momentary 400 ms")
    ax2.plot(meters["time"], meters["short_term_lufs_p"], lw=1, label="short-term 3 s")
    ax2.plot(meters["time"], meters["integrated_lufs_p"], lw=1, label="block integrated")
    ax2.plot(meters["time"], meters["b_lufs"], "k--", lw=1, label="B")
    for t in alone_times:
        ax2.axvspan(t, t + pd.Timedelta(seconds=0.5), color="tab:green", alpha=.08, lw=0)
    ax2.set_ylabel("LUFS (perceived)"); ax2.set_ylim(-70, 0); ax2.grid(alpha=.3)
    ax2.legend(loc="upper left", ncol=4, fontsize=8)
    ax2.text(0.01, 0.03, "green = this app was the only one sounding (bus loudness == its loudness)",
             transform=ax2.transAxes, fontsize=8, color="tab:green")

    ax3.plot(alone["time"], alone["src_mom"], ".", ms=2, alpha=.5, color="tab:blue", label="source momentary 400 ms")
    ax3.plot(alone["time"], alone["src_short"], ".", ms=3, color="tab:green", label="source short-term 3 s")
    if "b_lufs" in meters:
        ax3.plot(meters["time"], meters["b_lufs"], "k--", lw=1, label="B")
    ax3.set_ylabel("LUFS (app itself, gains out)"); ax3.set_ylim(-70, 0); ax3.grid(alpha=.3)
    ax3.legend(loc="upper left", ncol=3, fontsize=8)
    ax3.text(0.01, 0.03, "the app's own loudness at 100 % session volume / reference master; "
             "only where it sounded alone", transform=ax3.transAxes, fontsize=8, color="tab:green")

    ax4.plot(meters["time"], meters["gain_total_db"], lw=1, label="bus gain (dB)")
    ax4.plot(mine["time"], mine["fader_db"], color="tab:red", lw=1, label="app fader (dB)")
    ax4.plot(mine["time"], mine["volume"].clip(lower=1e-6).apply(lambda v: 20 * math.log10(v)),
             color="tab:orange", lw=1, label="session volume (dB)")
    ax4.set_ylabel("dB"); ax4.grid(alpha=.3); ax4.legend(loc="upper left", ncol=3, fontsize=8)
    ax4.set_xlabel("time")

    import matplotlib.dates as mdates
    ax4.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    fig.autofmt_xdate(); fig.tight_layout()
    out = Path(a.out) if a.out else Path.cwd() / f"{a.app}-{run}.png"
    fig.savefig(out, dpi=120)
    print("wrote", out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
