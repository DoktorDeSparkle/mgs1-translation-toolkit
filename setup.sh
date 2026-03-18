#!/usr/bin/env bash
# MGS Undubbed GUI — Setup Script (macOS / Linux)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== MGS Undubbed GUI Setup ==="

# ── Python check ─────────────────────────────────────────────────────────────
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
echo "Using Python: $($PYTHON --version)"

# ── Create virtual environment ───────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment at .venv ..."
    "$PYTHON" -m venv .venv
else
    echo "Virtual environment already exists at .venv"
fi

# Activate
source .venv/bin/activate

# ── Install GUI dependencies ─────────────────────────────────────────────────
echo "Installing GUI dependencies ..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ── Pull latest scripts submodule ────────────────────────────────────────────
echo "Updating scripts submodule ..."
git submodule update --init --recursive
cd scripts
git checkout main
git pull origin main
cd "$SCRIPT_DIR"

# ── Install scripts dependencies ─────────────────────────────────────────────
echo "Installing scripts dependencies ..."
pip install -r scripts/requirements.txt -q

# ── Create run shortcut ──────────────────────────────────────────────────────
LAUNCHER="$SCRIPT_DIR/run.sh"
cat > "$LAUNCHER" <<'RUNEOF'
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
cd "$SCRIPT_DIR"
python mainwindow.py "$@"
RUNEOF
chmod +x "$LAUNCHER"

# macOS: create .command file (double-clickable from Finder)
if [ "$(uname)" = "Darwin" ]; then
    COMMAND_FILE="$SCRIPT_DIR/MGS Undubbed GUI.command"
    cat > "$COMMAND_FILE" <<CMDEOF
#!/usr/bin/env bash
cd "$SCRIPT_DIR"
source .venv/bin/activate
python mainwindow.py
CMDEOF
    chmod +x "$COMMAND_FILE"
    echo "Created Finder shortcut: MGS Undubbed GUI.command"
fi

# Linux: create .desktop file
if [ "$(uname)" = "Linux" ]; then
    DESKTOP_FILE="$SCRIPT_DIR/mgs-undubbed-gui.desktop"
    cat > "$DESKTOP_FILE" <<DSKEOF
[Desktop Entry]
Type=Application
Name=MGS Undubbed GUI
Exec=bash -c 'cd "$SCRIPT_DIR" && source .venv/bin/activate && python mainwindow.py'
Terminal=false
Categories=Utility;
DSKEOF
    chmod +x "$DESKTOP_FILE"
    echo "Created desktop shortcut: mgs-undubbed-gui.desktop"
fi

echo ""
echo "=== Setup complete! ==="
echo "Run the app with:  ./run.sh"
echo "Or activate the venv manually:  source .venv/bin/activate && python mainwindow.py"
