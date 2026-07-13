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
        # Regenerate missing presentation timestamps.
        "-fflags", "+genpts",
        # Preserve every stream's timing exactly, uniformly shifted so the
        # file starts at zero.  Without -copyts, ffmpeg's input discontinuity
        # correction misfires on sparse streams: the audio-description track
        # on Channel 4 HD legitimately stops transmitting during ad breaks
        # (gaps of several minutes), and ffmpeg treated each gap as a
        # timestamp discontinuity, shifting the whole input back by the gap
        # length - then bounced the offset back on the next video packet, over
        # and over ("timestamp discontinuity ... new offset" ping-pong).  The
        # net effect collapsed the AD track's gaps, displacing its narration
        # by minutes, while drifting everything else a microsecond per bounce.
        # -copyts disables that machinery entirely; -start_at_zero keeps the
        # QSF convention of a zero-based output.
        "-copyts",
        "-start_at_zero",
        "-i", source_path,
        # Map every stream from the input so no tracks are silently dropped.
        # Without this ffmpeg's default selection picks only one stream per
        # type, which strips secondary audio tracks (e.g. the HE-AACv2
        # audio-description track on Channel 4 HD recordings).  Two classes of
        # stream are then excluded again deliberately:
        #   * -map -0:d drops data streams (SCTE-35 splice markers, EPG feeds):
        #     they're useless for editing or playback, and their timestamps can
        #     run non-monotonically, which the mpegts muxer rejects outright
        #     ("non monotonically increasing dts ... in stream N") - failing
        #     the whole repair for a stream nobody needs;
        #   * -ignore_unknown skips streams ffmpeg cannot represent at all
        #     (type "unknown"), instead of failing with "Cannot map stream".
        # Video, all audio tracks, and subtitles always survive.
        "-map", "0",
        "-map", "-0:d",
        "-ignore_unknown",
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
