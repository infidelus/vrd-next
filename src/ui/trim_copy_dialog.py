"""Trim and Copy Source File dialog.

A read/act dialog modelled on VideoReDo's "Create Trimmed File Copy": pick a
source and output file, choose how much to copy (From Beginning / To End Of
File / Start At MByte / Use Selection Markers), and write a byte-for-byte copy
of that portion - no re-encode.  See media/trim_copy.py for the copy logic.
"""

import os
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QDoubleSpinBox,
    QProgressBar,
    QFileDialog,
    QMessageBox,
)

from media.trim_copy import (
    MB,
    MarkerByteMapper,
    TrimCopyWorker,
    plan_byte_range,
)


def _selection_markers(selection):
    """Return (in_frame, out_frame) for the current selection, or None.

    Prefers the live IN/OUT markers; failing that, falls back to the overall
    span of the committed keep ranges.
    """
    if selection is None:
        return None
    pin = getattr(selection, "pending_in", None)
    pout = getattr(selection, "pending_out", None)
    if pin is not None and pout is not None:
        return min(pin, pout), max(pin, pout)
    ranges = getattr(selection, "ranges", None)
    if ranges:
        starts = [s for s, _ in ranges]
        ends = [e for _, e in ranges]
        return min(starts), max(ends)
    return None


def _fmt_size(num_bytes):
    mb = num_bytes / MB
    if mb >= 1024:
        return "%.3f GB" % (mb / 1024.0)
    return "%.3f MB" % (mb,)


def _ensure_extension(path, selected_filter):
    """Append the extension from the chosen save-dialog filter when the user
    didn't type one.  Qt doesn't always add it itself, so the output could end
    up with no extension at all (e.g. 'clip' instead of 'clip.ts')."""
    if not path or os.path.splitext(path)[1]:
        return path                         # already has an extension
    match = re.search(r"\*\.(\w+)", selected_filter or "")
    return path + "." + match.group(1) if match else path


