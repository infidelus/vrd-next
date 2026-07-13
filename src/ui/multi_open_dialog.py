"""The "Open Multiple Files" dialogue, modelled on VideoReDo's.

Selecting more than one file in Open Video brings this up instead of loading
just one: it shows the selected files in order, lets them be reordered (drag,
or the Sort List button for name order), and adds each file - whole - to the
Joiner list.  VideoReDo also offered "add as DVD titles" here; DVD authoring
isn't part of this project, so the joiner is the one destination.
"""

import os

from PySide6.QtCore import QFileInfo, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileIconProvider,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class MultiOpenDialog(QDialog):
    """Choose what to do with a multi-file selection from Open Video."""

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Open Multiple Files"))

        self._result_paths = None
        # Whether every file shares one folder - if so it's shown once in the
        # caption and the list stays to clean file names; otherwise each entry
        # carries its own folder alongside the name.
        folders = {os.path.dirname(p) for p in paths}
        self._common_folder = folders.pop() if len(folders) == 1 else None

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        intro = QLabel(self.tr(
            "Add these files to the Joiner list?  Each is added as a whole "
            "file, in the order below - drag to reorder."
        ))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        if self._common_folder:
            where = QLabel(self.tr("From %s") % self._common_folder)
            where.setStyleSheet("color: palette(mid);")
            where.setWordWrap(True)
            layout.addWidget(where)

        # The file list: drag to reorder, or Sort List for name order.
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setIconSize(QSize(20, 20))
        self.list.setUniformItemSizes(True)
        self._icons = QFileIconProvider()
        for p in paths:
            self.list.addItem(self._make_item(p))
        # Size the list to its rows (up to ten) rather than a fixed slab.
        row_h = max(24, self.list.sizeHintForRow(0))
        visible = min(10, max(3, len(paths)))
        self.list.setMinimumHeight(row_h * visible + 8)
        layout.addWidget(self.list, 1)

        bottom = QHBoxLayout()
        sort_btn = QPushButton(self.tr("Sort List"))
        sort_btn.setToolTip(self.tr("Sort the files by name."))
        sort_btn.clicked.connect(self._sort)
        bottom.addWidget(sort_btn)
        bottom.addStretch(1)

        buttons = QDialogButtonBox()
        add_btn = buttons.addButton(
            self.tr("Add To Joiner List"), QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        add_btn.setDefault(True)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

        self.setMinimumWidth(460)

    def _make_item(self, path):
        """A list row for one file: icon and file name, with the folder shown
        alongside only when the selection spans more than one folder.  The
        full path is always on the tooltip."""
        name = os.path.basename(path)
        if self._common_folder is None:
            folder = os.path.basename(os.path.dirname(path)) or \
                os.path.dirname(path)
            text = "%s   (%s)" % (name, folder)
        else:
            text = name
        item = QListWidgetItem(self._icons.icon(QFileInfo(path)), text)
        item.setToolTip(path)
        item.setData(Qt.UserRole, path)
        return item

    def _paths(self):
        return [self.list.item(i).data(Qt.UserRole)
                for i in range(self.list.count())]

    def _sort(self):
        """Sort the list by file name (then by folder, for duplicates)."""
        paths = sorted(
            self._paths(),
            key=lambda p: (os.path.basename(p).lower(), p.lower()))
        self.list.clear()
        for p in paths:
            self.list.addItem(self._make_item(p))

    def _accept(self):
        self._result_paths = self._paths()
        self.accept()

    def result_paths(self):
        """The files, in the order chosen, or None if cancelled."""
        return self._result_paths
