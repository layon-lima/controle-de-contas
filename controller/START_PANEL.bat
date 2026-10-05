@echo off
setlocal
cd /d "%~dp0.."
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" ".\controller\server.py"
  exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw ".\controller\server.py"
  exit /b 0
)
python ".\controller\server.py"
