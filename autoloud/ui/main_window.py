from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
                               QComboBox, QCheckBox, QStackedWidget, QDoubleSpinBox, QFormLayout,
                               QLineEdit, QFileDialog, QSystemTrayIcon, QMenu, QScrollArea, QSlider,
                               QProgressBar, QApplication, QMessageBox, QDialog, QInputDialog)

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
        top.addSpacing(12)
        top.addWidget(QLabel(t("dash.mode")))
        self.mode_box = QComboBox(); self.mode_box.setMinimumWidth(160)
        self.mode_box.setToolTip(t("dash.mode.tip"))
        self._fill_modes()
        self.mode_box.currentTextChanged.connect(self._mode_changed)
        top.addWidget(self.mode_box)
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

        # comfort level B as a knob: where you leave it is the new target
        bcard = Card()
        brow = QHBoxLayout()
        self.b_lbl = QLabel(t("dash.b")); brow.addWidget(self.b_lbl)
        self.b_slider = NoWheelSlider(Qt.Horizontal); self.b_slider.setRange(-600, 0)   # x0.1 LUFS
        self.b_slider.setTickPosition(QSlider.TicksBelow); self.b_slider.setTickInterval(100)
        self.b_slider.setSingleStep(1); self.b_slider.setPageStep(10)
        self.b_slider.valueChanged.connect(self._b_changed)
        brow.addWidget(self.b_slider, 1)
        self.b_val = QLabel("—"); self.b_val.setMinimumWidth(110); brow.addWidget(self.b_val)
        bcard.body.addLayout(brow)
        bhint = QLabel(t("dash.b.hint")); bhint.setWordWrap(True); bhint.setObjectName("muted")
        bcard.body.addWidget(bhint)
        trow = QHBoxLayout()
        trow.addWidget(QLabel(t("dash.thr")))
        self.thr_slider = NoWheelSlider(Qt.Horizontal); self.thr_slider.setRange(-10, 50)   # x0.5 dB
        self.thr_slider.setTickPosition(QSlider.TicksBelow); self.thr_slider.setTickInterval(10)
        self.thr_slider.setValue(int(round(engine.config.params.safety_margin_db * 2)))
        self.thr_slider.valueChanged.connect(self._thr_changed)
        trow.addWidget(self.thr_slider, 1)
        self.thr_val = QLabel("—"); self.thr_val.setMinimumWidth(110); trow.addWidget(self.thr_val)
        bcard.body.addLayout(trow)
        thint = QLabel(t("dash.thr.hint")); thint.setWordWrap(True); thint.setObjectName("muted")
        bcard.body.addWidget(thint)
        lay.addWidget(bcard)

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

        act = Card()
        self.act_now = QLabel("—"); self.act_now.setObjectName("h2"); self.act_now.setWordWrap(True)
        act.body.addWidget(self.act_now)
        self.act_log = QLabel(""); self.act_log.setObjectName("muted"); self.act_log.setWordWrap(True)
        self.act_log.setTextFormat(Qt.RichText)
        act.body.addWidget(self.act_log)
        lay.addWidget(act)

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

    def _update_activity(self, s: EngineState):
        import time as _time
        a = s.activity or {}
        c = s.command
        now_txt, frac = "—", None
        if c is None:
            now_txt = t("status.calibrating") if s.calibrating else t("act.off")
        elif "ramp" in a:
            r = a["ramp"]
            now_txt = t("act.ramp").format(frm=r["from"], to=r["to"], d=r["to"] - r["from"], dur=r["dur"], left=r["left"])
            frac = 1.0 - r["left"] / max(1e-3, r["dur"])
        elif "onset" in a:
            o = a["onset"]
            now_txt = t("act.onset").format(el=o["elapsed"], tot=o["total"], g=a["gain"])
            frac = o["elapsed"] / max(1e-3, o["total"])
        elif "listen" in a:
            l = a["listen"]
            key = "act.listen_steady" if a["phase"] == "steady" else "act.listen"
            b = s.profile.b_lufs if s.profile is not None else 0.0
            blk = s.reading.integrated + self._hidden_db(s)
            now_txt = t(key).format(el=l["elapsed"], tot=l["total"], g=a["gain"], lufs=blk, rel=blk - b)
            frac = l["elapsed"] / max(1e-3, l["total"])
        elif "listen_in" in a:
            now_txt = t("act.listen_in").format(sec=a["listen_in"], g=a["gain"])
        elif a.get("phase") == "safety":
            now_txt = t("act.safety").format(g=a["gain"], pre=a.get("pre_safety_gain", a["gain"]))
        elif a.get("phase") == "idle":
            now_txt = t("act.idle").format(g=a["gain"])
        self.act_now.setText(now_txt)
        lines = []
        for ts, kind, d in list(s.events)[-6:][::-1]:
            lines.append(f'<span style="color:#8b919b">{_time.strftime("%H:%M:%S", _time.localtime(ts))}</span>&nbsp; {self._fmt_event(kind, d)}')
        self.act_log.setText("<br>".join(lines))

    @staticmethod
    def _fmt_event(kind: str, d: dict) -> str:
        d = dict(d)
        if kind == "new_work":
            d["mode"] = t("ev.mode." + d.get("mode", "mixed"))
        if kind == "work":
            d["label"] = (" · " + d["label"]) if d.get("label") else ""
            d["source"] = t("ev.src." + d.get("source", "silence"))
        try:
            return t("ev." + kind).format(**d)
        except Exception:
            return f"{kind} {d}"

    def _kpi(self, grid, label):
        c = Card()
        l = QLabel(label); l.setObjectName("muted")
        v = QLabel("—"); v.setObjectName("big")
        c.body.addWidget(l); c.body.addWidget(v)
        grid.addWidget(c)
        return v

    def _fill_modes(self):
        cfg = self.engine.config
        cfg.ensure_presets()
        self.mode_box.blockSignals(True)
        self.mode_box.clear()
        for name in cfg.presets:
            self.mode_box.addItem(name)
        self.mode_box.setCurrentText(cfg.active_preset)
        self.mode_box.blockSignals(False)

    def _mode_changed(self, name: str):
        cfg = self.engine.config
        if not name or name == cfg.active_preset:
            return
        cfg.switch_preset(name)
        self.engine.apply_params()
        self.engine._event("mode", name=name)
        cfg.save()

    def _b_changed(self, v):
        self.engine.set_target_b(v / 10.0)
        self.b_val.setText(t("dash.b.mute") if v <= -600 else f"{v / 10.0:.1f} LUFS")

    def _thr_changed(self, v):
        self.engine.config.params.safety_margin_db = v / 2.0
        self.engine.apply_params(); self.engine.config.save()
        self._update_thr_label()

    def _update_thr_label(self):
        m = self.engine.config.params.safety_margin_db
        p = self.engine.profile
        self.thr_val.setText(f"B {m:+.1f} dB" + (f" = {p.b_lufs + m:.1f}" if p else ""))

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

    @staticmethod
    def _hidden_db(s: EngineState) -> float:
        """Master boost the loopback cannot see: add it so the meters show what
        the controller reasons about (and what you hear), not the raw tap."""
        c, p = s.command, s.profile
        if c is None or p is None or p.master_visible_in_loopback:
            return 0.0
        return max(0.0, c.master_offset_db)

    def update_state(self, s: EngineState):
        r = s.reading
        hid = self._hidden_db(s)
        self.m_mom.set_value(r.momentary + hid if r.momentary > -100 else r.momentary)
        self.m_st.set_value(r.short_term + hid if r.short_term > -100 else r.short_term)
        self.m_int.set_value(r.integrated + hid if r.integrated > -100 else r.integrated)
        p = s.profile
        if p is not None and not self.b_slider.isSliderDown():
            v = int(round(p.b_lufs * 10))
            if v != self.b_slider.value():
                self.b_slider.blockSignals(True); self.b_slider.setValue(v); self.b_slider.blockSignals(False)
            self.b_val.setText(t("dash.b.mute") if p.b_lufs <= -59.95 else f"{p.b_lufs:.1f} LUFS")
        mv = int(round(self.engine.config.params.safety_margin_db * 2))
        if mv != self.thr_slider.value() and not self.thr_slider.isSliderDown():
            self.thr_slider.blockSignals(True); self.thr_slider.setValue(mv); self.thr_slider.blockSignals(False)
        self._update_thr_label()
        if p is not None and p.calibrated:
            for m in (self.m_mom, self.m_st, self.m_int):
                m.set_marker("B", p.b_lufs, theme.ACCENT2)
            self.prof_lbl.setText(f"{t('b')} = {p.b_lufs:.1f} LUFS · "
                                  f"master ref = {p.master_ref_db:+.1f} dB · {t('genre')}: {p.genre or '—'}")
            self.uncal.hide()
        else:
            self.uncal.show()
            self.prof_lbl.setText("—")
        c = s.command
        if c is not None:
            self.k_total.setText(f"{c.total_db:+.1f} dB")
            self.k_session.setText(f"{c.session_gain_db:+.1f} dB")
            cap = getattr(self.engine.controller, "boost_cap_db", 1e9) if self.engine.controller else 1e9
            at_cap = c.master_offset_db >= min(cap, self.engine.config.params.max_master_boost_db) - 0.05 and c.master_offset_db > 0
            self.k_master.setText(f"{c.master_offset_db:+.1f} dB" + ("  " + t("gain.master.cap") if at_cap else ""))
            self.k_target.setText(f"{c.target_lufs:.1f}")
            self.m_mom.set_marker("T", c.target_lufs, theme.ACCENT)
            key = {Phase.IDLE: "status.idle", Phase.ONSET: "status.onset", Phase.ADJUST: "status.adjust",
                   Phase.STEADY: "status.steady", Phase.SAFETY: "status.safety"}[c.phase]
        else:
            key = "status.calibrating" if s.calibrating else "status.disabled"
            for k in (self.k_total, self.k_session, self.k_master, self.k_target):
                k.setText("—")
        self.status.setText(t(key))
        self._update_activity(s)
        if self.mode_box.currentText() != self.engine.config.active_preset or \
                self.mode_box.count() != len(self.engine.config.presets):
            self._fill_modes()
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
            mix = self.engine.config.apps.get(sess.name)
            if mix is not None:
                if row["auto"].isChecked() != mix.strong:
                    row["auto"].blockSignals(True); row["auto"].setChecked(mix.strong); row["auto"].blockSignals(False)
                row["tool"].setVisible(not mix.auto and mix.auto_set_by == "tool")
                if not row["slider"].isSliderDown():       # fader moved by the program (strong mode)
                    want = -60 if mix.offset_db <= -60 else int(round(mix.offset_db))
                    if want != row["slider"].value():
                        row["slider"].blockSignals(True); row["slider"].setValue(want); row["slider"].blockSignals(False)
                        row["val"].setText("mute" if want <= -60 else f"{want:+d} dB")
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
        auto = QCheckBox(t("apps.strong")); auto.setChecked(mix.strong)
        auto.setToolTip(t("apps.strong.tip"))
        def strong_changed(on, n=name):
            self.engine.set_strong(n, bool(on)); self._save_timer.start()
        auto.toggled.connect(strong_changed)
        top.addWidget(auto)
        tool = QLabel(t("apps.tool")); tool.setObjectName("muted"); tool.setToolTip(t("apps.auto.tool")); tool.hide()
        top.addWidget(tool)
        work = QLabel(""); work.setObjectName("muted"); work.setToolTip(t("apps.work.tip"))
        bar = QProgressBar(); bar.setRange(0, 100); bar.setTextVisible(False); bar.setFixedHeight(8)
        row = QHBoxLayout()
        row.addWidget(QLabel(t("apps.fader")))
        MUTE = -60   # bottom of the fader = mute
        sl = NoWheelSlider(Qt.Horizontal); sl.setRange(MUTE, rng)
        sl.setValue(MUTE if mix.offset_db <= MUTE else int(round(mix.offset_db)))
        sl.setTickPosition(QSlider.TicksBelow); sl.setTickInterval(5)
        def fmt(v): return "mute" if v <= MUTE else f"{v:+d} dB"
        val = QLabel(fmt(sl.value())); val.setMinimumWidth(56)
        def changed(v, m=mix, n=name, l=val):
            m.offset_db = -200.0 if v <= MUTE else float(v); l.setText(fmt(v))
            self.engine.user_touched_fader(n); self._save_timer.start()
        sl.valueChanged.connect(changed)
        reset = QPushButton("0 dB"); reset.setFixedWidth(56); reset.setToolTip(t("apps.reset"))
        reset.clicked.connect(lambda _c, s=sl: s.setValue(0))
        row.addWidget(sl, 1); row.addWidget(val); row.addWidget(reset)
        c.body.addLayout(top); c.body.addWidget(work); c.body.addWidget(bar); c.body.addLayout(row)
        self.inner_lay.insertWidget(self.inner_lay.count() - 1, c)
        self.rows[key] = {"card": c, "bar": bar, "slider": sl, "val": val, "sub": sub, "auto": auto, "tool": tool, "work": work, "active": False}


