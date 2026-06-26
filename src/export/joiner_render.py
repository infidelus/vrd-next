"""Joiner render - turn a joiner list into one video.

Each scene entry is rendered with the exporter's smartcut engine to its own
temporary .ts, the pieces are joined with ffmpeg's concat demuxer, and the
joined .ts is finally written out in the chosen format (Match source / MKV /
MP4) using the same conversion the normal exporter uses.

The join itself is a plain stream copy, so it works when every entry shares the
same codec, resolution and frame rate - several recordings off the same
channel, the normal case.  Mixed formats need re-encoding to a common format,
which is a later phase; if the join step fails for that reason the error says
so.  (The final MKV/MP4 step is independent of that - it converts the single
joined file.)
"""

import os
import json
import shutil
import subprocess
import tempfile

from PySide6.QtCore import QThread, Signal

from media.frame_index import build_index_sync
from export.exporter import (
    export_ranges,
    _write_mkv_chapters,
    _transcode_to_mp4,
)


def _find_font(bold=False):
    """Return a usable .ttf path, preferring DejaVu Sans (present on Mint)."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


def _ffmpeg_colour(hex_colour, default="0x000000"):
    """Convert a '#RRGGBB' colour to ffmpeg's '0xRRGGBB' form."""
    value = (hex_colour or "").strip()
    if value.startswith("#") and len(value) == 7:
        return "0x" + value[1:]
    return default


class _Cancelled(Exception):
    """Raised internally to unwind cleanly when the user cancels."""


# Friendly names for the codecs we see on UK broadcast streams.
_VCODEC_NAMES = {"h264": "H.264", "hevc": "HEVC", "mpeg2video": "MPEG-2"}
_ACODEC_NAMES = {
    "aac": "AAC", "aac_latm": "AAC", "mp2": "MP2", "mp3": "MP3",
    "ac3": "AC-3", "eac3": "E-AC-3",
}


def _source_signature(path):
    """(video_codec, width, height, audio_codec) for the first video/audio
    streams - the things that must match for a straight-copy join to work."""
    try:
        data = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height", "-of", "json", path],
            capture_output=True, text=True).stdout)
    except Exception:
        return (None, 0, 0, None)

    vcodec = acodec = None
    width = height = 0
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and vcodec is None:
            vcodec = stream.get("codec_name")
            width = stream.get("width") or 0
            height = stream.get("height") or 0
        elif stream.get("codec_type") == "audio" and acodec is None:
            acodec = stream.get("codec_name")
    return (vcodec, width, height, acodec)


def _describe_signature(sig):
    vcodec, width, height, acodec = sig
    video = _VCODEC_NAMES.get(vcodec, vcodec or "unknown video")
    audio = _ACODEC_NAMES.get(acodec, acodec or "no") + " audio"
    if width and height:
        return "%s %d×%d, %s" % (video, width, height, audio)
    return "%s, %s" % (video, audio)


def scan_join_compatibility(entries):
    """Quick header-only probe of each distinct source.  Returns
    (ok, formats_text): ok is False when the sources don't share one format (so
    a straight-copy join won't work), and formats_text is a bulleted list of
    the distinct formats found, for the caller to present."""
    distinct = {}
    for entry in entries:
        src = entry.source
        if not src or not os.path.exists(src):
            continue
        sig = _source_signature(src)
        distinct.setdefault(sig, os.path.basename(src))

    if len(distinct) <= 1:
        return True, ""

    lines = ["• %s — e.g. %s" % (_describe_signature(sig), name)
             for sig, name in distinct.items()]
    return False, "\n".join(lines)


