"""
Exporter - frame-accurate video export built on the `smartcut` library.

smartcut does true smart-rendering: whole GOPs inside a keep-range are
stream-copied (fast, lossless), and only the partial GOPs at each cut
boundary are re-encoded.  Frame-accurate AND fast.

Audio: every track is passed through (copied) by default.  Some broadcast
streams carry a second audio track (e.g. described/visual-impaired) that
smartcut can't pass through into a .ts; if the full export fails we retry
with the primary track only, so a problematic secondary track never blocks
the export.  What survived is reported back.

Output:
  - "match" -> same container as the source (e.g. .ts)
  - "mkv"   -> Matroska, always with a chapter per kept segment
  - "mp4"   -> MP4 (cannot carry DVB subtitles; they are dropped)

Frame->time conversion uses smartcut's own scheme so cuts land exactly on the
requested frames.  Runs on a worker thread.
"""

import bisect
import os
import shutil
import subprocess
import time
import logging
import json
from fractions import Fraction

from PySide6.QtCore import (
    QThread,
    Signal,
)

from smartcut.smart_cut import (
    smart_cut,
    make_cut_segments,
    make_adjusted_segment_times,
)
from smartcut.media_container import MediaContainer
from smartcut.misc_data import (
    AudioExportInfo,
    AudioExportSettings,
)


class ExportError(Exception):
    pass


logger = logging.getLogger(__name__)


def _fmt_tc(secs, fps=25.0):
    """Format seconds as HH:MM:SS.ff (ff = frames within the second)."""
    if secs is None or secs < 0:
        return "??:??:??.??"
    whole = int(secs)
    frames = int(round((secs - whole) * fps))
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{frames:02d}"


def _log_source_details(source_path):
    """Log the source's video/audio stream properties, VideoReDo-style, so the
    log records exactly what was fed in (codec, scan type, channels, etc.)."""
    try:
        raw = subprocess.run(
            [
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-show_streams", "-show_format", "-of", "json",
                source_path,
            ],
            capture_output=True, text=True,
        ).stdout
        data = json.loads(raw)
    except Exception:
        logger.exception("Could not probe source details")
        return

    fmt = data.get("format", {}) or {}
    logger.info(
        "Source: %s | container=%s, duration=%ss, bitrate=%s bps",
        os.path.basename(source_path),
        fmt.get("format_name", "?"),
        fmt.get("duration", "?"),
        fmt.get("bit_rate", "?"),
    )

    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video":
            logger.info(
                "  video: codec=%s, profile=%s, %sx%s, fps=%s, scan=%s, "
                "bitrate=%s bps",
                stream.get("codec_name", "?"),
                stream.get("profile", "?"),
                stream.get("width", "?"),
                stream.get("height", "?"),
                stream.get("r_frame_rate", "?"),
                stream.get("field_order", "?"),
                stream.get("bit_rate", "?"),
            )
        elif kind == "audio":
            tags = stream.get("tags", {}) or {}
            logger.info(
                "  audio: codec=%s, channels=%s, sample_rate=%s, lang=%s",
                stream.get("codec_name", "?"),
                stream.get("channels", "?"),
                stream.get("sample_rate", "?"),
                tags.get("language", "?"),
            )


def frame_to_time(source, frame_num, end_frame=False):
    if frame_num == -1:
        return source.duration
    if end_frame:
        frame_num += 1
    if frame_num >= len(source.video_frame_times):
        return source.duration
    return source.video_frame_times[frame_num] - source.start_time


def ranges_to_segments(source, keep_ranges, frame_index=None):
    """Convert kept frame ranges to (start_time, end_time) segments.

    The frame numbers in keep_ranges are indices into VRD-Next's FrameIndex.
    For field-coded (PAFF) sources that index is COLLAPSED to one entry per
    displayable frame, so it has fewer entries than the file has coded
    pictures.  smartcut's own frame list (source.video_frame_times) counts every
    coded picture, so looking a collapsed frame number up in it lands on the
    wrong picture and compresses every cut (a 29:09 keep becomes ~20:11).

    When we have the FrameIndex, map each collapsed frame to the native coded
    picture at the same PTS and then use smartcut's own frame_to_time(), so it
    receives the identical Fraction-typed times it expects.  For progressive
    sources the index isn't collapsed and the mapping is the identity, so this
    is a true no-op for those files.
    """
    if frame_index is None:
        return [
            (frame_to_time(source, a), frame_to_time(source, b, True))
            for a, b in keep_ranges
        ]

    # keep_ranges index VRD-Next's FrameIndex.  For field-coded (PAFF) sources
    # that index is COLLAPSED to one entry per displayable frame, so it has
    # fewer entries than smartcut's own frame list (which counts every coded
    # picture).  Indexing smartcut's list with a collapsed number lands on the
    # wrong picture and compresses every cut (29:09 -> ~20:11).
    #
    # So map each collapsed frame to the native coded picture at the SAME PTS,
    # then defer to frame_to_time() exactly as the uncollapsed path does - that
    # hands smartcut the identical Fraction-typed times it expects, so its
    # copy-vs-reencode decisions are unchanged.  When the index wasn't collapsed
    # (len matches), the mapping is the identity and this is a true no-op.
    num, den = frame_index.time_base
    vft = source.video_frame_times
    n_native = len(vft)
    collapsed = n_native != frame_index.frame_count

    def to_native(f):
        if not collapsed:
            return f
        target = Fraction(int(frame_index.pts[f]) * int(num), int(den))
        i = bisect.bisect_left(vft, target)
        if i >= n_native:
            return n_native - 1
        if i > 0 and (target - vft[i - 1]) <= (vft[i] - target):
            return i - 1
        return i

    return [
        (frame_to_time(source, to_native(a)),
         frame_to_time(source, to_native(b), True))
        for a, b in keep_ranges
    ]


def _usable_audio_tracks(source_path, n_audio):
    """Return a list of audio-track indices that are actually usable.

    Some broadcast recordings carry a malformed secondary audio track (e.g. a
    visual-impaired "descriptions" track) reporting 0 channels / no sample
    rate.  Passing such a track through corrupts the whole export - smartcut
    can silently drop the video while struggling with it.  We probe each audio
    stream and keep only those with a real channel count and sample rate.

    Always keeps at least track 0 (so we never end up with no audio at all,
    even if probing is inconclusive).
    """
    try:
        out = subprocess.run(
            [
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index,codec_name,channels,sample_rate",
                "-of", "default=noprint_wrappers=1",
                source_path,
            ],
            capture_output=True, text=True,
        ).stdout

        # ffprobe prints a block of key=value lines per stream; for TS files
        # streams can appear more than once, so we key by the stream index and
        # map those to audio-track positions in order of first appearance.
        per_stream = {}        # stream_index -> {"channels":..,"sample_rate":..}
        order = []             # stream indices in first-seen order
        cur = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("index="):
                cur = line.split("=", 1)[1]
                if cur not in per_stream:
                    per_stream[cur] = {
                        "channels": 0, "sample_rate": 0, "codec": "?"
                    }
                    order.append(cur)
            elif cur is not None and line.startswith("codec_name="):
                per_stream[cur]["codec"] = line.split("=", 1)[1]
            elif cur is not None and line.startswith("channels="):
                try:
                    per_stream[cur]["channels"] = int(line.split("=", 1)[1])
                except ValueError:
                    pass
            elif cur is not None and line.startswith("sample_rate="):
                val = line.split("=", 1)[1]
                try:
                    per_stream[cur]["sample_rate"] = int(val)
                except ValueError:
                    pass

        usable = []
        for audio_idx, sidx in enumerate(order):
            info = per_stream[sidx]
            logger.info(
                "Audio track %d: codec=%s, channels=%d, sample_rate=%d",
                audio_idx, info["codec"], info["channels"],
                info["sample_rate"],
            )
            if info["channels"] > 0 and info["sample_rate"] > 0:
                usable.append(audio_idx)

    except Exception:
        # If probing fails entirely, fall back to "all tracks".
        logger.exception("Audio probe failed; keeping all %d track(s)", n_audio)
        return list(range(n_audio))

    if not usable:
        # Probing found nothing usable - keep track 0 as a last resort.
        return [0] if n_audio > 0 else []

    return usable


def _passthru_for_indices(n_audio, usable_indices):
    """Build a passthru output-track list keeping only usable_indices."""
    tracks = []
    for i in range(n_audio):
        if i in usable_indices:
            tracks.append(AudioExportSettings(codec="passthru"))
        else:
            tracks.append(None)
    return tracks


def _passthru_tracks(n):
    return [AudioExportSettings(codec="passthru") for _ in range(n)]


