from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
                               QComboBox, QCheckBox, QStackedWidget, QDoubleSpinBox, QFormLayout,
                               QLineEdit, QFileDialog, QSystemTrayIcon, QMenu, QScrollArea, QSlider,
                               QProgressBar, QApplication, QMessageBox, QDialog)

from autoloud.core.engine import Engine, EngineState
from autoloud.core.controller import Phase
from autoloud.ui import theme
from autoloud.ui.i18n import t, set_language
from autoloud.ui.widgets import LoudnessBar, Card, badge, NoWheelSlider
from autoloud.ui.calibration import CalibrationWizard

log = logging.getLogger(__name__)


def make_icon(color: str = theme.ACCENT) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(6, 6, 52, 52, 14, 14)
    p.setBrush(QColor("#0b1220"))
    for i, h in enumerate([18, 30, 40, 26, 14]):
        p.drawRoundedRect(12 + i * 9, 48 - h, 6, h, 2, 2)
    p.end()
    return QIcon(pm)


# ----------------------------------------------------------------------
class DashboardPage(QWidget):
    def __init__(self, engine: Engine, on_calibrate):
        super().__init__()
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        top = QHBoxLayout()
        title = QLabel(t("nav.dashboard"))
        title.setObjectName("h1")
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(QLabel(t("device")))
        self.device_box = QComboBox()
        self.device_box.setMinimumWidth(260)
        top.addWidget(self.device_box)
        self.enable = QCheckBox(t("enable"))
        top.addWidget(self.enable)
        lay.addLayout(top)

        self.status = badge(t("status.idle"))
        self.uncal = QLabel(t("status.uncal"))
        self.uncal.setStyleSheet(f"color:{theme.WARN}")
        srow = QHBoxLayout()
        srow.addWidget(self.status)
        srow.addWidget(self.uncal)
        srow.addStretch(1)
        self.cal_btn = QPushButton(t("recal"))
        self.cal_btn.setObjectName("primary")
        self.cal_btn.clicked.connect(on_calibrate)
        srow.addWidget(self.cal_btn)
        lay.addLayout(srow)

        meters = Card()
        self.m_mom = LoudnessBar(t("meter.momentary"))
        self.m_st = LoudnessBar(t("meter.short"))
        self.m_int = LoudnessBar(t("meter.integrated"))
        for m in (self.m_mom, self.m_st, self.m_int):
            meters.body.addWidget(m)
        lay.addWidget(meters)

        grid = QHBoxLayout()
        self.k_total = self._kpi(grid, t("gain.total"))
        self.k_session = self._kpi(grid, t("gain.session"))
        self.k_master = self._kpi(grid, t("gain.master"))
        self.k_target = self._kpi(grid, t("meter.target"))
        lay.addLayout(grid)

        prof = Card()
        self.prof_lbl = QLabel("—")
        self.prof_lbl.setObjectName("muted")
        prof.body.addWidget(self.prof_lbl)
        lay.addWidget(prof)
        self.err = QLabel("")
        self.err.setStyleSheet(f"color:{theme.DANGER}")
        self.err.setWordWrap(True)
        lay.addWidget(self.err)
        lay.addStretch(1)

    def _kpi(self, grid, label):
        c = Card()
        l = QLabel(label); l.setObjectName("muted")
        v = QLabel("—"); v.setObjectName("big")
        c.body.addWidget(l); c.body.addWidget(v)
        grid.addWidget(c)
        return v

    def refresh_devices(self):
        self.device_box.blockSignals(True)
        self.device_box.clear()
        for d in self.engine.backend.list_output_devices():
            self.device_box.addItem(d.name + ("  ★" if d.is_default else ""), d.id)
        if self.engine.device:
            i = self.device_box.findData(self.engine.device.id)
            if i >= 0:
                self.device_box.setCurrentIndex(i)
        self.device_box.blockSignals(False)

    def update_state(self, s: EngineState):
        r = s.reading
        self.m_mom.set_value(r.momentary)
        self.m_st.set_value(r.short_term)
        self.m_int.set_value(r.integrated)
        p = s.profile
        if p is not None and p.calibrated:
            for m in (self.m_mom, self.m_st, self.m_int):
                m.set_marker("A", p.a_lufs, theme.WARN)
                m.set_marker("B", p.b_lufs, theme.ACCENT2)
            self.prof_lbl.setText(f"{t('a')} = {p.a_lufs:.1f} LUFS · {t('b')} = {p.b_lufs:.1f} LUFS · "
                                  f"master ref = {p.master_ref_db:+.1f} dB · {t('genre')}: {p.genre or '—'}")
            self.uncal.hide()
        else:
            self.uncal.show()
            self.prof_lbl.setText("—")
        c = s.command
        if c is not None:
            self.k_total.setText(f"{c.total_db:+.1f} dB")
            self.k_session.setText(f"{c.session_gain_db:+.1f} dB")
            self.k_master.setText(f"{c.master_offset_db:+.1f} dB")
            self.k_target.setText(f"{c.target_lufs:.1f}")
            self.m_mom.set_marker("T", c.target_lufs, theme.ACCENT)
            key = {Phase.IDLE: "status.idle", Phase.ONSET: "status.onset", Phase.STEADY: "status.steady"}[c.phase]
        else:
            key = "status.calibrating" if s.calibrating else "status.disabled"
            for k in (self.k_total, self.k_session, self.k_master, self.k_target):
                k.setText("—")
        self.status.setText(t(key))
        self.enable.blockSignals(True)
        self.enable.setChecked(s.enabled)
        self.enable.blockSignals(False)
        self.err.setText(s.error)


