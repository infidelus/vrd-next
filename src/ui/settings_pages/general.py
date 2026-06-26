"""General page: the editor's interface basics."""

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton

from ui.settings_pages import SettingsPage
from ui.settings_widgets import hint

_FRAME_TYPE_OPTIONS = [
    ("None", "none"),
    ("Thumbnails", "thumbnails"),
    ("Preview", "preview"),
    ("Both", "both"),
]


class GeneralPage(SettingsPage):
    TITLE = "General"

    def build(self):
        s = self._settings()

        self._show_tooltips = QCheckBox("Show tooltips on the transport controls")
        self._show_tooltips.setChecked(s.get("show_tooltips", True))
        self.add(self._show_tooltips)
        self.add(hint(
            "Hover hints on the play, skip and marker buttons. Turn off once "
            "you know the controls. Takes effect after a restart."
        ))

        ft_row = QHBoxLayout()
        ft_row.addWidget(QLabel("Show frame type (I/P/B):"))
        self._frame_type_display = QComboBox()
        for label_text, _val in _FRAME_TYPE_OPTIONS:
            self._frame_type_display.addItem(label_text)
        current = s.get("frame_type_display", "none")
        for i, (_label, val) in enumerate(_FRAME_TYPE_OPTIONS):
            if val == current:
                self._frame_type_display.setCurrentIndex(i)
                break
        ft_row.addStretch(1)
        ft_row.addWidget(self._frame_type_display)
        self.add_layout(ft_row)
        self.add(hint(
            "Overlay each frame's picture type (I, P or B) in the top-left "
            "corner of the thumbnail strip and/or the preview."
        ))

        self._restore_size_btn = QPushButton("Restore default window size")
        self._restore_size_btn.clicked.connect(self._ctx.restore_window_size)
        self.add(self._restore_size_btn)
        self.add(hint("Un-maximise and reset the window to its default size."))

    def save(self, config):
        settings = config.setdefault("settings", {})
        settings["show_tooltips"] = self._show_tooltips.isChecked()
        settings["frame_type_display"] = _FRAME_TYPE_OPTIONS[
            self._frame_type_display.currentIndex()
        ][1]