def _probe_output_extra(path):
    """Return (audio_frames, video_bitrate_bps) from the finished output.

    Uses ffprobe; returns (0, 0) on any failure so the dialog still shows.
    """
    audio_frames = 0
    video_bitrate = 0
    try:
        # Audio packet count of the first audio stream.
        out = subprocess.run(
            [
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-select_streams", "a:0",
                "-count_packets",
                "-show_entries", "stream=nb_read_packets",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True,
        ).stdout
        for line in out.splitlines():
            if line.strip().isdigit():
                audio_frames = int(line.strip())
                break
    except Exception:
        pass

    try:
        # Video bitrate: prefer stream bit_rate, else derive from size/duration.
        out = subprocess.run(
            [
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=bit_rate",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True,
        ).stdout.strip()
        if out.isdigit():
            video_bitrate = int(out)
    except Exception:
        pass

    return audio_frames, video_bitrate


def _count_output_frames(path):
    """Count output video frames via ffprobe.

    Uses -count_packets (which counts demuxed packets without decoding) rather
    than -count_frames (which decodes every frame).  For a clean cut output the
    packet count equals the frame count, and packet counting is ~100x faster -
    counting frames on a 5-minute HD file decodes the whole thing and can take
    a minute or more, which is not acceptable as a post-export check.

    Returns 0 if unreadable.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return 0
    try:
        cmd = [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-select_streams", "v:0",
            "-count_packets",
            "-show_entries", "stream=nb_read_packets",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
        return 0
    except Exception:
        return 0


class _Progress:
    """Adapter for smartcut's progress.emit.

    smartcut first emits the total cut-segment count, then emits an
    incrementing done-count as each segment is written.  We translate that
    into a rich update for the UI: percent done, the current phase ("copy" or
    "encode"), and which scene (keep-range) is being processed.

    seg_info maps a cut-segment index -> (phase, scene_number).
    """

    def __init__(self, cb, seg_info, total_scenes):
        self._cb = cb
        self._seg_info = seg_info
        self._total_scenes = total_scenes
        self._total = None

    def emit(self, value):
        if self._cb is None:
            return

        if self._total is None:
            # First emit is the total number of cut segments.
            self._total = max(1, value)
            self._cb({
                "percent": 0,
                "phase": "copy",
                "scene": 1,
                "total_scenes": self._total_scenes,
            })
            return

        pct = int(round(100 * value / self._total))

        phase, scene = self._seg_info.get(value, ("copy", 1))

        self._cb({
            "percent": min(pct, 99),
            "phase": phase,
            "scene": scene,
            "total_scenes": self._total_scenes,
        })


class _Cancel:
    def __init__(self, is_cancelled):
        self._is_cancelled = is_cancelled

    @property
    def cancelled(self):
        return bool(self._is_cancelled())


class _Cancelled(Exception):
    """Raised when the user aborts an export, so the run unwinds promptly and
    the caller can clean up partial files instead of finishing the job."""


def _run_cancellable(cmd, cancel_cb=None):
    """Run a subprocess like ``subprocess.run(..., capture_output=True)`` but
    abortable: while it runs we poll ``cancel_cb`` a few times a second, and if
    it turns true we kill the process and raise ``_Cancelled``.  stderr is
    captured via a temp file so a chatty ffmpeg can't dead-lock on a full pipe.
    With no ``cancel_cb`` it's a plain blocking run."""
    if cancel_cb is None:
        return subprocess.run(cmd, capture_output=True, text=True)
    import tempfile
    with tempfile.TemporaryFile(mode="w+") as errf:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=errf, text=True
        )
        while proc.poll() is None:
            if cancel_cb():
                proc.kill()
                proc.wait()
                raise _Cancelled()
            time.sleep(0.15)
        errf.seek(0)
        err = errf.read()
    return subprocess.CompletedProcess(cmd, proc.returncode, "", err)


def _target_sar(aspect, width, height):
    """Sample (pixel) aspect ratio that yields display aspect ``aspect`` at the
    given coded ``width`` x ``height``, reduced to lowest terms.

    For a target display ratio of n:d, the pixel ratio is SAR = (n*H)/(d*W) -
    e.g. PAL 720x576 wants 16:15 for 4:3 and 64:45 for 16:9.  Returns
    ``(num, den)`` or ``None`` when the aspect isn't one we stamp or the
    dimensions are unknown.
    """
    dar = {"4:3": (4, 3), "16:9": (16, 9)}.get(aspect)
    if not dar or not width or not height:
        return None
    sar = Fraction(dar[0] * height, dar[1] * width)
    return sar.numerator, sar.denominator


def _probe_video_stream(path):
    """``(codec_name, width, height)`` of the first video stream, or
    ``(None, 0, 0)`` if it can't be read."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height",
             "-of", "default=noprint_wrappers=1", path],
            capture_output=True, text=True,
        ).stdout
        codec, w, h = None, 0, 0
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("codec_name="):
                codec = (line.split("=", 1)[1] or "") or None
            elif line.startswith("width="):
                w = int(line.split("=", 1)[1] or 0)
            elif line.startswith("height="):
                h = int(line.split("=", 1)[1] or 0)
        return codec, w, h
    except Exception:
        return None, 0, 0


def _aspect_correct_source(source_path, aspect, cancel_cb=None):
    """Stamp a display aspect (4:3 / 16:9) onto a COPY of the source, for
    .ts/.mp4 output, returning the path to that temporary corrected source (or
    None to leave the source untouched).

    Why the source and not the finished cut: smartcut's output carries many SPS
    - one at every copy/re-encode seam - and the H.264 bitstream filter can't
    reassemble that, so stamping the cut truncates the file at the first seam.
    The source has a single, consistent SPS, so the filter handles it cleanly;
    the cut then inherits the corrected aspect on both its copied and its
    re-encoded frames.  (.mkv doesn't come through here at all - it sets the
    aspect on the container at mux time.)

      * H.264 / HEVC: a sample (pixel) aspect ratio in the SPS/VUI, chosen so
        width*SAR : height gives the wanted display ratio (h264_metadata /
        hevc_metadata sample_aspect_ratio);
      * MPEG-2: the display ratio in the sequence header (mpeg2_metadata
        display_aspect_ratio).

    The coded pixels are untouched.  Returns None - leaving the source as-is and
    skipping the override - for "source", an unsupported codec, an ffmpeg
    failure, or (critically) if the rewrite dropped frames, so a fragile stream
    can never yield a short/truncated export.
    """
    if aspect in (None, "", "source"):
        return None

    codec, width, height = _probe_video_stream(source_path)
    codec = (codec or "").lower()

    if codec in ("h264", "hevc"):
        sar = _target_sar(aspect, width, height)
        if sar is None:
            return None
        bsf = "%s_metadata=sample_aspect_ratio=%d/%d" % (codec, sar[0], sar[1])
    elif codec in ("mpeg2video", "mpeg2"):
        dar = {"4:3": "4/3", "16:9": "16/9"}.get(aspect)
        if dar is None:
            return None
        bsf = "mpeg2_metadata=display_aspect_ratio=%s" % dar
    else:
        logger.info(
            "Display-aspect override skipped: codec %r isn't one we can stamp "
            "losslessly.", codec,
        )
        return None

    before = _count_output_frames(source_path)
    tmp = source_path + ".aspect-src.ts"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", source_path, "-map", "0", "-c", "copy",
        "-bsf:v", bsf,
        # Keep the stream zero-based, as Quick Stream Fix and the cut stages do.
        "-muxpreload", "0", "-muxdelay", "0",
        "-f", "mpegts", tmp,
    ]
    logger.info("Stamping %s display aspect onto the source via %s.", aspect, bsf)
    proc = _run_cancellable(cmd, cancel_cb=cancel_cb)
    if (proc.returncode != 0 or not os.path.exists(tmp)
            or os.path.getsize(tmp) == 0):
        logger.warning(
            "Source aspect rewrite failed (ffmpeg rc=%s); exporting at the "
            "source aspect.\n%s", proc.returncode,
            (proc.stderr or "").strip()[:500],
        )
        _safe_remove(tmp)
        return None

    after = _count_output_frames(tmp)
    if before and after and after < before - 1:
        # The bitstream filter dropped frames - never proceed, or the cut would
        # come out short.  Leave the source untouched and skip the override.
        logger.warning(
            "Source aspect rewrite dropped frames (%s -> %s); exporting at the "
            "source aspect instead of risking a short file.", before, after,
        )
        _safe_remove(tmp)
        return None
    return tmp


def _safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _build_seg_info(source, segments, keep_ranges, fps):
    """Map each cut-segment index to (phase, scene_number).

    phase is "encode" if that segment is re-encoded at a boundary, else
    "copy".  scene_number is which keep-range (1-based) the segment falls in,
    so the dialog can show "Scene 2 of 4" like VideoReDo.
    """
    try:
        adjusted = make_adjusted_segment_times(segments, source)
        cut_segments = make_cut_segments(source, adjusted, False)
    except Exception:
        return {}

    # Scene boundaries in seconds (relative to stream start), to attribute
    # each cut segment to a keep-range.
    scene_spans = []
    for a, b in keep_ranges:
        scene_spans.append((a / fps, (b + 1) / fps))

    info = {}
    for i, cs in enumerate(cut_segments, start=1):
        phase = "encode" if cs.require_recode else "copy"

        mid = (float(cs.start_time) + float(cs.end_time)) / 2.0
        scene = 1
        for s_i, (s0, s1) in enumerate(scene_spans, start=1):
            if s0 <= mid <= s1:
                scene = s_i
                break
        else:
            # Past the last scene start; attribute to nearest by start.
            for s_i, (s0, s1) in enumerate(scene_spans, start=1):
                if mid >= s0:
                    scene = s_i

        info[i] = (phase, scene)

    return info


def _log_segment_plan(seg_info, total_scenes):
    """Log how the cut breaks down into stream-copied vs re-encoded segments.

    smart-cutting copies whole GOPs inside a keep-range (fast, lossless) and
    only re-encodes the partial GOPs at each cut boundary, so this is a useful
    record of how much of the output was copied vs re-encoded.
    """
    if not seg_info:
        logger.debug("Segment plan unavailable.")
        return

    copy_n = sum(1 for (ph, _s) in seg_info.values() if ph == "copy")
    enc_n = sum(1 for (ph, _s) in seg_info.values() if ph == "encode")

    logger.info(
        "Cut plan: %d segment(s) - %d copied, %d re-encoded, across "
        "%d scene(s)",
        len(seg_info), copy_n, enc_n, total_scenes,
    )

    # Per-scene breakdown at debug level.
    by_scene = {}
    for (phase, scene) in seg_info.values():
        counts = by_scene.setdefault(scene, [0, 0])
        counts[0 if phase == "copy" else 1] += 1

    for scene in sorted(by_scene):
        copied, encoded = by_scene[scene]
        logger.debug(
            "  scene %d: %d copied, %d re-encoded", scene, copied, encoded
        )

    # When verbose, list every individual cut-segment (the per-GOP detail) -
    # the closest analogue to the per-segment "noise" VideoReDo records.
    try:
        from utils.applog import is_verbose
        verbose = is_verbose()
    except Exception:
        verbose = False

    if verbose:
        for idx in sorted(seg_info):
            phase, scene = seg_info[idx]
            logger.debug(
                "  cut-segment %d: %s (scene %d)", idx, phase, scene
            )


def _call_smartcut(*args, **kwargs):
    """Call smartcut, capturing its (very chatty) stdout into the log at DEBUG
    when verbose logging is enabled; otherwise call it plainly.  When verbose,
    also raise smartcut's own log level so it emits its full diagnostics."""
    try:
        from utils.applog import is_verbose
        verbose = is_verbose()
    except Exception:
        verbose = False

    if not verbose:
        return smart_cut(*args, **kwargs)

    import io
    import contextlib

    kwargs["log_level"] = "debug"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = smart_cut(*args, **kwargs)
    for line in buf.getvalue().splitlines():
        if line.strip():
            logger.debug("smartcut: %s", line.rstrip())
    return result


def _run_smartcut(source_path, out_path, segments, n_audio, keep_ranges, fps,
                  progress_cb=None, cancel_cb=None):
    """Export, passing through only usable audio tracks.

    Broken secondary tracks (0 channels / no sample rate) are detected and
    excluded up front, because passing them through can make smartcut silently
    drop the video.  If the export still fails, we retry with the primary
    track only as a last resort.

    Returns the number of audio tracks actually written.
    """
    total_scenes = len(keep_ranges)

    # Detect which audio tracks are actually usable and skip the rest.
    usable = _usable_audio_tracks(source_path, n_audio)
    n_written = len(usable)

    logger.debug(
        "Usable audio tracks: %s (of %d)", usable, n_audio
    )

    mc = MediaContainer(source_path)
    seg_info = _build_seg_info(mc, segments, keep_ranges, fps)

    _log_segment_plan(seg_info, total_scenes)

    prog = _Progress(progress_cb, seg_info, total_scenes) if progress_cb else None
    cancel = _Cancel(cancel_cb) if cancel_cb else None

    audio_info = AudioExportInfo(
        output_tracks=_passthru_for_indices(n_audio, usable)
    )

    try:
        err = _call_smartcut(mc, segments, out_path,
                        audio_export_info=audio_info, log_level="warning",
                        progress=prog, cancel_object=cancel)
        if err is None:
            logger.info(
                "smartcut: finished OK (%d audio track%s written)",
                n_written, "" if n_written == 1 else "s",
            )
            return n_written
    except Exception:
        logger.exception("smartcut: first attempt raised")

    # Already down to the primary track only - nothing more to drop.
    if n_written <= 1:
        raise ExportError(
            "Export failed even with a single audio track. The source stream "
            "may need repairing first."
        )

    # Retry: primary audio only.
    logger.warning(
        "smartcut: first attempt failed; retrying with the primary audio "
        "track only (was %d track(s))", n_written,
    )
    mc = MediaContainer(source_path)
    seg_info = _build_seg_info(mc, segments, keep_ranges, fps)
    prog = _Progress(progress_cb, seg_info, total_scenes) if progress_cb else None
    cancel = _Cancel(cancel_cb) if cancel_cb else None

    tracks = [AudioExportSettings(codec="passthru")]
    tracks += [None] * (n_audio - 1)
    audio_info = AudioExportInfo(output_tracks=tracks)

    err = _call_smartcut(mc, segments, out_path,
                    audio_export_info=audio_info, log_level="warning",
                    progress=prog, cancel_object=cancel)
    if err is not None:
        raise ExportError(f"Export failed: {err}")

    logger.info("smartcut: finished OK on retry (primary audio only)")

    return 1


def _mkvmerge_exe():
    """Resolve the mkvmerge executable.

    Prefer the path the user set in Settings; if that's blank or missing, fall
    back to whatever is found on PATH.  Returns None if neither is available, in
    which case the MKV export uses the ffmpeg fallback.
    """
    try:
        from config.loader import ensure_config
        configured = (
            ensure_config().get("paths", {}).get("mkvmerge_binary", "") or ""
        ).strip()
    except Exception:
        configured = ""
    if configured:
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            return configured
        logger.warning(
            "Configured mkvmerge path '%s' is missing or not executable; "
            "falling back to PATH.", configured,
        )
    return shutil.which("mkvmerge")


def _have_mkvmerge():
    """True if an mkvmerge executable is available (configured or on PATH)."""
    return _mkvmerge_exe() is not None


def _aspect_dar(aspect):
    """The display ratio string mkvmerge/ffmpeg want for an aspect override, or
    None for 'source'/unknown."""
    return {"4:3": "4/3", "16:9": "16/9"}.get(aspect)


def _audio_ad_tracks(path):
    """Per audio track (in stream order): language and whether the track is
    flagged as broadcast audio description (visual-impaired narration).

    The .ts carries this in an MPEG-TS descriptor (audio_type), which ffmpeg
    surfaces as the visual_impaired/descriptions dispositions - and players
    label the track "visual impaired [eng]" from it.  Matroska and MP4 have
    their own equivalents, but nothing translates the TS descriptor across
    automatically, so the mux steps use this probe to re-state it explicitly.
    """
    tracks = []
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries",
             "stream=index:stream_tags=language:"
             "stream_disposition=visual_impaired,descriptions",
             "-of", "json", path],
            capture_output=True, text=True,
        ).stdout
        seen = set()
        for s in json.loads(out).get("streams", []):
            idx = s.get("index")
            if idx in seen:      # .ts lists streams once per program
                continue
            seen.add(idx)
            disp = s.get("disposition", {}) or {}
            tracks.append({
                "language": (s.get("tags", {}) or {}).get("language", ""),
                "visual_impaired": bool(disp.get("visual_impaired")),
                "descriptions": bool(disp.get("descriptions")),
            })
    except Exception:
        logger.exception("Audio disposition probe failed")
    return tracks