# ----------------------------------------------------------------------
class AppsPage(QWidget):
    """Per-app mixer: fader (+/- mixer_range_db) + auto-loudness switch per app.

    Always active, whether or not the bus auto-loudness is on. Rows are
    sorted once each time the page is opened: apps producing sound first,
    loudest (smoothed peak) on top. No live re-sorting while you look at it.
    """

    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self.rows: dict[str, dict] = {}
        self._smooth: dict[str, float] = {}
        self._order: list[str] = []
        self._sort_pending = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        h = QLabel(t("apps.h")); h.setObjectName("h1"); lay.addWidget(h)
        hint = QLabel(t("apps.hint")); hint.setWordWrap(True); hint.setObjectName("muted"); lay.addWidget(hint)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.inner = QWidget(); self.inner_lay = QVBoxLayout(self.inner); self.inner_lay.addStretch(1)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True); self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.engine.config.save)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._sort_pending = True          # sort once on entering the page

    def update_state(self, s: EngineState):
        seen = set()
        for sess in s.sessions:
            if sess.is_self:
                continue
            seen.add(sess.key)
            if sess.key not in self.rows:
                self._add_row(sess.key, sess.name)
            row = self.rows[sess.key]
            pk = min(1.0, sess.peak * sess.volume) if sess.active else 0.0
            if self._sort_pending:
                self._smooth[sess.key] = pk        # fresh snapshot for the one-off sort
            else:
                sm = self._smooth.get(sess.key, pk)
                self._smooth[sess.key] = sm + (pk - sm) * 0.3
            row["bar"].setValue(int(min(1.0, sess.peak) * 100))
            row["active"] = sess.active
            w = s.works.get(sess.name)
            if w is None:
                row["work"].setText("")
            else:
                lab = w.label() or t("apps.work_n").format(n=w.index)
                row["work"].setText(("\u266a " if w.source == "media" else "\u25b6 ") + lab)
            row["sub"].setText(f"{sess.name}   \u00b7   vol {sess.volume * 100:.0f}%" +
                               ("" if sess.active else "   \u00b7   " + t("apps.idle")))
        for key in list(self.rows):
            if key not in seen:
                w = self.rows.pop(key)["card"]
                self._smooth.pop(key, None)
                w.setParent(None); w.deleteLater()
        if self._sort_pending:
            self._sort_pending = False
            self._resort()

    def _resort(self):
        order = sorted(self.rows, key=lambda k: (not self.rows[k]["active"], -self._smooth.get(k, 0.0)))
        if order == self._order:
            return
        self._order = order
        for i, key in enumerate(order):
            card = self.rows[key]["card"]
            self.inner_lay.removeWidget(card)
            self.inner_lay.insertWidget(i, card)

    def _changed(self, name: str):
        self.engine.mixer_changed()
        self._save_timer.start()

    def _add_row(self, key, name):
        cfg = self.engine.config
        mix = cfg.mix_for(name)
        rng = int(cfg.mixer_range_db)
        c = Card()
        top = QHBoxLayout()
        alias = QLineEdit(mix.alias); alias.setPlaceholderText(name)
        alias.setToolTip(t("apps.alias.tip")); alias.setMaximumWidth(260)
        alias.setStyleSheet("font-weight:600;")
        def alias_changed(text, m=mix, n=name):
            m.alias = text; self._changed(n)
        alias.textEdited.connect(alias_changed)
        top.addWidget(alias)
        sub = QLabel(name); sub.setObjectName("muted"); top.addWidget(sub, 1)
        auto = QCheckBox(t("apps.auto")); auto.setChecked(mix.auto)
        auto.setToolTip(t("apps.auto.tip"))
        def auto_changed(on, m=mix, n=name):
            m.auto = bool(on); self._changed(n)
        auto.toggled.connect(auto_changed)
        top.addWidget(auto)
        work = QLabel(""); work.setObjectName("muted"); work.setToolTip(t("apps.work.tip"))
        bar = QProgressBar(); bar.setRange(0, 100); bar.setTextVisible(False); bar.setFixedHeight(8)
        row = QHBoxLayout()
        row.addWidget(QLabel(t("apps.fader")))
        sl = NoWheelSlider(Qt.Horizontal); sl.setRange(-rng, rng); sl.setValue(int(round(mix.offset_db)))
        sl.setTickPosition(QSlider.TicksBelow); sl.setTickInterval(10)
        val = QLabel(f"{sl.value():+d} dB"); val.setMinimumWidth(56)
        def changed(v, m=mix, n=name, l=val):
            m.offset_db = float(v); l.setText(f"{v:+d} dB"); self._changed(n)
        sl.valueChanged.connect(changed)
        reset = QPushButton("0 dB"); reset.setFixedWidth(56); reset.setToolTip(t("apps.reset"))
        reset.clicked.connect(lambda _c, s=sl: s.setValue(0))
        row.addWidget(sl, 1); row.addWidget(val); row.addWidget(reset)
        c.body.addLayout(top); c.body.addWidget(work); c.body.addWidget(bar); c.body.addLayout(row)
        self.inner_lay.insertWidget(self.inner_lay.count() - 1, c)
        self.rows[key] = {"card": c, "bar": bar, "slider": sl, "sub": sub, "auto": auto, "work": work, "active": False}


