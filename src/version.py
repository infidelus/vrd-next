"""
Single source of truth for the application's name and version.

The build number is bumped as the project changes so that artefacts we write
(e.g. project files) record which version produced them.  It is independent of
VideoReDo's own version numbers.
"""

APP_NAME = "VRD Next"

# Semantic-ish version for display.
VERSION = "1.0.0"

# Monotonic build number, bumped as features land.  Kept simple for now; can
# later be wired to a git commit count or CI build if desired.
BUILD_NUMBER = 2

# Convenience string, e.g. "VRD Next 0.1.0 (build 1)".
VERSION_STRING = f"{APP_NAME} {VERSION} (build {BUILD_NUMBER})"
