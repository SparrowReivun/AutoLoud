from __future__ import annotations

import logging
import os
import sys

from PySide6.QtWidgets import QApplication

from autoloud.backends.base import make_backend
from autoloud.core.engine import Engine
from autoloud.core.profiles import AppConfig, config_dir, log_dir


_ENGINE: dict = {}      # the running engine, for the crash handlers


def _install_crash_handlers() -> None:
    """Native crashes -> crash.log (faulthandler); Python exceptions -> log + dialog."""
    import faulthandler
    import traceback
    try:
        _crash_file = open(log_dir() / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except Exception:
        pass

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.getLogger("autoloud").error("Unhandled exception:\n%s", text)
        eng = _ENGINE.get("engine")
        if eng is not None:
            eng.emergency_exit()                  # never leave the master parked high
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "AutoLoud - error", text[-1500:])
        except Exception:
            pass
    sys.excepthook = hook


def main(argv=None) -> int:
    _install_crash_handlers()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_dir() / "autoloud.log", encoding="utf-8"),
                  logging.StreamHandler()],
    )
    argv = list(sys.argv if argv is None else argv)
    if "--mock" in argv:
        os.environ["AUTOLOUD_MOCK"] = "1"
        argv.remove("--mock")

    logging.getLogger("autoloud").info("AutoLoud starting; frozen=%s", getattr(sys, "frozen", False))
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("AutoLoud")

    cfg = AppConfig.load()
    backend = make_backend()
    engine = Engine(backend, cfg)

    # Demo sources for the simulator: a tone "app" so the meters move.
    if backend.name == "simulator":
        try:
            backend.add_tone_source("music-player.exe", lufs_amp=0.08, freq=220.0)
        except Exception:
            pass

    from autoloud.ui.main_window import MainWindow, DashboardPage
    win = MainWindow(engine)
    _ENGINE["engine"] = engine
    import atexit
    atexit.register(lambda: engine.emergency_exit() if engine._running else None)
    # every decision, in the dashboard's words, into adjust-<timestamp>.log
    from autoloud.core.adjustlog import AdjustLog
    adjust_log = AdjustLog(log_dir(), DashboardPage._fmt_event, engine.dashboard_mode_slug())
    engine.on_event = adjust_log
    engine.adjust_log = adjust_log
    from autoloud.core.meterlog import MeterLog
    engine.meter_log = MeterLog(log_dir(), engine.dashboard_mode_slug())   # raw measurements, meters-/apps-/volume-<ts>-<mode>.csv
    logging.getLogger("autoloud").info("logs in %s", log_dir())
    win.start()
    # volume keys / knob move B instead of the Windows master volume (Windows only)
    from autoloud.backends.volumekeys_windows import VolumeKeys
    # in monitor-only mode the keys are left to Windows (so the user's own moves are counted as such)
    volkeys = VolumeKeys(lambda d: engine.nudge_b(d * cfg.volume_key_step_db),
                         lambda: cfg.volume_keys and not cfg.monitor_only)
    volkeys.start()
    if not cfg.start_minimized:
        win.show()
    # CI smoke test hook: exit cleanly after N seconds
    exit_after = os.environ.get("AUTOLOUD_EXIT_AFTER")
    if exit_after:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(int(float(exit_after) * 1000), win.quit)
    rc = app.exec()
    volkeys.stop()
    engine.stop()
    adjust_log.close()
    engine.meter_log.close()
    try:
        backend.close()
    except Exception:
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
