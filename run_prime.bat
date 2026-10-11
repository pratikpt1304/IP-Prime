@echo off
title PRIME AI :: AUTONOMOUS NEURAL COCKPIT (DUBAI CHAMPIONSHIP EDITION)
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo ========================================================
echo   PRIME AI :: AUTONOMOUS NEURAL COCKPIT
echo   Starting ambient voice ^& neural core...
echo ========================================================
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0prime.py" %*
) else (
    python "%~dp0prime.py" %*
)
echo.
echo Prime AI session ended. Press any key to exit.
pause >nul

