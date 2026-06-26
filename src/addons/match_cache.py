"""Persistent cache of renamer matches, keyed by file path.

Matching a file means a TMDB lookup, and once we know which film or episode a
file is there's no reason to ask again - not when the folder is refreshed, and
not after the application is relaunched.  This remembers each match against its
file's absolute path in the app config, so a re-match only queries TMDB for
files that aren't matched yet.

Two namespaces are kept: "tv" (the rendered episode meta) and "film" (the
matched movie).  Each entry also records the file size, so if a different file
ever takes a path that's been seen before, the stale match is ignored rather
than applied to the wrong file.
"""

import os

from config.loader import save_config

_CAP = 5000          # most-recent entries kept per namespace


def _bucket(config, kind):
    return config.setdefault("renamer_match_cache", {}).setdefault(kind, {})


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
    bucket[key] = {"size": _size(path), "data": data, "checked": checked_val}
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


def save(config):
    try:
        save_config(config)
    except Exception:
        pass
