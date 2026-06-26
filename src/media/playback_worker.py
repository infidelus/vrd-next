"""
PlaybackWorker - decodes video frames sequentially on a background thread into
a small look-ahead queue, for smooth playback.

Unlike ScrubWorker (latest-wins, one cold seek per request - right for chasing
a dragged cursor, wrong for steady playback), this worker decodes a continuous
forward run of frames (the cheap sequential path) and buffers a few ahead.  The
UI thread never decodes during playback: each tick it just pulls the frame for
the current clock time from the queue (dropping any older ones) and shows it.

  start(frame, w, h)  - begin decoding forward from `frame`
  seek(frame)         - jump: flush the queue and resume decoding from `frame`
  take(target)        - UI: pop the newest buffered frame at index <= target
  stop_playback()     - pause: stop decoding, clear the queue
  set_size(w, h)      - change the output size for subsequent frames
  stop()              - exit the worker loop (on close)
"""

from collections import deque

from PySide6.QtCore import (
    QMutex,
    QObject,
    QWaitCondition,
)

from media.fetcher import FrameFetcher
from media.scrub_worker import frame_to_qimage, frame_label


class PlaybackWorker(QObject):

    def __init__(self, path, index):
        super().__init__()

        self._path = str(path)
        self._index = index
        self._fetcher = None

        self._mutex = QMutex()
        self._cond = QWaitCondition()

        self._running = True
        self._playing = False
        self._seek_to = None
        self._next_index = 0
        self._size = (1280, 720)
        self._target = None                # live presentation frame (from take)

        # Decoded frames waiting to be shown, ascending by index.
        self._queue = deque()
        self._max_queue = 12               # ~0.5s of look-ahead at 25fps

        self._frame_count = (
            index.frame_count if index is not None else 0
        )

    # ------------------------------------------------------------------ #
    # Called from the UI thread
    # ------------------------------------------------------------------ #

    def start(self, frame, width, height):
        """Begin (or restart) decoding forward from `frame`."""
        self._mutex.lock()
        self._size = (max(1, int(width)), max(1, int(height)))
        self._seek_to = max(0, int(frame))
        self._playing = True
        self._target = None
        self._queue.clear()
        self._cond.wakeOne()
        self._mutex.unlock()

    def seek(self, frame):
        """Jump to `frame`: flush the queue and decode from there."""
        self._mutex.lock()
        if self._playing:
            self._seek_to = max(0, int(frame))
            self._target = None
            self._queue.clear()
            self._cond.wakeOne()
        self._mutex.unlock()

    def stop_playback(self):
        """Pause: stop decoding and drop the buffer."""
        self._mutex.lock()
        self._playing = False
        self._seek_to = None
        self._target = None
        self._queue.clear()
        self._cond.wakeOne()
        self._mutex.unlock()

    def set_size(self, width, height):
        self._mutex.lock()
        self._size = (max(1, int(width)), max(1, int(height)))
        self._mutex.unlock()

    def take(self, target):
        """Pop the newest buffered frame whose index is <= target (dropping any
        older ones).  Returns (index, QImage, frame_type) or None if nothing is
        ready."""
        result = None
        self._mutex.lock()
        self._target = target
        while self._queue and self._queue[0][0] <= target:
            result = self._queue.popleft()
        # Freed space - let the decoder run on.
        self._cond.wakeOne()
        self._mutex.unlock()
        return result

    def stop(self):
        """Exit the worker loop (call before quitting its thread)."""
        self._mutex.lock()
        self._running = False
        self._playing = False
        self._cond.wakeOne()
        self._mutex.unlock()

    # ------------------------------------------------------------------ #
    # Runs on the worker thread (connect to QThread.started)
    # ------------------------------------------------------------------ #

    def run(self):
        self._fetcher = FrameFetcher(
            self._path,
            self._index,
        )

        while True:

            self._mutex.lock()

            while (
                    self._running
                    and self._seek_to is None
                    and (
                        not self._playing
                        or len(self._queue) >= self._max_queue
                        or self._next_index >= self._frame_count
                    )
            ):
                self._cond.wait(self._mutex)

            if not self._running:
                self._mutex.unlock()
                break

            if self._seek_to is not None:
                self._next_index = self._seek_to
                self._seek_to = None
                self._queue.clear()

            #
            # If decode has fallen too far behind the live playback position
            # (e.g. the machine is busy), skip ahead to it - drop the frames we
            # could never show in time - rather than grinding through every one
            # in slow motion.
            #
            if (
                    self._target is not None
                    and self._next_index < self._target - self._max_queue
            ):
                self._next_index = self._target
                self._queue.clear()

            if (
                    not self._playing
                    or self._next_index >= self._frame_count
            ):
                self._mutex.unlock()
                continue

            index = self._next_index
            width, height = self._size

            self._mutex.unlock()

            try:
                frame = self._fetcher.frame_at(index)
            except Exception:
                frame = None

            if frame is None:
                # End or unrecoverable error: stop advancing.
                self._mutex.lock()
                self._next_index = self._frame_count
                self._mutex.unlock()
                continue

            image = frame_to_qimage(
                frame,
                self._fetcher.container,
                width,
                height,
                fast=True,
            )
            letter = frame_label(frame, self._fetcher, index)

            self._mutex.lock()
            # Keep it only if no seek/stop happened while we were decoding.
            if self._seek_to is None and self._playing and index == self._next_index:
                self._queue.append((index, image, letter))
                self._next_index = index + 1
            self._mutex.unlock()

        if self._fetcher is not None:
            self._fetcher.close()