def _mkvmerge_video_track_id(exe, ts_path):
    """The first video track's id as mkvmerge sees it (needed to target
    --aspect-ratio).  Falls back to 0 - the usual id for a single-video .ts -
    on any failure."""
    try:
        out = subprocess.run(
            [exe, "-J", ts_path], capture_output=True, text=True,
        ).stdout
        data = json.loads(out)
        for t in data.get("tracks", []):
            if t.get("type") == "video":
                return t.get("id", 0)
    except Exception:
        pass
    return 0


def _relax_ts_audio_via_mkvmerge(ts_path, exe, cancel_cb=None):
    """Repackage a .ts whose copied audio players can't read - losslessly, and
    without disturbing the video.

    smartcut's straight copy of some broadcast LATM (notably 6-channel) yields a
    .ts whose audio ffmpeg and media players can't see at all, even though the
    bytes are valid (mkvmerge reads them fine).  So we let mkvmerge rewrite that
    LATM as native AAC into a temporary .mkv, then ffmpeg muxes a new .ts that
    keeps the ORIGINAL video stream untouched (a straight TS-to-TS copy - the
    video never goes through Matroska, which would upset the field-coded frame
    timing and make playback stutter) and takes the now-readable audio from the
    .mkv.  Nothing is decoded or re-encoded, so the audio samples stay
    bit-identical, the video frames and their timing are exactly as smartcut
    wrote them, and A/V sync is preserved.

    Replaces ts_path in place on success.  Returns True if it worked.
    """
    tmp_mkv = ts_path + ".relax.mkv"
    tmp_ts = ts_path + ".relax.ts"
    try:
        r1 = _run_cancellable([exe, "-o", tmp_mkv, ts_path], cancel_cb=cancel_cb)
        if r1.returncode >= 2 or not os.path.exists(tmp_mkv):
            logger.warning(
                "mkvmerge could not repackage the .ts audio: %s",
                (r1.stderr or r1.stdout or "").strip(),
            )
            return False
        # Original video (and any subtitles) from the .ts, readable audio from
        # the .mkv.  The original unreadable audio is simply not mapped.
        r2 = _run_cancellable(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", ts_path, "-i", tmp_mkv,
             "-map", "0:v", "-map", "1:a", "-map", "0:s?",
             "-c", "copy", "-f", "mpegts", tmp_ts],
            cancel_cb=cancel_cb,
        )
        if r2.returncode != 0 or not os.path.exists(tmp_ts):
            logger.warning(
                "ffmpeg could not graft the repackaged audio onto the .ts: %s",
                (r2.stderr or "").strip(),
            )
            return False
        os.replace(tmp_ts, ts_path)
        return True
    finally:
        for p in (tmp_mkv, tmp_ts):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


def _mkv_audio_ok(mkv_path, cancel_cb=None):
    """True if the freshly-muxed .mkv's audio actually decodes.

    mkvmerge repackages broadcast LATM-muxed AAC into Matroska's native A_AAC
    using a single fixed codec configuration.  That's fine when the broadcast's
    channel configuration is constant, but Channel 4 HD carries it in-band and
    it isn't constant, so the repackaged track ends up referencing channel
    elements the one fixed header never declared - and the decoder rejects every
    such frame ("channel element ... is not allocated / Invalid data found").
    The result is a silent .mkv even though mkvmerge reported success.  We catch
    it by decoding the first minute of audio and watching for those errors.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-v", "error",
        "-i", mkv_path, "-map", "0:a:0?", "-t", "60", "-f", "null", "-",
    ]
    try:
        result = _run_cancellable(cmd, cancel_cb=cancel_cb)
    except _Cancelled:
        raise
    except Exception:
        return True            # never block an export on a verification hiccup
    err = result.stderr or ""
    return not (
        "not allocated" in err
        or "Invalid data found" in err
        or "Error submitting packet" in err
    )


def _write_mkv_chapters(ts_path, mkv_path, segment_durations, aspect="source",
                        cancel_cb=None, progress_cb=None):
    """Build the final .mkv from the cut .ts, one chapter per scene.

    mkvmerge (mkvtoolnix) is preferred: it repackages the broadcast LATM-muxed
    AAC into Matroska's native A_AAC, which is the most portable result.  But it
    does so with a single fixed configuration, and Channel 4 HD's in-band,
    varying channel config can't be captured that way - the repackaged audio
    then won't decode (a silent .mkv).  So we verify the muxed audio and, if it
    doesn't decode, rebuild with ffmpeg, which stream-copies the LATM frames
    untouched (generic A_MS/ACM wrapper) - less "native", but it actually plays
    in Plex/Jellyfin and other ffmpeg-based players.  The video is copied
    untouched either way.  If mkvmerge isn't installed we use ffmpeg directly.

    A 4:3/16:9 `aspect` override is written as the container's display
    dimensions (mkvmerge --aspect-ratio, ffmpeg -aspect) rather than into the
    H.264 bitstream, so field-coded HD keeps the seek points smartcut produced.

    The mux and the audio verify/rebuild have no measurable progress but can take
    20-30s, so we flag each stage to `progress_cb` with a negative percent - a
    cue for the UI to show a busy/pulsing bar instead of a frozen one.
    """
    def _busy(phase):
        if progress_cb is not None:
            progress_cb({"phase": phase, "percent": -1})

    if cancel_cb is not None and cancel_cb():
        raise _Cancelled()
    exe = _mkvmerge_exe()
    if exe:
        _busy("finalise_mkv")
        _write_mkv_chapters_mkvmerge(ts_path, mkv_path, segment_durations, exe,
                                     aspect=aspect, cancel_cb=cancel_cb)
        if _mkv_audio_ok(mkv_path, cancel_cb=cancel_cb):
            logger.info(
                "MKV audio repackaged to native AAC by mkvmerge "
                "(verified decodable)."
            )
            return False
        logger.warning(
            "mkvmerge's MKV audio won't decode; rebuilding with ffmpeg to keep "
            "the broadcast audio intact (see the export summary for why)."
        )
        _busy("rebuild_audio")
        _write_mkv_chapters_ffmpeg(ts_path, mkv_path, segment_durations,
                                   aspect=aspect, cancel_cb=cancel_cb)
        return True
    else:
        logger.warning(
            "mkvmerge (mkvtoolnix) not found; using ffmpeg for the MKV.  The "
            "audio is kept as broadcast LATM in the generic A_MS/ACM wrapper - "
            "it plays in Plex/Jellyfin and other ffmpeg-based players.  Install "
            "mkvtoolnix (or set its path in Settings) for native AAC where the "
            "broadcast allows it."
        )
        _busy("rebuild_audio")
        _write_mkv_chapters_ffmpeg(ts_path, mkv_path, segment_durations,
                                   aspect=aspect, cancel_cb=cancel_cb)
        return True


def _write_mkv_chapters_mkvmerge(ts_path, mkv_path, segment_durations, exe,
                                 aspect="source", cancel_cb=None):
    """Mux the cut .ts into .mkv with mkvmerge (native AAC, lossless)."""
    # mkvmerge's simple/OGM chapter format: absolute HH:MM:SS.mmm timestamps.
    lines = []
    cursor_ms = 0
    for i, dur in enumerate(segment_durations, start=1):
        start_ms = cursor_ms
        total_s = start_ms // 1000
        h, rem = divmod(total_s, 3600)
        m, s = divmod(rem, 60)
        ms = start_ms % 1000
        lines.append("CHAPTER%02d=%02d:%02d:%02d.%03d" % (i, h, m, s, ms))
        lines.append(
            "CHAPTER%02dNAME=Chapter %02d (%02d:%02d:%02d)" % (i, i, h, m, s)
        )
        cursor_ms = start_ms + int(round(float(dur) * 1000))

    chapter_path = mkv_path + ".chapters.txt"
    with open(chapter_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    try:
        cmd = [
            exe,
            "-o", mkv_path,
            "--chapters", chapter_path,
            "--no-subtitles",            # drop DVB subtitles, as before
        ]
        dar = _aspect_dar(aspect)
        if dar:
            # Set the display aspect on the container (no bitstream rewrite).
            vid = _mkvmerge_video_track_id(exe, ts_path)
            cmd += ["--aspect-ratio", "%d:%s" % (vid, dar)]
            logger.info("Setting %s display aspect on the MKV (track %d).",
                        aspect, vid)
        # Re-state broadcast audio-description marking.  The .ts carries it as
        # an MPEG-TS descriptor which mkvmerge doesn't translate, so the MKV
        # would show a bare "[eng]" where the .ts shows "visual impaired
        # [eng]".  Matroska's own equivalent is the visual-impaired track flag
        # plus a track name; map the .ts audio order onto mkvmerge's track ids
        # and set both on any flagged track.
        ad = _audio_ad_tracks(ts_path)
        if any(t["visual_impaired"] for t in ad):
            try:
                ident = json.loads(subprocess.run(
                    [exe, "-J", ts_path], capture_output=True, text=True,
                ).stdout)
                audio_tids = [t.get("id") for t in ident.get("tracks", [])
                              if t.get("type") == "audio"]
            except Exception:
                logger.exception("mkvmerge identify failed; AD flag not set")
                audio_tids = []
            # --visual-impaired-flag needs MKVToolNix 52+ (2021); older ones
            # reject unknown options outright, so probe before using it.
            try:
                have_vi_flag = "--visual-impaired-flag" in subprocess.run(
                    [exe, "--help"], capture_output=True, text=True,
                ).stdout
            except Exception:
                have_vi_flag = False
            for info, tid in zip(ad, audio_tids):
                if not info["visual_impaired"] or tid is None:
                    continue
                if have_vi_flag:
                    cmd += ["--visual-impaired-flag", "%d:1" % tid]
                cmd += ["--track-name", "%d:visual impaired" % tid]
                logger.info(
                    "Marking MKV track %d as visual impaired (broadcast "
                    "audio description).", tid,
                )
        cmd.append(ts_path)
        result = _run_cancellable(cmd, cancel_cb=cancel_cb)
        # mkvmerge: 0 = OK, 1 = OK with warnings, 2 = error.
        if result.returncode >= 2 or not os.path.exists(mkv_path):
            raise ExportError(
                "mkvmerge failed:\n%s"
                % ((result.stderr or result.stdout or "").strip())
            )
        if result.returncode == 1:
            logger.info(
                "mkvmerge completed with warnings:\n%s",
                (result.stdout or "").strip(),
            )
    finally:
        if os.path.exists(chapter_path):
            os.remove(chapter_path)


def _write_mkv_chapters_ffmpeg(ts_path, mkv_path, segment_durations,
                               aspect="source", cancel_cb=None):
    """Fallback MKV mux via ffmpeg (lossless, but audio stays LATM-in-ACM)."""
    chapters = [";FFMETADATA1"]
    cursor_ms = 0
    for i, dur in enumerate(segment_durations, start=1):
        start_ms = cursor_ms
        end_ms = cursor_ms + int(round(float(dur) * 1000))

        # Human-readable timecode of the chapter start (HH:MM:SS).
        total_s = start_ms // 1000
        h, rem = divmod(total_s, 3600)
        m, s = divmod(rem, 60)
        tc = f"{h:02d}:{m:02d}:{s:02d}"

        chapters += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start_ms}",
            f"END={end_ms}",
            f"title=Chapter {i:02d} ({tc})",
        ]
        cursor_ms = end_ms

    meta_path = mkv_path + ".chapters.txt"
    with open(meta_path, "w") as f:
        f.write("\n".join(chapters) + "\n")

    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", ts_path,
            "-i", meta_path,
            "-map", "0:v",
            "-map", "0:a?",
            "-map_metadata", "1",
            "-c", "copy",
        ]
        # ffmpeg carries the visual-impaired disposition through to Matroska's
        # track flag by itself, but players display the track NAME - name any
        # audio-description track for parity with the .ts ("visual impaired").
        for i, info in enumerate(_audio_ad_tracks(ts_path)):
            if info["visual_impaired"]:
                cmd += ["-metadata:s:a:%d" % i, "title=visual impaired"]
        dar = _aspect_dar(aspect)
        if dar:
            # Container display aspect, set without re-encoding the video.
            cmd += ["-aspect", dar]
            logger.info("Setting %s display aspect on the MKV (ffmpeg).", aspect)
        cmd.append(mkv_path)
        result = _run_cancellable(cmd, cancel_cb=cancel_cb)
        if result.returncode != 0:
            raise ExportError(
                f"Adding MKV chapters failed:\n{result.stderr.strip()}"
            )
    finally:
        if os.path.exists(meta_path):
            os.remove(meta_path)


def _transcode_to_mp4(
        ts_path, mp4_path, video_codec, interlaced,
        total_seconds=0.0, phase="recode_audio", progress_cb=None,
        cancel_cb=None,
):
    """Convert the freshly-cut .ts into an .mp4.

    Broadcast audio (LATM-muxed AAC, or MP2 on SD) and SD MPEG-2 video can't
    be stream-copied into MP4, which is why a straight copy fails.  So:
      - audio is ALWAYS re-encoded to AAC (cheap, and the only way into MP4),
      - video is stream-copied when it's already MP4-friendly (H.264/HEVC),
        so HD stays lossless and fast; otherwise it's re-encoded to H.264,
      - an interlaced source that has to be re-encoded is deinterlaced.

    ffmpeg reports progress on stdout (-progress) so the bar can move and show
    an ETA during the encode.  We write to a temporary name in the same folder
    and only rename it into place when finished, so an external tool watching
    for the final filename can't grab or rename a half-written file.
    """
    copy_video = (video_codec or "").lower() in ("h264", "hevc")

    # Write under a non-media temp name, then atomically rename on success.
    part_path = mp4_path + ".vrd-encoding.tmp"

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-i", ts_path,
        "-map", "0:v:0",
        "-map", "0:a?",
    ]

    if copy_video:
        cmd += ["-c:v", "copy"]
    else:
        cmd += [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
        ]
        if interlaced:
            cmd += ["-vf", "yadif"]

    cmd += [
        "-c:a", "aac",
        "-b:a", "192k",
    ]
    # Name any audio-description track ("visual impaired"), for parity with
    # the .ts label.  MP4 has no equivalent of the TS descriptor and its muxer
    # drops per-track titles, so the name goes in the handler atom - which is
    # what players display as the track label for MP4.
    for i, info in enumerate(_audio_ad_tracks(ts_path)):
        if info["visual_impaired"]:
            cmd += ["-metadata:s:a:%d" % i, "handler_name=visual impaired"]
    cmd += [
        "-movflags", "+faststart",
        "-f", "mp4",
        "-progress", "pipe:1",
        part_path,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        for line in proc.stdout:
            if cancel_cb is not None and cancel_cb():
                proc.kill()
                proc.wait()
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except OSError:
                        pass
                raise _Cancelled()
            line = line.strip()
            if (
                line.startswith("out_time=")
                and total_seconds > 0
                and progress_cb is not None
            ):
                stamp = line.split("=", 1)[1].strip()
                try:
                    h, m, s = stamp.split(":")
                    done = int(h) * 3600 + int(m) * 60 + float(s)
                except ValueError:
                    continue
                pct = max(1, min(99, int(done / total_seconds * 100)))
                progress_cb({
                    "percent": pct,
                    "phase": phase,
                    "scene": 1,
                    "total_scenes": 1,
                })
        err = proc.stderr.read()
    finally:
        proc.wait()

    if proc.returncode != 0:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass
        raise ExportError(
            "MP4 conversion failed:\n" + (err or "").strip()
        )

    os.replace(part_path, mp4_path)


def _audio_codecs(path):
    """``codec_name`` for each audio stream, in order, de-duplicated by index
    (an MPEG-TS file can list a stream once per program)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index,codec_name",
             "-of", "default=noprint_wrappers=1", path],
            capture_output=True, text=True,
        ).stdout
    except Exception:
        return []
    records = []
    cur = None
    for line in out.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if key == "index":
            cur = {"index": val}
            records.append(cur)
        elif cur is not None:
            cur[key] = val
    seen = set()
    codecs = []
    for rec in records:
        idx = rec.get("index")
        if idx in seen:
            continue
        seen.add(idx)
        codecs.append(rec.get("codec_name", ""))
    return codecs


