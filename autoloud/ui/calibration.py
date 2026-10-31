"""Calibration wizard: beep → A, music → B."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtWidgets import (QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QComboBox, QMessageBox)

from autoloud.core.engine import Engine
from autoloud.core.signals import harsh_beep, list_genres, load_audio, fallback_music
from autoloud.ui.i18n import t
from autoloud.ui.widgets import DbFader, LoudnessBar


class _PlayPage(QWizardPage):
    """Shared machinery: fader + play/stop + live meter."""

    def __init__(self, engine: Engine, title: str, text: str, fader_key: str):
        super().__init__()
        self.engine = engine
        self.fader_key = fader_key
        self.player = None
        self.measured = None
        self.setTitle(title)
        lay = QVBoxLayout(self)
        desc = QLabel(text)
        desc.setWordWrap(True)
        desc.setObjectName("muted")
        lay.addWidget(desc)
        self.extra = QVBoxLayout()
        lay.addLayout(self.extra)

        row = QHBoxLayout()
        self.fader = DbFader(-60, 0)
        row.addWidget(self.fader, 0, Qt.AlignHCenter)
        right = QVBoxLayout()
        self.meter = LoudnessBar(t("measured"))
        right.addWidget(self.meter)
        self.readout = QLabel("—")
        self.readout.setObjectName("big")
        self.readout.setAlignment(Qt.AlignCenter)
        right.addWidget(self.readout)
        self.btn = QPushButton(t("play"))
        self.btn.setObjectName("primary")
        self.btn.setCheckable(True)
        self.btn.toggled.connect(self._toggle)
        right.addWidget(self.btn)
        right.addStretch(1)
        row.addLayout(right, 1)
        lay.addLayout(row, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)

    # audio to play (override)
    def audio(self):
        raise NotImplementedError

    def initializePage(self):
        prof = self.engine.profile
        self.fader.set_db(-60 + 60 * getattr(prof, self.fader_key, 0.3))
        self.timer.start()

    def cleanupPage(self):
        self._stop()
        self.timer.stop()

    def _toggle(self, on: bool):
        if on:
            try:
                audio, fs = self.audio()
                self.player = self.engine.play(audio, fs, self.fader.scalar, loop=True)
            except Exception as e:
                logging.getLogger(__name__).exception("calibration playback failed")
                self._stop()
                QMessageBox.critical(self, t("error"), f"{type(e).__name__}: {e}")
                return
            self.btn.setText(t("stop"))
        else:
            self._stop()

    def _stop(self):
        if self.player is not None:
            self.player.stop()
            self.player = None
        self.btn.blockSignals(True)
        self.btn.setChecked(False)
        self.btn.blockSignals(False)
        self.btn.setText(t("play"))

    def _tick(self):
        v = self.engine.measured_lufs()
        self.meter.set_value(v)
        if self.player is not None and v > -70:
            self.measured = v
            self.readout.setText(f"{v:.1f} LUFS")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self.measured is not None

    def validatePage(self) -> bool:
        if self.measured is None:
            return False
        setattr(self.engine.profile, self.fader_key, (self.fader.db() + 60) / 60)
        self._stop()
        return True


class BeepPage(_PlayPage):
    def __init__(self, engine):
        super().__init__(engine, t("cal.beep.h"), t("cal.beep"), "beep_fader")

    def audio(self):
        fs = self.engine.device.fs if self.engine.device else 48000
        return harsh_beep(fs), fs

    def validatePage(self):
        if self.measured is None:
            return False
        a = float(self.measured)
        # Probe how the master volume relates to the loopback tap while the
        # beep is still playing (keeps the fader as the user left it).
        if self.player is not None:
            self.readout.setText("…")
            from PySide6.QtCore import QEventLoop, QTimer as _QT
            from PySide6.QtWidgets import QApplication
            def wait(sec):
                loop = QEventLoop(); _QT.singleShot(int(sec * 1000), loop.quit); loop.exec()
            self.setEnabled(False)
            try:
                vis = self.engine.probe_master_visibility(wait)
            finally:
                self.setEnabled(True)
            if vis is not None:
                self.engine.profile.master_visible_in_loopback = vis
            QApplication.processEvents()
        if not super().validatePage():
            return False
        self.engine.profile.a_lufs = a
        return True


class _Decoder(QThread):
    """Decode + resample a file off the GUI thread."""
    done = Signal(object, int, str)   # audio, fs, error

    def __init__(self, path: str, fs: int):
        super().__init__()
        self.path, self.fs = path, fs

    def run(self):
        try:
            self.done.emit(load_audio(self.path, self.fs), self.fs, "")
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, self.fs, f"{type(e).__name__}: {e}")


class MusicPage(_PlayPage):
    """Step 2: comfort level B.

    Source is either a bundled/user track played by us, or any application
    that is currently producing sound (Spotify, a browser tab...). For an
    external app the fader drives that app's session volume, so the
    measurement stays in the same loopback domain as everything else.
    """
    EXTERNAL = "external"

    def __init__(self, engine, music_dir: str):
        super().__init__(engine, t("cal.music.h"), t("cal.music"), "music_fader")
        self.genres = list_genres(music_dir)
        if music_dir:
            from autoloud.core.signals import default_music_dir
            for g, files in list_genres(default_music_dir()).items():
                self.genres.setdefault(g, files)
        self._cache: dict[str, tuple] = {}
        self._decoder: _Decoder | None = None
        self._ext_key: str | None = None        # session key when an app is the source
        self._ext_touched: set[str] = set()

        row = QHBoxLayout()
        row.addWidget(QLabel(t("cal.source")))
        self.source_box = QComboBox()
        row.addWidget(self.source_box, 3)
        self.extra.addLayout(row)
        hint = QLabel(t("cal.source.hint"))
        hint.setWordWrap(True); hint.setObjectName("muted")
        self.extra.addWidget(hint)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.extra.addWidget(self.status)

        self._rebuild_sources()
        self.source_box.currentIndexChanged.connect(self._source_changed)
        self._src_timer = QTimer(self); self._src_timer.setInterval(1000)
        self._src_timer.timeout.connect(self._rebuild_sources)

    # ---- source list -----------------------------------------------------
    def _rebuild_sources(self):
        cur = self.source_box.currentData()
        items: list[tuple[str, object]] = []
        for g, files in self.genres.items():
            for p in files:
                items.append((f"{t('cal.builtin')} \u00b7 {g} / {p.name}", str(p)))
        if not items:
            items.append((f"{t('cal.builtin')} \u00b7 synthetic", ""))
        for sess in self.engine.state.sessions:
            if sess.active and not sess.is_self:
                shown = self.engine.config.display_name(sess.name)
                label = shown if shown == sess.name else f"{shown} ({sess.name})"
                items.append((f"{t('cal.app')} \u00b7 {label}", (self.EXTERNAL, sess.key, sess.name)))
        existing = [self.source_box.itemData(i) for i in range(self.source_box.count())]
        if [d for _l, d in items] == existing:
            return
        self.source_box.blockSignals(True)
        self.source_box.clear()
        for label, data in items:
            self.source_box.addItem(label, data)
        i = self.source_box.findData(cur) if cur is not None else -1
        self.source_box.setCurrentIndex(max(0, i))
        self.source_box.blockSignals(False)
        if i < 0 and cur is not None:
            self._source_changed(0)

    def _is_external(self) -> bool:
        d = self.source_box.currentData()
        return isinstance(d, tuple) and d[0] == self.EXTERNAL

    def _source_changed(self, _i):
        was_playing = self.player is not None
        self._stop()
        self._release_external()
        if self._is_external():
            _k, key, _name = self.source_box.currentData()
            self._ext_key = key
            self.btn.hide()
            self.status.setText(t("cal.ext.active"))
            self._apply_ext_volume()
        else:
            self._ext_key = None
            self.btn.show()
            self.status.setText("")
            if was_playing:
                self.btn.setChecked(True)

    def _release_external(self):
        for key in self._ext_touched:
            try:
                self.engine.backend.set_session_volume(self.engine.device, key, 1.0)
            except Exception:
                pass
        self._ext_touched.clear()

    def _apply_ext_volume(self):
        if self._ext_key is None:
            return
        try:
            self.engine.backend.set_session_volume(self.engine.device, self._ext_key, self.fader.scalar())
            self._ext_touched.add(self._ext_key)
        except Exception:
            logging.getLogger(__name__).exception("external session volume")

    # ---- page lifecycle ----------------------------------------------------
    def initializePage(self):
        super().initializePage()
        self.fader.valueChanged.connect(self._apply_ext_volume)
        self._src_timer.start()
        self._rebuild_sources()
        self._source_changed(self.source_box.currentIndex())

    def cleanupPage(self):
        super().cleanupPage()
        self._src_timer.stop()
        try:
            self.fader.valueChanged.disconnect(self._apply_ext_volume)
        except Exception:
            pass
        self._release_external()

    # ---- playback (built-in sources) ------------------------------------------
    def _toggle(self, on: bool):
        if not on:
            self._stop(); return
        fs = self.engine.device.fs if self.engine.device else 48000
        path = self.source_box.currentData()
        if not path:
            audio = fallback_music(fs)
            self._start(audio, fs); return
        if path in self._cache:
            self._start(*self._cache[path]); return
        self.btn.setEnabled(False)
        self.status.setText(t("cal.decoding"))
        self._decoder = _Decoder(path, fs)
        self._decoder.done.connect(lambda a, f, err, p=path: self._decoded(p, a, f, err))
        self._decoder.start()

    def _decoded(self, path, audio, fs, err):
        self.btn.setEnabled(True)
        self.status.setText("")
        if audio is None:
            self._stop()
            QMessageBox.warning(self, t("error"), err)
            return
        self._cache[path] = (audio, fs)
        if self.btn.isChecked() and self.source_box.currentData() == path:
            self._start(audio, fs)

    def _start(self, audio, fs):
        try:
            self.player = self.engine.play(audio, fs, self.fader.scalar, loop=True)
            self.btn.setText(t("stop"))
        except Exception as e:
            logging.getLogger(__name__).exception("calibration playback failed")
            self._stop()
            QMessageBox.critical(self, t("error"), f"{type(e).__name__}: {e}")

    def _tick(self):
        v = self.engine.measured_lufs()
        self.meter.set_value(v)
        if (self.player is not None or self._ext_key is not None) and v > -70:
            self.measured = v
            self.readout.setText(f"{v:.1f} LUFS")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return True      # step 2 is optional: B can be set with the dashboard slider instead

    def validatePage(self):
        self._stop()
        self._release_external()
        if self.measured is None:
            # skipped: start B a little above the ceiling A; adjust on the dashboard later
            self.engine.profile.b_lufs = float(self.engine.profile.a_lufs + 6.0)
            self.engine.profile.genre = ""
            return True
        setattr(self.engine.profile, self.fader_key, (self.fader.db() + 60) / 60)
        self.engine.profile.b_lufs = float(self.measured)
        d = self.source_box.currentData()
        self.engine.profile.genre = d[2] if isinstance(d, tuple) else self.source_box.currentText()
        return True


class IntroPage(QWizardPage):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setTitle(t("cal.intro.h"))
        lay = QVBoxLayout(self)
        l = QLabel(t("cal.intro"))
        l.setWordWrap(True)
        lay.addWidget(l)
        self.dev = QLabel()
        self.dev.setObjectName("muted")
        lay.addWidget(self.dev)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color:#ffb020")
        lay.addWidget(self.warn)
        lay.addStretch(1)
        self._timer = QTimer(self); self._timer.setInterval(1000); self._timer.timeout.connect(self._poll)

    def _poll(self):
        active = [s.name for s in self.engine.state.sessions if s.active and not s.is_self]
        self.warn.setText((t("cal.active_apps") + ", ".join(active)) if active else "")

    def cleanupPage(self):
        self._timer.stop()

    def initializePage(self):
        self._timer.start(); self._poll()
        d = self.engine.device
        try:
            m = self.engine.backend.get_master_db(d)
            self.dev.setText(f"{t('device')}: {d.name}    master = {m:+.1f} dB")
        except Exception:
            self.dev.setText(f"{t('device')}: {d.name if d else '?'}")


class DonePage(QWizardPage):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setTitle(t("cal.done.h"))
        lay = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        lay.addWidget(QLabel(t("cal.done")))
        lay.addStretch(1)

    def initializePage(self):
        p = self.engine.profile
        gap = p.b_lufs - p.a_lufs
        self.summary.setText(
            f"{t('a')}: {p.a_lufs:.1f} LUFS\n{t('b')}: {p.b_lufs:.1f} LUFS   (B − A = {gap:+.1f} LU)\n"
            f"{t('genre')}: {p.genre or '—'}\n"
            f"{t('cal.vis')}: {t('cal.vis.sw') if p.master_visible_in_loopback else t('cal.vis.hw')}")


class CalibrationWizard(QWizard):
    def __init__(self, engine: Engine, music_dir: str, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle(t("cal.title"))
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setButtonText(QWizard.NextButton, t("next"))
        self.setButtonText(QWizard.BackButton, t("back"))
        self.setButtonText(QWizard.FinishButton, t("finish"))
        self.setButtonText(QWizard.CancelButton, t("cancel"))
        self.resize(720, 560)
        self.addPage(IntroPage(engine))
        self.addPage(BeepPage(engine))
        self.addPage(MusicPage(engine, music_dir))
        self.addPage(DonePage(engine))
        self._backup = None

    def exec(self):
        prof = self.engine.profile
        self._backup = (prof.a_lufs, prof.b_lufs, prof.master_ref_db, prof.calibrated, prof.genre)
        self.engine.begin_calibration()
        try:
            prof.master_ref_db = self.engine.backend.get_master_db(self.engine.device)
        except Exception:
            pass
        rc = super().exec()
        if rc == QWizard.Accepted:
            # A is normally below B (a harsh beep annoys at a lower loudness
            # than music pleases). If not, the onset simply ramps downwards.
            prof.calibrated = True
        else:
            prof.a_lufs, prof.b_lufs, prof.master_ref_db, prof.calibrated, prof.genre = self._backup
        self.engine.end_calibration()
        return rc
