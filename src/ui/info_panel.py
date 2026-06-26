from PySide6.QtCore import Qt

from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QGridLayout,
    QVBoxLayout,
    QLabel,
    QSizePolicy,
)

from utils.timecode import (
    frame_to_timecode,
    seconds_to_timecode,
)


class InfoPanel(QWidget):

    def __init__(
            self,
            window,
    ):

        super().__init__()

        self.window = window

        #
        # Outer column: the "Info" title, then a bordered box holding the
        # figures.  The box fills the panel width - which matches the scene
        # list above it - so the border is the same width as the scene panel
        # and stays that width regardless of how wide the figures get (it used
        # to hug its contents, so it was narrower before a file was loaded).
        #

        outer = QVBoxLayout(
            self
        )

        # No horizontal margin: the box lines up with the scene list, which
        # sits flush in the same column.
        outer.setContentsMargins(
            0,
            6,
            0,
            6,
        )

        outer.setSpacing(
            6
        )

        title = QLabel(
            "Info"
        )

        title.setStyleSheet(
            "font-weight:bold;"
        )

        outer.addWidget(
            title
        )

        #
        # The bordered box.  1px #555 border to match the app's other panels
        # (transport, scene list).  It fills the width horizontally and is
        # capped to its contents vertically (so it never stretches down the
        # column).
        #

        box = QFrame()

        box.setObjectName(
            "infoBox"
        )

        box.setStyleSheet(
            "#infoBox { border: 1px solid #555555; }"
        )

        box.setSizePolicy(
            QSizePolicy.Preferred,
            QSizePolicy.Maximum,
        )

        grid = QGridLayout(
            box
        )

        # A little inner padding so the figures aren't flush against the
        # border, plus spacing between the columns.
        grid.setContentsMargins(
            10,
            8,
            10,
            8,
        )

        grid.setHorizontalSpacing(
            28
        )

        grid.setVerticalSpacing(
            6
        )

        # Columns 0 and 4 are empty stretch spacers; the actual content
        # (label / Time / MB in columns 1-3) is therefore centred in the box,
        # sitting a little indented from the border rather than crammed to one
        # side or spread to the edges.
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(4, 1)

        # Pin the Time and MB columns to the width of their widest possible
        # value.  Otherwise the columns shrink to fit short content - so the
        # dashes shown before a file is open ("--:--:--.--", and "0.00") made
        # the whole block narrower and shifted than once real figures appeared.
        # Fixed widths keep the layout identical whether or not a file's open.
        fm = self.fontMetrics()
        grid.setColumnMinimumWidth(
            2,
            fm.horizontalAdvance("00:00:00.00") + 6,
        )
        grid.setColumnMinimumWidth(
            3,
            fm.horizontalAdvance("99999.99") + 6,
        )

        #
        # Column headers
        #

        time_header = QLabel("Time")
        time_header.setAlignment(Qt.AlignCenter)
        grid.addWidget(
            time_header,
            0,
            2,
        )

        mb_header = QLabel("MB")
        mb_header.setAlignment(Qt.AlignCenter)
        grid.addWidget(
            mb_header,
            0,
            3,
        )

        #
        # Rows
        #

        self.rows = {}

        labels = [
            "Cursor",
            "Program",
            "Selection",
            "Output",
            "Joiner",
        ]

        for index, name in enumerate(
                labels,
                start=1,
        ):

            label = QLabel(
                f"{name}:"
            )

            value = QLabel(
                "--:--:--.--"
            )

            value.setAlignment(
                Qt.AlignCenter
            )

            mb = QLabel(
                "0.00"
            )

            mb.setAlignment(
                Qt.AlignRight | Qt.AlignVCenter
            )

            grid.addWidget(
                label,
                index,
                1,
            )

            grid.addWidget(
                value,
                index,
                2,
            )

            grid.addWidget(
                mb,
                index,
                3,
            )

            self.rows[name] = {
                "time": value,
                "mb": mb,
            }

        outer.addWidget(
            box
        )

        # Spare height collects below the box rather than stretching it.
        outer.addStretch(
            1
        )

    def update_info(self):

        window = self.window

        total_frames = (
            len(window.frames)
            if window.frames
            else 0
        )

        file_mb = getattr(
            window,
            "source_size_mb",
            0.0,
        )

        def mb_for(frame_count):
            if not total_frames:
                return 0.0
            return file_mb * (frame_count / total_frames)

        #
        # Cursor: position in time, size up to the cursor.
        #

        self.rows["Cursor"]["time"].setText(
            frame_to_timecode(window.current_frame)
        )

        self.rows["Cursor"]["mb"].setText(
            f"{mb_for(window.current_frame):.2f}"
        )

        #
        # Selection: duration + size of the highlighted saved range.
        #

        selection_text = "--:--:--.--"
        selection_frames = 0

        if window.selected_scene is not None:

            ranges = window.selection.ranges

            if 0 <= window.selected_scene < len(ranges):
                start, end = ranges[window.selected_scene]
                selection_frames = end - start + 1
                selection_text = frame_to_timecode(selection_frames)

        self.rows["Selection"]["time"].setText(selection_text)

        self.rows["Selection"]["mb"].setText(
            f"{mb_for(selection_frames):.2f}"
        )

        #
        # Output: total kept duration + estimated size.
        #

        total_kept = 0

        for start, end in window.selection.ranges:
            total_kept += end - start + 1

        self.rows["Output"]["time"].setText(
            frame_to_timecode(total_kept)
        )

        self.rows["Output"]["mb"].setText(
            f"{mb_for(total_kept):.2f}"
        )

        #
        # Program: the whole active video's length + size.
        #

        if total_frames:
            self.rows["Program"]["time"].setText(
                frame_to_timecode(total_frames - 1)
            )
            self.rows["Program"]["mb"].setText(
                f"{file_mb:.2f}"
            )
        else:
            self.rows["Program"]["time"].setText("--:--:--.--")
            self.rows["Program"]["mb"].setText("0.00")

        #
        # Joiner: combined duration (and rough size) of the joiner list.  Spans
        # whatever's queued for joining, independent of the open video.
        #

        joiner = getattr(window, "joiner_list", None)

        if joiner is not None and len(joiner):
            self.rows["Joiner"]["time"].setText(
                seconds_to_timecode(joiner.total_duration())
            )
            joiner_mb = joiner.total_size_mb()
            self.rows["Joiner"]["mb"].setText(
                f"{joiner_mb:.2f}" if joiner_mb > 0 else "--"
            )
        else:
            self.rows["Joiner"]["time"].setText("--:--:--.--")
            self.rows["Joiner"]["mb"].setText("0.00")
