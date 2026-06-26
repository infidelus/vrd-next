"""
FrameFetcher - turns a frame index into actual decoded frames, on demand.

Design goals (validated by index_probe.py):

  - Frame-exact: asking for index N returns the frame whose PTS == index N's
    PTS, never a neighbour.
  - Crash-safe: broadcast streams contain packets the decoder rejects right
    after a seek; we decode packet-by-packet and skip those instead of dying.
  - Fast where it matters: sequential access (stepping forward, playback)
    reuses a live decoder at a few ms/frame.  Only a cold jump pays the
    seek+decode-forward cost (cheap on SD, ~200ms on HD - run off the UI
    thread by the caller).
  - Bounded memory: a small cache of recently decoded frames makes short
    backward steps instant without holding the whole file in RAM.

This class is deliberately Qt-free so it can run on a worker thread.
"""

import av


def _packet_has_idr(data):
    """True if an H.264 access unit (Annex-B byte string) contains an IDR
    slice (NAL unit type 5).  Scans start codes and stops at the first VCL
    slice NAL (types 1-5): type 5 is IDR, types 1-4 are non-IDR, so we can
    decide as soon as we reach the picture's first slice."""
    i = 0
    n = len(data)
    while i < n - 4:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                nal_type = data[i + 3] & 0x1F
                i += 4
            elif data[i + 2] == 0 and data[i + 3] == 1:
                nal_type = data[i + 4] & 0x1F
                i += 5
            else:
                i += 1
                continue
            if nal_type == 5:
                return True
            if nal_type in (1, 2, 3, 4):
                return False    # first picture slice is non-IDR
        else:
            i += 1
    return False