def _audio_needs_rebuild(source_path):
    """True if the source audio has to be decoded and re-encoded rather than
    stream-copied from a mid-stream cut.

    aac_latm (LATM/LOAS AAC, used by UK DVB H.264 broadcasts) carries its
    decoder config inline, so a copy that begins partway through is undecodable
    and won't mux into MKV/MP4; it must be rebuilt, as VideoReDo does.  The
    decision is taken from the *intact* source rather than from the cut output,
    because a broken LATM cut is unreliable to probe - ffprobe can even mislabel
    it as mp3 - whereas the source codec is always reported correctly."""
    for codec in _audio_codecs(source_path):
        if (codec or "").lower() == "aac_latm":
            return True
    return False


def _has_audio(path):
    """True if the file has at least one audio stream (used to confirm a rebuild
    actually produced audio)."""
    return len(_audio_codecs(path)) > 0


def _container_seconds(path):
    """Container duration in seconds (0.0 if it can't be read)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def _progress_seconds(prog_path):
    """Largest out_time an ffmpeg -progress run recorded, in seconds."""
    try:
        with open(prog_path, "r") as f:
            text = f.read()
    except OSError:
        return 0.0
    best = 0
    for line in text.splitlines():
        if line.startswith("out_time_us="):
            v = line.split("=", 1)[1].strip()
            if v.lstrip("-").isdigit():
                best = max(best, int(v))
    return best / 1_000_000.0


def _one_audio_track_decodes(path, track=0):
    """True if audio track ``track`` decodes to a usable length.

    The aac_latm a mid-stream copy produces is normally perfectly decodable, so
    the broadcast audio can be kept as-is (copied for .ts/.mkv, re-encoded only
    for .mp4).  But a broadcast can change audio configuration partway through
    (stereo<->surround at break points), and ffmpeg re-initialises its decoder
    across such a change - a recoverable event we must NOT treat as fatal.  So
    we decode the whole track and judge by how much actually came out, instead
    of aborting on the first warning (which used to push these legitimately-
    copyable files into a needless rebuild).  Only a track that yields almost
    nothing fails.  It's an audio-only decode to null, far cheaper than the
    rebuild it guards."""
    expected = _container_seconds(path)
    prog = path + (".decodecheck%d" % track)
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-progress", prog, "-i", path,
             "-map", "0:a:%d" % track, "-vn", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        decoded = _progress_seconds(prog)
    except Exception:
        logger.exception("Audio decode check raised")
        return False
    finally:
        try:
            os.remove(prog)
        except OSError:
            pass

    # A usable track decodes essentially the whole way; a broken one yields next
    # to nothing.  Require at least half the running time, with a small absolute
    # floor so very short jobs aren't tripped up.
    floor = max(2.0, 0.5 * expected) if expected > 0 else 2.0
    ok = result.returncode == 0 and decoded >= floor
    if not ok:
        tail = (result.stderr or "").strip().splitlines()
        logger.warning(
            "Audio track %d not usable as-is (rc=%s, decoded %.1fs of ~%.1fs); "
            "rebuilding from source. ffmpeg: %s",
            track, result.returncode, decoded, expected,
            tail[-1] if tail else "(no error output)",
        )
    return ok


def _audio_decodes(path):
    """True if the file's first audio track decodes to a usable length."""
    return _one_audio_track_decodes(path, 0)


def _all_audio_tracks_decode(path):
    """True only if *every* audio track in the file decodes to a usable length.

    Used to verify a multi-track lossless graft: a mis-synced or mangled
    secondary track (e.g. audio description) must not be shipped, so if any
    track fails to decode the caller falls back to the re-encode rebuild.  A
    file with no audio tracks is considered fine (there's nothing to break).
    """
    n = len(_audio_codecs(path))
    if n <= 1:
        return _audio_decodes(path)
    for t in range(n):
        if not _one_audio_track_decodes(path, t):
            return False
    return True


def _cut_audio_intact(path):
    """True if the cut's first audio track is still structurally sound AAC.

    Inside the aac_latm branch the source is known to be AAC, so if the cut's
    audio is no longer in the AAC family we know smartcut's straight copy
    mangled it - ffprobe reports the mistyped result as mp3, and neither
    mkvmerge nor players can read it.  When that happens the copy must not be
    trusted (a lenient decode check can still pass on it); the audio has to be
    rebuilt from source instead.
    """
    codecs = _audio_codecs(path)
    if not codecs:
        return False
    return (codecs[0] or "").lower() in ("aac", "aac_latm")


def _source_start_seconds(source_path):
    """The source container's start time, in seconds.

    Reported in the rebuild log as a diagnostic - it is the origin smartcut
    measures its segment times from (the earliest stream start; on these
    broadcast .ts recordings the audio is muxed a little ahead of the video).
    It is no longer added to the atrim/-ss selection: ffmpeg already rebases
    each input to a zero-based timeline, so the container-relative segment times
    line up on their own.  An earlier version added this value back, which
    pulled the rebuilt sound ahead of the picture by the whole origin on
    captures whose first PTS was not zero (the "out of sync until Quick Stream
    Fixed" bug)."""
    try:
        import av
        with av.open(source_path) as container:
            if container.start_time is not None:
                return container.start_time / 1_000_000.0
            starts = [
                float(s.start_time) * float(s.time_base or 0)
                for s in container.streams
                if s.start_time is not None and s.time_base
            ]
            if starts:
                return min(starts)
    except Exception:
        logger.exception("Could not read source start time")
    return 0.0


def _audio_track_info(source_path):
    """``(channels, profile)`` for each audio stream in the source, in order.

    De-duplicated by stream index, because an MPEG-TS file can list the same
    stream once per program.  ``profile`` lets the rebuild tell an HE-AAC
    (SBR/PS) track - which has to be seeked rather than decoded from the start -
    from an ordinary LC track; ``channels`` picks a sensible per-track bitrate."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index,channels,profile",
             "-of", "default=noprint_wrappers=1", source_path],
            capture_output=True, text=True,
        ).stdout
    except Exception:
        return []
    # ffprobe prints the fields in its own order, so parse key=value rather
    # than relying on column positions.  A new "index=" starts a new stream.
    records = []
    cur = None
    for line in out.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if key == "index":
            cur = {"index": val}
            records.append(cur)
        elif cur is not None:
            cur[key] = val
    seen = set()
    info = []
    for rec in records:
        idx = rec.get("index")
        if idx in seen:
            continue
        seen.add(idx)
        try:
            ch = int(rec.get("channels", "0"))
        except ValueError:
            ch = 0
        info.append((ch, rec.get("profile", "")))
    return info


def _audio_track_starts(source_path):
    """Per-track audio stream start PTS in seconds (``None`` where unknown).

    A sibling of ``_audio_track_info`` used only for the rebuild's sync
    diagnostics: the audio rebuild anchors every track on the container origin,
    so logging each track's own start PTS makes a track whose start differs
    from that origin - the one case the container-relative anchor wouldn't suit
    - visible in the log.  De-duplicated by stream index like the sibling, as a
    .ts file can list the same stream once per program.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index,start_time",
             "-of", "default=noprint_wrappers=1", source_path],
            capture_output=True, text=True,
        ).stdout
    except Exception:
        return []
    records = []
    cur = None
    for line in out.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if key == "index":
            cur = {"index": val}
            records.append(cur)
        elif cur is not None:
            cur[key] = val
    starts = []
    seen = set()
    for rec in records:
        idx = rec.get("index")
        if idx in seen:
            continue
        seen.add(idx)
        try:
            starts.append(float(rec["start_time"]))
        except (KeyError, ValueError):
            starts.append(None)
    return starts


