#!/usr/bin/env bash
#
# Install VRD Next application-menu entries (editor + watcher) for the current
# user, with correct absolute paths and the app icon.
#
# Usage:
#   ./install-desktop-entries.sh [python]
#
# Pass a Python interpreter as the first argument to pin a specific one (the
# project's .venv/bin/python is what install-linux.sh passes); otherwise
# python3 from PATH is used.  The entries embed absolute paths, so re-run this
# after moving the project to a new location.
#
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"                 # the src/ directory
PY="${1:-$(command -v python3)}"
ICON="$SRC/assets/app_icon.svg"
APPS="$HOME/.local/share/applications"

mkdir -p "$APPS"

cat > "$APPS/vrd-next.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=VRD Next
GenericName=Video Cutter
Comment=Frame-accurate cutter for broadcast recordings
Exec="$PY" "$SRC/main.py" %f
Icon=$ICON
Terminal=false
StartupWMClass=vrd-next
Categories=AudioVideo;Video;AudioVideoEditing;
MimeType=video/mp2t;video/x-matroska;
StartupNotify=true
EOF

cat > "$APPS/vrd-next-watcher.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=VRD Next Watcher
GenericName=Recording Watcher
Comment=Scan recordings for commercials and prepare cut projects
Exec="$PY" "$SRC/watcher.py"
Icon=$ICON
Terminal=false
StartupWMClass=vrd-next-watcher
Categories=AudioVideo;Video;
StartupNotify=false
EOF

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi

echo "Installed menu entries:"
echo "  $APPS/vrd-next.desktop"
echo "  $APPS/vrd-next-watcher.desktop"
echo "Python: $PY"
echo "Icon:   $ICON"
echo
echo "They should appear in your applications menu shortly.  Re-run this script"
echo "if you move the project (the entries embed absolute paths)."
