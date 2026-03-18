@echo off
REM MGS Undubbed GUI — Setup Script (Windows)
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo === MGS Undubbed GUI Setup ===

REM ── Python check ───────────────────────────────────────────────────────────
set "PYTHON="
where python >nul 2>&1 && set "PYTHON=python"
where python3 >nul 2>&1 && set "PYTHON=python3"

if "%PYTHON%"=="" (
    echo ERROR: Python 3 not found. Please install Python 3.8+ and try again.
    pause
    exit /b 1
)
echo Using Python:
%PYTHON% --version

REM ── Create virtual environment ─────────────────────────────────────────────
if not exist ".venv" (
    echo Creating virtual environment at .venv ...
    %PYTHON% -m venv .venv
) else (
    echo Virtual environment already exists at .venv
)

REM Activate
call .venv\Scripts\activate.bat

REM ── Install GUI dependencies ───────────────────────────────────────────────
echo Installing GUI dependencies ...
pip install --upgrade pip -q
pip install -r requirements.txt -q

REM ── Pull latest scripts submodule ──────────────────────────────────────────
echo Updating scripts submodule ...
git submodule update --init --recursive
cd scripts
git checkout main
git pull origin main
cd ..

REM ── Install scripts dependencies ───────────────────────────────────────────
echo Installing scripts dependencies ...
pip install -r scripts\requirements.txt -q

REM ── Create run shortcut ────────────────────────────────────────────────────
(
echo @echo off
echo cd /d "%%~dp0"
echo call .venv\Scripts\activate.bat
echo python mainwindow.py %%*
) > run.bat

echo.
echo === Setup complete! ===
echo Run the app with:  run.bat
echo Or activate the venv manually:  .venv\Scripts\activate.bat ^& python mainwindow.py
pause
