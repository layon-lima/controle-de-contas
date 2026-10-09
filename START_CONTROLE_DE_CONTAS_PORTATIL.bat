@echo off
setlocal
cd /d "%~dp0"
REM Iniciador do Controle de Contas portatil (sem build, sem instalador).
REM Banco e arquivos continuam no mesmo LOCALAPPDATA\ControleContas do app atual.
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
  "%LocalAppData%\Programs\Python\Python313\python.exe" -u "%~dp0portable_app\server.py"
  exit /b %ERRORLEVEL%
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3.13 -u "%~dp0portable_app\server.py"
  exit /b %ERRORLEVEL%
)
where python >nul 2>nul
if not errorlevel 1 (
  python -u "%~dp0portable_app\server.py"
  exit /b %ERRORLEVEL%
)
echo Python 3 nao encontrado. O app portatil precisa do Python usado pelo projeto.
pause
exit /b 1
