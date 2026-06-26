"""A single batch job and the rules for naming its output.

A job is one saved .vprj project plus a chosen output format.  Where the output
is written is worked out from a shared destination folder, an optional name
modifier and the source recording's clean base name - mirroring VideoReDo's
Batch Builder, including its prefix/suffix-and-collision behaviour.
"""

import os


# Output formats offered in the batch manager, mapped to the value
# export_ranges expects and the file extension to use.  "match" produces a
# transport stream in the source's own container (.ts for broadcast).
FORMATS = ("ts", "mkv", "mp4")

_FORMAT_TO_EXPORT = {
    "ts": "match",
    "mkv": "mkv",
    "mp4": "mp4",
}

_FORMAT_TO_EXT = {
    "ts": ".ts",
    "mkv": ".mkv",
    "mp4": ".mp4",
}

# A profile's container ("match"/"mkv"/"mp4") maps to the batch format above,
# so the existing naming/extension rules carry over unchanged.
_CONTAINER_TO_FMT = {"match": "ts", "mkv": "mkv", "mp4": "mp4"}

# Pre-profile jobs stored only a container format; map it to the matching
# built-in profile so an existing queue keeps working after the upgrade.
_FORMAT_TO_PROFILE = {
    "ts": "Match Source",
    "mkv": "Matroska MKV",
    "mp4": "MP4",
}

DEFAULT_PROFILE_NAME = "Match Source"


def container_to_fmt(container):
    """The batch format ('ts'/'mkv'/'mp4') for a profile's container."""
    return _CONTAINER_TO_FMT.get(container, "ts")


# Job lifecycle states.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
NEEDS_REVIEW = "needs_review"   # held: needs repair + cut confirmation


# Decorations a working/QSF'd copy might carry, stripped so outputs are named
# after the original recording (kept in step with main._clean_basename).
_STRIP_PREFIXES = (
    "vrd-next-manual-fix-",
    "vrd-next-export-fix-",
    "vrd-next-",
)
_STRIP_SUFFIXES = (" - QSF", " - fixed", " - edited")


def clean_basename(path):
    """The recording's name with extension and any QSF/temp decoration
    stripped, e.g. '/tmp/Movie - QSF.ts' -> 'Movie'."""
    base = os.path.splitext(os.path.basename(path or ""))[0]
    for prefix in _STRIP_PREFIXES:
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    for suffix in _STRIP_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base


def export_format(fmt):
    """The value export_ranges expects for a batch format ('ts'/'mkv'/'mp4')."""
    return _FORMAT_TO_EXPORT.get(fmt, "match")


def format_ext(fmt, source_path=""):
    """The output extension for a batch format.  For 'ts' we follow the
    source's own extension (a .ts recording stays .ts), defaulting to .ts."""
    if fmt == "ts":
        ext = os.path.splitext(source_path)[1]
        return ext or ".ts"
    return _FORMAT_TO_EXT.get(fmt, ".ts")


def apply_modifier(base, modifier):
    """Apply a VRD-style name modifier to a base name.

    The modifier prefixes the name, UNLESS it begins with '-' or '_', in which
    case it is appended as a suffix instead.  An empty modifier leaves the
    name unchanged.
    """
    modifier = (modifier or "").strip()
    if not modifier:
        return base
    if modifier[0] in ("-", "_"):
        return f"{base}{modifier}"
    return f"{modifier}{base}"


def build_dest_path(folder, modifier, source_path, fmt, taken=None):
    """Build the output path for a job.

    Combines `folder`, the modifier-applied clean base name of `source_path`
    and the format's extension.  If that exact path already exists on disk, or
    is in `taken` (paths already claimed by earlier jobs in this batch), a
    ' (2)', ' (3)' … is inserted before the extension so no job silently
    overwrites another's output.
    """
    base = apply_modifier(clean_basename(source_path), modifier)
    ext = format_ext(fmt, source_path)
    taken = taken or set()

    candidate = os.path.join(folder, f"{base}{ext}")
    n = 2
    while os.path.exists(candidate) or candidate in taken:
        candidate = os.path.join(folder, f"{base} ({n}){ext}")
        n += 1
    return candidate


class BatchJob:
    """One project queued for processing.

    Only `vprj_path` and `profile_name` need persisting; everything else is
    resolved or recomputed when a batch runs.  The profile (resolved from the
    config by name at run time) supplies the container, audio handling and
    display aspect.
    """

    def __init__(self, vprj_path, profile_name=DEFAULT_PROFILE_NAME):
        self.vprj_path = vprj_path
        self.profile_name = profile_name or DEFAULT_PROFILE_NAME

        # Filled in as the job is resolved / processed.
        self.source_path = None      # resolved, existing recording
        self.dest_path = None        # where the output was/will be written
        self.status = QUEUED
        self.message = ""            # error detail when FAILED
        self.percent = 0             # 0-100 within the current job

    # -- persistence -------------------------------------------------------- #

    def to_dict(self):
        # A job that was mid-flight when we persisted didn't actually finish,
        # so record it as queued rather than running.
        status = QUEUED if self.status == RUNNING else self.status
        return {
            "vprj_path": self.vprj_path,
            "profile_name": self.profile_name,
            "status": status,
            "message": self.message,
            "dest_path": self.dest_path or "",
        }

    @classmethod
    def from_dict(cls, data):
        # Prefer the stored profile name; fall back to migrating a pre-profile
        # job's container format to the matching built-in profile.
        name = data.get("profile_name")
        if not name:
            old_fmt = data.get("out_format", "ts")
            name = _FORMAT_TO_PROFILE.get(old_fmt, DEFAULT_PROFILE_NAME)
        job = cls(
            data.get("vprj_path", ""),
            name,
        )
        status = data.get("status", QUEUED)
        if status == RUNNING:
            status = QUEUED
        job.status = status if status in (
            QUEUED, DONE, FAILED, CANCELLED, NEEDS_REVIEW
        ) else QUEUED
        job.message = data.get("message", "")
        job.dest_path = data.get("dest_path") or None
        if job.status == DONE:
            job.percent = 100
        return job

    # -- display ------------------------------------------------------------ #

    @property
    def name(self):
        return os.path.basename(self.vprj_path)
