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
import tempfile

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
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
    QSlider,
    QSpinBox,
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
_CROP = [
    ("None (lossless)", "none"),
    ("Auto-detect bars", "auto"),
    ("Fixed pixels", "fixed"),
]


def _combo(pairs):
    c = QComboBox()
    for label, data in pairs:
        c.addItem(label, data)
    return c


def _select_data(combo, data):
    i = combo.findData(data)
    if i >= 0:
        combo.setCurrentIndex(i)


class CropPreviewDialog(QDialog):
    """Shows a real frame from the open recording with the crop shaded in.

    A slider scrubs through the recording so the user can pick a frame to crop
    against; the four edge values shade the cropped-away regions live; and
    'Auto-detect' fills them from the bars on the current frame.  On OK it
    returns the chosen (top, bottom, left, right).
    """

    _BOX_W = 520
    _BOX_H = 320

    def __init__(self, top, bottom, left, right, sample_source="", parent=None,
                 auto_on_open=False):
        super().__init__(parent)
        self.setWindowTitle("Crop preview")
        self._sample = sample_source
        self._src_w = 0
        self._src_h = 0
        self._base = None          # fitted QPixmap of the current frame, no overlay
        self._result = None
        self._tmp_png = None
        self._auto_pending = auto_on_open

        from export import crop as _crop
        self._crop = _crop
        self._duration = _crop.source_duration(sample_source) if sample_source else 0.0

        v = QVBoxLayout(self)

        self._name_label = QLabel(
            os.path.basename(sample_source) if sample_source else "No recording open."
        )
        self._name_label.setStyleSheet("color: gray;")
        v.addWidget(self._name_label)

        self._view = QLabel("Loading…")
        self._view.setAlignment(Qt.AlignCenter)
        self._view.setFixedSize(self._BOX_W, self._BOX_H)
        self._view.setStyleSheet(
            "background:#1b1b1f; border:1px solid #3a3a3a; color:#777;"
        )
        v.addWidget(self._view, 0, Qt.AlignHCenter)

        # Scrub slider to pick a frame.
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Frame:"))
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, 1000)
        self._slider.setValue(400)                 # ~40% in by default
        self._slider.valueChanged.connect(self._on_scrub)
        srow.addWidget(self._slider, 1)
        v.addLayout(srow)

        # Edge values + auto-detect.
        crow = QHBoxLayout()
        self._spins = {}
        for key in ("Top", "Bottom", "Left", "Right"):
            crow.addWidget(QLabel(key))
            sp = QSpinBox()
            sp.setRange(0, 4000)
            sp.setSingleStep(2)
            sp.valueChanged.connect(self._render)
            crow.addWidget(sp)
            self._spins[key.lower()] = sp
        crow.addStretch(1)
        self._auto_btn = QPushButton("Auto-detect")
        self._auto_btn.clicked.connect(self._auto)
        crow.addWidget(self._auto_btn)
        v.addLayout(crow)

        for key, val in (("top", top), ("bottom", bottom),
                         ("left", left), ("right", right)):
            self._spins[key].blockSignals(True)
            self._spins[key].setValue(int(val))
            self._spins[key].blockSignals(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

        # Debounce scrubbing so we don't spawn ffmpeg on every pixel of drag.
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.setInterval(160)
        self._scrub_timer.timeout.connect(self._load_current_frame)

        # Load the first frame once the dialog is up.
        if sample_source:
            QTimer.singleShot(0, self._load_current_frame)
        else:
            self._view.setText("No recording open.")

    def _edges(self):
        return (self._spins["top"].value(), self._spins["bottom"].value(),
                self._spins["left"].value(), self._spins["right"].value())

    def _on_scrub(self, _value):
        self._scrub_timer.start()

    def _current_time(self):
        if self._duration > 0:
            return self._duration * (self._slider.value() / 1000.0)
        return 0.0

    def _load_current_frame(self):
        if not self._sample:
            return
        if self._tmp_png is None:
            fd, png = tempfile.mkstemp(suffix=".png", prefix="vrdcrop_")
            os.close(fd)
            self._tmp_png = png
        ok = self._crop.extract_frame(
            self._sample, self._tmp_png, at_seconds=self._current_time()
        )
        full = QPixmap(self._tmp_png) if ok else QPixmap()
        if full.isNull():
            self._view.setText("Couldn't read a frame here.")
            self._base = None
            return
        self._src_w, self._src_h = full.width(), full.height()
        # Fit the whole frame inside the fixed box, keeping aspect, so nothing is
        # ever clipped (top/bottom bars included).
        self._base = full.scaled(
            self._BOX_W, self._BOX_H, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._render()
        # If opened from auto mode, show what auto would crop on this frame.
        if self._auto_pending:
            self._auto_pending = False
            self._auto()

    def _auto(self):
        if not self._sample:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            edges = self._crop.detect_crop_window(
                self._sample, self._current_time(), self._src_w, self._src_h
            )
        finally:
            QApplication.restoreOverrideCursor()
        for key, val in zip(("top", "bottom", "left", "right"), edges):
            self._spins[key].blockSignals(True)
            self._spins[key].setValue(val)
            self._spins[key].blockSignals(False)
        self._render()

    def _render(self):
        if self._base is None or not (self._src_w and self._src_h):
            return
        pix = self._base.copy()
        sx = pix.width() / self._src_w
        sy = pix.height() / self._src_h
        top, bottom, left, right = self._edges()
        t, b = int(top * sy), int(bottom * sy)
        l, r = int(left * sx), int(right * sx)
        painter = QPainter(pix)
        shade = QColor(0, 0, 0, 150)
        if t:
            painter.fillRect(0, 0, pix.width(), t, shade)
        if b:
            painter.fillRect(0, pix.height() - b, pix.width(), b, shade)
        if l:
            painter.fillRect(0, 0, l, pix.height(), shade)
        if r:
            painter.fillRect(pix.width() - r, 0, r, pix.height(), shade)
        painter.setPen(QColor(47, 155, 255))
        painter.drawRect(
            l, t, max(0, pix.width() - l - r - 1),
            max(0, pix.height() - t - b - 1)
        )
        painter.end()
        self._view.setPixmap(pix)
        self._view.repaint()       # force an immediate redraw

    def _accept(self):
        self._result = self._edges()
        self.accept()

    def result_box(self):
        return self._result

    def cleanup(self):
        if self._tmp_png and os.path.exists(self._tmp_png):
            try:
                os.remove(self._tmp_png)
            except OSError:
                pass


class ProfileEditDialog(QDialog):
    """Edit a single profile."""

    def __init__(self, profile, parent=None, sample_source=""):
        super().__init__(parent)
        self.setWindowTitle("Output Profile")
        self.setMinimumWidth(460)
        self._result = None
        self._sample_source = sample_source

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

        self.crop_combo = row("Cropping:", _combo(_CROP))
        _select_data(self.crop_combo, getattr(profile, "crop_mode", "none"))
        self.crop_combo.currentIndexChanged.connect(self._on_crop_changed)

        # Edge amounts for "Fixed pixels"; greyed out unless that mode is chosen.
        crop_row = QHBoxLayout()
        clab = QLabel("Crop pixels:")
        clab.setMinimumWidth(140)
        crop_row.addWidget(clab)
        self.crop_spins = {}
        cur_crop = getattr(profile, "crop", (0, 0, 0, 0))
        for i, key in enumerate(("Top", "Bottom", "Left", "Right")):
            crop_row.addWidget(QLabel(key))
            sp = QSpinBox()
            sp.setRange(0, 4000)
            sp.setSingleStep(2)
            sp.setValue(int(cur_crop[i]) if i < len(cur_crop) else 0)
            crop_row.addWidget(sp)
            self.crop_spins[key.lower()] = sp
        self.crop_preview_btn = QPushButton("Preview…")
        self.crop_preview_btn.clicked.connect(self._open_crop_preview)
        crop_row.addWidget(self.crop_preview_btn)
        layout.addLayout(crop_row)

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
            "leaves it untouched.\n\n"
            "Cropping removes black bars, but unlike everything else it "
            "re-encodes the video (slower, not lossless).  'Auto-detect' finds "
            "the bars per file; 'Fixed pixels' uses the amounts above."
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
        self._on_crop_changed()

    def _on_crop_changed(self):
        mode = self.crop_combo.currentData()
        fixed = mode == "fixed"
        for sp in self.crop_spins.values():
            sp.setEnabled(fixed)
        # Preview is for seeing the crop on a real frame, so it needs a crop
        # mode (not "none") and a recording open to preview against.
        if mode == "none":
            self.crop_preview_btn.setEnabled(False)
            self.crop_preview_btn.setToolTip(
                "Set cropping to Auto-detect or Fixed to preview."
            )
        elif not self._sample_source:
            self.crop_preview_btn.setEnabled(False)
            self.crop_preview_btn.setToolTip(
                "Open a recording first to preview the crop."
            )
        else:
            self.crop_preview_btn.setEnabled(True)
            self.crop_preview_btn.setToolTip("")

    def _open_crop_preview(self):
        cur = (self.crop_spins["top"].value(), self.crop_spins["bottom"].value(),
               self.crop_spins["left"].value(), self.crop_spins["right"].value())
        dlg = CropPreviewDialog(
            *cur, sample_source=self._sample_source, parent=self,
            auto_on_open=(self.crop_combo.currentData() == "auto"),
        )
        accepted = dlg.exec()
        box = dlg.result_box()
        dlg.cleanup()
        if accepted and box:
            for key, val in zip(("top", "bottom", "left", "right"), box):
                self.crop_spins[key].setValue(int(val))
            # Setting a crop by eye means fixed pixel values.
            _select_data(self.crop_combo, "fixed")
            self._on_crop_changed()

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
            crop_mode=self.crop_combo.currentData(),
            crop=(
                self.crop_spins["top"].value(),
                self.crop_spins["bottom"].value(),
                self.crop_spins["left"].value(),
                self.crop_spins["right"].value(),
            ),
            output_dir=self.dir_edit.text().strip(),
            builtin=self._keep_builtin,
        )
        self.accept()

    def result(self):
        return self._result


class ProfileManagerDialog(QDialog):
    """Manage the list of output profiles."""

    COL_ENABLED, COL_FAV, COL_NAME, COL_CODEC, COL_CONTAINER, COL_MODE = range(6)

    def __init__(self, config, parent=None, sample_source=""):
        super().__init__(parent)
        self.setWindowTitle("Manage Output Profiles")
        self.setMinimumSize(620, 420)
        self.config = config
        self._sample_source = sample_source
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
        dlg = ProfileEditDialog(
            OutputProfile("New profile", "match"), self,
            sample_source=self._sample_source,
        )
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
        dlg = ProfileEditDialog(
            self.profiles[row], self, sample_source=self._sample_source
        )
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
