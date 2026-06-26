"""Loader for the application's built-in SVG icon set.

Icons live in ``src/assets/icons`` as small SVGs drawn to a 24x24 box.  Every
button goes through :func:`load_icon` so a future "icon theme" feature can put a
user-chosen directory in front of the built-in set in one place, without having
to revisit each call site.
"""

import os

from PySide6.QtGui import QIcon


_BUILTIN_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "assets", "icons")
)

_cache = {}


def builtin_icon_dir():
    """Absolute path to the bundled icon directory."""
    return _BUILTIN_DIR


def icon_path(name):
    """Absolute path to a built-in icon by name (without the .svg extension)."""
    return os.path.join(_BUILTIN_DIR, name + ".svg")


def load_icon(name):
    """Return a cached :class:`QIcon` for ``name``.

    A missing file yields an empty icon rather than raising, so a typo can never
    take the UI down - the button simply shows no glyph.
    """
    if name in _cache:
        return _cache[name]

    path = icon_path(name)
    icon = QIcon(path) if os.path.exists(path) else QIcon()
    _cache[name] = icon
    return icon


def clear_cache():
    """Drop cached icons (for when a future theme switch needs a reload)."""
    _cache.clear()


def app_icon():
    """QIcon for the application (window / taskbar).  Lives one level up from
    the button icons, in ``src/assets``."""
    path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "assets", "app_icon.svg")
    )
    return QIcon(path) if os.path.exists(path) else QIcon()
