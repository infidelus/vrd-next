from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

from PySide6.QtGui import (
    QFont,
)

from PySide6.QtCore import Qt

from utils.timecode import (
    frame_to_timecode,
)


class SceneList(
    QTableWidget,
):

    def __init__(
            self,
            window,
    ):

        super().__init__()

        self.window = window

        self.setColumnCount(
            3
        )

        self.setHorizontalHeaderLabels(
            [
                "Scene Start",
                "Scene End",
                "Duration",
            ]
        )

        #
        # Compact VRD-style appearance
        #

        font = QFont(
            "Sans",
            8,
        )

        self.setFont(
            font
        )

        self.horizontalHeader().setFont(
            font
        )

        self.verticalHeader().hide()

        self.verticalHeader().setDefaultSectionSize(
            18
        )

        self.horizontalHeader().setStretchLastSection(
            True
        )

        self.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )

        self.setSelectionBehavior(
            QTableWidget.SelectRows
        )

        # Allow several scenes to be selected at once (Ctrl/Shift click) so
        # they can be removed in one go.
        self.setSelectionMode(
            QTableWidget.ExtendedSelection
        )

        self.setEditTriggers(
            QTableWidget.NoEditTriggers
        )

        self.setShowGrid(
            True
        )

        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )

        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )

        self.setFocusPolicy(
            Qt.StrongFocus
        )

    def refresh(
            self,
    ):

        ranges = (
            self.window.selection.ranges
        )

        self.setRowCount(
            len(
                ranges
            )
        )

        for row, (
                start,
                end,
        ) in enumerate(
            ranges
        ):
            duration = (
                    end
                    -
                    start
                    +
                    1
            )

            start_item = QTableWidgetItem(
                frame_to_timecode(
                    start,
                )
            )
            start_item.setTextAlignment(Qt.AlignCenter)
            self.setItem(
                row,
                0,
                start_item,
            )

            end_item = QTableWidgetItem(
                frame_to_timecode(
                    end,
                )
            )
            end_item.setTextAlignment(Qt.AlignCenter)
            self.setItem(
                row,
                1,
                end_item,
            )

            duration_item = QTableWidgetItem(
                frame_to_timecode(
                    duration,
                )
            )
            duration_item.setTextAlignment(Qt.AlignCenter)
            self.setItem(
                row,
                2,
                duration_item,
            )

        # The scene list is the single funnel every cut edit passes through, so
        # this is the natural place to keep the title's unsaved-changes marker
        # up to date.
        if hasattr(self.window, "_update_title"):
            self.window._update_title()

    def keyPressEvent(
            self,
            event,
    ):

        key = event.key()

        #
        # Transport keys belong to MainWindow
        #

        if key in (
                Qt.Key_Left,
                Qt.Key_Right,
                Qt.Key_Space,
                Qt.Key_Home,
                Qt.Key_End,
        ):
            event.ignore()

            return

        #
        # Ctrl+A is the global "run Comskip" shortcut, so intercept it here too
        # rather than let the table use it to select every row.
        #

        if self.window.keys.match(event, "detect_commercials"):
            self.window.detect_commercials()
            return

        #
        # Enter = jump to scene
        #

        if key in (
                Qt.Key_Return,
                Qt.Key_Enter,
        ):

            row = self.currentRow()

            if row >= 0:
                self.window.scene_double_clicked(
                    row,
                    0,
                )

            return

        #
        # Delete = remove the selected scene(s)
        #

        if key == Qt.Key_Delete:

            self.window.delete_selected_scenes()

            return

        #
        # Default table behaviour
        #

        super().keyPressEvent(
            event
        )

        #
        # Update info panel after Up/Down
        #

        row = self.currentRow()

        if row >= 0:
            self.window.selected_scene = row

            self.window.update_timecode()

            self.window.info_panel.update_info()