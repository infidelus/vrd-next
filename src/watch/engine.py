"""The watcher engine: find new recordings, run Comskip, write a .vprj.

Deliberately free of any Qt or UI code so it can be unit-tested headlessly and
reused.  A standalone tray app drives it on a timer; it could equally be driven
from a cron-style one-shot.

For each recording it finds that it hasn't already handled and that has
finished recording, it runs Comskip to detect the commercials and writes a
.vprj of those cuts into the output folder.  It never edits or exports the
recording - the produced project is a starting point the user reviews and
confirms in the editor (via the Batch Manager) before anything is cut.
"""

import fnmatch
import logging
import os
import shutil
import tempfile
import time

from project.edl import parse_edl_cuts
from project.vprj import save_vprj_from_cuts
from repair.comskip import run_comskip, ComskipError

log = logging.getLogger("vrd-next.watch")


class ProcessResult:
    """Outcome of handling one recording."""

    def __init__(self, source, vprj_path=None, cut_count=0, skipped_reason=None,
                 error=None):
        self.source = source
        self.vprj_path = vprj_path
        self.cut_count = cut_count
        self.skipped_reason = skipped_reason
        self.error = error

    @property
    def ok(self):
        return self.error is None and self.skipped_reason is None


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def iter_recordings(roots, pattern="*.ts"):
    """Yield recording paths under each root (recursively) matching pattern."""
    seen = set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if fnmatch.fnmatch(name.lower(), pattern.lower()):
                    full = os.path.join(dirpath, name)
                    if full not in seen:
                        seen.add(full)
                        yield full


def file_settled(path, settle_seconds, now=None):
    """True if the file hasn't been modified for at least settle_seconds, i.e.
    the recording has almost certainly finished.  Guards against scanning a
    recording that's still being written."""
    if settle_seconds <= 0:
        return True
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    now = now if now is not None else time.time()
    return (now - mtime) >= settle_seconds


def probe_duration(path):
    """Best-effort container duration in seconds (header read, no decode).
    Returns 0.0 if it can't be determined."""
    try:
        import av
        with av.open(path) as container:
            if container.duration:
                return float(container.duration) / 1_000_000.0
    except Exception:
        pass
    return 0.0


# --------------------------------------------------------------------------- #
# Ignore list
# --------------------------------------------------------------------------- #

def load_ignore_patterns(path):
    """Read the ignore list (one programme-title pattern per line).  Blank
    lines and lines starting with '#' are skipped."""
    patterns = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    except OSError:
        pass
    return patterns


def matches_ignore(filename, patterns):
    """True if the recording's file name contains any ignore pattern
    (case-insensitive substring match)."""
    name = os.path.basename(filename).lower()
    return any(p.lower() in name for p in patterns if p.strip())


# --------------------------------------------------------------------------- #
# Processed log
# --------------------------------------------------------------------------- #

class ProcessedLog:
    """Remembers which recordings have already been scanned (one path/line)."""

    def __init__(self, path):
        self.path = str(path)
        self._set = set()
        self.load()

    def load(self):
        self._set = set()
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._set.add(line)
        except OSError:
            pass

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                for p in sorted(self._set):
                    f.write(p + "\n")
        except OSError:
            pass

    def contains(self, path):
        return path in self._set

    def add(self, path):
        self._set.add(path)

    def prune_missing(self):
        """Drop entries whose recording no longer exists, so re-recording the
        same path later gets picked up again."""
        before = len(self._set)
        self._set = {p for p in self._set if os.path.exists(p)}
        return before - len(self._set)

    def __len__(self):
        return len(self._set)


# --------------------------------------------------------------------------- #
# Processing
# --------------------------------------------------------------------------- #

def process_recording(source, comskip_binary, comskip_ini, output_dir,
                      progress_cb=None, cancel_cb=None,
                      save_when_empty=True,
                      _run_comskip=run_comskip):
    """Run Comskip on one recording and write a .vprj of the commercials.

    Returns a ProcessResult.  When commercials are found, the project lists
    those cuts.  When none are found and ``save_when_empty`` is true (the
    default), a full-length project with an empty cut list is written anyway, so
    the recording still reaches the Batch Manager ready to review or copy; with
    it false, nothing is written (the old behaviour).  Never raises for a
    Comskip "no commercials" result; genuine failures are returned as
    result.error.
    """
    tmp_dir = tempfile.mkdtemp(prefix="vrd-next-watch-")
    try:
        try:
            edl_path = _run_comskip(
                comskip_binary, comskip_ini, source, tmp_dir,
                progress_cb=progress_cb, cancel_cb=cancel_cb,
            )
        except ComskipError as exc:
            return ProcessResult(source, error=str(exc))

        cuts = parse_edl_cuts(edl_path) if edl_path else []
        if not cuts and not save_when_empty:
            return ProcessResult(source, vprj_path=None, cut_count=0)

        os.makedirs(output_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(source))[0]
        vprj_path = os.path.join(output_dir, base + ".vprj")

        # An empty cut list is valid: save_vprj_from_cuts writes a project that
        # keeps the whole recording (no cuts), which is exactly what we want
        # when Comskip found no commercials.
        save_vprj_from_cuts(
            vprj_path, source, cuts,
            duration_seconds=probe_duration(source),
        )
        return ProcessResult(source, vprj_path=vprj_path, cut_count=len(cuts))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Scan orchestration
