"""Film Renamer dialog (Extras -> Film Renamer).

Films don't group like TV - each file is its own movie - so the flow is: choose
files, let each one auto-match against TMDB, fix any wrong guesses by
double-clicking the row, then rename.  A  /  in the naming pattern builds a
folder tree, so '%N (%Y)/%N (%Y)' files each film into its own
'Movie (Year)/' folder for Plex/Jellyfin; a flat pattern renames in place.

Shares the dependency-free ``addons`` engine with the TV renamer; the matching
core (``match_films``) is kept free of widgets so it can be unit-tested.
"""

import os
import shutil

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from config.loader import save_config
from addons.filename_parse import parse_filename
from addons.match_cache import get_match, put_match, get_checked, save as save_cache
from addons.rename_pattern import DEFAULT_MOVIE_PATTERN, format_path
from addons.tmdb_client import TmdbClient, TmdbError, year_of
from ui.match_worker import MatchRunner
from utils.file_mover import move_jobs

VIDEO_EXTS = {
    ".ts", ".mkv", ".mp4", ".m2ts", ".mts", ".mpg", ".mpeg", ".avi",
    ".m4v", ".mov", ".wmv",
}

CODES_HELP = (
    "Pattern codes:\n\n"
    "  %N        film title\n"
    "  %NY       title with year, e.g. The Matrix (1999)\n"
    "  %Y        release year\n"
    "  %TMDBID   TMDB id\n\n"
    "A  /  in the pattern makes a folder, so '%N (%Y)/%N (%Y)' puts each film\n"
    "in its own  Title (Year)/  folder - the Plex/Jellyfin layout.  Pick a\n"
    "ready-made layout from the Preset list, or type your own and it becomes\n"
    "'Custom'.  The original file extension is kept."
)

# Ready-made film layouts offered in the Preset list.  ("label", pattern).
FILM_PRESETS = [
    ("Film (Year) in its own folder", "%N (%Y)/%N (%Y)"),
    ("Flat - Film (Year)",            "%N (%Y)"),
]

# A stand-in film used to render the live example line before anything has been
# matched, so the pattern's effect is visible straight away.
SAMPLE_FILM_META = {"name": "Inception", "year": 2010, "tmdb_id": 27205}
SAMPLE_FILM_EXT = "mkv"


def _movie_meta(movie):
    return {
        "name": movie.get("title", ""),
        "year": year_of(movie.get("release_date")),
        "tmdb_id": movie.get("id"),
    }


def _trim_movie(movie):
    """Just the fields the renderer needs, for the persistent match cache."""
    return {
        "id": movie.get("id"),
        "title": movie.get("title", ""),
        "release_date": movie.get("release_date", ""),
    }


def build_film_name(movie, pattern, ext):
    base = format_path(pattern, _movie_meta(movie))
    return base + ("." + ext if ext else "")


class FilmRow:
    """One film file's match and resulting name."""

    def __init__(self, path, movie, new_name, note):
        self.path = path
        self.movie = movie            # matched TMDB movie dict, or None
        self.new_name = new_name      # relative path incl. extension, or None
        self.note = note              # reason it can't be renamed, or None
        self.status = None            # "done" once renamed (kept in the list)
        self.checked = None           # remembered tick state across rebuilds

    @property
    def renamable(self):
        return self.new_name is not None and self.note is None


def match_films(client, rows, pattern, progress_cb=None):
    """Auto-match each unmatched row to its best TMDB movie (no Qt, so it's
    testable).

    Rows already matched (or done) are left untouched and cost no TMDB lookup -
    so refreshing the folder or matching again only queries the new files.  One
    search per remaining file; the year in the filename narrows it, with a retry
    without the year if that finds nothing.  Mutates the rows in place.  Raises
    ``TmdbError`` on failure.

    ``progress_cb(done, total, label)`` is called before each file; if it
    returns ``False`` the run stops early, leaving the rest as they were.
    """
    todo = [r for r in rows if r.new_name is None and r.status != "done"]
    total = len(todo)
    for i, r in enumerate(todo):
        if progress_cb is not None and not progress_cb(
            i, total, os.path.basename(r.path)
        ):
            return rows
        p = parse_filename(r.path)
        results = client.search_movie(p.show, p.year)
        if not results and p.year:
            results = client.search_movie(p.show)

        if not results:
            r.movie, r.new_name = None, None
            r.note = "no match — double-click to search"
            continue

        movie = results[0]
        r.movie = movie
        r.new_name = build_film_name(movie, pattern, p.ext)
        r.note = None

    if progress_cb is not None:
        progress_cb(total, total, "")
    return rows


class FilmRenamerDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.files = []
        self.rows = []
        self._current_folder = None   # last folder loaded, for Refresh
        # Optional library root to move renamed films into; "" = next to source.
        self._dest_root = self.config.get("settings", {}).get(
            "film_renamer_dest_root", ""
        )
        # Guard so syncing the Preset list to the pattern box can't loop.
        self._syncing_preset = False

        self.setWindowTitle("Film Renamer")
        self.setMinimumWidth(740)
        self.setMinimumHeight(540)

        layout = QVBoxLayout(self)

        # --- source -------------------------------------------------------
        source_box = QGroupBox("Source")
        source_layout = QHBoxLayout(source_box)
        choose_folder = QPushButton("Choose Folder…")
        choose_folder.clicked.connect(self._choose_folder)
        choose_files = QPushButton("Add Files…")
        choose_files.clicked.connect(self._choose_files)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Re-scan the current folder for new files")
        self.refresh_btn.clicked.connect(self._refresh_folder)
        self.refresh_btn.setEnabled(False)
        self.source_label = QLabel("No files chosen.")
        self.autoload_check = QCheckBox("Load last folder on open")
        self.autoload_check.setToolTip(
            "When ticked, the renamer opens straight into the folder you used "
            "last."
        )
        autoload_on = self.config.get("settings", {}).get(
            "film_renamer_autoload", True
        )
        self.autoload_check.setChecked(bool(autoload_on))
        self.autoload_check.toggled.connect(self._on_autoload_toggled)
        source_layout.addWidget(choose_folder)
        source_layout.addWidget(choose_files)
        source_layout.addWidget(self.refresh_btn)
        source_layout.addWidget(self.source_label, 1)
        source_layout.addWidget(self.autoload_check)
        layout.addWidget(source_box)

        # --- preset + pattern + match -------------------------------------
        saved_pattern = self.config.get("settings", {}).get(
            "film_renamer_pattern", DEFAULT_MOVIE_PATTERN
        )

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        for label, pat in FILM_PRESETS:
            self.preset_combo.addItem(label, pat)
        self.preset_combo.addItem("Custom…", None)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo, 1)
        layout.addLayout(preset_row)

        pattern_row = QHBoxLayout()
        pattern_row.addWidget(QLabel("Pattern:"))
        self.pattern_edit = QLineEdit(saved_pattern)
        # Live example + Preset sync on each keystroke; persist and re-render
        # the matched rows once editing finishes.
        self.pattern_edit.textEdited.connect(self._on_pattern_text_edited)
        self.pattern_edit.editingFinished.connect(self._reapply_pattern)
        pattern_row.addWidget(self.pattern_edit, 1)
        codes_btn = QPushButton("Codes…")
        codes_btn.clicked.connect(
            lambda: QMessageBox.information(self, "Pattern codes", CODES_HELP)
        )
        pattern_row.addWidget(codes_btn)
        self.match_btn = QPushButton("Match Films")
        self.match_btn.clicked.connect(self._match)
        pattern_row.addWidget(self.match_btn)
        layout.addLayout(pattern_row)

        # Greyed "e.g." line showing what the current pattern produces.
        self.example_label = QLabel("")
        self.example_label.setEnabled(False)
        self.example_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.example_label)

        self._sync_preset_combo()
        self._update_example()

        # --- destination --------------------------------------------------
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Destination:"))
        self.dest_field = QLineEdit()
        self.dest_field.setReadOnly(True)
        dest_row.addWidget(self.dest_field, 1)
        dest_btn = QPushButton("Choose…")
        dest_btn.clicked.connect(self._choose_dest)
        dest_clear = QPushButton("Clear")
        dest_clear.clicked.connect(self._reset_dest)
        dest_row.addWidget(dest_btn)
        dest_row.addWidget(dest_clear)
        layout.addLayout(dest_row)
        self._update_dest_field()

        # --- options ------------------------------------------------------
        # Folders come straight from any  /  in the pattern: '%N (%Y)/%N (%Y)'
        # gives each film its own folder, '%N (%Y)' renames flat in place.

        # Destructive, so deliberately NOT remembered - it starts off each time
        # and the user has to opt in for that session.
        self.overwrite_check = QCheckBox(
            "Overwrite files that already exist at the destination"
        )
        layout.addWidget(self.overwrite_check)

        # --- preview table ------------------------------------------------
        hint = QLabel("Double-click a row to pick a different film for it.")
        layout.addWidget(hint)

        self._filling = False         # guards itemChanged during table rebuilds
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Current name", "New name", "Status"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.cellDoubleClicked.connect(self._row_double_clicked)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        # --- actions ------------------------------------------------------
        actions = QHBoxLayout()
        self.status_label = QLabel("")
        actions.addWidget(self.status_label, 1)
        self.rename_btn = QPushButton("Process Ticked")
        self.rename_btn.clicked.connect(self._do_rename)
        self.rename_btn.setEnabled(False)
        self.clear_btn = QPushButton("Clear Completed")
        self.clear_btn.clicked.connect(self._clear_completed)
        self.clear_btn.setEnabled(False)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(self.rename_btn)
        actions.addWidget(self.clear_btn)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        # Open straight into the last folder, if that option is on.
        self._maybe_autoload()

    # -- key ---------------------------------------------------------------
    def _client(self):
        """Build a TMDB client from the key stored in Settings."""
        key = self.config.get("settings", {}).get("tmdb_api_key", "").strip()
        if not key:
            QMessageBox.warning(
                self,
                "Film Renamer",
                "No TMDB API key set. Add one in Settings (the TMDB API key "
                "field on the General page), then try again.",
            )
            return None
        return TmdbClient(key)

    # -- source ------------------------------------------------------------
    def _default_dir(self):
        last = self.config.get("settings", {}).get(
            "film_renamer_last_folder", ""
        )
        if last and os.path.isdir(last):
            return last
        paths = self.config.get("paths", {})
        for key in ("export_folder", "last_export"):
            folder = paths.get(key, "")
            if folder and os.path.isdir(folder):
                return folder
        return ""

    def _set_files(self, files):
        self.files = files
        # Show the files in the preview straight away, applying any match we've
        # already cached for them (so a refresh or relaunch keeps matches and
        # doesn't re-hit TMDB); Match Films fills in the rest.
        pattern = self.pattern_edit.text().strip()
        rows = []
        for f in files:
            movie = get_match(self.config, "film", f)
            if movie:
                ext = parse_filename(f).ext
                fr = FilmRow(f, movie, build_film_name(movie, pattern, ext), None)
                # Restore the user's remembered tick (the film renamer often
                # mis-guesses a TV show as a film; an un-tick should survive a
                # refresh/relaunch rather than silently re-ticking).
                fr.checked = get_checked(self.config, "film", f)
                rows.append(fr)
            else:
                rows.append(FilmRow(f, None, None, None))
        self.rows = rows
        self.source_label.setText(
            "%d file(s) chosen." % len(files) if files else "No files chosen."
        )
        self.status_label.setText("")
        self._fill_table()

    def _cache_matches(self):
        """Persist the current matches so a refresh/relaunch keeps them."""
        for r in self.rows:
            if r.movie:
                put_match(self.config, "film", r.path, _trim_movie(r.movie),
                          checked=(True if r.checked is None else r.checked))
        save_cache(self.config)

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose folder", self._default_dir()
        )
        if folder:
            self._load_folder(folder)

    def _load_folder(self, folder, announce=True):
        """Scan a folder for videos, load them, and remember it as the last
        folder used.  ``announce`` controls whether an empty folder pops a
        message - the silent auto-load on open passes False."""
        if not folder or not os.path.isdir(folder):
            return
        files = [
            os.path.join(folder, name)
            for name in sorted(os.listdir(folder))
            if os.path.splitext(name)[1].lower() in VIDEO_EXTS
            and os.path.isfile(os.path.join(folder, name))
        ]
        self._current_folder = folder
        self.refresh_btn.setEnabled(True)
        self._save_last_folder(folder)
        if not files:
            if announce:
                QMessageBox.information(
                    self, "Film Renamer", "No video files found in that folder."
                )
            return
        self._set_files(files)

    def _refresh_folder(self):
        """Re-scan the current folder, picking up any films added since."""
        if self._current_folder:
            self._load_folder(self._current_folder)

    def _on_autoload_toggled(self, checked):
        self.config.setdefault("settings", {})["film_renamer_autoload"] = \
            bool(checked)
        try:
            save_config(self.config)
        except Exception:
            pass

    def _save_last_folder(self, folder):
        self.config.setdefault("settings", {})["film_renamer_last_folder"] = \
            folder
        try:
            save_config(self.config)
        except Exception:
            pass

    def _maybe_autoload(self):
        """On open, load the last folder if the option is on and it's valid."""
        if not self.autoload_check.isChecked():
            return
        last = self.config.get("settings", {}).get(
            "film_renamer_last_folder", ""
        )
        if last and os.path.isdir(last):
            self._load_folder(last, announce=False)

    def _choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add files", self._default_dir()
        )
        if files:
            self._set_files(list(files))

    # -- matching ----------------------------------------------------------
    def _match(self):
        if not self.files:
            QMessageBox.information(self, "Film Renamer", "Choose some files first.")
            return
        client = self._client()
        if client is None:
            return

        pattern = self.pattern_edit.text().strip()
        # One TMDB search per file, so a big folder is run off the UI thread
        # with a cancellable progress dialog rather than freezing.
        self._runner = MatchRunner(
            self, "Matching films on TMDB…", self._match_finished
        )
        self._runner.start(
            lambda cb: self._run_film_match(client, pattern, cb)
        )

    def _run_film_match(self, client, pattern, progress_cb):
        # Match in place over the current rows (already-matched ones are skipped
        # inside match_films, so only new files cost a TMDB lookup).
        self._pending_rows = match_films(
            client, self.rows, pattern, progress_cb=progress_cb
        )

    def _match_finished(self, error):
        if error:
            QMessageBox.warning(self, "Film Renamer", error)
        else:
            self.rows = getattr(self, "_pending_rows", self.rows)
            self._cache_matches()
        self._fill_table()

    def _reapply_pattern(self):
        """Persist the pattern and re-render already-matched rows with it, so an
        edit shows immediately without re-searching TMDB."""
        self.config.setdefault("settings", {})["film_renamer_pattern"] = \
            self.pattern_edit.text()
        try:
            save_config(self.config)
        except Exception:
            pass
        pattern = self.pattern_edit.text().strip() or DEFAULT_MOVIE_PATTERN
        changed = False
        for r in getattr(self, "rows", []):
            if r.status == "done" or not r.movie:
                continue
            ext = parse_filename(r.path).ext
            new_name = build_film_name(r.movie, pattern, ext)
            if new_name != r.new_name:
                r.new_name = new_name
                changed = True
        self._sync_preset_combo()
        self._update_example()
        if changed:
            self._fill_table()

    def _on_pattern_text_edited(self, _text):
        """Per-keystroke: refresh the example and keep the Preset selector in
        step; persisting waits for editingFinished."""
        self._sync_preset_combo()
        self._update_example()

    def _on_preset_changed(self, _index):
        """Drop the chosen preset's pattern into the box (unless 'Custom…'),
        then apply it."""
        if self._syncing_preset:
            return
        pattern = self.preset_combo.currentData()
        if pattern is None:
            return
        self.pattern_edit.setText(pattern)
        self._reapply_pattern()

    def _sync_preset_combo(self):
        """Point the Preset selector at whichever preset matches the current
        pattern, or 'Custom…' when none does."""
        text = self.pattern_edit.text().strip()
        idx = self.preset_combo.count() - 1          # "Custom…" is last
        for i in range(self.preset_combo.count()):
            data = self.preset_combo.itemData(i)
            if data is not None and data == text:
                idx = i
                break
        self._syncing_preset = True
        try:
            self.preset_combo.setCurrentIndex(idx)
        finally:
            self._syncing_preset = False

    def _example_meta(self):
        """Metadata + extension for the example line: the first matched film if
        there is one, else a built-in sample."""
        for r in getattr(self, "rows", []):
            if r.movie:
                return _movie_meta(r.movie), (parse_filename(r.path).ext or SAMPLE_FILM_EXT)
        return SAMPLE_FILM_META, SAMPLE_FILM_EXT

    def _update_example(self):
        """Refresh the greyed 'e.g.' line under the pattern box."""
        pattern = self.pattern_edit.text().strip() or DEFAULT_MOVIE_PATTERN
        meta, ext = self._example_meta()
        rel = format_path(pattern, meta)
        if rel:
            rel += "." + ext
        self.example_label.setText("e.g.   " + rel if rel else "")

    # -- destination root --------------------------------------------------
    def _update_dest_field(self):
        self.dest_field.setText(
            self._dest_root if self._dest_root else "Next to the source files"
        )
        # Paint the field now: the callers follow this with a heavy _fill_table()
        # that blocks the event loop, which would otherwise delay the visible
        # update and make "Clear" look unresponsive.
        self.dest_field.repaint()

    def _save_dest(self):
        self.config.setdefault("settings", {})["film_renamer_dest_root"] = self._dest_root
        try:
            save_config(self.config)
        except Exception:
            pass

    def _choose_dest(self):
        start = self._dest_root or self._default_dir()
        folder = QFileDialog.getExistingDirectory(
            self, "Choose destination library folder", start
        )
        if not folder:
            return
        self._dest_root = folder
        self._save_dest()
        self._update_dest_field()
        self._fill_table()

    def _reset_dest(self):
        if not self._dest_root:
            return
        self._dest_root = ""
        self._save_dest()
        self._update_dest_field()
        self._fill_table()

    def _row_double_clicked(self, r, _c):
        if r < 0 or r >= len(self.rows):
            return
        row = self.rows[r]
        movie = self._pick_movie(parse_filename(row.path).show)
        if not movie:
            return
        ext = parse_filename(row.path).ext
        row.movie = movie
        row.new_name = build_film_name(
            movie, self.pattern_edit.text().strip(), ext
        )
        row.note = None
        put_match(self.config, "film", row.path, _trim_movie(movie))
        save_cache(self.config)
        self._fill_table()

    def _pick_movie(self, query):
        """Modal chooser: search films and return the picked movie dict."""
        client = self._client()
        if client is None:
            return None

        dialog = QDialog(self)
        dialog.setWindowTitle("Choose film")
        dialog.setMinimumWidth(420)
        v = QVBoxLayout(dialog)

        q = QLineEdit(query)
        v.addWidget(q)
        listing = QListWidget()
        v.addWidget(listing)
        # Double-clicking an entry is the natural "OK" - pick it and accept,
        # the same as the TV show picker.
        listing.itemDoubleClicked.connect(lambda *_: dialog.accept())

        def do_search():
            listing.clear()
            try:
                results = client.search_movie(q.text().strip())
            except TmdbError as e:
                QMessageBox.warning(dialog, "Film Renamer", str(e))
                return
            for r in results:
                yr = year_of(r.get("release_date"))
                item = QListWidgetItem(
                    "%s (%s)" % (r.get("title", "?"), yr or "----")
                )
                item.setData(Qt.UserRole, r)
                listing.addItem(item)
            if listing.count():
                listing.setCurrentRow(0)

        q.returnPressed.connect(do_search)
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(do_search)
        v.addWidget(search_btn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        v.addWidget(buttons)

        do_search()
        if dialog.exec() != QDialog.Accepted:
            return None
        item = listing.currentItem()
        return item.data(Qt.UserRole) if item else None

    # -- targets / table ---------------------------------------------------
    def _target_for(self, row):
        """Absolute destination path.  ``new_name`` is already the relative path
        the pattern produced - its own folder for '%N (%Y)/%N (%Y)', or a bare
        file name for a flat pattern - so we just root it at the destination."""
        base = self._dest_root or os.path.dirname(row.path)
        return os.path.join(base, row.new_name)

    def _display_new(self, row):
        if row.renamable:
            return row.new_name
        return ("— %s" % row.note) if row.note else ""

    def _already_named(self, row):
        """True when a renamable row already sits at its target name/place."""
        if not row.renamable:
            return False
        return os.path.abspath(self._target_for(row)) == os.path.abspath(row.path)

    def _status_text(self, row):
        if row.status == "done":
            return "Done"
        if row.note:
            return row.note
        if row.new_name is None:
            return "not matched yet"
        if self._already_named(row):
            return "Rename Not Required"
        return "Ready"

    def _on_item_changed(self, item):
        """Remember the user's ticks so a table rebuild doesn't reset them - and
        persist them so an un-tick survives a refresh or relaunch."""
        if self._filling or item.column() != 0:
            return
        i = item.row()
        if 0 <= i < len(self.rows):
            if item.flags() & Qt.ItemIsUserCheckable:
                r = self.rows[i]
                r.checked = item.checkState() == Qt.Checked
                if r.movie:
                    put_match(self.config, "film", r.path,
                              _trim_movie(r.movie), checked=r.checked)
                    save_cache(self.config)

    def _fill_table(self):
        self._filling = True
        try:
            self.table.setRowCount(len(self.rows))
            ready = done = 0
            for i, row in enumerate(self.rows):
                current = QTableWidgetItem(os.path.basename(row.path))
                new_item = QTableWidgetItem(self._display_new(row))
                status_item = QTableWidgetItem(self._status_text(row))

                if row.status == "done":
                    current.setFlags(Qt.ItemIsEnabled)
                    current.setForeground(Qt.gray)
                    new_item.setForeground(Qt.gray)
                    status_item.setForeground(Qt.darkGreen)
                    done += 1
                elif row.renamable and self._already_named(row):
                    # Correct name and place already — nothing to do, so no tick.
                    current.setFlags(Qt.ItemIsEnabled)
                    current.setForeground(Qt.gray)
                    new_item.setForeground(Qt.gray)
                    status_item.setForeground(Qt.gray)
                elif row.renamable:
                    current.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                    if row.checked is None:
                        row.checked = True      # newly matched -> ticked by default
                    current.setCheckState(
                        Qt.Checked if row.checked else Qt.Unchecked
                    )
                    ready += 1
                else:
                    current.setFlags(Qt.ItemIsEnabled)
                    new_item.setForeground(Qt.gray)
                    status_item.setForeground(Qt.gray)

                self.table.setItem(i, 0, current)
                self.table.setItem(i, 1, new_item)
                self.table.setItem(i, 2, status_item)
        finally:
            self._filling = False

        self.rename_btn.setEnabled(ready > 0)
        self.clear_btn.setEnabled(done > 0)
        self._update_example()
        self.status_label.setText(
            "%d ready · %d done · %d total" % (ready, done, len(self.rows))
        )

    # -- rename ------------------------------------------------------------
    def _clear_completed(self):
        """Drop the rows that have been renamed, leaving the rest in place."""
        self.rows = [r for r in self.rows if r.status != "done"]
        self.files = [r.path for r in self.rows]
        self._fill_table()

    def _do_rename(self):
        # Work out the moves on the UI thread, then hand the precomputed
        # (row, target) jobs to a background worker so a big move doesn't freeze.
        jobs = []
        for i, row in enumerate(self.rows):
            if row.status == "done":
                continue
            item = self.table.item(i, 0)
            if not row.renamable or item is None:
                continue
            if item.checkState() != Qt.Checked:
                continue
            jobs.append((row, self._target_for(row)))

        if not jobs:
            QMessageBox.information(self, "Film Renamer", "Nothing is ticked to rename.")
            return

        overwrite = self.overwrite_check.isChecked()
        self._move_runner = MatchRunner(
            self, "Renaming films…", self._move_finished, verb="",
            show_count=False,
        )
        self._move_runner.start(
            lambda cb: self._run_moves(jobs, overwrite, cb)
        )

    def _run_moves(self, jobs, overwrite, progress_cb):
        """Background body: move each (row, target) with byte-level progress so a
        big move (e.g. to a NAS) shows real progress instead of sitting at 0%."""
        self._move_result = move_jobs(
            jobs, overwrite, progress_cb, lambda r: os.path.basename(r.path)
        )

    def _move_finished(self, error):
        if error:
            QMessageBox.warning(self, "Film Renamer", error)
        else:
            done, skipped, failed = getattr(self, "_move_result", (0, 0, 0))
            QMessageBox.information(
                self,
                "Film Renamer",
                "Renamed %d film(s).%s%s"
                % (
                    done,
                    ("\nSkipped %d (target already exists)." % skipped) if skipped else "",
                    ("\nFailed %d." % failed) if failed else "",
                ),
            )
        # Renamed rows stay in the list marked Done (greyed); 'Clear Completed'
        # removes them.  This matches the TV renamer.
        self.files = [r.path for r in self.rows]
        self._fill_table()
