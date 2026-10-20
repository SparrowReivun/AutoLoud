"""Dark theme stylesheet."""

BG = "#14161a"
PANEL = "#1d2026"
PANEL2 = "#23272e"
BORDER = "#2e333b"
TEXT = "#e6e8eb"
MUTED = "#8b919b"
ACCENT = "#4fa3ff"
ACCENT2 = "#36d399"
WARN = "#ffb020"
DANGER = "#ff5c5c"

QSS = f"""
* {{ font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog, QWizard {{ background: {BG}; }}
QWidget#nav {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
QPushButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ background: #2b3039; }}
QPushButton:pressed {{ background: #1a1d22; }}
QPushButton:checked {{ background: {ACCENT}; color: #0b1220; border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: #0b1220; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #6fb5ff; }}
QPushButton#navbtn {{ text-align: left; padding: 10px 16px; border: none; border-radius: 8px; background: transparent; }}
QPushButton#navbtn:checked {{ background: {PANEL2}; color: {ACCENT}; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#h1 {{ font-size: 20px; font-weight: 600; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#big {{ font-size: 30px; font-weight: 600; }}
QLabel#badge {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 3px 10px; color: {MUTED}; }}
QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; selection-background-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 6px; background: {PANEL2}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 18px; height: 18px; margin: -6px 0; background: {ACCENT}; border-radius: 9px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::groove:vertical {{ width: 8px; background: {PANEL2}; border-radius: 4px; }}
QSlider::handle:vertical {{ height: 22px; width: 22px; margin: 0 -7px; background: {ACCENT}; border-radius: 11px; }}
QSlider::add-page:vertical {{ background: {ACCENT}; border-radius: 4px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid {BORDER}; background: {PANEL2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QProgressBar {{ background: {PANEL2}; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background: {ACCENT2}; border-radius: 4px; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; }}
QWizard QWidget {{ background: {BG}; }}
QTextEdit {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 6px; }}
"""
