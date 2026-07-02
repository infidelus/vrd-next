"""The in-app User Guide viewer (Help -> User Guide).

Shows the bundled HTML guide in a QTextBrowser, with a button to open the same
file in the system browser for anyone who prefers reading it there.
"""

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

_GUIDE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "assets", "help", "user-guide.html")
)


class UserGuideDialog(QDialog):
    """A simple HTML viewer for the bundled user guide."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VRD Next User Guide")
        self.resize(900, 720)

        layout = QVBoxLayout(self)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)     # http links open in the browser
        self._browser.setStyleSheet("QTextBrowser { background-color: #1e1e22; }")
        if os.path.exists(_GUIDE):
            self._browser.setSource(QUrl.fromLocalFile(_GUIDE))
        else:
            self._browser.setHtml(
                "<h2>User guide not found</h2>"
                "<p>The guide file appears to be missing from this installation.</p>"
            )
        layout.addWidget(self._browser)

        row = QHBoxLayout()
        open_btn = QPushButton("Open in Browser")
        open_btn.clicked.connect(self._open_in_browser)
        row.addWidget(open_btn)
        row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _open_in_browser(self):
        if os.path.exists(_GUIDE):
            QDesktopServices.openUrl(QUrl.fromLocalFile(_GUIDE))
