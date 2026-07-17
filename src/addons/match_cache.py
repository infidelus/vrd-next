"""Persistent cache of renamer matches, keyed by file path.

Matching a file means a TMDB lookup, and once we know which film or episode a
file is there's no reason to ask again - not when the folder is refreshed, and
not after the application is relaunched.  This remembers each match against its
file's absolute path so a re-match only queries TMDB for files that aren't
matched yet.

Two namespaces are kept: "tv" (the rendered episode meta) and "film" (the
matched movie).  Each entry records the file size (so a different file taking a
seen path isn't given the wrong match) and a timestamp (so old matches can be
purged - see purge_old).

The cache lives in its own file (renamer-cache.json) rather than in
settings.json: it's bulky and disposable, and keeping it separate stops it
bloating the real settings.  A one-time migration lifts any cache that older
versions stored inside the main config.
"""

import os
import time

from config.loader import load_sidecar, save_sidecar

_CACHE_FILE = "renamer-cache.json"
_CAP = 5000          # most-recent entries kept per namespace

# Loaded once, lazily, and written back on save().  Structure:
#   {"tv": {path: {size, data, checked, ts}}, "film": {...}}
_cache = None


def _load(config=None):
    """Return the in-memory cache, loading it (and migrating any in-config
    cache from older versions) on first use."""
    global _cache
    if _cache is not None:
        return _cache
    _cache = load_sidecar(_CACHE_FILE, default={})
    # One-time migration: older versions kept the cache under
    # config["renamer_match_cache"].  Lift it out and drop it from the config.
    if config is not None:
        legacy = config.pop("renamer_match_cache", None)
        if legacy and not _cache:
            _cache = legacy
            save_sidecar(_CACHE_FILE, _cache)
            try:
                from config.loader import save_config
                save_config(config)      # persist the removal from settings.json
            except Exception:
                pass
    return _cache


def _bucket(config, kind):
    return _load(config).setdefault(kind, {})


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def get_match(config, kind, path):
    """The cached match for ``path``, if present and the file looks unchanged
    (same size); otherwise None."""
    entry = _bucket(config, kind).get(os.path.abspath(path))
    if not entry or entry.get("size") != _size(path):
        return None
    return entry.get("data")


def put_match(config, kind, path, data, checked=None):
    """Remember ``data`` as the match for ``path``.

    ``checked`` records whether the user has the file ticked for renaming, so an
    un-tick survives a refresh or relaunch.  Passing None leaves the stored tick
    state untouched (a new entry defaults to ticked), so callers that only mean
    to refresh the match data don't reset the user's ticks."""
    bucket = _bucket(config, kind)
    key = os.path.abspath(path)
    prev = bucket.get(key) or {}
    bucket.pop(key, None)                 # re-insert so it counts as most recent
    if checked is None:
        checked_val = prev.get("checked", True)
    else:
        checked_val = bool(checked)
    bucket[key] = {
        "size": _size(path),
        "data": data,
        "checked": checked_val,
        "ts": time.time(),
    }
    while len(bucket) > _CAP:             # drop the oldest beyond the cap
        del bucket[next(iter(bucket))]


def get_checked(config, kind, path, default=True):
    """The remembered tick state for ``path`` (True/False), or ``default`` if
    there's no usable cached entry for it."""
    entry = _bucket(config, kind).get(os.path.abspath(path))
    if not entry or entry.get("size") != _size(path):
        return default
    return bool(entry.get("checked", default))


def forget_match(config, kind, path):
    _bucket(config, kind).pop(os.path.abspath(path), None)


def purge_old(config, max_age_days):
    """Remove cache entries older than ``max_age_days``.  0 (or less) keeps
    everything.  Returns the number of entries removed.

    Entries from before timestamps were recorded have no ``ts`` and are treated
    as brand new (never purged on age) - they'll gain a timestamp next time
    they're matched, so nothing is lost by being cautious here."""
    try:
        days = float(max_age_days)
    except (TypeError, ValueError):
        return 0
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    cache = _load(config)
    removed = 0
    for kind, bucket in cache.items():
        for key in [k for k, e in bucket.items()
                    if isinstance(e, dict) and "ts" in e and e["ts"] < cutoff]:
            del bucket[key]
            removed += 1
    if removed:
        save_sidecar(_CACHE_FILE, cache)
    return removed


def clear(config=None):
    """Empty the cache entirely (for a Settings 'clear now' action)."""
    global _cache
    _cache = {}
    save_sidecar(_CACHE_FILE, _cache)


def save(config=None):
    if _cache is not None:
        save_sidecar(_CACHE_FILE, _cache)
