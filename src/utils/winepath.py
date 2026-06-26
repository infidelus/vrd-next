"""Resolving a recording's real path from what a project file stored.

A .vprj saved under VideoReDo (run via WINE) records a Windows-style path such
as ``H:\\Recordings\\file.ts``.  Saved under VRD Next it records a normal Linux
path.  These helpers turn either into a real, existing file path, so the import
flow and the batch manager resolve sources the same way.
"""

import os


def translate_wine_path(win_path):
    """Translate a Windows path (e.g. ``H:\\dir\\file.ts``) to a Linux path
    using the WINE drive mappings in ``~/.wine/dosdevices``, if present.

    Returns the translated path (whether or not it exists), or None if it
    can't be resolved (not a drive-letter path, or no matching WINE mapping).
    """
    if not win_path:
        return None

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

    return os.path.join(target, rest)


def resolve_source(embedded_path):
    """Find the real, existing video file for a project's stored path.

    Tries, in order: the embedded path as-is; a WINE drive-mapping translation
    if it's a Windows path.  Returns an existing path, or None if neither
    resolves.
    """
    if embedded_path and os.path.isfile(embedded_path):
        return embedded_path

    if embedded_path and len(embedded_path) >= 2 and embedded_path[1] == ":":
        translated = translate_wine_path(embedded_path)
        if translated and os.path.isfile(translated):
            return translated

    return None