def _verify_audio_sync(out_path, source_path, scene_start_rel):
    """Post-export sanity check: confirm the rebuilt audio lines up with the
    source at the first cut, and log the measured offset.

    Run only when the ``verify_audio_sync`` setting is on - it decodes two short
    audio windows and cross-correlates them, so it adds a couple of seconds and
    is off by default.  ``scene_start_rel`` is the first kept scene's start in
    the source's container-relative (zero-based) seconds - the same timeline
    ffmpeg's ``-ss`` seeks on - so the exported audio's start should line up
    with the source there.  A small residual (under ~60 ms) is expected because
    the rebuild re-encodes to AAC, whose priming delay shifts the start by up to
    a frame; anything larger means the picture and sound have parted company.
    """
    try:
        import numpy as np
        sr = 8000

        def decode(path, ss, dur):
            cmd = ["ffmpeg", "-v", "error"]
            if ss is not None:
                cmd += ["-ss", f"{ss:.3f}"]
            cmd += ["-i", path, "-map", "0:a:0", "-t", f"{dur:.3f}",
                    "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
            raw = subprocess.run(cmd, capture_output=True).stdout
            return np.frombuffer(raw, dtype=np.float32)

        scene_start_rel = max(0.0, float(scene_start_rel))
        out_a = decode(out_path, None, 6.0)            # exported audio, from its start
        src_ss = max(0.0, scene_start_rel - 3.0)       # source window around the cut
        src_a = decode(source_path, src_ss, 12.0)
        if out_a.size < sr or src_a.size < sr:
            logger.info("Audio sync check: too little audio to measure; skipped.")
            return

        probe = out_a[:sr * 4]
        probe = probe - probe.mean()
        window = src_a - src_a.mean()
        if probe.size > window.size:
            probe = probe[:window.size]
        corr = np.correlate(window, probe, mode="valid")
        if corr.size == 0:
            logger.info("Audio sync check: correlation window empty; skipped.")
            return
        measured_lag = int(np.argmax(corr)) / sr
        expected_lag = scene_start_rel - src_ss        # where the start should land
        offset = measured_lag - expected_lag

        if abs(offset) <= 0.060:
            logger.info(
                "Audio sync check: first cut in sync (measured offset %+.3fs, "
                "within AAC re-encode tolerance).", offset,
            )
        else:
            logger.warning(
                "Audio sync check: first cut OFF by %+.3fs - the rebuilt audio "
                "does not line up with the source at the cut.", offset,
            )
    except Exception:
        logger.exception("Audio sync check could not be run")


def _graft_one_track_audio(source_path, track_idx, scene_times, offsets,
                           out_path, origin_seconds):
    """Build a single grafted audio track: copy the source's own audio packets
    for the kept scene ranges and re-stamp them onto a continuous, zero-based
    output timeline anchored to the video offsets.  No decode, no re-encode -
    the broadcast AAC-LATM frames are passed through byte-for-byte.

    ``origin_seconds`` is the *shared* container origin all tracks are measured
    against (the same zero-point scene_times use).  It must NOT be each track's
    own start time: the audio streams in a .ts can begin ~0.1s apart, so anchor
    a secondary track to its own start and it lands offset from the picture.
    Anchoring every track to one common origin keeps them locked together and to
    the video.

    Returns the number of packets written (0 means nothing matched).
    """
    import av  # PyAV - already a smartcut dependency

    written = 0
    with av.open(source_path) as inp:
        if track_idx >= len(inp.streams.audio):
            return 0
        astream = inp.streams.audio[track_idx]
        tb = astream.time_base

        with av.open(out_path, "w", format="mpegts") as out:
            ostream = out.add_stream_from_template(astream)
            out_tb = ostream.time_base

            for pkt in inp.demux(astream):
                if pkt.pts is None:
                    continue
                t = float(pkt.pts * tb) - origin_seconds
                # Which kept scene does this packet's timestamp fall in?
                for (a, b), off in zip(scene_times, offsets):
                    a = float(a)
                    b = float(b)
                    if a <= t < b:
                        new_t = (t - a) + off
                        new_pts = int(round(new_t / float(out_tb)))
                        pkt.stream = ostream
                        pkt.pts = new_pts
                        pkt.dts = new_pts
                        out.mux(pkt)
                        written += 1
                        break
    return written


def _graft_source_audio(video_path, source_path, scene_times, n_audio,
                        progress_cb=None, cancel_cb=None):
    """Replace ``video_path``'s audio with a LOSSLESS copy of the source audio
    for the kept scene ranges - the broadcast AAC-LATM frames copied straight
    through (never decoded or re-encoded) and muxed onto the untouched video,
    matching VideoReDo's lossless audio cutting.

    Where :func:`_rebuild_audio_from_source` decodes and re-encodes the audio
    (sample-accurate but lossy, and adding the AAC encoder's ~one-frame priming
    delay), this copies the source's *own* audio packets.  Each kept range's
    packets are selected by their timestamp and re-stamped onto a continuous,
    zero-based output timeline anchored to the cumulative *video* duration of the
    preceding scenes, so the sound tracks the picture with no cumulative drift
    across the cuts.  The audio cut lands within one AAC frame (~21ms) of the
    video cut, which is exactly what a lossless cut must do - you cannot trim
    mid-frame without re-encoding - and is how VideoReDo behaves.

    Every usable audio track is grafted (each gets its own output stream copied
    from the source's parameters); a malformed secondary track (e.g. a 0-channel
    descriptions track) is skipped, as for the straight passthrough.  Returns
    the number of tracks grafted (0 on failure).  The caller verifies the result
    (for more than one track, that every track decodes) and falls back to the
    re-encode rebuild if it does not, so a stream this cannot graft cleanly is
    never shipped.
    """
    if not scene_times:
        return 0

    # Per-scene cumulative video offset: scene i's audio starts where the video
    # of all the earlier scenes ends, so the two stay locked together.
    durs = [float(b) - float(a) for a, b in scene_times]
    offsets = [sum(durs[:i]) for i in range(len(scene_times))]

    usable = _usable_audio_tracks(source_path, n_audio)
    if not usable:
        logger.warning("Audio graft: no usable audio tracks found.")
        return 0

    # Every usable track is grafted against one shared container origin (below),
    # which keeps them locked to each other and to the video - so a secondary
    # track (typically audio description on Channel 4 HD) stays in sync.  A
    # packet copy lands each track's audio within one AAC frame (~21ms) of the
    # video cut, exactly as a lossless cut must.  The caller verifies the result
    # (for more than one track, that *every* track decodes) and falls back to
    # the re-encode rebuild if anything is off, so a stream this cannot graft
    # cleanly is never shipped.

    # One shared origin for every track, taken from the primary (first usable)
    # track, so all tracks stay locked to each other and to the scene_times
    # timeline.  On these broadcast .ts the audio defines the container origin,
    # which is why the primary track's start is the right zero-point; using each
    # track's own start would shift any secondary track out of sync.
    origin_seconds = 0.0
    try:
        import av
        with av.open(source_path) as _probe:
            a0 = _probe.streams.audio[usable[0]]
            if a0.start_time is not None:
                origin_seconds = float(a0.start_time * a0.time_base)
    except Exception:
        origin_seconds = _source_start_seconds(source_path)

    if progress_cb is not None:
        progress_cb({"percent": -1, "phase": "graft_audio"})

    logger.info(
        "Grafting broadcast audio losslessly for %d track(s) %s across %d "
        "scene(s) (copy - no re-encode, origin %.3fs).",
        len(usable), ", ".join(str(t) for t in usable), len(scene_times),
        origin_seconds,
    )

    tmp_tracks = []
    try:
        for t in usable:
            if cancel_cb is not None and cancel_cb():
                return 0
            tmp = video_path + (".graft_a%d.ts" % t)
            try:
                n = _graft_one_track_audio(
                    source_path, t, scene_times, offsets, tmp,
                    origin_seconds,
                )
            except Exception:
                logger.exception("Audio graft failed on track %d", t)
                _safe_remove(tmp)
                return 0
            if n <= 0:
                logger.warning(
                    "Audio graft: track %d produced no packets.", t
                )
                _safe_remove(tmp)
                return 0
            tmp_tracks.append(tmp)
            logger.info("  track %d: grafted %d audio packets.", t, n)

        # Mux every grafted track onto the untouched cut video in one copy pass,
        # trimmed to the video's own length so no stray audio frame hangs past
        # the picture.
        vdur = _container_seconds(video_path)
        out_tmp = video_path + ".grafted.ts"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-i", video_path]
        for tmp in tmp_tracks:
            cmd += ["-i", tmp]
        cmd += ["-map", "0:v"]
        for i in range(len(tmp_tracks)):
            cmd += ["-map", "%d:a" % (i + 1)]
        cmd += ["-c", "copy"]
        if vdur > 0:
            cmd += ["-t", "%.3f" % vdur]
        cmd += ["-muxpreload", "0", "-muxdelay", "0", out_tmp]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0 or not os.path.exists(out_tmp):
            tail = (res.stderr or "").strip().splitlines()
            logger.warning(
                "Audio graft mux failed (rc=%s): %s",
                res.returncode, tail[-1] if tail else "(no error output)",
            )
            _safe_remove(out_tmp)
            return 0

        os.replace(out_tmp, video_path)
        logger.info(
            "Audio graft complete (lossless broadcast audio kept, %d track%s).",
            len(tmp_tracks), "" if len(tmp_tracks) == 1 else "s",
        )
        return len(tmp_tracks)
    finally:
        for tmp in tmp_tracks:
            _safe_remove(tmp)


def _rebuild_audio_from_source(video_path, source_path, scene_times,
                               n_audio, progress_cb=None, cancel_cb=None):
    """Replace ``video_path``'s audio by re-encoding the source audio for the
    kept scene time-ranges and muxing it onto the (untouched) video.

    Needed for audio that can't be stream-copied from a mid-stream start - most
    notably aac_latm (LATM/LOAS AAC), whose decoder config is carried inline, so
    a copy that begins after a config frame is undecodable.  Decoding and
    re-encoding (as VideoReDo does) gives every output a valid header.

    ``scene_times`` are (start, end) seconds as smartcut reports them, measured
    from the source container start.  They are used to drive ``atrim`` (and the
    ``-ss`` seek) directly, with *no* origin added back: ffmpeg shifts every
    input down to a zero-based timeline (it subtracts the container start before
    the filtergraph and before an input ``-ss``), so a container-relative time
    is exactly what those selectors expect.  Adding the container start back
    double-counted it, pulling the rebuilt sound ahead of the picture by the
    whole origin (e.g. 1.4s) on captures whose first PTS was not zero - the
    classic "in sync once Quick Stream Fixed, out of sync raw" symptom, since a
    QSF rebases the origin to zero and so happened to add nothing.  Ordinary
    tracks are all rebuilt, so a multi-track file (e.g. a 5.1 track plus a
    stereo track) keeps them, each re-encoded at a bitrate matched to its
    channels; HE-AAC (SBR/PS) tracks are skipped, as they cannot be re-encoded
    in sync.

    Two ffmpeg quirks shape how each track is read:

    * An HE-AAC (SBR/PS) track decoded continuously from the file start goes
      silent after the first few minutes, so those tracks are read from a
      per-scene ``-ss`` seek instead.
    * An aac_latm track carries its channel layout inline, which a seek would
      skip past (a 5.1 track would collapse to stereo), so ordinary tracks are
      decoded from the start and trimmed with ``atrim`` - which is also frame
      accurate, keeping them perfectly in sync.

    ``progress_cb``, if given, receives the usual phase dicts so the UI can show
    this (potentially slow) decode pass moving rather than a frozen "Verifying
    output".  Returns True on success.
    """
    info = _audio_track_info(source_path)
    n_tracks = n_audio if n_audio and n_audio > 0 else max(1, len(info))
    n_scenes = len(scene_times)

    def is_sbr(t):
        prof = info[t][1] if t < len(info) else ""
        return bool(prof) and "HE" in prof.upper()

    def chans(t):
        ch = info[t][0] if t < len(info) else 0
        return ch if ch > 0 else 2

    # An HE-AAC (SBR/PS) track cannot be re-encoded in sync: decoded from the
    # start it falls silent after a few minutes, and decoded from a seek its
    # SBR framing drifts by seconds.  Rather than ship a drifting or silent
    # track we leave those out (a Quick Stream Fix drops them too).  If *every*
    # track is SBR we still rebuild them all, so the video keeps some audio.
    build_tracks = [t for t in range(n_tracks) if not is_sbr(t)]
    if not build_tracks:
        build_tracks = list(range(n_tracks))
    skipped = [t for t in range(n_tracks) if t not in build_tracks]
    if skipped:
        logger.warning(
            "Skipping audio track(s) %s during rebuild: HE-AAC (SBR) audio "
            "cannot be re-encoded in sync from a mid-stream cut.",
            ", ".join(str(t) for t in skipped),
        )

    need_seek = any(is_sbr(t) for t in build_tracks)

    # Sync diagnostics (a few lines, not per-frame): record how each rebuilt
    # track is anchored, so should the picture and sound ever part company the
    # log alone shows what was selected.  ffmpeg's atrim/-ss work on the
    # container-rebased (zero-based) timeline, so each scene's container-relative
    # start *is* the selector; the absolute source video PTS it maps to is
    # (start + container origin).  A track whose own start PTS differs from that
    # origin is flagged, as the shared container-relative anchor assumes they
    # match (they do on these broadcast .ts captures, where audio defines the
    # origin).
    if scene_times:
        container_origin = _source_start_seconds(source_path)
        track_starts = _audio_track_starts(source_path)
        s0 = float(scene_times[0][0])
        e0 = float(scene_times[0][1])
        logger.info(
            "Audio rebuild sync: container origin %.3fs; first kept scene "
            "%.3f-%.3fs -> source video PTS %.3f-%.3fs; rebuilding track(s) %s.",
            container_origin, s0, e0,
            s0 + container_origin, e0 + container_origin,
            ", ".join(str(t) for t in build_tracks) or "none",
        )
        for t in build_tracks:
            ts = track_starts[t] if t < len(track_starts) else None
            if ts is None:
                logger.info(
                    "  track %d: atrim anchor %.3fs (stream start unknown).",
                    t, s0,
                )
                continue
            drift = ts - container_origin
            if abs(drift) < 0.005:
                logger.info(
                    "  track %d: atrim anchor %.3fs, stream start %.3fs "
                    "(aligned with origin).", t, s0, ts,
                )
            else:
                logger.warning(
                    "  track %d: stream start %.3fs differs from container "
                    "origin %.3fs by %+.3fs - watch this track for drift.",
                    t, ts, container_origin, drift,
                )

    # input 0 is the video; input 1 is the source decoded from the start (used
    # to atrim ordinary tracks accurately).  When an SBR track is present we add
    # one extra seeked source input per scene, used only by those tracks.
    tmp = video_path + ".audiofix.ts"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", video_path,
        "-i", source_path,
    ]
    seek_base = 2
    total_seconds = 0.0
    for (s, e) in scene_times:
        total_seconds += max(0.0, float(e) - float(s))
    if need_seek:
        for (s, e) in scene_times:
            # ffmpeg's input -ss is itself zero-based (it seeks relative to the
            # container start), so the container-relative scene time is used as
            # is - adding the origin back would seek too far in by that amount.
            a = max(0.0, float(s))
            dur = max(0.0, float(e) - float(s))
            cmd += ["-ss", f"{a:.6f}", "-t", f"{dur:.6f}", "-i", source_path]

    # Build, per kept track, a concatenation of its scenes.
    parts = []
    out_labels = []
    for t in build_tracks:
        seg_labels = []
        for si, (s, e) in enumerate(scene_times):
            lbl = f"a{t}_{si}"
            if is_sbr(t):
                # Seeked input already bounds the scene; just zero its PTS.
                parts.append(f"[{seek_base + si}:a:{t}]asetpts=PTS-STARTPTS[{lbl}]")
            else:
                # atrim works on the same zero-based timeline ffmpeg rebases
                # the input to, so the container-relative scene times are the
                # correct selectors; no origin is added back.
                a = max(0.0, float(s))
                b = max(0.0, float(e))
                parts.append(
                    f"[1:a:{t}]atrim={a:.6f}:{b:.6f},asetpts=PTS-STARTPTS[{lbl}]"
                )
            seg_labels.append(f"[{lbl}]")
        parts.append(
            "".join(seg_labels)
            + f"concat=n={n_scenes}:v=0:a=1[aout{t}]"
        )
        out_labels.append(f"[aout{t}]")

    filt = ";".join(parts)

    # Tell the UI we've moved on to the audio rebuild straight away, so the
    # phase label changes the moment the (slow) decode begins.
    if progress_cb is not None:
        progress_cb({
            "percent": 1,
            "phase": "rebuild_audio",
            "scene": 1,
            "total_scenes": 1,
        })

    cmd += ["-filter_complex", filt, "-map", "0:v"]
    for t in build_tracks:
        cmd += ["-map", f"[aout{t}]"]
    cmd += ["-c:v", "copy", "-c:a", "aac"]
    for out_idx, t in enumerate(build_tracks):
        # ~64 kbps per channel (so stereo ~128k, 5.1 ~384k), floored at 128k.
        cmd += [f"-b:a:{out_idx}", f"{max(128, chans(t) * 64)}k"]
    # Without this the MPEG-TS muxer's interleave buffer silently corrupts the
    # second AAC track (it ends up reporting 0 channels); -max_interleave_delta 0
    # keeps every track intact when there is more than one.
    cmd += ["-max_interleave_delta", "0"]
    cmd += ["-progress", "pipe:1", tmp]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for line in proc.stdout:
                if cancel_cb is not None and cancel_cb():
                    proc.kill()
                    proc.wait()
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                    raise _Cancelled()
                line = line.strip()
                if (
                    line.startswith("out_time=")
                    and total_seconds > 0
                    and progress_cb is not None
                ):
                    stamp = line.split("=", 1)[1].strip()
                    try:
                        h, m, s = stamp.split(":")
                        done = int(h) * 3600 + int(m) * 60 + float(s)
                    except ValueError:
                        continue
                    pct = max(1, min(99, int(done / total_seconds * 100)))
                    progress_cb({
                        "percent": pct,
                        "phase": "rebuild_audio",
                        "scene": 1,
                        "total_scenes": 1,
                    })
            err = proc.stderr.read()
        finally:
            proc.wait()

        if proc.returncode != 0:
            raise ExportError((err or "").strip() or "ffmpeg failed")

        os.replace(tmp, video_path)
        return True
    except _Cancelled:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    except Exception:
        logger.exception("Audio rebuild failed")
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


