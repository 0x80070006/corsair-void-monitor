@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python n'est pas installe ou le lanceur py est introuvable.
  pause
  exit /b 1
)
py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo Installation echouee.
  pause
  exit /b 1
)
echo.
echo Installation terminee. Lance run.bat pour demarrer le widget.
pause
