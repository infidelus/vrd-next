"""Single source of truth for the application's name and version."""

APP_NAME = "VRD Next"

# major.minor.patch
VERSION = "1.0.11"

# Shown in the title bar, About box and logs, e.g. "VRD Next 1.0.2".
VERSION_STRING = f"{APP_NAME} {VERSION}"


def build_stamp():
    """A single rising integer derived from VERSION.

    Some saved project files carry a numeric build field; this gives them one
    that climbs monotonically across releases, e.g. 1.0.2 -> 10002, 1.1.0 ->
    10100, 2.0.0 -> 20000.  Nothing reads it back - it only records which
    version produced the file.
    """
    try:
        major, minor, patch = (int(part) for part in VERSION.split("."))
    except ValueError:
        return 0
    return major * 10000 + minor * 100 + patch