# ----------------------------------------------------------------------
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
        # ---- parameter presets ------------------------------------------
        pcard = Card()
        prow = QHBoxLayout()
        prow.addWidget(QLabel(t("preset.label")))
        self.preset_box = QComboBox(); self.preset_box.setMinimumWidth(220)
        prow.addWidget(self.preset_box, 1)
        for key, slot in (("preset.new", self._preset_new), ("preset.rename", self._preset_rename),
                          ("preset.delete", self._preset_delete)):
            b = QPushButton(t(key)); b.clicked.connect(slot); prow.addWidget(b)
        pcard.body.addLayout(prow)
        phint = QLabel(t("preset.hint")); phint.setObjectName("muted"); phint.setWordWrap(True)
        pcard.body.addWidget(phint)
        lay.addWidget(pcard)
        self._fill_presets()
        self.preset_box.currentTextChanged.connect(self._preset_switched)
        card = Card()
        form = QFormLayout()
        self.spins = {}
        P = cfg.params
        for key, label, lo, hi, step in [
            ("listen_s", "set.listen", 2, 30, 1),
            ("step_up_db", "set.stepup", 0.1, 6, 0.1),
            ("step_down_db", "set.stepdown", 0.5, 12, 0.5),
            ("deadband_up_db", "set.deadband_up", 0, 15, 0.5),
            ("deadband_down_db", "set.deadband_down", 0, 30, 0.5),
            ("snap_db", "set.snap", 0.5, 6, 0.5),
            ("steady_trigger_db", "set.steadytrig", 1, 15, 0.5),
            ("steady_over_blocks", "set.steadyover_n", 1, 10, 1),
            ("steady_under_db", "set.steadyunder", -40, -3, 1),
            ("steady_under_blocks", "set.steadyunder_n", 1, 20, 1),
            ("safety_margin_db", "set.safety", -5, 25, 0.5),
            ("safety_land_db", "set.safetyland", 0, 15, 0.5),
            ("safety_ramp_s", "set.safetyramp", 0.05, 3, 0.05),
            ("onset_below_b_db", "set.onset_below", 0, 30, 1),
            ("move_ramp_s", "set.moveramp", 0.2, 10, 0.2),
            ("onset_guard_lufs", "set.guard", -40, 0, 1),
            ("max_master_boost_db", "set.boost", 0, 40, 1),
            ("work_silence_s", "set.worksilence", 3, 120, 1),
        ]:
            sp = QDoubleSpinBox(); sp.setRange(lo, hi); sp.setSingleStep(step); sp.setValue(getattr(P, key))
            form.addRow(t(label), sp)
            self.spins[key] = sp
        for sp in self.spins.values():
            sp.setKeyboardTracking(False)       # typed digits apply on Enter / focus-out, not per keystroke
            sp.valueChanged.connect(self._changed)
        self.music_dir = QLineEdit(cfg.music_dir)
        browse = QPushButton(t("set.browse"))
        browse.clicked.connect(self._browse)
        r = QHBoxLayout(); r.addWidget(self.music_dir, 1); r.addWidget(browse)
        form.addRow(t("set.music_dir"), r)
        self.minimized = QCheckBox(); self.minimized.setChecked(cfg.start_minimized)
        form.addRow(t("set.minimized"), self.minimized)
        self.startup_master = QDoubleSpinBox(); self.startup_master.setRange(0, 100); self.startup_master.setSingleStep(5)
        self.startup_master.setDecimals(0); self.startup_master.setValue(cfg.startup_master_percent)
        form.addRow(t("set.startup_master"), self.startup_master)
        self.volkeys = QCheckBox(); self.volkeys.setChecked(cfg.volume_keys)
        form.addRow(t("set.volkeys"), self.volkeys)
        self.volkey_step = QDoubleSpinBox(); self.volkey_step.setRange(0.1, 6); self.volkey_step.setSingleStep(0.1)
        self.volkey_step.setValue(cfg.volume_key_step_db)
        form.addRow(t("set.volkeys.step"), self.volkey_step)
        self.lang = QComboBox(); self.lang.addItems(["zh", "en"]); self.lang.setCurrentText(cfg.language)
        form.addRow(t("set.lang"), self.lang)
        card.body.addLayout(form)
        self.status = QLabel(t("set.autosave")); self.status.setObjectName("muted")
        card.body.addWidget(self.status, 0, Qt.AlignRight)
        lay.addWidget(card)
        # every control applies and saves on change (debounced a little for the spin arrows)
        self.music_dir.editingFinished.connect(self._changed)
        self.minimized.toggled.connect(self._changed)
        self.volkeys.toggled.connect(self._changed)
        self.startup_master.setKeyboardTracking(False); self.startup_master.valueChanged.connect(self._changed)
        self.volkey_step.setKeyboardTracking(False); self.volkey_step.valueChanged.connect(self._changed)
        self.lang.currentTextChanged.connect(self._changed)
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True); self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save)
        info = QLabel(f"{t('backend')}: {engine.backend.name}   ·   config: {cfg.path()}")
        info.setObjectName("muted"); info.setWordWrap(True)
        lay.addWidget(info)
        lay.addStretch(1)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, t("set.music_dir"), self.music_dir.text())
        if d:
            self.music_dir.setText(d); self._changed()

    def showEvent(self, ev):
        self._reload_spins()
        super().showEvent(ev)

    def _reload_spins(self):
        # the dashboard sliders (B, loud-sound trigger) and preset switches change params too
        P = self.engine.config.params
        for k, sp in self.spins.items():
            sp.blockSignals(True); sp.setValue(getattr(P, k)); sp.blockSignals(False)

    # ---- presets ------------------------------------------------------------
    def _fill_presets(self):
        cfg = self.engine.config
        cfg.ensure_presets()
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        for name in cfg.presets:
            self.preset_box.addItem(name)
        self.preset_box.setCurrentText(cfg.active_preset)
        self.preset_box.blockSignals(False)

    def _preset_switched(self, name: str):
        cfg = self.engine.config
        if not name or name == cfg.active_preset:
            return
        cfg.switch_preset(name)
        self.engine.apply_params()
        self._reload_spins()
        cfg.save()
        self.status.setText(t("preset.switched").format(name=name))

    def _preset_new(self):
        cfg = self.engine.config
        name, ok = QInputDialog.getText(self, t("preset.new"), t("preset.name_prompt"),
                                        text=t("preset.default_name").format(n=len(cfg.presets) + 1))
        name = (name or "").strip()
        if not ok or not name or name in cfg.presets:
            return
        cfg.add_preset(name)          # a copy of the current values, now the active one
        self._fill_presets(); cfg.save()
        self.status.setText(t("preset.created").format(name=name))

    def _preset_rename(self):
        cfg = self.engine.config
        old = cfg.active_preset
        name, ok = QInputDialog.getText(self, t("preset.rename"), t("preset.name_prompt"), text=old)
        name = (name or "").strip()
        if not ok or not name or name == old or name in cfg.presets:
            return
        cfg.rename_preset(old, name)
        self._fill_presets(); cfg.save()

    def _preset_delete(self):
        cfg = self.engine.config
        if len(cfg.presets) <= 1:
            return
        name = cfg.active_preset
        if QMessageBox.question(self, t("preset.delete"), t("preset.delete_confirm").format(name=name)) != QMessageBox.Yes:
            return
        cfg.delete_preset(name)
        self.engine.apply_params(); self._reload_spins(); self._fill_presets(); cfg.save()

    def _changed(self, *_a):
        self.status.setText(t("set.saving"))
        self._save_timer.start()

    def _save(self):
        cfg = self.engine.config
        lang_before = cfg.language
        from autoloud.core.controller import ControllerParams as _CP
        for k, sp in self.spins.items():
            is_int = _CP.__dataclass_fields__[k].type in ("int", int)
            setattr(cfg.params, k, int(round(sp.value())) if is_int else float(sp.value()))
        cfg.music_dir = self.music_dir.text()
        cfg.start_minimized = self.minimized.isChecked()
        cfg.volume_keys = self.volkeys.isChecked()
        cfg.startup_master_percent = float(self.startup_master.value())
        cfg.volume_key_step_db = float(self.volkey_step.value())
        cfg.language = self.lang.currentText()
        cfg.save()
        self.engine.apply_params()
        import time as _time
        self.status.setText(t("set.saved").format(when=_time.strftime("%H:%M:%S")) +
                            (("   " + t("set.lang.restart")) if cfg.language != lang_before else ""))
        self.saved.emit()


