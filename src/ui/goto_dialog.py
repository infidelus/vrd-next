from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QDialogButtonBox,
)

from utils.timecode import (
    frame_to_timecode,
    parse_timecode,
)


class GoToTimecodeDialog(QDialog):
    """Jump the cursor to a timecode.

    Accepts the same timecode forms as the cursor box - HH:MM:SS.FF, the dotted
    variant, or the compact all-digits form (e.g. ``00080000`` = 00:08:00.00).
    Precede the value with ``+`` or ``-`` to jump RELATIVE to the current
    position by that amount.

    On accept, ``self.frame`` holds the absolute target frame (already clamped
    into range); it stays ``None`` if the dialog was cancelled.
    """

    def __init__(
            self,
            current_frame,
            max_frame,
            parent=None,
    ):
        super().__init__(parent)

        self.setWindowTitle(self.tr("Go to timecode"))
        self._current_frame = current_frame
        self._max_frame = max_frame
        self.frame = None

        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(self.tr("Enter a timecode:"))
        )

        self._edit = QLineEdit(
            frame_to_timecode(current_frame)
        )
        self._edit.selectAll()
        layout.addWidget(self._edit)

        hint = QLabel(
            self.tr("Precede with + or - for a relative jump")
        )
        hint.setStyleSheet("color:#9aa0a6;")
        layout.addWidget(hint)

        self._error = QLabel("")
        self._error.setStyleSheet("color:#e06c75;")
        self._error.setVisible(False)
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._edit.returnPressed.connect(self._on_ok)

    def _parse(self, text):
        """Return the absolute target frame, or None if the text is invalid."""
        text = text.strip()
        if not text:
            return None

        relative = False
        sign = 1

        if text[0] in "+-":
            relative = True
            if text[0] == "-":
                sign = -1
            text = text[1:].strip()
            if not text:
                return None

        # Everything is a timecode (this is what keeps the compact "00080000"
        # form working); the only special case is the leading +/- handled above.
        value = parse_timecode(text)
        if value is None:
            return None

        if relative:
            return self._current_frame + sign * value

        return value

    def _on_ok(self):
        frame = self._parse(
            self._edit.text()
        )

        if frame is None:
            self._error.setText(self.tr("That isn't a valid timecode."))
            self._error.setVisible(True)
            self._edit.selectAll()
            self._edit.setFocus()
            return

        if self._max_frame is not None and self._max_frame >= 0:
            frame = max(0, min(self._max_frame, frame))
        else:
            frame = max(0, frame)

        self.frame = frame
        self.accept()
