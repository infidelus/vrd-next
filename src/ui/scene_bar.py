from PySide6.QtCore import (
    Qt,
    QPoint,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QFont,
)
from PySide6.QtWidgets import (
    QWidget,
)


class SceneBar(QWidget):

    def __init__(
            self,
            window,
    ):

        super().__init__()

        self.window = window

        self.scrubbing = False

        self.setFixedHeight(
            18
        )

    def paintEvent(
            self,
            event,
    ):

        from PySide6.QtGui import (
            QPainter,
            QColor,
            QPen,
        )

        painter = QPainter(self)

        w = self.width()
        h = self.height()

        total = max(
            1,
            len(self.window.frames)
        )

        #
        # Base bar (delete area)
        #

        painter.fillRect(
            0,
            0,
            w,
            h,
            # Slightly lighter than the old #702020 so the cut regions read
            # more clearly, while staying well behind the green kept scenes and
            # keeping the white markers legible.
            QColor("#8a2a2a")
        )

        #
        # Selection (keep area)
        #

        selection = getattr(
            self.window,
            "selection",
            None
        )

        if selection is None:
            painter.end()
            return

        for index, (
                start,
                end,
        ) in enumerate(
            selection.ranges
        ):

            x1 = int(
                start
                /
                total
                *
                w
            )

            x2 = int(
                end
                /
                total
                *
                w
            )

            colour = QColor(
                "#2f8f3c"
            )

            if (
                    self.window.selected_scene
                    ==
                    index
            ):
                colour = QColor(
                    "#d0b000"
                )

            painter.fillRect(
                x1,
                0,
                max(
                    2,
                    x2 - x1
                ),
                h,
                colour
            )

        #
        # Scene markers
        #

        scene_markers = (
            self.window.scenes.markers
        )

        marker_colour = QColor(
            "#00d8ff"
        )

        painter.setPen(
            QPen(
                marker_colour,
                1,
            )
        )

        painter.setBrush(
            marker_colour
        )

        for frame in scene_markers:
            x = int(
                frame
                /
                total
                *
                w
            )

            #
            # Vertical marker line
            #

            painter.drawLine(
                x,
                5,
                x,
                h,
            )

            #
            # Top triangle
            #

            painter.drawPolygon([
                QPoint(x - 4, 0),
                QPoint(x + 4, 0),
                QPoint(x, 5),
            ])

        #
        # Current frame
        #

        current_frame = getattr(
            self.window,
            "current_frame",
            0
        )

        current_x = int(
            current_frame
            /
            total
            *
            w
        )

        painter.setPen(
            QPen(
                QColor("#ffffff"),
                3,
            )
        )

        painter.drawLine(
            current_x,
            0,
            current_x,
            h,
        )

        #
        # Top cursor tab
        #

        painter.fillRect(
            current_x - 2,
            0,
            5,
            6,
            QColor("#ffffff")
        )

        #
        # Active unfinished selection markers
        #

        painter.setPen(
            QPen(
                QColor("#d0d0d0"),
                1,
            )
        )

        #
        # Pending IN marker
        #

        if selection.pending_in is not None:
            x = int(
                selection.pending_in
                /
                total
                *
                w
            )

            #
            # Left bracket
            #

            painter.drawLine(
                x,
                0,
                x,
                h,
            )

            painter.drawLine(
                x,
                0,
                x + 5,
                0,
            )

            painter.drawLine(
                x,
                h - 1,
                x + 5,
                h - 1,
            )

        #
        # Pending OUT marker
        #

        if selection.pending_out is not None:
            x = int(
                selection.pending_out
                /
                total
                *
                w
            )

            #
            # Right bracket
            #

            painter.drawLine(
                x,
                0,
                x,
                h,
            )

            painter.drawLine(
                x - 5,
                0,
                x,
                0,
            )

            painter.drawLine(
                x - 5,
                h - 1,
                x,
                h - 1,
            )

        painter.end()

    def seek_from_x(
            self,
            x,
    ):

        if not self.window.frames:
            return

        total = len(
            self.window.frames
        )

        x = max(
            0,
            min(
                self.width(),
                x,
            )
        )

        frame = int(
            (
                    x
                    /
                    self.width()
            )
            *
            total
        )

        frame = max(
            0,
            min(
                total - 1,
                frame,
            )
        )

        self.window.current_frame = frame

        self.window.scrub_to(frame)

    def mousePressEvent(
            self,
            event,
    ):

        if event.button() != Qt.LeftButton:
            return

        self.scrubbing = True

        self.seek_from_x(
            event.position().x()
        )

        self.window.setFocus()

    def mouseMoveEvent(
            self,
            event,
    ):

        if not self.scrubbing:
            return

        self.seek_from_x(
            event.position().x()
        )

    def mouseReleaseEvent(
            self,
            event,
    ):

        if event.button() == Qt.LeftButton:
            self.scrubbing = False

            #
            # Lock in the exact frame and refresh thumbnails / scene list.
            #

            self.window.scrub_finish()