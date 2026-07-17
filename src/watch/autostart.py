"""Launch the watcher on login - per platform.

Exposes a small platform-agnostic API (is_enabled / enable / disable /
set_enabled) that the Settings UI calls without caring which OS it's on.

- Linux: an XDG .desktop file in ~/.config/autostart, honoured by every
  mainstream desktop (Cinnamon - what Linux Mint uses - GNOME, KDE, XFCE...).
- Windows: a .cmd launcher in the per-user Startup folder, which Windows runs
  at login.  A .cmd (rather than a .lnk shortcut) is used deliberately so we
  need no extra dependency like pywin32; it launches pythonw.exe so there's no
  lingering console window.
- macOS: not implemented (untested platform); the functions report "not
  enabled" and do nothing, so the UI degrades gracefully.
"""

import os
import sys


def _entry_script():
    """Absolute path to watcher.py (at the src root, one level up from here)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, os.pardir, "watcher.py"))


# --------------------------------------------------------------------------- #
# Linux (XDG autostart)
# --------------------------------------------------------------------------- #

_LINUX_DIR = os.path.join(os.path.expanduser("~"), ".config", "autostart")
_LINUX_FILE = os.path.join(_LINUX_DIR, "vrd-next-watcher.desktop")


def _linux_launch_command():
    return f'"{sys.executable}" "{_entry_script()}"'


def _linux_icon_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(
        os.path.join(here, os.pardir, "assets", "app_icon.svg"))


def _linux_is_enabled():
    return os.path.isfile(_LINUX_FILE)


def _linux_enable():
    try:
        os.makedirs(_LINUX_DIR, exist_ok=True)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=VRD Next Watcher\n"
            "Comment=Scan recordings for commercials and prepare cut projects\n"
            f"Exec={_linux_launch_command()}\n"
            f"Icon={_linux_icon_path()}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        with open(_LINUX_FILE, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except OSError:
        return False


def _linux_disable():
    try:
        if os.path.isfile(_LINUX_FILE):
            os.remove(_LINUX_FILE)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Windows (Startup folder .cmd)
# --------------------------------------------------------------------------- #

def _windows_startup_dir():
    """The per-user Startup folder.  Uses APPDATA (always set on Windows);
    falls back to the well-known relative path if for some reason it isn't."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        appdata = os.path.join(os.path.expanduser("~"),
                               "AppData", "Roaming")
    return os.path.join(appdata, "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


def _windows_file():
    return os.path.join(_windows_startup_dir(), "vrd-next-watcher.cmd")


def _windows_pythonw():
    """pythonw.exe next to the running interpreter, so the watcher starts with
    no console window.  Falls back to the normal interpreter if pythonw isn't
    found (the console flash is harmless, just less tidy)."""
    exe = sys.executable
    base = os.path.dirname(exe)
    cand = os.path.join(base, "pythonw.exe")
    return cand if os.path.isfile(cand) else exe


def _windows_is_enabled():
    return os.path.isfile(_windows_file())


def _windows_enable():
    try:
        os.makedirs(_windows_startup_dir(), exist_ok=True)
        pyw = _windows_pythonw()
        entry = _entry_script()
        # `start "" ...` launches detached and lets the .cmd exit at once, so
        # any console window closes immediately rather than lingering.
        content = (
            "@echo off\r\n"
            f'start "" "{pyw}" "{entry}"\r\n'
        )
        with open(_windows_file(), "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return True
    except OSError:
        return False


def _windows_disable():
    try:
        f = _windows_file()
        if os.path.isfile(f):
            os.remove(f)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Platform-agnostic API
# --------------------------------------------------------------------------- #

def supported():
    """Whether start-on-login is implemented on this platform (so the UI can
    disable/hide the option on, e.g., macOS rather than offer a dead toggle)."""
    return sys.platform.startswith("linux") or os.name == "nt"


def is_enabled():
    if os.name == "nt":
        return _windows_is_enabled()
    if sys.platform.startswith("linux"):
        return _linux_is_enabled()
    return False


def enable():
    """Write the autostart entry.  Returns True on success."""
    if os.name == "nt":
        return _windows_enable()
    if sys.platform.startswith("linux"):
        return _linux_enable()
    return False


def disable():
    """Remove the autostart entry.  Returns True if it's gone afterwards."""
    if os.name == "nt":
        return _windows_disable()
    if sys.platform.startswith("linux"):
        return _linux_disable()
    return True


def set_enabled(on):
    return enable() if on else disable()
