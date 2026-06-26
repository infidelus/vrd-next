"""Files & folders page: opening behaviour and the working folders."""

from PySide6.QtWidgets import QCheckBox, QFrame

from ui.settings_pages import SettingsPage
from ui.settings_widgets import PathRow, hint


def _divider():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("color: #3a3d42;")
    return line


class FilesPage(SettingsPage):
    TITLE = "Files & folders"

    def build(self):
        s = self._settings()
        p = self._paths()

        self._qsf_on_open = QCheckBox("Quick Stream Fix on open")
        self._qsf_on_open.setChecked(s.get("qsf_on_open", False))
        self.add(self._qsf_on_open)
        self.add(hint(
            "When enabled, opened files are remuxed first to repair broken "
            "broadcast streams before loading."
        ))

        self._qsf_no_rewarn = QCheckBox(
            "Don't warn when re-running Quick Stream Fix"
        )
        self._qsf_no_rewarn.setChecked(s.get("qsf_no_rewarn", False))
        self.add(self._qsf_no_rewarn)
        self.add(hint(
            "VRD Next remembers files it has already Quick Stream Fixed and "
            "asks before repairing one again. Tick this to skip that prompt."
        ))

        self.add(_divider())

        self._open_row = PathRow(
            "Opening videos:",
            p.get("open_mode", "last"),
            p.get("open_folder", ""),
        )
        self.add(self._open_row)

        self._export_row = PathRow(
            "Saving videos:",
            p.get("export_mode", "last"),
            p.get("export_folder", ""),
        )
        self.add(self._export_row)

        self._project_row = PathRow(
            "Project files:",
            p.get("project_mode", "last"),
            p.get("project_folder", ""),
        )
        self.add(self._project_row)

    def save(self, config):
        settings = config.setdefault("settings", {})
        settings["qsf_on_open"] = self._qsf_on_open.isChecked()
        settings["qsf_no_rewarn"] = self._qsf_no_rewarn.isChecked()

        paths = config.setdefault("paths", {})
        paths["open_mode"] = self._open_row.mode()
        paths["open_folder"] = self._open_row.folder()
        paths["export_mode"] = self._export_row.mode()
        paths["export_folder"] = self._export_row.folder()
        paths["project_mode"] = self._project_row.mode()
        paths["project_folder"] = self._project_row.folder()
