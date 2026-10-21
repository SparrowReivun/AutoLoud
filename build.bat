@echo off
REM Local Windows build: creates dist\AutoLoud-en.exe
setlocal
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt || goto :err
python tools\make_icon.py
pyinstaller --noconfirm --clean AutoLoud.spec || goto :err
echo.
echo Build finished: dist\AutoLoud-en.exe
exit /b 0
:err
echo Build FAILED.
exit /b 1
