"""
Settings dialog (Tools -> Settings).

A VideoReDo-style options window: a category list on the left and a stacked set
of pages on the right.  Clicking a category switches the right pane to the
matching page.

This module is just the shell.  Each category is a self-contained page in
``ui/settings_pages`` that builds and saves its own controls; the dialog lists
the pages, shows them in the stack, and on OK asks each to write itself back into
the config.  Shared, dialog-level actions (restore defaults, edit config.json,
reset the window size, clear the cache) live here and are handed to the pages
through a :class:`~ui.settings_pages.SettingsContext`.

All user-facing text uses British English.

The dialog edits a copy of the config dict and only writes changes back to the
caller when the user clicks OK, so Cancel discards cleanly.
"""

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QStackedWidget,
    QVBoxLayout,
)

from ui.settings_pages import SettingsContext, page_classes


class SettingsDialog(QDialog):

    # Custom result code: the user edited the config file directly via the
    # built-in editor (or restored defaults), so the caller should reload config
    # from disk rather than take this dialog's in-memory values.
    EDITED_EXTERNALLY = 2

    def __init__(self, config, config_file, default_log_folder, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumSize(640, 420)

        self._edited_externally = False

        # Work on a copy; only commit on OK.
        self._config = copy.deepcopy(config)
        self._config_file = config_file
        self._default_log_folder = default_log_folder

        ctx = SettingsContext(
            config_file=config_file,
            default_log_folder=default_log_folder,
            restore_defaults=self._restore_defaults,
            edit_config=self._edit_config_file,
            restore_window_size=self._restore_window_size,
            clear_cache=self._clear_cache_now,
        )

        outer = QVBoxLayout(self)

        body = QHBoxLayout()
        outer.addLayout(body, 1)

        # Left-hand category list.
        self._nav = QListWidget()
        self._nav.setMaximumWidth(180)
        body.addWidget(self._nav)

        # Right-hand stacked pages, one per category.
        self._pages = QStackedWidget()
        body.addWidget(self._pages, 1)

        self._page_widgets = []
        for page_cls in page_classes():
            page = page_cls(self._config, ctx)
            self._nav.addItem(page.TITLE)
            self._pages.addWidget(page)
            self._page_widgets.append(page)

        self._nav.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._nav.setCurrentRow(0)

        # OK / Cancel.
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    #
    # Shared, dialog-level actions (handed to pages via the context)
    #

    def _restore_defaults(self):
        from PySide6.QtWidgets import QMessageBox
        from config.defaults import DEFAULT_CONFIG
        from config.loader import save_config

        reply = QMessageBox.question(
            self,
            "Restore default settings",
            "This resets all settings - paths, options and keyboard shortcuts "
            "- to their defaults.\n\nYour recordings and projects are not "
            "affected. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        save_config(copy.deepcopy(DEFAULT_CONFIG))
        # Apply via the same path as a manual edit: the caller reloads the
        # (now-default) config from disk rather than writing the dialog's old
        # in-memory values back over it.
        self._edited_externally = True
        self.done(self.EDITED_EXTERNALLY)

    def _edit_config_file(self):
        from ui.config_editor import ConfigEditorDialog

        dialog = ConfigEditorDialog(self._config_file, self)
        if dialog.exec() == QDialog.Accepted:
            # The file was edited directly on disk.  The Settings UI still holds
            # the *old* values, so if we let OK run it would overwrite the manual
            # edit.  Instead we close via the "edited directly" path: the caller
            # reloads config from disk.
            self._edited_externally = True
            self.done(self.EDITED_EXTERNALLY)

    def _restore_window_size(self):
        window = self.parent()
        if window is not None and hasattr(
                window, "restore_default_window_size"
        ):
            window.restore_default_window_size()

    def _clear_cache_now(self):
        from PySide6.QtWidgets import QMessageBox
        from media.frame_index import clear_index_cache
        from repair import qsf_registry

        removed, freed = clear_index_cache()
        records = qsf_registry.clear_all()

        if freed >= 1024 * 1024:
            size_txt = f"{freed / (1024 * 1024):.1f} MB"
        elif freed >= 1024:
            size_txt = f"{freed / 1024:.0f} KB"
        else:
            size_txt = f"{freed} bytes"

        if removed or records:
            msg = (
                f"Cleared {removed} cached index"
                f"{'' if removed == 1 else 'es'} ({size_txt})"
            )
            if records:
                msg += (
                    f" and {records} Quick Stream Fix record"
                    f"{'' if records == 1 else 's'}"
                )
            msg += "."
        else:
            msg = "The cache is already empty."

        QMessageBox.information(self, "Cache cleared", msg)

    #
    # Result
    #

    def result_config(self):
        """Return the updated config dict (call after exec() returns Accepted).

        Each page writes its own controls back into the working config."""
        for page in self._page_widgets:
            page.save(self._config)
        return self._config
