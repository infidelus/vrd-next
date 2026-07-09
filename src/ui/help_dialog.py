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

_HELP_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "assets", "help")
)
_GUIDE = os.path.join(_HELP_DIR, "user-guide.html")


def guide_path():
    """The user guide for the current interface language.

    A translated guide is a copy of the English one named
    ``user-guide_<code>.html`` (e.g. ``user-guide_de.html``) sitting beside it in
    ``assets/help``.  If there isn't one for the chosen language, the English
    guide is used, so a missing translation never leaves the reader with nothing.
    """
    try:
        from config.loader import ensure_config
        code = ensure_config().get("settings", {}).get("language", "en")
    except Exception:
        code = "en"
    if code and code != "en":
        translated = os.path.join(_HELP_DIR, "user-guide_%s.html" % code)
        if os.path.exists(translated):
            return translated
    return _GUIDE


class UserGuideDialog(QDialog):
    """A simple HTML viewer for the bundled user guide."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("VRD Next User Guide"))
        self.resize(900, 720)

        layout = QVBoxLayout(self)

        self._guide = guide_path()
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)     # http links open in the browser
        self._browser.setStyleSheet("QTextBrowser { background-color: #1e1e22; }")
        if os.path.exists(self._guide):
            self._browser.setSource(QUrl.fromLocalFile(self._guide))
        else:
            self._browser.setHtml(
                "<h2>%s</h2><p>%s</p>" % (
                    self.tr("User guide not found"),
                    self.tr("The guide file appears to be missing from this "
                            "installation."),
                )
            )
        layout.addWidget(self._browser)

        row = QHBoxLayout()
        open_btn = QPushButton(self.tr("Open in Browser"))
        open_btn.clicked.connect(self._open_in_browser)
        row.addWidget(open_btn)
        row.addStretch(1)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _open_in_browser(self):
        if os.path.exists(self._guide):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._guide))