# ----------------------------------------------------------------------
class MainWindow(QMainWindow):
    state_changed = Signal(object)

    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        if not engine.config.language:               # first run: follow the system language
            from autoloud.core.profiles import default_language
            engine.config.language = default_language()
        set_language(engine.config.language)
        self.setWindowTitle(t("app.title"))
        self.setWindowIcon(make_icon())
        self.resize(980, 680)
        self.setStyleSheet(theme.QSS)

        root = QWidget(); self.setCentralWidget(root)
        hl = QHBoxLayout(root); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(0)
        self.NAV_W, self.NAV_W_MIN = 180, 56
        nav = QWidget(); nav.setObjectName("nav"); self.nav = nav
        nl = QVBoxLayout(nav); nl.setContentsMargins(8, 12, 8, 12); nl.setSpacing(4)
        head = QHBoxLayout(); head.setSpacing(4)
        self.brand = QLabel("AutoLoud"); self.brand.setObjectName("h1"); head.addWidget(self.brand, 1)
        self.collapse_btn = QPushButton("\u2039"); self.collapse_btn.setObjectName("navtoggle")
        self.collapse_btn.setFixedSize(32, 32); self.collapse_btn.setToolTip(t("nav.collapse"))
        self.collapse_btn.clicked.connect(self.toggle_sidebar)
        head.addWidget(self.collapse_btn, 0, Qt.AlignTop)
        nl.addLayout(head)
        nl.addSpacing(10)
        self.stack = QStackedWidget()
        self.dash = DashboardPage(engine, self.run_calibration)
        self.apps = AppsPage(engine)
        self.settings = SettingsPage(engine)
        self.nav_btns = []
        self._nav_items = [("nav.dashboard", "\u25c9", self.dash), ("nav.apps", "\u2630", self.apps),
                           ("nav.settings", "\u2699", self.settings)]
        for i, (key, icon, page) in enumerate(self._nav_items):
            b = QPushButton(f"{icon}   {t(key)}"); b.setObjectName("navbtn"); b.setCheckable(True)
            b.clicked.connect(lambda _c, i=i: self._go(i))
            nl.addWidget(b); self.nav_btns.append(b); self.stack.addWidget(page)
        nl.addStretch(1)
        self.backend_lbl = QLabel(engine.backend.name); self.backend_lbl.setObjectName("muted")
        nl.addWidget(self.backend_lbl)
        hl.addWidget(nav); hl.addWidget(self.stack, 1)
        self._nav_anim = None
        self._set_sidebar(engine.config.sidebar_collapsed, animate=False)
        self._go(0)

        # engine → UI (thread-safe via signal)
        self.state_changed.connect(self._on_state)
        engine.listeners.append(self.state_changed.emit)
        self.dash.device_box.currentIndexChanged.connect(self._device_changed)

        self._ui_timer = QTimer(self); self._ui_timer.setInterval(100)
        self._ui_timer.timeout.connect(lambda: self._on_state(engine.state))
        self._ui_timer.start()
        self._latest = engine.state

        # tray
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
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

    # ---- collapsible sidebar ----------------------------------------------
    def toggle_sidebar(self):
        self._set_sidebar(not self.engine.config.sidebar_collapsed)
        self.engine.config.save()

    def _set_sidebar(self, collapsed: bool, animate: bool = True):
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        self.engine.config.sidebar_collapsed = collapsed
        self.brand.setVisible(not collapsed)
        self.backend_lbl.setVisible(not collapsed)
        self.collapse_btn.setText("\u203a" if collapsed else "\u2039")
        self.collapse_btn.setToolTip(t("nav.expand") if collapsed else t("nav.collapse"))
        for b, (key, icon, _page) in zip(self.nav_btns, self._nav_items):
            b.setText(icon if collapsed else f"{icon}   {t(key)}")
            b.setToolTip(t(key) if collapsed else "")
            b.setProperty("compact", collapsed); b.style().unpolish(b); b.style().polish(b)
        target = self.NAV_W_MIN if collapsed else self.NAV_W
        if not animate:
            self.nav.setFixedWidth(target); return
        self.nav.setMinimumWidth(0); self.nav.setMaximumWidth(16777215)
        anim = QPropertyAnimation(self.nav, b"maximumWidth", self)
        anim.setDuration(180); anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(self.nav.width()); anim.setEndValue(target)
        anim.finished.connect(lambda: self.nav.setFixedWidth(target))
        self._nav_anim = anim; anim.start()

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

    def _device_changed(self, idx):
        dev_id = self.dash.device_box.itemData(idx)
        for d in self.engine.backend.list_output_devices():
            if d.id == dev_id:
                self.engine.start(d)
                self.engine.config.save()
                if not self.engine.profile.calibrated:
                    QTimer.singleShot(200, self.run_calibration)
                break

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
