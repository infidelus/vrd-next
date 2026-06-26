"""
ScrubWorker - decodes preview frames on a background thread during scrubbing.

Why this exists:
  Cold seeks on HD are ~200-400ms.  Doing them on the UI thread while the
  user drags the timeline freezes the interface and lurches through stale
  frames.  This worker moves the decode off the UI thread and only ever
  decodes the LATEST requested position (latest-wins), so the preview races
  toward where the mouse is rather than queueing every intermediate frame.

Design:
  - The worker owns its OWN FrameFetcher (a second av.open on the same file,
    sharing the read-only index).  Nothing is shared with the UI thread's
    fetcher, so no locking around the decoder is needed.
  - request() (called from the UI thread) just stores the target index under
    a mutex and wakes the worker.  No signal/slot queue to pile up.
  - The worker decodes, scales to a QImage (safe off the GUI thread; QPixmap
    is not), and emits it - unless a newer target has already arrived, in
    which case it skips straight to the newer one.
"""

from PySide6.QtCore import (
    QMutex,
    QObject,
    QWaitCondition,
    Qt,
    Signal,
)
from PySide6.QtGui import QImage

from media.fetcher import FrameFetcher


def frame_type_letter(frame):
    """Return 'I', 'P' or 'B' for a decoded frame, or '' if unknown.

    PyAV exposes ``pict_type`` as an int in this build (1=I, 2=P, 3=B,
    matching FFmpeg's AV_PICTURE_TYPE_*); other versions expose an enum whose
    name is the letter.  IDR is distinguished separately (see frame_label),
    because it needs the packet's NAL type, not just the picture type.
    """
    pt = getattr(frame, "pict_type", None)
    if pt is None:
        return ""
    name = getattr(pt, "name", None)
    if isinstance(name, str) and name in ("I", "P", "B"):
        return name
    try:
        return {1: "I", 2: "P", 3: "B"}.get(int(pt), "")
    except (TypeError, ValueError):
        return ""


def frame_label(frame, fetcher=None, frame_index=None):
    """Like frame_type_letter, but upgrades an I-frame to 'IDR' when it is a
    true IDR (instant decoder refresh) rather than a plain/recovery-point
    I-frame.  IDR can't be told from the decoded frame alone - the decoder's
    key_frame flag is set for every I-frame here - so the fetcher detects it
    from the packet's H.264 NAL unit type and we look it up by frame index.
    """
    letter = frame_type_letter(frame)
    if (
        letter == "I"
        and fetcher is not None
        and frame_index is not None
    ):
        try:
            if fetcher.is_idr(frame_index):
                return "IDR"
        except Exception:
            pass
    return letter


def frame_to_qimage(frame, container, width, height, fast=False):
    """Decode -> RGB -> DAR-correct -> fit, returning a detached QImage.

    Mirrors media.renderer.render_frame but stays in QImage space so it can
    run on a worker thread (QPixmap may only be used on the GUI thread).
    `fast` uses nearest-neighbour scaling (cheaper, for playback).
    """
    mode = Qt.FastTransformation if fast else Qt.SmoothTransformation

    array = frame.to_ndarray(format="rgb24")

    h, w, _ = array.shape

    # .copy() detaches from the numpy buffer, which is freed when `array` goes.
    image = QImage(
        array.data,
        w,
        h,
        w * 3,
        QImage.Format_RGB888,
    ).copy()

    # Correct display aspect ratio (anamorphic SD).
    try:
        stream = container.streams.video[0]
        dar = stream.display_aspect_ratio

        if dar:
            corrected_width = int(h * float(dar))

            image = image.scaled(
                corrected_width,
                h,
                Qt.IgnoreAspectRatio,
                mode,
            )
    except Exception:
        pass

    if width:
        image = image.scaled(
            width,
            height,
            Qt.KeepAspectRatio,
            mode,
        )

    return image


class ScrubWorker(QObject):

    # (frame_index, QImage, frame_type) - delivered on the UI thread via a
    # queued connection.
    frame_ready = Signal(int, object, str)

    def __init__(
            self,
            path,
            index,
    ):
        super().__init__()

        self._path = str(path)
        self._index = index

        self._fetcher = None

        self._mutex = QMutex()
        self._cond = QWaitCondition()

        self._target = None
        self._size = (320, 180)
        self._running = True

    # ------------------------------------------------------------------ #
    # Called from the UI thread
    # ------------------------------------------------------------------ #

    def request(
            self,
            frame_index,
            width,
            height,
    ):
        """Ask for a frame.  Overwrites any pending request (latest wins)."""
        self._mutex.lock()

        self._target = frame_index
        self._size = (max(1, width), max(1, height))

        self._cond.wakeOne()
        self._mutex.unlock()

    def stop(self):
        """Ask the worker loop to exit; safe to call from the UI thread."""
        self._mutex.lock()

        self._running = False

        self._cond.wakeOne()
        self._mutex.unlock()

    # ------------------------------------------------------------------ #
    # Runs on the worker thread (connect to QThread.started)
    # ------------------------------------------------------------------ #

    def run(self):
        # The fetcher (and its av container) must live on this thread.
        self._fetcher = FrameFetcher(
            self._path,
            self._index,
        )

        while True:

            self._mutex.lock()

            while self._running and self._target is None:
                self._cond.wait(self._mutex)

            if not self._running:
                self._mutex.unlock()
                break

            target = self._target
            width, height = self._size
            self._target = None

            self._mutex.unlock()

            try:
                frame = self._fetcher.frame_at(target)
            except Exception:
                frame = None

            if frame is None:
                continue

            image = frame_to_qimage(
                frame,
                self._fetcher.container,
                width,
                height,
            )

            self.frame_ready.emit(
                target, image, frame_label(frame, self._fetcher, target)
            )

        if self._fetcher is not None:
            self._fetcher.close()
