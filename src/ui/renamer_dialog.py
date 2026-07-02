"""TV Renamer dialog (Extras -> TV Renamer).

This is a batch worklist.  Choose a folder (or files) and every video is listed
straight away.  "Match TV Shows" looks each one up on TMDB and proposes a new
name; you review the whole list, tick what you want, and rename.  Nothing is
overwritten, and renamed rows stay in the list marked "Done" so they are never
offered again.

Two ways to match:

* Auto-match every show (the default, remembered between runs) — one Match TV
  Shows groups the files by show, searches TMDB for each, takes the best hit
  as a suggestion and fills the whole table in a single pass.  Double-click any
  row to open a chooser and pick a different show; all of that show's rows are
  re-matched.

* Auto-match off — the older one-show-at-a-time flow: search a series in the
  Series box, choose it, then Match TV Shows matches just that show's files.

A  /  in the naming pattern builds a folder tree as well as a file name, so
renamed files can be organised straight into a 'Show (Year)/Season N/' layout
for Plex/Jellyfin libraries; a flat pattern just renames in place.

All the metadata/parsing/formatting lives in the dependency-free ``addons``
package; this module is the Qt skin over it.  The matching helpers
(``auto_match``, ``apply_series_to_rows``) take a stand-in client and no widgets,
so they can be unit-tested on their own.
"""

import os
import re
import shutil

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from config.loader import save_config
from addons.filename_parse import parse_filename
from addons.rename_pattern import DEFAULT_TV_PATTERN, format_path
from addons.match_cache import get_match, put_match, get_checked, save as save_cache
from addons.tmdb_client import TmdbClient, TmdbError, year_of
from ui.match_worker import MatchRunner
from utils.file_mover import move_jobs

# Video containers we offer to scan a folder for.
VIDEO_EXTS = {
    ".ts", ".mkv", ".mp4", ".m2ts", ".mts", ".mpg", ".mpeg", ".avi",
    ".m4v", ".mov", ".wmv",
}

CODES_HELP = (
    "Pattern codes:\n\n"
    "  %N        series name\n"
    "  %NY       series name with year, e.g. Castle (2009)\n"
    "  %Y        year the series started\n"
    "  %S / %SZ  season  /  season zero-padded (05)\n"
    "  %E / %EZ  episode /  episode zero-padded (07)\n"
    "  %T        episode title\n"
    "  %TMDBID   TMDB id\n\n"
    "Multi-episode files join the episode numbers with a dash, so two\n"
    "episodes give e.g. S05E11-12.  The original file extension is kept.\n\n"
    "A  /  in the pattern makes a folder, so the folder padding is just\n"
    "another code: 'Season %S' gives an unpadded folder while 'S%SZE%EZ'\n"
    "gives padded files - the usual Kodi/Jellyfin layout.  Pick a ready-made\n"
    "layout from the Preset list, or type your own and it becomes 'Custom'."
)

# Ready-made TV layouts offered in the Preset list.  Plex, Jellyfin and Kodi all
# read this shape and accept either season-folder padding.  ("label", pattern).
TV_PRESETS = [
    ("Show (Year) / Season 02 / Episode", "%NY/Season %SZ/%N - S%SZE%EZ - %T"),
    ("Show (Year) / Season 2 / Episode",  "%NY/Season %S/%N - S%SZE%EZ - %T"),
    ("Flat - no folders",                 "%N - S%SZE%EZ - %T"),
]

# A stand-in episode used to render the live example line before anything has
# been matched, so the pattern's effect is visible straight away.
SAMPLE_TV_META = {
    "name": "Castle", "year": 2009, "season": 2, "episodes": [1],
    "title": "Deep in Death", "tmdb_id": 1419,
}
SAMPLE_TV_EXT = "mkv"


