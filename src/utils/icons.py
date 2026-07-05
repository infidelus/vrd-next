"""Loader for the application's built-in SVG icon set.

Icons live in ``src/assets/icons`` as small SVGs drawn to a 24x24 box.  Every
button goes through :func:`load_icon` so a future "icon theme" feature can put a
user-chosen directory in front of the built-in set in one place, without having
to revisit each call site.
"""

import os

from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPalette
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


# The light-grey the glyphs are drawn in.  Only this colour is re-tinted to suit
# the theme; the blue play/pause accent and its white triangle are left alone.
_GLYPH_COLOUR = "#dcdcdc"


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


def _glyph_colour():
    """The colour the arrow glyphs should take in the current theme.

    Uses the application's ButtonText - dark on a light theme, light on a dark
    one - so the arrows are always visible.  Falls back to the built-in light
    grey before any application exists.
    """
    app = QApplication.instance()
    if app is not None:
        return app.palette().color(QPalette.ButtonText).name()
    return _GLYPH_COLOUR


def load_icon(name):
    """Return a cached :class:`QIcon` for ``name``, tinted to suit the theme.

    The bundled SVGs are drawn in a light grey; here that glyph colour is
    swapped for the current theme's text colour so the arrows stay visible on
    both light and dark backgrounds.  The blue play accent is left untouched.
    A missing file yields an empty icon rather than raising, so a typo can never
    take the UI down - the button simply shows no glyph.

    Icons are recoloured when they're loaded, so a theme change is reflected
    after a restart (as with the rest of the appearance switch).
    """
    colour = _glyph_colour()
    key = (name, colour)
    if key in _cache:
        return _cache[key]

    path = icon_path(name)
    if not os.path.exists(path):
        icon = QIcon()
        _cache[key] = icon
        return icon

    try:
        with open(path, "r", encoding="utf-8") as handle:
            svg = handle.read()
        if colour.lower() != _GLYPH_COLOUR:
            svg = svg.replace(_GLYPH_COLOUR, colour)
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        pixmap = QPixmap(64, 64)             # render large, Qt scales it down
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        icon = QIcon(pixmap)
    except Exception:
        icon = QIcon(path)                   # fall back to the raw SVG
    _cache[key] = icon
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
