"""
Read and write VideoReDo project files (.vprj / .VPrj).

A .vprj describes an edit of a source recording.  The important parts:

  <CutList>      - the regions to REMOVE (commercials, head/tail).  Each cut
                   has <CutTimeStart>/<CutTimeEnd> in 100-nanosecond ticks
                   (10,000,000 per second).
  <SceneList>    - optional navigation markers (scene-change detections), each
                   a <SceneMarker> giving a tick position.  These are NOT cut
                   points; they are "interesting frames" to jump to.

Two dialects exist:
  Version 3  - written by Comskip-style tools: <Cut> elements, <InputPIDList>,
               no byte offsets.
  Version 5  - written by VideoReDo itself: <cut> elements with timecode
               attributes, CutByteStart/End, individual PID tags, a
               <ChapterList>.

We read both.  We write a Version 5-style file (with the human-readable
timecode attributes), which VideoReDo and Comskip-aware tools both accept.

Because VRD-Next works in KEPT ranges (the green scenes) while a .vprj stores
REMOVED ranges, loading inverts the cut list against the total duration, and
saving inverts the kept ranges back into cuts.
"""

import xml.etree.ElementTree as ET


TICKS_PER_SECOND = 10_000_000


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _ticks_to_seconds(ticks):
    return ticks / TICKS_PER_SECOND


def _seconds_to_ticks(seconds):
    # Clamp to >= 0: the very first frames of some broadcast streams carry a
    # presentation time fractionally before zero, which must not become a
    # negative tick value (VideoReDo rejects those and mis-places the cut).
    return max(0, int(round(seconds * TICKS_PER_SECOND)))


def _timecode(seconds, fps):
    """Format seconds as VideoReDo's HH:MM:SS;FF timecode.

    Seconds are clamped to >= 0: some broadcast streams have a few frames whose
    presentation time is fractionally before the container start, which would
    otherwise produce a negative (and invalid) timecode.
    """
    seconds = max(0.0, seconds)
    total_frames = int(round(seconds * fps))
    ff = int(total_frames % round(fps))
    whole = int(seconds)
    hh = whole // 3600
    mm = (whole % 3600) // 60
    ss = whole % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d};{ff:02d}"


def _invert_ranges(removed, total_frames):
    """Given REMOVED (cut) ranges as (start, end) frame pairs, return the KEPT
    ranges that fill the gaps across [0, total_frames - 1].

    The cut list is the source of truth: every gap between cuts becomes a kept
    scene, however short.  (Tiny scenes are legitimate - e.g. joining around a
    corrupt section.)
    """
    if total_frames <= 0:
        return []

    removed = sorted(removed)
    kept = []
    cursor = 0

    for start, end in removed:
        start = max(0, start)
        end = min(total_frames - 1, end)
        if start > cursor:
            kept.append((cursor, start - 1))
        cursor = max(cursor, end + 1)

    if cursor <= total_frames - 1:
        kept.append((cursor, total_frames - 1))

    return kept


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #

class VprjData:
    """Parsed result: kept frame ranges + navigation markers + source path."""

    def __init__(self, keep_ranges, markers, source_filename):
        self.keep_ranges = keep_ranges        # list of (start_frame, end_frame)
        self.markers = markers                # list of frame numbers
        self.source_filename = source_filename


def read_source_filename(path):
    """Return just the source recording path stored in a .vprj, without needing
    a FrameIndex.  Used by the batch manager to find (and index) the video
    before mapping the project's cut times onto it.  Returns "" if absent."""
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return ""
    fn = root.find("Filename")
    if fn is not None and fn.text:
        return fn.text.strip()
    return ""


