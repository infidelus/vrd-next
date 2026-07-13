"""Helpers for building Qt file-dialog filter strings.

On Linux, Qt's native file picker is case-sensitive: a filter of ``*.mp4``
will not match ``VIDEO.MP4``.  ``video_filter`` expands each extension to
both its lower- and upper-case forms so capital-extension files are visible
by default, without having to switch to "All files" first.

Usage::

    from utils.filters import video_filter

    VIDEO_OPEN_FILTER = video_filter(
        "Videos", ".ts", ".m2ts", ".mkv", ".mp4", ".mov", ".avi"
    )
    # -> "Videos (*.ts *.TS *.m2ts *.M2TS *.mkv *.MKV ...);;All files (*)"
"""


def video_filter(label, *exts):
    """Return a Qt file-dialog filter string that matches extensions in both
    lower and upper case, with "All files (*)" as a fallback.

    *exts* should be lowercase, with the leading dot (e.g. ``".mp4"``).
    Extensions whose lower- and upper-case forms are identical are listed once.
    """
    globs = []
    for ext in exts:
        lower = "*" + ext.lower()
        upper = "*" + ext.upper()
        globs.append(lower)
        if upper != lower:
            globs.append(upper)
    return "%s (%s);;All files (*)" % (label, " ".join(globs))
