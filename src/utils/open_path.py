"""Open a file or folder in the system's default handler, quietly.

``QDesktopServices.openUrl`` launches the file manager as a child process that
inherits our stdout/stderr, so its own warnings - and those of anything IT
launches (e.g. a text editor opening a .log) - spill into our console.  We
launch the opener ourselves instead, with its output sent to devnull and in a
detached session, so the console stays clean.

Best-effort and cross-platform: xdg-open on Linux, ``open`` on macOS,
``os.startfile`` on Windows.
"""

import os
import sys
import subprocess


def open_path(path):
    """Open ``path`` (a file or folder) in the OS default handler, suppressing
    the handler's console output.  Returns True if a launch was attempted."""
    path = str(path)

    try:
        if sys.platform.startswith("win"):
            # Windows: no subprocess needed; ShellExecute handles its own I/O.
            os.startfile(path)  # type: ignore[attr-defined]
            return True

        if sys.platform == "darwin":
            opener = ["open", path]
        else:
            opener = ["xdg-open", path]

        subprocess.Popen(
            opener,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False
