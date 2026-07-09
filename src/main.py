import os
import sys
import time
import logging

import av

from PySide6.QtCore import (
    Qt,
    QEvent,
    QObject,
    QThread,
    QTimer,
)
from media.renderer import (
    render_frame,
)
from media.scrub_worker import (
    ScrubWorker,
    frame_label,
)
from media.playback_worker import (
    PlaybackWorker,
)
from media.thumbnail_worker import (
    ThumbnailWorker,
)
from PySide6.QtGui import (
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QProxyStyle,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleFactory,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
)
from media.frame_index import (
    get_or_build_index,
)
from version import (
    VERSION_STRING,
    APP_NAME,
    VERSION,
)
from media.fetcher import (
    FrameFetcher,
)
from media.frame_sequence import (
    FrameSequence,
)
from media.audio import (
    AudioController,
)
from export.exporter import (
    ExportWorker,
)
from repair.stream_fix import (
    StreamFixWorker,
)
from project.selection import (
    SelectionManager,
)
from utils.timecode import (
    frame_to_timecode,
    marker_text,
)
from utils.tool_paths import (
    apply_tool_paths,
    tool_issues,
)
from ui.thumbnail_bar import ThumbnailBar
from project.scenes import (
    SceneManager,
)
from ui.scene_bar import SceneBar
from config.loader import (
    ensure_config,
    save_config,
)
from config.shortcuts import (
    ShortcutManager,
)
from ui.scene_list import (
    SceneList,
)
from ui.info_panel import (
    InfoPanel,
)
from ui.transport_panel import (
    TransportPanel,
    TransportControls,
    ActionBar,
)


# Startup fallback frame rate, used only until a file is opened.  Once a video
# loads, ``self.fps`` is replaced by the file's real, detected rate (see
# ``set_index``), and everything frame-related works from that - so the app
# copes with any frame rate without the user setting anything.
DEFAULT_FPS = 25

# Name filter for "save a video" dialogs.  Listing the common broadcast/video
# extensions (and defaulting to it) means a Save As into a folder of recordings
# shows the videos already there, rather than every file or nothing useful.
VIDEO_SAVE_FILTER = (
    "Videos (*.ts *.m2ts *.mkv *.mp4 *.mpg *.mpeg);;All files (*)"
)

# Name filter for "open a video" dialogs.  The cutter decodes through ffmpeg,
# so the mainstream containers all open; ".ts" and ".mkv" remain the primary
# formats, with the others offered for convenience and "All files" as a
# fall-back so nothing is ever blocked from opening.  ".avi" is old but still
# turns up in older recordings, so it's included too.
VIDEO_OPEN_FILTER = (
    "Videos (*.ts *.m2ts *.mkv *.mp4 *.mov *.avi);;All files (*)"
)


log = logging.getLogger("vrd-next")


