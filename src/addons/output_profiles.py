"""Output profiles: the named bundles of export settings the Save Video dialog
and the Profile Options manager share, plus their persistence in the config.

A profile currently carries:
  * name, container          - drive the export today
  * output_dir               - per-profile default destination (Save dialog)
  * audio, audio_bitrate     - lossless copy, or re-encode to AAC on export
  * aspect                   - display aspect, stamped losslessly on export
  * favourite, enabled, order - manager/list behaviour

All video is smart-cut (copied), so the codec column always reads
"Match Source" and the output mode always "Smart".
"""

from config.loader import save_config

_CONTAINER_LABELS = {
    "match": "Match Source",
    "mkv": "Matroska MKV",
    "mp4": "MP4",
}

AUDIO_MODES = ("copy", "aac")          # smart copy (lossless) | re-encode AAC
ASPECT_MODES = ("source", "4:3", "16:9")
AAC_AUTO = 0                            # 0 == let the bitrate follow the source
AAC_BITRATES = (128, 160, 192, 224, 256, 288, 315, 320, 384)


class OutputProfile:
    """One named output profile."""

    def __init__(self, name, container, *, audio="copy", audio_bitrate=AAC_AUTO,
                 aspect="source", output_dir="", favourite=False,
                 enabled=True, builtin=False):
        self.name = name
        self.container = container          # "match" | "mkv" | "mp4"
        self.audio = audio                  # "copy" | "aac"
        self.audio_bitrate = audio_bitrate  # kbps, or AAC_AUTO (0) for automatic
        self.aspect = aspect                # "source" | "4:3" | "16:9"
        self.output_dir = output_dir        # per-profile default destination
        self.favourite = favourite
        self.enabled = enabled
        self.builtin = builtin

    # -- display helpers for the list columns ------------------------------
    @property
    def codec_label(self):
        return "Match Source"               # VRD Next always copies the video

    @property
    def container_label(self):
        return _CONTAINER_LABELS.get(self.container, self.container)

    @property
    def output_mode_label(self):
        return "Smart"

    def audio_label(self):
        if self.audio == "aac":
            if self.audio_bitrate:
                return "Re-encode AAC %d kbps" % self.audio_bitrate
            return "Re-encode AAC (automatic)"
        return "Smart copy (lossless)"

    def aspect_label(self):
        return {"source": "Source", "4:3": "4:3", "16:9": "16:9"}.get(
            self.aspect, self.aspect
        )

    def extension(self, source_ext):
        """The output file extension.  ``match`` keeps the source's own
        extension (e.g. .ts for Freeview recordings)."""
        if self.container == "mkv":
            return ".mkv"
        if self.container == "mp4":
            return ".mp4"
        return source_ext or ".ts"

    # -- persistence -------------------------------------------------------
    def to_dict(self):
        return {
            "name": self.name,
            "container": self.container,
            "audio": self.audio,
            "audio_bitrate": self.audio_bitrate,
            "aspect": self.aspect,
            "output_dir": self.output_dir,
            "favourite": self.favourite,
            "enabled": self.enabled,
            "builtin": self.builtin,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            d.get("name", "Profile"),
            d.get("container", "match"),
            audio=d.get("audio", "copy"),
            audio_bitrate=int(d.get("audio_bitrate", AAC_AUTO)),
            aspect=d.get("aspect", "source"),
            output_dir=d.get("output_dir", ""),
            favourite=bool(d.get("favourite", False)),
            enabled=bool(d.get("enabled", True)),
            builtin=bool(d.get("builtin", False)),
        )

    def copy(self):
        return OutputProfile.from_dict(self.to_dict())


def default_profiles():
    """The built-in profiles seeded on first use."""
    return [
        OutputProfile("Match Source", "match", favourite=True, builtin=True),
        OutputProfile("Matroska MKV", "mkv", favourite=True, builtin=True),
        OutputProfile("MP4", "mp4", favourite=False, builtin=True),
    ]


def load_profiles(config):
    """Return the saved profiles, seeding the built-ins on first use."""
    raw = config.get("profiles")
    if not raw:
        return default_profiles()
    out = []
    for d in raw:
        try:
            out.append(OutputProfile.from_dict(d))
        except Exception:
            continue
    return out or default_profiles()


def save_profiles(config, profiles):
    """Persist the profile list to the config."""
    config["profiles"] = [p.to_dict() for p in profiles]
    try:
        save_config(config)
    except Exception:
        # Persisting profiles should never crash the dialog.
        pass


def profile_names(config):
    """Names of the saved profiles, in list order (for a picker)."""
    return [p.name for p in load_profiles(config)]


def default_profile_name(config):
    """A sensible default profile name for a new job: a favourite if there is
    one, else the first profile, else the built-in Match Source."""
    profiles = load_profiles(config)
    for p in profiles:
        if p.favourite and p.enabled:
            return p.name
    return profiles[0].name if profiles else "Match Source"


def resolve_profile(config, name):
    """The saved profile with this ``name``.

    If it's gone (renamed or deleted since a job was queued) fall back to a
    favourite, then the first profile, then a plain Match-Source profile - so a
    queued job can always still run rather than being orphaned by a profile
    edit.
    """
    profiles = load_profiles(config)
    for p in profiles:
        if p.name == name:
            return p
    for p in profiles:
        if p.favourite and p.enabled:
            return p
    return profiles[0] if profiles else OutputProfile("Match Source", "match")
