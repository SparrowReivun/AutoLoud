"""Custom widgets: loudness bar meter, dB fader, toggle."""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QLinearGradient
from PySide6.QtWidgets import QWidget, QSlider, QVBoxLayout, QLabel, QHBoxLayout, QFrame

from autoloud.ui import theme


class LoudnessBar(QWidget):
    """Horizontal LUFS bar from -60 to 0 with markers for A, B and target."""

    LO, HI = -60.0, 0.0

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.label = label
        self.value = self.LO
        self.markers: dict[str, tuple[float, str]] = {}   # name -> (lufs, color)
        self.setMinimumHeight(60)

    def set_value(self, v: float) -> None:
        self.value = max(self.LO, min(self.HI, v)) if v > -200 else self.LO
        self.update()

    def set_marker(self, name: str, lufs: float | None, color: str = theme.ACCENT2) -> None:
        if lufs is None:
            self.markers.pop(name, None)
        else:
            self.markers[name] = (lufs, color)
        self.update()

    def _x(self, v: float, w: float) -> float:
        return (max(self.LO, min(self.HI, v)) - self.LO) / (self.HI - self.LO) * w

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        bar_y, bar_h = 20, 14
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(self.font().family(), 9))
        p.drawText(0, 0, w, 16, Qt.AlignLeft | Qt.AlignVCenter, self.label)
        if self.value <= self.LO:
            txt = "—"
        elif "B" in self.markers:                     # dashboard: show the distance to B
            txt = f"B {self.value - self.markers['B'][0]:+.1f} dB"
        else:
            txt = f"{self.value:.1f} LUFS"
        p.drawText(0, 0, w, 16, Qt.AlignRight | Qt.AlignVCenter, txt)
        # groove
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.PANEL2))
        p.drawRoundedRect(QRectF(0, bar_y, w, bar_h), 4, 4)
        # fill
        g = QLinearGradient(0, 0, w, 0)
        g.setColorAt(0.0, QColor(theme.ACCENT2))
        g.setColorAt(0.7, QColor(theme.ACCENT))
        g.setColorAt(1.0, QColor(theme.DANGER))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(0, bar_y, self._x(self.value, w), bar_h), 4, 4)
        # ticks
        p.setPen(QPen(QColor(theme.BORDER), 1))
        for v in range(int(self.LO), int(self.HI) + 1, 10):
            x = self._x(v, w)
            p.drawLine(int(x), bar_y + bar_h, int(x), bar_y + bar_h + 4)
            p.setPen(QColor(theme.MUTED))
            p.drawText(max(0, min(int(x) - 14, w - 28)), bar_y + bar_h + 6, 28, 14, Qt.AlignCenter, str(v))
            p.setPen(QPen(QColor(theme.BORDER), 1))
        # markers
        for name, (v, color) in self.markers.items():
            x = self._x(v, w)
            p.setPen(QPen(QColor(color), 2))
            p.drawLine(int(x), bar_y - 4, int(x), bar_y + bar_h + 4)
            p.setPen(QColor(color))
            p.drawText(int(x) - 20, 2, 40, 14, Qt.AlignCenter, name)
        p.end()


class NoWheelSlider(QSlider):
    """QSlider that ignores the mouse wheel (so scrolling a list never moves a fader)."""
    def wheelEvent(self, ev):
        ev.ignore()


class DbFader(QWidget):
    """A vertical fader in dB (default -60..0) with a numeric readout."""
    valueChanged = Signal(float)

    def __init__(self, lo=-60.0, hi=0.0, parent=None):
        super().__init__(parent)
        self.lo, self.hi = lo, hi
        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setMinimumHeight(220)
        self.setMinimumWidth(90)
        self.readout = QLabel("-∞ dB")
        self.readout.setAlignment(Qt.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.slider, 1, Qt.AlignHCenter)
        lay.addWidget(self.readout)
        self.slider.valueChanged.connect(self._changed)

    def db(self) -> float:
        return self.lo + (self.hi - self.lo) * self.slider.value() / 1000.0

    def scalar(self) -> float:
        if self.slider.value() == 0:
            return 0.0
        return 10 ** (self.db() / 20)

    def set_db(self, db: float) -> None:
        f = (max(self.lo, min(self.hi, db)) - self.lo) / (self.hi - self.lo)
        self.slider.setValue(int(round(f * 1000)))

    def _changed(self, _v):
        self.readout.setText("-∞ dB" if self.slider.value() == 0 else f"{self.db():+.1f} dB")
        self.valueChanged.emit(self.db())


class Card(QFrame):
    def __init__(self, title: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(10)
        if title:
            h = QLabel(title)
            h.setObjectName("h2")
            self.body.addWidget(h)


def badge(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("badge")
    return l


def hrow(*widgets, stretch_last=False) -> QHBoxLayout:
    r = QHBoxLayout()
    for w in widgets:
        r.addWidget(w)
    if stretch_last:
        r.addStretch(1)
    return r
