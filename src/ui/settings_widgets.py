"""
Reusable widgets and helpers for the Settings pages.

The Settings dialog is split into one page per category (see
``ui/settings_pages``); these are the building blocks shared across those pages -
the folder/file rows and the small heading/hint label helpers - kept here so a
page module only has to describe its own controls.

All user-facing text uses British English.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def heading(text):
    """A page heading label."""
    label = QLabel(text)
    label.setStyleSheet("font-weight: 600; font-size: 14px;")
    return label


def hint(text):
    """A greyed, word-wrapped explanatory line shown under a control."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #9aa0a6;")
    return label


class PathRow(QWidget):
    """A folder setting: a 'Last used / Fixed folder' mode chooser plus a
    folder field with a Browse button (the field is only enabled in fixed
    mode)."""

    def __init__(self, label_text, mode, folder, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(4)

        layout.addWidget(QLabel(label_text))

        row = QHBoxLayout()
        row.setSpacing(6)

        self._mode = QComboBox()
        self._mode.addItem("Remember last used folder", "last")
        self._mode.addItem("Always use this folder", "fixed")
        idx = self._mode.findData(mode if mode in ("last", "fixed") else "last")
        self._mode.setCurrentIndex(max(0, idx))
        self._mode.currentIndexChanged.connect(self._sync_enabled)
        row.addWidget(self._mode)

        self._folder = QLineEdit(folder or "")
        self._folder.setPlaceholderText("(no folder set)")
        row.addWidget(self._folder, 1)

        self._browse = QPushButton("Browse…")
        self._browse.setFocusPolicy(Qt.NoFocus)
        self._browse.clicked.connect(self._on_browse)
        row.addWidget(self._browse)

        layout.addLayout(row)

        self._sync_enabled()

    def _sync_enabled(self):
        is_fixed = self._mode.currentData() == "fixed"
        self._folder.setEnabled(is_fixed)
        self._browse.setEnabled(is_fixed)

    def _on_browse(self):
        start = self._folder.text() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose folder", start
        )
        if chosen:
            self._folder.setText(chosen)

    def mode(self):
        return self._mode.currentData()

    def folder(self):
        return self._folder.text().strip()


class PlainFolderRow(QWidget):
    """A single folder field with Browse (no last/fixed mode) - used for the
    log folder."""

    def __init__(self, label_text, folder, placeholder, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(4)

        layout.addWidget(QLabel(label_text))

        row = QHBoxLayout()
        row.setSpacing(6)

        self._folder = QLineEdit(folder or "")
        self._folder.setPlaceholderText(placeholder)
        row.addWidget(self._folder, 1)

        browse = QPushButton("Browse…")
        browse.setFocusPolicy(Qt.NoFocus)
        browse.clicked.connect(self._on_browse)
        row.addWidget(browse)

        layout.addLayout(row)

    def _on_browse(self):
        start = self._folder.text() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose folder", start
        )
        if chosen:
            self._folder.setText(chosen)

    def folder(self):
        return self._folder.text().strip()


class FileRow(QWidget):
    """A single file field with Browse (picks a file, not a folder) - used for
    the Comskip program and its .ini."""

    def __init__(self, label_text, value, placeholder, file_filter, parent=None):
        super().__init__(parent)

        self._filter = file_filter

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(4)

        layout.addWidget(QLabel(label_text))

        row = QHBoxLayout()
        row.setSpacing(6)

        self._path = QLineEdit(value or "")
        self._path.setPlaceholderText(placeholder)
        row.addWidget(self._path, 1)

        browse = QPushButton("Browse…")
        browse.setFocusPolicy(Qt.NoFocus)
        browse.clicked.connect(self._on_browse)
        row.addWidget(browse)

        layout.addLayout(row)

    def _on_browse(self):
        start = self._path.text() or os.path.expanduser("~")
        if os.path.isfile(start):
            start = os.path.dirname(start)
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Choose file", start, self._filter
        )
        if chosen:
            self._path.setText(chosen)

    def value(self):
        return self._path.text().strip()
