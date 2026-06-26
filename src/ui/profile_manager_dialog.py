"""The Profile Options manager and its editor.

The manager mirrors VideoReDo's "Manage Output Profiles": a list with an enable
checkbox and a favourite star per row, plus Add / Edit / Duplicate / Delete and
reorder, and Save / Cancel.  It works on a copy of the profile list so Cancel
discards every change; Save persists the whole list.

The editor is scoped to what VRD Next actually uses: name, container, audio
handling (smart copy vs re-encode AAC), display aspect, and a per-profile
default output directory.  Audio and aspect are saved now; the exporter starts
acting on them in the next step.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from addons.output_profiles import (
    AAC_BITRATES,
    OutputProfile,
    load_profiles,
    save_profiles,
)

_CONTAINERS = [("Match Source", "match"), ("Matroska MKV", "mkv"), ("MP4", "mp4")]
_AUDIO = [("Smart copy (lossless)", "copy"), ("Re-encode to AAC", "aac")]
_ASPECT = [("Source", "source"), ("4:3", "4:3"), ("16:9", "16:9")]


def _combo(pairs):
    c = QComboBox()
    for label, data in pairs:
        c.addItem(label, data)
    return c


def _select_data(combo, data):
    i = combo.findData(data)
    if i >= 0:
        combo.setCurrentIndex(i)


class ProfileEditDialog(QDialog):
    """Edit a single profile."""

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Output Profile")
        self.setMinimumWidth(460)
        self._result = None

        layout = QVBoxLayout(self)

        def row(label, widget):
            r = QHBoxLayout()
            lab = QLabel(label)
            lab.setMinimumWidth(140)
            r.addWidget(lab)
            r.addWidget(widget, 1)
            layout.addLayout(r)
            return widget

        self.name_edit = row("Profile name:", QLineEdit(profile.name))

        self.container_combo = row("Container:", _combo(_CONTAINERS))
        _select_data(self.container_combo, profile.container)

        self.audio_combo = row("Audio:", _combo(_AUDIO))
        _select_data(self.audio_combo, profile.audio)
        self.audio_combo.currentIndexChanged.connect(self._on_audio_changed)

        self.bitrate_combo = row("AAC bitrate:", QComboBox())
        self.bitrate_combo.addItem("Automatic", 0)
        for b in AAC_BITRATES:
            self.bitrate_combo.addItem("%d kbps" % b, b)
        _select_data(self.bitrate_combo, profile.audio_bitrate)

        self.aspect_combo = row("Display aspect:", _combo(_ASPECT))
        _select_data(self.aspect_combo, profile.aspect)

        # Default output directory (read-only field + Choose/Clear).
        dir_row = QHBoxLayout()
        lab = QLabel("Default directory:")
        lab.setMinimumWidth(140)
        dir_row.addWidget(lab)
        self.dir_edit = QLineEdit(profile.output_dir)
        self.dir_edit.setReadOnly(True)
        if not profile.output_dir:
            self.dir_edit.setPlaceholderText("(use the export folder)")
        dir_row.addWidget(self.dir_edit, 1)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose_dir)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.dir_edit.setText(""))
        dir_row.addWidget(choose)
        dir_row.addWidget(clear)
        layout.addLayout(dir_row)

        note = QLabel(
            "Display aspect is applied losslessly on export: 4:3 or 16:9 is "
            "stamped into the video's aspect signalling without re-encoding, so "
            "a wrongly-flagged recording plays at the right shape.  'Source' "
            "leaves it untouched."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._keep_builtin = profile.builtin
        self._on_audio_changed()

    def _on_audio_changed(self):
        self.bitrate_combo.setEnabled(self.audio_combo.currentData() == "aac")

    def _choose_dir(self):
        start = self.dir_edit.text().strip() or ""
        folder = QFileDialog.getExistingDirectory(
            self, "Choose default output folder", start
        )
        if folder:
            self.dir_edit.setText(folder)

    def _accept(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "Output Profile", "Please give the profile a name.")
            return
        self._result = OutputProfile(
            name,
            self.container_combo.currentData(),
            audio=self.audio_combo.currentData(),
            audio_bitrate=self.bitrate_combo.currentData(),
            aspect=self.aspect_combo.currentData(),
            output_dir=self.dir_edit.text().strip(),
            builtin=self._keep_builtin,
        )
        self.accept()

    def result(self):
        return self._result


class ProfileManagerDialog(QDialog):
    """Manage the list of output profiles."""

    COL_ENABLED, COL_FAV, COL_NAME, COL_CODEC, COL_CONTAINER, COL_MODE = range(6)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Output Profiles")
        self.setMinimumSize(620, 420)
        self.config = config
        self.profiles = [p.copy() for p in load_profiles(config)]
        self._filling = False

        layout = QVBoxLayout(self)

        body = QHBoxLayout()
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["On", "Fav", "Profile", "Codec", "Container", "Output Mode"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
        for c in (self.COL_ENABLED, self.COL_FAV, self.COL_CODEC,
                  self.COL_CONTAINER, self.COL_MODE):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.itemDoubleClicked.connect(lambda _i: self._edit())
        body.addWidget(self.table, 1)

        side = QVBoxLayout()
        for label, slot in (
            ("Add…", self._add),
            ("Edit…", self._edit),
            ("Duplicate", self._duplicate),
            ("Delete", self._delete),
            ("Move Up", lambda: self._move(-1)),
            ("Move Down", lambda: self._move(1)),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            side.addWidget(b)
        side.addStretch(1)
        body.addLayout(side)
        layout.addLayout(body)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).clicked.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._fill_table()

    # -- table --------------------------------------------------------------
    def _fill_table(self):
        self._filling = True
        self.table.setRowCount(len(self.profiles))
        for i, p in enumerate(self.profiles):
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            on.setCheckState(Qt.Checked if p.enabled else Qt.Unchecked)
            self.table.setItem(i, self.COL_ENABLED, on)

            fav = QTableWidgetItem("\u2605" if p.favourite else "\u2606")
            fav.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, self.COL_FAV, fav)

            self.table.setItem(i, self.COL_NAME, QTableWidgetItem(p.name))
            self.table.setItem(i, self.COL_CODEC, QTableWidgetItem(p.codec_label))
            self.table.setItem(i, self.COL_CONTAINER, QTableWidgetItem(p.container_label))
            self.table.setItem(i, self.COL_MODE, QTableWidgetItem(p.output_mode_label))
        self._filling = False

    def _on_item_changed(self, item):
        if self._filling or item.column() != self.COL_ENABLED:
            return
        row = item.row()
        if 0 <= row < len(self.profiles):
            self.profiles[row].enabled = item.checkState() == Qt.Checked

    def _on_cell_clicked(self, row, col):
        if col != self.COL_FAV or not (0 <= row < len(self.profiles)):
            return
        p = self.profiles[row]
        p.favourite = not p.favourite
        self._filling = True
        self.table.item(row, self.COL_FAV).setText("\u2605" if p.favourite else "\u2606")
        self._filling = False

    def _selected_row(self):
        idx = self.table.currentRow()
        return idx if 0 <= idx < len(self.profiles) else -1

    # -- actions ------------------------------------------------------------
    def _add(self):
        dlg = ProfileEditDialog(OutputProfile("New profile", "match"), self)
        if dlg.exec() == QDialog.Accepted:
            self.profiles.append(dlg.result())
            self._fill_table()
            self.table.selectRow(len(self.profiles) - 1)

    def _edit(self):
        row = self._selected_row()
        if row < 0:
            return
        if self.profiles[row].builtin:
            QMessageBox.information(
                self, "Built-in profile",
                "\u201c%s\u201d is a built-in profile and can't be edited "
                "here.  Use Duplicate to make your own editable copy."
                % self.profiles[row].name,
            )
            return
        dlg = ProfileEditDialog(self.profiles[row], self)
        if dlg.exec() == QDialog.Accepted:
            edited = dlg.result()
            edited.favourite = self.profiles[row].favourite
            edited.enabled = self.profiles[row].enabled
            self.profiles[row] = edited
            self._fill_table()
            self.table.selectRow(row)

    def _duplicate(self):
        row = self._selected_row()
        if row < 0:
            return
        dup = self.profiles[row].copy()
        dup.name = dup.name + " copy"
        dup.builtin = False
        self.profiles.insert(row + 1, dup)
        self._fill_table()
        self.table.selectRow(row + 1)

    def _delete(self):
        row = self._selected_row()
        if row < 0:
            return
        p = self.profiles[row]
        if p.builtin:
            QMessageBox.information(
                self, "Built-in profile",
                "\u201c%s\u201d is a built-in profile and can't be deleted."
                % p.name,
            )
            return
        if QMessageBox.question(
            self, "Delete Profile",
            "Delete the profile \u201c%s\u201d?" % p.name,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        del self.profiles[row]
        self._fill_table()
        if self.profiles:
            self.table.selectRow(min(row, len(self.profiles) - 1))

    def _move(self, delta):
        row = self._selected_row()
        if row < 0:
            return
        new = row + delta
        if not (0 <= new < len(self.profiles)):
            return
        self.profiles[row], self.profiles[new] = self.profiles[new], self.profiles[row]
        self._fill_table()
        self.table.selectRow(new)

    def _save(self):
        save_profiles(self.config, self.profiles)
        self.accept()
