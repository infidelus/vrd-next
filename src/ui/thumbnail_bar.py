"""
ThumbnailBar - the strip of frame thumbnails beneath the preview.

Behaviour:
  - Thumbnails are a fixed size (~120px).  The number shown is computed from
    the available width, so the strip fills the window and re-flows on resize.
  - The count is always odd, so the cursor frame sits in the dead centre.
    Near the start/end of the file the off-the-edge slots show blank, so the
    cursor stays centred rather than drifting.
  - A pixmap cache keyed by frame index means stepping one frame only decodes
    the single newly-revealed thumbnail; the other ten are reused.  Frames are
    fetched in increasing index order so the decoder stays on its fast
    sequential path.

The bar reads frames from window.frames (the FrameSequence) exactly as before,
so it works for both the in-RAM and decode-on-demand back ends.
"""

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
)

from media.renderer import render_frame
from media.scrub_worker import frame_label


# Target width per thumbnail; the real width flexes slightly so the strip
# fills the bar edge-to-edge with no gaps.
THUMB_TARGET_WIDTH = 120
THUMB_HEIGHT = 68
THUMB_SPACING = 4
THUMB_MIN_WIDTH = 80
MIN_THUMBS = 3
MAX_PIXMAP_CACHE = 400


class _Thumb(QLabel):

    # Emitted with the thumb's frame index when clicked (release inside the
    # widget).  -1 means the thumb is empty / has no frame.
    clicked = Signal(int)

    def __init__(self):
        super().__init__()

        # The frame this thumb currently shows; -1 when empty.
        self.frame_index = -1

        # Picture type letter ("I"/"P"/"B") to overlay, or "" for none.
        self.letter = ""

        self.setFixedHeight(
            THUMB_HEIGHT
        )

        self.setMinimumWidth(
            THUMB_MIN_WIDTH
        )

        # Expand to share the bar width equally; height stays fixed.
        self.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed,
        )

        self.setAlignment(
            Qt.AlignCenter
        )

        # A pointing-hand cursor hints that thumbnails are clickable.
        self.setCursor(Qt.PointingHandCursor)

        self.setStyleSheet(_STYLE_EMPTY)

    # Report a fixed size hint that does NOT depend on the current pixmap.
    #
    # A plain QLabel returns its pixmap's size as the size hint.  Combined with
    # the Expanding policy and _fit_to_label() (which scales each pixmap to its
    # label's current width), that formed a feedback loop: the cursor thumb's
    # 3px border made its hint a hair wider, the layout gave it more width, the
    # next refresh scaled its pixmap wider still, and it ballooned across
    # refreshes while its neighbours were squeezed toward the minimum (the
    # "4:3" thumbs).  Pinning the hint to the canonical thumbnail size decouples
    # layout width from pixmap width, so every slot shares the bar equally and
    # the pixmap is just painted (centred/scaled) within whatever width it gets.
    def sizeHint(self):
        return QSize(THUMB_TARGET_WIDTH, THUMB_HEIGHT)

    def minimumSizeHint(self):
        return QSize(THUMB_MIN_WIDTH, THUMB_HEIGHT)

    def mouseReleaseEvent(self, event):
        # Treat a left-click release inside the thumb as a request to jump to
        # that frame.  Ignore empty thumbs.
        if (
            event.button() == Qt.LeftButton
            and self.rect().contains(event.position().toPoint())
            and self.frame_index >= 0
        ):
            self.clicked.emit(self.frame_index)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        # Draw the thumbnail (pixmap + border) as usual, then overlay the
        # picture-type letter in the top-left corner if one is set.
        super().paintEvent(event)

        if not self.letter:
            return

        colour = _FRAME_TYPE_COLOURS.get(self.letter, QColor("#e0e0e0"))

        painter = QPainter(self)
        try:
            font = QFont()
            font.setPixelSize(13)
            font.setBold(True)
            painter.setFont(font)

            metrics = painter.fontMetrics()
            text_w = metrics.horizontalAdvance(self.letter)
            text_h = metrics.height()

            # A small translucent chip so the letter reads over any image.
            pad = 2
            box_w = text_w + pad * 2
            box_h = text_h
            painter.fillRect(
                3, 3, box_w, box_h, QColor(0, 0, 0, 150)
            )

            painter.setPen(colour)
            painter.drawText(
                3 + pad,
                3 + metrics.ascent(),
                self.letter,
            )
        finally:
            painter.end()