def _reencode_cut_audio_to_aac(cut_path, bitrate, cancel_cb=None):
    """Re-encode the cut's audio to AAC in place, video and subtitles copied.

    Used when a profile asks for re-encoded audio rather than the lossless
    broadcast copy.  ``bitrate`` of 0 means automatic (the encoder's own
    default).  The result is plain ADTS AAC, which mkvmerge stores as native
    A_AAC and which every player reads - the universally-compatible option for
    streams (like Channel 4 HD) whose LATM copy some hardware players reject.

    Returns True on success; on failure the original cut is left untouched so
    the caller can fall back to rebuilding the audio from source.
    """
    tmp = cut_path + ".aac.ts"
    cmd = [
        "ffmpeg", "-y", "-i", cut_path,
        "-map", "0:v?", "-map", "0:a", "-c:v", "copy", "-c:a", "aac",
    ]
    if bitrate:
        cmd += ["-b:a", "%dk" % bitrate]
    cmd += [tmp]
    try:
        result = _run_cancellable(cmd, cancel_cb=cancel_cb)
    except _Cancelled:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    if result.returncode == 0 and os.path.exists(tmp) and _has_audio(tmp):
        os.replace(tmp, cut_path)
        return True
    if os.path.exists(tmp):
        os.remove(tmp)
    return False


def _reinterleave_ts(path, progress_cb=None):
    """Rewrite a finished .ts in place with proper audio/video interleaving.

    smartcut muxes each cut segment generator-by-generator (all the video
    packets, then the audio), and at seams this can leave a long run of
    video-only packets with the matching audio arriving in a lump many seconds
    later.  Structurally the file is perfect - timestamps, PCR and continuity
    counters are all clean - but players with small demux buffers (Kodi, VLC)
    wait for the late audio, give up, and play silent video until the skew
    shrinks back under their buffer; mpv-based players buffer deeply enough to
    ride it out.  A straight stream-copy remux re-orders the packets by
    timestamp, which fixes the playback everywhere at the cost of one fast
    disk copy.  -max_interleave_delta raises the muxer's reordering window to
    comfortably cover the worst bunching seen in the wild (~23s) while still
    capping memory if a stream has a genuine long gap.
    """
    tmp = path + ".ileave.ts"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", path,
        "-map", "0", "-map", "-0:d", "-ignore_unknown",
        "-c", "copy",
        "-max_interleave_delta", "60000000",   # 60s window (microseconds)
        "-muxpreload", "0", "-muxdelay", "0",
        tmp,
    ]
    if progress_cb is not None:
        progress_cb({"percent": -1, "phase": "interleave"})
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(tmp) or \
            os.path.getsize(tmp) == 0:
        tail = (res.stderr or "").strip().splitlines()
        logger.warning(
            "Interleave pass failed (rc=%s); keeping the un-reordered file. "
            "ffmpeg: %s", res.returncode, tail[-1] if tail else "(no output)",
        )
        _safe_remove(tmp)
        return False
    os.replace(tmp, path)
    logger.info("Re-interleaved the finished .ts (audio/video packet order).")
    return True


