from dataclasses import dataclass, field
from fractions import Fraction


@dataclass
class AudioExportSettings:
    codec: str
    channels: str | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    denoise: int = -1

@dataclass
class AudioExportInfo:
    output_tracks: list[AudioExportSettings | None] = field(default_factory=lambda: [])

@dataclass
class CutSegment:
    require_recode: bool
    start_time: Fraction
    end_time: Fraction
    gop_start_dts: int = -1
    gop_end_dts: int = -1
    gop_index: int = -1