def load_vprj(path, index):
    """Parse a .vprj file and map its times onto the given FrameIndex.

    Returns a VprjData.  The cut list is inverted into kept ranges; the scene
    list (if any) becomes navigation markers.  All times are converted from
    100ns ticks to frame numbers via index.index_of_seconds().
    """
    tree = ET.parse(path)
    root = tree.getroot()

    source_filename = ""
    fn = root.find("Filename")
    if fn is not None and fn.text:
        source_filename = fn.text.strip()

    total_frames = index.frame_count

    # --- Cuts (removed regions) ---------------------------------------- #
    removed = []
    cutlist = root.find("CutList")
    if cutlist is not None:
        # Match both <Cut> (V3) and <cut> (V5).
        for cut in list(cutlist):
            tag = cut.tag.lower()
            if tag != "cut":
                continue
            ts = cut.find("CutTimeStart")
            te = cut.find("CutTimeEnd")
            if ts is None or te is None:
                continue
            try:
                start_ticks = int((ts.text or "0").strip())
                end_ticks = int((te.text or "0").strip())
            except ValueError:
                continue
            # A cut starting at (or before) tick 0 means "from the very start".
            # Map it to frame 0 explicitly: some broadcast indexes place frame
            # 0 at a slightly negative time, so index_of_seconds(0.0) can land
            # a few frames in - which would leave a phantom tiny keep-range at
            # the start after inversion.
            if start_ticks <= 0:
                start_f = 0
            else:
                start_f = index.index_of_seconds(
                    _ticks_to_seconds(start_ticks)
                )
            end_f = index.index_of_seconds(_ticks_to_seconds(end_ticks))
            if end_f < start_f:
                start_f, end_f = end_f, start_f
            removed.append((start_f, end_f))

    keep_ranges = _invert_ranges(removed, total_frames)

    # --- Scene markers (navigation) ------------------------------------ #
    markers = []
    scenelist = root.find("SceneList")
    if scenelist is not None:
        for marker in scenelist.findall("SceneMarker"):
            if not marker.text:
                continue
            try:
                ticks = int(marker.text.strip())
            except ValueError:
                continue
            frame = index.index_of_seconds(_ticks_to_seconds(ticks))
            if 0 <= frame < total_frames:
                markers.append(frame)
    markers = sorted(set(markers))

    return VprjData(keep_ranges, markers, source_filename)


# --------------------------------------------------------------------------- #
# Save
# --------------------------------------------------------------------------- #

def cuts_seconds_from_keeps(keep_ranges, index):
    """Given KEPT frame ranges and a FrameIndex, return
    (cut_ranges_seconds, duration_seconds): the REMOVED regions in seconds,
    derived exactly as save_vprj() writes them.

    Lets the joiner capture an edit (source + cut list) on the fly - without the
    user having to save a .vprj - by storing the cuts in seconds.  They can be
    written back out later with save_vprj_from_cuts().
    """
    total_frames = index.frame_count
    removed = _invert_ranges(sorted(keep_ranges), total_frames)
    cuts = [[index.seconds_of(start_f), index.seconds_of(end_f)]
            for start_f, end_f in removed]
    duration = index.seconds_of(total_frames - 1) if total_frames else 0.0
    return cuts, duration


def save_vprj(path, keep_ranges, markers, source_filename, index):
    """Write a Version 5-style .vprj describing the edit.

    keep_ranges are KEPT (green) frame ranges; they are inverted into cuts.
    markers are navigation-marker frame numbers written into a <SceneList>.
    """
    fps = index.fps
    total_frames = index.frame_count
    duration_secs = index.seconds_of(total_frames - 1) if total_frames else 0.0
    duration_ticks = _seconds_to_ticks(duration_secs)

    # Invert kept ranges into removed (cut) ranges across the whole file.
    removed = _invert_ranges(sorted(keep_ranges), total_frames)

    root = ET.Element("VideoReDoProject", Version="5")

    from version import APP_NAME, VERSION, build_stamp
    ver = ET.SubElement(root, "VideoReDoVersion", BuildNumber=str(build_stamp()))
    ver.text = f"{APP_NAME} {VERSION}"

    ET.SubElement(root, "Filename").text = source_filename or ""
    ET.SubElement(root, "Description").text = ""
    ET.SubElement(root, "StreamType").text = "2"
    ET.SubElement(root, "Duration").text = str(duration_ticks)
    ET.SubElement(root, "SyncAdjustment").text = "0"
    ET.SubElement(root, "AudioVolumeAdjust").text = "1.000000"
    ET.SubElement(root, "CutMode").text = "1"

    cutlist = ET.SubElement(root, "CutList")
    elapsed_secs = 0.0
    for i, (start_f, end_f) in enumerate(removed, start=1):
        start_secs = index.seconds_of(start_f)
        # The cut END is exclusive of the next kept frame; use the frame's time.
        end_secs = index.seconds_of(end_f)

        cut = ET.SubElement(
            cutlist,
            "cut",
            Sequence=str(i),
            CutStart=_timecode(start_secs, fps),
            CutEnd=_timecode(end_secs, fps),
            Elapsed=_timecode(elapsed_secs, fps),
        )
        ET.SubElement(cut, "CutTimeStart").text = str(
            _seconds_to_ticks(start_secs)
        )
        ET.SubElement(cut, "CutTimeEnd").text = str(
            _seconds_to_ticks(end_secs)
        )
        ET.SubElement(cut, "CutByteStart").text = "0"
        ET.SubElement(cut, "CutByteEnd").text = "0"

        elapsed_secs += (end_secs - start_secs)

    scenelist = ET.SubElement(root, "SceneList")
    for seq, frame in enumerate(sorted(markers)):
        secs = index.seconds_of(frame)
        sm = ET.SubElement(
            scenelist,
            "SceneMarker",
            Sequence=str(seq),
            Timecode=_timecode(secs, fps),
        )
        sm.text = str(_seconds_to_ticks(secs))

    _indent(root)
    tree = ET.ElementTree(root)
    with open(path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)