# ----------------------------------------------------------------------
class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        cfg = engine.config
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        h = QLabel(t("nav.settings")); h.setObjectName("h1"); lay.addWidget(h)
        card = Card()
        form = QFormLayout()
        self.spins = {}
        P = cfg.params
        for key, label, lo, hi, step in [
            ("ramp_s", "set.ramp", 0.1, 10, 0.1),
            ("onset_ramp_s", "set.onset_ramp", 0.2, 10, 0.1),
            ("window_s", "set.window", 5, 900, 5),
            ("first_window_s", "set.first_window", 2, 120, 1),
            ("onset_guard_lufs", "set.guard", -40, 0, 1),
            ("safety_margin_db", "set.safety", 1, 20, 0.5),
            ("max_master_boost_db", "set.boost", 0, 40, 1),
            ("silence_timeout_s", "set.silence", 0.5, 30, 0.5),
            ("source_change_window_s", "set.srcchange", 0, 30, 1),
            ("work_silence_s", "set.worksilence", 3, 120, 1),
        ]:
            sp = QDoubleSpinBox(); sp.setRange(lo, hi); sp.setSingleStep(step); sp.setValue(getattr(P, key))
            form.addRow(t(label), sp)
            self.spins[key] = sp
        self.music_dir = QLineEdit(cfg.music_dir)
        browse = QPushButton(t("set.browse"))
        browse.clicked.connect(self._browse)
        r = QHBoxLayout(); r.addWidget(self.music_dir, 1); r.addWidget(browse)
        form.addRow(t("set.music_dir"), r)
        self.minimized = QCheckBox(); self.minimized.setChecked(cfg.start_minimized)
        form.addRow(t("set.minimized"), self.minimized)
        self.lang = QComboBox(); self.lang.addItems(["zh", "en"]); self.lang.setCurrentText(cfg.language)
        form.addRow(t("set.lang"), self.lang)
        card.body.addLayout(form)
        save = QPushButton(t("set.save")); save.setObjectName("primary"); save.clicked.connect(self._save)
        card.body.addWidget(save, 0, Qt.AlignRight)
        lay.addWidget(card)
        info = QLabel(f"{t('backend')}: {engine.backend.name}   ·   config: {cfg.path()}")
        info.setObjectName("muted"); info.setWordWrap(True)
        lay.addWidget(info)
        lay.addStretch(1)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, t("set.music_dir"), self.music_dir.text())
        if d:
            self.music_dir.setText(d)

    def _save(self):
        cfg = self.engine.config
        for k, sp in self.spins.items():
            setattr(cfg.params, k, float(sp.value()))
        cfg.music_dir = self.music_dir.text()
        cfg.start_minimized = self.minimized.isChecked()
        cfg.language = self.lang.currentText()
        cfg.save()
        self.engine.apply_params()
        self.saved.emit()


