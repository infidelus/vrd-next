"""General page: the editor's interface basics."""

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton

from ui.settings_pages import SettingsPage
from ui.settings_widgets import hint
from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

_FRAME_TYPE_OPTIONS = [
    (QT_TRANSLATE_NOOP("Settings", "None"), "none"),
    (QT_TRANSLATE_NOOP("Settings", "Thumbnails"), "thumbnails"),
    (QT_TRANSLATE_NOOP("Settings", "Preview"), "preview"),
    (QT_TRANSLATE_NOOP("Settings", "Both"), "both"),
]

_THEME_OPTIONS = [
    (QT_TRANSLATE_NOOP("Settings", "Follow system"), "system"),
    (QT_TRANSLATE_NOOP("Settings", "Light"), "light"),
    (QT_TRANSLATE_NOOP("Settings", "Dark"), "dark"),
]


class GeneralPage(SettingsPage):
    TITLE = QT_TRANSLATE_NOOP("Settings", "General")

    def build(self):
        s = self._settings()

        self._show_tooltips = QCheckBox(self.tr("Show tooltips on the transport controls"))
        self._show_tooltips.setChecked(s.get("show_tooltips", True))
        self.add(self._show_tooltips)
        self.add(hint(
            self.tr("Hover hints on the play, skip and marker buttons. Turn off once "
            "you know the controls. Takes effect after a restart.")
        ))

        ft_row = QHBoxLayout()
        ft_row.addWidget(QLabel(self.tr("Show frame type (I/P/B):")))
        self._frame_type_display = QComboBox()
        for label_text, _val in _FRAME_TYPE_OPTIONS:
            self._frame_type_display.addItem(QCoreApplication.translate("Settings", label_text))
        current = s.get("frame_type_display", "none")
        for i, (_label, val) in enumerate(_FRAME_TYPE_OPTIONS):
            if val == current:
                self._frame_type_display.setCurrentIndex(i)
                break
        ft_row.addStretch(1)
        ft_row.addWidget(self._frame_type_display)
        self.add_layout(ft_row)
        self.add(hint(
            self.tr("Overlay each frame's picture type (I, P or B) in the top-left "
            "corner of the thumbnail strip and/or the preview.")
        ))

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel(self.tr("Theme:")))
        self._theme = QComboBox()
        for label_text, _val in _THEME_OPTIONS:
            self._theme.addItem(QCoreApplication.translate("Settings", label_text))
        current_theme = s.get("theme", "system")
        for i, (_label, val) in enumerate(_THEME_OPTIONS):
            if val == current_theme:
                self._theme.setCurrentIndex(i)
                break
        theme_row.addStretch(1)
        theme_row.addWidget(self._theme)
        self.add_layout(theme_row)
        self.add(hint(
            self.tr("Follow the desktop's own colours, or pin a Light or Dark look for "
            "VRD Next. The editor's timeline and thumbnail bars stay dark in "
            "every theme. The change applies straight away.")
        ))

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel(self.tr("Language:")))
        self._language = QComboBox()
        from ui.i18n import available_languages
        self._languages = available_languages()
        for _code, display in self._languages:
            self._language.addItem(display)
        current_lang = s.get("language", "en")
        for i, (code, _display) in enumerate(self._languages):
            if code == current_lang:
                self._language.setCurrentIndex(i)
                break
        lang_row.addStretch(1)
        lang_row.addWidget(self._language)
        self.add_layout(lang_row)
        self.add(hint(
            self.tr("The interface language. English is built in; other languages appear "
            "here once their translation file is added to the translations "
            "folder. Takes effect after a restart.")
        ))

        self._restore_size_btn = QPushButton(self.tr("Restore default window size"))
        self._restore_size_btn.clicked.connect(self._ctx.restore_window_size)
        self.add(self._restore_size_btn)
        self.add(hint(self.tr("Un-maximise and reset the window to its default size.")))

    def save(self, config):
        settings = config.setdefault("settings", {})
        settings["show_tooltips"] = self._show_tooltips.isChecked()
        settings["frame_type_display"] = _FRAME_TYPE_OPTIONS[
            self._frame_type_display.currentIndex()
        ][1]
        settings["theme"] = _THEME_OPTIONS[self._theme.currentIndex()][1]
        settings["language"] = self._languages[self._language.currentIndex()][0]
