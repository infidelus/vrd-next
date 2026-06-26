from PySide6.QtCore import Qt


KEY_MAP = {

    #
    # Letters
    #

    **{

        chr(i): getattr(
            Qt,
            f"Key_{chr(i)}"
        )

        for i

        in range(
            ord("A"),
            ord("Z") + 1,
        )

    },

    #
    # Function keys
    #

    **{

        f"F{i}": getattr(
            Qt,
            f"Key_F{i}"
        )

        for i

        in range(
            1,
            13,
        )

    },

    #
    # Digits
    #

    **{

        str(i): getattr(
            Qt,
            f"Key_{i}"
        )

        for i

        in range(
            0,
            10,
        )

    },

    #
    # Named keys
    #

    "Delete": Qt.Key_Delete,

    # Both the main and numeric-keypad Enter keys, under either name users
    # reach for.
    "Return": (Qt.Key_Return, Qt.Key_Enter),
    "Enter": (Qt.Key_Return, Qt.Key_Enter),

    "Left": Qt.Key_Left,
    "Right": Qt.Key_Right,

    "Up": Qt.Key_Up,
    "Down": Qt.Key_Down,

    "Home": Qt.Key_Home,
    "End": Qt.Key_End,

    "PageUp": Qt.Key_PageUp,
    "PageDown": Qt.Key_PageDown,

    "Insert": Qt.Key_Insert,

    "Space": Qt.Key_Space,

}


# Spelling of multi-character key names as they appear in KEY_MAP, keyed by
# their lower-case form, so a config can use any case.
NORMALISE = {

    "home": "Home",
    "end": "End",

    "left": "Left",
    "right": "Right",

    "up": "Up",
    "down": "Down",

    "pageup": "PageUp",
    "pagedown": "PageDown",

    "delete": "Delete",

    "insert": "Insert",

    "return": "Return",
    "enter": "Enter",

    "space": "Space",

}


def _canonical_key_name(key_name):
    """Normalise a raw key name (the part after the last '+') to its KEY_MAP
    spelling, or return it unchanged if unrecognised."""
    if len(key_name) == 1:
        return key_name.upper()

    lowered = key_name.lower()
    if lowered in NORMALISE:
        return NORMALISE[lowered]

    # Function keys (F1..F12) in any case.
    if len(lowered) >= 2 and lowered[0] == "f" and lowered[1:].isdigit():
        return "F" + lowered[1:]

    return key_name


def parse_combo(combo):
    """Split a shortcut string into (frozenset_of_modifiers, key_name).

    Returns None if ``combo`` isn't a usable string or names a key the app
    can't match (so the caller can flag it as unrecognised).
    """
    if not isinstance(combo, str) or not combo.strip():
        return None

    parts = [p.strip() for p in combo.split("+")]
    mods = frozenset(
        p.lower() for p in parts[:-1]
        if p.lower() in ("ctrl", "shift", "alt")
    )
    key_name = _canonical_key_name(parts[-1])

    if key_name not in KEY_MAP:
        return None

    return (mods, key_name)


def find_shortcut_problems(shortcuts):
    """Inspect a shortcuts mapping for issues worth warning the user about.

    Returns (duplicates, unknown):
      * duplicates - list of (combo, [actions...]) where one key combination is
        bound to more than one action (only one will respond to the key).
      * unknown - list of (action, combo) whose key the app can't match, so the
        binding would silently never fire.
    """
    by_combo = {}
    unknown = []

    for action, combo in sorted(shortcuts.items()):
        parsed = parse_combo(combo)
        if parsed is None:
            if isinstance(combo, str) and combo.strip():
                unknown.append((action, combo))
            continue
        norm = (tuple(sorted(parsed[0])), parsed[1])
        by_combo.setdefault(norm, []).append((action, combo))

    duplicates = [
        (entries[0][1], [action for action, _ in entries])
        for entries in by_combo.values()
        if len(entries) > 1
    ]

    return duplicates, unknown


class ShortcutManager:

    def __init__(
            self,
            config,
    ):

        self.shortcuts = (
            config[
                "shortcuts"
            ]
        )

    def match(
            self,
            event,
            action,
    ):

        key = (

            self.shortcuts
            .get(
                action,
                ""
            )

        )

        if not isinstance(
                key,
                str,
        ):
            return False

        key = key.strip()

        parts = key.split(
            "+"
        )

        modifiers = Qt.NoModifier

        key_name = parts[-1]

        for part in parts[:-1]:

            part = part.strip().lower()

            if part == "ctrl":
                modifiers |= Qt.ControlModifier

            elif part == "shift":
                modifiers |= Qt.ShiftModifier

            elif part == "alt":
                modifiers |= Qt.AltModifier

        key_name = _canonical_key_name(key_name)

        if key_name not in KEY_MAP:
            return False

        expected = KEY_MAP[
            key_name
        ]

        # An entry may map a name to a single Qt key or to several (e.g. the
        # two physical Enter keys), so accept a match against any of them.
        if isinstance(expected, (tuple, list, set, frozenset)):
            if event.key() not in expected:
                return False
        elif event.key() != expected:
            return False

        #
        # Exact modifier match: the required modifiers must be present AND no
        # other relevant modifier may be held.  We mask to Ctrl/Shift/Alt so
        # that incidental flags (e.g. the keypad modifier Qt sets on arrow
        # keys) don't interfere.
        #

        relevant = (
            Qt.ControlModifier
            | Qt.ShiftModifier
            | Qt.AltModifier
        )

        held = event.modifiers() & relevant

        return held == (modifiers & relevant)