"""Joiner editing dialog - Phase 1 (list management + persistence).

Modelled on VideoReDo's "Joiner editing" window: an ordered list of segments
with reorder / remove / describe controls, a File menu to load and save the
joiner list (our .vjr format), and the usual toggles.  Rendering the joined
video ("Create video from joiner list") and title cards arrive in later phases,
so those controls are present but disabled.

The dialog edits a working copy of the joiner list, so Cancel discards changes
and OK keeps them; the caller adopts result_list() on OK.  "Edit selection"
accepts the dialog and sets entry_to_edit, so the caller can load that entry
back into the editor.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QAbstractItemView,
    QPushButton,
    QCheckBox,
    QMenuBar,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QLineEdit,
    QLabel,
    QGroupBox,
    QDoubleSpinBox,
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QFormLayout,
    QDialogButtonBox,
)

from project.joiner import JoinerList, JoinerEntry, JOINER_EXT


def _fmt_clock(seconds):
    seconds = max(0, int(round(seconds)))
    return "%02d:%02d:%02d" % (
        seconds // 3600, (seconds % 3600) // 60, seconds % 60,
    )


def _fmt_tc(seconds, fps):
    """HH:MM:SS.FF for a scene boundary, matching the editor's timecode."""
    rate = int(round(fps)) or 25
    frame = int(round(max(0.0, seconds) * rate))
    return "%02d:%02d:%02d.%02d" % (
        frame // (rate * 3600),
        (frame // (rate * 60)) % 60,
        (frame // rate) % 60,
        frame % rate,
    )


class TitleEditorDialog(QDialog):
    """Small editor for a title card: text, optional subtitle, duration and
    background/text colours."""

    def __init__(self, entry=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Title Card")
        self._bg = QColor(entry.bg_color if entry else "#000000")
        self._fg = QColor(entry.text_color if entry else "#FFFFFF")
        self._bg_image = entry.bg_image if entry else ""

        form = QFormLayout(self)
        self.ed_title = QLineEdit(entry.text if entry else "")
        self.ed_title.setPlaceholderText("Main text")
        self.ed_sub = QLineEdit(entry.subtitle if entry else "")
        self.ed_sub.setPlaceholderText("Optional second line")

        self.sp_dur = QDoubleSpinBox()
        self.sp_dur.setRange(0.5, 120.0)
        self.sp_dur.setDecimals(1)
        self.sp_dur.setSingleStep(0.5)
        self.sp_dur.setSuffix(" seconds")
        self.sp_dur.setValue(entry.duration if entry and entry.duration else 5.0)

        self.btn_bg = QPushButton()
        self.btn_bg.clicked.connect(self._pick_bg)
        self.btn_fg = QPushButton()
        self.btn_fg.clicked.connect(self._pick_fg)
        self._update_swatches()

        # Optional background image: a "Choose…" button (showing the filename
        # once picked) with a "Clear" button, plus a scaling mode that decides
        # how an odd-shaped image maps onto the card's frame.
        self.btn_img = QPushButton()
        self.btn_img.clicked.connect(self._pick_image)
        self.btn_img_clear = QPushButton("Clear")
        self.btn_img_clear.clicked.connect(self._clear_image)
        img_row = QHBoxLayout()
        img_row.setContentsMargins(0, 0, 0, 0)
        img_row.addWidget(self.btn_img, 1)
        img_row.addWidget(self.btn_img_clear)

        self.cb_scaling = QComboBox()
        self.cb_scaling.addItem("Fill frame (may crop edges)", "fill")
        self.cb_scaling.addItem("Fit inside (letterbox)", "fit")
        self.cb_scaling.addItem("Stretch (may distort)", "stretch")
        want = (entry.bg_scaling if entry else "fill") or "fill"
        self.cb_scaling.setCurrentIndex(max(0, self.cb_scaling.findData(want)))
        self._update_image_state()

        form.addRow("Title:", self.ed_title)
        form.addRow("Subtitle:", self.ed_sub)
        form.addRow("Duration:", self.sp_dur)
        form.addRow("Background colour:", self.btn_bg)
        form.addRow("Background image:", img_row)
        form.addRow("Image scaling:", self.cb_scaling)
        form.addRow("Text colour:", self.btn_fg)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.ed_title.setFocus()

    @staticmethod
    def _ink(colour):
        # Readable text colour for a swatch button background.
        luma = colour.red() * 0.299 + colour.green() * 0.587 \
            + colour.blue() * 0.114
        return "#000000" if luma > 140 else "#FFFFFF"

    def _update_swatches(self):
        for btn, col in ((self.btn_bg, self._bg), (self.btn_fg, self._fg)):
            btn.setText(col.name())
            btn.setStyleSheet(
                "background-color:%s; color:%s;" % (col.name(), self._ink(col)))

    def _pick_bg(self):
        col = QColorDialog.getColor(self._bg, self, "Background colour")
        if col.isValid():
            self._bg = col
            self._update_swatches()

    def _pick_fg(self):
        col = QColorDialog.getColor(self._fg, self, "Text colour")
        if col.isValid():
            self._fg = col
            self._update_swatches()

    def _update_image_state(self):
        # Reflect the current image choice on the button, and only let the
        # scaling and clear controls matter when an image is actually set.
        if self._bg_image:
            self.btn_img.setText(os.path.basename(self._bg_image))
            self.btn_img.setToolTip(self._bg_image)
        else:
            self.btn_img.setText("Choose image…")
            self.btn_img.setToolTip("")
        self.btn_img_clear.setEnabled(bool(self._bg_image))
        self.cb_scaling.setEnabled(bool(self._bg_image))

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose background image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)"
            ";;All files (*)")
        if path:
            self._bg_image = path
            self._update_image_state()

    def _clear_image(self):
        self._bg_image = ""
        self._update_image_state()

    def values(self):
        return {
            "text": self.ed_title.text(),
            "subtitle": self.ed_sub.text(),
            "duration": float(self.sp_dur.value()),
            "bg_color": self._bg.name(),
            "text_color": self._fg.name(),
            "bg_image": self._bg_image,
            "bg_scaling": self.cb_scaling.currentData() or "fill",
        }


class JoinerDialog(QDialog):

    def __init__(self, joiner_list, joiner_dir="", parent=None):
        super().__init__(parent)

        self.setWindowTitle("Joiner editing")
        self.resize(660, 430)

        # Work on a copy so Cancel discards and OK keeps.
        self._list = JoinerList()
        self._list.path = joiner_list.path
        self._list.entries = [
            JoinerEntry.from_dict(e.to_dict()) for e in joiner_list.entries
        ]
        self._joiner_dir = joiner_dir
        self.entry_to_edit = None             # set by "Edit selection"
        self.create_requested = False         # set by "Create video…"

        outer = QVBoxLayout(self)

        # ---- File menu --------------------------------------------------
        menubar = QMenuBar(self)
        file_menu = menubar.addMenu("File")
        file_menu.addAction("Load Joiner List…", self._load)
        file_menu.addAction("Save Joiner List", self._save)
        file_menu.addAction("Save Joiner List As…", self._save_as)
        outer.setMenuBar(menubar)

        # ---- List + side buttons ---------------------------------------
        mid = QHBoxLayout()

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Filename", "Description", "Duration"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 200)
        self.tree.currentItemChanged.connect(lambda *_: self._sync_buttons())
        self.tree.itemDoubleClicked.connect(lambda *_: self._edit_selection())
        mid.addWidget(self.tree, 1)

        side = QVBoxLayout()
        self.btn_up = QPushButton("Up")
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down = QPushButton("Down")
        self.btn_down.clicked.connect(lambda: self._move(1))
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self._remove)
        self.btn_desc = QPushButton("Description")
        self.btn_desc.clicked.connect(self._edit_description)
        self.btn_edit = QPushButton("Edit selection")
        self.btn_edit.clicked.connect(self._edit_selection)
        self.btn_title = QPushButton("Add title")
        self.btn_title.clicked.connect(self._add_title)
        for b in (self.btn_up, self.btn_down, self.btn_remove,
                  self.btn_desc, self.btn_edit, self.btn_title):
            side.addWidget(b)
        side.addStretch(1)
        mid.addLayout(side)
        outer.addLayout(mid)

        # ---- Toggles ----------------------------------------------------
        toggles = QHBoxLayout()
        self.chk_fullpath = QCheckBox("Display full path name")
        self.chk_fullpath.toggled.connect(lambda *_: self._refresh())
        self.chk_clear_after = QCheckBox("Clear after successful save/queue")
        toggles.addWidget(self.chk_fullpath)
        toggles.addStretch(1)
        toggles.addWidget(self.chk_clear_after)
        outer.addLayout(toggles)

        # ---- Per-clip fade (to/from black) -----------------------------
        # Reflects the selected entry; edits write straight back to it.  A
        # non-zero fade forces the whole join to be re-encoded.
        self._loading_fade = False
        fade_box = QGroupBox("Fade to/from black (selected clip)")
        fade_row = QHBoxLayout(fade_box)

        fade_row.addWidget(QLabel("In:"))
        self.sp_fade_in = QDoubleSpinBox()
        self.sp_fade_in.setRange(0.0, 10.0)
        self.sp_fade_in.setSingleStep(0.5)
        self.sp_fade_in.setDecimals(1)
        self.sp_fade_in.setSuffix(" s")
        self.sp_fade_in.valueChanged.connect(self._fade_changed)
        fade_row.addWidget(self.sp_fade_in)

        fade_row.addSpacing(16)
        fade_row.addWidget(QLabel("Out:"))
        self.sp_fade_out = QDoubleSpinBox()
        self.sp_fade_out.setRange(0.0, 10.0)
        self.sp_fade_out.setSingleStep(0.5)
        self.sp_fade_out.setDecimals(1)
        self.sp_fade_out.setSuffix(" s")
        self.sp_fade_out.valueChanged.connect(self._fade_changed)
        fade_row.addWidget(self.sp_fade_out)

        fade_row.addStretch(1)
        outer.addWidget(fade_box)

        # ---- Bottom buttons --------------------------------------------
        bottom = QHBoxLayout()
        self.btn_clear_all = QPushButton("Clear all")
        self.btn_clear_all.clicked.connect(self._clear_all)
        self.btn_create = QPushButton("Create video from joiner list…")
        self.btn_create.clicked.connect(self._create_video)
        self.btn_ok = QPushButton("OK")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(self.btn_clear_all)
        bottom.addWidget(self.btn_create)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_ok)
        bottom.addWidget(self.btn_cancel)
        outer.addLayout(bottom)

        self._refresh()

    # -- access ------------------------------------------------------------

    def result_list(self):
        """The edited joiner list, for the caller to adopt on OK."""
        return self._list

    def clear_after_requested(self):
        return self.chk_clear_after.isChecked()

    # -- helpers -----------------------------------------------------------

    def _current_index(self):
        item = self.tree.currentItem()
        if item is None:
            return -1
        return self.tree.indexOfTopLevelItem(item)

    def _refresh(self, select=None):
        self.tree.clear()
        full = self.chk_fullpath.isChecked()
        for entry in self._list.entries:
            name = entry.source if full else entry.display_name
            item = QTreeWidgetItem(
                [name, entry.description, _fmt_tc(entry.duration, entry.fps)]
            )
            if entry.is_title:
                # Title cards stand out in a muted blue rather than greyed.
                for col in range(3):
                    item.setForeground(col, QColor("#3a6ea5"))
            elif not entry.exists:
                # Grey out and flag entries whose file is missing.
                for col in range(3):
                    item.setForeground(col, QColor("gray"))
                item.setToolTip(0, "File not found: %s" % (entry.source,))
            self.tree.addTopLevelItem(item)

        if select is not None and 0 <= select < self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(select))
        self._sync_buttons()

    def _sync_buttons(self):
        i = self._current_index()
        has = i >= 0
        count = len(self._list.entries)
        self.btn_up.setEnabled(has and i > 0)
        self.btn_down.setEnabled(has and i < count - 1)
        self.btn_remove.setEnabled(has)
        self.btn_desc.setEnabled(has)
        self.btn_edit.setEnabled(has)
        self.btn_clear_all.setEnabled(count > 0)
        self.btn_create.setEnabled(count > 0)

        # Reflect the selected clip's fade values.
        self._loading_fade = True
        self.sp_fade_in.setEnabled(has)
        self.sp_fade_out.setEnabled(has)
        if has:
            e = self._list.entries[i]
            self.sp_fade_in.setValue(float(getattr(e, "fade_in", 0.0) or 0.0))
            self.sp_fade_out.setValue(float(getattr(e, "fade_out", 0.0) or 0.0))
        else:
            self.sp_fade_in.setValue(0.0)
            self.sp_fade_out.setValue(0.0)
        self._loading_fade = False

    def _fade_changed(self, *_):
        """Write the fade spinboxes back to the selected entry."""
        if self._loading_fade:
            return
        i = self._current_index()
        if 0 <= i < len(self._list.entries):
            e = self._list.entries[i]
            e.fade_in = float(self.sp_fade_in.value())
            e.fade_out = float(self.sp_fade_out.value())

    def _move(self, delta):
        i = self._current_index()
        if i < 0:
            return
        self._refresh(select=self._list.move(i, delta))

    def _remove(self):
        i = self._current_index()
        if i < 0:
            return
        self._list.remove(i)
        self._refresh(select=min(i, len(self._list.entries) - 1))

    def _edit_description(self):
        i = self._current_index()
        if i < 0:
            return
        entry = self._list.entries[i]
        text, ok = QInputDialog.getText(
            self, "Description", "Description:", text=entry.description
        )
        if ok:
            entry.description = text
            self._refresh(select=i)

    def _edit_selection(self):
        i = self._current_index()
        if i < 0:
            return
        entry = self._list.entries[i]
        if entry.is_title:
            # Edit the card in place rather than loading it into the editor.
            self._open_title_editor(entry, i)
            return
        self.entry_to_edit = entry
        self.accept()

    def _add_title(self):
        dialog = TitleEditorDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        entry = JoinerEntry(
            JoinerEntry.KIND_TITLE,
            description=values["text"] or "Title card",
            end=values["duration"],
            text=values["text"],
            subtitle=values["subtitle"],
            bg_color=values["bg_color"],
            text_color=values["text_color"],
            bg_image=values["bg_image"],
            bg_scaling=values["bg_scaling"],
        )
        i = self._current_index()
        insert_at = i + 1 if i >= 0 else len(self._list.entries)
        self._list.entries.insert(insert_at, entry)
        self._refresh(select=insert_at)

    def _open_title_editor(self, entry, index):
        dialog = TitleEditorDialog(entry, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        entry.text = values["text"]
        entry.subtitle = values["subtitle"]
        entry.start = 0.0
        entry.end = values["duration"]
        entry.bg_color = values["bg_color"]
        entry.text_color = values["text_color"]
        entry.bg_image = values["bg_image"]
        entry.bg_scaling = values["bg_scaling"]
        entry.description = values["text"] or "Title card"
        self._refresh(select=index)

    def _create_video(self):
        """Hand the current list back to the caller to render and join.  Done
        via accept() so the caller adopts the edited list first."""
        if not self._list.entries:
            QMessageBox.information(
                self, "Joiner", "The joiner list is empty.")
            return
        self.create_requested = True
        self.accept()

    def _clear_all(self):
        if not self._list.entries:
            return
        resp = QMessageBox.question(
            self, "Clear all",
            "Remove all entries from the joiner list?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            self._list.clear()
            self._refresh()

    # -- persistence -------------------------------------------------------

    def _start_dir(self):
        if self._list.path:
            return os.path.dirname(self._list.path)
        return self._joiner_dir or os.path.expanduser("~")

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Joiner List", self._start_dir(),
            "Joiner list (*%s);;All files (*)" % (JOINER_EXT,),
        )
        if not path:
            return

        append = False
        if self._list.entries:
            resp = QMessageBox.question(
                self, "Load Joiner List",
                "Add the loaded entries to the current list?\n\n"
                "Yes = append,  No = replace the current list.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if resp == QMessageBox.StandardButton.Cancel:
                return
            append = resp == QMessageBox.StandardButton.Yes

        try:
            self._list.load_into(path, append=append)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "Load Joiner List",
                "Could not load the joiner list:\n\n%s" % (exc,))
            return
        self._refresh()

    def _save(self):
        if self._list.path:
            self._write(self._list.path)
        else:
            self._save_as()

    def _save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Joiner List", self._start_dir(),
            "Joiner list (*%s);;All files (*)" % (JOINER_EXT,),
        )
        if not path:
            return
        if not path.lower().endswith(JOINER_EXT):
            path += JOINER_EXT
        self._write(path)

    def _write(self, path):
        try:
            self._list.save(path)
        except OSError as exc:
            QMessageBox.critical(
                self, "Save Joiner List",
                "Could not save the joiner list:\n\n%s" % (exc,))
