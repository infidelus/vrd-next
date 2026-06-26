"""Settings for the standalone watcher (tray) app.

The watcher keeps its OWN small config file, separate from the editor's
config.json, so the two processes never fight over the same file.  It only
*reads* the editor's config (never writes it) to pick up the shared Comskip
paths, which are configured once in the editor's Settings.
"""

import json
import os

from config.loader import CONFIG_DIR, ensure_config

WATCH_FILE = CONFIG_DIR / "watch.json"

# Where the watcher records which recordings it has already scanned, so it
# never re-runs Comskip on the same file.
PROCESSED_FILE = CONFIG_DIR / "watch_processed.txt"

# Programme-title patterns to skip (one per line).  A recording is ignored if
# its file name contains any of these (case-insensitive).  Edited from the
# watcher's settings, or by hand.
IGNORE_FILE = CONFIG_DIR / "watch_ignore.txt"


def _default_output_dir():
    return os.path.join(os.path.expanduser("~"), "Videos", "VRD Watch")


DEFAULTS = {
    # Folders to scan (recursively) for new recordings.
    "input_roots": [],
    # Where the produced .vprj projects are written.
    "output_dir": _default_output_dir(),
    # Glob matched against file names.
    "pattern": "*.ts",
    # How often to scan, in minutes.
    "scan_interval_minutes": 30,
    # A recording must have been untouched for this long before we treat it as
    # finished recording and scan it (guards against in-progress recordings).
    "settle_minutes": 5,
    # Paused: the timer keeps the app alive but scans don't run automatically.
    "paused": False,
    # Run a scan shortly after launch instead of waiting a full interval.
    "scan_on_launch": False,
    # Launch the watcher automatically on login (XDG autostart).
    "autostart": False,
    # When Comskip finds no commercials, still write a full-length .vprj (one
    # with an empty cut list) so the recording always lands in the Batch
    # Manager ready to review or copy, rather than being silently passed over.
    "save_when_no_adverts": True,
    # How many of the watcher's own per-day log files to keep (the oldest
    # beyond this are pruned at startup; 0 keeps every log).  The watcher is a
    # separate helper app, so it keeps its own retention rather than borrowing
    # the editor's.
    "log_max_files": 30,
    # Recordings to never process (absolute paths).
    "ignore": [],
}


class WatchConfig:
    """Loads/saves watch.json and exposes the settings as attributes."""

    def __init__(self, data=None):
        self._data = dict(DEFAULTS)
        if data:
            self._data.update({k: v for k, v in data.items() if k in DEFAULTS})

    # --- persistence ------------------------------------------------------ #

    @classmethod
    def load(cls):
        try:
            with open(WATCH_FILE, encoding="utf-8") as f:
                return cls(json.load(f))
        except (OSError, ValueError):
            return cls()

    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(WATCH_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass

    # --- typed accessors -------------------------------------------------- #

    @property
    def input_roots(self):
        return list(self._data.get("input_roots", []))

    @input_roots.setter
    def input_roots(self, value):
        self._data["input_roots"] = list(value)

    @property
    def output_dir(self):
        return self._data.get("output_dir") or _default_output_dir()

    @output_dir.setter
    def output_dir(self, value):
        self._data["output_dir"] = value

    @property
    def pattern(self):
        return self._data.get("pattern") or "*.ts"

    @pattern.setter
    def pattern(self, value):
        self._data["pattern"] = value

    @property
    def scan_interval_minutes(self):
        try:
            return max(1, int(self._data.get("scan_interval_minutes", 30)))
        except (TypeError, ValueError):
            return 30

    @scan_interval_minutes.setter
    def scan_interval_minutes(self, value):
        self._data["scan_interval_minutes"] = int(value)

    @property
    def settle_seconds(self):
        try:
            return max(0, int(float(self._data.get("settle_minutes", 5)) * 60))
        except (TypeError, ValueError):
            return 300

    @property
    def settle_minutes(self):
        return self._data.get("settle_minutes", 5)

    @settle_minutes.setter
    def settle_minutes(self, value):
        self._data["settle_minutes"] = value

    @property
    def log_max_files(self):
        try:
            return max(0, int(self._data.get("log_max_files", 30)))
        except (TypeError, ValueError):
            return 30

    @log_max_files.setter
    def log_max_files(self, value):
        self._data["log_max_files"] = int(value)

    @property
    def paused(self):
        return bool(self._data.get("paused", False))

    @paused.setter
    def paused(self, value):
        self._data["paused"] = bool(value)

    @property
    def scan_on_launch(self):
        return bool(self._data.get("scan_on_launch", False))

    @scan_on_launch.setter
    def scan_on_launch(self, value):
        self._data["scan_on_launch"] = bool(value)

    @property
    def autostart(self):
        return bool(self._data.get("autostart", False))

    @autostart.setter
    def autostart(self, value):
        self._data["autostart"] = bool(value)

    @property
    def save_when_no_adverts(self):
        return bool(self._data.get("save_when_no_adverts", True))

    @save_when_no_adverts.setter
    def save_when_no_adverts(self, value):
        self._data["save_when_no_adverts"] = bool(value)

    @property
    def ignore(self):
        return set(self._data.get("ignore", []))

    @ignore.setter
    def ignore(self, value):
        self._data["ignore"] = sorted(set(value))

    # --- shared Comskip paths (read-only from the editor's config) -------- #

    def comskip_paths(self):
        """Return (binary, ini) from the editor's config, or ("", "")."""
        try:
            cfg = ensure_config()
            paths = cfg.get("paths", {})
            return paths.get("comskip_binary", ""), paths.get("comskip_ini", "")
        except Exception:
            return "", ""
