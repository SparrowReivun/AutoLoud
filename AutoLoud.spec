# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds a single-file windowed exe.
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = (collect_submodules("pycaw") + collect_submodules("comtypes")
          + ["pyaudiowpatch", "psutil", "soundfile", "winsdk", "winsdk.windows.media.control", "winsdk.windows.foundation"])
datas = [("assets/autoloud.ico", "assets"), ("assets/autoloud.png", "assets"),
         ("assets/music/default", "assets/music/default")] + collect_data_files("soundfile")

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    excludes=["tkinter", "matplotlib", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtMultimedia", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtPdf", "pyloudnorm", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="AutoLoud-en",
    icon="assets/autoloud.ico",
    console=False,
    upx=False,
    disable_windowed_traceback=False,
)