class MainWindow(QMainWindow):

    def __init__(self):

        super().__init__()

        self.config = (
            ensure_config()
        )

        # Prepend any user-configured ffmpeg/ffprobe build to PATH, so every
        # export/join/probe call (which invokes them by bare name) uses it.
        apply_tool_paths(self.config)

        # Prune stale on-disk caches (frame indices + QSF registry) once at
        # startup, by age.  Best-effort and quick; never blocks launch.
        try:
            max_age = self.config.get("settings", {}).get(
                "cache_max_age_days", 30
            )
            from media.frame_index import prune_index_cache
            from repair.qsf_registry import prune as prune_qsf_registry
            prune_index_cache(max_age)
            prune_qsf_registry(max_age)
        except Exception:
            pass

        self.keys = (
            ShortcutManager(
                self.config
            )
        )

        self.setWindowTitle(
            VERSION_STRING
        )

        # Snapshot of the scene list as last saved (see the dirty-tracking
        # helpers); empty until a file is loaded.
        self._saved_ranges = []

        # Restore the last-used window size (saved on close).
        win = self.config.get("window", {})
        self.resize(
            int(win.get("width", 1400)),
            int(win.get("height", 900)),
        )
        # If the last session was maximised, restore that once the window is
        # up (deferred so it applies after show()).
        if win.get("maximized", False):
            QTimer.singleShot(0, self.showMaximized)

        self.frames = []
        self.current_frame = 0
        self.selected_scene = None
        self.transport_direction = 0

        #
        # On-demand decoding (replaces the in-RAM frame list)
        #

        self.index = None
        self.fetcher = None
        self.index_builder = None
        self.source_size_mb = 0.0

        #
        # Background decoder for responsive timeline scrubbing.
        #

        self.scrub_worker = None
        self.scrub_thread = None
        self.playback_worker = None
        self.playback_thread = None
        self.thumb_worker = None
        self.thumb_thread = None

        self.transport_timer = QTimer()
        self.transport_started = False
        self.playing = False

        self.fps = DEFAULT_FPS

        #
        # Playback audio (Qt Multimedia when available; a silent no-op if the
        # multimedia module isn't installed).  Created before the transport
        # widgets, since the volume control reads it.
        #
        self._audio_active = False
        self.audio = AudioController(
            volume=(
                self.config
                .get("settings", {})
                .get("volume", 80)
            ) / 100.0,
            latency_ms=(
                self.config
                .get("settings", {})
                .get("audio_latency_ms", 0)
            ),
        )

        # Batch queue + worker live here (not on the dialog), so a batch keeps
        # running when the Batch Manager window is closed.
        from batch.controller import BatchController
        self.batch_controller = BatchController(self.config, self)
        self._batch_dialog = None

        # Fixed trim (seconds) added to where the AUDIO is seeked to, to line
        # the sound up with the picture.  If the sound runs AHEAD of the
        # picture, make this negative (pulls the sound earlier); if it lags,
        # positive.  From audio_latency_ms in the config.
        self._audio_offset = (
            self.config
            .get("settings", {})
            .get("audio_latency_ms", 0)
        ) / 1000.0
        #
        # Playback timing anchors (wall-clock compensated, set on play start).
        #

        self._play_anchor_time = None
        self._play_anchor_frame = 0

        # True while the user is dragging the timeline, so playback yields the
        # playhead to the drag instead of fighting it.
        self._scrubbing = False

        self.transport_timer.setTimerType(
            Qt.PreciseTimer
        )

        self.transport_timer.timeout.connect(
            self.transport_step
        )

        self.transport_timer.setInterval(
            round(
                1000 / DEFAULT_FPS
            )
        )

        self.container = None

        # Continuation to run after a programmatic file load completes (used by
        # the QSF-and-reload flow).  None when not awaiting one.
        self._pending_after_load = None

        # Path of the project (.vprj) currently open, set when a project is
        # imported or saved-as.  "Save Project" (Ctrl+P) overwrites this file
        # without prompting; None means there isn't one yet, so it falls back
        # to "Save Project As".
        self.current_project_path = None

        # The joiner list (segments to be joined into one video).  Persists for
        # the lifetime of the window; edited via the Joiner menu.
        from project.joiner import JoinerList
        self.joiner_list = JoinerList()

        # Path of the original recording the user opened.  Survives a Quick
        # Stream Fix swapping in a /tmp working copy, so output names track the
        # real recording rather than the temp.
        self.original_source = None

        # Path of the file currently loaded (may be a /tmp QSF working copy).
        # Initialised here so helpers that read it (e.g. when naming a QSF temp
        # during the very first open) never hit an unset attribute.
        self.current_filename = None

        # After a QSF reload, remembers the (out_path, out_format) the user had
        # chosen, so the next Save can default to the same destination.
        self._pending_export = None

        self.selection = (
            SelectionManager()
        )

        self.scenes = (
            SceneManager()
        )

        self.build_ui()
        self.build_menu()

    def build_ui(self):

        root = QWidget()

        self.setCentralWidget(
            root
        )

        layout = QVBoxLayout(
            root
        )

        self.preview = QLabel()

        self.preview.setStyleSheet("""
            background:black;
        """)

        #
        # The preview holds a pixmap; without this its size hint grows with
        # the image and can fight the window manager (stuck size, lost
        # maximise toggle).  Ignored = the label never dictates window size.
        #

        self.preview.setMinimumSize(
            1,
            1,
        )

        self.preview.setSizePolicy(
            QSizePolicy.Ignored,
            QSizePolicy.Ignored,
        )

        self.preview.setAlignment(
            Qt.AlignCenter
        )

        #
        # Click the preview to toggle play/pause.
        #

        self.preview.mousePressEvent = (
            self._preview_clicked
        )

        # Picture-type (I/P/B) overlay drawn on top of the preview pixmap. The
        # letter is refreshed as frames arrive; whether it's drawn depends on
        # the frame-type display setting (set via _apply_frame_type_display).
        self.preview._ft_letter = ""
        self._show_preview_frame_type = False
        self._preview_default_paint = self.preview.paintEvent
        self.preview.paintEvent = self._preview_paint_event

        #
        # Main content area
        #

        content = QHBoxLayout()

        #
        # Left side
        #

        left = QVBoxLayout()

        left.setSpacing(
            8
        )

        left.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        left.addWidget(
            self.preview,
            stretch=1,
        )

        self.thumbnail_bar = ThumbnailBar(
            self
        )

        left.addWidget(
            self.thumbnail_bar
        )

        # Apply the saved frame-type display setting now both the bar and the
        # preview exist.
        self._apply_frame_type_display()

        self.scene_bar = SceneBar(
            self
        )

        left.addWidget(
            self.scene_bar
        )

        self.transport_panel = (
            TransportPanel(
                self
            )
        )

        left.addWidget(
            self.transport_panel
        )

        self.transport_controls = (
            TransportControls(
                self
            )
        )

        left.addWidget(
            self.transport_controls
        )

        self.action_bar = (
            ActionBar(
                self
            )
        )

        left.addWidget(
            self.action_bar
        )

        #
        # Nothing is loaded yet, so the playback / mark / action controls
        # below the timeline start disabled; they're enabled once a video
        # finishes loading and disabled again when the video is closed.
        #
        self._set_controls_enabled(False)

        content.addLayout(
            left,
            8,
        )

        #
        # Right panel
        #

        self.scene_list = SceneList(
            self
        )

        self.scene_list.cellClicked.connect(
            self.scene_clicked
        )

        self.scene_list.cellDoubleClicked.connect(
            self.scene_double_clicked
        )

        self.scene_list.setFixedWidth(
            280
        )

        #
        # remove scrollbars
        #

        self.scene_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )

        self.scene_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )

        right = QVBoxLayout()

        right.addWidget(
            self.scene_list,
            stretch=1,
        )

        # Remove the highlighted scene(s).  Enabled only while something is
        # selected (see _update_remove_button).
        self.remove_scenes_btn = QPushButton(
            self.tr("Remove Selected Scenes")
        )
        self.remove_scenes_btn.setEnabled(False)
        self.remove_scenes_btn.setFocusPolicy(Qt.NoFocus)
        self.remove_scenes_btn.clicked.connect(
            self.delete_selected_scenes
        )
        self.scene_list.itemSelectionChanged.connect(
            self._update_remove_button
        )

        right.addWidget(
            self.remove_scenes_btn
        )

        self.info_panel = InfoPanel(
            self
        )

        right.addWidget(
            self.info_panel
        )

        content.addLayout(
            right
        )

        # Keep the scene list's height tied to the video preview's, so its
        # bottom edge lines up with the bottom of the preview rather than
        # running on down past it.  Both columns are the same total height, so
        # matching the list to the preview is all that's needed.
        #
        # Use a MAXIMUM (not a fixed) height: a fixed height is also a hard
        # minimum, which stopped the window shrinking back when un-maximised.
        # A maximum caps the list to the preview while still letting it - and
        # the window - get smaller freely.
        self._preview_default_resize = self.preview.resizeEvent

        def _sync_scene_list_height(event):
            self._preview_default_resize(event)
            self.scene_list.setMaximumHeight(
                self.preview.height()
            )
            # While playing, the background worker pre-scales each frame to a
            # fixed size, so a resize/maximise wouldn't otherwise take effect
            # until the next pause re-rendered.  Two steps keep it smooth:
            #   * immediately rescale the frame already on screen to the new
            #     size, so no old-size picture is left sitting in the resized
            #     window; then
            #   * tell the worker the new size - it flushes its old-size
            #     look-ahead and refills at the new size (see set_size).
            if self.playing and self.playback_worker is not None:
                pm = self.preview.pixmap()
                if pm is not None and not pm.isNull():
                    self.preview.setPixmap(pm.scaled(
                        self.preview.width(),
                        self.preview.height(),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    ))
                self.playback_worker.set_size(
                    self.preview.width(),
                    self.preview.height(),
                )

        self.preview.resizeEvent = _sync_scene_list_height

        layout.addLayout(
            content
        )

        #
        # Indexing progress bar (lives in the status bar, hidden until used)
        #

        self.index_progress = QProgressBar()

        self.index_progress.setMaximumWidth(
            220
        )

        self.index_progress.setMaximumHeight(
            14
        )

        self.index_progress.setTextVisible(
            True
        )

        self.index_progress.hide()

        self.statusBar().addPermanentWidget(
            self.index_progress
        )

    def _menu_label(self, text, action_key):
        """Append the configured keyboard shortcut as a right-aligned hint.

        The shortcut is actually handled in keyPressEvent (so it stays driven
        by the user-editable config); this only shows the hint in the menu so
        the two never drift apart.
        """
        sc = (
            self.config
            .get("shortcuts", {})
            .get(action_key, "")
        )
        return f"{text}\t{sc}" if sc else text

    #
    # Unsaved-changes tracking
    #
    # "Dirty" simply means the current scene list differs from the last state
    # we consider saved.  We snapshot that saved state on load, on Save
    # Project, and after a successful export (which, VRD-style, is treated as a
    # save point).  Comparing against a snapshot is robust - we don't have to
    # remember to flag every individual edit.
    #

    def _is_dirty(self):
        return sorted(self.selection.ranges) != self._saved_ranges

    def _update_title(self):
        if getattr(self, "current_filename", None):
            name = os.path.basename(self.current_filename)
            star = " *" if self._is_dirty() else ""
            self.setWindowTitle(f"{VERSION_STRING} - {name}{star}")
        else:
            self.setWindowTitle(VERSION_STRING)

    def _mark_saved(self):
        """Record the current scene list as the saved baseline."""
        self._saved_ranges = sorted(self.selection.ranges)
        self._update_title()

    def _confirm_discard_changes(self, title="Unsaved changes"):
        """If there are unsaved scene edits, ask what to do.  Returns True if
        it's OK to proceed (the user saved or chose to discard), or False to
        abort the current action."""
        if not self._is_dirty():
            return True

        choice = QMessageBox.question(
            self,
            title,
            self.tr("You have unsaved changes to the scene list.\n\n"
            "Save them as a project before continuing?"),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )

        if choice == QMessageBox.Cancel:
            return False

        if choice == QMessageBox.Discard:
            return True

        # Save: only proceed if the save actually succeeds (e.g. not cancelled
        # at the file picker).
        return bool(self.save_project())

    def build_menu(self):

        menu = (
            self.menuBar()
            .addMenu(
                self.tr("File")
            )
        )

        open_action = menu.addAction(
            self._menu_label(self.tr("Open Video"), "open_video")
        )
        open_action.triggered.connect(self.open_video)

        recent_menu = menu.addMenu(self.tr("Open Recent"))
        self._populate_recent_menu(recent_menu)
        menu.aboutToShow.connect(
            lambda rm=recent_menu: self._populate_recent_menu(rm)
        )

        export_action = menu.addAction(
            self._menu_label(self.tr("Save Video…"), "save_video")
        )
        export_action.triggered.connect(self.export_video)

        close_action = menu.addAction(
            self._menu_label(self.tr("Close Video"), "close_video")
        )
        close_action.triggered.connect(self.close_video)

        menu.addSeparator()

        open_project_action = menu.addAction(
            self._menu_label(self.tr("Open Project…"), "open_project")
        )
        open_project_action.triggered.connect(self.open_project)

        save_project_action = menu.addAction(
            self._menu_label(self.tr("Save Project"), "save_project")
        )
        save_project_action.triggered.connect(self.save_project)

        save_project_as_action = menu.addAction(
            self._menu_label(self.tr("Save Project As…"), "save_project_as")
        )
        save_project_as_action.triggered.connect(self.save_project_as)

        menu.addSeparator()

        queue_batch_action = menu.addAction(
            self._menu_label(self.tr("Queue to Batch"), "queue_to_batch")
        )
        queue_batch_action.triggered.connect(self.queue_to_batch)

        menu.addSeparator()

        exit_action = menu.addAction(self.tr("Exit"))
        exit_action.triggered.connect(self.close)

        edit_menu = (
            self.menuBar()
            .addMenu(
                self.tr("Edit")
            )
        )

        mark_in_action = edit_menu.addAction(
            self._menu_label(self.tr("Mark In"), "mark_in")
        )
        mark_in_action.triggered.connect(self.mark_in)

        mark_out_action = edit_menu.addAction(
            self._menu_label(self.tr("Mark Out"), "mark_out")
        )
        mark_out_action.triggered.connect(self.mark_out)

        edit_menu.addSeparator()

        add_selection_action = edit_menu.addAction(
            self._menu_label(self.tr("Add Selection"), "commit_selection")
        )
        add_selection_action.triggered.connect(self.commit_selection)

        add_unselected_action = edit_menu.addAction(
            self._menu_label(self.tr("Add Unselected"), "add_unselected")
        )
        add_unselected_action.triggered.connect(self.add_unselected)

        clear_all_action = edit_menu.addAction(
            self._menu_label(self.tr("Clear All Scenes"), "clear_all_scenes")
        )
        clear_all_action.triggered.connect(self.clear_all_scenes)

        edit_menu.addSeparator()

        goto_start_action = edit_menu.addAction(
            self._menu_label(self.tr("Previous Scene Start"), "goto_selection_start")
        )
        goto_start_action.triggered.connect(self.goto_selection_start)

        goto_end_action = edit_menu.addAction(
            self._menu_label(self.tr("Next Scene End"), "goto_selection_end")
        )
        goto_end_action.triggered.connect(self.goto_selection_end)

        joiner_menu = (
            self.menuBar()
            .addMenu(
                self.tr("Joiner")
            )
        )

        add_to_joiner_action = joiner_menu.addAction(
            self.tr("Add Current Project To Joiner List")
        )
        add_to_joiner_action.triggered.connect(self.add_current_to_joiner)

        edit_joiner_action = joiner_menu.addAction(
            self.tr("Edit Joiner List…")
        )
        edit_joiner_action.triggered.connect(self.edit_joiner_list)

        create_joiner_action = joiner_menu.addAction(
            self.tr("Create Video From Joiner List…")
        )
        create_joiner_action.triggered.connect(self.create_joiner_video)

        tools_menu = (
            self.menuBar()
            .addMenu(
                self.tr("Tools")
            )
        )

        qsf_action = (
            tools_menu
            .addAction(
                self.tr("Quick Stream Fix…")
            )
        )

        qsf_action.triggered.connect(
            self.quick_stream_fix
        )

        detect_action = (
            tools_menu
            .addAction(
                self._menu_label(self.tr("Detect Commercials\u2026"), "detect_commercials")
            )
        )

        detect_action.triggered.connect(
            self.detect_commercials
        )

        batch_action = (
            tools_menu
            .addAction(
                self.tr("Batch Manager…")
            )
        )

        batch_action.triggered.connect(
            self.open_batch_manager
        )

        profiles_action = (
            tools_menu
            .addAction(
                self.tr("Manage Profiles…")
            )
        )

        profiles_action.triggered.connect(
            self.open_profile_manager
        )

        tools_menu.addSeparator()

        info_action = (
            tools_menu
            .addAction(
                self._menu_label(
                    self.tr("Show Video Programme Info"),
                    "show_program_info",
                )
            )
        )

        info_action.triggered.connect(
            self.show_program_info
        )

        trim_copy_action = (
            tools_menu
            .addAction(
                self.tr("Trim and Copy Source File…")
            )
        )

        trim_copy_action.triggered.connect(
            self.show_trim_copy
        )

        tools_menu.addSeparator()

        log_action = (
            tools_menu
            .addAction(
                self.tr("Open Log Folder")
            )
        )

        log_action.triggered.connect(
            self.open_log_folder
        )

        settings_action = (
            tools_menu
            .addAction(
                self.tr("Settings…")
            )
        )

        settings_action.triggered.connect(
            self.open_settings
        )

        #
        # Extras menu - companion-app launchers and optional add-on features
        # (the TMDB/TVDB renamer will land here later).
        #
        extras_menu = self.menuBar().addMenu(self.tr("Extras"))

        renamer_action = extras_menu.addAction(self.tr("TV Renamer…"))
        renamer_action.triggered.connect(self._open_renamer)

        film_action = extras_menu.addAction(self.tr("Film Renamer…"))
        film_action.triggered.connect(self._open_film_renamer)

        watcher_action = extras_menu.addAction(self.tr("Launch VRD Next Watcher"))
        watcher_action.triggered.connect(self._launch_watcher)

        #
        # Help menu - added last so it sits at the far right of the bar, the
        # conventional home for About.
        #
        help_menu = self.menuBar().addMenu(self.tr("Help"))

        guide_action = help_menu.addAction(self.tr("User Guide"))
        guide_action.setShortcut("F1")
        guide_action.triggered.connect(self.show_user_guide)

        help_menu.addSeparator()

        about_action = help_menu.addAction(self.tr("About %s") % APP_NAME)
        about_action.triggered.connect(self.show_about)

    def show_user_guide(self):
        """Open the in-app User Guide (Help -> User Guide)."""
        from ui.help_dialog import UserGuideDialog
        UserGuideDialog(self).exec()

    def show_about(self):
        """Modal About dialog: app icon, version, GitHub link and developer."""
        from PySide6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QLabel,
            QPushButton,
        )
        from utils.icons import app_icon

        url = "https://github.com/infidelus/vrd-next"

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("About %s") % APP_NAME)

        layout = QVBoxLayout(dialog)

        icon_label = QLabel()
        icon_label.setPixmap(app_icon().pixmap(72, 72))
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        version_line = self.tr("Version %s") % VERSION
        blurb = self.tr(
            "An open-source, Linux-native, frame-accurate video cutter, "
            "heavily inspired by VideoReDo."
        )
        body = QLabel(
            "<div style='text-align:center'>"
            f"<h2 style='margin:6px 0 2px 0'>{APP_NAME}</h2>"
            f"<p style='margin:0;color:#9aa0a6'>{version_line}</p>"
            f"<p style='margin:12px 0 2px 0'>{blurb}</p>"
            f"<p style='margin:10px 0 0 0'>"
            f"<a href='{url}' style='color:#4ea3ff'>{url}</a></p>"
            "</div>"
        )
        body.setTextFormat(Qt.RichText)
        body.setOpenExternalLinks(True)
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignCenter)
        layout.addWidget(body)

        # A little breathing room so OK isn't pressed against the link, and
        # centre it rather than tucking it into the corner.
        layout.addSpacing(12)

        ok_button = QPushButton(self.tr("OK"))
        ok_button.setDefault(True)
        ok_button.clicked.connect(dialog.accept)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(ok_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        dialog.setMinimumWidth(380)
        dialog.exec()

    def _open_renamer(self):
        """Open the TV Renamer (Extras -> TV Renamer)."""
        from ui.renamer_dialog import RenamerDialog

        dialog = RenamerDialog(self.config, self)
        dialog.exec()

    def _open_film_renamer(self):
        """Open the Film Renamer (Extras -> Film Renamer)."""
        from ui.film_renamer_dialog import FilmRenamerDialog

        dialog = FilmRenamerDialog(self.config, self)
        dialog.exec()

    def _launch_watcher(self):
        """Start the standalone VRD Next Watcher tray app as its own detached
        process.

        If a Watcher is already running we just say so rather than spawning a
        duplicate (the Watcher refuses to start twice itself, but checking here
        means the user gets told why nothing new appeared).
        """
        import os
        import sys
        import subprocess

        from watch.single_instance import watcher_is_running

        if watcher_is_running():
            QMessageBox.information(
                self,
                self.tr("VRD Next Watcher"),
                self.tr("The VRD Next Watcher is already running - look for its icon "
                "in your system tray."),
            )
            return

        watcher_py = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "watcher.py",
        )

        if not os.path.exists(watcher_py):
            QMessageBox.warning(
                self,
                self.tr("VRD Next Watcher"),
                self.tr("Couldn't find watcher.py alongside the application."),
            )
            return

        try:
            # start_new_session detaches it: it outlives the editor and won't
            # receive the editor's signals.
            subprocess.Popen(
                [sys.executable, watcher_py],
                start_new_session=True,
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                self.tr("VRD Next Watcher"),
                self.tr("Couldn't start the Watcher:\n%s") % e,
            )
            return

        QMessageBox.information(
            self,
            self.tr("VRD Next Watcher"),
            self.tr("The VRD Next Watcher has started and now lives in your system "
            "tray."),
        )

    def show_program_info(self):
        """Show the open file's stream/format details (Tools -> Show Video
        Programme Info)."""
        path = self.current_filename or self.original_source
        if not path:
            return

        from ui.program_info_dialog import ProgramInfoDialog

        frame_count = len(self.frames) if self.frames else None
        dialog = ProgramInfoDialog(path, frame_count, self.fps, self)
        dialog.exec()

        # Pull focus back so the navigation keys keep working afterwards.
        self.setFocus()

    def show_trim_copy(self):
        """Open the Trim and Copy Source File dialog (Tools -> Trim and Copy
        Source File), pre-filled with the open recording if there is one."""
        from ui.trim_copy_dialog import TrimCopyDialog

        source = self.current_filename or self.original_source
        dialog = TrimCopyDialog(
            source,
            self.index,
            self.selection,
            source_dir=self._start_dir("open"),
            output_dir=self._start_dir("export"),
            parent=self,
        )
        dialog.exec()

        # Pull focus back so the navigation keys keep working afterwards.
        self.setFocus()

    def add_current_to_joiner(self):
        """Joiner -> Add Current Project To Joiner List.

        Adds each of the open recording's current scenes as its own joiner
        entry, so scenes can be freely reordered and interleaved with scenes
        from other videos.  No need to save a project first; each scene is
        captured in seconds so the entry is self-contained.
        """
        from project.joiner import JoinerEntry
        from project.vprj import cuts_seconds_from_keeps
        from utils.timecode import frame_to_timecode

        source = self.current_filename
        if not source or self.index is None:
            QMessageBox.information(
                self, self.tr("Joiner"),
                self.tr("Open a recording before adding it to the joiner list."))
            return

        total_frames = self.index.frame_count
        # Kept scenes; an empty selection means "the whole file".
        keep = sorted(self.selection.ranges) or [(0, total_frames - 1)]
        fps = self.fps or 25.0

        for start_f, end_f in keep:
            # Per-scene cut list (everything except this one scene), captured
            # frame-accurately so a .vprj can be rebuilt for edit/render.
            cuts, total_duration = cuts_seconds_from_keeps(
                [(start_f, end_f)], self.index)
            # Estimate this scene's size as its share of the source file.
            scene_mb = (
                self.source_size_mb * ((end_f - start_f + 1) / total_frames)
                if total_frames else 0.0
            )
            self.joiner_list.add(JoinerEntry(
                kind=JoinerEntry.KIND_SCENE,
                source=source,
                description="%s - %s" % (
                    frame_to_timecode(start_f), frame_to_timecode(end_f)),
                start=self.index.seconds_of(start_f),
                end=self.index.seconds_of(end_f),
                cuts=cuts,
                total_duration=total_duration,
                fps=fps,
                size_mb=scene_mb,
            ))

        added = len(keep)
        total = len(self.joiner_list)
        self.statusBar().showMessage(
            "Added %d scene%s from %s (%d in joiner list)."
            % (added, "" if added == 1 else "s",
               os.path.basename(source), total), 5000)

        self.info_panel.update_info()

    def edit_joiner_list(self):
        """Joiner -> Edit Joiner List.  Opens the joiner editing dialog."""
        from ui.joiner_dialog import JoinerDialog

        dialog = JoinerDialog(
            self.joiner_list, joiner_dir=self._start_dir("export"), parent=self)
        if dialog.exec():
            self.joiner_list = dialog.result_list()
            if dialog.entry_to_edit is not None:
                self._load_joiner_entry(dialog.entry_to_edit)
            elif getattr(dialog, "create_requested", False):
                self.create_joiner_video(
                    clear_after=dialog.clear_after_requested())
        self.info_panel.update_info()
        self.setFocus()

    def create_joiner_video(self, clear_after=False):
        """Joiner -> Create Video From Joiner List.

        Renders each scene and joins them into one output file (a stream-copy
        join, for entries that share the same format).
        """
        from export.joiner_render import JoinerRenderWorker

        entries = list(self.joiner_list.entries)
        if not entries:
            QMessageBox.information(
                self, self.tr("Joiner"),
                self.tr("The joiner list is empty.  Add one or more scenes first "
                "(Joiner -> Add Current Project To Joiner List)."))
            return

        from project.joiner import JoinerEntry
        missing = [e for e in entries
                   if e.kind != JoinerEntry.KIND_TITLE and not e.exists]
        if missing:
            names = "\n".join(os.path.basename(e.source) for e in missing[:8])
            QMessageBox.warning(
                self, self.tr("Joiner"),
                self.tr("Some entries refer to files that can't be found, so the video "
                "can't be created:\n\n%s") % (names,))
            return

        # Quick header-only scan.  If the scenes don't share a format - or the
        # list includes a title card (which can't be stream-copied alongside
        # broadcast) - offer to re-encode everything to a common format.
        from export.joiner_render import (
            scan_join_compatibility, recommended_target)
        compatible, formats = scan_join_compatibility(entries)
        has_title = any(e.kind == JoinerEntry.KIND_TITLE for e in entries)
        has_fade = any(
            (getattr(e, "fade_in", 0.0) or getattr(e, "fade_out", 0.0))
            for e in entries
        )
        reencode_target = None
        if (not compatible) or has_title or has_fade:
            tw, th, tfps = recommended_target(entries)
            reasons = []
            if not compatible:
                reasons.append("the scenes don't all use the same format")
            if has_title:
                reasons.append("the list includes a title card")
            if has_fade:
                reasons.append("a fade is set on one or more clips")
            reason = " and ".join(reasons)
            detail = ("\n\nFormats found:\n%s" % (formats,)) if formats else ""
            resp = QMessageBox.question(
                self, self.tr("Joiner"),
                self.tr("Because %s, the whole video will be re-encoded to a common "
                "format:%s\n\n"
                "    H.264, %d×%d, %d fps, AAC stereo\n\n"
                "Lower-resolution scenes are upscaled to match the highest. "
                "This re-encodes everything, so it's slower and slightly "
                "reduces quality.\n\nGo ahead?")
                % (reason, detail, tw, th, tfps),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if resp != QMessageBox.StandardButton.Yes:
                return
            reencode_target = (tw, th, tfps)

        out_format = self._ask_export_options()
        if out_format is None:
            return
        ext = {"match": ".ts", "mkv": ".mkv", "mp4": ".mp4"}.get(
            out_format, ".ts")
        save_filter = {
            "match": "Transport stream (*.ts)",
            "mkv": "Matroska video (*.mkv)",
            "mp4": "MP4 video (*.mp4)",
        }.get(out_format, "Transport stream (*.ts)")

        out, _ = QFileDialog.getSaveFileName(
            self, self.tr("Create Joined Video"), self._start_dir("export"),
            "%s;;All files (*)" % (save_filter,),
            "", QFileDialog.Option.DontConfirmOverwrite)
        if not out:
            return
        if not out.lower().endswith(ext):
            out += ext
        if os.path.exists(out):
            resp = QMessageBox.question(
                self, self.tr("Create Joined Video"),
                self.tr("%s already exists.\n\nOverwrite it?")
                % (os.path.basename(out),),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if resp != QMessageBox.StandardButton.Yes:
                return

        progress = QProgressDialog(self.tr("Preparing…"), self.tr("Cancel"), 0, 100, self)
        progress.setWindowTitle(self.tr("Create Joined Video"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)

        worker = JoinerRenderWorker(
            entries, out, out_format, reencode_target, self)
        self._joiner_worker = worker          # keep a reference while it runs

        def on_progress(percent, label):
            progress.setLabelText(label)
            progress.setValue(percent)

        def on_done(path):
            progress.close()
            self.statusBar().showMessage(
                "Joined video created: %s" % (os.path.basename(path),), 6000)
            if clear_after:
                self.joiner_list.clear()
            QMessageBox.information(
                self, self.tr("Joiner"), self.tr("Joined video created:\n\n%s") % (path,))

        def on_failed(message):
            progress.close()
            if message != "Cancelled.":
                QMessageBox.critical(
                    self, self.tr("Joiner"),
                    self.tr("Could not create the joined video:\n\n%s") % (message,))

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_done)
        worker.failed.connect(on_failed)
        progress.canceled.connect(worker.cancel)
        worker.start()

    def _load_joiner_entry(self, entry):
        """Load a joiner entry back into the editor (the dialog's Edit
        selection).  The source is reopened with just this scene kept."""
        if not entry.source or not os.path.exists(entry.source):
            QMessageBox.warning(
                self, self.tr("Joiner"),
                self.tr("That entry's file could no longer be found:\n\n%s")
                % (entry.source,))
            return

        import tempfile
        from project.vprj import save_vprj_from_cuts

        # Write the scene's cut list to a temporary .vprj and load it the same
        # way Open Project does.  A single reused path is fine: only one Edit
        # selection can be in flight at a time, and load_project_file reads the
        # file during its deferred after-index step.
        tmp = os.path.join(tempfile.gettempdir(), "vrd-next-joiner-edit.vprj")
        save_vprj_from_cuts(
            tmp, entry.source, entry.cuts, entry.total_duration, entry.fps)
        self.load_project_file(tmp, title="Edit Joiner Selection",
                               remember=False)

    def open_log_folder(self):
        """Open the folder containing the log files in the system file
        manager, so the current log is easy to find when reporting a problem."""
        from utils.applog import log_directory
        from utils.open_path import open_path

        directory = log_directory()
        if directory is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                self.tr("Logs"),
                self.tr("No log folder is available yet."),
            )
            return

        log.info("Opening log folder: %s", directory)
        open_path(directory)

    def _translate_wine_path(self, win_path):
        """Translate a Windows path (e.g. H:\\dir\\file.ts) to a Linux path
        using the WINE drive mappings in ~/.wine/dosdevices, if present.

        Returns the translated path, or None if it can't be resolved.
        """
        win_path = win_path.strip()
        if len(win_path) < 2 or win_path[1] != ":":
            return None

        drive = win_path[0].lower()
        rest = win_path[2:].lstrip("\\/").replace("\\", "/")

        dev = os.path.join(
            os.path.expanduser("~"),
            ".wine",
            "dosdevices",
            f"{drive}:",
        )

        if not os.path.exists(dev):
            return None

        try:
            target = os.path.realpath(dev)
        except OSError:
            return None

        candidate = os.path.join(target, rest)
        return candidate

    def _resolve_project_source(self, embedded_path):
        """Find the real, existing video file for a project's stored path.

        Tries, in order: the embedded path as-is; a WINE drive-mapping
        translation if it's a Windows path.  Returns an existing path, or None
        if neither resolves (the caller then prompts the user to locate it).
        """
        if embedded_path and os.path.isfile(embedded_path):
            return embedded_path

        # Windows path?  Try translating via WINE's drive mappings.
        if embedded_path and len(embedded_path) >= 2 and embedded_path[1] == ":":
            translated = self._translate_wine_path(embedded_path)
            if translated and os.path.isfile(translated):
                return translated

        return None

    def detect_commercials(self):
        """Run Comskip on the open file and populate the timeline with the
        detected scenes (commercial breaks removed)."""
        from repair.comskip import ComskipWorker

        if not self.frames or self.index is None:
            QMessageBox.information(
                self,
                self.tr("Detect Commercials"),
                self.tr("Open a video first."),
            )
            return

        binary = self.config.get("paths", {}).get("comskip_binary", "")
        ini = self.config.get("paths", {}).get("comskip_ini", "")

        # Same per-channel .ini selection the Watcher uses (Settings > External
        # tools), matched against the open recording's filename.
        if self.config.get("paths", {}).get("comskip_ini_by_channel", False):
            from repair.comskip import pick_comskip_ini
            picked = pick_comskip_ini(self.current_filename, ini)
            if picked != ini:
                log.info(
                    "Detect Commercials: using channel Comskip .ini: %s",
                    os.path.basename(picked),
                )
            ini = picked

        if not binary or not os.path.isfile(binary):
            QMessageBox.information(
                self,
                self.tr("Detect Commercials"),
                self.tr("The Comskip program hasn't been set yet.\n\n"
                "Add the path to Comskip (and optionally its .ini file) in "
                "Tools > Settings > Folders, then try again."),
            )
            return

        if self.selection.ranges:
            confirm = QMessageBox.question(
                self,
                self.tr("Detect Commercials"),
                self.tr("This will replace your current scene markers with Comskip's "
                "detected scenes. Continue?"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if confirm != QMessageBox.Yes:
                return

        progress = QProgressDialog(
            self.tr("Detecting commercials (Comskip)…"),
            self.tr("Cancel"),
            0,
            100,
            self,
        )
        progress.setWindowTitle(self.tr("Detect Commercials"))
        progress.setMinimumDuration(0)
        progress.setValue(0)

        worker = ComskipWorker(binary, ini, self.current_filename, self)
        self._comskip_worker = worker

        worker.progress.connect(progress.setValue)

        def _done(edl_path):
            from project.edl import load_edl
            progress.close()
            try:
                keep_ranges, _ = load_edl(edl_path, self.index)
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    self.tr("Detect Commercials"),
                    self.tr("Comskip finished but its output could not be read:\n\n%s") % exc,
                )
                worker.cleanup()
                self._comskip_worker = None
                return

            worker.cleanup()
            self._comskip_worker = None

            if not keep_ranges:
                QMessageBox.information(
                    self,
                    self.tr("Detect Commercials"),
                    self.tr("Comskip found no commercials to remove (the whole file "
                    "is one scene)."),
                )
                return

            self.selection.ranges = list(keep_ranges)
            self.scenes.markers = []
            self._refresh_scenes_from_selection()

            # Jump to the start of the first detected scene.
            self.goto_frame(keep_ranges[0][0])

            self.statusBar().showMessage(
                f"Comskip found {len(keep_ranges)} scene(s)."
            )

        def _fail(message):
            progress.close()
            worker.cleanup()
            self._comskip_worker = None
            QMessageBox.warning(
                self,
                self.tr("Detect Commercials"),
                message,
            )

        worker.finished_ok.connect(_done)
        worker.failed.connect(_fail)
        progress.canceled.connect(worker.cancel)

        worker.start()

    def open_project(self):
        start = self._start_dir("project")
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Import Project"),
            start,
            "VideoReDo Project (*.vprj *.VPrj *.Vprj);;All files (*)",
        )

        if not path:
            return

        self._remember_dir("project", path)
        self.load_project_file(path, "Import Project")

    def load_project_file(self, path, title="Import Project", remember=True):
        """Load a saved .vprj into the editor and make it the current project
        (so Save Project / Ctrl+P overwrites it).  Shared by Import Project and
        the Batch Manager's per-row Edit button.  remember=False suppresses
        adding it to the Recent list (used for transient internal loads such as
        the Joiner's temporary edit project)."""
        from project.vprj import load_vprj

        # Bring the editor to the front - the Batch Manager may be on top of it.
        self.raise_()
        self.activateWindow()

        # Warn before discarding unsaved scene edits from the current video.
        if not self._confirm_discard_changes(title):
            return False

        if remember:
            self._remember_recent(path)

        # Read the project's stored source path first (cheap - just the XML
        # header) so we can find the video before building an index.
        try:
            import xml.etree.ElementTree as ET
            root = ET.parse(path).getroot()
            fn = root.find("Filename")
            embedded = fn.text.strip() if (fn is not None and fn.text) else ""
        except Exception as exc:
            QMessageBox.warning(
                self,
                title,
                self.tr("This project file could not be read:\n\n%s") % exc,
            )
            return False

        source = self._resolve_project_source(embedded)

        if source is None:
            # VRD-style "can't find the source, locate it?" prompt.
            choice = QMessageBox.question(
                self,
                self.tr("Locate video file"),
                self.tr(
                    "There was a problem opening the video file associated with "
                    "this project.\n"
                    "The original file may not exist or may be mapped to a "
                    "different drive or folder.\n\n"
                    "Original file: %s\n\n"
                    "Do you wish to manually search for the file?"
                ) % embedded,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if choice != QMessageBox.Yes:
                return False

            # Open the picker at the project's folder if it exists, else the
            # configured open-folder, else the OS default.
            start_dir = ""
            if embedded:
                folder = os.path.dirname(embedded)
                if os.path.isdir(folder):
                    start_dir = folder
            if not start_dir:
                start_dir = self._start_dir("open")

            source, _ = QFileDialog.getOpenFileName(
                self,
                self.tr("Locate video for project"),
                start_dir,
                VIDEO_OPEN_FILTER,
            )
            if not source:
                return False

        # Load the video, then apply the project's cuts/markers once the index
        # is ready (same deferred-after-load mechanism the QSF reload uses).
        def _apply(new_index):
            data = load_vprj(path, new_index)
            self.selection.ranges = list(data.keep_ranges)
            self.scenes.markers = list(data.markers)
            self._refresh_scenes_from_selection()

            # Jump the cursor to the start of the first kept scene, as VRD does
            # on import, so the user lands where the content begins.
            if data.keep_ranges:
                self.goto_frame(data.keep_ranges[0][0])

            self.statusBar().showMessage(
                f"Project loaded: {os.path.basename(path)}"
            )

            # Remember this as the current project so "Save Project" (Ctrl+P)
            # overwrites it without prompting, VRD-style.
            self.current_project_path = path

            # A freshly imported project is, by definition, saved.
            self._mark_saved()

        self._pending_after_load = _apply
        self.original_source = source

        # Honour "Quick Stream Fix on open" for imported projects, the same way
        # Open Video does: repair first, then load the repaired copy once.  The
        # project stores times (100ns ticks), not frame numbers, so load_vprj
        # maps them onto whichever index we end up with - meaning we can repair
        # up-front rather than indexing the original, mapping, repairing and
        # indexing all over again.
        qsf_on_open = (
            self.config
            .get("settings", {})
            .get("qsf_on_open", False)
        )
        if qsf_on_open:
            self._open_with_qsf(source)
        else:
            self._load_file(source)
        return True

    def _can_save_project(self, title):
        """Shared guard for the project-save actions."""
        if not self.frames or self.index is None:
            QMessageBox.information(self, title, self.tr("Open a video first."))
            return False
        if not self.selection.ranges:
            QMessageBox.information(
                self,
                title,
                self.tr("No scenes marked to keep. Mark at least one scene before "
                "saving a project."),
            )
            return False
        return True

    def _write_project(self, path, title):
        """Write the current cuts/markers to path, updating the current
        project and the remembered folder.  Returns True on success."""
        from project.vprj import save_vprj

        try:
            save_vprj(
                path,
                self.selection.ranges,
                self.scenes.markers,
                self.current_filename,
                self.index,
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                title,
                self.tr("The project could not be saved:\n\n%s") % exc,
            )
            return False

        self.current_project_path = path
        self._remember_dir("project", path)
        self.statusBar().showMessage(
            f"Project saved: {os.path.basename(path)}"
        )
        self._mark_saved()
        return True

    def _autosave_project_on_export(self):
        """Write a project file alongside a successful export, the way VRD does.

        Saves to the already-associated project if there is one, otherwise to
        the Project Files folder under the original recording's name.  This is
        best-effort and quiet: the export itself is the real save point, so if
        the project can't be written we don't nag or block - we just skip it.
        """
        from project.vprj import save_vprj

        try:
            path = self.current_project_path

            if not path:
                project_dir = self._start_dir("project")
                if not project_dir:
                    project_dir = os.path.dirname(
                        self.original_source or self.current_filename
                    )
                os.makedirs(project_dir, exist_ok=True)
                base = self._original_base()
                path = os.path.join(project_dir, f"{base}.vprj")

            save_vprj(
                path,
                self.selection.ranges,
                self.scenes.markers,
                self.current_filename,
                self.index,
            )

            self.current_project_path = path
            self._remember_dir("project", path)
            self.statusBar().showMessage(
                f"Project saved: {os.path.basename(path)}"
            )
        except Exception:
            pass

    def save_project(self):
        """Save to the current project file without prompting (VRD's Ctrl+P).
        If no project file is associated yet, fall back to Save Project As.
        Returns True on a successful save."""
        if not self._can_save_project("Save Project"):
            return False

        if self.current_project_path:
            return self._write_project(self.current_project_path, "Save Project")
        else:
            return self.save_project_as()

    def save_project_as(self):
        """Prompt for a file and save the project there (VRD's Ctrl+Shift+P).
        Returns True on a successful save."""
        if not self._can_save_project("Save Project As"):
            return False

        if self.current_project_path:
            suggested = self.current_project_path
        else:
            base = os.path.splitext(
                os.path.basename(self.current_filename)
            )[0]
            start = self._start_dir("project")
            if not start:
                start = os.path.dirname(self.current_filename)
            suggested = os.path.join(start, f"{base}.vprj")

        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Project As"),
            suggested,
            "VideoReDo Project (*.vprj)",
        )

        if not path:
            return False

        if not path.lower().endswith(".vprj"):
            path += ".vprj"

        return self._write_project(path, "Save Project As")

    def restore_default_window_size(self):
        """Un-maximise (if needed) and resize the window to the built-in
        default.  Handy when you've dragged it around and want it back."""
        from config.defaults import DEFAULT_CONFIG

        win = DEFAULT_CONFIG.get("window", {})
        self.showNormal()
        self.resize(
            int(win.get("width", 1400)),
            int(win.get("height", 900)),
        )

    def open_settings(self):
        from ui.settings_dialog import SettingsDialog
        from config.loader import CONFIG_FILE, CONFIG_DIR, ensure_config

        # Default log folder when none is set: the app config directory.
        default_log_folder = str(CONFIG_DIR)

        # Remember the language so we can offer a restart if it's changed (a
        # language switch only takes full effect on the next launch).
        old_language = self.config.get("settings", {}).get("language", "en")

        dialog = SettingsDialog(
            self.config,
            CONFIG_FILE,
            default_log_folder,
            self,
        )

        result = dialog.exec()

        if result == QDialog.Accepted:
            self.config = dialog.result_config()
            save_config(self.config)
            # open_video / export read self.config each time, so the new
            # paths take effect immediately - no restart needed.
            apply_tool_paths(self.config)
            # Re-check the tool paths now, so setting a bad one is flagged at
            # once rather than only on the next Open.
            self._warned_missing_tools = False
            self._warn_if_tools_missing()
            self._reconfigure_logging()
            self._apply_frame_type_display()
            self._apply_shortcut_changes()
            self._apply_theme()
            self._prompt_language_restart(old_language)

        elif result == SettingsDialog.EDITED_EXTERNALLY:
            # The user edited config.json directly (or restored defaults); reload
            # it from disk so the running app picks up the change.  Shortcut
            # clashes are caught in the editor at save time, so there's nothing
            # to re-check here.
            self.config = ensure_config()
            apply_tool_paths(self.config)
            self._reconfigure_logging()
            self._apply_frame_type_display()
            self._apply_shortcut_changes()
            self._apply_theme()
            self._prompt_language_restart(old_language)

    def _prompt_language_restart(self, old_language):
        """If the interface language was changed, tell the user it needs a
        restart and offer to do it for them."""
        new_language = self.config.get("settings", {}).get("language", "en")
        if new_language == old_language:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(self.tr("Language changed"))
        box.setText(self.tr(
            "The interface language will change when VRD Next is restarted."
        ))
        box.setInformativeText(self.tr("Restart now?"))
        restart_btn = box.addButton(
            self.tr("Restart now"), QMessageBox.AcceptRole
        )
        box.addButton(self.tr("Later"), QMessageBox.RejectRole)
        box.setDefaultButton(restart_btn)
        box.exec()
        if box.clickedButton() is restart_btn:
            self._restart_application()

    def _restart_application(self):
        """Relaunch VRD Next.  The restart goes through the normal window close,
        so any unsaved project or running batch is handled first; if the user
        cancels at one of those prompts, the restart is abandoned and the app
        stays open."""
        self._restart_after_close = True
        self.close()
        # Still visible means the close was cancelled - don't restart later.
        if self.isVisible():
            self._restart_after_close = False

    def _apply_theme(self):
        """Re-apply the chosen appearance (System / Light / Dark) live.

        After swapping the palette we clear the icon cache and ask every widget
        that knows how (the transport panel and controls) to re-colour its
        readouts and reload its icons, so the whole interface updates without a
        restart.
        """
        try:
            from PySide6.QtWidgets import QApplication
            from ui.theme import apply_theme
            from utils.icons import clear_cache
            mode = self.config.get("settings", {}).get("theme", "system")
            app = QApplication.instance()
            apply_theme(app, mode, wrap_style=_SlowTooltipStyle)
            # Cached icons were rendered in the previous theme's colour; drop
            # them so they re-render, then refresh the widgets that hold them.
            clear_cache()
            for widget in app.allWidgets():
                refresh = getattr(widget, "refresh_theme", None)
                if callable(refresh):
                    try:
                        refresh()
                    except Exception:
                        pass
        except Exception:
            pass

    def _apply_shortcut_changes(self):
        """Re-bind keyboard shortcuts and rebuild the menus from the current
        config, so changes made in Settings (or a hand-edited config file) take
        effect immediately - no restart needed.

        The key dispatcher caches the shortcut map, and the menu hints are read
        once when each action is created, so both are rebuilt here against the
        now-current self.config.  build_menu only adds menus, so the bar is
        cleared first to avoid stacking a second copy.
        """
        self.keys = ShortcutManager(self.config)
        self.menuBar().clear()
        self.build_menu()

    def _reconfigure_logging(self):
        """Re-apply logging config (called after the log folder / retention may
        have changed in Settings).  Best-effort."""
        try:
            from utils.applog import configure_logging
            configure_logging(
                self.config.get("paths", {}).get("log_folder", ""),
                self.config.get("settings", {}).get("log_max_age_days", 30),
                self.config.get("settings", {}).get("verbose_logging", False),
                max_files=self.config.get("settings", {}).get(
                    "log_max_files", 30),
            )
        except Exception:
            pass

    def _apply_frame_type_display(self):
        """Push the frame-type display setting to the thumbnail bar and preview.

        Mode is one of "none"/"thumbnails"/"preview"/"both"; thumbnails show the
        badge for "thumbnails"/"both", the preview for "preview"/"both".
        """
        mode = self.config.get("settings", {}).get("frame_type_display", "none")
        self.thumbnail_bar.set_frame_type_display(mode)
        self._show_preview_frame_type = mode in ("preview", "both")
        if not self._show_preview_frame_type:
            self.preview._ft_letter = ""
        self.preview.update()

    def _set_preview_frame_type(self, letter):
        """Record the current preview frame's picture type and repaint the
        overlay (no-op when the preview badge is off)."""
        letter = letter if self._show_preview_frame_type else ""
        if getattr(self.preview, "_ft_letter", "") != letter:
            self.preview._ft_letter = letter
            self.preview.update()

    def _preview_paint_event(self, event):
        # Draw the video frame as usual, then overlay the picture-type letter
        # in the top-left corner if the preview badge is enabled.
        self._preview_default_paint(event)

        letter = getattr(self.preview, "_ft_letter", "")
        if not letter:
            return

        from PySide6.QtGui import QColor, QFont, QPainter

        colours = {
            "IDR": QColor("#37d67a"),
            "I": QColor("#37d67a"),
            "P": QColor("#f5a623"),
            "B": QColor("#9bb4d4"),
        }
        colour = colours.get(letter, QColor("#e0e0e0"))

        painter = QPainter(self.preview)
        try:
            font = QFont()
            font.setPixelSize(22)
            font.setBold(True)
            painter.setFont(font)

            metrics = painter.fontMetrics()
            pad = 4
            box_w = metrics.horizontalAdvance(letter) + pad * 2
            box_h = metrics.height()

            painter.fillRect(8, 8, box_w, box_h, QColor(0, 0, 0, 150))
            painter.setPen(colour)
            painter.drawText(8 + pad, 8 + metrics.ascent(), letter)
        finally:
            painter.end()

    def _set_qsf_on_open(self, enabled):
        self.config.setdefault("settings", {})["qsf_on_open"] = bool(enabled)
        save_config(self.config)

    def _start_dir(self, kind):
        """Return the directory a file dialog of the given kind should start
        in, honouring the user's path settings.

        kind is one of "open", "export", "project".

        - "fixed" mode: always start in the configured folder (if it exists).
        - "last" mode: start in the last-used folder; if none has been
          recorded yet, fall back to the configured folder (if set); failing
          that, the OS default (empty string).
        """
        paths = self.config.get("paths", {})
        mode = paths.get(f"{kind}_mode", "last")
        fixed = paths.get(f"{kind}_folder", "")
        last = paths.get(f"last_{kind}", "")

        if mode == "fixed":
            if fixed and os.path.isdir(fixed):
                return fixed
            # Configured folder missing/invalid - fall back gracefully.
            if last and os.path.isdir(last):
                return last
            return ""

        # "last" mode.
        if last and os.path.isdir(last):
            return last
        if fixed and os.path.isdir(fixed):
            return fixed
        return ""

    def _remember_dir(self, kind, chosen_path):
        """Store the folder of a chosen file as the last-used folder for the
        given kind, when that kind is in 'remember last used' mode."""
        if not chosen_path:
            return
        paths = self.config.setdefault("paths", {})
        if paths.get(f"{kind}_mode", "last") == "last":
            folder = os.path.dirname(chosen_path)
            if folder:
                paths[f"last_{kind}"] = folder
                save_config(self.config)

    # ---------------------------------------------------------------- #
    # Filename helpers
    # ---------------------------------------------------------------- #

    def _clean_basename(self, path):
        """The recording's name with extension and any QSF/temp decoration
        stripped, e.g. '/tmp/Movie - QSF.ts' -> 'Movie'.  Used so exported
        and QSF'd files are named after the original recording, never after a
        working copy."""
        base = os.path.splitext(os.path.basename(path or ""))[0]
        for prefix in (
                "vrd-next-manual-fix-",
                "vrd-next-export-fix-",
                "vrd-next-",
        ):
            if base.startswith(prefix):
                base = base[len(prefix):]
                break
        for suffix in (" - QSF", " - fixed", " - edited"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return base

    def _original_base(self):
        """Clean base name of the original recording, falling back to whatever
        is currently loaded if no original was recorded."""
        src = (
            getattr(self, "original_source", None)
            or self.current_filename
            or ""
        )
        return self._clean_basename(src)

    def _dedup_path(self, directory, filename):
        """Full path for `filename` in `directory`, appending ' (2)', ' (3)' …
        before the extension if a file with that exact name already exists, so
        an export never silently overwrites a previous one."""
        root, ext = os.path.splitext(filename)
        candidate = os.path.join(directory, filename)
        n = 2
        while os.path.exists(candidate):
            candidate = os.path.join(directory, f"{root} ({n}){ext}")
            n += 1
        return candidate

    def _qsf_temp_path(self, ext):
        """A /tmp path named '<original> - QSF<ext>' for an internal QSF working
        copy.  Guards the one collision that matters - it never returns the
        path of the file that's currently open (which a re-QSF would clobber)."""
        import tempfile
        name = f"{self._original_base()} - QSF{ext}"
        path = os.path.join(tempfile.gettempdir(), name)
        cur = (
            os.path.abspath(self.current_filename)
            if getattr(self, "current_filename", None) else ""
        )
        if os.path.abspath(path) == cur:
            root, e = os.path.splitext(name)
            path = os.path.join(
                tempfile.gettempdir(), f"{root} (2){e}"
            )
        return path

    def _log_source_info(self, filename):
        """Log a concise, ffprobe-style summary of the opened source, so a
        troubleshooting log shows exactly what kind of file is involved
        (container, codecs, stream layout and seek-relevant timing)."""
        try:
            import av
            with av.open(filename) as c:
                dur = float(c.duration) / 1e6 if c.duration else 0.0
                log.info(
                    "source: format=%s duration=%.1fs streams=%d",
                    getattr(c.format, "name", "?"), dur, len(c.streams),
                )
                for s in c.streams:
                    cc = s.codec_context
                    if s.type == "video":
                        log.info(
                            "  video[%d] %s profile=%s %sx%s field=%s "
                            "tb=%s start=%s",
                            s.index, cc.name, getattr(cc, "profile", None),
                            getattr(cc, "width", None),
                            getattr(cc, "height", None),
                            getattr(cc, "field_order", None),
                            s.time_base, s.start_time,
                        )
                    elif s.type == "audio":
                        log.info(
                            "  audio[%d] %s profile=%s %sHz %sch tb=%s start=%s",
                            s.index, cc.name, getattr(cc, "profile", None),
                            getattr(cc, "sample_rate", None),
                            getattr(cc, "channels", None),
                            s.time_base, s.start_time,
                        )
        except Exception as exc:
            log.info("source: could not probe %s (%s)", filename, exc)

    def open_video(self):

        filename, _ = (
            QFileDialog
            .getOpenFileName(
                self,
                self.tr("Open Video"),
                self._start_dir("open"),
                VIDEO_OPEN_FILTER
            )
        )

        if not filename:
            return

        self._remember_dir("open", filename)
        self._open_video_path(filename)

    def _warn_if_tools_missing(self):
        """Warn once per session about ffmpeg/ffprobe problems.

        Covers both a tool that can't be found at all and a configured path
        that points at a missing file (silently ignored otherwise).  The
        preview needs neither tool, but export, join and stream probing do -
        so flagging it on open gives the user a chance to fix it in Settings
        before it bites at export time.
        """
        if getattr(self, "_warned_missing_tools", False):
            return
        issues = tool_issues(self.config)
        if not issues:
            return
        self._warned_missing_tools = True
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(
            self, self.tr("External tools"),
            self.tr("The preview works without them, but exporting, joining and "
            "showing stream info need ffmpeg and ffprobe.\n\n") + "\n\n".join(issues),
        )

    def _open_video_path(self, filename):
        """Open a video by path - shared by Open Video and the Recent menu."""
        if not os.path.exists(filename):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, self.tr("Open Video"),
                self.tr("That file no longer exists:\n%s") % filename,
            )
            self._forget_recent(filename)
            return

        # Heads-up (once per session) if ffmpeg/ffprobe can't be found: the
        # preview needs neither, but export/join/probe do, so flag it now rather
        # than let it surprise the user at export time.
        self._warn_if_tools_missing()

        # Warn before discarding unsaved scene edits from the current video.
        if not self._confirm_discard_changes("Open Video"):
            return

        self._remember_recent(filename)

        # A freshly opened video starts with no scenes (the per-file reset
        # doesn't touch the cut list, so clear it explicitly here).
        self.selection.clear_all()
        self.scenes.markers = []

        # This is a brand-new video, not a loaded project, so forget any project
        # file the previous video was associated with - otherwise Ctrl+P (Save
        # Project) would silently overwrite that earlier video's .vprj.
        self.current_project_path = None

        # Remember the original recording the user chose, so exported/QSF'd
        # files are always named after it - never after a /tmp working copy
        # that a Quick Stream Fix loads in its place.
        self.original_source = filename

        #
        # If "Quick Stream Fix on open" is enabled, repair to /tmp first and
        # load the repaired copy; otherwise load the file directly.
        #

        qsf_on_open = (
            self.config
            .get("settings", {})
            .get("qsf_on_open", False)
        )

        if qsf_on_open:
            self._open_with_qsf(filename)
        else:
            self._load_file(filename)

    def _remember_recent(self, path):
        """Add a video to the recent-files list (newest first, max 5)."""
        recent = [p for p in self.config.get("recent_files", []) if p != path]
        recent.insert(0, path)
        del recent[5:]
        self.config["recent_files"] = recent
        save_config(self.config)

    def _forget_recent(self, path):
        """Drop a path from the recent-files list (e.g. it's gone now)."""
        current = self.config.get("recent_files", [])
        recent = [p for p in current if p != path]
        if recent != current:
            self.config["recent_files"] = recent
            save_config(self.config)

    def _clear_recent(self):
        """Empty the recent-files list."""
        if self.config.get("recent_files"):
            self.config["recent_files"] = []
            save_config(self.config)

    def _populate_recent_menu(self, recent_menu):
        """(Re)fill the File > Open Recent submenu from the saved list.

        Missing files are still listed - a recent list is a history - but shown
        greyed-out with a "(missing)" marker rather than hidden, so a source
        that has since been deleted or moved doesn't silently vanish.
        """
        recent_menu.clear()
        recent = self.config.get("recent_files", [])
        if not recent:
            act = recent_menu.addAction(self.tr("(no recent files)"))
            act.setEnabled(False)
            return
        for path in recent:
            exists = os.path.exists(path)
            label = os.path.basename(path)
            if not exists:
                label += "  (missing)"
            act = recent_menu.addAction(label)
            act.setToolTip(path)
            if not exists:
                act.setEnabled(False)
                continue
            if path.lower().endswith(".vprj"):
                act.triggered.connect(
                    lambda checked=False, p=path: self.load_project_file(p)
                )
            else:
                act.triggered.connect(
                    lambda checked=False, p=path: self._open_video_path(p)
                )
        recent_menu.addSeparator()
        clear_act = recent_menu.addAction(self.tr("Clear Recent"))
        clear_act.triggered.connect(self._clear_recent)

    def _open_with_qsf(self, filename):
        """Quick Stream Fix the chosen file into /tmp, then load the result."""
        import tempfile

        # If this file has already been Quick Stream Fixed, skip the redundant
        # repair silently and just open it - QSF-on-open shouldn't nag.  The
        # "run it again anyway?" prompt is reserved for a *manual* Quick Stream
        # Fix; opening should just work.
        try:
            from repair.qsf_registry import is_qsfd
            already_fixed = is_qsfd(filename)
        except Exception:
            already_fixed = False

        if already_fixed:
            self._load_file(filename)
            return

        ext = os.path.splitext(filename)[1] or ".ts"

        tmp_path = self._qsf_temp_path(ext)

        progress = QProgressDialog(
            self.tr("Quick Stream Fix on open (remuxing)…"),
            self.tr("Cancel"),
            0,
            100,
            self,
        )
        progress.setWindowTitle(self.tr("Opening"))
        progress.setMinimumDuration(0)

        worker = StreamFixWorker(filename, tmp_path, self)
        self._open_qsf_worker = worker

        worker.progress.connect(progress.setValue)

        def _done(path):
            progress.close()
            self._open_qsf_worker = None
            self._record_qsf(filename, path)
            self._load_file(path)

        def _fail(message):
            progress.close()
            self._open_qsf_worker = None
            #
            # If the repair fails, fall back to opening the original so the
            # user isn't blocked.
            #
            QMessageBox.warning(
                self,
                self.tr("Quick Stream Fix on open failed"),
                self.tr("%s\n\nOpening the original file instead.") % message,
            )
            self._load_file(filename)

        worker.finished_ok.connect(_done)
        worker.failed.connect(_fail)
        progress.canceled.connect(worker.cancel)

        worker.start()

    def _load_file(self, filename):
        #
        # Tear down any previously open media and its per-file state, so
        # opening a second file never inherits the first file's cuts.
        #

        log.info("Opening video: %s", filename)
        self._log_source_info(filename)

        self._reset_media_state()

        self.current_filename = filename

        #
        # Build (or load from cache) the frame index off the UI thread.
        #

        index, builder = get_or_build_index(
            filename,
            self,
        )

        if index is not None:
            #
            # Cache hit - ready immediately.
            #
            self._on_index_ready(index)
            return

        #
        # Cache miss - build with a progress bar.
        #

        self.index_builder = builder

        self.index_progress.setRange(
            0,
            100,
        )

        self.index_progress.setValue(
            0
        )

        self.index_progress.setFormat(
            "Indexing… %p%"
        )

        self.index_progress.show()

        self.statusBar().showMessage(
            self.tr("Indexing video…")
        )

        #
        # Estimate total frames from duration so the bar is determinate.
        #

        self._estimated_frames = (
            self._estimate_frame_count(
                filename
            )
        )

        builder.progress.connect(
            self._on_index_progress
        )

        builder.finished_index.connect(
            self._on_index_ready
        )

        builder.failed.connect(
            self._on_index_failed
        )

        builder.start()

    def close_video(self):
        """Unload the current video and return the UI to its empty state."""

        if not self.frames:
            return

        # Warn before discarding unsaved scene edits.
        if not self._confirm_discard_changes("Close Video"):
            return

        self._reset_media_state()

        #
        # Empty the cut list and markers (the per-file reset doesn't touch
        # them) and forget the filenames BEFORE refreshing the widgets, so the
        # scene list clears and the title drops back to the bare app name with
        # no stale "*filename".
        #

        self.selection.clear_all()
        self.scenes.markers = []

        self.current_filename = None
        self.current_project_path = None
        self.original_source = None

        #
        # Refresh the widgets back to their empty (no-video) appearance.  The
        # video preview keeps its last pixmap until told otherwise, so clear it
        # explicitly; the rest read from the now-empty selection/scenes.
        #

        if hasattr(self, "preview"):
            self.preview.clear()
            self.preview._ft_letter = ""

        self.scene_list.refresh()
        self.scene_bar.update()
        self.thumbnail_bar.refresh()
        self.info_panel.update_info()
        self.update_timecode()

        if hasattr(self, "transport_panel"):
            self.transport_panel.update_transport()

        if hasattr(self, "transport_controls"):
            self.transport_controls.update_buttons()

        self._set_controls_enabled(False)

        self.audio.clear()
        self._audio_active = False

    def _set_controls_enabled(self, on):
        """Enable/disable the playback, mark and action controls below the
        timeline.  They are off when no video is loaded."""
        self.transport_panel.set_enabled(on)
        self.transport_controls.set_enabled(on)
        self.action_bar.set_enabled(on)

    #
    # Playback state.  `playing` is a property so that every place that starts
    # or stops playback (there are several) keeps the audio in step without
    # each having to remember to do so.
    #

    @property
    def playing(self):
        return getattr(self, "_playing", False)

    @playing.setter
    def playing(self, value):
        value = bool(value)
        changed = value != getattr(self, "_playing", False)
        self._playing = value
        if changed:
            self._update_audio()
            self._update_playback()

    def _update_playback(self):
        """Start/stop the background playback decoder with the play state."""
        worker = getattr(self, "playback_worker", None)
        if worker is None:
            return
        if self._playing and self.frames:
            worker.start(
                self.current_frame,
                self.preview.width(),
                self.preview.height(),
            )
        else:
            worker.stop_playback()

    def _content_seconds(self, frame):
        """True content time (seconds, 0-based) of a frame, taken from the
        index's real timestamps.  Broadcast/field-coded files are NOT a
        uniform 1/fps apart, so frame/fps can drift several seconds over a
        long file - the index knows the real time, so use it.  Falls back to
        frame/fps only if the index can't answer."""
        idx = getattr(self, "index", None)
        if idx is not None:
            try:
                secs = idx.seconds_of(int(frame))
                if secs is not None:
                    return float(secs)
            except Exception:
                pass
        return int(frame) / (self.fps or 25.0)

    def _update_audio(self):
        """Keep audio playback aligned with the play state.  Audio plays only
        during real-time playback - not while frame-stepping or jumping."""
        audio = getattr(self, "audio", None)
        if audio is None or not audio.available:
            return

        want = bool(self._playing and self.frames and audio.volume() > 0)

        if want and not self._audio_active:
            audio.play_from(
                self._content_seconds(self.current_frame)
                + self._audio_offset
            )
            self._audio_active = True
        elif not want and self._audio_active:
            audio.pause()
            self._audio_active = False

    def _resync_audio(self):
        """Re-seek audio to the current frame during playback (after a seek),
        so it carries on from the new position instead of where it was."""
        audio = getattr(self, "audio", None)
        if audio is None or not audio.available or not self._audio_active:
            return
        audio.play_from(
            self._content_seconds(self.current_frame)
            + self._audio_offset
        )

    def _save_volume(self, value):
        """Persist the playback volume (0-100) to the config."""
        try:
            from config.loader import save_config
            self.config.setdefault("settings", {})["volume"] = int(value)
            save_config(self.config)
        except Exception:
            pass

    def export_video(self):

        if not self.frames or self.index is None:
            QMessageBox.information(
                self,
                self.tr("Export"),
                self.tr("Open a video first."),
            )
            return

        # Fresh export attempt: allow the QSF-and-retry fallback to be offered
        # again if needed.
        self._export_already_fixed = False

        keep_ranges = list(self.selection.ranges)

        if not keep_ranges:
            QMessageBox.information(
                self,
                self.tr("Export"),
                self.tr("No segments marked to keep. Mark at least one "
                "green segment before exporting."),
            )
            return

        # Profile-based Save Video dialog: pick a profile and an output file in
        # one click-and-save step (replaces the old format dropdown + separate
        # file picker).  After a QSF reload we default to the format and
        # destination the user chose for the original attempt.
        from ui.save_video_dialog import SaveVideoDialog

        pending = self._pending_export
        default_container = pending[1] if pending else "match"
        source_ext = (
            os.path.splitext(self.original_source or self.current_filename)[1]
            or ".ts"
        )

        if pending and pending[0]:
            suggested = pending[0]
        else:
            export_dir = self._start_dir("export")
            if not export_dir:
                export_dir = os.path.dirname(
                    self.original_source or self.current_filename
                )
            ext = {"mkv": ".mkv", "mp4": ".mp4"}.get(default_container, source_ext)
            # The Save Video dialog de-duplicates per the chosen profile's
            # extension, so pass a plain suggestion here.
            suggested = os.path.join(
                export_dir, "%s%s" % (self._original_base(), ext)
            )

        dialog = SaveVideoDialog(
            self.config, suggested, source_ext, self,
            default_container=default_container,
            sample_source=getattr(self, "current_filename", "") or "",
        )
        if dialog.exec() != QDialog.Accepted:
            return    # cancelled

        out_path = dialog.result_path()
        profile = dialog.result_profile()
        out_format = profile.container

        # MKV without mkvmerge still works and stays lossless - the audio just
        # lands in a less-portable wrapper (fine for Plex/Jellyfin).  Warn so
        # the choice is informed.
        if out_format == "mkv" and not self._mkvmerge_available():
            reply = QMessageBox.question(
                self,
                self.tr("mkvmerge not found"),
                self.tr("mkvmerge (mkvtoolnix) isn't installed or set in Settings.\n\n"
                "MKV export still works and stays lossless, but the audio is "
                "stored in a less-portable wrapper rather than native AAC.  It "
                "plays in Plex/Jellyfin and other ffmpeg-based players.\n\n"
                "Installing mkvtoolnix - or pointing Settings > Paths at an "
                "mkvmerge - gives the portable, native-AAC result.\n\n"
                "Export to MKV anyway?"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # Consume the pending-export defaults once used.
        self._pending_export = None

        self._remember_dir("export", out_path)

        self._run_export(
            out_path,
            keep_ranges,
            out_format,
            audio_mode=profile.audio,
            audio_bitrate=profile.audio_bitrate,
            aspect=profile.aspect,
            crop_mode=getattr(profile, "crop_mode", "none"),
            crop=getattr(profile, "crop", (0, 0, 0, 0)),
            video_mode=getattr(profile, "video", "copy"),
        )

    def open_profile_manager(self):
        """Open the output-profile manager (Tools -> Manage Profiles)."""
        from ui.profile_manager_dialog import ProfileManagerDialog
        sample = getattr(self, "current_filename", "") or ""
        ProfileManagerDialog(self.config, self, sample_source=sample).exec()

    def open_batch_manager(self):
        """Open (or re-show) the Batch Manager window, non-modally, so a batch
        can run in the background while you keep working."""
        from ui.batch_manager import BatchManagerDialog
        if self._batch_dialog is not None:
            self._batch_dialog.raise_()
            self._batch_dialog.activateWindow()
            return
        dlg = BatchManagerDialog(self, self.batch_controller)
        self._batch_dialog = dlg

        def _forget(_obj=None):
            self._batch_dialog = None
        dlg.destroyed.connect(_forget)
        dlg.show()

    def queue_to_batch(self):
        """Save the current edit as a project and add it to the batch queue
        (VideoReDo's Ctrl+B 'Queue To Batch').

        The project is written into a queue staging folder under the config
        directory and references the file you're actually working from - the
        Quick-Stream-Fixed /tmp copy when you've repaired it, otherwise the
        original recording.  That way the batch cuts the exact stream your cut
        points were made against (a QSF rebases the timeline to zero, so cuts
        made on a repaired file only line up against that same repaired file).

        Note a QSF working copy lives in /tmp for the session only, so queue and
        run the batch in the same session - or use Quick Stream Fix's 'save a
        copy' and work from that durable file - if you need it to survive a
        restart.  The exported file is still named after the original recording
        (the batch strips the ' - QSF' decoration from the working copy's name).
        """
        if not self.frames or self.index is None:
            QMessageBox.information(
                self, self.tr("Queue to Batch"), self.tr("Open a video first.")
            )
            return

        keep_ranges = list(self.selection.ranges)
        if not keep_ranges:
            QMessageBox.information(
                self,
                self.tr("Queue to Batch"),
                self.tr("No segments marked to keep. Mark at least one green segment "
                "before queueing."),
            )
            return

        from project.vprj import save_vprj
        from config.loader import CONFIG_DIR

        queue_dir = os.path.join(str(CONFIG_DIR), "queue")
        try:
            os.makedirs(queue_dir, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self, self.tr("Queue to Batch"),
                self.tr("Couldn't create the batch queue folder:\n\n%s") % exc,
            )
            return

        # Reference the file we're working from: the QSF'd /tmp copy when one is
        # loaded, else the original recording.  The cut points are in this file's
        # timeline, so the batch must cut THIS file - not the raw original, whose
        # timeline a QSF would have shifted.  (The queue .vprj is still named
        # after the original recording for tidiness.)
        source = self.current_filename or self.original_source
        base = self._original_base()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        vprj_path = os.path.join(queue_dir, f"{base} - {stamp}.vprj")

        try:
            save_vprj(
                vprj_path,
                keep_ranges,
                self.scenes.markers,
                source,
                self.index,
            )
        except Exception as exc:
            QMessageBox.warning(
                self, self.tr("Queue to Batch"),
                self.tr("The project couldn't be saved for batching:\n\n%s") % exc,
            )
            return

        # The controller owns the queue and persists it; if a batch is already
        # running, this job is appended live and gets picked up.
        self.batch_controller.add_job(vprj_path)
        profile_name = self.batch_controller.default_profile

        self.statusBar().showMessage(
            f"Queued to batch: {base} ({profile_name}). "
            "Open Tools \u2192 Batch Manager to run it.",
            8000,
        )

    def _qsf_confirm_if_already_fixed(self, path):
        """Return True to proceed with Quick Stream Fix, False to skip it.

        If the file has already been QSF-processed before (recorded in the
        registry) and the user hasn't disabled the warning in Settings, ask for
        confirmation first.  Best-effort: any problem reading the registry just
        lets the repair proceed.
        """
        if self.config.get("settings", {}).get("qsf_no_rewarn", False):
            return True

        try:
            from repair.qsf_registry import is_qsfd
            if not is_qsfd(path):
                return True
        except Exception:
            return True

        reply = QMessageBox.question(
            self,
            self.tr("Already Quick Stream Fixed"),
            self.tr("This file appears to have already been processed by Quick Stream "
            "Fix.\n\nRun Quick Stream Fix on it again anyway?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _record_qsf(self, source_path, output_path):
        """Record both the source and the produced output as QSF-processed, so
        re-opening either is recognised later.  Best-effort; never raises."""
        try:
            from repair.qsf_registry import mark_qsfd
            if source_path:
                mark_qsfd(source_path)
            if output_path:
                mark_qsfd(output_path)
        except Exception:
            pass

    def quick_stream_fix(self):
        """Manual Quick Stream Fix (Tools -> Quick Stream Fix).

        Offers two modes:
          - Repair & reload: remux the current file to temporary storage and
            reload it now, carrying the current scene markers across (remapped
            onto the repaired file).  This matches the auto-on-open and
            on-save-failure behaviour and is what you want when editing an
            imported project.
          - Repair & save a copy: write a permanently-fixed copy to a location
            you choose, leaving what's currently open untouched.
        """
        if not self.current_filename or self.index is None:
            QMessageBox.information(
                self,
                self.tr("Quick Stream Fix"),
                self.tr("Open a video first."),
            )
            return

        # Ask up front if this file has already been Quick Stream Fixed, so a
        # "no" costs no extra clicks (we don't even show the mode chooser).
        if not self._qsf_confirm_if_already_fixed(self.current_filename):
            return

        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Quick Stream Fix"))
        box.setIcon(QMessageBox.Question)
        box.setText(self.tr("How would you like to run Quick Stream Fix?"))
        box.setInformativeText(
            self.tr("Repair and reload: repair to temporary storage and reload it now, "
            "carrying your current scene markers across (recommended when "
            "editing).\n\n"
            "Repair and save a copy: write a permanently-fixed copy to a "
            "location you choose, without changing what's currently open.")
        )

        reload_btn = box.addButton(self.tr("Repair and reload"), QMessageBox.AcceptRole)
        save_btn = box.addButton(self.tr("Repair and save a copy…"), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(reload_btn)

        box.exec()
        clicked = box.clickedButton()

        if clicked is reload_btn:
            self._qsf_reload_in_place(check=False)
        elif clicked is save_btn:
            self._qsf_save_copy(check=False)
        # Cancel: do nothing.

    def _qsf_reload_in_place(self, check=True):
        """Repair the current file to /tmp, reload it, and remap the current
        scene markers (kept ranges AND navigation markers) onto the repaired
        copy - the same robust remap the on-save-failure path uses.

        Quick Stream Fix renumbers timestamps and may drop a few corrupt frames
        (usually clustered at the very start).  When the frame count drops we
        subtract that constant delta - exact for start-clustered drops; when
        nothing was dropped we fall back to a time-based remap.  Either way the
        user is asked to eyeball the markers before saving.
        """
        import tempfile

        source = self.current_filename

        # Skip the repair (with confirmation) if this file is already known to
        # have been Quick Stream Fixed.  The manual chooser checks up front, so
        # it passes check=False to avoid asking twice; the import auto-path uses
        # the default and checks here.
        if check and not self._qsf_confirm_if_already_fixed(source):
            return

        # Capture the current markers two ways (frames + times) so we can remap
        # robustly once the repaired index is ready.
        keep_frames = list(self.selection.ranges)
        keep_times = [
            (self.index.seconds_of(a), self.index.seconds_of(b))
            for (a, b) in keep_frames
        ]
        nav_frames = list(self.scenes.markers)
        nav_times = [self.index.seconds_of(m) for m in nav_frames]
        old_frame_count = self.index.frame_count

        ext = os.path.splitext(source)[1] or ".ts"
        tmp_path = self._qsf_temp_path(ext)

        progress = QProgressDialog(
            self.tr("Repairing stream (remuxing)…"),
            self.tr("Cancel"),
            0,
            100,
            self,
        )
        progress.setWindowTitle(self.tr("Quick Stream Fix"))
        progress.setMinimumDuration(0)
        progress.setValue(0)

        worker = StreamFixWorker(source, tmp_path, self)
        self._streamfix_worker = worker

        worker.progress.connect(progress.setValue)

        def _done(path):
            progress.close()
            self._streamfix_worker = None
            self._record_qsf(source, path)
            self.statusBar().showMessage(self.tr("Re-indexing repaired stream…"))

            def _after_load(new_index):
                delta = old_frame_count - new_index.frame_count
                last = new_index.frame_count - 1

                def remap_frame(orig_frame, orig_time):
                    if delta > 0:
                        return max(0, min(last, orig_frame - delta))
                    return new_index.index_of_seconds(orig_time)

                # Remap kept ranges.
                remapped_ranges = []
                for (a, b), (ta, tb) in zip(keep_frames, keep_times):
                    fa = remap_frame(a, ta)
                    fb = remap_frame(b, tb)
                    if fb < fa:
                        fa, fb = fb, fa
                    remapped_ranges.append((fa, fb))

                # Remap navigation markers.
                remapped_markers = sorted({
                    remap_frame(m, t)
                    for m, t in zip(nav_frames, nav_times)
                })

                self.selection.ranges = remapped_ranges
                self.scenes.markers = remapped_markers
                self._refresh_scenes_from_selection()

                # Land the cursor on the first kept scene, exactly as the
                # pre-QSF import does (line in import _apply).  Without this the
                # reload leaves the cursor at frame 0, so a vprj-imported file
                # that was QSF'd-and-reloaded didn't jump to the first scene
                # the way a plain import does.
                if remapped_ranges:
                    self.goto_frame(remapped_ranges[0][0])

                if remapped_ranges or remapped_markers:
                    self.statusBar().showMessage(
                        self.tr("Stream repaired and reloaded - check your scene "
                        "markers.")
                    )
                    QMessageBox.information(
                        self,
                        self.tr("Stream repaired"),
                        self.tr("The stream has been repaired and reloaded.\n\n"
                        "Your scene markers have been carried over, but the "
                        "repair can shift them slightly. Please check each "
                        "scene (double-click a scene to jump to its start) and "
                        "adjust if needed."),
                    )
                else:
                    self.statusBar().showMessage(
                        self.tr("Stream repaired and reloaded.")
                    )

            self._pending_after_load = _after_load
            self._load_file(path)

        def _fail(message):
            progress.close()
            self._streamfix_worker = None
            QMessageBox.warning(
                self,
                self.tr("Quick Stream Fix failed"),
                message,
            )

        worker.finished_ok.connect(_done)
        worker.failed.connect(_fail)
        progress.canceled.connect(worker.cancel)

        worker.start()

    def _qsf_save_copy(self, check=True):
        """Repair the current file to a location the user chooses, leaving the
        currently-open file untouched (the original manual-QSF behaviour, for
        making a permanently-fixed copy)."""
        if check and not self._qsf_confirm_if_already_fixed(self.current_filename):
            return

        ext = os.path.splitext(self.current_filename)[1] or ".ts"

        save_dir = os.path.dirname(
            self.original_source or self.current_filename
        )

        suggested = self._dedup_path(
            save_dir,
            f"{self._original_base()} - QSF{ext}",
        )

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Quick Stream Fix - Save As"),
            suggested,
            VIDEO_SAVE_FILTER,
        )

        if not out_path:
            return

        progress = QProgressDialog(
            self.tr("Repairing stream (remuxing)…"),
            self.tr("Cancel"),
            0,
            100,
            self,
        )
        progress.setWindowTitle(self.tr("Quick Stream Fix"))
        progress.setMinimumDuration(0)

        worker = StreamFixWorker(
            self.current_filename,
            out_path,
            self,
        )

        self._streamfix_worker = worker

        worker.progress.connect(progress.setValue)

        def _done(path):
            progress.close()
            self._record_qsf(self.current_filename, path)
            QMessageBox.information(
                self,
                self.tr("Quick Stream Fix complete"),
                self.tr("Saved:\n%s") % path,
            )
            self._streamfix_worker = None

        def _fail(message):
            progress.close()
            QMessageBox.warning(
                self,
                self.tr("Quick Stream Fix failed"),
                message,
            )
            self._streamfix_worker = None

        worker.finished_ok.connect(_done)
        worker.failed.connect(_fail)
        progress.canceled.connect(worker.cancel)

        worker.start()

    def _ask_export_options(self, default_format=None):
        """Modal dialog for output format.  MKV always gets chapters.

        If default_format is given, that option starts selected.

        Returns out_format or None if cancelled.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Export Options"))

        layout = QVBoxLayout(dialog)

        layout.addWidget(
            QLabel(self.tr("Output format:"))
        )

        combo = QComboBox()
        combo.addItem(self.tr("Match source"), "match")
        combo.addItem(self.tr("MKV (Matroska, with chapters)"), "mkv")
        combo.addItem(self.tr("MP4 (no subtitles)"), "mp4")
        if default_format is not None:
            i = combo.findData(default_format)
            if i >= 0:
                combo.setCurrentIndex(i)
        layout.addWidget(combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok
            | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        # Make OK the default and give it focus, so Enter accepts the dialog
        # instead of dropping into the format combo and opening its list.
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        ok_btn.setDefault(True)
        ok_btn.setAutoDefault(True)
        ok_btn.setFocus()

        if dialog.exec() != QDialog.Accepted:
            return None

        chosen = combo.currentData()
        if chosen == "mkv" and not self._mkvmerge_available():
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                self.tr("mkvmerge not found"),
                self.tr("mkvmerge (mkvtoolnix) isn't installed or set in Settings.\n\n"
                "MKV export will still work and stays lossless, but the audio "
                "is stored in a less-portable wrapper that some video players "
                "may reject, rather than native AAC.\n\n"
                "Installing mkvtoolnix - or pointing Settings > Paths at an "
                "mkvmerge - gives the portable, widely-compatible result.\n\n"
                "Export to MKV anyway?"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return None
        return chosen

    def _mkvmerge_available(self):
        """True if mkvmerge can be found - the path set in Settings, or on PATH.
        """
        import shutil
        configured = (
            self.config.get("paths", {}).get("mkvmerge_binary", "") or ""
        ).strip()
        if (configured and os.path.isfile(configured)
                and os.access(configured, os.X_OK)):
            return True
        return shutil.which("mkvmerge") is not None

    def _run_export(
            self,
            out_path,
            keep_ranges,
            out_format,
            audio_mode="copy",
            audio_bitrate=0,
            aspect="source",
            crop_mode="none",
            crop=(0, 0, 0, 0),
            video_mode="copy",
    ):
        from ui.export_dialogs import (
            ExportProgressDialog,
            ExportCompleteDialog,
        )

        base = os.path.basename(out_path)

        progress = ExportProgressDialog(base, self)

        worker = ExportWorker(
            self.current_filename,
            out_path,
            keep_ranges,
            self.index,
            out_format,
            self,
            audio_mode=audio_mode,
            audio_bitrate=audio_bitrate,
            aspect=aspect,
            crop_mode=crop_mode,
            crop=crop,
            video_mode=video_mode,
        )

        # Keep a reference so the thread isn't garbage-collected.
        self._export_worker = worker

        def _on_progress(info):
            progress.update_progress(info)

        def _on_done(stats):
            progress.close()
            log.info(
                "Export complete: %s",
                stats.get("out_path", out_path)
                if isinstance(stats, dict) else out_path,
            )
            # Write a project file (VRD-style) and clear the unsaved flag - a
            # successful export is our save point.
            self._autosave_project_on_export()
            self._mark_saved()
            ExportCompleteDialog(stats, self).exec()
            self._export_worker = None

        def _on_fail(message):
            progress.close()
            log.warning("Export failed: %s", message)
            self._export_worker = None

            # Special case: the export produced no readable video.  This
            # happens on some awkward broadcast streams that Quick Stream Fix
            # can repair.  Offer to repair and reload so the user can check
            # their scene markers and then save.
            if "no readable video" in message.lower() and not getattr(
                self, "_export_already_fixed", False
            ):
                choice = QMessageBox.question(
                    self,
                    self.tr("Export produced no video"),
                    self.tr("The export contained no usable video. This can happen "
                    "with some broadcast recordings whose streams need "
                    "repairing first.\n\n"
                    "Would you like to run Quick Stream Fix on the source? "
                    "The repaired file will be reloaded with your scene "
                    "markers so you can check them before saving."),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if choice == QMessageBox.Yes:
                    self._qsf_and_reload(
                        out_path, keep_ranges, out_format
                    )
                    return

            QMessageBox.warning(
                self,
                self.tr("Export failed"),
                message,
            )

        def _on_cancelled():
            progress.close()
            log.info("Export aborted by user; partial output cleaned up.")
            self._export_worker = None

        worker.progress.connect(_on_progress)
        worker.finished_ok.connect(_on_done)
        worker.failed.connect(_on_fail)
        worker.cancelled.connect(_on_cancelled)

        progress.set_abort_callback(worker.cancel)

        worker.start()
        progress.show()

    def _refresh_scenes_from_selection(self):
        """Push the current selection.ranges out to all the scene UI widgets.

        Used after programmatically replacing the selection (e.g. when scene
        boundaries are remapped onto a repaired file), so the scene list,
        scene bar, thumbnails and info panel all reflect the new ranges.
        """
        self.selection.clear_pending()
        self.scene_list.refresh()
        self.update_timecode()
        self.scene_bar.update()
        self.draw_thumbnails()
        self.info_panel.update_info()

    def _qsf_and_reload(self, out_path, keep_ranges, out_format):
        """Quick Stream Fix the current source into /tmp, then reload the
        repaired copy with the scene markers remapped onto it.

        Quick Stream Fix renumbers the stream's timestamps (and may drop a few
        bad frames), so the original markers' frame numbers would point at
        slightly different content on the repaired file.  We capture each
        boundary as a time now, convert those times back to frame numbers on
        the repaired index after reloading, and present them for the user to
        check before saving - we do not auto-export, since a repair can shift
        boundaries slightly and the user should verify them.
        """
        import tempfile

        # Capture each scene boundary three ways so we can remap robustly onto
        # the repaired file:
        #   - as a time (seconds), for a time-based remap, and
        #   - as the original frame number, for a frame-count-delta remap.
        # We also note the original total frame count.  Quick Stream Fix tends
        # to drop a few corrupt frames clustered at the very start; when that's
        # the case the boundaries all shift by a constant (the dropped count),
        # so subtracting that delta lands them exactly.  We still let the user
        # verify before saving, so this is a best-effort starting point.
        keep_times = []
        keep_frames = list(keep_ranges)
        old_frame_count = self.index.frame_count
        for a, b in keep_ranges:
            keep_times.append((
                self.index.seconds_of(a),
                self.index.seconds_of(b),
            ))

        source = self.current_filename
        ext = os.path.splitext(source)[1] or ".ts"
        tmp_path = self._qsf_temp_path(ext)

        progress = QProgressDialog(
            self.tr("Repairing the stream (Quick Stream Fix)…"),
            None,  # no cancel button - this is a short, automatic step
            0,
            100,
            self,
        )
        progress.setWindowTitle(self.tr("Repairing"))
        progress.setMinimumDuration(0)
        progress.setValue(0)

        worker = StreamFixWorker(source, tmp_path, self)
        self._export_fix_worker = worker

        worker.progress.connect(progress.setValue)

        def _fixed(path):
            progress.close()
            self._export_fix_worker = None
            self._record_qsf(source, path)

            self.statusBar().showMessage(self.tr("Re-indexing repaired stream…"))

            # Fully load the repaired file so the whole app (preview, fetcher,
            # index, selection) is consistent.  We remap the scene boundaries
            # onto the repaired index as a best-effort starting point, then
            # STOP and ask the user to verify the markers before saving -
            # rather than silently exporting a guess.  A repair can drop a few
            # frames, so the remapped markers may be slightly off; letting the
            # user eyeball them (and nudge if needed) is safer than auto-saving.

            def _after_load(new_index):
                # Remap each scene boundary onto the repaired file's index.
                #
                # Quick Stream Fix usually drops a few corrupt frames at the
                # very start, shifting every later frame by a constant amount.
                # When the frame count dropped, subtract that delta from the
                # original frame numbers - this lands the markers exactly when
                # the drops are start-clustered (the common case).  When no
                # frames were dropped, fall back to a time-based remap.
                delta = old_frame_count - new_index.frame_count
                last = new_index.frame_count - 1

                remapped = []
                if delta > 0:
                    for (a, b) in keep_frames:
                        fa = max(0, min(last, a - delta))
                        fb = max(0, min(last, b - delta))
                        if fb < fa:
                            fa, fb = fb, fa
                        remapped.append((fa, fb))
                else:
                    for (t_start, t_end) in keep_times:
                        fa = new_index.index_of_seconds(t_start)
                        fb = new_index.index_of_seconds(t_end)
                        if fb < fa:
                            fa, fb = fb, fa
                        remapped.append((fa, fb))

                # Replace the selection with the remapped ranges so the UI,
                # scene list and any later save all agree.
                self.selection.ranges = remapped
                self._refresh_scenes_from_selection()

                # Remember the intended output so the next Save can default to
                # the same destination/format the user already chose.
                self._pending_export = (out_path, out_format)

                self.statusBar().showMessage(
                    self.tr("Stream repaired and reloaded - check your scene markers, "
                    "then Save Video.")
                )

                QMessageBox.information(
                    self,
                    self.tr("Stream repaired"),
                    self.tr("The stream has been repaired and reloaded.\n\n"
                    "Your scene markers have been carried over, but the repair "
                    "can shift them slightly. Please check each scene "
                    "(double-click a scene to jump to its start) and adjust if "
                    "needed, then click Save Video when you're happy."),
                )

            self._pending_after_load = _after_load
            self._load_file(path)

        def _fix_failed(message):
            progress.close()
            self._export_fix_worker = None
            QMessageBox.warning(
                self,
                self.tr("Repair failed"),
                self.tr("The stream could not be repaired automatically:\n\n%s") % message,
            )

        worker.finished_ok.connect(_fixed)
        worker.failed.connect(_fix_failed)
        worker.start()

    def _estimate_frame_count(
            self,
            filename,
    ):
        """Rough total-frame estimate from container duration, for the bar."""
        try:
            container = av.open(filename)
            stream = container.streams.video[0]

            fps = (
                float(stream.average_rate)
                if stream.average_rate
                else 25.0
            )

            seconds = 0.0

            if stream.duration is not None:
                seconds = float(
                    stream.duration * stream.time_base
                )
            elif container.duration is not None:
                seconds = container.duration / 1_000_000

            container.close()

            return max(
                1,
                int(seconds * fps),
            )

        except Exception:
            return 0

    def _on_index_progress(
            self,
            frames_done,
    ):
        if not self._estimated_frames:
            return

        pct = int(
            100 * frames_done / self._estimated_frames
        )

        #
        # Hold at 99% until the build actually finishes.
        #

        self.index_progress.setValue(
            min(99, pct)
        )

    def _on_index_ready(
            self,
            index,
    ):
        self.index = index
        self.fps = index.fps

        # Timecodes (cursor, scene times, durations, .vprj export) must use the
        # file's real rate, or files that aren't 25fps show wrong times.
        from utils.timecode import set_timecode_fps
        set_timecode_fps(index.fps)

        #
        # Keep playback timing in step with the source frame rate.
        #

        self.transport_timer.setInterval(
            int(1000 / self.fps)
        )

        self.fetcher = FrameFetcher(
            self.current_filename,
            index,
        )

        #
        # render_frame() reads display aspect ratio from this container to
        # correct anamorphic SD; keep it pointed at the fetcher's container.
        #

        self.container = self.fetcher.container

        #
        # The list-like view the rest of the UI reads from.
        #

        self.frames = FrameSequence(
            self.fetcher,
            index,
        )

        self.current_frame = 0

        self.index_builder = None

        #
        # Spin up the background scrub decoder (own fetcher, shared index).
        #

        self._start_scrub_worker()

        self.index_progress.hide()

        self.statusBar().clearMessage()

        #
        # Show the loaded file in the title bar.  This is also the clean
        # baseline for unsaved-change tracking (a project import re-snapshots
        # after it applies its cuts).
        #

        self._mark_saved()

        #
        # File size (MB) for the info panel's size estimates.
        #

        try:
            self.source_size_mb = (
                os.path.getsize(self.current_filename)
                / (1024 * 1024)
            )
        except OSError:
            self.source_size_mb = 0.0

        self.update_display()

        self.setFocus()

        # A video is now loaded - enable the playback/mark/action controls.
        self._set_controls_enabled(True)

        # Point the audio player at the loaded file (playback is started later,
        # only when the user actually plays).
        self._audio_active = False
        self.audio.set_source(self.current_filename)

        #
        # If a load was initiated programmatically (e.g. the QSF-and-retry
        # export reloading the repaired file), run its continuation now that
        # the file is fully loaded and consistent.
        #
        pending = getattr(self, "_pending_after_load", None)
        if pending is not None:
            self._pending_after_load = None
            pending(self.index)

    def _start_scrub_worker(self):

        self.scrub_thread = QThread(self)

        self.scrub_worker = ScrubWorker(
            self.current_filename,
            self.index,
        )

        self.scrub_worker.moveToThread(
            self.scrub_thread
        )

        self.scrub_thread.started.connect(
            self.scrub_worker.run
        )

        self.scrub_worker.frame_ready.connect(
            self._on_scrub_frame
        )

        self.scrub_thread.start()

        #
        # Playback decoder: sequential decode-ahead on its own thread + fetcher,
        # feeding a small frame queue the UI pulls from during playback.  This
        # keeps playback decode off the UI thread (so audio can't starve it)
        # while still showing every frame in order (unlike the latest-wins
        # scrub worker).
        #

        self.playback_thread = QThread(self)

        self.playback_worker = PlaybackWorker(
            self.current_filename,
            self.index,
        )

        self.playback_worker.moveToThread(
            self.playback_thread
        )

        self.playback_thread.started.connect(
            self.playback_worker.run
        )

        self.playback_thread.start()

        #
        # Thumbnail strip decoder (own thread + fetcher).  Keeps the ~9-frame
        # strip decode off the UI thread, which is the main cause of HD
        # navigation freezing.
        #

        self.thumb_thread = QThread(self)

        self.thumb_worker = ThumbnailWorker(
            self.current_filename,
            self.index,
        )

        self.thumb_worker.moveToThread(
            self.thumb_thread
        )

        self.thumb_thread.started.connect(
            self.thumb_worker.run
        )

        self.thumb_worker.thumb_ready.connect(
            self._on_thumb_ready
        )

        self.thumb_thread.start()

    def _on_thumb_ready(self, frame_index, width, image, letter=""):
        # Deliver a decoded thumbnail to the bar (on the UI thread).
        if not self.frames:
            return
        self.thumbnail_bar.set_thumb_image(frame_index, width, image, letter)

    def _on_scrub_frame(
            self,
            frame_index,
            image,
            letter="",
    ):
        #
        # Show whatever the worker decoded.  During a drag this "chases" the
        # cursor - on SD it keeps up frame-for-frame; on HD the preview flips
        # through frames as fast as they decode rather than only on pause.
        #

        if not self.frames:
            return

        # Only show it if it's still the frame we want (latest-wins guard).
        if frame_index != self.current_frame:
            # A newer navigation happened; ignore this stale delivery for the
            # preview, but it's still a valid decode so let it through only if
            # it matches.  Simplest: drop it.
            pass

        self._set_preview_frame_type(letter)
        self.preview.setPixmap(
            QPixmap.fromImage(image)
        )

        # Keep the centre (cursor) thumbnail in step with the preview on cold
        # jumps too, by reusing this decoded image (scaled down).  Guarantees
        # the green centre thumbnail matches the preview rather than going
        # blank while the async strip catches up.
        if frame_index == self.current_frame:
            self.thumbnail_bar.set_center_from_image(
                frame_index, image, letter
            )

    def _on_index_failed(
            self,
            message,
    ):
        self.index_builder = None

        self.index_progress.hide()

        self.statusBar().showMessage(
            f"Could not open video: {message}"
        )

    def _reset_media_state(self):
        """Clear all per-file state when opening a new (or no) video."""

        #
        # Cancel an in-flight index build.
        #

        if self.index_builder is not None:
            self.index_builder.cancel()
            self.index_builder.wait()
            self.index_builder = None

        #
        # Stop the background scrub decoder and its thread.
        #

        if self.scrub_worker is not None:
            self.scrub_worker.stop()

        if self.scrub_thread is not None:
            self.scrub_thread.quit()
            self.scrub_thread.wait()
            self.scrub_thread = None

        self.scrub_worker = None

        if self.playback_worker is not None:
            self.playback_worker.stop()

        if self.playback_thread is not None:
            self.playback_thread.quit()
            self.playback_thread.wait()
            self.playback_thread = None

        self.playback_worker = None

        if self.thumb_worker is not None:
            self.thumb_worker.stop()

        if self.thumb_thread is not None:
            self.thumb_thread.quit()
            self.thumb_thread.wait()
            self.thumb_thread = None

        self.thumb_worker = None

        #
        # Release the previous fetcher's decoder/container.
        #

        if self.fetcher is not None:
            self.fetcher.close()
            self.fetcher = None

        self.container = None

        self.index = None

        self.frames = []
        self.current_frame = 0
        self.selected_scene = None

        self._saved_ranges = []

        self.setWindowTitle(
            VERSION_STRING
        )

        #
        # Stop any transport.
        #

        self.playing = False
        self.transport_direction = 0
        self.transport_started = False
        self.transport_timer.stop()

        #
        # Clear cuts, pending IN/OUT, undo/redo and scene markers.
        #

        self.selection = SelectionManager()
        self.scenes = SceneManager()

        #
        # Thumbnail pixmaps are keyed by frame index, which means something
        # different in a new file - drop them.
        #

        if hasattr(self, "thumbnail_bar"):
            self.thumbnail_bar.clear_cache()

    def update_timecode(self):

        self.statusBar().clearMessage()

    def transport_step(self):

        if not self.isActiveWindow():
            return

        #
        # Playback
        #

        if self.playing:

            #
            # While the user is dragging the timeline, let the scrub drive the
            # playhead.  Don't advance from the (now stale) wall-clock anchor,
            # or playback fights the drag and snaps back to the old position on
            # release.  Keep the anchor cleared so playback re-anchors at the
            # released frame once the drag ends.
            #
            if self._scrubbing:
                self._play_anchor_time = None
                return

            last = len(self.frames) - 1

            #
            # Play on a smooth wall clock (this is what made playback smooth
            # before audio existed) and let the background worker skip ahead if
            # it falls behind.  The picture is correct real-time; audio is
            # aligned to it by seeking the SOUND to the right spot (see
            # _update_audio / _audio_offset), not by shifting the picture.
            #

            if self._play_anchor_time is None:
                self._play_anchor_time = time.perf_counter()
                self._play_anchor_frame = self.current_frame

            elapsed = (
                time.perf_counter()
                - self._play_anchor_time
            )

            target = (
                self._play_anchor_frame
                + int(elapsed * self.fps)
            )

            snapped = False

            if target >= last:
                self.current_frame = last

                self.playing = False
                self._play_anchor_time = None

                self.transport_timer.stop()

                self.transport_controls.update_buttons()

                self.update_display()

                return

            #
            # Advance to the target frame (skips frames if we fell behind, so
            # playback tracks real time rather than drifting slow).
            #

            if target <= self.current_frame:
                target = self.current_frame + 1

            self.current_frame = target

            #
            # Display the frame the background worker decoded for us - the UI
            # thread never decodes during playback.  On a discontinuity (drift
            # correction) tell the worker to jump.  If nothing is ready yet,
            # hold the last frame rather than block.
            #
            worker = self.playback_worker
            if worker is not None:
                if snapped:
                    worker.seek(target)

                data = worker.take(target)
                if data is not None:
                    self._set_preview_frame_type(
                        data[2] if len(data) > 2 else ""
                    )
                    self.preview.setPixmap(
                        QPixmap.fromImage(data[1])
                    )

                self.update_timecode()
                self.transport_panel.update_transport()
                self.scene_bar.update()
            else:
                self.update_preview_only()

            return

        #
        # Held arrow-key transport
        #

        if not self.transport_direction:
            return

        #
        # first repeat delay
        #

        if not self.transport_started:
            self.transport_started = True

            self.transport_timer.setInterval(
                33
            )

            return

        self.current_frame = max(
            0,
            min(
                len(self.frames) - 1,
                self.current_frame
                +
                self.transport_direction
            )
        )

        self.step_display()

    def step_display(self):
        """Display update for frame-stepping: preview AND thumbnail strip both
        synchronous, so the strip the user is watching stays exactly in sync.

        Stepping is sequential, so both decodes hit the fetcher's cheap
        fast-path; doing them inline keeps everything in lockstep without the
        async lag/frame-dropping that made the strip unreliable.
        """
        if not self.frames:
            return

        frame = self.frames[self.current_frame]
        if frame is not None:
            self._set_preview_frame_type(
                frame_label(frame, self.fetcher, self.current_frame)
            )
            self.preview.setPixmap(
                render_frame(
                    frame,
                    self.container,
                    self.preview.width(),
                    self.preview.height(),
                )
            )

        self.thumbnail_bar.refresh_sync()

        self.update_timecode()
        self.transport_panel.update_transport()
        self.transport_controls.update_buttons()
        self.scene_bar.update()

    def update_preview_only(self):

        if not self.frames:
            return

        frame = (
            self.frames[
                self.current_frame
            ]
        )

        if frame is not None:
            self._set_preview_frame_type(
                frame_label(frame, self.fetcher, self.current_frame)
            )
            self.preview.setPixmap(

                render_frame(
                    frame,
                    self.container,
                    self.preview.width(),
                    self.preview.height(),
                    fast=True,
                )

            )

        self.update_timecode()

        self.transport_panel.update_transport()

        self.scene_bar.update()

    def update_display(self):

        if not self.frames:
            return

        #
        # Preview frame.  A sequential step (or a cached frame) is cheap, so
        # decode it synchronously for instant, judder-free feedback.  Only a
        # cold jump (an uncached, non-sequential target - ~200ms on HD) is
        # handed to the background worker so the UI doesn't freeze.
        #

        cheap = (
            self.fetcher is not None
            and self.fetcher.can_serve_cheaply(self.current_frame)
        )

        if cheap:
            frame = self.frames[self.current_frame]
            if frame is not None:
                self._set_preview_frame_type(
                frame_label(frame, self.fetcher, self.current_frame)
            )
                self.preview.setPixmap(
                    render_frame(
                        frame,
                        self.container,
                        self.preview.width(),
                        self.preview.height(),
                    )
                )
                # Set the centre thumbnail from the same frame so it always
                # matches the preview (the async strip fill can't guarantee
                # this on its own).
                self.thumbnail_bar.set_center_from_frame(
                    self.current_frame, frame, self.container
                )
        elif self.scrub_worker is not None:
            self.scrub_worker.request(
                self.current_frame,
                self.preview.width(),
                self.preview.height(),
            )

        self.draw_thumbnails()

        self.update_timecode()

        self.scene_bar.update()

        self.transport_panel.update_transport()

        self.transport_controls.update_buttons()

        self.info_panel.update_info()

        if hasattr(
                self,
                "scene_list",
        ):

            try:
                self.scene_list.refresh()

            except Exception as e:
                print(
                    e
                )

    def goto_frame(self, frame_index):
        """Jump the cursor to a specific frame and refresh the display.

        Used by clickable thumbnails (and any other 'jump straight here'
        navigation).  Stops playback first so the view settles on the chosen
        frame rather than continuing from it.
        """
        if not self.frames:
            return

        last = len(self.frames) - 1
        self.current_frame = max(0, min(frame_index, last))

        # Settle on the chosen frame rather than playing on from it.
        self.playing = False
        self.transport_direction = 0
        self.transport_started = False
        self.transport_timer.stop()

        self.update_display()
        self.setFocus()

    def scrub_to(
            self,
            frame_index,
    ):
        """Move to a frame during a scrub: cheap UI now, preview decoded async.

        No thumbnail decode and no UI-thread frame decode - the preview is
        served by the background worker so dragging stays responsive on HD.
        """
        if not self.frames:
            return

        self._scrubbing = True
        self.current_frame = frame_index

        #
        # Instant, decode-free feedback.
        #

        self.update_timecode()
        self.scene_bar.update()
        self.transport_panel.update_transport()

        #
        # Ask the worker for the preview frame (latest-wins, off the UI thread).
        #

        if self.scrub_worker is not None:
            self.scrub_worker.request(
                frame_index,
                self.preview.width(),
                self.preview.height(),
            )

    def scrub_finish(self):
        """End of a scrub: lock in the exact frame and refresh everything."""
        if not self.frames:
            return

        self._scrubbing = False

        #
        # If the seek happened mid-playback, re-anchor the video clock at the
        # new position and re-seek the audio so both carry on from here -
        # otherwise the audio keeps playing from where it was.
        #
        if self.playing:
            self._play_anchor_time = None
            self._resync_audio()
            if self.playback_worker is not None:
                self.playback_worker.seek(self.current_frame)

        self.update_display()

    def jump_frames(
            self,
            frames,
    ):

        #
        # fps is a float from the source, so callers like fps*30 produce a
        # float; array indices must be ints, so coerce here.
        #

        frames = int(frames)

        if not self.frames:
            return

        #
        # Stop playback
        #

        self.playing = False

        self.transport_timer.stop()

        self.transport_direction = 0

        self.transport_started = False

        self.current_frame = max(
            0,
            min(
                len(self.frames) - 1,
                self.current_frame + frames
            )
        )

        self.update_display()

    def commit_selection(self):
        """Save the pending IN/OUT as a kept (green) range."""
        if self.selection.commit_range():
            self.scene_list.refresh()

            self.update_timecode()

            self.scene_bar.update()

            self.draw_thumbnails()

            self.info_panel.update_info()

    def add_unselected(self):
        """Invert the kept ranges: keep what's currently dropped, and vice versa.

        The gaps between the current green ranges (and the head/tail of the
        file) become the new keep-ranges.  With nothing yet marked, this keeps
        the whole file.
        """
        if not self.frames:
            return

        total = len(self.frames)

        ranges = sorted(self.selection.ranges)

        inverted = []
        cursor = 0

        for start, end in ranges:
            if start > cursor:
                inverted.append(
                    (cursor, start - 1)
                )
            cursor = max(cursor, end + 1)

        if cursor <= total - 1:
            inverted.append(
                (cursor, total - 1)
            )

        self.selection.push_undo_state()

        self.selection.ranges = inverted

        self.selection.clear_pending()

        self.scene_list.refresh()

        self.update_timecode()

        self.scene_bar.update()

        self.draw_thumbnails()

        self.info_panel.update_info()

    def clear_all_scenes(self):
        """Remove every saved scene in one undoable step (VRD's Clear All).

        Also clears any pending IN/OUT.  Does nothing - and so leaves the undo
        history alone - when there is nothing to clear.
        """
        if (
                not self.selection.ranges
                and self.selection.pending_in is None
                and self.selection.pending_out is None
        ):
            return

        self.selection.push_undo_state()

        self.selection.clear_all()

        self.scene_list.refresh()

        self.update_timecode()

        self.scene_bar.update()

        self.draw_thumbnails()

        self.info_panel.update_info()

    def goto_selection_start(self):
        """S: step to the nearest scene START to the left of the playhead.

        Pressing S repeatedly walks back through the scene starts one at a time
        and, like F5/F6, wraps past the first scene round to the last.  This is
        independent of which scene is highlighted in the list - it always works
        from where the playhead currently is.
        """
        if not self.frames or not self.selection.ranges:
            return

        starts = sorted(r[0] for r in self.selection.ranges)
        earlier = [s for s in starts if s < self.current_frame]
        target = earlier[-1] if earlier else starts[-1]   # wrap to last

        self.current_frame = max(0, min(target, len(self.frames) - 1))
        self.update_display()

    def goto_selection_end(self):
        """E: step to the nearest scene END to the right of the playhead.

        Pressing E repeatedly walks forward through the scene ends one at a time
        and wraps past the last scene round to the first.
        """
        if not self.frames or not self.selection.ranges:
            return

        ends = sorted(r[1] for r in self.selection.ranges)
        later = [e for e in ends if e > self.current_frame]
        target = later[0] if later else ends[0]           # wrap to first

        self.current_frame = max(0, min(target, len(self.frames) - 1))
        self.update_display()

    def mark_in(self):

        # Whether this becomes a new scene or replaces/edits an existing one is
        # decided at commit time by whether the IN..OUT span overlaps an
        # existing scene (see SelectionManager.commit_range), so we just record
        # the IN point here.
        self.selection.set_in(
            self.current_frame
        )

        self.update_timecode()

        self.scene_bar.update()

        self.transport_panel.update_transport()

        self.transport_controls.update_buttons()

    def mark_out(self):

        self.selection.set_out(
            self.current_frame
        )

        self.update_timecode()

        self.scene_bar.update()

        self.transport_panel.update_transport()

        self.transport_controls.update_buttons()

    def draw_thumbnails(self):
        self.thumbnail_bar.refresh()

    def goto_timecode(self):
        if not self.frames:
            return

        from ui.goto_dialog import GoToTimecodeDialog

        dialog = GoToTimecodeDialog(
            self.current_frame,
            len(self.frames) - 1,
            self,
        )

        accepted = (
            dialog.exec() == QDialog.Accepted
        )

        # Always pull focus back to the main window so the navigation keys
        # keep working whether the dialog was accepted or cancelled.
        self.setFocus()

        if (
                accepted
                and
                dialog.frame is not None
        ):
            self.current_frame = dialog.frame

            #
            # Stop any playback so the jump lands cleanly (mirrors what
            # double-clicking a scene does).
            #

            self.playing = False

            self.transport_direction = 0

            self.transport_started = False

            self.transport_timer.stop()

            self.transport_panel.update_transport()

            self.update_display()

    def scene_clicked(
            self,
            row,
            column,
    ):

        self.selected_scene = row

        # Move the playhead into the clicked scene so S/E act on it, and pull
        # focus back to the main window so the navigation keys work straight
        # away (without first having to click in the video).
        ranges = self.selection.ranges
        if self.frames and 0 <= row < len(ranges):
            start = ranges[row][0]
            self.current_frame = max(0, min(start, len(self.frames) - 1))
            self.update_display()

        self.update_timecode()

        self.info_panel.update_info()

        self.scene_bar.update()

        self.setFocus()

    def scene_double_clicked(
            self,
            row,
            column,
    ):

        self.selected_scene = row

        self.update_timecode()

        self.info_panel.update_info()

        ranges = (
            self.selection.ranges
        )

        if (
                row < 0
                or
                row >= len(ranges)
        ):
            return

        start, end = ranges[row]

        # Load this scene's range into the IN/OUT markers, mirroring VRD:
        # double-clicking a scene populates the IN/OUT boxes.  Because the
        # IN..OUT span then overlaps this scene, committing an edit adjusts
        # THIS scene rather than adding a new one (see commit_range).
        self.selection.set_in(start)
        self.selection.set_out(end)

        self.current_frame = start

        #
        # Reset transport state
        #

        self.playing = False

        self.transport_direction = 0

        self.transport_started = False

        self.transport_timer.stop()

        self.transport_panel.update_transport()

        self.transport_controls.update_buttons()

        self.scene_bar.update()

        self.update_display()

        # Pull focus back to the main window so S/E and the other navigation
        # keys work immediately after double-clicking, without needing to click
        # in the video first.
        self.setFocus()

    def _update_remove_button(self):
        """Enable the Remove button only while one or more scenes are selected."""
        if hasattr(self, "remove_scenes_btn"):
            has_selection = bool(
                self.scene_list.selectionModel().selectedRows()
            )
            self.remove_scenes_btn.setEnabled(has_selection)

    def delete_selected_scenes(self):
        """Remove every selected scene from the cut list in a single undo step.

        Falls back to the current row if the model reports no selection (e.g.
        the Delete key with a current-but-unselected row).
        """
        rows = sorted(
            idx.row()
            for idx in self.scene_list.selectionModel().selectedRows()
        )

        if not rows:
            current = self.scene_list.currentRow()
            if current >= 0:
                rows = [current]

        if not rows:
            return

        removed = self.selection.remove_ranges(rows)

        if not removed:
            return

        self.selected_scene = None
        self.scene_list.clearSelection()

        self.scene_list.refresh()
        self.scene_bar.update()
        self.update_timecode()
        self.info_panel.update_info()

    def keyPressEvent(
            self,
            event,
    ):

        if event.isAutoRepeat():
            return

        #
        # File / project shortcuts.  These are handled before the "no video
        # loaded" guard below so that Open Video / Import Project work from an
        # empty window; the ones that act on a loaded video are guarded.
        #

        if self.keys.match(event, "open_video"):
            self.open_video()
            return

        if self.keys.match(event, "open_project"):
            self.open_project()
            return

        if self.keys.match(event, "save_video"):
            if self.frames:
                self.export_video()
            return

        if self.keys.match(event, "save_project"):
            if self.frames:
                self.save_project()
            return

        if self.keys.match(event, "save_project_as"):
            if self.frames:
                self.save_project_as()
            return

        if self.keys.match(event, "queue_to_batch"):
            if self.frames:
                self.queue_to_batch()
            return

        if self.keys.match(event, "close_video"):
            if self.frames:
                self.close_video()
            return

        if not self.frames:
            return

        if self.keys.match(event, "goto_timecode"):
            self.goto_timecode()
            return

        if self.keys.match(event, "show_program_info"):
            self.show_program_info()
            return

        if self.keys.match(event, "detect_commercials"):
            self.detect_commercials()
            return

        if (
                self.playing
                and
                not self.keys.match(
                    event,
                    "play_pause",
                )
        ):
            self.playing = False

            self.transport_timer.stop()

        #
        # Stop transport when pressing
        # non-navigation keys
        #

        if event.key() not in (
                Qt.Key_Left,
                Qt.Key_Right,
        ):
            self.transport_timer.stop()

            self.transport_direction = 0

            self.transport_started = False

        delta = 0

        if self.keys.match(
                event,
                "play_pause",
        ):

            self.playing = (
                not
                self.playing
            )

            if self.playing:

                self._play_anchor_time = None

                self.transport_timer.setInterval(
                    round(1000 / self.fps)
                )

                self.transport_timer.start()

                self.transport_controls.update_buttons()

            else:

                self._play_anchor_time = None

                self.transport_timer.stop()

                self.transport_controls.update_buttons()

                #
                # Bring the strip / scene bar up to the paused frame.
                #

                self.update_display()

            return

        if self.keys.match(
                event,
                "scene_toggle",
        ):
            self.scenes.toggle(
                self.current_frame
            )

            self.update_display()

            return

        if self.keys.match(
                event,
                "scene_previous",
        ):
            self.current_frame = (

                self.scenes
                .previous(
                    self.current_frame
                )

            )

            self.update_display()

            return

        if self.keys.match(
                event,
                "scene_next",
        ):
            self.current_frame = (

                self.scenes
                .next(
                    self.current_frame
                )

            )

            self.update_display()

            return

        if self.keys.match(
                event,
                "undo",
        ):

            if self.selection.undo():
                self.scene_list.refresh()

                self.scene_bar.update()

                self.info_panel.update_info()

            return

        if self.keys.match(
                event,
                "redo",
        ):

            if self.selection.redo():
                self.scene_list.refresh()

                self.scene_bar.update()

                self.info_panel.update_info()

            return

        if self.keys.match(
                event,
                "mark_in",
        ):
            self.mark_in()

            return

        if self.keys.match(
                event,
                "mark_out",
        ):
            self.mark_out()

            return

        if self.keys.match(
                event,
                "commit_selection",
        ):

            self.commit_selection()

            return

        if self.keys.match(
                event,
                "add_unselected",
        ):

            self.add_unselected()

            return

        if self.keys.match(
                event,
                "clear_all_scenes",
        ):

            self.clear_all_scenes()

            return

        if self.keys.match(
                event,
                "goto_selection_start",
        ):

            self.goto_selection_start()

            return

        if self.keys.match(
                event,
                "goto_selection_end",
        ):

            self.goto_selection_end()

            return

        if self.keys.match(
                event,
                "clear_selection",
        ):

            #
            # Delete selected saved range
            #

            if (
                    self.scene_list.selectionModel().selectedRows()
                    or self.selected_scene is not None
            ):
                # Delete EVERY highlighted scene, not just the last-clicked one
                # (the button already used this plural path; the key didn't).
                self.delete_selected_scenes()

                return

            #
            # Otherwise clear pending IN/OUT
            #

            self.selection.clear_pending()

            self.scene_bar.update()

            self.update_timecode()

            self.info_panel.update_info()

            return

        if self.keys.match(
                event,
                "jump_start",
        ):
            self.current_frame = 0

            self.update_display()

            return

        if self.keys.match(
                event,
                "jump_end",
        ):
            self.current_frame = max(
                0,
                len(self.frames) - 1
            )

            self.update_display()

            return

        if self.keys.match(
                event,
                "jump_back_10",
        ):
            self.jump_frames(
                -(self.fps * 10)
            )

            return

        if self.keys.match(
                event,
                "jump_forward_10",
        ):
            self.jump_frames(
                self.fps * 10
            )

            return

        if self.keys.match(
                event,
                "jump_back_30",
        ):
            self.jump_frames(
                -(self.fps * 30)
            )

            return

        if self.keys.match(
                event,
                "jump_forward_30",
        ):
            self.jump_frames(
                self.fps * 30
            )

            return

        if self.keys.match(
                event,
                "jump_back_120",
        ):
            self.jump_frames(
                -(self.fps * 120)
            )

            return

        if self.keys.match(
                event,
                "jump_forward_120",
        ):
            self.jump_frames(
                self.fps * 120
            )

            return

        if self.keys.match(
                event,
                "frame_right",
        ):
            delta = 1

        if self.keys.match(

                event,

                "frame_left",

        ):
            delta = -1

        if not delta:
            return

        #
        # Immediate single frame move
        #

        self.current_frame = max(
            0,
            min(
                len(self.frames) - 1,
                self.current_frame + delta
            )
        )

        self.step_display()

        #
        # Prepare continuous motion
        #

        self.transport_direction = delta

        self.transport_started = False

        self.transport_timer.setInterval(
            250
        )

        self.transport_timer.start()

    def _preview_clicked(
            self,
            event,
    ):
        #
        # Toggle play/pause on a left click in the preview (only when a
        # video is loaded).
        #

        if self.frames:
            self.transport_controls.toggle_play()

    def resizeEvent(
            self,
            event,
    ):
        super().resizeEvent(event)

        #
        # Re-render the current frame to the new preview size.  The frame is
        # served from the fetcher cache, so this is just a rescale, no decode.
        #

        if self.frames:
            self.update_preview_only()

            #
            # If the window is resized or maximised *during* playback, tell the
            # background decoder the new preview size too.  Otherwise it keeps
            # emitting frames at the old size and the video only catches up when
            # playback next pauses.
            #

            worker = getattr(self, "playback_worker", None)
            if worker is not None:
                worker.set_size(
                    self.preview.width(),
                    self.preview.height(),
                )

    def closeEvent(
            self,
            event,
    ):
        # Warn before quitting with unsaved scene edits.
        if self.frames and not self._confirm_discard_changes("Quit"):
            event.ignore()
            return

        # A background batch can't survive the app closing - warn, then stop it.
        if (
            getattr(self, "batch_controller", None) is not None
            and self.batch_controller.is_running()
        ):
            choice = QMessageBox.question(
                self,
                self.tr("Batch running"),
                self.tr("A batch is still running. Quitting will stop it after the "
                "current job.\n\nQuit anyway?"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if choice != QMessageBox.Yes:
                event.ignore()
                return
            self.batch_controller.stop()
            self.batch_controller.wait(10000)

        # Remember the window size for next launch (use the normal, non-
        # maximised geometry so a maximised session doesn't save a giant size),
        # plus whether it was maximised so we can restore that state.
        try:
            maximized = self.isMaximized()
            geo = self.normalGeometry()
            self.config.setdefault("window", {})
            if geo.width() > 0 and geo.height() > 0:
                self.config["window"]["width"] = geo.width()
                self.config["window"]["height"] = geo.height()
            self.config["window"]["maximized"] = maximized
            save_config(self.config)
        except Exception:
            pass

        # If a restart was requested (e.g. after a language change), launch a
        # fresh instance now that the close has been confirmed and the settings
        # saved.  startDetached spawns it independently; this instance then
        # finishes closing and quits.
        if getattr(self, "_restart_after_close", False):
            try:
                import os
                from PySide6.QtCore import QProcess
                script = os.path.abspath(sys.argv[0])
                QProcess.startDetached(sys.executable, [script] + sys.argv[1:])
            except Exception:
                pass

        #
        # Stop the background scrub thread before the window is destroyed,
        # otherwise Qt warns/crashes about a running QThread.
        #

        self._reset_media_state()

        super().closeEvent(event)

    def keyReleaseEvent(
            self,
            event,
    ):

        if event.isAutoRepeat():
            return

        if event.key() in (
                Qt.Key_Left,
                Qt.Key_Right,
        ):
            self.transport_timer.stop()

            self.transport_direction = 0

            self.transport_started = False

            self.update_display()


class _SlowTooltipStyle(QProxyStyle):
    """Lengthens the tooltip wake-up delay so hover hints appear after a short
    pause rather than almost immediately, and gives check/radio indicators a
    visible outline on the Light theme.  Everything else is delegated to the
    wrapped base style, so the app's look is otherwise unchanged."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return 1200    # milliseconds
        return super().styleHint(hint, option, widget, returnData)

    def drawPrimitive(self, element, option, painter, widget=None):
        super().drawPrimitive(element, option, painter, widget)
        # On the forced Light theme (Fusion), the check/radio indicator's border
        # is a very faint grey derived from the window colour, so the box can be
        # almost invisible against a light background.  Overlay a darker outline
        # so it's clearly visible.  Dark and System themes read fine and are
        # left untouched.
        if element in (QStyle.PrimitiveElement.PE_IndicatorCheckBox,
                       QStyle.PrimitiveElement.PE_IndicatorRadioButton):
            from PySide6.QtGui import QColor, QPen, QPalette, QPainter
            from PySide6.QtCore import Qt
            base = self.baseStyle()
            fusion = (base is not None
                      and base.metaObject().className() == "QFusionStyle")
            if fusion and option.palette.color(
                    QPalette.Window).lightness() > 140:
                painter.save()
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(90, 90, 90), 1))
                rect = option.rect.adjusted(0, 0, -1, -1)
                if element == QStyle.PrimitiveElement.PE_IndicatorRadioButton:
                    painter.setRenderHint(QPainter.Antialiasing, True)
                    painter.drawEllipse(rect)
                else:
                    painter.drawRoundedRect(rect, 2, 2)
                painter.restore()


app = QApplication(
    sys.argv
)

# Identify the application to the desktop environment so a launched window
# groups under the pinned launcher (vrd-next.desktop) rather than appearing as a
# second panel icon.  setDesktopFileName drives the Wayland app_id and the X11
# WM_CLASS the panel matches against StartupWMClass in the .desktop file.
app.setApplicationName("vrd-next")
# Deliberately no setApplicationDisplayName: the desktop appends it to the
# window title, which already starts with "VRD Next <version>", giving a
# redundant "... - VRD Next".  The window title carries the name itself.
app.setDesktopFileName("vrd-next")

# Application icon (window + taskbar).  Set on the QApplication so every
# top-level window and dialog inherits it.
try:
    from utils.icons import app_icon
    app.setWindowIcon(app_icon())
except Exception:
    pass

# Set up logging as early as we can, so anything that goes wrong from here on
# leaves a trace in the per-day log file.  Reads the log folder / retention
# straight from the config on disk.
try:
    from utils.applog import configure_logging

    _boot_cfg = ensure_config()
    _log_file = configure_logging(
        _boot_cfg.get("paths", {}).get("log_folder", ""),
        _boot_cfg.get("settings", {}).get("log_max_age_days", 30),
        _boot_cfg.get("settings", {}).get("verbose_logging", False),
        max_files=_boot_cfg.get("settings", {}).get("log_max_files", 30),
    )
    log.info("Starting %s", VERSION_STRING)
    if _log_file is not None:
        log.info("Logging to %s", _log_file)
except Exception:
    # Never let a logging problem stop the app launching.
    pass

# Appearance: apply the user's chosen theme (System / Light / Dark).  This sets
# the palette, the Fusion style for Light/Dark (or the desktop's own for
# System), and a readable tooltip style - wrapped in the slow-tooltip proxy so
# hints still appear after a brief pause rather than instantly.
try:
    from ui.theme import apply_theme

    _theme_mode = _boot_cfg.get("settings", {}).get("theme", "system")
    apply_theme(app, _theme_mode, wrap_style=_SlowTooltipStyle)
except Exception:
    # If theming fails for any reason, fall back to the plain readable tooltip
    # so the app still launches looking sensible.
    app.setStyleSheet(
        "QToolTip { color:#f0f0f0; background-color:#2b2b2b;"
        " border:1px solid #555; padding:4px 6px; }"
    )

# Language: install the chosen UI translation before any windows are built, so
# every translatable string picks it up.  English is the built-in default and
# needs no file; an unknown or missing language quietly falls back to English.
try:
    from ui.i18n import install_language

    _lang = _boot_cfg.get("settings", {}).get("language", "en")
    install_language(app, _lang)
except Exception:
    pass

class _TooltipGate(QObject):
    """When tooltips are switched off in Settings, this app-wide filter quietly
    eats tooltip events so no hints appear anywhere."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.ToolTip:
            return True    # swallow it
        return False


window = MainWindow()

# Respect the "Show tooltips" setting (read once at startup, like the other
# settings).  If it's off, install a filter that suppresses every tooltip.
try:
    _tips_on = (
        window.config
        .get("settings", {})
        .get("show_tooltips", True)
    )
    if not _tips_on:
        _tooltip_gate = _TooltipGate()
        app.installEventFilter(_tooltip_gate)
except Exception:
    pass

window.show()

sys.exit(
    app.exec()
)