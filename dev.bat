@echo off
REM Run from source on Windows (real audio backend).
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
pip install -q -r requirements-dev.txt
python run.py %*