def export_ranges(
        source_path,
        out_path,
        keep_ranges,
        frame_index,
        out_format="match",
        progress_cb=None,
        cancel_cb=None,
        rebuild_audio=True,
        audio_mode="copy",
        audio_bitrate=0,
        aspect="source",
        crop_mode="none",
        crop=(0, 0, 0, 0),
        video_mode="copy",
        encoder_preset="faster",
        encoder_crf=None,
):
    """Cut and export the kept ranges.

    rebuild_audio: when the source audio can't be stream-copied from a
    mid-stream cut (notably aac_latm), the interactive path rebuilds it by
    re-encoding from source.  The batch passes rebuild_audio=False instead, so
    such a file is reported back (audio_needs_repair) and held for a Quick
    Stream Fix rather than cut unattended with re-encoded (and possibly
    out-of-sync) audio.

    audio_mode/audio_bitrate: a profile may force the audio to be re-encoded to
    AAC ("aac", with audio_bitrate kbps or 0 for automatic) instead of the
    default lossless broadcast copy ("copy").  Re-encoded AAC is the
    universally-compatible option for streams whose LATM copy some players
    reject (e.g. Channel 4 HD in .mkv).

    aspect: a profile may override the display aspect ratio ("4:3" / "16:9";
    "source" leaves it untouched).  Applied losslessly to the cut's video
    bitstream before any mux - the pixels are never re-encoded, only the aspect
    signalling changes - so a 16:9-flagged 4:3 recording can be set right.
    """
    if not keep_ranges:
        raise ExportError("No segments to export.")

    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise ExportError(
            "%s could not be found. Install ffmpeg, or set the path in "
            "Settings > External tools." % " and ".join(missing)
        )

    source = MediaContainer(source_path)
    n_audio = len(source.audio_tracks)
    segments = ranges_to_segments(source, keep_ranges, frame_index)

    fps = frame_index.fps or 25.0

    logger.info(
        "Export start: %s -> %s | %d scene(s), format=%s, fps=%.3f, "
        "audio tracks=%d",
        source_path, out_path, len(keep_ranges), out_format, fps, n_audio,
    )
    logger.debug("Keep ranges (frames): %s", keep_ranges)

    _log_source_details(source_path)

    # Per-segment boundaries with timecodes, so the log shows exactly where
    # each kept scene begins and ends (the times handed to smartcut).
    for i, ((a, b), (t0, t1)) in enumerate(
            zip(keep_ranges, segments), start=1
    ):
        logger.info(
            "  scene %d: %s -> %s  (frames %d-%d, %d frames)",
            i, _fmt_tc(float(t0), fps), _fmt_tc(float(t1), fps),
            a, b, b - a + 1,
        )

    # Chapter marks must land where the cuts ACTUALLY land.  smartcut cuts on
    # the stream's real timestamps, so the chapter positions have to be built
    # from those same timestamps - not from frame counts.  On field-coded HD
    # the per-frame spacing isn't a uniform 1/fps, so (b-a+1)/fps drifts from
    # real time and the marks creep later with every scene (and jump across an
    # ad-break PTS discontinuity).  Using the index's real seconds keeps every
    # mark on its join.  For progressive sources the two are identical.
    def _segment_duration(a, b):
        try:
            # Real span of frames a..b inclusive: from a's timestamp to the
            # timestamp of the frame after b (its true end).
            if b + 1 < frame_index.frame_count:
                return frame_index.seconds_of(b + 1) - frame_index.seconds_of(a)
            return (
                frame_index.seconds_of(b)
                - frame_index.seconds_of(a)
                + (1.0 / fps)
            )
        except Exception:
            return (b - a + 1) / fps

    segment_durations = [_segment_duration(a, b) for a, b in keep_ranges]

    if progress_cb is not None:
        progress_cb({
            "percent": 0,
            "phase": "copy",
            "scene": 1,
            "total_scenes": len(keep_ranges),
        })

    want_mkv = out_format == "mkv"
    want_mp4 = out_format == "mp4"
    needs_temp_ts = want_mkv or want_mp4
    cut_target = out_path + ".tmp.ts" if needs_temp_ts else out_path

    start_time = time.perf_counter()

    success = False
    aspect_source = None        # temp aspect-corrected source for .ts/.mp4
    try:
        # A 4:3/16:9 override can't be stamped onto the finished cut for .ts/.mp4
        # - smartcut's output has an SPS at every seam and the bitstream filter
        # truncates it at the first.  So stamp the aspect onto the (single-SPS)
        # source up front and cut that; the cut then inherits it on both copied
        # and re-encoded frames.  .mkv sets the aspect on the container at mux
        # time, so it's left untouched here.  The frame index still matches (the
        # rewrite preserves timing), and a rewrite that drops frames is rejected
        # inside the helper, so this can never shorten the export.
        if not want_mkv and aspect not in (None, "", "source"):
            aspect_source = _aspect_correct_source(
                source_path, aspect, cancel_cb=cancel_cb
            )
            if aspect_source:
                source_path = aspect_source

        audio_written = _run_smartcut(
            source_path, cut_target, segments, n_audio, keep_ranges, fps,
            progress_cb=progress_cb, cancel_cb=cancel_cb,
        )

        #
        # Verify the cut actually produced video.  smartcut can occasionally
        # write a structurally-broken file while reporting success; never let
        # that masquerade as a good export.
        #

        if progress_cb is not None:
            progress_cb({
                "percent": 99,
                "phase": "verify",
                "scene": len(keep_ranges),
                "total_scenes": len(keep_ranges),
            })

        produced = _count_output_frames(cut_target)
        expected = sum(b - a + 1 for a, b in keep_ranges)

        if produced == 0:
            raise ExportError(
                "Export produced no readable video. The source stream may "
                "need repairing first - try enabling 'Quick Stream Fix on "
                "open' in Settings, then re-open and export."
            )

        # Decide whether the cut audio has to be rebuilt from source.
        #
        # aac_latm (UK DVB H.264 broadcasts) carries its decoder config inline,
        # which used to mean a mid-stream copy was treated as unusable and the
        # whole track was re-encoded on every save.  In practice the config
        # recurs often enough that smartcut's copied audio decodes cleanly, and
        # it can then be kept untouched in every target we support - matching
        # VideoReDo:
        #   * .ts and .mkv copy the audio straight through (lossless);
        #   * .mp4 re-encodes it in _transcode_to_mp4 anyway (the only way in).
        # So the broadcast audio is kept whenever it decodes, and the
        # decode/re-encode-from-source rebuild is now only a fallback for the
        # rare cut whose audio really is undecodable.
        audio_needs_repair = False
        if audio_mode == "aac" and n_audio > 0 and not want_mp4:
            # The profile asks for re-encoded AAC rather than the lossless
            # broadcast copy - the universally-compatible escape hatch (e.g. a
            # hardware player that won't take the Channel 4 LATM-in-MKV
            # wrapper).  Re-encode the cut's audio to plain AAC in place;
            # mkvmerge then stores native A_AAC and the .ts holds standard AAC.
            # (.mp4 already re-encodes audio to AAC in _transcode_to_mp4, so it
            # is left to that path.)  bitrate 0 == automatic.
            rate = audio_bitrate or 0
            logger.info(
                "Profile audio: re-encoding to AAC (%s).",
                "automatic" if not rate else "%d kbps" % rate,
            )
            if not _reencode_cut_audio_to_aac(
                    cut_target, rate, cancel_cb=cancel_cb):
                logger.warning(
                    "In-place AAC re-encode failed; rebuilding audio from "
                    "source instead."
                )
                if rebuild_audio:
                    _rebuild_audio_from_source(
                        cut_target, source_path, segments, n_audio,
                        progress_cb=progress_cb, cancel_cb=cancel_cb,
                    )
        elif n_audio > 0 and _audio_needs_rebuild(source_path):
            # smartcut's straight copy of some broadcast LATM comes through
            # mistyped and broken: ffprobe reports it as mp3, and neither
            # mkvmerge nor players can read it.  So the copied audio is trusted
            # only when it's still structurally sound AAC (_cut_audio_intact);
            # otherwise it's rebuilt from source - for EVERY container, .mkv
            # included, since mkvmerge can't rescue a copy it can't read either.
            #
            # When the copy IS intact:
            #   * .mkv keeps it untouched - mkvmerge repackages the LATM to
            #     native AAC losslessly WITHOUT decoding, so even a 6-channel
            #     LATM track (which ffmpeg can't stand up for a decode check)
            #     comes through perfectly;
            #   * .ts must hold audio players can actually read, so it keeps the
            #     copy when it decodes, else has mkvmerge relax it to native AAC.
            cut_intact = _cut_audio_intact(cut_target)
            if not cut_intact:
                logger.warning(
                    "smartcut mis-muxed the broadcast audio (it comes through "
                    "mistyped and unreadable); rebuilding it from source."
                )

            if cut_intact and want_mkv:
                logger.info(
                    "Keeping the broadcast audio for the .mkv (mkvmerge "
                    "repackage, verified below)."
                )
            elif cut_intact and _audio_decodes(cut_target):
                # .ts keeps the lossless copy; .mp4 re-encodes it in
                # _transcode_to_mp4.
                logger.info(
                    "Cut audio decodes cleanly; keeping the broadcast audio "
                    "(copied through for .ts, re-encoded for .mp4) - no rebuild "
                    "needed."
                )
            elif cut_intact and (not want_mp4) and _have_mkvmerge() and \
                    _relax_ts_audio_via_mkvmerge(
                        cut_target, _mkvmerge_exe(), cancel_cb=cancel_cb):
                # .ts whose copied audio players can't read (e.g. 6-channel
                # LATM): repackage it to readable native AAC losslessly via
                # mkvmerge - no re-encode, samples stay bit-identical.
                logger.info(
                    "Repackaged the .ts audio to readable native AAC via "
                    "mkvmerge (lossless - no re-encode)."
                )
            elif rebuild_audio:
                # The copied audio can't be trusted (smartcut mis-muxed the
                # broadcast LATM, or it won't decode and mkvmerge can't relax
                # it).  First try to keep it LOSSLESS by grafting the source's
                # own audio packets for the kept ranges onto the video - copied,
                # never re-encoded, exactly as VideoReDo cuts audio.  Only if
                # that can't produce usable audio do we fall back to the lossy
                # decode/re-encode rebuild.
                audio_needs_repair = True
                grafted = _graft_source_audio(
                    cut_target, source_path, segments, n_audio,
                    progress_cb=progress_cb, cancel_cb=cancel_cb,
                )
                # _graft_source_audio returns the number of tracks it grafted
                # (0 on failure).  Acceptance:
                #   * a single track keeps the established behaviour - .mkv on
                #     faith (mkvmerge repackages even a 6-channel LATM track
                #     ffmpeg can't stand up for a decode check), .ts only when
                #     it decodes;
                #   * more than one track must have EVERY track decode, for both
                #     containers, so a mis-synced or mangled secondary track
                #     (e.g. audio description) can never ship - it falls back to
                #     the re-encode rebuild instead, exactly as multi-track did
                #     before the graft learned to handle it.
                if grafted > 1:
                    graft_ok = _has_audio(cut_target) and \
                        _all_audio_tracks_decode(cut_target)
                else:
                    graft_ok = grafted and _has_audio(cut_target) and \
                        (want_mkv or _audio_decodes(cut_target))
                if graft_ok:
                    logger.info(
                        "Kept the broadcast audio losslessly via the source "
                        "graft (copied, no re-encode)."
                    )
                    audio_needs_repair = False
                    ok = False  # skip the re-encode rebuild below
                else:
                    if grafted:
                        logger.warning(
                            "Grafted audio did not come through cleanly; "
                            "falling back to the re-encode rebuild."
                        )
                    # Fallback: rebuild by re-encoding from source, as VideoReDo
                    # does.  smartcut's segment times are on the same zero-based
                    # timeline ffmpeg's atrim/-ss select on, so they pass through
                    # unchanged; the container origin is logged as a diagnostic.
                    audio_offset = _source_start_seconds(source_path)
                    logger.warning(
                        "Rebuilding all %d audio track(s) from source "
                        "(container origin %.3fs)."
                        % (n_audio, audio_offset)
                    )
                    ok = _rebuild_audio_from_source(
                        cut_target, source_path, segments,
                        n_audio,
                        progress_cb=progress_cb,
                        cancel_cb=cancel_cb,
                    )
                if ok and _has_audio(cut_target):
                    logger.info("Audio rebuild succeeded.")
                    # Optional, off by default: confirm the rebuilt audio still
                    # lines up with the source at the first cut and log the
                    # measured offset.  Adds a couple of seconds, so it's gated
                    # on the 'verify_audio_sync' setting.
                    try:
                        from config.loader import ensure_config
                        _verify = bool(
                            ensure_config()
                            .get("settings", {})
                            .get("verify_audio_sync", False)
                        )
                    except Exception:
                        _verify = False
                    if _verify and segments:
                        _verify_audio_sync(
                            cut_target, source_path, float(segments[0][0]),
                        )
                    # Repaired in place - no Quick Stream Fix needed.
                    audio_needs_repair = False
                elif audio_needs_repair:
                    logger.warning(
                        "Audio rebuild did not yield readable audio; "
                        "leaving the job to be held for a Quick Stream Fix."
                    )
            else:
                # Undecodable, and the caller asked us not to rebuild: leave it
                # flagged so the batch holds the job for a Quick Stream Fix.
                audio_needs_repair = True
                logger.info(
                    "Cut audio is undecodable; skipping rebuild on request."
                )

        # The display aspect, if any, was stamped onto the source before the cut
        # for .ts/.mp4; .mkv sets it on the container in the mux below.
        audio_repackaged = False
        if want_mkv:
            audio_repackaged = _write_mkv_chapters(
                cut_target, out_path, segment_durations,
                aspect=aspect, cancel_cb=cancel_cb, progress_cb=progress_cb,
            )
        elif want_mp4:
            #
            # MP4 can't hold broadcast audio (LATM AAC / MP2) or SD MPEG-2
            # video, so convert the cut .ts into MP4 (audio -> AAC always;
            # video copied when it's H.264/HEVC, else re-encoded to H.264).
            #
            video_codec = getattr(frame_index, "codec", None)
            interlaced = getattr(frame_index, "interlaced", False)

            # H.264/HEVC video is copied (only the audio is recoded) - a quick
            # job; anything else needs the video re-encoding too - the slow one.
            audio_only = (video_codec or "").lower() in ("h264", "hevc")
            phase = "recode_audio" if audio_only else "recode_full"

            if progress_cb is not None:
                progress_cb({
                    "percent": 1,
                    "phase": phase,
                    "scene": len(keep_ranges),
                    "total_scenes": len(keep_ranges),
                })

            _transcode_to_mp4(
                cut_target,
                out_path,
                video_codec,
                interlaced,
                total_seconds=sum(segment_durations),
                phase=phase,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
            )
        else:
            # Plain .ts delivery (cut_target IS out_path).  Fix the packet
            # interleaving smartcut leaves at seams; without this, players
            # with small demux buffers (Kodi, VLC) can lose audio for tens of
            # seconds after a cut.  MKV goes through mkvmerge and MP4 through
            # the transcode above, both of which re-interleave inherently.
            if cancel_cb is None or not cancel_cb():
                _reinterleave_ts(out_path, progress_cb=progress_cb)

        if progress_cb is not None:
            progress_cb({
                "percent": 100,
                "phase": "done",
                "scene": len(keep_ranges),
                "total_scenes": len(keep_ranges),
            })

        success = True

    finally:
        if needs_temp_ts and os.path.exists(cut_target):
            os.remove(cut_target)
        _safe_remove(aspect_source)
        if not success:
            # Aborted or failed part-way: don't leave a partial final file (or
            # an mp4 ".part") sitting on disk.
            for p in (out_path, out_path + ".part", out_path + ".tmp.ts"):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass

    # Pixel crop - the one non-lossless step.  It runs last, on the finished
    # file, so it's independent of the container/audio/aspect handling above: the
    # black bars are simply re-encoded away.  The same pass also re-encodes the
    # video to HEVC when a profile asks for a smaller file (video_mode="hevc") -
    # crop and HEVC share one encode, so a cropped HEVC output is a single pass.
    # Only when the cut itself succeeded; any failure keeps the previous file.
    reencode_hevc = video_mode == "hevc"
    if success and (crop_mode in ("auto", "fixed") or reencode_hevc):
        try:
            from export import crop as _crop
            cw, ch = _crop.source_dimensions(out_path)
            field_order = _crop.source_field_order(out_path)
            interlaced = field_order in ("tt", "bb")
            rect = None
            if crop_mode in ("auto", "fixed"):
                if crop_mode == "auto":
                    edges = _crop.detect_crop(out_path, cw, ch)
                else:
                    edges = (tuple(crop) + (0, 0, 0, 0))[:4]
                if cw and ch and any(edges):
                    candidate = _crop.even_rect(
                        cw, ch, *edges, interlaced=interlaced
                    )
                    if (candidate[0], candidate[1]) != (cw, ch):
                        rect = candidate
            # Re-encode when we have a crop to apply, or HEVC was requested.
            if rect is not None or reencode_hevc:
                codec = "libx265" if reencode_hevc else "libx264"
                phase = "encode" if reencode_hevc else "crop"
                n_scenes = len(keep_ranges)

                def _reenc_progress(pct):
                    if progress_cb is not None:
                        progress_cb({
                            "percent": pct, "phase": phase,
                            "scene": n_scenes, "total_scenes": n_scenes,
                        })

                _reenc_progress(0)
                ext = os.path.splitext(out_path)[1]
                tmp_re = out_path + ".reenc.tmp" + ext
                ok = _crop.crop_reencode(
                    out_path, tmp_re, rect,
                    codec=codec,
                    preset=encoder_preset,
                    crf=encoder_crf,
                    cap_kbps=_crop.target_cap_kbps(out_path),
                    fps=_crop.source_fps(out_path),
                    field_order=field_order,
                    total_seconds=_crop.source_duration(out_path),
                    progress_cb=_reenc_progress,
                    cancel_cb=cancel_cb,
                )
                if (ok and os.path.exists(tmp_re)
                        and os.path.getsize(tmp_re) > 0):
                    os.replace(tmp_re, out_path)
                    if reencode_hevc and rect is not None:
                        logger.info(
                            "Cropped to %dx%d and re-encoded to HEVC.",
                            rect[0], rect[1],
                        )
                    elif reencode_hevc:
                        logger.info("Re-encoded video to HEVC.")
                    else:
                        logger.info(
                            "Cropped to %dx%d (re-encoded).", rect[0], rect[1]
                        )
                else:
                    _safe_remove(tmp_re)
                    logger.warning(
                        "Finishing re-encode failed; kept the previous output."
                    )
        except Exception as exc:
            logger.warning(
                "Finishing re-encode error (%s); kept the previous output.", exc
            )

    # Stop the clock here, after the crop re-encode, so the reported time and
    # frames/sec include it rather than just the fast cut that preceded it.
    elapsed = time.perf_counter() - start_time

    # The rebuild can legitimately drop a track (e.g. an HE-AAC audio-
    # description stream that can't be re-encoded in sync from a mid-stream
    # cut), so the count smartcut first reported can overstate what the
    # finished file actually holds.  Re-read the output so the figures and the
    # "dropped" count are honest about what was written.
    try:
        actual_written = len(_audio_track_info(out_path))
    except Exception:
        actual_written = audio_written
    if 0 <= actual_written != audio_written:
        logger.info(
            "Audio tracks in finished file: %d (smartcut wrote %d; %d "
            "track(s) dropped during rebuild).",
            actual_written, audio_written, audio_written - actual_written,
        )
        audio_written = actual_written

    dropped = n_audio - audio_written

    # For field-coded (collapsed) sources, `produced` counts coded pictures
    # (roughly twice the displayable frames), while `expected` counts the
    # displayable frames the edit actually keeps.  The difference between them
    # is therefore a counting artifact, not a real change in length, so we
    # report the displayable count and skip the misleading "longer than the
    # edit" note that would otherwise alarm the user.
    collapsed = len(source.video_frame_times) != frame_index.frame_count
    reported_frames = expected if collapsed else produced
    diff = 0 if collapsed else (produced - expected)

    # Probe the finished output up front: its size and audio-packet count feed
    # both the stats table and the no-audio check just below.
    try:
        out_size = os.path.getsize(out_path)
    except OSError:
        out_size = 0
    audio_frames, video_bitrate = _probe_output_extra(out_path)

    #
    # Keep the success message short.  The exact figures (size, duration,
    # frame count) are already in the stats table, so only flag anything worth
    # a glance as a brief asterisked note, VRD-style, rather than a paragraph.
    # (When the log file lands, the full detail can go there instead.)
    #
    tolerance = 2 * max(1, len(keep_ranges))

    # Two buckets for the completion dialog (VRD-style): genuine problems shown
    # plainly at the top, and brief "* ..." footnotes below the figures.  Each
    # is a (short label, full explanation) pair: the dialog shows
    # "<label> - see logs", while the log's completion summary spells the full
    # explanation out inline, right where you'd look for it.
    errors = []      # (label, full) - serious; shown at the top of the dialog
    notes = []       # (label, full) - informational; shown as "* ..." footnotes

    if dropped > 0:
        # Tell a genuinely-dropped HE-AAC (SBR) description track - which can't
        # be cut in sync and is dropped on purpose - from the main audio going
        # missing, which is a real problem.
        try:
            sbr = sum(
                1 for _ch, prof in _audio_track_info(source_path)
                if "he-aac" in (prof or "").lower()
            )
        except Exception:
            sbr = 0
        plural = "s" if dropped != 1 else ""
        if dropped <= sbr:
            notes.append((
                "%d audio-description track%s not carried over" % (dropped, plural),
                "These carry SBR/PS extensions that can't be re-encoded in sync "
                "from a mid-stream cut, so they are dropped on purpose "
                "(VideoReDo drops them too).",
            ))
        else:
            full = ("%d audio track%s could not be carried over - the output "
                    "may be missing its main audio." % (dropped, plural))
            errors.append(("%d audio track%s missing" % (dropped, plural), full))
            logger.warning(full)

    if abs(diff) > tolerance:
        secs = abs(diff) / fps
        direction = "shorter" if diff < 0 else "longer"
        notes.append((
            "output ~%.1fs %s than the edit" % (secs, direction),
            "Output is ~%.1fs %s than the edit (%d frames) - normal for smart "
            "cutting, where the partial GOPs at the cut points are re-encoded."
            % (secs, direction, abs(diff)),
        ))

    if audio_written > 0 and audio_frames == 0:
        full = ("Output has NO audio despite passing through %d track(s); the "
                "source audio codec likely cannot be stream-copied."
                % audio_written)
        errors.append(("output has no audio", full))
        logger.warning(full)

    if audio_repackaged:
        notes.append((
            "repackaged audio",
            "Audio repackaged losslessly for the MKV: some broadcasts carry the "
            "AAC channel configuration in-band (LATM) and it isn't constant, "
            "which a single fixed Matroska header can't represent.  The frames "
            "were kept untouched (no re-encode) so the audio stays playable in "
            "Plex, Jellyfin and other ffmpeg-based players.  For maximum "
            "portability (e.g. a hardware player) use a profile with Audio set "
            "to Re-encode AAC.",
        ))

    if collapsed:
        logger.info(
            "Export done: %d displayable frames (%d coded pictures, "
            "field-coded source), audio dropped=%d/%d, elapsed=%.1fs",
            expected, produced, dropped, n_audio, elapsed,
        )
    else:
        logger.info(
            "Export done: produced=%d expected=%d diff=%+d frames "
            "(~%.2fs %s), audio dropped=%d/%d, elapsed=%.1fs",
            produced, expected, diff,
            abs(diff) / fps, ("shorter" if diff < 0 else "longer"),
            dropped, n_audio, elapsed,
        )

    #
    # Stats for the completion dialog.
    #

    total_frames = sum(b - a + 1 for a, b in keep_ranges)

    # Fall back to size/duration if the container didn't report a bitrate.
    duration_secs = total_frames / fps
    if video_bitrate == 0 and duration_secs > 0 and out_size > 0:
        video_bitrate = int(out_size * 8 / duration_secs)

    stats = {
        "out_path": out_path,
        "errors": ["%s - see logs" % label for label, _full in errors],
        "notes": ["%s - see logs" % label for label, _full in notes],
        "scenes": len(keep_ranges),
        "video_frames": reported_frames,
        "audio_frames": audio_frames,
        "duration_secs": duration_secs,
        "out_size": out_size,
        "processing_secs": elapsed,
        "fps": (reported_frames / elapsed) if elapsed > 0 else 0.0,
        "audio_tracks": audio_written,
        "dropped_audio": dropped,
        "video_bitrate": video_bitrate,
        "audio_needs_repair": audio_needs_repair,
    }

    # The completion figures, one per line (like the dialog) so the summary is
    # easy to find in the log later, with any dialog notes/errors echoed below.
    _dh = int(duration_secs) // 3600
    _dm = (int(duration_secs) % 3600) // 60
    _ds = int(duration_secs) % 60
    summary = [
        "Export complete:",
        "    Output file     : %s" % out_path,
        "    Video length    : %02d:%02d:%02d" % (_dh, _dm, _ds),
        "    Video size      : %.0f MB" % (out_size / (1024 * 1024)),
        "    Output scenes   : %d" % len(keep_ranges),
        "    Video frames    : %d" % reported_frames,
        "    Audio frames    : %d" % audio_frames,
        "    Audio tracks    : %d" % audio_written,
        "    Processing time : %.1fs" % elapsed,
        "    Frames/sec      : %.0f" % stats["fps"],
        "    Video bitrate   : %.2f Mbps" % (video_bitrate / 1_000_000),
    ]
    for label, full in notes:
        summary.append("    * %s - %s" % (label, full))
    for label, full in errors:
        summary.append("    ! %s - %s" % (label, full))
    logger.info("\n".join(summary))

    return stats


