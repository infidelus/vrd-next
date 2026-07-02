"""External tools page: the helper programs and API keys VRD Next relies on."""

import shutil

from PySide6.QtWidgets import QLabel, QLineEdit

from ui.settings_pages import SettingsPage
from ui.settings_widgets import FileRow, hint


class ToolsPage(SettingsPage):
    TITLE = "External tools"

    def build(self):
        s = self._settings()
        p = self._paths()

        self._comskip_bin_row = FileRow(
            "Comskip program:",
            p.get("comskip_binary", ""),
            "(path to the comskip executable)",
            "All files (*)",
        )
        self.add(self._comskip_bin_row)

        self._comskip_ini_row = FileRow(
            "Comskip .ini:",
            p.get("comskip_ini", ""),
            "(optional: path to comskip.ini)",
            "INI files (*.ini);;All files (*)",
        )
        self.add(self._comskip_ini_row)
        self.add(hint(
            "Comskip detects the commercial breaks for the Watcher. The .ini "
            "is optional - leave it blank to use Comskip's built-in defaults."
        ))

        # mkvmerge (mkvtoolnix): used for lossless MKV exports.  If the user
        # hasn't set a path, auto-fill the field with whatever's detected on
        # PATH so it's ready to save without browsing.
        mkvmerge_value = p.get("mkvmerge_binary", "") or (
            shutil.which("mkvmerge") or ""
        )
        self._mkvmerge_row = FileRow(
            "mkvmerge program:",
            mkvmerge_value,
            "(path to mkvmerge - install mkvtoolnix; auto-detected if on PATH)",
            "All files (*)",
        )
        self.add(self._mkvmerge_row)
        self.add(hint(
            "mkvmerge (part of MKVToolNix) is used for lossless MKV exports."
        ))

        # ffmpeg / ffprobe: the core tools behind export, join and stream
        # probing.  Auto-fill from PATH when the user hasn't set a path, so a
        # normal install is ready to save without browsing.
        ffmpeg_value = p.get("ffmpeg_binary", "") or (
            shutil.which("ffmpeg") or ""
        )
        self._ffmpeg_row = FileRow(
            "ffmpeg program:",
            ffmpeg_value,
            "(path to ffmpeg - auto-detected if on PATH)",
            "All files (*)",
        )
        self.add(self._ffmpeg_row)

        ffprobe_value = p.get("ffprobe_binary", "") or (
            shutil.which("ffprobe") or ""
        )
        self._ffprobe_row = FileRow(
            "ffprobe program:",
            ffprobe_value,
            "(path to ffprobe - auto-detected if on PATH)",
            "All files (*)",
        )
        self.add(self._ffprobe_row)
        self.add(hint(
            "ffmpeg and ffprobe do the cutting, joining and stream probing. "
            "They aren't included with VRD Next and aren't always pre-installed "
            "- if they're on your PATH they're detected automatically here, "
            "otherwise install them (or download a build) and set the paths. "
            "Point these at a specific build if you want a particular version."
        ))

        key_label = QLabel("TMDB API key")
        key_label.setStyleSheet("font-weight: bold;")
        self.add(key_label)

        self._tmdb_key = QLineEdit(s.get("tmdb_api_key", ""))
        self._tmdb_key.setEchoMode(QLineEdit.Password)
        self._tmdb_key.setPlaceholderText(
            "v3 API key from themoviedb.org/settings/api"
        )
        self.add(self._tmdb_key)
        self.add(hint(
            "Used by the TV and Film renamers (Extras menu) to look up titles. "
            "A free key is available from your TMDB account."
        ))

    def save(self, config):
        settings = config.setdefault("settings", {})
        settings["tmdb_api_key"] = self._tmdb_key.text().strip()

        paths = config.setdefault("paths", {})
        paths["comskip_binary"] = self._comskip_bin_row.value()
        paths["comskip_ini"] = self._comskip_ini_row.value()
        paths["mkvmerge_binary"] = self._mkvmerge_row.value()
        paths["ffmpeg_binary"] = self._ffmpeg_row.value()
        paths["ffprobe_binary"] = self._ffprobe_row.value()
