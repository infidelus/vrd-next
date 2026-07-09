"""System-tray front-end for the watcher engine.

A standalone app (its own process) that sits in the system tray, scans the
configured recording folders on a timer, runs Comskip on anything new, and
writes a .vprj of the detected commercials into the output folder for the
Batch Manager to pick up.

If the desktop has no usable system tray, it degrades to a normal control
window instead of failing.
"""

import logging
import os
import os
import subprocess
import sys

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QUrl, QRectF, QCoreApplication
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QBrush, QPen, QDesktopServices,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QDialog, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QListWidget, QLineEdit, QSpinBox,
    QCheckBox, QFileDialog, QMessageBox, QGroupBox, QPlainTextEdit,
)

from watch.config import WatchConfig, PROCESSED_FILE, IGNORE_FILE
from watch.engine import scan_once, ProcessedLog, load_ignore_patterns
from watch import autostart

log = logging.getLogger("vrd-next.watch.tray")


_TRAY_SVG = os.path.normpath(
    os.path.join(os.path.dirname(__file__), os.pardir, "assets", "tray.svg")
)


def make_tray_icon(active=False):
    """Tray icon: the app emblem (red lightning bolt + blue play triangle,
    rendered from assets/tray.svg).  When a scan is running, a small green dot
    marks it as active at a glance."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    renderer = QSvgRenderer(_TRAY_SVG)
    if renderer.isValid():
        renderer.render(p, QRectF(0, 0, size, size))

    if active:
        p.setBrush(QBrush(QColor("#33c052")))
        p.setPen(QPen(QColor("#0d3b1a"), 2))
        p.drawEllipse(QRectF(42, 42, 18, 18))

    p.end()
    return QIcon(pm)


class WatchScanWorker(QThread):
    """Runs one scan pass off the UI thread."""

    event = Signal(str, dict)       # (kind, info)
    finished_scan = Signal(dict)    # summary

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._cancel = False
        self._pause = False

    def cancel(self):
        """Hard stop - abandon the current file immediately (used on Quit)."""
        self._cancel = True

    def request_pause(self):
        """Graceful stop - finish the current file, then stop before the next
        (used on Pause)."""
        self._pause = True

    def run(self):
        processed = ProcessedLog(PROCESSED_FILE)
        ignore_patterns = load_ignore_patterns(IGNORE_FILE)

        def on_event(kind, r):
            self.event.emit(kind, {
                "source": r.source,
                "vprj": r.vprj_path or "",
                "cuts": r.cut_count,
                "error": r.error or "",
                "skipped": r.skipped_reason or "",
            })

        try:
            summary = scan_once(
                self._cfg, processed,
                on_event=on_event,
                cancel_cb=lambda: self._cancel,
                pause_cb=lambda: self._pause,
                ignore_patterns=ignore_patterns,
            )
        except Exception as exc:        # never let the worker die silently
            log.exception("watch scan crashed")
            summary = {"error": str(exc)}
        self.finished_scan.emit(summary)


class IgnoreListDialog(QDialog):
    """A plain text editor for the ignore list - one programme title per line.
    Recordings whose file name contains any line are skipped by the watcher."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Edit ignore list"))
        self.setModal(True)
        self.setMinimumSize(480, 460)

        layout = QVBoxLayout(self)
        info = QLabel(
            self.tr("One programme title per line. Any recording whose file name "
            "contains a line here is skipped (case-insensitive).\n\n"
            "Lines starting with # are comments.")
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._editor = QPlainTextEdit()
        self._editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self._editor, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(self.tr("Cancel"))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        save = QPushButton(self.tr("Save"))
        save.setDefault(True)
        save.clicked.connect(self._save)
        row.addWidget(save)
        layout.addLayout(row)

        self._load()

    def _load(self):
        try:
            with open(IGNORE_FILE, encoding="utf-8") as f:
                self._editor.setPlainText(f.read())
        except OSError:
            self._editor.setPlainText("")

    def _save(self):
        text = self._editor.toPlainText()
        try:
            os.makedirs(os.path.dirname(IGNORE_FILE), exist_ok=True)
            with open(IGNORE_FILE, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            QMessageBox.warning(self, self.tr("Ignore list"),
                                self.tr("Couldn't save the ignore list:\n\n%s") % exc)
            return
        self.accept()


class WatchControlDialog(QDialog):
    """Settings + manual controls.  Doubles as the main window when there's no
    system tray available."""

    def __init__(self, tray):
        super().__init__()
        self.tray = tray
        self.cfg = tray.cfg
        self.setWindowTitle(self.tr("VRD Next Watcher"))
        self.setWindowIcon(make_tray_icon())
        self.resize(560, 520)
        self._build_ui()
        self._load_into_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)

        self.status_label = QLabel(self.tr("Idle."))
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        # --- manual controls ---
        controls = QHBoxLayout()
        self.scan_btn = QPushButton(self.tr("Scan Now"))
        self.scan_btn.clicked.connect(self.tray.scan_now)
        self.pause_btn = QPushButton()
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.open_out_btn = QPushButton(self.tr("Open Output Folder"))
        self.open_out_btn.clicked.connect(self.tray.open_output)
        self.launch_btn = QPushButton(self.tr("Launch VRD Next"))
        self.launch_btn.clicked.connect(self.tray.launch_editor)
        for b in (self.scan_btn, self.pause_btn, self.open_out_btn,
                  self.launch_btn):
            controls.addWidget(b)
        controls.addStretch(1)
        outer.addLayout(controls)

        # --- watched folders ---
        folders_box = QGroupBox(self.tr("Recording folders to watch"))
        fb = QVBoxLayout(folders_box)
        self.folders = QListWidget()
        fb.addWidget(self.folders)
        frow = QHBoxLayout()
        add_btn = QPushButton(self.tr("Add…"))
        add_btn.clicked.connect(self._add_folder)
        rem_btn = QPushButton(self.tr("Remove"))
        rem_btn.clicked.connect(self._remove_folder)
        frow.addWidget(add_btn)
        frow.addWidget(rem_btn)
        frow.addStretch(1)
        fb.addLayout(frow)
        outer.addWidget(folders_box)

        # --- scanning: when and how often the watcher runs ---
        scan_box = QGroupBox(self.tr("Scanning"))
        scan_v = QVBoxLayout(scan_box)
        scan_grid = QGridLayout()
        scan_grid.addWidget(QLabel(self.tr("Scan every (minutes):")), 0, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 1440)
        scan_grid.addWidget(self.interval_spin, 0, 1, alignment=Qt.AlignLeft)

        scan_grid.addWidget(QLabel(self.tr("Wait after last change (minutes):")), 1, 0)
        self.settle_spin = QSpinBox()
        self.settle_spin.setRange(0, 240)
        self.settle_spin.setToolTip(
            self.tr("How long a recording must be untouched before it's scanned, so "
            "in-progress recordings are left alone.")
        )
        scan_grid.addWidget(self.settle_spin, 1, 1, alignment=Qt.AlignLeft)
        scan_grid.setColumnStretch(2, 1)
        scan_v.addLayout(scan_grid)

        self.scan_launch_chk = QCheckBox(self.tr("Scan immediately on launch"))
        self.scan_launch_chk.setToolTip(
            self.tr("Run a scan a few seconds after starting, instead of waiting for "
            "the first interval.")
        )
        scan_v.addWidget(self.scan_launch_chk)

        self.autostart_chk = QCheckBox(
            self.tr("Start the watcher automatically on login")
        )
        scan_v.addWidget(self.autostart_chk)
        outer.addWidget(scan_box)

        # --- output: where results go and what gets written ---
        out_box = QGroupBox(self.tr("Output"))
        out_v = QVBoxLayout(out_box)
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel(self.tr("Output folder:")))
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        out_row.addWidget(self.output_edit, 1)
        out_btn = QPushButton(self.tr("Browse…"))
        out_btn.clicked.connect(self._choose_output)
        out_row.addWidget(out_btn)
        out_v.addLayout(out_row)

        self.save_no_ads_chk = QCheckBox(
            self.tr("Save a full-length project even when no commercials are found")
        )
        self.save_no_ads_chk.setToolTip(
            self.tr("When Comskip finds no adverts, still write a .vprj covering the "
            "whole recording, so it reaches the Batch Manager ready to review "
            "or copy.  Turn off to skip advert-free recordings entirely.")
        )
        out_v.addWidget(self.save_no_ads_chk)
        outer.addWidget(out_box)

        # --- logs & files: the watcher's own housekeeping ('&&' shows a literal
        # '&' in a Qt group-box title, which would otherwise be a mnemonic) ---
        logs_box = QGroupBox(self.tr("Logs && files"))
        logs_v = QVBoxLayout(logs_box)
        log_row = QHBoxLayout()
        log_row.addWidget(QLabel(self.tr("Log files to keep:")))
        self.log_keep_spin = QSpinBox()
        self.log_keep_spin.setRange(0, 3650)
        self.log_keep_spin.setToolTip(
            self.tr("How many of the watcher's own per-day log files to keep. The "
            "oldest beyond this are removed when the watcher starts. Set to 0 "
            "to keep every log. (The editor keeps its logs separately.)")
        )
        log_row.addWidget(self.log_keep_spin)
        log_row.addStretch(1)
        logs_v.addLayout(log_row)

        config_btn = QPushButton(self.tr("Open config folder"))
        config_btn.setToolTip(
            self.tr("Open the folder holding the watcher's settings and lists - "
            "watch_processed.txt (the completed list, delete entries to have a "
            "recording picked up again), watch_ignore.txt, and the watcher log.")
        )
        config_btn.clicked.connect(self.tray.open_config_folder)
        logs_v.addWidget(config_btn, alignment=Qt.AlignLeft)
        outer.addWidget(logs_box)

        # --- comskip status (set in the editor) ---
        self.comskip_label = QLabel()
        self.comskip_label.setWordWrap(True)
        outer.addWidget(self.comskip_label)

        outer.addStretch(1)

        # --- save / close ---
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        save_btn = QPushButton(self.tr("Save"))
        save_btn.clicked.connect(self._save)
        bottom.addWidget(save_btn)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        outer.addLayout(bottom)

    def _load_into_ui(self):
        self.folders.clear()
        self.folders.addItems(self.cfg.input_roots)
        self.output_edit.setText(self.cfg.output_dir)
        self.interval_spin.setValue(self.cfg.scan_interval_minutes)
        self.settle_spin.setValue(int(self.cfg.settle_minutes))
        self.log_keep_spin.setValue(self.cfg.log_max_files)
        self.autostart_chk.setChecked(autostart.is_enabled())
        self.scan_launch_chk.setChecked(self.cfg.scan_on_launch)
        self.save_no_ads_chk.setChecked(self.cfg.save_when_no_adverts)
        self._refresh_pause_button()
        self._refresh_comskip_label()

    def _refresh_comskip_label(self):
        binary, ini = self.cfg.comskip_paths()
        if binary and os.path.isfile(binary):
            self.comskip_label.setText(
                self.tr("Comskip: %s") % binary
                + (self.tr("\nIni: %s") % ini if ini else "")
            )
            self.comskip_label.setStyleSheet("")
        else:
            self.comskip_label.setText(
                self.tr("⚠ Comskip isn't set. Open the VRD Next editor → Settings and "
                "set the Comskip program (and .ini); the watcher reads it from "
                "there.")
            )
            self.comskip_label.setStyleSheet("color: #b03030;")

    def _refresh_pause_button(self):
        self.pause_btn.setText(self.tr("Resume") if self.cfg.paused else self.tr("Pause"))

    def _toggle_pause(self):
        self.tray.set_paused(not self.cfg.paused)
        self._refresh_pause_button()

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("Add recording folder"))
        if folder and not self._folder_in_list(folder):
            self.folders.addItem(folder)

    def _folder_in_list(self, folder):
        return any(self.folders.item(i).text() == folder
                   for i in range(self.folders.count()))

    def _remove_folder(self):
        for item in self.folders.selectedItems():
            self.folders.takeItem(self.folders.row(item))

    def _choose_output(self):
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Output folder"), self.output_edit.text()
        )
        if folder:
            self.output_edit.setText(folder)

    def _save(self):
        self.cfg.input_roots = [
            self.folders.item(i).text() for i in range(self.folders.count())
        ]
        self.cfg.output_dir = self.output_edit.text()
        self.cfg.scan_interval_minutes = self.interval_spin.value()
        self.cfg.settle_minutes = self.settle_spin.value()
        self.cfg.log_max_files = self.log_keep_spin.value()
        self.cfg.scan_on_launch = self.scan_launch_chk.isChecked()
        self.cfg.save_when_no_adverts = self.save_no_ads_chk.isChecked()
        self.cfg.save()
        autostart.set_enabled(self.autostart_chk.isChecked())
        self.tray.settings_changed()
        self.status_label.setText(self.tr("Settings saved."))

    def set_status(self, text):
        self.status_label.setText(text)

    def closeEvent(self, event):
        # When there's no tray, this window is the app: closing quits.
        if self.tray.no_tray:
            self.tray.quit()
        super().closeEvent(event)


