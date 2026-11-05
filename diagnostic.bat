@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Lance d'abord install.bat
  pause
  exit /b 1
)
.venv\Scripts\python.exe diagnostic.py
