"""
ThumbnailWorker - decodes thumbnail-strip frames on a background thread.

The thumbnail strip shows ~9 frames around the cursor.  On HD, decoding those
on the UI thread (as part of every navigation) is the single biggest cause of
the interface freezing: a cold jump means up to 9 sequential cold seeks, each
~200ms, all blocking the GUI.

This worker owns its own FrameFetcher (a separate av.open sharing the
read-only index), decodes the requested frame indices, and emits each as a
QImage as it becomes ready, so the strip fills in progressively while the UI
stays responsive.

Latest-wins: a new request() (the user navigated again) replaces the pending
strip, so we never grind through a stale strip the user has already left.
"""

from PySide6.QtCore import (
    QMutex,
    QObject,
    QWaitCondition,
    Signal,
)

from media.fetcher import FrameFetcher
from media.scrub_worker import frame_to_qimage, frame_label


class ThumbnailWorker(QObject):

    # (frame_index, width, QImage, frame_type) delivered on the UI thread.
    thumb_ready = Signal(int, int, object, str)

    def __init__(self, path, index):
        super().__init__()

        self._path = str(path)
        self._index = index

        self._fetcher = None

        self._mutex = QMutex()
        self._cond = QWaitCondition()

        # Pending strip: list of (frame_index, width, height) plus a generation
        # counter so stale results can be discarded.
        self._requests = None
        self._generation = 0
        self._running = True

    # ------------------------------------------------------------------ #
    # Called from the UI thread
    # ------------------------------------------------------------------ #

    def request(self, items):
        """Ask for a strip of thumbnails.

        items is a list of (frame_index, width, height).  Replaces any pending
        strip (latest wins).
        """
        self._mutex.lock()
        self._requests = list(items)
        self._generation += 1
        self._cond.wakeOne()
        self._mutex.unlock()

    def stop(self):
        self._mutex.lock()
        self._running = False
        self._cond.wakeOne()
        self._mutex.unlock()

    # ------------------------------------------------------------------ #
    # Worker thread
    # ------------------------------------------------------------------ #

    def run(self):
        self._fetcher = FrameFetcher(self._path, self._index)

        while True:
            self._mutex.lock()
            while self._running and self._requests is None:
                self._cond.wait(self._mutex)

            if not self._running:
                self._mutex.unlock()
                break

            items = self._requests
            generation = self._generation
            self._requests = None
            self._mutex.unlock()

            for frame_index, width, height in items:
                # Abandon this strip if a newer request has arrived.
                self._mutex.lock()
                superseded = generation != self._generation
                self._mutex.unlock()
                if superseded or not self._running:
                    break

                try:
                    frame = self._fetcher.frame_at(frame_index)
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

                self.thumb_ready.emit(
                    frame_index, width, image,
                    frame_label(frame, self._fetcher, frame_index)
                )

        if self._fetcher is not None:
            self._fetcher.close()
