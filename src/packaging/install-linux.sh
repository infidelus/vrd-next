#!/usr/bin/env bash
#
# One-step setup for VRD Next on Debian/Ubuntu/Mint.
#
#   ./install-linux.sh
#
# It will, in order:
#   1. install the system packages VRD Next needs (Python, venv support,
#      ffmpeg, mkvmerge, and the Qt runtime libraries) via apt, asking for
#      sudo only if something is actually missing;
#   2. create a self-contained virtual environment in the project root
#      (.venv) and install the Python dependencies into it;
#   3. add "VRD Next" and "VRD Next Watcher" to your applications menu,
#      pointing at the environment's Python (via install-desktop-entries.sh);
#   4. check the installed dependencies actually import, and print the exact
#      error if they don't.
#
# Re-running it is safe: existing system packages are left alone and the venv
# is reused.  Re-run it (or just install-desktop-entries.sh) after moving the
# project to a new folder, so the menu entries pick up the new absolute paths.
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"                    # the src/ directory
ROOT="$(cd "$SRC/.." && pwd)"                    # the project root (holds src/)
REQ="$ROOT/requirements.txt"
VENV="$ROOT/.venv"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }    # bold heading
info() { printf '  %s\n' "$*"; }

# --- 1. system packages ---------------------------------------------------
say "1/4  System packages"

if ! command -v apt-get >/dev/null 2>&1; then
    info "This script uses apt (Debian/Ubuntu/Mint).  On another distribution,"
    info "install these yourself, then re-run to finish the setup:"
    info "  python3, python3-venv, ffmpeg, mkvtoolnix, libxcb-cursor0"
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
    # venv support isn't a command; probe the module on the system Python.
    python3 -c "import venv, ensurepip" >/dev/null 2>&1 || missing+=("python3-venv")
    # Qt runtime libraries the PySide6 wheels need but a fresh install can
    # lack.  libxcb-cursor0 in particular is required by Qt 6.5+ - without it
    # the application dies silently when launched from the menu.
    for pkg in libxcb-cursor0 libegl1 libxkbcommon-x11-0; do
        if ! dpkg -s "$pkg" >/dev/null 2>&1; then
            if apt-cache show "$pkg" >/dev/null 2>&1; then
                missing+=("$pkg")
            fi
        fi
    done

    if [ "${#missing[@]}" -eq 0 ]; then
        info "All present (python3, venv, ffmpeg, mkvmerge, Qt libraries)."
    else
        # De-duplicate.
        mapfile -t missing < <(printf '%s\n' "${missing[@]}" | sort -u)
        info "Installing: ${missing[*]}"
        APT="apt-get"
        [ "$(id -u)" -ne 0 ] && APT="sudo apt-get"
        # A failed update (say, one stale third-party repository) shouldn't
        # abort the install - the packages we need are in the main archives.
        $APT update || info "apt-get update reported problems; carrying on."
        $APT install -y "${missing[@]}"
    fi
fi

# --- 2. virtual environment + Python dependencies -------------------------
say "2/4  Virtual environment"

# VRD Next runs on Python 3.12 (Linux Mint 22's system Python).  Prefer an
# explicit python3.12 if present, so the venv is built on it even where the
# default python3 is something else; fall back to python3 otherwise.
BUILD_PY="$(command -v python3.12 || command -v python3 || true)"
if [ -z "$BUILD_PY" ]; then
    info "No python3 found on PATH - install Python 3.12 and re-run."
    exit 1
fi
PY_VER="$("$BUILD_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

if [ ! -x "$VENV/bin/python" ]; then
    info "Creating $VENV on Python $PY_VER…"
    "$BUILD_PY" -m venv "$VENV"
else
    have="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    info "Reusing existing $VENV (Python $have)."
    if [ "$have" != "$PY_VER" ]; then
        info "(To rebuild it on Python $PY_VER instead: rm -rf \"$VENV\" and re-run.)"
    fi
fi

PY="$VENV/bin/python"

info "Installing the Python dependencies into the environment…"
"$PY" -m pip install --upgrade pip

# Install the dependencies.  Prefer requirements.txt, fall back to the known
# set.  We do NOT hide pip's output or let a failure pass silently - a partial
# install (an interrupted download of the large PySide6 wheels, say) is exactly
# what leaves a venv that looks present but can't import PySide6.
install_deps() {
    if [ -f "$REQ" ]; then
        "$PY" -m pip install -r "$REQ"
    else
        info "requirements.txt not found at $REQ - installing the known set instead."
        "$PY" -m pip install PySide6 av numpy bitstring tqdm
    fi
}
install_deps

# Verify the dependencies actually import.  A reused venv from a previously
# interrupted install can have some packages but not others, and pip won't
# reinstall what it thinks is already there - so if the import check fails,
# force a clean reinstall before giving up.
if ! "$PY" -c 'import PySide6, av, numpy, bitstring, tqdm' >/dev/null 2>&1; then
    info "Some dependencies are missing or incomplete - reinstalling them cleanly…"
    if [ -f "$REQ" ]; then
        "$PY" -m pip install --force-reinstall --no-cache-dir -r "$REQ"
    else
        "$PY" -m pip install --force-reinstall --no-cache-dir \
            PySide6 av numpy bitstring tqdm
    fi
fi

# --- 3. menu entries ------------------------------------------------------
say "3/4  Application menu entries"
bash "$HERE/install-desktop-entries.sh" "$PY"

# --- 4. verification --------------------------------------------------------
say "4/4  Checking the installation"
if err="$("$PY" -c 'import PySide6, av, numpy, bitstring, tqdm' 2>&1)"; then
    info "All Python dependencies import cleanly."
else
    info "A dependency didn't install correctly:"
    printf '%s\n' "$err" | sed 's/^/    /'
    info "Try re-running this script; if it still fails, please report the"
    info "text above at https://github.com/infidelus/vrd-next/issues"
    exit 1
fi

say "Done."
info "Launch VRD Next from your applications menu, or run it directly with:"
info "  $PY $SRC/main.py"
info "The Watcher is \"VRD Next Watcher\" in the menu, or:"
info "  $PY $SRC/watcher.py"
info "If the menu entry ever fails to launch, run the command above in a"
info "terminal - the error it prints says what's wrong."
