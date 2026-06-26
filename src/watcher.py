#!/usr/bin/env python3
"""Entry point for the standalone VRD Next Watcher (tray app).

Run it directly:

    .venv/bin/python src/watcher.py

or let it start on login via Settings → "Start the watcher automatically".

It runs as its own process, separate from the editor, so it can quietly scan
recordings in the background whether or not the editor is open.
"""

import sys
import logging

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QLockFile

from watch.tray import WatcherTray
from watch.single_instance import watcher_lock_path


def main():
    # Write to a proper per-day log file (its own 'watcher' family) so a
    # background run launched from the Extras menu or autostart - with no
    # terminal attached - still leaves a trace of what it decided to do.  It
    # goes in the watcher's own config folder, alongside watch.json and the
    # processed/ignore lists, so the tray's "Open config folder" reaches it.
    # The watcher is a separate helper app, so its retention comes from its own
    # config (watch.json), not the editor's settings.
    try:
        from utils.applog import configure_logging
        from config.loader import CONFIG_DIR
        from watch.config import WatchConfig
        log_file = configure_logging(
            str(CONFIG_DIR),
            0,                       # prune by count only, not by age
            False,                   # the watcher's decision log isn't verbose
            app_tag="watcher",
            max_files=WatchConfig.load().log_max_files,
        )
        logging.getLogger("vrd-next.watch").info(
            "Starting VRD Next Watcher"
        )
        if log_file is not None:
            logging.getLogger("vrd-next.watch").info(
                "Logging to %s", log_file
            )
    except Exception:
        # Never let a logging problem stop the watcher starting.
        logging.basicConfig(level=logging.INFO)

    app = QApplication(sys.argv)
    app.setApplicationName("vrd-next-watcher")
    app.setApplicationDisplayName("VRD Next Watcher")
    # Group any Watcher windows under its own launcher (vrd-next-watcher.desktop)
    # rather than appearing as a stray panel icon.
    app.setDesktopFileName("vrd-next-watcher")
    # A tray app must not quit when its (optional) windows close.
    app.setQuitOnLastWindowClosed(False)

    # Single-instance guard: refuse to start a second Watcher - two tray icons
    # and two background scanners would only fight each other.  This covers a
    # duplicate from the editor's Extras menu, autostart, or a terminal alike.
    # The lock is held for the lifetime of this process (kept alive on the
    # stack through app.exec()).
    lock = QLockFile(watcher_lock_path())
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        logging.info("VRD Next Watcher is already running; exiting.")
        return

    tray = WatcherTray(app)        # noqa: F841 - keeps the tray alive
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
