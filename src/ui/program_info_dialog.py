"""Read-only dialog showing the open file's programme information.

Mirrors VideoReDo's "Program Information" window: File, Video and Audio
sections, with a Copy to clipboard button.  Opened from Tools > Show Video
Programme Info or the configured shortcut (Ctrl+L by default).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
    QFrame,
    QApplication,
)

from utils.program_info import gather_program_info, to_plaintext


class ProgramInfoDialog(QDialog):

    def __init__(self, source_path, frame_count=None, fps=None, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Programme Information")
        self._sections = gather_program_info(source_path, frame_count, fps)

        outer = QVBoxLayout(self)

        # Scrollable so a file with several audio tracks can't grow the window
        # off-screen.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # Never scroll/stretch sideways: a long value wraps to the width instead.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(4, 4, 4, 4)

        # Fields rendered full-width (their own wrapping row) so a long value -
        # notably the filename - wraps down the window rather than stretching it
        # sideways.  These go in their own horizontal row rather than the grid,
        # because QGridLayout mis-handles the height of a wrapped, spanned cell.
        full_width = {"Name"}

        for title, rows in self._sections:
            header = QLabel(title)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            body.addWidget(header)

            grid_rows = []
            for label, value in rows:
                if label not in full_width:
                    grid_rows.append((label, value))
                    continue

                row = QHBoxLayout()
                row.setContentsMargins(8, 0, 0, 0)
                row.setSpacing(8)
                lab = QLabel(f"{label}:")
                val = QLabel(str(value))
                val.setWordWrap(True)
                val.setAlignment(Qt.AlignLeft | Qt.AlignTop)
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                val.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
                row.addWidget(lab, 0, Qt.AlignTop)
                row.addWidget(val, 1, Qt.AlignTop)
                body.addLayout(row)

            if not grid_rows:
                continue

            grid = QGridLayout()
            grid.setContentsMargins(8, 0, 0, 8)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(2)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(3, 1)

            # Two label/value pairs per row for a compact, VRD-like layout.
            for idx, (label, value) in enumerate(grid_rows):
                r = idx // 2
                c = (idx % 2) * 2

                lab = QLabel(f"{label}:")
                val = QLabel(str(value))
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)

                grid.addWidget(lab, r, c, Qt.AlignLeft | Qt.AlignTop)
                grid.addWidget(val, r, c + 1, Qt.AlignLeft | Qt.AlignTop)

            body.addLayout(grid)

        body.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # Buttons: Copy to clipboard (left), OK (right).
        buttons = QHBoxLayout()

        copy_button = QPushButton("Copy to clipboard")
        copy_button.clicked.connect(self._copy_to_clipboard)
        buttons.addWidget(copy_button)

        buttons.addStretch(1)

        ok_button = QPushButton("OK")
        ok_button.setDefault(True)
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(ok_button)

        outer.addLayout(buttons)

        self.resize(560, 520)

    def _copy_to_clipboard(self):
        QApplication.clipboard().setText(
            to_plaintext(self._sections)
        )