# Picture-type badge colours: I/IDR/keyframes green, P amber, B grey-blue.
_FRAME_TYPE_COLOURS = {
    "IDR": QColor("#37d67a"),
    "I": QColor("#37d67a"),
    "P": QColor("#f5a623"),
    "B": QColor("#9bb4d4"),
}


_STYLE_EMPTY = (
    "background:#081420; border:1px solid #222;"
)

_STYLE_CURSOR = (
    "background:#001026; border:3px solid #2f9bff;"
)

_STYLE_MARKER = (
    "background:#06121c; border:1px solid #19c3c3;"
)

_STYLE_SELECTED = (
    "background:#203000; border:1px solid #e6c200;"
)

_STYLE_NORMAL = (
    "background:#081420; border:1px solid #222;"
)


class ThumbnailBar(QWidget):

    def __init__(self, window):
        super().__init__()

        # The MainWindow.  Named 'main' to avoid shadowing QWidget.window().
        self.main = window

        self._labels = []
        self._count = 0

        # frame_index -> rendered QPixmap (content only; borders set per refresh)
        self._pixmap_cache = {}
        self._cache_order = []

        # frame_index -> picture-type letter ("I"/"P"/"B"); kept in step with
        # the pixmap cache.  Whether it's actually drawn depends on the setting.
        self._letters = {}
        self._show_frame_type = False

        self._layout = QHBoxLayout(self)

        # The bar re-flows its thumbnail count to whatever width it's given
        # (see resizeEvent).  Crucially it must NOT report a large minimum
        # width back up to the window: when maximised it packs in many thumbs,
        # and if each one's 80px minimum counted, the window's minimum width
        # would balloon and refuse to shrink on un-maximise (the same stuck-
        # size trap the preview avoids).  SetNoConstraint stops the inner
        # layout forcing a minimum, the Ignored policy stops the bar dictating
        # the window width, and minimumSizeHint() pins the reported minimum to
        # zero width.  The bar simply takes the width it's handed and rebuilds.
        self._layout.setSizeConstraint(
            QLayout.SetNoConstraint
        )

        self.setSizePolicy(
            QSizePolicy.Ignored,
            QSizePolicy.Fixed,
        )

        self._layout.setSpacing(
            THUMB_SPACING
        )

        self._layout.setContentsMargins(
            0,
            8,
            0,
            8,
        )

        self.setFixedHeight(
            THUMB_HEIGHT + 32
        )

        self._rebuild_labels()

    def minimumSizeHint(self):
        # Zero width: the bar never forces the window wider (see __init__).
        return QSize(0, THUMB_HEIGHT + 32)

    # ------------------------------------------------------------------ #
    # Layout / sizing
    # ------------------------------------------------------------------ #

    def _fit_count(self):
        """Odd number of thumbs that best fills the bar at ~target width."""
        width = self.width()

        if width <= 0:
            return MIN_THUMBS

        # Round to the nearest fit so the strip fills rather than under-fills.
        per = THUMB_TARGET_WIDTH + THUMB_SPACING

        n = round((width + THUMB_SPACING) / per)

        n = max(MIN_THUMBS, int(n))

        if n % 2 == 0:
            n -= 1

        return n

    def _rebuild_labels(self):
        """(Re)create the thumb labels when the fitting count changes."""
        n = self._fit_count()

        if n == self._count and self._labels:
            return

        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self._labels = []

        for _ in range(n):
            label = _Thumb()
            label.clicked.connect(self._on_thumb_clicked)
            self._layout.addWidget(label)
            self._labels.append(label)

        self._count = n

        # Thumb widths change with the count, so cached pixmaps are stale.
        self.clear_cache()

    def resizeEvent(self, event):
        super().resizeEvent(event)

        self._rebuild_labels()

        # Re-render to the new thumb width so the strip stays filled.
        self.refresh()

    # ------------------------------------------------------------------ #
    # Cache
    # ------------------------------------------------------------------ #

    def clear_cache(self):
        self._pixmap_cache.clear()
        self._cache_order.clear()
        self._letters.clear()

    def set_frame_type_display(self, mode):
        """Update whether the I/P/B badge is shown on thumbnails.

        mode is the config value ("none"/"thumbnails"/"preview"/"both"); the
        badge shows for "thumbnails" and "both".
        """
        show = mode in ("thumbnails", "both")
        if show == self._show_frame_type:
            return
        self._show_frame_type = show
        for label in self._labels:
            self._apply_letter(label, label.frame_index)
            label.update()

    def _apply_letter(self, label, frame_index):
        """Set a label's overlay letter from the cache, honouring the setting."""
        if self._show_frame_type and frame_index >= 0:
            label.letter = self._letters.get(frame_index, "")
        else:
            label.letter = ""

    def _cached_pixmap(self, frame_index):
        """Return the cached pixmap for a frame, or None.

        Cached at a single canonical width (THUMB_TARGET_WIDTH) and keyed by
        frame index alone, so a frame stays cached as it shifts between slots
        whose pixel widths differ by a pixel or two.  The tiny rescale to the
        actual slot width happens on display and is free (no decode).
        """
        return self._pixmap_cache.get(frame_index)

    def _store_pixmap(self, frame_index, pixmap):
        if frame_index not in self._pixmap_cache:
            self._cache_order.append(frame_index)

        self._pixmap_cache[frame_index] = pixmap

        while len(self._cache_order) > MAX_PIXMAP_CACHE:
            old = self._cache_order.pop(0)
            self._pixmap_cache.pop(old, None)
            self._letters.pop(old, None)

    def _fit_to_label(self, pixmap, label):
        """Scale a canonical-width cached pixmap to a label's exact width.

        A cheap pixmap scale (no decode); the difference is usually 1-2px.
        """
        w = label.width() or THUMB_TARGET_WIDTH
        if pixmap.width() == w:
            return pixmap
        return pixmap.scaledToWidth(w, Qt.SmoothTransformation)

    # ------------------------------------------------------------------ #
    # Drawing
    # ------------------------------------------------------------------ #

    def set_center_from_image(self, frame_index, image, letter=""):
        """Cache the cursor frame (from the scrub worker's preview image) at
        the canonical thumbnail width, then refresh so it's sized by the same
        path as every other thumbnail.  Guarantees the centre thumbnail matches
        the preview without a separate, inconsistently-sized render.
        """
        from PySide6.QtGui import QPixmap

        if frame_index not in self._pixmap_cache:
            pixmap = QPixmap.fromImage(image).scaledToWidth(
                THUMB_TARGET_WIDTH, Qt.SmoothTransformation
            )
            self._store_pixmap(frame_index, pixmap)
        if letter:
            self._letters[frame_index] = letter

        # Place it via the normal refresh so all slots size consistently.
        if frame_index == self.main.current_frame:
            self.refresh()

    def set_center_from_frame(self, frame_index, frame, container):
        """Cache the cursor frame (already decoded for the preview) at the
        canonical thumbnail width, then refresh.  Same single sizing path as
        all other thumbnails - no special-case rendering that could mis-size.
        """
        if frame is None:
            return

        if frame_index not in self._pixmap_cache:
            pixmap = render_frame(
                frame,
                container,
                THUMB_TARGET_WIDTH,
                THUMB_HEIGHT,
            )
            self._store_pixmap(frame_index, pixmap)
        self._letters[frame_index] = frame_label(
            frame, getattr(self.main, "fetcher", None), frame_index
        )

    def refresh_sync(self):
        """Refresh the strip decoding any uncached thumbnails inline.

        Used during frame-stepping, where the user watches the strip to find
        a cut point so it must stay exactly in sync.  Stepping shifts the
        strip by one frame, so at most one thumbnail is uncached and the
        decode is a cheap sequential frame - fast enough to do on the spot.
        """
        window = self.main

        if not self._labels:
            return

        frames = window.frames
        total = len(frames)

        if not total:
            for label in self._labels:
                label.frame_index = -1
                label.letter = ""
                label.clear()
                label.setStyleSheet(_STYLE_EMPTY)
            return

        count = self._count
        half = count // 2

        start = window.current_frame - half
        max_start = max(0, total - count)
        start = max(0, min(start, max_start))

        self._wanted = {}

        for i, label in enumerate(self._labels):
            frame_index = start + i

            if frame_index < 0 or frame_index >= total:
                label.frame_index = -1
                label.letter = ""
                label.clear()
                label.setStyleSheet(_STYLE_EMPTY)
                continue

            label.frame_index = frame_index
            self._wanted[frame_index] = label

            pixmap = self._cached_pixmap(frame_index)

            if pixmap is None:
                # Decode this one inline (cheap: a sequential fast-path frame).
                frame = frames[frame_index]
                if frame is not None:
                    pixmap = render_frame(
                        frame,
                        window.container,
                        THUMB_TARGET_WIDTH,
                        THUMB_HEIGHT,
                    )
                    self._store_pixmap(frame_index, pixmap)
                    self._letters[frame_index] = frame_label(
            frame, getattr(self.main, "fetcher", None), frame_index
        )

            if pixmap is not None:
                label.setPixmap(self._fit_to_label(pixmap, label))

            self._apply_letter(label, frame_index)
            label.setStyleSheet(self._style_for(frame_index))

    def refresh(self):
        window = self.main

        if not self._labels:
            return

        frames = window.frames
        total = len(frames)

        count = self._count
        half = count // 2

        if not total:
            for label in self._labels:
                label.frame_index = -1
                label.letter = ""
                label.clear()
                label.setStyleSheet(_STYLE_EMPTY)
            return

        # Cursor centred in general, but clamp at the file ends so the bar
        # stays full (frames fall off the side rather than showing blanks).
        start = window.current_frame - half

        max_start = max(0, total - count)

        start = max(0, min(start, max_start))

        # Map of which frame each visible label wants, so a thumbnail decoded
        # later (off-thread) can be matched to the right label and stale
        # deliveries (from a previous strip) can be ignored.
        self._wanted = {}

        to_decode = []

        for i, label in enumerate(self._labels):

            frame_index = start + i

            # Only short files leave trailing slots empty.
            if frame_index < 0 or frame_index >= total:
                label.frame_index = -1
                label.letter = ""
                label.clear()
                label.setStyleSheet(_STYLE_EMPTY)
                continue

            label.frame_index = frame_index
            self._wanted[frame_index] = label

            pixmap = self._cached_pixmap(frame_index)

            if pixmap is not None:
                # Cached - scale to this slot (cheap) and show immediately.
                label.setPixmap(self._fit_to_label(pixmap, label))
            else:
                # Not cached: leave the current pixmap in place for now (avoids
                # a distracting flash to blank) and ask the worker to decode it
                # at the canonical width.
                to_decode.append(
                    (frame_index, THUMB_TARGET_WIDTH, THUMB_HEIGHT)
                )

            # Apply the badge from the letter cache.  For an uncached frame the
            # letter isn't known yet; set_thumb_image() will apply it when the
            # worker delivers the thumbnail.
            self._apply_letter(label, frame_index)

            label.setStyleSheet(
                self._style_for(frame_index)
            )

        # Hand the uncached thumbnails to the background worker.  Decode them
        # in left-to-right order so the strip fills like a normal load rather
        # than visibly radiating outwards from the centre.
        if to_decode:
            to_decode.sort(key=lambda t: t[0])
            worker = getattr(window, "thumb_worker", None)
            if worker is not None:
                worker.request(to_decode)

    def set_thumb_image(self, frame_index, width, image, letter=""):
        """Receive a thumbnail decoded off the UI thread and place it.

        Ignores deliveries that no longer match a visible label (the user has
        navigated away since the request).
        """
        from PySide6.QtGui import QPixmap

        wanted = getattr(self, "_wanted", None)
        if not wanted:
            return

        label = wanted.get(frame_index)
        if label is None:
            return    # stale - this frame isn't on the strip any more

        pixmap = QPixmap.fromImage(image)
        self._store_pixmap(frame_index, pixmap)
        if letter:
            self._letters[frame_index] = letter

        # Only set it if the label is still showing this frame.
        if label.frame_index == frame_index:
            label.setPixmap(self._fit_to_label(pixmap, label))
            self._apply_letter(label, frame_index)
            label.setStyleSheet(self._style_for(frame_index))

    def _on_thumb_clicked(self, frame_index):
        # Jump the main window's cursor to the clicked thumbnail's frame.
        self.main.goto_frame(frame_index)

    def _style_for(self, frame_index):
        window = self.main

        if frame_index == window.current_frame:
            return _STYLE_CURSOR

        if window.scenes.has_marker(frame_index):
            return _STYLE_MARKER

        for range_start, range_end in window.selection.ranges:
            if range_start <= frame_index <= range_end:
                return _STYLE_SELECTED

        return _STYLE_NORMAL
