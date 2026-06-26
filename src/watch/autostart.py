"""Linux XDG autostart: launch the watcher on login.

Writes (or removes) a .desktop file in ~/.config/autostart.  This is the
standard mechanism honoured by all the mainstream Linux desktops (Cinnamon -
which Linux Mint uses - GNOME, KDE, XFCE, ...).
"""

import os
import sys

AUTOSTART_DIR = os.path.join(
    os.path.expanduser("~"), ".config", "autostart"
)
DESKTOP_FILE = os.path.join(AUTOSTART_DIR, "vrd-next-watcher.desktop")


def _launch_command():
    """The command that starts the watcher: the current interpreter running
    this package's entry script.  Resolved at enable-time so it matches however
    the user launched it (e.g. their virtualenv's python)."""
    # watcher.py lives at the src root, one level up from this package.
    here = os.path.dirname(os.path.abspath(__file__))
    entry = os.path.normpath(os.path.join(here, os.pardir, "watcher.py"))
    return f'"{sys.executable}" "{entry}"'


def _icon_path():
    """Absolute path to the application icon, so Startup Applications and the
    login session show the proper icon rather than a generic placeholder."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(
        os.path.join(here, os.pardir, "assets", "app_icon.svg")
    )


def is_enabled():
    return os.path.isfile(DESKTOP_FILE)


def enable():
    """Write the autostart entry.  Returns True on success."""
    try:
        os.makedirs(AUTOSTART_DIR, exist_ok=True)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=VRD Next Watcher\n"
            "Comment=Scan recordings for commercials and prepare cut projects\n"
            f"Exec={_launch_command()}\n"
            f"Icon={_icon_path()}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        with open(DESKTOP_FILE, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except OSError:
        return False


def disable():
    """Remove the autostart entry.  Returns True if it's gone afterwards."""
    try:
        if os.path.isfile(DESKTOP_FILE):
            os.remove(DESKTOP_FILE)
        return True
    except OSError:
        return False


def set_enabled(on):
    return enable() if on else disable()