# ----------------------------------------------------------------------
class MainWindow(QMainWindow):
    state_changed = Signal(object)

    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        set_language(engine.config.language)
        self.setWindowTitle(t("app.title"))
        self.setWindowIcon(make_icon())
        self.resize(980, 680)
        self.setStyleSheet(theme.QSS)

        root = QWidget(); self.setCentralWidget(root)
        hl = QHBoxLayout(root); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(0)
        nav = QWidget(); nav.setObjectName("nav"); nav.setFixedWidth(180)
        nl = QVBoxLayout(nav); nl.setContentsMargins(12, 16, 12, 16); nl.setSpacing(4)
        brand = QLabel("AutoLoud"); brand.setObjectName("h1"); nl.addWidget(brand)
        nl.addSpacing(10)
        self.stack = QStackedWidget()
        self.dash = DashboardPage(engine, self.run_calibration)
        self.apps = AppsPage(engine)
        self.settings = SettingsPage(engine)
        self.nav_btns = []
        for i, (key, page) in enumerate([("nav.dashboard", self.dash), ("nav.apps", self.apps),
                                         ("nav.settings", self.settings)]):
            b = QPushButton(t(key)); b.setObjectName("navbtn"); b.setCheckable(True)
            b.clicked.connect(lambda _c, i=i: self._go(i))
            nl.addWidget(b); self.nav_btns.append(b); self.stack.addWidget(page)
        nl.addStretch(1)
        self.backend_lbl = QLabel(engine.backend.name); self.backend_lbl.setObjectName("muted")
        nl.addWidget(self.backend_lbl)
        hl.addWidget(nav); hl.addWidget(self.stack, 1)
        self._go(0)

        # engine → UI (thread-safe via signal)
        self.state_changed.connect(self._on_state)
        engine.listeners.append(self.state_changed.emit)
        self.dash.device_box.currentIndexChanged.connect(self._device_changed)
        self.dash.enable.toggled.connect(self._enable_toggled)
        self.settings.saved.connect(lambda: QMessageBox.information(self, t("nav.settings"), "OK"))

        self._ui_timer = QTimer(self); self._ui_timer.setInterval(100)
        self._ui_timer.timeout.connect(lambda: self._on_state(engine.state))
        self._ui_timer.start()
        self._latest = engine.state

        # tray
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        self.tray_enable = QAction(t("enable"), menu, checkable=True)
        self.tray_enable.setChecked(engine.config.enabled)
        self.tray_enable.toggled.connect(self._enable_toggled)
        menu.addAction(self.tray_enable)
        menu.addAction(QAction(t("tray.show"), menu, triggered=self.showNormal))
        menu.addSeparator()
        menu.addAction(QAction(t("tray.quit"), menu, triggered=self.quit))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.showNormal() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        self._quitting = False

    # ------------------------------------------------------------------
    def _go(self, i):
        self.stack.setCurrentIndex(i)
        for j, b in enumerate(self.nav_btns):
            b.setChecked(i == j)

    def start(self):
        self.engine.start()
        self.dash.refresh_devices()
        if self.engine.profile is not None and not self.engine.profile.calibrated:
            QTimer.singleShot(400, self.run_calibration)

    def _on_state(self, s: EngineState):
        self._latest = s
        if not self.isVisible():
            return
        self.dash.update_state(s)
        if self.stack.currentIndex() == 1:
            self.apps.update_state(s)
        self.tray_enable.blockSignals(True); self.tray_enable.setChecked(s.enabled); self.tray_enable.blockSignals(False)

    def _device_changed(self, idx):
        dev_id = self.dash.device_box.itemData(idx)
        for d in self.engine.backend.list_output_devices():
            if d.id == dev_id:
                self.engine.start(d)
                self.engine.config.save()
                if not self.engine.profile.calibrated:
                    QTimer.singleShot(200, self.run_calibration)
                break

    def _enable_toggled(self, on):
        self.engine.set_enabled(bool(on))
        self.engine.config.save()

    def run_calibration(self):
        if self.engine.device is None:
            return
        wiz = CalibrationWizard(self.engine, self.engine.config.music_dir, self)
        wiz.setStyleSheet(theme.QSS)
        if wiz.exec() == QDialog.Accepted:
            self.engine.config.save()

    def closeEvent(self, ev):
        if self._quitting or not QSystemTrayIcon.isSystemTrayAvailable():
            self.engine.stop()
            ev.accept()
            return
        ev.ignore()
        self.hide()
        self.tray.showMessage("AutoLoud", t("tray.show"), make_icon(), 1500)

    def quit(self):
        self._quitting = True
        self.engine.stop()
        self.engine.config.save()
        QApplication.instance().quit()
