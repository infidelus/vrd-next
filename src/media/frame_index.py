"""
FrameIndex - a lightweight, frame-exact index of a video stream.

Built by demuxing packets (no pixel decoding), so it is fast even on long
recordings.  It records, in display (PTS) order:

  - every frame's PTS                (so frame N  <->  index N, exactly)
  - which PTS values are keyframes   (so we can seek reliably)
  - the source fps, dimensions, codec, time_base, start PTS, interlaced flag
    (so nothing is hard-coded to 25fps / PAL)

The index is cached to disk keyed by the file's path + mtime + size, so
re-opening the same recording is instant.

Building runs on a worker thread (FrameIndexBuilder) so the UI never blocks.
"""

import bisect
import hashlib
import json
import os
import struct
import time
from array import array
from pathlib import Path

import av

from PySide6.QtCore import (
    QThread,
    Signal,
)


CACHE_DIR = (
    Path.home()
    / ".cache"
    / "vrd-next"
    / "index"
)

CACHE_VERSION = 3


class FrameIndex:

    def __init__(
            self,
            pts,
            keyframe_pts,
            fps,
            width,
            height,
            codec,
            time_base,
            start_pts,
            interlaced,
    ):

        # Display-ordered frame PTS as a packed int64 array.
        self.pts = pts

        # Sorted list of keyframe PTS.
        self.keyframe_pts = keyframe_pts

        self.fps = fps
        self.width = width
        self.height = height
        self.codec = codec

        # (numerator, denominator) of the stream time_base.
        self.time_base = time_base

        self.start_pts = start_pts
        self.interlaced = interlaced

        # Median keyframe spacing in PTS units - used to size the seek margin.
        gaps = [
            keyframe_pts[i + 1] - keyframe_pts[i]
            for i in range(len(keyframe_pts) - 1)
        ]

        gaps.sort()

        self.median_gop_pts = (
            gaps[len(gaps) // 2]
            if gaps
            else 0
        )

    @property
    def frame_count(self):
        return len(self.pts)

    def pts_of(self, index):
        return self.pts[index]

    def index_of_pts(self, pts):
        """Nearest frame index for a given PTS."""
        i = bisect.bisect_left(self.pts, pts)

        if i >= len(self.pts):
            return len(self.pts) - 1

        if i > 0 and (pts - self.pts[i - 1]) <= (self.pts[i] - pts):
            return i - 1

        return i

    def seconds_of(self, index):
        """Container-relative time of a frame, in seconds."""
        num, den = self.time_base

        return (self.pts[index] - self.start_pts) * num / den

    def index_of_seconds(self, seconds):
        """Nearest frame index for a container-relative time in seconds.

        The inverse of seconds_of().  Used to re-map scene boundaries onto a
        differently-indexed copy of the same content (e.g. after a Quick
        Stream Fix renumbers the timestamps): convert each boundary to seconds
        on the old index, then back to a frame number on the new index, so the
        same moment in the video is kept rather than the same frame number.
        """
        num, den = self.time_base
        target_pts = self.start_pts + round(seconds * den / num)
        return self.index_of_pts(target_pts)

    # ------------------------------------------------------------------ #
    # Disk cache
    # ------------------------------------------------------------------ #

    def save(self, path):

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        header = {
            "version": CACHE_VERSION,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "codec": self.codec,
            "time_base": list(self.time_base),
            "start_pts": self.start_pts,
            "interlaced": self.interlaced,
            "n_pts": len(self.pts),
            "n_kf": len(self.keyframe_pts),
        }

        with open(path, "wb") as f:

            blob = json.dumps(header).encode("utf-8")

            f.write(
                struct.pack("<I", len(blob))
            )

            f.write(blob)

            array("q", self.pts).tofile(f)

            array("q", self.keyframe_pts).tofile(f)

    @staticmethod
    def load(path):

        with open(path, "rb") as f:

            (length,) = struct.unpack(
                "<I",
                f.read(4),
            )

            header = json.loads(
                f.read(length).decode("utf-8")
            )

            if header.get("version") != CACHE_VERSION:
                raise ValueError("cache version mismatch")

            pts = array("q")
            pts.fromfile(f, header["n_pts"])

            kf = array("q")
            kf.fromfile(f, header["n_kf"])

        return FrameIndex(
            pts=pts,
            keyframe_pts=list(kf),
            fps=header["fps"],
            width=header["width"],
            height=header["height"],
            codec=header["codec"],
            time_base=tuple(header["time_base"]),
            start_pts=header["start_pts"],
            interlaced=header["interlaced"],
        )


# ---------------------------------------------------------------------- #
# Building
# ---------------------------------------------------------------------- #

def _probe_interlaced(path):
    """Decode a single frame to learn whether the stream is interlaced."""
    try:
        container = av.open(str(path))
        stream = container.streams.video[0]

        for frame in container.decode(stream):
            result = bool(
                getattr(frame, "interlaced_frame", False)
            )
            container.close()
            return result

        container.close()

    except Exception:
        pass

    return False


def build_frame_index(
        path,
        progress_cb=None,
        cancel_cb=None,
):
    """
    Build a FrameIndex by demuxing packets (no pixel decode).

    progress_cb(frame_count) is called periodically.
    cancel_cb() -> bool lets a worker abort early.
    """
    container = av.open(str(path))
    stream = container.streams.video[0]

    pts_list = []
    keyframe_pts = []

    for packet in container.demux(stream):

        if cancel_cb is not None and cancel_cb():
            container.close()
            return None

        if packet.pts is None:
            continue

        pts_list.append(packet.pts)

        if packet.is_keyframe:
            keyframe_pts.append(packet.pts)

        if (
                progress_cb is not None
                and len(pts_list) % 2000 == 0
        ):
            progress_cb(len(pts_list))

    fps = (
        float(stream.average_rate)
        if stream.average_rate
        else 25.0
    )

    cc = stream.codec_context

    time_base = (
        stream.time_base.numerator,
        stream.time_base.denominator,
    )

    start_pts = (
        stream.start_time
        if stream.start_time is not None
        else (pts_list[0] if pts_list else 0)
    )

    width = cc.width
    height = cc.height
    codec = cc.name

    container.close()

    pts_list.sort()
    keyframe_pts = sorted(set(keyframe_pts))

    # --- Field-coded (PAFF) detection and collapse ---------------------- #
    #
    # Some broadcast H.264 is field-coded: each displayable frame is split
    # into two field-pictures with separate PTS ~half a frame apart, so the
    # demuxer emits ~2x as many packets as there are frames.  Indexing those
    # as frames doubles the count and makes duration, the timeline scale and
    # thumbnails all wrong (e.g. a 42:50 file reported as 59:27).
    #
    # The stream's average_rate is the authoritative *displayable* rate, so we
    # collapse PTS onto that frame grid: keep one entry per frame-period slot.
    # Guarded - only applied when the raw count clearly exceeds what the
    # duration at the declared rate implies (i.e. field-coding is present),
    # so normal progressive files are never altered.
    if pts_list and fps and time_base[1]:
        tb_num, tb_den = time_base
        ticks_per_frame = (tb_den / tb_num) / float(fps)

        # Decide whether to collapse by looking for field-pairs directly: any
        # consecutive PTS closer together than ~3/4 of a frame period means two
        # field-pictures share a frame slot.  This is robust where the old
        # global "raw count vs duration" ratio failed:
        #   - PTS discontinuities (ad breaks) inflated the duration estimate and
        #     suppressed the collapse, leaving the count (and reported length)
        #     too high - e.g. a 73-min file reported as 82 min.
        #   - Partially field-coded files (only some sections interlaced) never
        #     reached the old 20%-excess threshold and so were left uncollapsed.
        # The slot-collapse itself is correct for progressive streams too (one
        # PTS per slot = no change), but we still gate on field-pair presence so
        # a genuinely progressive file is never touched.
        if ticks_per_frame:
            near = ticks_per_frame * 0.75
            has_field_pairs = any(
                0 < (pts_list[i] - pts_list[i - 1]) < near
                for i in range(1, len(pts_list))
            )
        else:
            has_field_pairs = False

        if has_field_pairs:
            start = pts_list[0]
            collapsed = []
            seen_slots = set()
            for p in pts_list:
                slot = round((p - start) / ticks_per_frame)
                if slot not in seen_slots:
                    seen_slots.add(slot)
                    collapsed.append(p)
            pts_list = collapsed
            # Keep only keyframes that survive in the collapsed set.
            kf = set(keyframe_pts)
            keyframe_pts = [p for p in pts_list if p in kf]

    interlaced = _probe_interlaced(path)

    return FrameIndex(
        pts=array("q", pts_list),
        keyframe_pts=keyframe_pts,
        fps=fps,
        width=width,
        height=height,
        codec=codec,
        time_base=time_base,
        start_pts=start_pts,
        interlaced=interlaced,
    )


def _cache_path_for(src):
    st = os.stat(src)

    key = hashlib.md5(
        f"{Path(src).resolve()}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")
    ).hexdigest()

    return CACHE_DIR / f"{key}.idx"