# --------------------------------------------------------------------------- #

def scan_once(cfg, processed, comskip_binary=None, comskip_ini=None,
              on_event=None, cancel_cb=None, pause_cb=None,
              ignore_patterns=None, _process=process_recording):
    """Scan all configured roots once.

    on_event(kind, result) is called as work proceeds, with kind one of:
        "processing" (result.source set, about to run Comskip)
        "done"       (a recording was scanned; result has vprj_path/cut_count)
        "skip"       (skipped; result.skipped_reason explains why)
        "error"      (result.error set)

    cancel_cb is a hard stop: it's also passed to Comskip, so the current file
    is abandoned immediately (used on Quit).  pause_cb is a graceful stop: it's
    checked only between files, so the file being processed runs to completion
    and the scan then stops before starting the next one (used on Pause).

    Returns a summary dict with counts.
    """
    cancel_cb = cancel_cb or (lambda: False)
    pause_cb = pause_cb or (lambda: False)
    ignore_patterns = ignore_patterns or []
    if comskip_binary is None or comskip_ini is None:
        comskip_binary, comskip_ini = cfg.comskip_paths()

    def emit(kind, result):
        if on_event:
            try:
                on_event(kind, result)
            except Exception:
                log.exception("watch on_event listener failed")

    seen_paths = []
    summary = {"scanned": 0, "projects": 0, "no_ads": 0,
               "skipped": 0, "errors": 0, "ignored": 0, "paused": False}

    log.info(
        "Scan starting: roots=%s, pattern=%s, settle=%ds, %d already on the "
        "processed list.",
        cfg.input_roots, cfg.pattern, cfg.settle_seconds, len(processed),
    )

    for source in iter_recordings(cfg.input_roots, cfg.pattern):
        if cancel_cb():
            log.info("Scan cancelled.")
            break
        # Graceful pause: the previous file (if any) has finished; stop before
        # starting the next one.
        if pause_cb():
            summary["paused"] = True
            log.info("Scan paused before the next file.")
            break
        seen_paths.append(source)
        name = os.path.basename(source)

        # Skip recordings on the ignore list (housemates' programmes, etc.).
        # Deliberately NOT marked processed, so removing a title from the list
        # later lets it be picked up.
        if matches_ignore(source, ignore_patterns):
            summary["ignored"] += 1
            log.info("Ignored (matches ignore list): %s", name)
            continue
        if processed.contains(source):
            # Already done in an earlier scan.  This is the decision that used
            # to be silent - logging it explains why a recording the user can
            # see in the folder is being left alone (remove it from
            # watch_processed.txt to have it picked up again).
            log.info("Skipped (already on the processed list): %s", name)
            continue
        if not file_settled(source, cfg.settle_seconds):
            summary["skipped"] += 1
            log.info(
                "Skipped (still recording - not untouched for %ds yet): %s",
                cfg.settle_seconds, name,
            )
            emit("skip", ProcessResult(source, skipped_reason="still recording"))
            continue

        log.info("Processing: %s", name)
        emit("processing", ProcessResult(source))
        result = _process(
            source, comskip_binary, comskip_ini, cfg.output_dir,
            cancel_cb=cancel_cb,
            save_when_empty=cfg.save_when_no_adverts,
        )

        if cancel_cb() and result.error:
            # Cancelled mid-Comskip - don't mark processed, try again next time.
            log.info("Processing cancelled mid-Comskip: %s", name)
            break

        if result.error:
            summary["errors"] += 1
            log.warning("Error processing %s: %s", name, result.error)
            emit("error", result)
            # Mark processed so a persistently-bad file doesn't jam every scan.
            processed.add(source)
            processed.save()
            continue

        processed.add(source)
        processed.save()
        summary["scanned"] += 1
        # "projects" tracks recordings that actually had commercials; a
        # full-length project saved for an advert-free recording still counts
        # as "no ads" so the stat stays meaningful.
        if result.cut_count > 0:
            summary["projects"] += 1
            log.info(
                "Done: %s - %d commercial break(s), project written to %s",
                name, result.cut_count, result.vprj_path or cfg.output_dir,
            )
        else:
            summary["no_ads"] += 1
            log.info("Done: %s - no commercials found.", name)
        emit("done", result)

    # Forget recordings that have since been deleted.
    processed.prune_missing()
    processed.save()
    log.info(
        "Scan finished: %d new, %d with commercials, %d advert-free, "
        "%d still recording, %d ignored, %d error(s).",
        summary["scanned"], summary["projects"], summary["no_ads"],
        summary["skipped"], summary["ignored"], summary["errors"],
    )
    return summary