class ExportWorker(QThread):

    # progress emits a dict: percent, phase ('copy'/'encode'/'verify'/'done'),
    # scene, total_scenes
    progress = Signal(dict)
    # finished_ok emits the stats dict from export_ranges
    finished_ok = Signal(dict)
    failed = Signal(str)
    # cancelled emits when the user aborted (so the dialog can close cleanly)
    cancelled = Signal()

    def __init__(self, source_path, out_path, keep_ranges,
                 frame_index, out_format, parent=None,
                 audio_mode="copy", audio_bitrate=0, aspect="source",
                 crop_mode="none", crop=(0, 0, 0, 0), video_mode="copy",
                 encoder_preset="faster", encoder_crf=None):
        super().__init__(parent)
        self.source_path = source_path
        self.out_path = out_path
        self.keep_ranges = keep_ranges
        self.frame_index = frame_index
        self.out_format = out_format
        self.audio_mode = audio_mode
        self.audio_bitrate = audio_bitrate
        self.aspect = aspect
        self.crop_mode = crop_mode
        self.crop = crop
        self.video_mode = video_mode
        self.encoder_preset = encoder_preset
        self.encoder_crf = encoder_crf
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            stats = export_ranges(
                self.source_path,
                self.out_path,
                self.keep_ranges,
                self.frame_index,
                out_format=self.out_format,
                audio_mode=self.audio_mode,
                audio_bitrate=self.audio_bitrate,
                aspect=self.aspect,
                crop_mode=self.crop_mode,
                crop=self.crop,
                video_mode=self.video_mode,
                encoder_preset=self.encoder_preset,
                encoder_crf=self.encoder_crf,
                progress_cb=self.progress.emit,
                cancel_cb=lambda: self._cancel,
            )
        except _Cancelled:
            self.cancelled.emit()
            return
        except Exception as exc:
            # A cancel can surface as a generic error from a killed subprocess;
            # treat anything that arrives after an abort as a cancellation.
            if self._cancel:
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))
            return
        if self._cancel:
            self.cancelled.emit()
        else:
            self.finished_ok.emit(stats)
