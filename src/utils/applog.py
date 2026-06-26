"""
Application logging.

Sets up Python's standard logging so the app keeps a per-day log file
(``vrd-next-YYYY-MM-DD.log``) in the configured log folder - or, when none is
set, the app config directory (matching what Settings shows as the default).

The same messages are also echoed to the console, so running from a terminal
still shows progress, and uncaught exceptions are routed into the log as well
so a crash leaves a trace to debug from.

Everywhere else in the codebase just uses the standard library::

    import logging
    logger = logging.getLogger(__name__)
    logger.info("...")

Nothing else needs to know where the file lives.  Call :func:`configure_logging`
once at startup (and again if the log folder changes in Settings).
"""

import logging
import sys
from datetime import date, datetime, timedelta
from logging.handlers import WatchedFileHandler
from pathlib import Path

from config.loader import CONFIG_DIR


# The shared parent logger name.  Modules use logging.getLogger(__name__);
# this one is handy for app-level lifecycle lines ("Starting ...").
APP_LOGGER = "vrd-next"

_FILE_PREFIX = "vrd-next-"
_FILE_SUFFIX = ".log"

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Tag our handlers so re-configuring (e.g. after the folder changes) only
# removes the ones we added, never anything else.
_OURS = "_vrd_next_handler"

_current_log_file = None
_current_log_dir = None
_excepthook_installed = False
_verbose = False


def _resolve_dir(log_folder):
    """Return a writable directory for the log file.

    Prefers the configured folder, falls back to the config directory, and as a
    last resort returns None (meaning: console logging only - never crash just
    because we couldn't open a log file).
    """
    candidates = []

    if log_folder:
        candidates.append(Path(log_folder))

    candidates.append(CONFIG_DIR)

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            # Confirm we can actually write here.
            probe = path / ".vrd-next-log-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return path
        except Exception:
            continue

    return None


def _prune(directory, max_age_days, prefix=_FILE_PREFIX, max_files=0):
    """Trim old log files for one family (the editor's plain ``vrd-next-`` logs
    or the watcher's ``vrd-next-watcher-`` logs, never each other's).

    Two independent limits, either of which may be off (0/None):
      * ``max_age_days`` - delete files older than this many days;
      * ``max_files``    - keep only this many of the most recent files.

    The currently-open log (today's) is always the newest, so neither limit ever
    removes it.  Best-effort: a file we can't remove is simply skipped."""
    if not directory:
        return

    # Match only THIS family's dated files: the date always starts with a
    # digit, so "vrd-next-[0-9]*" excludes "vrd-next-watcher-..." and vice
    # versa, keeping the two families' pruning from treading on each other.
    try:
        entries = list(directory.glob(f"{prefix}[0-9]*{_FILE_SUFFIX}"))
    except Exception:
        return

    # Oldest first, so count-trimming can drop from the front.
    try:
        entries.sort(key=lambda p: p.stat().st_mtime)
    except Exception:
        return

    # Age-based.
    if max_age_days and max_age_days > 0:
        cutoff = datetime.now() - timedelta(days=max_age_days)
        survivors = []
        for entry in entries:
            try:
                if datetime.fromtimestamp(entry.stat().st_mtime) < cutoff:
                    entry.unlink()
                else:
                    survivors.append(entry)
            except Exception:
                survivors.append(entry)
        entries = survivors

    # Count-based: keep only the most recent ``max_files``.
    if max_files and max_files > 0 and len(entries) > max_files:
        for entry in entries[:-max_files]:
            try:
                entry.unlink()
            except Exception:
                continue


def _install_excepthook():
    """Route uncaught exceptions into the log (then on to the default hook so
    the console still shows the traceback)."""
    global _excepthook_installed

    if _excepthook_installed:
        return

    previous = sys.excepthook

    def hook(exc_type, exc_value, exc_tb):
        # Don't swallow Ctrl-C: let it behave normally.
        if not issubclass(exc_type, KeyboardInterrupt):
            logging.getLogger(APP_LOGGER).critical(
                "Uncaught exception",
                exc_info=(exc_type, exc_value, exc_tb),
            )
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = hook
    _excepthook_installed = True


def configure_logging(log_folder="", max_age_days=30, verbose=False,
                      app_tag="", max_files=0):
    """(Re)configure logging.  Safe to call more than once - existing handlers
    we added are removed first, so changing the folder in Settings takes effect
    without piling up duplicate handlers.

    ``verbose`` records extra-chatty diagnostics (e.g. smartcut's own output)
    into the log; see :func:`is_verbose`.

    ``app_tag`` distinguishes companion processes that share the log folder: the
    standalone Watcher passes ``"watcher"`` so it writes its own
    ``vrd-next-watcher-YYYY-MM-DD.log`` rather than interleaving with the
    editor's file.  Empty (the default) keeps the editor's plain
    ``vrd-next-YYYY-MM-DD.log``.

    ``max_age_days`` and ``max_files`` cap how many old log files are kept (by
    age and by count); either may be 0 to disable that limit.  They apply only
    to this app_tag's own family of files.

    Returns the path to the active log file, or None if no file could be opened
    (in which case logging still goes to the console).
    """
    global _current_log_file, _current_log_dir, _verbose

    _verbose = bool(verbose)

    prefix = _FILE_PREFIX + (f"{app_tag}-" if app_tag else "")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove only the handlers we previously installed.
    for handler in list(root.handlers):
        if getattr(handler, _OURS, False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    # Console handler (stderr): INFO normally, DEBUG when verbose, so a
    # terminal run is readable by default but detailed when asked.
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if _verbose else logging.INFO)
    console.setFormatter(formatter)
    setattr(console, _OURS, True)
    root.addHandler(console)

    # File handler: everything (DEBUG and up), per day, appended.
    directory = _resolve_dir(log_folder)
    log_file = None

    if directory is not None:
        log_file = directory / f"{prefix}{date.today():%Y-%m-%d}{_FILE_SUFFIX}"
        try:
            # WatchedFileHandler re-checks the file before each write and
            # reopens it if it has been moved or deleted, so logging quietly
            # recreates the file rather than writing into a vanished handle.
            file_handler = WatchedFileHandler(
                log_file, mode="a", encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            setattr(file_handler, _OURS, True)
            root.addHandler(file_handler)
        except Exception:
            log_file = None

    _current_log_dir = directory
    _current_log_file = log_file

    _prune(directory, max_age_days, prefix, max_files)
    _install_excepthook()

    return log_file


def current_log_file():
    """Path to the active log file, or None if logging is console-only."""
    return _current_log_file


def log_directory():
    """Directory the log file lives in, or None."""
    return _current_log_dir


def is_verbose():
    """True if verbose logging is enabled (capture extra diagnostics)."""
    return _verbose
