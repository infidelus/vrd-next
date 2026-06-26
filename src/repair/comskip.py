"""
Run Comskip on a video to detect commercial breaks, on a worker thread.

Comskip decodes the whole file looking for commercials, which can take a few
minutes on an HD recording, so it runs in the background with progress.  We
ask it to write an EDL (the simplest output) into a temporary directory and
hand that path back; the caller parses it and populates the timeline.

Comskip prints progress to stdout in the form of a percentage; we parse that
for a progress bar.
"""

import os
import re
import shutil
import subprocess
import tempfile

from PySide6.QtCore import (
    QThread,
    Signal,
)


class ComskipError(Exception):
    pass


# Comskip emits lines containing a percentage as it scans, e.g.
#   "  12.34%   ..."  (the exact surrounding text varies by build).
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def run_comskip(binary, ini, source_path, out_dir,
                progress_cb=None, cancel_cb=None):
    """Run Comskip on source_path, writing output into out_dir.

    Returns the path to the produced .edl file.  Raises ComskipError on
    failure.  progress_cb(percent) is called with 0-100 as scanning proceeds.
    """
    if not binary or not os.path.isfile(binary):
        raise ComskipError(
            "The Comskip program hasn't been set. Add it in "
            "Settings > Folders."
        )

    cmd = [binary]
    if ini and os.path.isfile(ini):
        cmd.append(f"--ini={ini}")

    # Write all output into our temp dir so we never litter the user's folders.
    cmd.append(f"--output={out_dir}")
    cmd.append(source_path)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise ComskipError(f"Could not start Comskip:\n{exc}")

    last_pct = -1
    if proc.stdout is not None:
        for line in proc.stdout:
            if cancel_cb is not None and cancel_cb():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise ComskipError("Commercial detection cancelled.")

            if progress_cb:
                m = _PERCENT_RE.search(line)
                if m:
                    try:
                        pct = int(float(m.group(1)))
                    except ValueError:
                        pct = last_pct
                    pct = max(0, min(99, pct))
                    if pct != last_pct:
                        last_pct = pct
                        progress_cb(pct)

    proc.wait()

    if proc.returncode not in (0, 1):
        # Comskip returns 0 when commercials were found and 1 when none were
        # found; both are success for our purposes.  Anything else is an error.
        raise ComskipError(
            f"Comskip exited with code {proc.returncode}."
        )

    # Find the EDL it wrote (named after the source file).
    base = os.path.splitext(os.path.basename(source_path))[0]
    edl_path = os.path.join(out_dir, base + ".edl")

    if not os.path.isfile(edl_path):
        # Some builds/configs may not emit an EDL if no commercials were found.
        # Fall back to any .edl in the output dir.
        candidates = [
            os.path.join(out_dir, f)
            for f in os.listdir(out_dir)
            if f.lower().endswith(".edl")
        ]
        if candidates:
            edl_path = candidates[0]
        else:
            raise ComskipError(
                "Comskip produced no EDL output (it may have found no "
                "commercials, or the .ini disables EDL output)."
            )

    if progress_cb:
        progress_cb(100)

    return edl_path


class ComskipWorker(QThread):

    finished_ok = Signal(str)     # emits the EDL path
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, binary, ini, source_path, parent=None):
        super().__init__(parent)
        self.binary = binary
        self.ini = ini
        self.source_path = source_path
        self._cancel = False
        self._out_dir = tempfile.mkdtemp(prefix="vrd-next-comskip-")

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            edl = run_comskip(
                self.binary,
                self.ini,
                self.source_path,
                self._out_dir,
                progress_cb=self.progress.emit,
                cancel_cb=lambda: self._cancel,
            )
            if not self._cancel:
                self.finished_ok.emit(edl)
        except Exception as exc:
            self.failed.emit(str(exc))

    def cleanup(self):
        """Remove the temporary output directory."""
        try:
            shutil.rmtree(self._out_dir, ignore_errors=True)
        except Exception:
            pass