class FrameFetcher:

    def __init__(
            self,
            path,
            index,
            cache_size=None,
    ):
        self.path = str(path)
        self.index = index

        self.container = av.open(self.path)
        self.stream = self.container.streams.video[0]

        # PTS of frames that are true IDRs, discovered lazily as packets are
        # demuxed (an I-frame's picture type can't tell IDR from a plain I, so
        # we read the NAL unit type from the packet).  Sparse, so it stays tiny.
        self._idr_pts = set()

        # Decode across multiple CPU cores.  By default PyAV decodes on a
        # single thread, which pins one core and leaves the rest idle - the
        # main reason HD H.264 navigation/playback stutters.  "AUTO" enables
        # frame + slice threading, spreading each decode over available cores.
        # Harmless on codecs that don't support threading (it just stays
        # effectively single-threaded for those).
        try:
            self.stream.codec_context.thread_type = "AUTO"
            self.stream.codec_context.thread_count = 0    # 0 = let ffmpeg pick
        except Exception:
            pass

        # Live decode generator and the index of the last frame it yielded.
        self._gen = None
        self._last_index = None

        # How far ahead a forward jump can be and still decode sequentially
        # rather than cold-seeking.  ~2 GOPs worth of frames: beyond that, a
        # fresh seek is cheaper than decoding through.
        gop_frames = 0

        if index.median_gop_pts and index.time_base[1]:
            num, den = index.time_base
            gop_frames = int(
                index.median_gop_pts * num / den * index.fps
            )

        self._forward_limit = max(12, 2 * gop_frames)

        # Recently-decoded frames cache: index -> VideoFrame.
        #
        # Sized to hold several GOPs.  This matters for BACKWARD stepping: a
        # cold seek decodes a whole GOP to reach the target, and we now cache
        # all of it, so stepping back through that GOP is instant.  If the
        # cache were smaller than a GOP, the earliest frames (the ones stepped
        # into next) would be evicted before use.  Capped to bound memory.
        if cache_size is None:
            cache_size = max(48, min(240, 3 * gop_frames + 24))

        self._cache = {}
        self._cache_order = []
        self._cache_size = cache_size

    def close(self):
        try:
            self.container.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Public
    # ------------------------------------------------------------------ #

    def can_serve_cheaply(self, index):
        """True if frame `index` can be returned without a cold seek.

        That means it's already cached, or it's a short sequential step ahead
        of the live decoder (the forward fast-path).  Callers use this to
        decide whether to decode on the UI thread (cheap) or hand off to a
        background worker (a cold seek, ~200ms on HD).
        """
        index = int(index)
        index = max(0, min(self.index.frame_count - 1, index))

        if index in self._cache:
            return True

        if (
                self._gen is not None
                and self._last_index is not None
                and self._last_index < index <= self._last_index + self._forward_limit
        ):
            return True

        return False

    def frame_at(self, index):
        """Return the decoded VideoFrame at the given frame index (clamped)."""
        index = int(index)

        index = max(
            0,
            min(self.index.frame_count - 1, index),
        )

        cached = self._cache.get(index)
        if cached is not None:
            return cached

        target = self.index.pts[index]

        # Sequential fast path: if the target is AHEAD of where the live
        # decoder is and not too far (a couple of GOPs), decode forward from
        # the existing generator rather than paying a cold seek.  This makes
        # forward scrubbing cheap (per-frame cost, not per-GOP).
        if (
                self._gen is not None
                and self._last_index is not None
                and self._last_index < index <= self._last_index + self._forward_limit
        ):
            frame = self._advance_to(target)

            if frame is not None:
                self._remember(index, frame)
                self._last_index = index
                return frame

        # Cold path: seek near the target and decode forward to it.
        frame = self._seek_and_decode(target)

        self._last_index = index if frame is not None else None

        if frame is not None:
            self._remember(index, frame)

        return frame

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _frame_iter(self):
        """Yield frames in display order, skipping packets the decoder rejects."""
        for packet in self.container.demux(self.stream):
            try:
                frames = packet.decode()
            except av.error.InvalidDataError:
                continue

            # Record IDR by the packet's OWN pts.  packet.decode() returns
            # reordered/delayed frames (B-frames), so the frames it yields here
            # are not this packet's picture - only packet.pts identifies it.
            # Only I-frames can be IDR and IDR packets are always keyframes, so
            # parse the NAL type for keyframe packets only (cheap, ~1 in 24).
            if packet.is_keyframe and packet.pts is not None:
                try:
                    if _packet_has_idr(bytes(packet)):
                        self._idr_pts.add(packet.pts)
                except Exception:
                    pass

            for frame in frames:
                if frame.pts is not None:
                    yield frame

    def is_idr(self, frame_index):
        """True if the frame at this index is a true IDR.  Only meaningful once
        the frame has been decoded (its packet seen), which is always the case
        when classifying a frame we've just fetched."""
        try:
            return self.index.pts[int(frame_index)] in self._idr_pts
        except (IndexError, KeyError, TypeError, ValueError):
            return False

    def _advance_to(self, target):
        """Pull from the existing live generator up to the target PTS.

        Intermediate frames passed on the way are cached, so a subsequent
        request for one of them (common when scrubbing) is instant.
        """
        try:
            for frame in self._gen:
                if frame.pts == target:
                    return frame
                if frame.pts > target:
                    return None    # missed it - caller falls back to cold path

                # Cache frames we pass over so they aren't wasted.
                passed_index = self.index.index_of_pts(frame.pts)
                if self.index.pts[passed_index] == frame.pts:
                    self._remember(passed_index, frame)
        except av.error.InvalidDataError:
            return None

        return None

    def _seek_and_decode(self, target):
        gop = self.index.median_gop_pts or 1
        margin = gop + gop // 2

        first_over = None

        for attempt in range(4):
            seek_to = max(0, target - margin - attempt * gop)

            self.container.seek(
                seek_to,
                stream=self.stream,
                backward=True,
                any_frame=False,
            )

            self._gen = self._frame_iter()

            first_over = None

            for frame in self._gen:
                if frame.pts == target:
                    return frame
                if frame.pts > target:
                    first_over = frame
                    break    # overshot this GOP - widen margin and retry

                # Cache frames decoded on the way to the target.  These are the
                # frames BEFORE the target in the same GOP, so a subsequent
                # backward step lands on a cache hit instead of paying another
                # full seek+decode - which is what made backward stepping slow.
                passed_index = self.index.index_of_pts(frame.pts)
                if (
                        0 <= passed_index < self.index.frame_count
                        and self.index.pts[passed_index] == frame.pts
                ):
                    self._remember(passed_index, frame)

        # Couldn't land exactly (very unusual) - hand back the closest after.
        self._gen = None
        return first_over

    def _remember(self, index, frame):
        if self._cache_size <= 0:
            return

        if index not in self._cache:
            self._cache_order.append(index)

        self._cache[index] = frame

        while len(self._cache_order) > self._cache_size:
            old = self._cache_order.pop(0)
            self._cache.pop(old, None)
