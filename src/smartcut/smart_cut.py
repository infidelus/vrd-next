import os
from collections.abc import Callable
from fractions import Fraction
from typing import Protocol, TypeAlias

import av
from av.container.output import OutputContainer
from av.packet import Packet

from smartcut.media_container import MediaContainer
from smartcut.media_utils import VideoExportMode
from smartcut.misc_data import AudioExportInfo, CutSegment
from smartcut.track_cutters import (
    PassthruAudioCutter,
    SubtitleCutter,
    create_audio_output_stream,
    create_subtitle_output_stream,
)
from smartcut.video_cutter import (
    VideoCutter,
    VideoSettings,
    create_video_output_stream,
)

__version__ = "1.7"


class ProgressCallback(Protocol):
    """Protocol for progress callback objects."""
    def emit(self, value: int) -> None:
        """Emit progress update."""
        ...


class StreamGenerator(Protocol):
    """Protocol for stream generators that produce packets for output."""
    def segment(self, cut_segment: CutSegment) -> list[Packet]: ...
    def finish(self) -> list[Packet]: ...


StreamGeneratorFactory: TypeAlias = Callable[[OutputContainer], StreamGenerator]


class CancelObject:
    cancelled: bool = False


def make_adjusted_segment_times(positive_segments: list[tuple[Fraction, Fraction]], media_container: MediaContainer) -> list[tuple[Fraction, Fraction]]:
    adjusted_segment_times = []
    EPSILON = Fraction(1, 1_000_000)
    for (s, e) in positive_segments:
        if s <= EPSILON:
            s = -10
        if e >= media_container.duration - EPSILON:
            e = media_container.duration + 10
        adjusted_segment_times.append((s + media_container.start_time, e + media_container.start_time))
    return adjusted_segment_times

def make_cut_segments(media_container: MediaContainer,
        positive_segments: list[tuple[Fraction, Fraction]],
        keyframe_mode: bool = False
        ) -> list[CutSegment]:
    cut_segments = []
    if media_container.video_stream is None:
        first_audio_track = media_container.audio_tracks[0]
        min_time = first_audio_track.frame_times[0]
        max_time = first_audio_track.frame_times[-1] + Fraction(1,10000)
        for p in positive_segments:
            s = max(p[0], min_time)
            e = min(p[1], max_time)
            while s + 20 < e:
                cut_segments.append(CutSegment(False, s, s + 19))
                s += 19
            cut_segments.append(CutSegment(False, s, e))
        return cut_segments

    source_cutpoints = [*media_container.gop_start_times_pts_s, media_container.start_time + media_container.duration + Fraction(1,10000)]
    p = 0
    for gop_idx, (i, o, i_dts, o_dts) in enumerate(zip(source_cutpoints[:-1], source_cutpoints[1:], media_container.gop_start_times_dts, media_container.gop_end_times_dts)):
        while p < len(positive_segments) and positive_segments[p][1] <= i:
            p += 1

        # Three cases: no overlap, complete overlap, and partial overlap
        if p == len(positive_segments) or o <= positive_segments[p][0]:
            pass
        elif keyframe_mode or (i >= positive_segments[p][0] and o <= positive_segments[p][1]):
            cut_segments.append(CutSegment(False, i, o, i_dts, o_dts, gop_idx))
        else:
            if i > positive_segments[p][0]:
                cut_segments.append(CutSegment(True, i, positive_segments[p][1], i_dts, o_dts, gop_idx))
                p += 1
            while p < len(positive_segments) and positive_segments[p][1] < o:
                cut_segments.append(CutSegment(True, positive_segments[p][0], positive_segments[p][1], i_dts, o_dts, gop_idx))
                p += 1
            if p < len(positive_segments) and positive_segments[p][0] < o:
                cut_segments.append(CutSegment(True, positive_segments[p][0], o, i_dts, o_dts, gop_idx))

    return cut_segments


