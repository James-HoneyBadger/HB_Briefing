#!/usr/bin/env bash
# HB Briefing v1.0.0 - Linux Desktop & Autostart Installer
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(command -v python3 || true)"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="hb-briefing.desktop"

if [ -z "$PYTHON_BIN" ]; then
    echo "[ERROR] python3 not found in PATH."
    exit 1
fi

for module in tkinter dotenv; do
    if ! "$PYTHON_BIN" -c "import $module" >/dev/null 2>&1; then
        case "$module" in
            tkinter) echo "[WARN] tkinter missing (Debian: python3-tk, Arch: tk, Fedora: python3-tkinter)." ;;
            dotenv)  echo "[WARN] python-dotenv missing: $PYTHON_BIN -m pip install -r \"$PROJECT_DIR/requirements.txt\"" ;;
        esac
    fi
done

chmod +x "$PROJECT_DIR/hbnscan.py" "$PROJECT_DIR/hbnscan_gui.py"

mkdir -p "$AUTOSTART_DIR" "$APPS_DIR"

cat > "$APPS_DIR/$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=HB Briefing
Comment=Autonomous RSS & DeepSeek AI Intelligence Monitor
Exec="${PYTHON_BIN}" "${PROJECT_DIR}/hbnscan_gui.py"
TryExec=$PYTHON_BIN
Path=$PROJECT_DIR
Icon=utilities-system-monitor
Terminal=false
Categories=Network;Utility;Development;
StartupNotify=true
X-GNOME-Autostart-enabled=true
EOF

cp "$APPS_DIR/$DESKTOP_FILE" "$AUTOSTART_DIR/$DESKTOP_FILE"

# Drop the launcher written by the pre-rename installer, if present.
for stale in "$APPS_DIR/hb-news-scan.desktop" "$AUTOSTART_DIR/hb-news-scan.desktop"; do
    if [ -f "$stale" ]; then
        rm -f "$stale"
        echo "[INFO] Removed stale launcher: $stale"
    fi
done

echo "[OK] Installed application menu launcher: $APPS_DIR/$DESKTOP_FILE"
echo "[OK] Installed login autostart entry:     $AUTOSTART_DIR/$DESKTOP_FILE"
echo "[INFO] Resolved project path dynamically: $PROJECT_DIR"