def _norm_show(name):
    """Normalise a show name for grouping: lowercase, letters/digits only."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _parse_se(season_text, episode_text):
    """Turn the chooser's Season / Episode text boxes into values.

    Returns ``(season_or_None, episodes_or_None)``.  The episode box accepts a
    single number, a range like ``11-12`` or a list like ``11,12`` — any digits
    found are taken in order.  Blank or non-numeric boxes give ``None`` so the
    parsed value (if any) is left untouched.
    """
    season = None
    st = (season_text or "").strip()
    if st.isdigit():
        season = int(st)
    eps = [int(x) for x in re.findall(r"\d+", episode_text or "")]
    return season, (eps or None)


class PreviewRow:
    """One file's state as it moves through the worklist.

    Created from a path; the matching helpers fill in ``series``/``new_name``/
    ``note`` later, and ``status`` flips to "done" once the file is renamed.

    ``man_season``/``man_episodes`` hold a season/episode set by hand from the
    double-click chooser; when present they override whatever was parsed from
    the filename, so a file the parser couldn't read can still be matched.
    """

    def __init__(self, path):
        p = parse_filename(path)
        self.path = path
        self.orig_name = os.path.basename(path)   # shown in the Current column
        self.parsed = p
        self.group = _norm_show(p.show)           # grouping key for the show
        self.man_season = None     # manual override season, or None
        self.man_episodes = None   # manual override episode list, or None
        self.series = None        # {"id","name","year"} once matched
        self.new_name = None      # proposed relative path incl. extension, or None
        self.note = None          # reason it can't be renamed, or None
        self.meta = None          # cached fields, to re-render on a pattern edit
        self.status = "pending"   # "pending" | "done"
        self.checked = None       # remembered tick state: None until first shown

    @property
    def season(self):
        """Effective season: a manual override wins over the parsed value."""
        return self.man_season if self.man_season is not None else self.parsed.season

    @property
    def episodes(self):
        """Effective episode list: a manual override wins over the parsed list."""
        if self.man_episodes is not None:
            return self.man_episodes
        return self.parsed.episodes

    @property
    def episode(self):
        eps = self.episodes
        return eps[0] if eps else None

    @property
    def has_se(self):
        """True once we have a season and at least one episode to work with."""
        return self.season is not None and bool(self.episodes)

    @property
    def renamable(self):
        return (
            self.status == "pending"
            and self.new_name is not None
            and self.note is None
        )


# --------------------------------------------------------------------------- #
#  Matching helpers (no Qt — unit-testable with a stand-in client)
# --------------------------------------------------------------------------- #
def _resolve_series(client, display_name):
    """Search TMDB for a show name and return the top hit as {id,name,year}.

    Returns ``None`` when there is nothing to search for or no result.
    """
    if not display_name:
        return None
    results = client.search_tv(display_name)
    if not results:
        return None
    top = results[0]
    return {
        "id": top["id"],
        "name": top.get("name", ""),
        "year": year_of(top.get("first_air_date")),
    }


def _season_titles(client, series_id, seasons):
    """Fetch episode titles for the given seasons -> {(season, ep): title}."""
    titles = {}
    for season in seasons:
        data = client.tv_season(series_id, season)
        for ep in data.get("episodes", []):
            titles[(season, ep.get("episode_number"))] = ep.get("name", "")
    return titles


def apply_series_to_rows(client, rows, series, pattern):
    """Fill ``series``/``new_name``/``note`` for rows that share one series.

    Mutates the rows in place.  One ``tv_season`` request is made per distinct
    season present.  Raises ``TmdbError`` if a season lookup fails.
    """
    seasons = sorted({r.season for r in rows if r.has_se})
    titles = _season_titles(client, series["id"], seasons) if series else {}

    for r in rows:
        r.series = series
        r.meta = None
        if not r.has_se:
            r.new_name, r.note = None, "no S/E — double-click to set it"
            continue
        if series is None:
            r.new_name, r.note = None, "no TMDB match — double-click to choose"
            continue
        title = titles.get((r.season, r.episode))
        if title is None:
            r.new_name, r.note = None, "episode not on TMDB"
            continue
        meta = {
            "name": series["name"],
            "year": series["year"],
            "season": r.season,
            "episodes": r.episodes,
            "title": title,
            "tmdb_id": series["id"],
        }
        r.meta = meta
        base = format_path(pattern, meta)
        r.new_name = base + ("." + r.parsed.ext if r.parsed.ext else "")
        r.note = None


def auto_match(client, rows, pattern, progress_cb=None):
    """Resolve every distinct show among ``rows`` and fill their names.

    Rows are grouped by normalised show name; each group is searched once and
    its best hit applied.  Mutates the rows in place.  Raises ``TmdbError`` on a
    lookup failure.

    ``progress_cb(done, total, label)`` is called before each show; if it
    returns ``False`` the run stops early (the shows done so far keep their
    matches, the rest are left untouched).
    """
    groups = {}
    for r in rows:
        # Skip rows already matched (or done): a re-match only queries the
        # files that still need it, leaving cached matches untouched.
        if r.new_name is not None or r.status == "done":
            continue
        groups.setdefault(r.group, []).append(r)
    groups = list(groups.values())
    total = len(groups)
    for i, grp in enumerate(groups):
        if progress_cb is not None and not progress_cb(i, total, grp[0].parsed.show or "?"):
            return
        display = grp[0].parsed.show
        series = _resolve_series(client, display)
        apply_series_to_rows(client, grp, series, pattern)
    if progress_cb is not None:
        progress_cb(total, total, "")


class RenamerDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.files = []
        self.series = None            # {"id","name","year"} for manual mode
        self.rows = []
        self._current_folder = None   # last folder loaded, for Refresh
        self._filling = False         # guards itemChanged during table rebuilds
        # Optional library root to move renamed files into; "" = next to source.
        self._dest_root = self.config.get("settings", {}).get(
            "renamer_dest_root", ""
        )
        # Guard so syncing the Preset list to the pattern box (and back) can't
        # ricochet into an endless signal loop.
        self._syncing_preset = False

        self.setWindowTitle("TV Renamer")
        self.setMinimumWidth(760)
        self.setMinimumHeight(560)

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
            "renamer_autoload", True
        )
        self.autoload_check.setChecked(bool(autoload_on))
        self.autoload_check.toggled.connect(self._on_autoload_toggled)
        source_layout.addWidget(choose_folder)
        source_layout.addWidget(choose_files)
        source_layout.addWidget(self.refresh_btn)
        source_layout.addWidget(self.source_label, 1)
        source_layout.addWidget(self.autoload_check)
        layout.addWidget(source_box)

        # --- series (used when auto-match is off) -------------------------
        self.series_box = QGroupBox("Series (used when auto-match is off)")
        series_layout = QHBoxLayout(self.series_box)
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Series name to search for")
        self.query_edit.returnPressed.connect(self._search)
        search_btn = QPushButton("Search TMDB")
        search_btn.clicked.connect(self._search)
        self.results_combo = QComboBox()
        self.results_combo.setMinimumWidth(220)
        self.results_combo.currentIndexChanged.connect(self._series_chosen)
        series_layout.addWidget(self.query_edit, 1)
        series_layout.addWidget(search_btn)
        series_layout.addWidget(self.results_combo, 1)
        layout.addWidget(self.series_box)

        # --- preset + pattern ---------------------------------------------
        saved_pattern = self.config.get("settings", {}).get(
            "renamer_pattern", DEFAULT_TV_PATTERN
        )

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        for label, pat in TV_PRESETS:
            self.preset_combo.addItem(label, pat)
        self.preset_combo.addItem("Custom…", None)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo, 1)
        layout.addLayout(preset_row)

        pattern_row = QHBoxLayout()
        pattern_row.addWidget(QLabel("Pattern:"))
        self.pattern_edit = QLineEdit(saved_pattern)
        # Live: update the example and the Preset selector on every keystroke;
        # persist and re-render the matched rows once editing finishes.
        self.pattern_edit.textEdited.connect(self._on_pattern_text_edited)
        self.pattern_edit.editingFinished.connect(self._reapply_pattern)
        pattern_row.addWidget(self.pattern_edit, 1)
        codes_btn = QPushButton("Codes…")
        codes_btn.clicked.connect(
            lambda: QMessageBox.information(self, "Pattern codes", CODES_HELP)
        )
        pattern_row.addWidget(codes_btn)
        self.preview_btn = QPushButton("Match TV Shows")
        self.preview_btn.clicked.connect(self._build_preview)
        pattern_row.addWidget(self.preview_btn)
        layout.addLayout(pattern_row)

        # Greyed "e.g." line showing what the current pattern produces, using the
        # first matched row when there is one or a built-in sample otherwise, so
        # a  /  in the pattern visibly becomes a folder.
        self.example_label = QLabel("")
        self.example_label.setEnabled(False)        # muted, like a hint
        self.example_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.example_label)

        # Reflect the saved pattern in the Preset selector and the example line.
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
        # Folders now come straight from any  /  in the pattern, so there's no
        # separate "organise into subfolders" control: a flat pattern renames in
        # place, a foldered one builds the tree.

        # Destructive, so deliberately NOT remembered - it starts off each time
        # and the user has to opt in for that session.
        self.overwrite_check = QCheckBox(
            "Overwrite files that already exist at the destination"
        )
        layout.addWidget(self.overwrite_check)

        self.auto_check = QCheckBox(
            "Auto-match every show in one pass  "
            "(double-click a row to change its show)"
        )
        # Remembered between runs; default on.
        auto_on = self.config.get("settings", {}).get("renamer_auto_match", True)
        self.auto_check.setChecked(bool(auto_on))
        # Connect after setChecked so opening the dialog doesn't trigger a save.
        self.auto_check.toggled.connect(self._on_auto_toggled)
        layout.addWidget(self.auto_check)

        # --- preview table ------------------------------------------------
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Current name", "New name", "Status"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.cellDoubleClicked.connect(self._row_double_clicked)
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

        # Hide the manual Series box when auto-match is on (it's only used in
        # manual mode); when off, show it and put the cursor in the query box.
        self._apply_auto_mode()

        # Open straight into the last folder, if that option is on.
        self._maybe_autoload()

    # -- preferences -------------------------------------------------------
    def _save_auto_pref(self, checked):
        """Remember the auto-match toggle across runs."""
        self.config.setdefault("settings", {})["renamer_auto_match"] = bool(checked)
        try:
            save_config(self.config)
        except Exception:
            # Persisting a UI preference should never break the dialog.
            pass

    def _on_auto_toggled(self, checked):
        self._save_auto_pref(checked)
        self._apply_auto_mode()

    def _reapply_pattern(self):
        """Persist the naming pattern and re-render already-matched rows with it,
        so an edit takes effect immediately without re-matching TMDB."""
        self.config.setdefault("settings", {})["renamer_pattern"] = \
            self.pattern_edit.text()
        try:
            save_config(self.config)
        except Exception:
            pass
        pattern = self.pattern_edit.text().strip() or DEFAULT_TV_PATTERN
        changed = False
        for r in getattr(self, "rows", []):
            if r.status == "done" or not getattr(r, "meta", None):
                continue
            new_name = format_path(pattern, r.meta) + (
                "." + r.parsed.ext if r.parsed.ext else ""
            )
            if new_name != r.new_name:
                r.new_name = new_name
                changed = True
        self._sync_preset_combo()
        self._update_example()
        if changed:
            self._fill_table()

    def _on_pattern_text_edited(self, _text):
        """Live, per-keystroke feedback: refresh the example line and keep the
        Preset selector in step.  Persisting and re-rendering rows waits for
        editingFinished (``_reapply_pattern``) to avoid churn on every key."""
        self._sync_preset_combo()
        self._update_example()

    def _on_preset_changed(self, _index):
        """Drop the chosen preset's pattern into the box (unless 'Custom…' was
        picked, which leaves whatever's there), then apply it."""
        if self._syncing_preset:
            return                      # programmatic change, not the user
        pattern = self.preset_combo.currentData()
        if pattern is None:
            return                      # "Custom…" - keep the current text
        self.pattern_edit.setText(pattern)
        self._reapply_pattern()

    def _sync_preset_combo(self):
        """Point the Preset selector at whichever preset matches the current
        pattern, or 'Custom…' when none does.  Guarded so it can't re-trigger
        ``_on_preset_changed``."""
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
        """The metadata and extension used to render the example line: the first
        matched row if there is one, else a built-in sample."""
        for r in getattr(self, "rows", []):
            if getattr(r, "meta", None):
                return r.meta, (r.parsed.ext or SAMPLE_TV_EXT)
        return SAMPLE_TV_META, SAMPLE_TV_EXT

    def _update_example(self):
        """Refresh the greyed 'e.g.' line under the pattern box."""
        pattern = self.pattern_edit.text().strip() or DEFAULT_TV_PATTERN
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
        self.config.setdefault("settings", {})["renamer_dest_root"] = self._dest_root
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

    def _apply_auto_mode(self):
        """Show the manual Series box only when auto-match is off.

        In auto-match mode it isn't used (matches come from Match TV Shows and
        per-row double-click), so hiding it keeps the window uncluttered.
        """
        manual = not self.auto_check.isChecked()
        self.series_box.setVisible(manual)
        if manual:
            self.query_edit.setFocus()

    # -- key ---------------------------------------------------------------
    def _client(self):
        """Build a TMDB client from the key stored in Settings."""
        key = self.config.get("settings", {}).get("tmdb_api_key", "").strip()
        if not key:
            QMessageBox.warning(
                self,
                "TV Renamer",
                "No TMDB API key set. Add one in Settings (the TMDB API key "
                "field on the General page), then try again.",
            )
            return None
        return TmdbClient(key)

    # -- source ------------------------------------------------------------
    def _default_dir(self):
        """Start file dialogs in the last renamer folder, else the configured
        'saved videos' (export) area."""
        last = self.config.get("settings", {}).get("renamer_last_folder", "")
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
        self.rows = [PreviewRow(f) for f in files]
        # Re-apply any match we've already cached for these files, so a refresh
        # or relaunch keeps matches and a new match only hits TMDB for the rest.
        pattern = self.pattern_edit.text().strip()
        for r in self.rows:
            meta = get_match(self.config, "tv", r.path)
            if not meta:
                continue
            r.meta = meta
            r.series = {
                "id": meta.get("tmdb_id"),
                "name": meta.get("name"),
                "year": meta.get("year"),
            }
            ext = r.parsed.ext
            r.new_name = format_path(pattern, meta) + (("." + ext) if ext else "")
            r.note = None
            # Restore the user's remembered tick (an un-tick should survive a
            # refresh/relaunch, not silently re-tick).
            r.checked = get_checked(self.config, "tv", r.path)
        if files:
            self.source_label.setText("%d file(s) loaded." % len(files))
            guess = parse_filename(files[0]).show
            if guess and not self.query_edit.text().strip():
                self.query_edit.setText(guess)
        else:
            self.source_label.setText("No files chosen.")
        self._fill_table()

    def _cache_matches(self):
        """Persist the current matches so a refresh/relaunch keeps them."""
        for r in self.rows:
            if r.meta:
                put_match(self.config, "tv", r.path, r.meta,
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
                    self, "TV Renamer", "No video files found in that folder."
                )
            return
        self._set_files(files)

    def _refresh_folder(self):
        """Re-scan the current folder, picking up any files added since."""
        if self._current_folder:
            self._load_folder(self._current_folder)

    def _on_autoload_toggled(self, checked):
        self.config.setdefault("settings", {})["renamer_autoload"] = bool(checked)
        try:
            save_config(self.config)
        except Exception:
            pass

    def _save_last_folder(self, folder):
        self.config.setdefault("settings", {})["renamer_last_folder"] = folder
        try:
            save_config(self.config)
        except Exception:
            pass

    def _maybe_autoload(self):
        """On open, load the last folder if the option is on and it's valid."""
        if not self.autoload_check.isChecked():
            return
        last = self.config.get("settings", {}).get("renamer_last_folder", "")
        if last and os.path.isdir(last):
            self._load_folder(last, announce=False)

    def _choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add files", self._default_dir()
        )
        if files:
            self._set_files(list(files))

    # -- series search (manual mode) --------------------------------------
    def _search(self):
        client = self._client()
        if client is None:
            return
        query = self.query_edit.text().strip()
        if not query:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        err = None
        try:
            results = client.search_tv(query)
        except TmdbError as e:
            err = str(e)
            results = []
        finally:
            QApplication.restoreOverrideCursor()
        if err:
            QMessageBox.warning(self, "TV Renamer", err)
            return

        self.results_combo.clear()
        if not results:
            self.results_combo.addItem("(no matches)", None)
            return
        for r in results:
            yr = year_of(r.get("first_air_date"))
            label = "%s (%s)" % (r.get("name", "?"), yr or "----")
            self.results_combo.addItem(label, r)

    def _series_chosen(self, _index):
        data = self.results_combo.currentData()
        if not data:
            self.series = None
            return
        self.series = {
            "id": data["id"],
            "name": data.get("name", ""),
            "year": year_of(data.get("first_air_date")),
        }

    # -- choosing a different show for a row (double-click) ----------------
    def _pick_show(self, initial_query, row):
        """Modal chooser: search TMDB and optionally set season/episode by hand.

        Returns ``{"series": {...}, "season": int|None, "episodes": list|None}``
        or ``None`` if cancelled.
        """
        client = self._client()
        if client is None:
            return None

        dlg = QDialog(self)
        dlg.setWindowTitle("Choose the right show")
        dlg.setMinimumWidth(500)
        v = QVBoxLayout(dlg)

        search_row = QHBoxLayout()
        q = QLineEdit(initial_query)
        search_btn = QPushButton("Search")
        search_row.addWidget(q, 1)
        search_row.addWidget(search_btn)
        v.addLayout(search_row)

        lst = QListWidget()
        v.addWidget(lst, 1)

        # Season / episode, pre-filled when they were read from the file name so
        # the user can correct a mis-parse, or fill them in when there were none.
        se_row = QHBoxLayout()
        se_row.addWidget(QLabel("Season:"))
        season_edit = QLineEdit()
        season_edit.setMaximumWidth(70)
        if row.season is not None:
            season_edit.setText(str(row.season))
        se_row.addWidget(season_edit)
        se_row.addWidget(QLabel("Episode(s):"))
        ep_edit = QLineEdit()
        if row.episodes:
            ep_edit.setText("-".join(str(e) for e in row.episodes))
        ep_edit.setPlaceholderText("e.g. 11  or  11-12")
        se_row.addWidget(ep_edit, 1)
        v.addLayout(se_row)

        hint = QLabel(
            "Set the season and episode here if they aren't in the file name."
        )
        hint.setStyleSheet("color: gray;")
        v.addWidget(hint)

        buttons = QHBoxLayout()
        ok = QPushButton("Use This Show")
        ok.setEnabled(False)
        cancel = QPushButton("Cancel")
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        v.addLayout(buttons)

        found = []

        def do_search():
            text = q.text().strip()
            if not text:
                return
            QApplication.setOverrideCursor(Qt.WaitCursor)
            err = None
            try:
                results = client.search_tv(text)
            except TmdbError as e:
                err = str(e)
                results = []
            finally:
                QApplication.restoreOverrideCursor()
            if err:
                QMessageBox.warning(dlg, "TV Renamer", err)
                return
            found.clear()
            found.extend(results)
            lst.clear()
            for r in results:
                yr = year_of(r.get("first_air_date"))
                lst.addItem("%s (%s)" % (r.get("name", "?"), yr or "----"))
            ok.setEnabled(False)

        search_btn.clicked.connect(do_search)
        q.returnPressed.connect(do_search)
        lst.currentRowChanged.connect(lambda i: ok.setEnabled(i >= 0))
        lst.itemDoubleClicked.connect(lambda *_: dlg.accept())
        ok.clicked.connect(dlg.accept)
        cancel.clicked.connect(dlg.reject)

        do_search()   # run once with the row's current show name
        if dlg.exec() != QDialog.Accepted:
            return None
        i = lst.currentRow()
        if i < 0 or i >= len(found):
            return None
        top = found[i]
        season, episodes = _parse_se(season_edit.text(), ep_edit.text())
        return {
            "series": {
                "id": top["id"],
                "name": top.get("name", ""),
                "year": year_of(top.get("first_air_date")),
            },
            "season": season,
            "episodes": episodes,
        }

    def _pick_episode(self, row):
        """Modal picker: browse the matched show's seasons and episodes on TMDB
        and choose the exact season/episode for this file.

        Seasons (Specials included) come from ``tv_details``; the chosen season's
        episodes from ``tv_season``.  The file's current match is pre-selected,
        and a 'Change show…' button falls through to the search chooser.  Returns
        ``{"series": {...}, "season": int, "episodes": [int, ...]}`` or ``None``
        if cancelled.
        """
        client = self._client()
        if client is None:
            return None
        if not row.series or not row.series.get("id"):
            # Nothing matched yet - search for the show first.
            return self._pick_show(row.parsed.show or "", row)

        dlg = QDialog(self)
        dlg.setWindowTitle("Pick episode")
        dlg.setMinimumWidth(460)
        v = QVBoxLayout(dlg)

        head = QHBoxLayout()
        show_lbl = QLabel()
        head.addWidget(show_lbl, 1)
        change_btn = QPushButton("Change show…")
        head.addWidget(change_btn)
        v.addLayout(head)

        srow = QHBoxLayout()
        srow.addWidget(QLabel("Season:"))
        season_combo = QComboBox()
        srow.addWidget(season_combo, 1)
        v.addLayout(srow)

        ep_list = QListWidget()
        ep_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        v.addWidget(ep_list, 1)

        hint = QLabel("Pick the episode (Ctrl-click for a two-parter).")
        hint.setStyleSheet("color: gray;")
        v.addWidget(hint)

        buttons = QHBoxLayout()
        ok = QPushButton("Select episode")
        ok.setEnabled(False)
        cancel = QPushButton("Cancel")
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        v.addLayout(buttons)

        # ``series`` may be swapped by Change show…; ``episodes`` holds the dicts
        # for the season currently shown, so the list rows map back to numbers.
        state = {"series": dict(row.series), "episodes": []}

        def set_show_label():
            s = state["series"]
            yr = s.get("year")
            show_lbl.setText(
                "<b>%s</b>%s"
                % (s.get("name", "?"), (" (%s)" % yr) if yr else "")
            )

        def load_episodes(season_number, preselect=None):
            ep_list.clear()
            state["episodes"] = []
            if season_number is None:
                ok.setEnabled(False)
                return
            QApplication.setOverrideCursor(Qt.WaitCursor)
            err = None
            try:
                data = client.tv_season(state["series"]["id"], season_number)
            except TmdbError as e:
                err, data = str(e), {}
            finally:
                QApplication.restoreOverrideCursor()
            if err:
                QMessageBox.warning(dlg, "TV Renamer", err)
                return
            eps = data.get("episodes", []) or []
            state["episodes"] = eps
            want = set(preselect or [])
            for ep in eps:
                num = ep.get("episode_number")
                label = "%3s   %s" % (num, ep.get("name", "") or "")
                air = ep.get("air_date") or ""
                if air:
                    label += "   (%s)" % air
                ep_list.addItem(label)
                if num in want:
                    ep_list.item(ep_list.count() - 1).setSelected(True)
            ok.setEnabled(bool(ep_list.selectedItems()))

        def load_seasons(select_season, preselect=None):
            QApplication.setOverrideCursor(Qt.WaitCursor)
            err = None
            try:
                details = client.tv_details(state["series"]["id"])
            except TmdbError as e:
                err, details = str(e), {}
            finally:
                QApplication.restoreOverrideCursor()
            if err:
                QMessageBox.warning(dlg, "TV Renamer", err)
                return
            nums = sorted({
                s.get("season_number")
                for s in (details.get("seasons", []) or [])
                if s.get("season_number") is not None
            })
            season_combo.blockSignals(True)
            season_combo.clear()
            for n in nums:
                season_combo.addItem("Specials" if n == 0 else "Season %d" % n, n)
            idx = season_combo.findData(select_season)
            season_combo.setCurrentIndex(idx if idx >= 0 else 0)
            season_combo.blockSignals(False)
            cur = season_combo.currentData()
            # On the file's own season, pre-select its episode(s) by default.
            if preselect is None and cur == row.season:
                preselect = row.episodes
            load_episodes(cur, preselect=preselect)

        def on_change_show():
            picked = self._pick_show(state["series"].get("name", ""), row)
            if not picked:
                return
            state["series"] = picked["series"]
            set_show_label()
            target_season = (
                picked["season"] if picked["season"] is not None else row.season
            )
            load_seasons(target_season, preselect=picked["episodes"])

        season_combo.currentIndexChanged.connect(
            lambda _i: load_episodes(season_combo.currentData())
        )
        ep_list.itemSelectionChanged.connect(
            lambda: ok.setEnabled(bool(ep_list.selectedItems()))
        )
        ep_list.itemDoubleClicked.connect(
            lambda *_: dlg.accept() if ep_list.selectedItems() else None
        )
        change_btn.clicked.connect(on_change_show)
        ok.clicked.connect(dlg.accept)
        cancel.clicked.connect(dlg.reject)

        set_show_label()
        load_seasons(row.season)

        if dlg.exec() != QDialog.Accepted:
            return None
        picked_nums = sorted(
            state["episodes"][i].get("episode_number")
            for i in range(ep_list.count())
            if ep_list.item(i).isSelected()
            and state["episodes"][i].get("episode_number") is not None
        )
        if not picked_nums:
            return None
        return {
            "series": state["series"],
            "season": season_combo.currentData(),
            "episodes": picked_nums,
        }

    def _row_double_clicked(self, row_index, _col):
        if row_index < 0 or row_index >= len(self.rows):
            return
        target = self.rows[row_index]
        if target.status == "done":
            return
        # A matched show -> browse its episodes live; otherwise search first.
        if target.series and target.series.get("id"):
            result = self._pick_episode(target)
        else:
            query = (target.series or {}).get("name") or target.parsed.show or ""
            result = self._pick_show(query, target)
        if not result:
            return

        # A manually-set season/episode belongs to this one file, so record it
        # on the double-clicked row; the chosen show applies to the whole group.
        if result["season"] is not None:
            target.man_season = result["season"]
        if result["episodes"]:
            target.man_episodes = result["episodes"]

        client = self._client()
        if client is None:
            return
        grp = [
            r for r in self.rows
            if r.group == target.group and r.status != "done"
        ]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        err = None
        try:
            apply_series_to_rows(
                client, grp, result["series"], self.pattern_edit.text().strip()
            )
        except TmdbError as e:
            err = str(e)
        finally:
            QApplication.restoreOverrideCursor()
        if err:
            QMessageBox.warning(self, "TV Renamer", err)
            return
        self._cache_matches()
        self._fill_table()

    # -- preview / targets -------------------------------------------------
    def _target_for(self, row):
        """Absolute destination path for a row.  ``new_name`` is already the
        relative path the pattern produced (folders included, or a bare file
        name for a flat pattern), so we just root it at the destination - the
        chosen library root, or the file's own folder when none is set."""
        base = self._dest_root or os.path.dirname(row.path)
        return os.path.join(base, row.new_name or row.orig_name)

    def _display_new(self, row):
        """The target shown in the 'New name' column - the relative path the
        file will take under the destination."""
        return row.new_name or ""

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

    def _build_preview(self):
        if not self.rows:
            QMessageBox.information(self, "TV Renamer", "Choose some files first.")
            return
        client = self._client()
        if client is None:
            return

        pending = [r for r in self.rows if r.status != "done"]
        if not pending:
            QMessageBox.information(
                self, "TV Renamer", "Everything in the list is done already."
            )
            return

        pattern = self.pattern_edit.text().strip()

        if self.auto_check.isChecked():
            # A mixed folder can mean many TMDB lookups, so run them off the UI
            # thread with a cancellable progress dialog rather than freezing.
            self._runner = MatchRunner(
                self, "Matching shows on TMDB…", self._match_finished
            )
            self._runner.start(
                lambda cb: auto_match(client, pending, pattern, progress_cb=cb)
            )
            return

        # Manual mode: a single chosen show is quick, so keep it inline.
        if not self.series:
            QMessageBox.information(
                self, "TV Renamer",
                "Search for and choose a series first, or tick 'Auto-match "
                "every show in one pass'.",
            )
            return
        first_group = pending[0].group
        grp = [r for r in pending if r.group == first_group]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        err = None
        try:
            apply_series_to_rows(client, grp, self.series, pattern)
        except TmdbError as e:
            err = str(e)
        finally:
            QApplication.restoreOverrideCursor()
        if err:
            QMessageBox.warning(self, "TV Renamer", err)
            return
        self._cache_matches()
        self._fill_table()

    def _match_finished(self, error):
        if error:
            QMessageBox.warning(self, "TV Renamer", error)
        else:
            self._cache_matches()
        self._fill_table()

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
                if r.meta:
                    put_match(self.config, "tv", r.path, r.meta,
                              checked=r.checked)
                    save_cache(self.config)

    def _fill_table(self):
        self._filling = True
        try:
            self.table.setRowCount(len(self.rows))
            ready = done = 0
            for i, row in enumerate(self.rows):
                current = QTableWidgetItem(row.orig_name)
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
            "%d ready · %d done · %d total   —   "
            "double-click a row to change its show."
            % (ready, done, len(self.rows))
        )

    # -- rename ------------------------------------------------------------
    def _clear_completed(self):
        """Drop the rows that have been renamed, leaving the rest in place."""
        self.rows = [r for r in self.rows if r.status != "done"]
        self._fill_table()

    def _do_rename(self):
        # Work out the moves on the UI thread (reads widget state), then hand
        # the precomputed (row, target) jobs to a background worker so a big
        # move - e.g. to a NAS - doesn't freeze the dialog.
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
            QMessageBox.information(self, "TV Renamer", "Nothing is ticked to rename.")
            return

        overwrite = self.overwrite_check.isChecked()
        self._move_runner = MatchRunner(
            self, "Renaming files…", self._move_finished, verb="",
            show_count=False,
        )
        self._move_runner.start(
            lambda cb: self._run_moves(jobs, overwrite, cb)
        )

    def _run_moves(self, jobs, overwrite, progress_cb):
        """Background body: move each (row, target) with byte-level progress, so
        a big move (e.g. to a NAS) shows real progress instead of sitting at 0%.
        No Qt access here - the targets were computed up front."""
        self._move_result = move_jobs(
            jobs, overwrite, progress_cb, lambda r: r.orig_name
        )

    def _move_finished(self, error):
        if error:
            QMessageBox.warning(self, "TV Renamer", error)
        else:
            done, skipped, failed = getattr(self, "_move_result", (0, 0, 0))
            QMessageBox.information(
                self,
                "TV Renamer",
                "Renamed %d file(s).%s%s"
                % (
                    done,
                    ("\nSkipped %d (target already exists)." % skipped) if skipped else "",
                    ("\nFailed %d." % failed) if failed else "",
                ),
            )
        self._fill_table()
