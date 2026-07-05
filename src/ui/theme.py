"""Light / dark theming for VRD Next.

By default the editor follows the desktop's own colours (on Linux Mint, whatever
GTK theme is set).  These helpers let the user pin a **Light** or **Dark** look
from Settings instead, by switching to Qt's Fusion style with an explicit
palette; **System** restores the desktop's own style and palette exactly.

The timeline, thumbnail and scene bars keep their own dark video-editor colours
in every theme - that's deliberate, and conventional in editors, where a dark
work area is easier on the eyes regardless of the surrounding chrome.
"""

from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QStyleFactory

# Captured once at startup so "System" can restore the desktop's own look
# exactly, rather than an approximation of it.
_original_palette = None
_original_style_name = None

# Tooltip styling per theme, kept readable on each background.
_TOOLTIP = {
    "dark": ("QToolTip { color:#f0f0f0; background-color:#2b2b2b;"
             " border:1px solid #555; padding:4px 6px; }"),
    "light": ("QToolTip { color:#202020; background-color:#ffffe1;"
              " border:1px solid #b0b0a0; padding:4px 6px; }"),
}


def remember_original(app):
    """Record the application's starting style and palette, once.

    Called before any theme is applied, so a later switch back to "System" can
    reinstate the desktop's own appearance rather than a guess at it.
    """
    global _original_palette, _original_style_name
    if _original_palette is None:
        _original_palette = QPalette(app.palette())
        _original_style_name = app.style().name()


def _dark_palette():
    p = QPalette()
    window = QColor(53, 53, 53)
    base = QColor(35, 35, 35)
    text = QColor(220, 220, 220)
    disabled = QColor(127, 127, 127)
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, window)
    p.setColor(QPalette.ToolTipBase, QColor(43, 43, 43))
    p.setColor(QPalette.ToolTipText, QColor(240, 240, 240))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, window)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(255, 80, 80))
    p.setColor(QPalette.Highlight, QColor(42, 130, 218))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.Link, QColor(90, 160, 240))
    p.setColor(QPalette.PlaceholderText, QColor(150, 150, 150))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


def _light_palette():
    p = QPalette()
    window = QColor(240, 240, 240)
    base = QColor(255, 255, 255)
    text = QColor(30, 30, 30)
    disabled = QColor(160, 160, 160)
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
    p.setColor(QPalette.ToolTipBase, QColor(255, 255, 225))
    p.setColor(QPalette.ToolTipText, QColor(30, 30, 30))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, window)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(200, 0, 0))
    p.setColor(QPalette.Highlight, QColor(42, 130, 218))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.Link, QColor(30, 90, 200))
    p.setColor(QPalette.PlaceholderText, QColor(130, 130, 130))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


def apply_theme(app, mode, wrap_style=None):
    """Apply ``mode`` ("system" | "light" | "dark") to the running application.

    ``wrap_style`` optionally wraps the chosen QStyle before it's installed -
    VRD Next passes its slow-tooltip proxy so the hover delay is preserved.
    ``remember_original`` is called first, so "system" always has the desktop's
    original look to restore.
    """
    remember_original(app)

    if mode == "dark":
        style_name, palette, tip = "Fusion", _dark_palette(), _TOOLTIP["dark"]
    elif mode == "light":
        style_name, palette, tip = "Fusion", _light_palette(), _TOOLTIP["light"]
    else:                                       # "system" (or anything unknown)
        style_name = _original_style_name
        palette = _original_palette
        tip = _TOOLTIP["dark"]

    base = QStyleFactory.create(style_name) if style_name else None
    if base is not None:
        app.setStyle(wrap_style(base) if wrap_style is not None else base)
    if palette is not None:
        app.setPalette(palette)
    app.setStyleSheet(tip)