class WatcherTray:
    """Owns the tray icon, the scan timer and the worker.  A plain object that
    wires Qt widgets together."""

    def __init__(self, app):
        self.app = app
        self.cfg = WatchConfig.load()
        # Always start watching - pause never carries across launches.
        self.cfg.paused = False
        self.worker = None
        self.dialog = None

        self.no_tray = not QSystemTrayIcon.isSystemTrayAvailable()

        self.tray = QSystemTrayIcon(make_tray_icon())
        self.tray.setToolTip(self.tr("VRD Next Watcher"))
        self._build_menu()
        self.tray.activated.connect(self._on_activated)

        self.timer = QTimer()
        self.timer.timeout.connect(self._maybe_scan)
        self._restart_timer()

        if self.no_tray:
            # No system tray - fall back to a visible control window.
            self.open_settings()
        else:
            self.tray.show()
            self._notify(
                self.tr("VRD Next Watcher"),
                self.tr("Watching for recordings. Right-click the tray icon for "
                "options."),
            )

        # Optionally scan shortly after launch rather than waiting a full
        # interval.  The short delay keeps it from fighting the desktop while
        # logging in.
        if self.cfg.scan_on_launch:
            QTimer.singleShot(5000, self._maybe_scan)

    # --- menu ------------------------------------------------------------- #

    def _build_menu(self):
        menu = QMenu()
        self.act_scan = menu.addAction(self.tr("Scan now"))
        self.act_scan.triggered.connect(self.scan_now)
        self.act_pause = menu.addAction(self.tr("Pause"))
        self.act_pause.triggered.connect(lambda: self.set_paused(not self.cfg.paused))
        menu.addSeparator()
        self.act_launch = menu.addAction(self.tr("Launch VRD Next"))
        self.act_launch.triggered.connect(self.launch_editor)
        self.act_output = menu.addAction(self.tr("Open output folder"))
        self.act_output.triggered.connect(self.open_output)
        self.act_ignore = menu.addAction(self.tr("Edit ignore list…"))
        self.act_ignore.triggered.connect(self.open_ignore_editor)
        self.act_settings = menu.addAction(self.tr("Settings…"))
        self.act_settings.triggered.connect(self.open_settings)
        menu.addSeparator()
        self.act_quit = menu.addAction(self.tr("Quit"))
        self.act_quit.triggered.connect(self.quit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self._refresh_pause_action()

    def _refresh_pause_action(self):
        self.act_pause.setText(self.tr("Resume") if self.cfg.paused else self.tr("Pause"))

    def _on_activated(self, reason):
        # Left-click (Trigger) toggles the control window: open it if it's
        # closed, or close it back to the tray if it's already showing.
        if reason == QSystemTrayIcon.Trigger:
            if self.dialog is not None and self.dialog.isVisible():
                self.dialog.close()
            else:
                self.open_settings()

    # --- timer ------------------------------------------------------------ #

    def _restart_timer(self):
        self.timer.stop()
        self.timer.setInterval(self.cfg.scan_interval_minutes * 60 * 1000)
        self.timer.start()

    def _maybe_scan(self):
        if not self.cfg.paused:
            self.scan_now()

    # --- actions ---------------------------------------------------------- #

    def scan_now(self):
        if self.worker is not None:
            return  # already scanning
        binary, _ini = self.cfg.comskip_paths()
        if not binary or not os.path.isfile(binary):
            self._set_status(
                self.tr("Comskip isn't set - open Settings and configure it in the "
                "editor first.")
            )
            if self.dialog is None:
                self.open_settings()
            return

        self.worker = WatchScanWorker(self.cfg)
        self.worker.event.connect(self._on_event)
        self.worker.finished_scan.connect(self._on_finished)
        self.tray.setIcon(make_tray_icon(active=True))
        self._set_status(self.tr("Scanning…"))
        self.worker.start()

    def set_paused(self, paused):
        # Pause is a runtime toggle - it is deliberately NOT persisted, so the
        # watcher always starts watching when launched and the button's meaning
        # is never ambiguous.
        self.cfg.paused = paused
        self._refresh_pause_action()
        if self.dialog is not None:
            self.dialog._refresh_pause_button()

        if paused:
            if self.worker is not None:
                # Let the file currently being scanned finish, then stop before
                # the next one.
                self.worker.request_pause()
                self._set_status(self.tr("Pausing — finishing the current file…"))
            else:
                self._set_status(self.tr("Paused."))
        else:
            self._set_status(self.tr("Watching."))
            # Resuming picks up the recordings that were left unscanned.
            if self.worker is None:
                self.scan_now()

    def open_output(self):
        out = self.cfg.output_dir
        os.makedirs(out, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(out))

    def open_config_folder(self):
        """Open the watcher's config folder - home to watch.json,
        watch_processed.txt (the completed list), watch_ignore.txt and the
        watcher log - so the user can view or prune them without hunting for
        the path by hand."""
        from config.loader import CONFIG_DIR
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONFIG_DIR)))

    def launch_editor(self):
        """Start the main VRD Next editor as a separate process."""
        here = os.path.dirname(os.path.abspath(__file__))   # .../src/watch
        main_py = os.path.normpath(os.path.join(here, os.pardir, "main.py"))
        if not os.path.isfile(main_py):
            self._notify(
                self.tr("Launch VRD Next"),
                self.tr("Couldn't find the editor (main.py) next to the watcher."),
            )
            return
        try:
            subprocess.Popen([sys.executable, main_py])
        except OSError as exc:
            self._notify(self.tr("Launch VRD Next"), self.tr("Couldn't launch the editor:\n%s") % exc)

    def open_ignore_editor(self):
        parent = self.dialog if self.dialog is not None else None
        IgnoreListDialog(parent).exec()

    def open_settings(self):
        if self.dialog is None:
            self.dialog = WatchControlDialog(self)
            self.dialog.finished.connect(self._on_dialog_closed)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _on_dialog_closed(self, _result):
        self.dialog = None

    def settings_changed(self):
        self._restart_timer()
        self._refresh_pause_action()

    def quit(self):
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(8000)
        self.tray.hide()
        self.app.quit()

    # --- worker signal handlers ------------------------------------------ #

    def _on_event(self, kind, info):
        name = os.path.basename(info.get("source", ""))
        if kind == "processing":
            self._set_status(self.tr("Scanning %s…") % name)
        elif kind == "done":
            if info.get("vprj") and info.get("cuts", 0) > 0:
                self._notify(
                    self.tr("Commercials found"),
                    self.tr("%s: %s break(s). Project ready in the Batch Manager.")
                    % (name, info['cuts']),
                )
        elif kind == "error":
            self._notify(self.tr("Scan problem"), self.tr("%s: %s") % (name, info.get('error', '')))

    def _on_finished(self, summary):
        self.worker = None
        self.tray.setIcon(make_tray_icon(active=False))
        if "error" in summary:
            self._set_status(self.tr("Scan failed: %s") % summary['error'])
            return
        if self.cfg.paused or summary.get("paused"):
            self._set_status(self.tr("Paused."))
            return
        projects = summary.get("projects", 0)
        scanned = summary.get("scanned", 0)
        msg = self.tr(
            "Last scan: %s new recording(s), %s with commercials."
        ) % (scanned, projects)
        self._set_status(msg)
        if projects:
            self._notify(self.tr("VRD Next Watcher"), msg)

    # --- helpers ---------------------------------------------------------- #

    def tr(self, text):
        """WatcherTray is a plain object (not a QObject), so it can't inherit
        Qt's tr().  This gives it the same behaviour, translating under the
        'WatcherTray' context so its strings sit alongside the rest."""
        return QCoreApplication.translate("WatcherTray", text)

    def _set_status(self, text):
        self.tray.setToolTip(self.tr("VRD Next Watcher\n%s") % text)
        if self.dialog is not None:
            self.dialog.set_status(text)

    def _notify(self, title, message):
        if not self.no_tray and self.tray.supportsMessages():
            self.tray.showMessage(title, message, make_tray_icon(), 5000)