def prune_index_cache(max_age_days):
    """Delete cached .idx files not used within max_age_days.

    "Used" means loaded - get_or_build_index touches the file on every cache
    hit, so an index for a file you still open regularly stays alive and only
    genuinely-stale ones expire.  A value <= 0 means keep forever.  Best-effort;
    returns the number of files removed.
    """
    try:
        if not max_age_days or max_age_days <= 0:
            return 0

        cutoff = time.time() - max_age_days * 86400
        removed = 0

        if CACHE_DIR.exists():
            for p in CACHE_DIR.glob("*.idx"):
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                        removed += 1
                except OSError:
                    pass

        return removed

    except Exception:
        return 0


def clear_index_cache():
    """Delete every cached .idx file now, regardless of age.  Best-effort;
    returns (files_removed, bytes_freed)."""
    removed = 0
    freed = 0
    try:
        if CACHE_DIR.exists():
            for p in CACHE_DIR.glob("*.idx"):
                try:
                    freed += p.stat().st_size
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
    except Exception:
        pass
    return removed, freed


class FrameIndexBuilder(QThread):

    # Note: named to avoid clashing with QThread.finished.
    progress = Signal(int)
    finished_index = Signal(object)
    failed = Signal(str)

    def __init__(
            self,
            path,
            parent=None,
    ):
        super().__init__(parent)

        self.path = path
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            index = build_frame_index(
                self.path,
                progress_cb=self.progress.emit,
                cancel_cb=lambda: self._cancel,
            )

            if index is not None and not self._cancel:
                self.finished_index.emit(index)

        except Exception as exc:
            self.failed.emit(str(exc))


