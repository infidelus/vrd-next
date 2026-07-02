"""Locate the external ffmpeg / ffprobe used across export, join and probing.

VRD Next shells out to ffmpeg and ffprobe from many places, always as the bare
command name so the operating system resolves them on PATH.  Rather than thread
an explicit path through every call site, the user can point at a specific build
in Settings > External tools and we simply prepend that build's folder to PATH
for this process - at startup and again whenever Settings is saved.  With
nothing configured, whatever is already on PATH is used, so a normal install
needs no setup at all.
"""

import os
import shutil
import logging

log = logging.getLogger("vrd-next")

# The external tools VRD Next expects, paired with the config key that holds a
# user-chosen override for each.
_TOOLS = (
    ("ffmpeg", "ffmpeg_binary"),
    ("ffprobe", "ffprobe_binary"),
)


def apply_tool_paths(config):
    """Prepend any user-set ffmpeg/ffprobe folders to PATH for this process.

    Called at startup and after Settings is saved, so a freshly-chosen build
    takes effect without a restart.  A configured path that no longer exists is
    ignored (we fall back to PATH), and a folder already on PATH is not added
    again.
    """
    paths = (config or {}).get("paths", {})
    for _name, key in _TOOLS:
        chosen = (paths.get(key) or "").strip()
        if not chosen or not os.path.isfile(chosen):
            continue
        folder = os.path.dirname(os.path.abspath(chosen))
        current = os.environ.get("PATH", "").split(os.pathsep)
        if folder and folder not in current:
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
            log.info("tools: using %s from %s", _name, folder)


def tool_issues(config):
    """Return human-readable problems with the ffmpeg/ffprobe setup, if any.

    Two distinct problems are reported so the user isn't left guessing:

      * a configured path that points at a file that doesn't exist - the
        override is being ignored and whatever is on PATH (possibly nothing,
        possibly a different build) is used instead; and
      * a tool that can't be found at all, meaning export/join/probe will fail.

    Applies the configured folders to PATH first so the check is accurate.
    """
    apply_tool_paths(config)
    paths = (config or {}).get("paths", {})
    issues = []
    for name, key in _TOOLS:
        chosen = (paths.get(key) or "").strip()
        if chosen and not os.path.isfile(chosen):
            issues.append(
                "The %s path you've set doesn't exist, so it's being "
                "ignored:\n    %s" % (name, chosen)
            )
        if shutil.which(name) is None:
            issues.append(
                "%s could not be found - install it, or set a valid path in "
                "Settings > External tools." % name
            )
    return issues
