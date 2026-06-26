"""
Settings pages - one module per category in the Settings dialog.

Each page is a self-contained :class:`SettingsPage`: it builds its own controls
from the config in ``build()`` and writes them back in ``save()``.  The dialog
(``ui/settings_dialog.py``) is just a shell that lists the pages, shows them in a
stack, and calls ``save()`` on each when the user clicks OK.  Adding a setting
means editing one page module; adding a whole category means adding one module
and listing it in :data:`PAGES`.

Shared actions that belong to the dialog rather than a single control - restoring
defaults, editing config.json directly, resetting the window size, clearing the
cache - are reached through the :class:`SettingsContext` handed to each page, so
a page never needs a reference to the dialog itself.

All user-facing text uses British English.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ui.settings_widgets import heading


class SettingsContext:
    """The bits of shared state and dialog-level actions a page may need.

    ``config_file`` / ``default_log_folder`` are values; the rest are callables
    the dialog wires to its own handlers (each closes the dialog or touches the
    main window, so they live on the dialog, not on a page)."""

    def __init__(self, config_file, default_log_folder,
                 restore_defaults, edit_config,
                 restore_window_size, clear_cache):
        self.config_file = config_file
        self.default_log_folder = default_log_folder
        self.restore_defaults = restore_defaults
        self.edit_config = edit_config
        self.restore_window_size = restore_window_size
        self.clear_cache = clear_cache


class SettingsPage(QWidget):
    """Base for a Settings page: a top-aligned column with the page heading
    already in place.  Subclasses set :attr:`TITLE`, fill in :meth:`build`, and
    (if they have anything to persist) override :meth:`save`."""

    TITLE = ""

    def __init__(self, config, ctx, parent=None):
        super().__init__(parent)
        self._config = config
        self._ctx = ctx
        self._layout = QVBoxLayout(self)
        self._layout.setAlignment(Qt.AlignTop)
        self._layout.addWidget(heading(self.TITLE))
        self.build()

    # -- helpers ---------------------------------------------------------- #

    def _settings(self):
        return self._config.get("settings", {})

    def _paths(self):
        return self._config.get("paths", {})

    def add(self, widget):
        self._layout.addWidget(widget)

    def add_layout(self, layout):
        self._layout.addLayout(layout)

    # -- to override ------------------------------------------------------ #

    def build(self):
        raise NotImplementedError

    def save(self, config):
        """Write this page's controls back into ``config`` (in place)."""


def page_classes():
    """The ordered page classes shown in the dialog's nav."""
    from ui.settings_pages.general import GeneralPage
    from ui.settings_pages.files import FilesPage
    from ui.settings_pages.logs import LoggingPage
    from ui.settings_pages.tools import ToolsPage
    from ui.settings_pages.maintenance import MaintenancePage

    return [
        GeneralPage,
        FilesPage,
        LoggingPage,
        ToolsPage,
        MaintenancePage,
    ]