def _probe_dims_fps(path):
    """(display_width, height, fps_rounded) for the first video stream.

    The width is the *display* width - the stored width adjusted by the sample
    aspect ratio - so anamorphic broadcast SD (e.g. 544x576 or 720x576 stored
    but 16:9 on screen) is handled by its true on-screen shape rather than its
    stored pixel grid.  Square-pixel sources are returned unchanged.
    """
    try:
        data = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=width,height,avg_frame_rate,r_frame_rate,"
             "sample_aspect_ratio",
             "-of", "json", path], capture_output=True, text=True).stdout)
        stream = data.get("streams", [{}])[0]
    except Exception:
        return (0, 0, 0)
    width = stream.get("width") or 0
    height = stream.get("height") or 0
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    try:
        num, den = rate.split("/")
        fps = round(float(num) / float(den)) if float(den) else 0
    except (ValueError, ZeroDivisionError):
        fps = 0

    # Fold in the sample aspect ratio so the width reflects the on-screen shape.
    # A non-square SAR (e.g. 32:17 on UK SD) means the picture is wider than its
    # stored width; using the stored width here is what made anamorphic joins
    # come out squashed and letterboxed.
    sar = stream.get("sample_aspect_ratio") or "1:1"
    try:
        sar_n, sar_d = (int(x) for x in sar.split(":"))
        if sar_n > 0 and sar_d > 0 and sar_n != sar_d:
            width = int(round(width * sar_n / sar_d))
    except (ValueError, ZeroDivisionError):
        pass
    if width % 2:                       # keep an even width for yuv420p
        width += 1
    return (width, height, fps)


def recommended_target(entries):
    """Choose a common (width, height, fps) for a re-encode join: the largest
    frame size present and the highest (rounded) frame rate, so higher-quality
    scenes are preserved and lower ones upscaled to match."""
    best_w = best_h = 0
    best_pixels = -1
    fps = 0
    seen = set()
    for entry in entries:
        src = entry.source
        if not src or src in seen or not os.path.exists(src):
            continue
        seen.add(src)
        width, height, rate = _probe_dims_fps(src)
        if width * height > best_pixels:
            best_pixels = width * height
            best_w, best_h = width, height
        fps = max(fps, rate)
    return (best_w or 1920, best_h or 1080, fps or 25)


