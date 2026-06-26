"""
A small persistent record of files that Quick Stream Fix has processed.

It stores a content fingerprint for each file VRD Next has either repaired
(used as a QSF source) or produced (a QSF output), so the app can recognise an
already-processed file and warn before repairing it a second time.

The fingerprint is content-based - file size plus a hash of the head and tail
of the file - so it still matches after a file is moved or renamed, yet stays
fast on multi-GB recordings (it reads only a few MB, not the whole file).

Every operation is best-effort: any I/O or parsing error is swallowed and
treated as "unknown", so the registry can never block, slow, or crash a repair.
"""

import hashlib
import json
import time
from pathlib import Path


REGISTRY_FILE = (
    Path.home()
    / ".cache"
    / "vrd-next"
    / "qsf_registry.json"
)

# Keep the registry bounded; the oldest entries fall off the front.
MAX_ENTRIES = 500

# How many bytes of the head and of the tail to hash.  Enough to be unique
# across distinct recordings while staying fast on large files.
_SAMPLE = 2 * 1024 * 1024


def fingerprint(path):
    """Return a content fingerprint (hex string) for the file, or None if it
    can't be read.

    The fingerprint combines the file size with a hash of the first and last
    _SAMPLE bytes - distinctive across different recordings, stable across
    moves/renames, and cheap to compute on multi-GB files.
    """
    try:
        size = Path(path).stat().st_size

        h = hashlib.sha1()
        h.update(str(size).encode("ascii"))

        with open(path, "rb") as f:
            h.update(f.read(_SAMPLE))

            # Hash the tail too (clamped so head and tail don't overlap on
            # small files).
            if size > _SAMPLE:
                f.seek(max(_SAMPLE, size - _SAMPLE))
                h.update(f.read(_SAMPLE))

        return h.hexdigest()

    except Exception:
        return None


def _load():
    """Return a list of {"fp": str, "ts": float} entries.

    Migrates the older format (a plain list of fingerprint strings) by giving
    each a current timestamp, so existing registries keep working.
    """
    try:
        with open(REGISTRY_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    now = time.time()
    entries = []
    for item in data:
        if isinstance(item, str):
            entries.append({"fp": item, "ts": now})
        elif isinstance(item, dict) and "fp" in item:
            entries.append({"fp": item["fp"], "ts": item.get("ts", now)})
    return entries


def _save(entries):
    try:
        REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY_FILE.write_text(json.dumps(entries))
    except Exception:
        pass


def is_qsfd(path):
    """True if this file's fingerprint is already recorded as QSF-processed."""
    fp = fingerprint(path)
    if fp is None:
        return False
    return any(e["fp"] == fp for e in _load())


def mark_qsfd(path):
    """Record this file's fingerprint as QSF-processed (best-effort)."""
    fp = fingerprint(path)
    if fp is None:
        return

    entries = [e for e in _load() if e["fp"] != fp]
    entries.append({"fp": fp, "ts": time.time()})

    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]

    _save(entries)


def prune(max_age_days):
    """Drop entries older than max_age_days.  A value <= 0 means keep forever.

    Best-effort: any failure is swallowed.  Returns the number removed.
    """
    try:
        if not max_age_days or max_age_days <= 0:
            return 0
        cutoff = time.time() - max_age_days * 86400
        entries = _load()
        kept = [e for e in entries if e["ts"] >= cutoff]
        if len(kept) != len(entries):
            _save(kept)
        return len(entries) - len(kept)
    except Exception:
        return 0


def clear_all():
    """Drop every Quick Stream Fix record now.  Best-effort; returns the
    number removed."""
    try:
        entries = _load()
        if entries:
            _save([])
        return len(entries)
    except Exception:
        return 0