class TrimCopyDialog(QDialog):

    def __init__(self, source_path=None, index=None, selection=None,
                 source_dir="", output_dir="", parent=None):
        super().__init__(parent)

        self.setWindowTitle(self.tr("Trim and Copy Source File"))
        self.setMinimumWidth(560)

        self._index = index
        self._markers = _selection_markers(selection)
        self._mapper = None
        self._worker = None
        self._source_size = 0
        # Default folders for the browse dialogs (from the app's path settings):
        # Source -> "Opening videos", Output -> "Saving videos".
        self._source_dir = source_dir
        self._output_dir = output_dir

        outer = QVBoxLayout(self)

        # ---- Source / Size / Output ------------------------------------
        files = QGridLayout()
        files.setHorizontalSpacing(8)
        files.setVerticalSpacing(6)
        files.setColumnStretch(1, 1)

        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        src_browse = QPushButton(self.tr("…"))
        src_browse.setFixedWidth(32)
        src_browse.clicked.connect(self._browse_source)
        files.addWidget(QLabel(self.tr("Source File:")), 0, 0)
        files.addWidget(self.source_edit, 0, 1)
        files.addWidget(src_browse, 0, 2)

        self.size_label = QLabel(self.tr("—"))
        files.addWidget(QLabel(self.tr("Size:")), 1, 0)
        files.addWidget(self.size_label, 1, 1, 1, 2)

        self.output_edit = QLineEdit()
        out_browse = QPushButton(self.tr("…"))
        out_browse.setFixedWidth(32)
        out_browse.clicked.connect(self._browse_output)
        files.addWidget(QLabel(self.tr("Output File:")), 2, 0)
        files.addWidget(self.output_edit, 2, 1)
        files.addWidget(out_browse, 2, 2)

        outer.addLayout(files)

        # ---- Output options + MBytes To Output -------------------------
        opts_row = QHBoxLayout()

        box = QGroupBox(self.tr("Output Options"))
        box_lay = QVBoxLayout(box)
        self.opt_group = QButtonGroup(self)

        self.rb_begin = QRadioButton(self.tr("From Beginning"))
        self.rb_end = QRadioButton(self.tr("To End Of File"))

        at_row = QHBoxLayout()
        self.rb_at = QRadioButton(self.tr("Start At MByte:"))
        self.start_mbyte = QDoubleSpinBox()
        self.start_mbyte.setDecimals(0)
        self.start_mbyte.setRange(0, 4_000_000)
        self.start_mbyte.setValue(1)
        at_row.addWidget(self.rb_at)
        at_row.addWidget(self.start_mbyte)
        at_row.addStretch(1)

        self.rb_markers = QRadioButton(self.tr("Use Selection Markers"))
        self.rb_markers.setEnabled(
            self._markers is not None and self._index is not None
        )

        for rb in (self.rb_begin, self.rb_end, self.rb_at, self.rb_markers):
            self.opt_group.addButton(rb)
        self.rb_begin.setChecked(True)

        box_lay.addWidget(self.rb_begin)
        box_lay.addWidget(self.rb_end)
        box_lay.addLayout(at_row)
        box_lay.addWidget(self.rb_markers)
        box_lay.addStretch(1)
        opts_row.addWidget(box, 1)

        mb_col = QVBoxLayout()
        mb_row = QHBoxLayout()
        mb_row.addWidget(QLabel(self.tr("MBytes To Output:")))
        self.mbytes = QDoubleSpinBox()
        self.mbytes.setDecimals(0)
        self.mbytes.setRange(1, 4_000_000)
        self.mbytes.setValue(1)
        mb_row.addWidget(self.mbytes)
        mb_col.addLayout(mb_row)
        mb_col.addStretch(1)
        opts_row.addLayout(mb_col)

        outer.addLayout(opts_row)

        for rb in (self.rb_begin, self.rb_end, self.rb_at, self.rb_markers):
            rb.toggled.connect(self._sync_controls)
        self.start_mbyte.valueChanged.connect(self._sync_controls)

        # ---- Progress + buttons ----------------------------------------
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        outer.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.start_button = QPushButton(self.tr("Start Copy"))
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self._start_copy)
        self.close_button = QPushButton(self.tr("Close"))
        self.close_button.clicked.connect(self._on_close)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.close_button)
        outer.addLayout(buttons)

        if source_path:
            self._set_source(source_path)
        self._sync_controls()

    # -- source / output ---------------------------------------------------

    def _set_source(self, path):
        self.source_edit.setText(path)
        try:
            self._source_size = os.path.getsize(path)
        except OSError:
            self._source_size = 0
        self.size_label.setText(_fmt_size(self._source_size))
        # A new source invalidates any open mapper.
        if self._mapper is not None:
            self._mapper.close()
            self._mapper = None
        self._sync_controls()

    def _browse_source(self):
        start_dir = (
            os.path.dirname(self.source_edit.text() or "")
            or self._source_dir
            or os.path.expanduser("~")
        )
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Select Source File"), start_dir,
            "Transport streams (*.ts *.m2ts *.mpg *.mpeg);;All files (*)",
        )
        if path:
            # Browsing to a different source drops the editor's markers, since
            # they only describe the originally-open recording.
            if path != self.source_edit.text():
                self._markers = None
                self.rb_markers.setEnabled(False)
                if self.rb_markers.isChecked():
                    self.rb_begin.setChecked(True)
            self._set_source(path)

    def _browse_output(self):
        start_dir = (
            os.path.dirname(self.output_edit.text() or "")
            or self._output_dir
            or os.path.dirname(self.source_edit.text() or "")
            or os.path.expanduser("~")
        )
        path, selected = QFileDialog.getSaveFileName(
            self, self.tr("Select Output File"), start_dir,
            "Transport stream (*.ts);;All files (*)",
            "",
            QFileDialog.Option.DontConfirmOverwrite,
        )
        if path:
            # Add the filter's extension if the user didn't type one, and leave
            # the overwrite check to Start Copy (the path can be reused across
            # several copies, so that's the right place to ask).
            self.output_edit.setText(_ensure_extension(path, selected))

    # -- option syncing ----------------------------------------------------

    def _ensure_mapper(self):
        if self._mapper is None and self.source_edit.text() and self._index is not None:
            gop = getattr(self._index, "median_gop_pts", 0)
            self._mapper = MarkerByteMapper(self.source_edit.text(), gop_pts=gop)
        return self._mapper

    def _marker_byte_range(self):
        """(start, length) for the current IN/OUT markers, or None on failure."""
        if not self._markers or self._index is None:
            return None
        try:
            mapper = self._ensure_mapper()
            in_pts = self._index.pts_of(self._markers[0])
            out_pts = self._index.pts_of(self._markers[1])
            start = mapper.start_byte(in_pts)
            end = mapper.end_byte(out_pts)
            if end <= start:
                return None
            return start, end - start
        except Exception:
            return None

    def _sync_controls(self):
        """Enable/disable inputs and reflect the computed size for markers."""
        using_markers = self.rb_markers.isChecked()
        using_at = self.rb_at.isChecked()

        self.start_mbyte.setEnabled(using_at)
        # MBytes To Output is the length the user sets for the byte options; for
        # markers it's computed, so show it read-only.
        self.mbytes.setReadOnly(using_markers)
        self.mbytes.setEnabled(not using_markers)

        if using_markers:
            rng = self._marker_byte_range()
            if rng:
                self.mbytes.setValue(round(rng[1] / MB))

    # -- copy --------------------------------------------------------------

    def _planned_range(self):
        """Return (start, length) for the chosen option, or None with a reason
        shown to the user."""
        size = self._source_size
        if size <= 0:
            QMessageBox.warning(self, self.tr("Trim and Copy"),
                                self.tr("Please choose a valid source file first."))
            return None

        if self.rb_markers.isChecked():
            rng = self._marker_byte_range()
            if rng is None:
                QMessageBox.warning(
                    self, self.tr("Trim and Copy"),
                    self.tr("Could not work out byte offsets from the selection "
                    "markers for this file."))
            return rng

        mode = ("beginning" if self.rb_begin.isChecked()
                else "end" if self.rb_end.isChecked()
                else "at")
        start, length = plan_byte_range(
            mode, size, self.mbytes.value(), self.start_mbyte.value())
        if length <= 0:
            QMessageBox.warning(self, self.tr("Trim and Copy"),
                                self.tr("That selection produces an empty file."))
            return None
        return start, length

    def _start_copy(self):
        src = self.source_edit.text()
        dst = self.output_edit.text()
        if not dst:
            QMessageBox.warning(self, self.tr("Trim and Copy"),
                                self.tr("Please choose an output file."))
            return
        if os.path.abspath(dst) == os.path.abspath(src):
            QMessageBox.warning(self, self.tr("Trim and Copy"),
                                self.tr("The output file must be different from the "
                                "source file."))
            return

        plan = self._planned_range()
        if plan is None:
            return
        start, length = plan

        if os.path.exists(dst):
            resp = QMessageBox.question(
                self, self.tr("Trim and Copy"),
                "%s already exists.\n\nOverwrite it?"
                % (os.path.basename(dst),),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return

        self.start_button.setEnabled(False)
        self.close_button.setText(self.tr("Cancel"))
        self.progress.setValue(0)
        self.progress.show()

        self._worker = TrimCopyWorker(src, dst, start, length, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, done, total):
        self.progress.setValue(int(done * 100 / total) if total else 0)

    def _reset_after_copy(self):
        self.start_button.setEnabled(True)
        self.close_button.setText(self.tr("Close"))
        self.progress.hide()
        self._worker = None

    def _on_done(self, written, path):
        self._reset_after_copy()
        QMessageBox.information(
            self, self.tr("Trim and Copy"),
            "Copy complete.\n\n%s\n%s written."
            % (path, _fmt_size(written)))

    def _on_failed(self, message):
        self._reset_after_copy()
        if message != "Cancelled.":
            QMessageBox.critical(self, self.tr("Trim and Copy"),
                                 self.tr("Copy failed:\n\n%s") % (message,))

    def _on_close(self):
        # While a copy is running, "Close" acts as Cancel.
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            return
        self.accept()

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        if self._mapper is not None:
            self._mapper.close()
            self._mapper = None
        super().closeEvent(event)
