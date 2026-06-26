"""Trim and Copy Source File - byte-for-byte extraction of a portion of a .ts.

A straight byte copy (no re-encode), aligned to 188-byte transport-stream
packet boundaries so the result is a usable transport stream.  Modelled on
VideoReDo's "Create Trimmed File Copy"; its main use is pulling a small sample
clip out of a recording (exactly how sample clips get sent for debugging).

The byte options (From Beginning / To End Of File / Start At MByte) are pure
byte arithmetic snapped to packet boundaries, the way VideoReDo does it.  The
"Use Selection Markers" option maps the editor's IN/OUT frames to byte offsets
via the frame index: the start snaps to the keyframe at or before IN, and the
end to the keyframe after OUT, so - unlike a raw byte cut - the extracted clip
begins on a random-access point and decodes cleanly from its first frame.
"""

import os

import av

from PySide6.QtCore import QThread, Signal


TS_PACKET = 188
MB = 1024 * 1024            # 1 MByte = 1,048,576 bytes, as VideoReDo defines it


def _snap_down(n):
    """Largest packet-boundary offset <= n."""
    return (int(n) // TS_PACKET) * TS_PACKET


def _snap_up(n):
    """Smallest packet-boundary offset >= n."""
    return ((int(n) + TS_PACKET - 1) // TS_PACKET) * TS_PACKET


def plan_byte_range(mode, size, n_mbytes, start_mbyte=0):
    """Return (start, length) in bytes for the byte-oriented options, snapped to
    packet boundaries.

    `mode` is one of 'beginning', 'end', 'at'.  `n_mbytes` is the requested
    output size in MBytes; `start_mbyte` is only used for 'at'.  As VideoReDo
    notes, snapping to a packet boundary can make the result slightly smaller
    than requested.
    """
    size = int(size)
    want = max(0, int(round(float(n_mbytes) * MB)))

    if mode == "beginning":
        start = 0
        length = min(want, size)
    elif mode == "end":
        start = min(_snap_up(max(0, size - want)), size)
        length = size - start
    elif mode == "at":
        start = min(_snap_up(max(0.0, float(start_mbyte)) * MB), size)
        length = min(want, size - start)
    else:
        raise ValueError("unknown mode: %r" % (mode,))

    return start, _snap_down(length)


class MarkerByteMapper:
    """Map editor frames (via their PTS) to byte offsets in a transport stream.

    Opens the source with PyAV and finds packet byte positions by seeking with
    a margin and then scanning forward, which stays reliable even though TS
    seeking is only approximate.  Create one, use it, then call close().
    """

    def __init__(self, path, gop_pts=None):
        self._path = path
        self._container = av.open(path)
        self._v = next(
            (s for s in self._container.streams if s.type == "video"), None
        )
        if self._v is None:
            self.close()
            raise ValueError("no video stream in %s" % (path,))

        self._size = os.path.getsize(path)
        self._start_time = self._v.start_time or 0

        # Seek margin: a couple of GOPs, so a backward seek always lands before
        # the target and we can then scan forward to the exact keyframe.
        gop = gop_pts or self._estimate_gop()
        self._margin = (gop or 0) * 2

    def _estimate_gop(self):
        """Median keyframe spacing (PTS units) from a sample near the start."""
        self._container.seek(self._start_time, stream=self._v, backward=True)
        kf = []
        for pkt in self._container.demux(self._v):
            if pkt.pts is None or not pkt.is_keyframe:
                continue
            kf.append(pkt.pts)
            if len(kf) >= 12:
                break
        gaps = sorted(kf[i + 1] - kf[i] for i in range(len(kf) - 1))
        return gaps[len(gaps) // 2] if gaps else 0

    def start_byte(self, in_pts):
        """Byte offset of the keyframe at or before `in_pts`, snapped down to a
        packet boundary (0 if there's nothing earlier)."""
        seek_to = max(self._start_time, in_pts - self._margin)
        self._container.seek(
            seek_to, stream=self._v, backward=True, any_frame=False
        )
        pos = None
        for pkt in self._container.demux(self._v):
            if pkt.pts is None or pkt.pos is None or not pkt.is_keyframe:
                continue
            if pkt.pts <= in_pts:
                pos = pkt.pos
            else:
                break
        return _snap_down(pos) if pos is not None else 0

    def end_byte(self, out_pts):
        """Byte offset of the first keyframe after `out_pts` (so the GOP holding
        OUT is fully included), snapped to a packet boundary; EOF if none."""
        seek_to = max(self._start_time, out_pts - self._margin)
        self._container.seek(
            seek_to, stream=self._v, backward=True, any_frame=False
        )
        for pkt in self._container.demux(self._v):
            if pkt.pts is None or pkt.pos is None or not pkt.is_keyframe:
                continue
            if pkt.pts > out_pts:
                return _snap_down(pkt.pos)
        return _snap_down(self._size)

    def close(self):
        try:
            self._container.close()
        except Exception:
            pass


def copy_byte_range(src, dst, start, length, chunk=4 * MB,
                    progress_cb=None, is_cancelled=None):
    """Copy `length` bytes starting at `start` from `src` into `dst`.

    Calls progress_cb(bytes_done, total) as it goes and stops early if
    is_cancelled() returns True.  Returns the number of bytes actually written.
    """
    start = int(start)
    length = max(0, int(length))
    written = 0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fin.seek(start)
        while written < length:
            if is_cancelled and is_cancelled():
                break
            todo = min(chunk, length - written)
            data = fin.read(todo)
            if not data:
                break
            fout.write(data)
            written += len(data)
            if progress_cb:
                progress_cb(written, length)
    return written


class TrimCopyWorker(QThread):
    """Runs copy_byte_range on a worker thread so the UI stays responsive."""

    progress = Signal(int, int)       # bytes_done, total
    finished_ok = Signal(int, str)    # bytes_written, output_path
    failed = Signal(str)              # message

    def __init__(self, src, dst, start, length, parent=None):
        super().__init__(parent)
        self._src = src
        self._dst = dst
        self._start = start
        self._length = length
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            written = copy_byte_range(
                self._src, self._dst, self._start, self._length,
                progress_cb=lambda d, t: self.progress.emit(d, t),
                is_cancelled=lambda: self._cancel,
            )
        except Exception as exc:                    # noqa: BLE001 - report it
            self.failed.emit(str(exc))
            return

        if self._cancel:
            # Tidy up the partial output so a cancelled copy leaves nothing.
            try:
                os.remove(self._dst)
            except OSError:
                pass
            self.failed.emit("Cancelled.")
        else:
            self.finished_ok.emit(written, self._dst)
