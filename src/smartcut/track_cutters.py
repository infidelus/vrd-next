from fractions import Fraction
from typing import cast

import numpy as np
from av import AudioStream
from av.container.output import OutputContainer
from av.packet import Packet
from av.stream import Disposition, Stream

from smartcut.latm import LatmError, LatmRepacketiser
from smartcut.media_container import MediaContainer
from smartcut.misc_data import CutSegment
from smartcut.video_cutter import copy_packet


def create_audio_output_stream(
    media_container: MediaContainer,
    output_av_container: OutputContainer,
    track_index: int,
) -> AudioStream:
    """
    Create output audio stream from source media container.

    Separated from PassthruAudioCutter to allow reuse in joining operations.

    Broadcast AAC arrives wrapped in LATM/LOAS framing, which nothing but
    another transport stream can carry as-is - a naive packet copy of it is
    exactly what used to force the exporter's audio graft.  When the source
    track is aac_latm and its framing is the supported broadcast profile, the
    output stream is created as plain AAC instead: PassthruAudioCutter then
    unwraps each LOAS frame to ADTS (byte-identical payload, different
    wrapper), which every container, player and mkvmerge accept directly.
    Anything unexpected falls back to the template copy exactly as before,
    with the exporter's decode check and graft still behind it.
    """
    track = media_container.audio_tracks[track_index]
    in_stream = track.av_stream
    if getattr(in_stream.codec_context, "name", "") == "aac_latm" \
            and track.packets:
        try:
            rep = LatmRepacketiser(bytes(track.packets[0]))
            out_stream = output_av_container.add_stream(
                "aac", rate=rep.sample_rate)
            out_stream.time_base = in_stream.time_base
            out_stream.metadata.update(in_stream.metadata)
            out_stream.disposition = cast(
                Disposition, in_stream.disposition.value)
            return out_stream
        except LatmError as e:
            print(f"LATM repacketise unavailable for track {track_index} "
                  f"({e}); copying as-is")
    out_stream = output_av_container.add_stream_from_template(
        track.av_stream,
        options={'x265-params': 'log_level=error'}
    )
    out_stream.metadata.update(track.av_stream.metadata)
    out_stream.disposition = cast(Disposition, track.av_stream.disposition.value)
    return out_stream


class PassthruAudioCutter:
    def __init__(
        self,
        media_container: MediaContainer,
        out_stream: AudioStream,
        track_index: int,
        initial_position: Fraction = Fraction(0),
        initial_prev_dts: int = -100_000,
        initial_prev_pts: int = -100_000,
    ) -> None:
        self.track = media_container.audio_tracks[track_index]
        self.out_stream = out_stream

        # When the source is LATM-wrapped broadcast AAC and the output stream
        # was created as plain AAC (see create_audio_output_stream), each
        # copied frame is unwrapped to ADTS - the payload bytes are untouched,
        # only the wrapper changes.  Any other pairing copies packets as-is.
        self._latm = None
        in_name = getattr(self.track.av_stream.codec_context, "name", "")
        out_name = getattr(out_stream.codec_context, "name", "")
        if in_name == "aac_latm" and out_name == "aac" and self.track.packets:
            self._latm = LatmRepacketiser(bytes(self.track.packets[0]))

        # Output position state - can be set for joining multiple files
        self.segment_start_in_output = initial_position
        self.prev_dts = initial_prev_dts
        self.prev_pts = initial_prev_pts

    def segment(self, cut_segment: CutSegment) -> list[Packet]:
        in_tb = cast(Fraction, self.track.av_stream.time_base)
        if cut_segment.start_time <= 0:
            start = 0
        else:
            start_pts = round(cut_segment.start_time / in_tb)
            start = np.searchsorted(self.track.frame_times_pts, start_pts)
        end_pts = round(cut_segment.end_time / in_tb)
        end = np.searchsorted(self.track.frame_times_pts, end_pts)
        in_packets = self.track.packets[start : end]
        packets = []
        for p in in_packets:
            if p.dts is None or p.pts is None:
                continue
            if self._latm is not None:
                try:
                    packet = Packet(self._latm.to_adts(bytes(p)))
                except LatmError as e:
                    # One unparsable frame: drop it (~21ms) rather than emit
                    # bytes the decoder can't read; timing continues cleanly.
                    print(f"LATM frame skipped: {e}")
                    continue
                packet.time_base = p.time_base
                packet.duration = p.duration
            else:
                packet = copy_packet(p)
            packet.stream = self.out_stream
            packet.pts = int(p.pts + (self.segment_start_in_output - cut_segment.start_time) / in_tb)
            packet.dts = int(p.dts + (self.segment_start_in_output - cut_segment.start_time) / in_tb)
            if packet.pts <= self.prev_pts:
                print("Correcting for too low pts in audio passthru")
                packet.pts = self.prev_pts + 1
            if packet.dts <= self.prev_dts:
                print("Correcting for too low dts in audio passthru")
                packet.dts = self.prev_dts + 1
            self.prev_pts = packet.pts
            self.prev_dts = packet.dts
            packets.append(packet)

        self.segment_start_in_output += cut_segment.end_time - cut_segment.start_time
        return packets

    def finish(self) -> list[Packet]:
        return []


