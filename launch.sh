#!/usr/bin/env bash
# MGS Undubbed GUI — Launch Script (macOS / Linux)
# On first run, sets up venv, dependencies, and submodule automatically.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Find Python ──────────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3 not found. Please install Python 3.8+ and try again."
    exit 1
fi

# ── Check for ffmpeg ─────────────────────────────────────────────────────────
if ! command -v ffmpeg &>/dev/null; then
    echo "ffmpeg not found. Attempting to install ..."
    if [ "$(uname)" = "Darwin" ]; then
        if command -v brew &>/dev/null; then
            brew install ffmpeg
        else
            echo "ERROR: ffmpeg is required but Homebrew is not installed."
            echo "Install Homebrew (https://brew.sh) then run:  brew install ffmpeg"
            exit 1
        fi
    elif command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y ffmpeg
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm ffmpeg
    else
        echo "ERROR: ffmpeg is required. Please install it manually and try again."
        exit 1
    fi
fi

# ── First-run setup (runs if .venv doesn't exist) ───────────────────────────
if [ ! -d ".venv" ]; then
    echo "=== First-run setup ==="
    echo "Using Python: $($PYTHON --version)"

    echo "Creating virtual environment ..."
    "$PYTHON" -m venv .venv
    source .venv/bin/activate

    echo "Installing dependencies ..."
    pip install --upgrade pip -q
    pip install -r requirements.txt -q

    echo "Initializing scripts submodule ..."
    git submodule update --init --recursive
    (cd scripts && git checkout main && git pull origin main)

    echo "Installing scripts dependencies ..."
    pip install -r scripts/requirements.txt -q

    echo "=== Setup complete! ==="
    echo ""
fi

# ── Activate and launch ─────────────────────────────────────────────────────
source .venv/bin/activate
exec python mainwindow.py "$@"
