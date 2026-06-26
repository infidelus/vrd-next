import argparse
import sys
from fractions import Fraction

from tqdm import tqdm

from smartcut.media_container import MediaContainer
from smartcut.media_utils import VideoExportMode, VideoExportQuality
from smartcut.misc_data import AudioExportInfo, AudioExportSettings
from smartcut.smart_cut import __version__, smart_cut
from smartcut.video_cutter import VideoSettings


def time_to_fraction(time_str_elem: str) -> Fraction:
    if ':' in time_str_elem:
        # Handle negative times properly
        is_negative = time_str_elem.startswith('-')
        if is_negative:
            time_str_elem = time_str_elem[1:]  # Remove the negative sign

        parts = time_str_elem.split(':')
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = Fraction(parts[2])
            result = Fraction(hours * 3600) + Fraction(minutes * 60) + seconds
        elif len(parts) == 2:
            minutes = int(parts[0])
            seconds = Fraction(parts[1])
            result = Fraction(minutes * 60) + seconds
        else:
            raise ValueError("Timestamp must be in format HH:MM:SS or MM:SS")

        return -result if is_negative else result

    return Fraction(time_str_elem)

def resolve_time_with_duration(time_str_elem: str, duration: Fraction) -> Fraction:
    """
    Resolve a time string element, handling special keywords and negative times.

    Args:
        time_str_elem: Time string (numeric, MM:SS, HH:MM:SS, or special keyword)
        duration: Media duration in seconds (Fraction)

    Returns:
        Fraction representing absolute time in seconds
    """
    # Handle special keywords
    time_lower = time_str_elem.lower()

    # Start of file keywords
    if time_lower in ['s', 'start']:
        return Fraction(0)

    # End of file keywords
    if time_lower in ['e', 'end', '-0']:
        return duration

    # Parse as regular time
    parsed_time = time_to_fraction(time_str_elem)

    # Handle negative times (seconds from end)
    if parsed_time < 0:
        return duration + parsed_time  # duration - abs(parsed_time)

    return parsed_time

def parse_time_segments(time_str: str) -> list[tuple[Fraction, Fraction]]:
    times = list(map(time_to_fraction, time_str.split(',')))
    if len(times) % 2 != 0:
        raise ValueError("You must provide an even number of time points for segments.")
    return list(zip(times[::2], times[1::2]))

def parse_time_segments_with_duration(time_str: str, duration: Fraction) -> list[tuple[Fraction, Fraction]]:
    """Parse time segments resolving special keywords and negative timestamps."""
    time_elements = time_str.split(',')
    times = [resolve_time_with_duration(elem, duration) for elem in time_elements]
    if len(times) % 2 != 0:
        raise ValueError("You must provide an even number of time points for segments.")
    return list(zip(times[::2], times[1::2]))

def frame_to_time(source: MediaContainer, frame_str: str, end_frame: bool = False) -> Fraction:
    frame_num = int(frame_str)
    if frame_num == -1:
        # Special case: frame "-1" means "the final frame of the video"
        return source.duration
    if end_frame:
        # Internal calculations in `smart_cut` function currently *exclude* the final frame if it lands
        # exactly on the specified end time, so we manually offset "end" frames by 1
        frame_num += 1
    return source.video_frame_times[frame_num] - source.start_time

def parse_frame_segments(source: MediaContainer, frame_str: str) -> list[tuple[Fraction, Fraction]]:
    all_frames = frame_str.split(',')
    if len(all_frames) % 2 != 0:
        raise ValueError("You must provide an even number of frames for segments.")
    start_frames = list(map(lambda f: frame_to_time(source, f), all_frames[::2]))
    end_frames = list(map(lambda f: frame_to_time(source, f, True), all_frames[1::2]))
    return list(zip(start_frames, end_frames))

class Progress:
    def __init__(self) -> None:
        self.first_call = True
        self.tqdm: tqdm | None = None

    def emit(self, value: int) -> None:
        if self.first_call:
            self.first_call = False
            self.tqdm = tqdm(total=value)
            return
        if self.tqdm is not None:
            self.tqdm.update(1)

def preprocess_argv_for_negative_numbers(argv: list[str]) -> list[str]:
    """
    Preprocess sys.argv to handle negative numbers in -k/--keep and -c/--cut arguments.

    This works around argparse limitation where it treats arguments starting with '-'
    as option flags rather than values.

    Args:
        argv: Command line arguments (typically sys.argv)

    Returns:
        Processed argv list where negative values are temporarily marked
    """
    processed = []
    i = 0

    while i < len(argv):
        if argv[i] in ['-k', '--keep', '-c', '--cut']:
            # This is one of our options that might take negative values
            processed.append(argv[i])

            # Check if there's a next argument and it's the value (not another option)
            if (i + 1 < len(argv) and
                not argv[i + 1].startswith('--') and
                argv[i + 1] not in ['-h', '--help', '--frames', '--version']):

                next_arg = argv[i + 1]
                # If it starts with '-' but not '--', it's likely a negative value
                if next_arg.startswith('-') and not next_arg.startswith('--'):
                    # Mark negative values with special prefix
                    processed.append('NEG_MARK_' + next_arg[1:])
                else:
                    processed.append(next_arg)
                i += 2
            else:
                i += 1
        else:
            processed.append(argv[i])
            i += 1

    return processed

