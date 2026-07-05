#!/usr/bin/env bash
#
# One-step setup for VRD Next on Debian/Ubuntu/Mint.
#
#   ./install-linux.sh
#
# It will, in order:
#   1. install the system packages VRD Next needs (Python, ffmpeg, mkvmerge)
#      via apt, asking for sudo only if something is actually missing;
#   2. create a virtual environment in the project root (.venv) and install the
#      Python dependencies from requirements.txt into it;
#   3. add "VRD Next" and "VRD Next Watcher" to your applications menu, pointing
#      at that virtual environment (by calling install-desktop-entries.sh).
#
# Re-running it is safe: existing packages are left alone and the venv is
# reused.  Nothing is installed system-wide except the apt packages.
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"                    # the src/ directory
ROOT="$(cd "$SRC/.." && pwd)"                    # the project root (holds src/)
VENV="$ROOT/.venv"
REQ="$ROOT/requirements.txt"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }    # bold heading
info() { printf '  %s\n' "$*"; }

# --- 1. system packages ---------------------------------------------------
say "1/3  System packages"

if ! command -v apt-get >/dev/null 2>&1; then
    info "This script uses apt (Debian/Ubuntu/Mint).  On another distribution,"
    info "install these yourself, then re-run to finish the Python setup:"
    info "  python3, python3-venv, python3-pip, ffmpeg, mkvtoolnix"
else
    # Map required commands to the apt package that provides them.
    declare -A NEED=(
        [python3]=python3
        [ffmpeg]=ffmpeg
        [mkvmerge]=mkvtoolnix
    )
    missing=()
    for cmd in "${!NEED[@]}"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("${NEED[$cmd]}")
    done
    # python3-venv / python3-pip provide no command of their own to probe.
    # NB: "import venv" can succeed while ensurepip is missing - and ensurepip is
    # exactly what "python3 -m venv" needs to bootstrap pip.  So probe ensurepip,
    # and pull in the version-specific python3.X-venv package that newer Ubuntus
    # (e.g. 26.04 with Python 3.14) require, using whichever names actually exist.
    if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
        pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' \
                 2>/dev/null)"
        for pkg in "python3-venv" "python3.${pyver}-venv"; do
            if apt-cache show "$pkg" >/dev/null 2>&1; then
                missing+=("$pkg")
            fi
        done
    fi
    python3 -c "import pip" >/dev/null 2>&1 || missing+=("python3-pip")

    if [ "${#missing[@]}" -eq 0 ]; then
        info "All present (python3, ffmpeg, mkvmerge, venv, pip)."
    else
        # De-duplicate.
        mapfile -t missing < <(printf '%s\n' "${missing[@]}" | sort -u)
        info "Installing: ${missing[*]}"
        sudo apt-get update
        sudo apt-get install -y "${missing[@]}"
    fi
fi

# --- 2. virtual environment ----------------------------------------------
say "2/3  Python environment"

if [ ! -d "$VENV" ]; then
    info "Creating virtual environment: $VENV"
    if ! python3 -m venv "$VENV"; then
        info "Virtual environment creation failed (see the message above)."
        info "Install the package it names (usually 'sudo apt install python3-venv'"
        info "or 'python3.X-venv'), delete the incomplete $VENV folder, then re-run."
        exit 1
    fi
else
    info "Reusing existing virtual environment: $VENV"
fi

# Belt and braces: make sure the interpreter is actually there before we use it.
if [ ! -x "$VENV/bin/python" ]; then
    info "The virtual environment is missing its interpreter ($VENV/bin/python)."
    info "Delete the $VENV folder and re-run."
    exit 1
fi

info "Installing Python dependencies (this can take a minute)…"
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null
if [ -f "$REQ" ]; then
    "$VENV/bin/python" -m pip install -r "$REQ"
else
    info "requirements.txt not found at $REQ - installing the known set instead."
    "$VENV/bin/python" -m pip install PySide6 av numpy bitstring tqdm
fi

# --- 3. menu entries ------------------------------------------------------
say "3/3  Application menu entries"
if [ -x "$HERE/install-desktop-entries.sh" ]; then
    "$HERE/install-desktop-entries.sh" "$VENV/bin/python"
else
    bash "$HERE/install-desktop-entries.sh" "$VENV/bin/python"
fi

say "Done."
info "Launch VRD Next from your applications menu, or run it directly with:"
info "  $VENV/bin/python $SRC/main.py"
info "The Watcher is \"VRD Next Watcher\" in the menu, or:"
info "  $VENV/bin/python $SRC/watcher.py"
