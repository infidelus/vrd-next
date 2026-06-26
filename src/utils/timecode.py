FPS = 25

# The frame rate timecodes are computed against.  Defaults to 25 (PAL) but is
# updated to the open file's real rate via set_timecode_fps(), so files at
# other rates (e.g. 50fps HD) show correct times rather than double/half.
_current_fps = 25


def set_timecode_fps(fps):
    """Set the frame rate used for timecode<->frame conversion.

    Called when a video loads.  Uses the rounded integer rate (e.g. 25, 50)
    since timecodes count whole frames per second.
    """
    global _current_fps
    try:
        rate = int(round(float(fps)))
    except (TypeError, ValueError):
        rate = 25
    _current_fps = rate if rate > 0 else 25


def seconds_to_timecode(seconds):
    """Format a duration given in seconds as HH:MM:SS.FF.

    Used where a duration is known in seconds rather than frames - e.g. the
    joiner total, which spans sources of differing frame rates.  The HH:MM:SS
    part is exact; the frame part uses the current timecode fps.
    """
    if seconds is None:
        return "--:--:--.--"

    fps = _current_fps
    total = max(0.0, float(seconds))
    whole = int(total)
    frames = int(round((total - whole) * fps))
    if frames >= fps:          # rounding spilled into the next second
        whole += 1
        frames = 0

    hours = whole // 3600
    minutes = (whole // 60) % 60
    secs = whole % 60

    return f"{hours:02}:{minutes:02}:{secs:02}.{frames:02}"


def frame_to_timecode(
        frame_num,
):

    if frame_num is None:

        return (
            "--:--:--.--"
        )

    fps = _current_fps

    hours = (
        frame_num
        //
        (
            fps
            *
            3600
        )
    )

    minutes = (
        frame_num
        //
        (
            fps
            *
            60
        )
    ) % 60

    secs = (
        frame_num
        //
        fps
    ) % 60

    frames = (
        frame_num
        %
        fps
    )

    return (

        f"{hours:02}:"

        f"{minutes:02}:"

        f"{secs:02}."

        f"{frames:02}"

    )

def parse_timecode(
        text,
):

    if not text:
        return None

    #
    # Accept:
    #
    # 00:05:04.22
    # 00.05.04.22
    # 00050422
    #

    cleaned = (
        text
        .strip()
        .replace(
            ":",
            ".",
        )
    )

    parts = cleaned.split(
        "."
    )

    #
    # Compact numeric form
    #

    if len(parts) == 1:

        digits = (
            parts[0]
        )

        if not digits.isdigit():
            return None

        digits = digits.zfill(
            8
        )

        parts = [
            digits[0:2],
            digits[2:4],
            digits[4:6],
            digits[6:8],
        ]

    if len(parts) != 4:
        return None

    try:

        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2])
        frames = int(parts[3])

    except ValueError:
        return None

    total = (

        (
            hours
            *
            3600
        )
        +
        (
            minutes
            *
            60
        )
        +
        seconds

    ) * _current_fps

    total += frames

    return total

def marker_text(selection):

    in_text = "--:--:--.--"
    out_text = "--:--:--.--"

    if selection.pending_in is not None:

        in_text = frame_to_timecode(
            selection.pending_in
        )

    if selection.pending_out is not None:

        out_text = frame_to_timecode(
            selection.pending_out
        )

    return (
        f"IN {in_text}    "
        f"OUT {out_text}"
    )