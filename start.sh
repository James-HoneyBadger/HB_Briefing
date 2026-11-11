#!/usr/bin/env bash
# HB Briefing - launcher
#
# Checks the machine over, installs whatever is missing, then opens the GUI.
# Safe to run repeatedly: nothing is reinstalled once it is present.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

VENV_DIR="$PROJECT_DIR/.venv"
REQUIREMENTS="$PROJECT_DIR/requirements.txt"

info() { printf '[INFO] %s\n' "$*"; }
die()  { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
have_module() { "$1" -c "import $2" >/dev/null 2>&1; }

# ---------------------------------------------------------------- interpreter
PYTHON_BIN="$(command -v python3 || true)"
[ -n "$PYTHON_BIN" ] || die "python3 was not found in PATH."

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' \
    || die "Python 3.8 or newer is required (found $("$PYTHON_BIN" --version 2>&1))."

# tkinter ships with Python but is packaged separately on most distributions.
if ! have_module "$PYTHON_BIN" tkinter; then
    info "tkinter is missing; trying the system package manager."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y python3-tk || true
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3-tkinter || true
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm tk || true
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper install -y python3-tk || true
    fi
fi
have_module "$PYTHON_BIN" tkinter || die \
    "tkinter is still unavailable. Debian/Ubuntu: sudo apt install python3-tk | Arch: sudo pacman -S tk | Fedora: sudo dnf install python3-tkinter"

# --------------------------------------------------------------- dependencies
# A project-local virtualenv keeps the system interpreter untouched and avoids
# PEP 668 "externally managed environment" refusals.
# A venv only counts if it has a working pip: `python -m venv` can leave a
# half-built directory behind when ensurepip is unavailable.
venv_usable() { [ -x "$VENV_DIR/bin/python" ] && "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; }

if venv_usable; then
    RUN_PYTHON="$VENV_DIR/bin/python"
else
    [ -d "$VENV_DIR" ] && rm -rf "$VENV_DIR"
    if "$PYTHON_BIN" -m venv "$VENV_DIR" >/dev/null 2>&1 && venv_usable; then
        info "Created virtual environment: $VENV_DIR"
        RUN_PYTHON="$VENV_DIR/bin/python"
    else
        rm -rf "$VENV_DIR"
        info "python3-venv is unavailable; using $PYTHON_BIN directly."
        RUN_PYTHON="$PYTHON_BIN"
    fi
fi

if ! have_module "$RUN_PYTHON" dotenv; then
    info "Installing Python dependencies from requirements.txt."
    if ! "$RUN_PYTHON" -m pip install -q --disable-pip-version-check -r "$REQUIREMENTS"; then
        "$RUN_PYTHON" -m pip install -q --disable-pip-version-check --user -r "$REQUIREMENTS" || true
    fi
fi
have_module "$RUN_PYTHON" dotenv || die \
    "python-dotenv could not be installed. Try: $PYTHON_BIN -m pip install --user -r $REQUIREMENTS"

# ----------------------------------------------------------------- first run
# Seed every local config from its committed example, so a fresh clone works
# immediately while personal files stay out of git.
for pair in ".env:.env.example" "feeds.txt:feeds.txt.example" \
            "watchlist.txt:watchlist.txt.example" "topics.txt:topics.txt.example"; do
    target="${pair%%:*}"
    example="${pair##*:}"
    if [ ! -f "$PROJECT_DIR/$target" ] && [ -f "$PROJECT_DIR/$example" ]; then
        cp "$PROJECT_DIR/$example" "$PROJECT_DIR/$target"
        info "Created $target from $example"
    fi
done

info "Starting HB Briefing (interpreter: $RUN_PYTHON)"
exec "$RUN_PYTHON" "$PROJECT_DIR/hbnscan_gui.py" "$@"