def get_or_build_index(
        path,
        parent=None,
):
    """
    Return (index, None) if a valid cached index loads.
    Return (None, builder) if it must be built - caller connects the
    builder's signals and calls builder.start().

    The builder auto-saves the finished index to cache.
    """
    cache = _cache_path_for(path)

    if cache.exists():
        try:
            index = FrameIndex.load(cache)
            # Mark the cache file as freshly used so age-based pruning keeps
            # indices for files you actually open and only expires stale ones.
            try:
                os.utime(cache, None)
            except OSError:
                pass
            return index, None
        except Exception:
            pass    # stale/corrupt - rebuild

    builder = FrameIndexBuilder(path, parent)

    def _autosave(index):
        try:
            index.save(cache)
        except Exception:
            pass

    builder.finished_index.connect(_autosave)

    return None, builder


def build_index_sync(path):
    """Synchronously return a FrameIndex for `path`, using the on-disk cache
    when valid and saving a freshly built index back to it.  This is the
    blocking equivalent of get_or_build_index, for callers that already run on
    a worker thread (the batch manager) and don't want the QThread builder.
    """
    cache = _cache_path_for(path)

    if cache.exists():
        try:
            index = FrameIndex.load(cache)
            try:
                os.utime(cache, None)
            except OSError:
                pass
            return index
        except Exception:
            pass    # stale/corrupt - rebuild

    index = build_frame_index(path)
    try:
        index.save(cache)
    except Exception:
        pass
    return index
