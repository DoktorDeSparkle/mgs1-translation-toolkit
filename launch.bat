@echo off
REM MGS Undubbed GUI — Launch Script (Windows)
REM On first run, sets up venv, dependencies, and submodule automatically.
setlocal enabledelayedexpansion

cd /d "%~dp0"

REM ── Find Python ────────────────────────────────────────────────────────────
set "PYTHON="
where python3 >nul 2>&1 && set "PYTHON=python3"
if "%PYTHON%"=="" where python >nul 2>&1 && set "PYTHON=python"

if "%PYTHON%"=="" (
    echo ERROR: Python 3 not found. Please install Python 3.8+ and try again.
    pause
    exit /b 1
)

REM ── Check for ffmpeg ────────────────────────────────────────────────────────
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo ffmpeg not found. Attempting to install via winget ...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo ERROR: ffmpeg is required. Please install it manually:
        echo   https://ffmpeg.org/download.html
        echo Or install winget, then run:  winget install Gyan.FFmpeg
        pause
        exit /b 1
    )
    winget install Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    echo NOTE: You may need to restart this script for ffmpeg to be on PATH.
)

REM ── First-run setup (runs if .venv doesn't exist) ─────────────────────────
if not exist ".venv" (
    echo === First-run setup ===
    echo Using Python:
    %PYTHON% --version

    echo Creating virtual environment ...
    %PYTHON% -m venv .venv
    call .venv\Scripts\activate.bat

    echo Installing dependencies ...
    pip install --upgrade pip -q
    pip install -r requirements.txt -q

    echo Initializing scripts submodule ...
    git submodule update --init --recursive
    cd scripts
    git checkout main
    git pull origin main
    cd ..

    echo Installing scripts dependencies ...
    pip install -r scripts\requirements.txt -q

    echo === Setup complete! ===
    echo.
)

REM ── Activate and launch ────────────────────────────────────────────────────
call .venv\Scripts\activate.bat
python mainwindow.py %*
