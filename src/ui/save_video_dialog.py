"""The Save Video dialog: pick an output profile from a list and a file, then
save - a single click-and-save flow modelled on VideoReDo's, replacing the
older "choose a format from a dropdown, then a separate file picker" path.

The profile list, model and persistence live in ``addons.output_profiles``;
this dialog just presents the enabled profiles, lets the user pick one and a
destination, and opens the Profile Options manager via its button.
"""

import os

from PySide6.QtCore import Qt, QCoreApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from addons.output_profiles import default_profiles, load_profiles


class SaveVideoDialog(QDialog):
    """Pick an output profile and a destination file in one step."""

    def __init__(self, config, suggested_path, source_ext, parent=None,
                 default_container=None, sample_source="",
                 preselect_profile=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Save Video"))
        self.setMinimumWidth(640)
        self.setMinimumHeight(460)

        self.config = config
        self._sample_source = sample_source
        self._source_ext = source_ext or ".ts"
        self._preselect_container = default_container
        # Exact profile name to preselect (used after a QSF reload to restore
        # the profile the user had already chosen).  Falls back to container
        # matching when None or when the named profile is no longer present.
        self._preselect_profile = preselect_profile
        self._result_path = None
        self._result_profile = None
        # A one-off, in-memory edit of the chosen profile for this export only.
        # It's never saved, so built-in profiles are left untouched and revert
        # the next time the dialog opens.
        self._override_profile = None

        # The directory + base name we build auto-suggestions from.  We use the
        # name as-is (no stripping - "(2011)" is a year, not a dedup suffix);
        # _apply_profile_path always rebuilds from this clean base before
        # de-duplicating, so nothing compounds.
        self._directory = os.path.dirname(suggested_path or "")
        self._base_stem = os.path.splitext(os.path.basename(suggested_path or ""))[0]
        # Once the user types a path or picks one, we stop auto-rewriting it.
        self._user_edited = False

        layout = QVBoxLayout(self)

        # --- output file --------------------------------------------------
        layout.addWidget(QLabel(self.tr("Output File")))
        file_row = QHBoxLayout()
        self.file_edit = QLineEdit(suggested_path or "")
        self.file_edit.textEdited.connect(self._on_path_edited)
        file_row.addWidget(self.file_edit, 1)
        select_btn = QPushButton(self.tr("Select File"))
        select_btn.clicked.connect(self._select_file)
        file_row.addWidget(select_btn)
        layout.addLayout(file_row)

        # --- selected profile name + Profile Options ----------------------
        layout.addWidget(QLabel(self.tr("Profile")))
        prof_row = QHBoxLayout()
        self.profile_field = QLineEdit()
        self.profile_field.setReadOnly(True)
        prof_row.addWidget(self.profile_field, 1)
        self.options_btn = QPushButton(self.tr("Profile Options\u2026"))
        self.options_btn.setToolTip(
            self.tr("Tweak the selected profile for this export only - the change isn't "
            "saved, so built-in profiles revert next time.  Manage and save "
            "profiles permanently from Tools \u2192 Manage Profiles.")
        )
        self.options_btn.clicked.connect(self._edit_for_export)
        prof_row.addWidget(self.options_btn)
        layout.addLayout(prof_row)

        # --- profile list -------------------------------------------------
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [self.tr("Profile"), self.tr("Codec"), self.tr("Container"), self.tr("Output Mode")]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(lambda _i: self._accept())
        layout.addWidget(self.table, 1)

        # --- favourites filter + buttons ----------------------------------
        bottom = QHBoxLayout()
        self.fav_only = QCheckBox(self.tr("Favourites Only"))
        self.fav_only.setChecked(True)
        self.fav_only.toggled.connect(self._fill_table)
        bottom.addWidget(self.fav_only)
        bottom.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).clicked.connect(self._accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

        self._reload_profiles()

    # -- profiles -----------------------------------------------------------
    def _reload_profiles(self, keep_name=None):
        """(Re)load the enabled profiles from config and refill the list."""
        loaded = [p for p in load_profiles(self.config) if p.enabled]
        if not loaded:
            loaded = load_profiles(self.config) or default_profiles()
        self._profiles = loaded
        # On the first load, honour an exact profile preselection (from a QSF
        # reload) if that profile still exists; otherwise fall back to
        # container matching.
        if keep_name is None and self._preselect_profile and any(
                p.name == self._preselect_profile for p in self._profiles):
            keep_name = self._preselect_profile
        # If favourites-only would hide the profile we want preselected, show all.
        want_name = keep_name
        if want_name is not None and not any(
                p.favourite and p.name == want_name for p in self._profiles):
            self.fav_only.setChecked(False)
        elif (self._preselect_container is not None and not any(
                p.favourite and p.container == self._preselect_container
                for p in self._profiles)):
            self.fav_only.setChecked(False)
        self._fill_table(keep_name=keep_name)

    def _edit_for_export(self):
        """Edit a copy of the selected profile for this export only.

        The copy is held in memory and used for the export; it's never written
        back, so built-in profiles stay at their defaults and a later export
        starts from the saved profile again."""
        from ui.profile_manager_dialog import ProfileEditDialog

        base = self._effective_profile()
        if base is None:
            QMessageBox.information(self, self.tr("Save Video"), self.tr("Please choose a profile."))
            return
        dlg = ProfileEditDialog(
            base.copy(), self, sample_source=self._sample_source
        )
        if dlg.exec() != QDialog.Accepted:
            return
        edited = dlg.result()
        # Keep the list-meta from the base; it's a transient, non-built-in copy.
        edited.favourite = base.favourite
        edited.enabled = base.enabled
        edited.builtin = False
        self._override_profile = edited
        self.profile_field.setText("%s  \u2014  edited for this export" % edited.name)
        self._apply_profile_path(edited)

    def _effective_profile(self):
        """The profile this export will actually use: the one-off edit if there
        is one, otherwise the selected profile."""
        return self._override_profile or self._selected_profile()

    # -- list ---------------------------------------------------------------
    def _visible_profiles(self):
        if self.fav_only.isChecked():
            favs = [p for p in self._profiles if p.favourite]
            if favs:
                return favs
        return list(self._profiles)

    def _fill_table(self, keep_name=None):
        rows = self._visible_profiles()
        self.table.setRowCount(len(rows))
        self._row_profiles = rows
        select_row = 0
        for i, p in enumerate(rows):
            # Built-in profiles and their labels are stored in English;
            # translate them for display.  User-typed names are left alone.
            def _pl(text):
                return QCoreApplication.translate("ProfileEditor", text)

            shown = _pl(p.name) if p.builtin else p.name
            name = ("\u2605 " if p.favourite else "   ") + shown
            cells = [name, _pl(p.codec_label), _pl(p.container_label),
                     _pl(p.output_mode_label)]
            for c, text in enumerate(cells):
                self.table.setItem(i, c, QTableWidgetItem(text))
            if keep_name is not None and p.name == keep_name:
                select_row = i
            elif (keep_name is None and self._preselect_container
                  and p.container == self._preselect_container):
                select_row = i
        if rows:
            self.table.selectRow(select_row)

    def _selected_profile(self):
        rows = getattr(self, "_row_profiles", [])
        idx = self.table.currentRow()
        if 0 <= idx < len(rows):
            return rows[idx]
        return None

    def _on_selection_changed(self):
        # Picking a different profile discards any one-off edit tied to the old
        # selection.
        self._override_profile = None
        p = self._selected_profile()
        if p is None:
            return
        self.profile_field.setText(p.name)
        self._apply_profile_path(p)

    def _on_path_edited(self, _text):
        # The user has taken control of the filename - stop auto-rewriting it.
        self._user_edited = True

    def _apply_profile_path(self, profile):
        """Update the output file for the selected profile.

        For an auto-suggested name we rebuild it from the clean base in the
        profile's default directory (or the export folder) and de-duplicate so
        an existing file gets a " (2)".  Once the user has chosen a path
        themselves we leave it alone, only keeping the extension in step with
        the container."""
        ext = profile.extension(self._source_ext)
        if self._user_edited:
            path = self.file_edit.text().strip()
            if path:
                root, _ = os.path.splitext(path)
                self.file_edit.setText(root + ext)
            return
        directory = profile.output_dir or self._directory
        path = os.path.join(directory, self._base_stem + ext)
        self.file_edit.setText(self._dedup(path))

    def _dedup(self, path):
        """Return path unchanged if free, else the same name with the lowest
        " (N)" suffix that doesn't already exist."""
        if not os.path.exists(path):
            return path
        directory, fname = os.path.split(path)
        stem, ext = os.path.splitext(fname)
        n = 2
        while True:
            candidate = os.path.join(directory, "%s (%d)%s" % (stem, n, ext))
            if not os.path.exists(candidate):
                return candidate
            n += 1

    # -- file ---------------------------------------------------------------
    def _select_file(self):
        p = self._effective_profile()
        ext = p.extension(self._source_ext) if p else self._source_ext
        label = {".mkv": "Matroska video", ".mp4": "MP4 video"}.get(
            ext, "Transport stream"
        )
        start = self.file_edit.text().strip() or ""
        ext_u = ext.upper()
        ext_globs = ("*%s *%s" % (ext, ext_u)) if ext_u != ext else ("*%s" % ext)
        chosen, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save Video As"), start,
            "%s (%s);;All files (*)" % (label, ext_globs),
        )
        if not chosen:
            return
        if p and not chosen.lower().endswith(ext.lower()):
            chosen += ext
        self._user_edited = True
        self.file_edit.setText(chosen)

    # -- accept -------------------------------------------------------------
    def _accept(self):
        path = self.file_edit.text().strip()
        profile = self._effective_profile()
        if profile is None:
            QMessageBox.information(self, self.tr("Save Video"), self.tr("Please choose a profile."))
            return
        if not path:
            QMessageBox.information(self, self.tr("Save Video"), self.tr("Please choose an output file."))
            return
        root, ext = os.path.splitext(path)
        want = profile.extension(self._source_ext)
        if ext.lower() != want.lower():
            path = root + want
        # Auto-suggested names are de-duplicated already, so this only catches a
        # name the user typed (or picked) that collides with an existing file.
        if os.path.exists(path):
            if QMessageBox.question(
                self, self.tr("Save Video"),
                "%s already exists.\n\nOverwrite it?" % os.path.basename(path),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return
        self._result_path = path
        self._result_profile = profile
        self.accept()

    def result_path(self):
        return self._result_path

    def result_profile(self):
        return self._result_profile
