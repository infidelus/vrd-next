"""Maintenance page: cached data, direct config editing, and reset."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox

from ui.settings_pages import SettingsPage
from ui.settings_widgets import hint
from PySide6.QtCore import QT_TRANSLATE_NOOP


class MaintenancePage(SettingsPage):
    TITLE = QT_TRANSLATE_NOOP("Settings", "Maintenance")

    def build(self):
        s = self._settings()

        cache_row = QHBoxLayout()
        cache_row.addWidget(QLabel(self.tr("Delete cached data older than")))
        self._cache_age = QSpinBox()
        self._cache_age.setRange(0, 3650)
        self._cache_age.setSuffix(" days")
        self._cache_age.setSpecialValueText("never")   # shown when value is 0
        self._cache_age.setValue(int(s.get("cache_max_age_days", 30)))
        cache_row.addWidget(self._cache_age)
        cache_row.addStretch(1)

        clear_btn = QPushButton(self.tr("Delete now"))
        clear_btn.clicked.connect(self._ctx.clear_cache)
        cache_row.addWidget(clear_btn)
        self.add_layout(cache_row)
        self.add(hint(
            self.tr("Cached frame indices and Quick Stream Fix records for files you "
            "haven't opened in this long are removed at startup. Set to 0 "
            "(never) to keep them indefinitely.")
        ))

        # Renamer match cache - separate age limit (it defaults to "never",
        # since renamer matches are usually worth keeping).
        rn_row = QHBoxLayout()
        rn_row.addWidget(QLabel(self.tr("Delete remembered renamer matches older than")))
        self._renamer_age = QSpinBox()
        self._renamer_age.setRange(0, 3650)
        self._renamer_age.setSuffix(" days")
        self._renamer_age.setSpecialValueText("never")
        self._renamer_age.setValue(int(s.get("renamer_cache_max_age_days", 0)))
        rn_row.addWidget(self._renamer_age)
        rn_row.addStretch(1)
        clear_rn = QPushButton(self.tr("Delete now"))
        clear_rn.clicked.connect(self._clear_renamer_cache)
        rn_row.addWidget(clear_rn)
        self.add_layout(rn_row)
        self.add(hint(
            self.tr("The TV and film renamer remember each TMDB/IMDb match so they "
            "don't look it up again. These are kept in their own file; purge "
            "old ones here, or set to 0 (never) to keep them indefinitely.")
        ))

        edit_row = QHBoxLayout()
        edit_cfg = QPushButton(self.tr("Edit config.json"))
        edit_cfg.setFocusPolicy(Qt.NoFocus)
        edit_cfg.clicked.connect(self._ctx.edit_config)
        edit_row.addWidget(edit_cfg)
        edit_row.addStretch(1)
        self.add_layout(edit_row)
        self.add(hint(
            self.tr("Edit every setting directly as text - including the keyboard "
            "shortcuts, which have no controls of their own here. Changes "
            "apply as soon as you save, and any clashing keys are flagged then.")
        ))

        restore_row = QHBoxLayout()
        restore = QPushButton(self.tr("Restore Default Settings"))
        restore.setFocusPolicy(Qt.NoFocus)
        restore.clicked.connect(self._ctx.restore_defaults)
        restore_row.addWidget(restore)
        restore_row.addStretch(1)
        self.add_layout(restore_row)
        self.add(hint(
            self.tr("Reset every setting - paths, options and keyboard shortcuts - "
            "back to its default. Your recordings and projects aren't affected.")
        ))

    def _clear_renamer_cache(self):
        from PySide6.QtWidgets import QMessageBox
        from addons.match_cache import clear as clear_renamer_cache
        clear_renamer_cache(self._config)
        QMessageBox.information(
            self, self.tr("Renamer cache cleared"),
            self.tr("The remembered renamer matches have been deleted."))

    def save(self, config):
        settings = config.setdefault("settings", {})
        settings["cache_max_age_days"] = self._cache_age.value()
        settings["renamer_cache_max_age_days"] = self._renamer_age.value()
