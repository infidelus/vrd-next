"""
Quick Stream Fix - re-multiplex a stream to repair its timestamps.

Like VideoReDo's QuickStream Fix: it copies the streams (no re-encode, so no
quality loss) while regenerating PTS/DTS and container timing.  Broadcast
captures - especially DVB and HD - sometimes carry non-sequential or offset
timestamps that cause errors when editing or saving; remuxing through ffmpeg
with fresh timestamps clears most of these up.

This does NOT cut anything; it produces a clean copy of the whole file.
Runs on a worker thread.
"""

import shutil
import subprocess

from PySide6.QtCore import (
    QThread,
    Signal,
)


class StreamFixError(Exception):
    pass


def _probe_duration_us(path):
    """Total duration in microseconds, or 0 if unknown."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True,
        ).stdout.strip()
        return int(float(out) * 1_000_000)
    except Exception:
        return 0


def quick_stream_fix(
        source_path,
        out_path,
        cancel_cb=None,
        progress_cb=None,
):
    """Remux source_path to out_path, regenerating timestamps (stream copy).

    If progress_cb is given it is called with an integer percentage (0-100)
    as the remux proceeds.
    """
    if shutil.which("ffmpeg") is None:
        raise StreamFixError("ffmpeg was not found on PATH.")

    total_us = _probe_duration_us(source_path) if progress_cb else 0

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        # Regenerate presentation timestamps and reset mux timing so the
        # output starts cleanly at zero with sequential stamps.
        "-fflags", "+genpts",
        "-i", source_path,
        "-c", "copy",
        "-muxpreload", "0",
        "-muxdelay", "0",
        # Machine-readable progress on stdout.
        "-progress", "pipe:1",
        "-nostats",
        out_path,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Read progress lines as they arrive.  ffmpeg writes key=value lines; the
    # one we want is out_time_us (microseconds processed so far).
    last_pct = -1
    if proc.stdout is not None:
        for line in proc.stdout:
            if cancel_cb is not None and cancel_cb():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise StreamFixError("Quick Stream Fix cancelled.")

            if progress_cb and total_us > 0 and line.startswith("out_time_us="):
                try:
                    done_us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                pct = max(0, min(99, int(100 * done_us / total_us)))
                if pct != last_pct:
                    last_pct = pct
                    progress_cb(pct)

    proc.wait()

    if proc.returncode != 0:
        err = proc.stderr.read() if proc.stderr else ""
        raise StreamFixError(
            f"Quick Stream Fix failed:\n{err.strip()}"
        )

    if progress_cb:
        progress_cb(100)

    return out_path


class StreamFixWorker(QThread):

    finished_ok = Signal(str)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(
            self,
            source_path,
            out_path,
            parent=None,
    ):
        super().__init__(parent)

        self.source_path = source_path
        self.out_path = out_path
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            quick_stream_fix(
                self.source_path,
                self.out_path,
                cancel_cb=lambda: self._cancel,
                progress_cb=self.progress.emit,
            )

            if not self._cancel:
                self.finished_ok.emit(self.out_path)

        except Exception as exc:
            self.failed.emit(str(exc))