def _probe_video(path):
    """Return (codec_name, interlaced) for the first video stream."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,field_order",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True).stdout.split()
    codec = out[0] if out else ""
    field_order = out[1] if len(out) > 1 else ""
    interlaced = field_order in ("tt", "bb", "tb", "bt")
    return codec, interlaced


class JoinerRenderWorker(QThread):
    """Renders every joiner entry and joins them into one output file."""

    progress = Signal(int, str)        # overall percent, status label
    finished_ok = Signal(str)          # output path
    failed = Signal(str)               # message ("Cancelled." if cancelled)

    def __init__(self, entries, out_path, out_format="match",
                 reencode_target=None, parent=None):
        super().__init__(parent)
        self._entries = list(entries)
        self._out = out_path
        self._out_format = out_format
        # None -> lossless stream-copy join (same-format scenes).
        # (width, height, fps) -> re-encode every scene to that and join.
        self._reencode_target = reencode_target
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _check_cancel(self):
        if self._cancel:
            raise _Cancelled()

    def run(self):
        tmpdir = tempfile.mkdtemp(prefix="vrd-next-joiner-")
        index_cache = {}
        segments = []
        durations = []
        try:
            n = len(self._entries)
            if n == 0:
                raise RuntimeError("The joiner list is empty.")

            # One progress "slot" per scene plus one for the join/convert step.
            slots = n + 1

            for i, entry in enumerate(self._entries):
                self._check_cancel()

                # A title card is generated, not cut from a source.  It needs
                # the re-encode join (it can't stream-copy alongside broadcast),
                # which the caller guarantees by setting a target when a title
                # is present.
                if entry.is_title:
                    self.progress.emit(
                        int(i * 100 / slots),
                        "Building title card %d of %d…" % (i + 1, n))
                    target = self._reencode_target or (1920, 1080, 25)
                    seg = self._make_title(entry, i, tmpdir, target)
                    segments.append(seg)
                    durations.append(entry.duration)
                    continue

                src = entry.source
                if not src or not os.path.exists(src):
                    raise RuntimeError("File not found:\n%s" % (src,))

                self.progress.emit(int(i * 100 / slots),
                                   "Rendering scene %d of %d…" % (i + 1, n))

                # Build (or reuse) the source's frame index, then map the
                # scene's seconds onto frame numbers for the exporter.
                if src not in index_cache:
                    index_cache[src] = build_index_sync(src)
                index = index_cache[src]

                start_f = index.index_of_seconds(entry.start)
                end_f = index.index_of_seconds(entry.end)
                if end_f < start_f:
                    start_f, end_f = end_f, start_f

                seg = os.path.join(tmpdir, "seg_%04d.ts" % i)

                def _cb(data, base=i):
                    pct = data.get("percent", 0) if isinstance(data, dict) else 0
                    self.progress.emit(
                        int((base + pct / 100.0) * 100 / slots),
                        "Rendering scene %d of %d…" % (base + 1, n))

                # Always render the intermediate pieces as .ts; the chosen
                # output format is applied once, to the joined file.
                export_ranges(
                    src, seg, [(start_f, end_f)], index,
                    out_format="match",
                    progress_cb=_cb,
                    cancel_cb=lambda: self._cancel,
                )
                self._check_cancel()
                segments.append(seg)
                durations.append(entry.duration)

            self.progress.emit(int(n * 100 / slots),
                               "Joining scenes…" if not self._reencode_target
                               else "Re-encoding and joining scenes…")
            if self._reencode_target:
                fades = [
                    (float(getattr(e, "fade_in", 0.0) or 0.0),
                     float(getattr(e, "fade_out", 0.0) or 0.0))
                    for e in self._entries
                ]
                joined_ts = self._join_reencode(
                    segments, tmpdir, self._reencode_target, durations, fades)
            else:
                joined_ts = self._join(segments, tmpdir)
            self._check_cancel()

            self._finalize(joined_ts, durations)
            self._check_cancel()

            self.progress.emit(100, "Done")
            self.finished_ok.emit(self._out)

        except _Cancelled:
            self._discard_output()
            self.failed.emit("Cancelled.")
        except Exception as exc:                    # noqa: BLE001 - reported
            self._discard_output()
            self.failed.emit(str(exc))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _discard_output(self):
        try:
            if os.path.exists(self._out):
                os.remove(self._out)
        except OSError:
            pass

    def _join(self, segments, tmpdir):
        """Concatenate the rendered .ts segments into one .ts; returns its
        path."""
        if not segments:
            raise RuntimeError("Nothing to join.")

        joined = os.path.join(tmpdir, "joined.ts")
        if len(segments) == 1:
            shutil.copyfile(segments[0], joined)
            return joined

        list_path = os.path.join(tmpdir, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as handle:
            for seg in segments:
                # concat-demuxer quoting: wrap in single quotes, escape any.
                handle.write("file '%s'\n" % seg.replace("'", "'\\''"))

        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c", "copy", joined,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(joined):
            tail = "\n".join((result.stderr or "").strip().splitlines()[-6:])
            raise RuntimeError(
                "Joining the scenes failed.  The clips may not share the same "
                "format (codec, resolution or frame rate); mixed formats will "
                "be supported in a later build.\n\n" + tail)
        return joined

    def _join_reencode(self, segments, tmpdir, target, durations, fades=None):
        """Join scenes that don't share a format by re-encoding them all to a
        common target (width, height, fps) in one ffmpeg concat-filter pass.

        Each scene is deinterlaced, scaled (preserving aspect, padded), set to
        the target frame rate and pixel format; audio is resampled to 48 kHz
        stereo AAC.  Lower-resolution scenes are upscaled to match the highest.

        fades, if given, is a per-segment list of (fade_in, fade_out) seconds -
        a fade to/from black applied to that clip's picture only (the audio is
        left at full level).
        """
        if not segments:
            raise RuntimeError("Nothing to join.")

        width, height, fps = target
        joined = os.path.join(tmpdir, "joined.ts")

        inputs = []
        filters = []
        labels = []
        for i, seg in enumerate(segments):
            inputs += ["-i", seg]

            # Optional fade to/from black for this clip's picture.  Clamp to the
            # clip length so a too-long fade can't run past the clip.
            fade = ""
            if fades and i < len(fades):
                dur = max(0.001, durations[i] if i < len(durations) else 0.0)
                fi = max(0.0, min(fades[i][0], dur))
                fo = max(0.0, min(fades[i][1], dur - fi))
                if fi > 0:
                    fade += ",fade=t=in:st=0:d=%.3f" % fi
                if fo > 0:
                    fade += ",fade=t=out:st=%.3f:d=%.3f" % (dur - fo, fo)

            filters.append(
                "[%d:v:0]yadif=deint=interlaced,"
                # Un-anamorphic first: resample by the sample aspect ratio to
                # square pixels at the true display width (UK SD is 544/720x576
                # stored but 16:9 on screen), so the fit below uses the real
                # picture shape instead of the stored pixel grid.
                "scale=trunc(iw*sar/2)*2:ih,setsar=1,"
                "scale=%d:%d:force_original_aspect_ratio=decrease,"
                "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%d,"
                "format=yuv420p%s[v%d]"
                % (i, width, height, width, height, fps, fade, i))
            filters.append(
                "[%d:a:0]aresample=48000,"
                "aformat=sample_fmts=fltp:channel_layouts=stereo[a%d]"
                % (i, i))
            labels.append("[v%d][a%d]" % (i, i))

        filters.append("%sconcat=n=%d:v=1:a=1[outv][outa]"
                       % ("".join(labels), len(segments)))

        total = max(0.001, sum(durations))
        cmd = [
            "ffmpeg", "-hide_banner", "-nostats", "-y",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-progress", "pipe:1", joined,
        ]
        self._run_with_progress(cmd, total, "Re-encoding and joining scenes…")
        if not os.path.exists(joined):
            raise RuntimeError("Re-encoding the joined video failed.")
        return joined

    def _run_with_progress(self, cmd, total_seconds, label):
        """Run an ffmpeg command that emits -progress, moving the bar via the
        out_time it reports.  The final slot of the overall bar (90-99%) tracks
        the encode."""
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for line in proc.stdout:
                if self._cancel:
                    proc.terminate()
                    break
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        secs = int(line.split("=", 1)[1]) / 1_000_000.0
                        pct = min(99, 90 + int(9 * secs / total_seconds))
                        self.progress.emit(pct, label)
                    except (ValueError, ZeroDivisionError):
                        pass
        finally:
            proc.wait()
        if self._cancel:
            raise _Cancelled()
        if proc.returncode != 0:
            err = (proc.stderr.read() or "").strip().splitlines()[-6:]
            raise RuntimeError(
                "Re-encoding the joined video failed.\n\n" + "\n".join(err))

    def _make_title(self, entry, idx, tmpdir, target):
        """Generate a title-card clip (text on a coloured background, silent
        audio) at the target format, returned as a .ts segment.

        Text is passed via textfile= so any characters (colons, quotes,
        percent signs) are safe without escaping.
        """
        width, height, fps = target
        duration = max(0.5, entry.duration)
        out = os.path.join(tmpdir, "title_%04d.ts" % idx)

        bg = _ffmpeg_colour(entry.bg_color, "0x000000")
        fg = _ffmpeg_colour(entry.text_color, "0xFFFFFF")
        font_bold = _find_font(bold=True)
        font = _find_font(bold=False)

        title_size = max(16, int(height * 0.075))
        sub_size = max(12, int(height * 0.040))
        has_sub = bool((entry.subtitle or "").strip())

        draws = []
        # Title text file.
        title_path = os.path.join(tmpdir, "title_%04d_main.txt" % idx)
        with open(title_path, "w", encoding="utf-8") as handle:
            handle.write(entry.text or "")
        title_y = "(h-text_h)/2-(h*0.06)" if has_sub else "(h-text_h)/2"
        draws.append(
            "drawtext=textfile=%s:fontfile=%s:fontcolor=%s:fontsize=%d:"
            "x=(w-text_w)/2:y=%s"
            % (title_path, font_bold, fg, title_size, title_y))

        if has_sub:
            sub_path = os.path.join(tmpdir, "title_%04d_sub.txt" % idx)
            with open(sub_path, "w", encoding="utf-8") as handle:
                handle.write(entry.subtitle or "")
            draws.append(
                "drawtext=textfile=%s:fontfile=%s:fontcolor=%s:fontsize=%d:"
                "x=(w-text_w)/2:y=(h-text_h)/2+(h*0.06)"
                % (sub_path, font, fg, sub_size))

        draws.append("format=yuv420p")

        # Background layer: a user-supplied image scaled onto the card's frame,
        # or (the default) a solid colour.  If the image path has gone missing
        # we quietly fall back to the colour rather than failing the render.
        bg_image = getattr(entry, "bg_image", "") or ""
        scaling = getattr(entry, "bg_scaling", "fill") or "fill"
        use_image = bool(bg_image) and os.path.exists(bg_image)

        if use_image:
            if scaling == "stretch":
                # Scale to the exact frame, ignoring aspect ratio (may distort).
                bg_filter = "scale=%d:%d,setsar=1" % (width, height)
            elif scaling == "fit":
                # Whole image visible, letterboxed with the background colour.
                bg_filter = (
                    "scale=%d:%d:force_original_aspect_ratio=decrease,"
                    "pad=%d:%d:(%d-iw)/2:(%d-ih)/2:color=%s,setsar=1"
                    % (width, height, width, height, width, height, bg))
            else:
                # "fill": cover the frame keeping aspect ratio, cropping overflow.
                bg_filter = (
                    "scale=%d:%d:force_original_aspect_ratio=increase,"
                    "crop=%d:%d,setsar=1" % (width, height, width, height))
            vf = ",".join([bg_filter] + draws)
            video_input = [
                "-loop", "1", "-framerate", "%d" % fps,
                "-t", "%.3f" % duration, "-i", bg_image,
            ]
        else:
            vf = ",".join(draws)
            video_input = [
                "-f", "lavfi",
                "-i", "color=c=%s:s=%dx%d:r=%d:d=%.3f"
                % (bg, width, height, fps, duration),
            ]

        cmd = [
            "ffmpeg", "-hide_banner", "-nostats", "-y",
            *video_input,
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-r", "%d" % fps,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-t", "%.3f" % duration, out,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(out):
            tail = "\n".join((result.stderr or "").strip().splitlines()[-6:])
            raise RuntimeError("Building the title card failed.\n\n" + tail)
        return out

    def _finalize(self, joined_ts, durations):
        """Write the joined .ts out in the requested format."""
        if self._out_format == "mkv":
            self.progress.emit(99, "Writing MKV…")
            _write_mkv_chapters(joined_ts, self._out, durations)
        elif self._out_format == "mp4":
            self.progress.emit(99, "Converting to MP4…")
            codec, interlaced = _probe_video(joined_ts)

            def _cb(data):
                # Keep the bar near the end during the MP4 encode.
                pct = data.get("percent", 0) if isinstance(data, dict) else 0
                self.progress.emit(min(99, 90 + pct // 10), "Converting to MP4…")

            _transcode_to_mp4(
                joined_ts, self._out, codec, interlaced,
                total_seconds=sum(durations), progress_cb=_cb)
        else:
            # Match source (.ts) - the joined file is the output.
            shutil.move(joined_ts, self._out)