def restore_negative_numbers(args: argparse.Namespace) -> None:
    """
    Restore negative signs to keep/cut arguments that were marked during preprocessing.

    Args:
        args: Parsed arguments from argparse

    Returns:
        None (modifies args in place)
    """
    if args.keep and args.keep.startswith('NEG_MARK_'):
        args.keep = '-' + args.keep[9:]  # Remove 'NEG_MARK_' and restore '-'

    if args.cut and args.cut.startswith('NEG_MARK_'):
        args.cut = '-' + args.cut[9:]   # Remove 'NEG_MARK_' and restore '-'


def main() -> None:
    description = (
        "SmartCut - Efficient video cutting with minimal recoding. "
        "Only segments around cutpoints are re-encoded, preserving original quality "
        "for the majority of the video. Supports various formats including MP4, MKV, AVI, MOV."
    )

    epilog = """
examples:
  Keep segments from 10-20s and 40-50s:
    smartcut input.mp4 output.mp4 --keep 10,20,40,50
    smartcut input.mp4 output.mp4 -k 10,20,40,50

  Cut out segments from 30-40s and 1m-1m10s:
    smartcut input.mp4 output.mp4 --cut 30,40,01:00,01:10
    smartcut input.mp4 output.mp4 -c 30,40,01:00,01:10

  Keep from start to 30s, then from 1m to end:
    smartcut input.mp4 output.mp4 -k start,30,01:00,end
    smartcut input.mp4 output.mp4 -k s,30,60,e

  Cut the last 5 seconds of the file:
    smartcut input.mp4 output.mp4 -c -5,end
    smartcut input.mp4 output.mp4 -c -5,-0

  Use frame numbers instead of times:
    smartcut input.mp4 output.mp4 --keep 300,600,1200,1500 --frames

  Subsecond precision cutting:
    smartcut input.mp4 output.mp4 --keep 00:06:48.799,00:06:50.123

time formats:
  - Seconds: 10, 30.5, 120
  - MM:SS: 01:30, 02:45
  - HH:MM:SS: 01:30:45
  - Subseconds: 48.799, 01:30.123, 01:30:45.678
  - Negative (from end): -5 (5s from end), -1:30 (1m30s from end)
  - Keywords: s/start (beginning), e/end/-0 (end of file)
"""

    parser = argparse.ArgumentParser(
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('input', metavar='INPUT', type=str,
                       help="Input media file (MP4, MKV, AVI, MOV, etc.)")
    parser.add_argument('output', metavar='OUTPUT', type=str,
                       help="Output media file path")
    parser.add_argument('-k', '--keep', metavar='SEGMENTS', type=str,
                       help="Keep specified time segments. Format: start1,end1,start2,end2,... "
                            "Times can be in seconds (10), MM:SS (01:30), HH:MM:SS (01:30:45), "
                            "subseconds (48.799, 01:30.123, 01:30:45.678), negative times from end (-5), "
                            "or keywords: s/start (beginning), e/end/-0 (end of file)")
    parser.add_argument('-c', '--cut', metavar='SEGMENTS', type=str,
                       help="Remove specified time segments (opposite of --keep). "
                            "Same time format as --keep option")
    parser.add_argument('--frames', action='store_true',
                       help="Interpret --keep/--cut values as frame numbers instead of times. "
                            "Frames are zero-indexed. Use -1 for the last frame")
    parser.add_argument('--log-level', choices=['warning', 'error', 'fatal'],
                       default='warning', metavar='LEVEL',
                       help="Set logging verbosity level (default: %(default)s)")
    parser.add_argument('--version', action='version', version=f'Smartcut {__version__}')

    # Preprocess argv to handle negative numbers in -k/-c arguments
    processed_argv = preprocess_argv_for_negative_numbers(sys.argv[1:])
    args = parser.parse_args(processed_argv)

    # Restore negative signs that were marked during preprocessing
    restore_negative_numbers(args)

    if (args.keep and args.cut) or not (args.keep or args.cut):
        raise ValueError("You must specify either --keep or --cut, not both.")

    source = MediaContainer(args.input)

    if args.keep:
        segments = parse_frame_segments(source, args.keep) if args.frames else parse_time_segments_with_duration(args.keep, source.duration)
    elif args.cut:
        cut_segments = parse_frame_segments(source, args.cut) if args.frames else parse_time_segments_with_duration(args.cut, source.duration)
        segments = [(Fraction(0), source.duration)]
        for c_start, c_end in cut_segments:
            last_segment = segments.pop()
            if c_start > last_segment[0]:
                segments.append((last_segment[0], c_start))
            if c_end < last_segment[1]:
                segments.append((c_end, last_segment[1]))
    else:
        raise ValueError("You must specify either --keep or --cut.")

    # Default audio settings: include all tracks with lossless passthru
    audio_settings: list[AudioExportSettings | None] = [AudioExportSettings(codec='passthru')] * len(source.audio_tracks)
    export_info = AudioExportInfo(output_tracks=audio_settings)

    video_settings = VideoSettings(VideoExportMode.SMARTCUT, VideoExportQuality.NORMAL, 'copy')

    progress = Progress()

    # Set logging levels - temporarily disabled due to import issues
    # TODO: Fix av.logging import when PyAV version is updated
    pass

    exception_value = smart_cut(source, segments, args.output,
                                audio_export_info=export_info,
                                video_settings=video_settings,
                                progress=progress, log_level=args.log_level)

    if progress.tqdm is not None:
        progress.tqdm.close()

    if exception_value is not None:
        raise exception_value

    print(f"Smart cut completed successfully. Output saved to {args.output}")

if __name__ == '__main__':
    main()