def create_subtitle_output_stream(
    media_container: MediaContainer,
    output_av_container: OutputContainer,
    track_index: int,
) -> Stream:
    """
    Create output subtitle stream from source media container.

    Separated from SubtitleCutter to allow reuse in joining operations.
    """
    in_stream = media_container.av_container.streams.subtitles[track_index]
    out_stream = output_av_container.add_stream_from_template(in_stream)
    out_stream.metadata.update(in_stream.metadata)
    out_stream.disposition = cast(Disposition, in_stream.disposition.value)
    return out_stream


class SubtitleCutter:
    def __init__(
        self,
        media_container: MediaContainer,
        out_stream: Stream,
        track_index: int,
        initial_position: Fraction = Fraction(0),
        initial_prev_pts: int = -100_000,
    ) -> None:
        self.track_i = track_index
        self.packets = media_container.subtitle_tracks[track_index]
        self.in_stream = media_container.av_container.streams.subtitles[track_index]
        self.out_stream = out_stream

        # Output position state - can be set for joining multiple files
        self.segment_start_in_output = initial_position
        self.prev_pts = initial_prev_pts

        self.current_packet_i = 0

    def segment(self, cut_segment: CutSegment) -> list[Packet]:
        in_tb = cast(Fraction, self.in_stream.time_base)
        segment_start_pts = int(cut_segment.start_time / in_tb)
        segment_end_pts = int(cut_segment.end_time / in_tb)

        out_packets = []

        # TODO: This is the simplest implementation of subtitle cutting. Investigate more complex logic.
        # We include subtitles for the whole original time if the subtitle start time is included in the output
        # Good: simple, Bad: 1) if start is cut it's not shown at all 2) we can show a subtitle for too long if there is cut after it's shown
        while self.current_packet_i < len(self.packets):
            p = self.packets[self.current_packet_i]
            if p.pts < segment_start_pts:
                self.current_packet_i += 1
            elif p.pts >= segment_start_pts and p.pts < segment_end_pts:
                out_packets.append(p)
                self.current_packet_i += 1
            else:
                break

        for packet in out_packets:
            packet.stream = self.out_stream
            packet.pts = int(packet.pts - segment_start_pts + self.segment_start_in_output / in_tb)

            if packet.pts < self.prev_pts:
                print("Correcting for too low pts in subtitle passthru. This should not happen.")
                packet.pts = self.prev_pts + 1
            packet.dts = packet.pts
            self.prev_pts = packet.pts
            self.prev_dts = packet.dts

        self.segment_start_in_output += cut_segment.end_time - cut_segment.start_time
        return out_packets

    def finish(self) -> list[Packet]:
        return []
