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
MimeType=video/mp2t;video/x-matroska;video/mp4;video/mpeg;video/quicktime;video/x-msvideo;application/x-vrd-project;
StartupNotify=true
EOF

# Register a MIME type for VideoReDo project files (.vprj) so the file manager
# offers "Open with VRD Next" on them and shows the app icon.  Without this the
# desktop has no idea what a .vprj is.
MIME="$HOME/.local/share/mime"
mkdir -p "$MIME/packages"
cat > "$MIME/packages/vrd-next.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-vrd-project">
    <comment>VideoReDo project</comment>
    <glob pattern="*.vprj"/>
    <glob pattern="*.VPRJ"/>
    <generic-icon name="application-x-vrd-project"/>
  </mime-type>
</mime-info>
EOF
if command -v update-mime-database >/dev/null 2>&1; then
    update-mime-database "$MIME" >/dev/null 2>&1 || true
fi

# Install the project-file icon into the user's icon theme under the MIME-type
# name, so file managers show it on .vprj files.  The scalable SVG covers all
# sizes; we drop it into hicolor/scalable/mimetypes where the desktop looks.
ICONS="$HOME/.local/share/icons/hicolor/scalable/mimetypes"
if [ -f "$SRC/assets/project_icon.svg" ]; then
    mkdir -p "$ICONS"
    cp "$SRC/assets/project_icon.svg" "$ICONS/application-x-vrd-project.svg"
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" \
            >/dev/null 2>&1 || true
    fi
fi

# We define application/x-vrd-project (above) with the *.vprj glob, so the
# desktop entry's MimeType line is enough to make VRD Next appear in the file
# manager's "Open With" list for .vprj files.  We deliberately do NOT call
# `xdg-mime default` here: that would make VRD Next the sole handler and remove
# whatever the user already had (e.g. a text editor for editing .vprj by hand).
# Offering it as a choice is friendlier - the user can set it as default
# themselves if they want to.  (update-desktop-database is run once below,
# after the watcher entry is written too.)

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