def save_vprj_from_cuts(path, source_filename, cut_ranges_seconds,
                        duration_seconds=0.0, fps=25.0):
    """Write a .vprj from commercial CUT regions given directly in seconds.

    This is the lightweight path used by the watcher: Comskip already produces
    cut regions (in its EDL), so there's no need to decode the recording to
    build a frame index just to write a project.  The cut times are written as
    100ns ticks, which is what load_vprj() reads; it maps them onto a real
    FrameIndex later, when the user opens the project to review the cuts.

    cut_ranges_seconds is a list of (start_seconds, end_seconds) REMOVE
    regions (exactly Comskip's output - no inversion).  fps is used only for
    the cosmetic HH:MM:SS;FF timecode attributes.
    """
    duration_ticks = _seconds_to_ticks(max(0.0, duration_seconds))

    removed = sorted(
        (max(0.0, float(a)), max(0.0, float(b)))
        for a, b in cut_ranges_seconds
    )

    root = ET.Element("VideoReDoProject", Version="5")

    from version import APP_NAME, VERSION, build_stamp
    ver = ET.SubElement(root, "VideoReDoVersion", BuildNumber=str(build_stamp()))
    ver.text = f"{APP_NAME} {VERSION}"

    ET.SubElement(root, "Filename").text = source_filename or ""
    ET.SubElement(root, "Description").text = ""
    ET.SubElement(root, "StreamType").text = "2"
    ET.SubElement(root, "Duration").text = str(duration_ticks)
    ET.SubElement(root, "SyncAdjustment").text = "0"
    ET.SubElement(root, "AudioVolumeAdjust").text = "1.000000"
    ET.SubElement(root, "CutMode").text = "1"

    cutlist = ET.SubElement(root, "CutList")
    elapsed_secs = 0.0
    for i, (start_secs, end_secs) in enumerate(removed, start=1):
        cut = ET.SubElement(
            cutlist,
            "cut",
            Sequence=str(i),
            CutStart=_timecode(start_secs, fps),
            CutEnd=_timecode(end_secs, fps),
            Elapsed=_timecode(elapsed_secs, fps),
        )
        ET.SubElement(cut, "CutTimeStart").text = str(
            _seconds_to_ticks(start_secs)
        )
        ET.SubElement(cut, "CutTimeEnd").text = str(
            _seconds_to_ticks(end_secs)
        )
        ET.SubElement(cut, "CutByteStart").text = "0"
        ET.SubElement(cut, "CutByteEnd").text = "0"
        elapsed_secs += (end_secs - start_secs)

    # Comskip carries no navigation markers.
    ET.SubElement(root, "SceneList")

    _indent(root)
    tree = ET.ElementTree(root)
    with open(path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)


def _indent(elem, level=0):
    """Pretty-print indentation for ElementTree (stdlib has no indent on 3.8)."""
    pad = "\n" + "\t" * level
    if len(elem):
        if not (elem.text and elem.text.strip()):
            elem.text = pad + "\t"
        for child in elem:
            _indent(child, level + 1)
        if not (child.tail and child.tail.strip()):
            child.tail = pad
    if level and not (elem.tail and elem.tail.strip()):
        elem.tail = pad
