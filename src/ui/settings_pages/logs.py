"""Logging page: where logs go, how many to keep, and how chatty they are."""

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSpinBox

from ui.settings_pages import SettingsPage
from ui.settings_widgets import PlainFolderRow, hint


class LoggingPage(SettingsPage):
    TITLE = "Logging"

    def build(self):
        s = self._settings()
        p = self._paths()

        self._log_row = PlainFolderRow(
            "Log files:",
            p.get("log_folder", ""),
            f"(default: {self._ctx.default_log_folder})",
        )
        self.add(self._log_row)

        keep_row = QHBoxLayout()
        keep_row.addWidget(QLabel("Number of log files to keep:"))
        self._log_max_files = QSpinBox()
        self._log_max_files.setRange(0, 3650)
        self._log_max_files.setMaximumWidth(90)
        self._log_max_files.setValue(int(s.get("log_max_files", 30)))
        self._log_max_files.setToolTip(
            "The oldest of the editor's log files beyond this many are deleted "
            "at startup. Set to 0 to keep every log."
        )
        keep_row.addWidget(self._log_max_files)
        keep_row.addStretch(1)
        self.add_layout(keep_row)
        self.add(hint(
            "Where the editor writes its per-day log files, and how many it "
            "keeps. The Watcher is a separate application and keeps its own "
            "logs - set those in the Watcher's own settings window."
        ))

        self._verbose_logging = QCheckBox("Verbose logging")
        self._verbose_logging.setChecked(s.get("verbose_logging", False))
        self.add(self._verbose_logging)
        self.add(hint(
            "Record extra-detailed export diagnostics (including the cutter's "
            "own output) to the log. Useful for chasing problems; off by "
            "default to keep logs readable."
        ))

    def save(self, config):
        settings = config.setdefault("settings", {})
        settings["log_max_files"] = self._log_max_files.value()
        settings["verbose_logging"] = self._verbose_logging.isChecked()

        paths = config.setdefault("paths", {})
        paths["log_folder"] = self._log_row.folder()