def smart_cut(media_container: MediaContainer, positive_segments: list[tuple[Fraction, Fraction]],
              out_path: str, audio_export_info: AudioExportInfo | None = None, log_level: str | None = None, progress: ProgressCallback | None = None,
              video_settings: VideoSettings | None = None, segment_mode: bool = False, cancel_object: CancelObject | None = None,
              external_generator_factories: list[StreamGeneratorFactory] | None = None) -> Exception | None:
    if video_settings is None:
        video_settings = VideoSettings(VideoExportMode.SMARTCUT, VideoExportQuality.NORMAL)

    adjusted_segment_times = make_adjusted_segment_times(positive_segments, media_container)
    cut_segments = make_cut_segments(media_container, adjusted_segment_times, video_settings.mode == VideoExportMode.KEYFRAMES)

    if video_settings.mode == VideoExportMode.RECODE:
        for c in cut_segments:
            c.require_recode = True

    if segment_mode:
        output_files = []
        padding = len(str(len(adjusted_segment_times)))
        for i, s in enumerate(adjusted_segment_times):
            segment_index = str(i + 1).zfill(padding)  # Zero-pad the segment index
            if "#" in out_path:
                pound_index = out_path.rfind("#")
                output_file = out_path[:pound_index] + segment_index + out_path[pound_index + 1:]
            else:
                # Insert the segment index right before the last '.'
                dot_index = out_path.rfind(".")
                output_file = out_path[:dot_index] + segment_index + out_path[dot_index:] if dot_index != -1 else f"{out_path}{segment_index}"

            output_files.append((output_file, s))

    else:
        output_files = [(out_path, adjusted_segment_times[-1])]
    previously_done_segments = 0
    for output_path_segment in output_files:
        if cancel_object is not None and cancel_object.cancelled:
            break
        with av.open(output_path_segment[0], 'w') as output_av_container:
            output_av_container.metadata['ENCODED_BY'] = f'smartcut {__version__}'

            include_video = True
            if output_av_container.format.name in ['ogg', 'mp3', 'm4a', 'ipod', 'flac', 'wav']: #ipod is the real name for m4a, I guess
                include_video = False

                        # Preserve container attachments (e.g., MKV attachments) when supported by the output format
            container_name = (output_av_container.format.name or "").lower()
            supports_attachments = any(x in container_name for x in ("matroska", "webm"))

            if supports_attachments:
                # Copy attachment streams from the primary input container
                for in_stream in media_container.av_container.streams:
                    if getattr(in_stream, "type", None) != "attachment":
                        continue

                    output_av_container.add_stream_from_template(in_stream)

            generators = []
            if media_container.video_stream is not None and include_video:
                video_stream_setup = create_video_output_stream(media_container, output_av_container, video_settings)
                generators.append(VideoCutter(media_container, video_stream_setup, output_av_container, video_settings, log_level))

            if external_generator_factories:
                for factory in external_generator_factories:
                    generators.append(factory(output_av_container))

            if audio_export_info is not None:
                for track_i, track_export_settings in enumerate(audio_export_info.output_tracks):
                    if track_export_settings is not None and track_export_settings.codec == 'passthru':
                        audio_out_stream = create_audio_output_stream(media_container, output_av_container, track_i)
                        generators.append(PassthruAudioCutter(media_container, audio_out_stream, track_i))

            for sub_track_i in range(len(media_container.subtitle_tracks)):
                subtitle_out_stream = create_subtitle_output_stream(media_container, output_av_container, sub_track_i)
                generators.append(SubtitleCutter(media_container, subtitle_out_stream, sub_track_i))

            output_av_container.start_encoding()
            if progress is not None:
                progress.emit(len(cut_segments))
            for s in cut_segments[previously_done_segments:]:
                if cancel_object is not None and cancel_object.cancelled:
                    break
                if s.start_time >= output_path_segment[1][1]: # Go to the next output file
                    break

                if progress is not None:
                    progress.emit(previously_done_segments)
                previously_done_segments += 1
                assert s.start_time < s.end_time, f"Invalid segment: start_time {s.start_time} >= end_time {s.end_time}"
                for g in generators:
                    for packet in g.segment(s):
                        if packet.dts is not None and packet.dts < -900_000:
                            packet.dts = None
                        if packet.dts is not None and packet.dts > 1_000_000_000_000:
                            print(f"BAD DTS: seg {s.start_time:.3f}-{s.end_time:.3f} gop={s.gop_index} recode={s.require_recode} pts={packet.pts} dts={packet.dts}")
                        output_av_container.mux(packet)
            for g in generators:
                for packet in g.finish():
                    if packet.dts is not None and packet.dts > 1_000_000_000_000:
                        print(f"BAD DTS in finish: pts={packet.pts} dts={packet.dts}", flush=True)
                    output_av_container.mux(packet)
            if progress is not None:
                progress.emit(previously_done_segments)

        if cancel_object is not None and cancel_object.cancelled:
            last_file_path = output_path_segment[0]

            if os.path.exists(last_file_path):
                os.remove(last_file_path)


# Re-export commonly used types for convenience
from smartcut.media_utils import VideoExportQuality  # noqa: E402
