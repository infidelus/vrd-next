"""Pixel cropping for letterboxed recordings.

Cropping the black bars off a recording is the one export that can't be done by
stream copy - the bars are baked into the pixels, so the video has to be
re-encoded.  This module is therefore deliberately separate from the lossless
smart-cut path: it runs only when a profile asks for a crop, and re-encodes the
already-cut output with the bars removed.

The encode recipe mirrors what VideoReDo does for the same job: H.264 via
libx264, a single pass at the source's own bitrate (predictable size, minimal
visible loss), a one-second GOP and 4:2:0 - so the result sits where a VRD user
would expect it to.  Audio and subtitles are copied straight through.

ffmpeg/ffprobe are expected on the PATH, as elsewhere in the exporter.
"""

import os
import re
import subprocess

_CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
_DEFAULT_KBPS = 8000          # fallback target when the source bitrate is unknown
_DEFAULT_FPS = 25.0


def _run(cmd, cancel_cb=None):
    """Run ffmpeg/ffprobe, returning the CompletedProcess.

    stderr is captured (ffmpeg's filters and progress go there).  A long encode
    is polled so a cancel request can terminate it promptly.
    """
    if cancel_cb is None:
        return subprocess.run(cmd, capture_output=True, text=True)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    while proc.poll() is None:
        try:
            if cancel_cb():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
        except Exception:
            pass
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            continue
    out, err = proc.communicate()
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _probe(path, entries, stream=False):
    """Return ffprobe's value(s) for ``entries`` as a list of strings."""
    scope = "stream=%s" % entries if stream else "format=%s" % entries
    cmd = [
        "ffprobe", "-hide_banner", "-loglevel", "error",
        "-select_streams", "v:0", "-show_entries", scope,
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
        return [ln.strip() for ln in out.splitlines() if ln.strip()]
    except Exception:
        return []


def source_dimensions(path):
    """(width, height) of the first video stream, or (0, 0)."""
    vals = _probe(path, "width,height", stream=True)
    try:
        return int(vals[0]), int(vals[1])
    except (IndexError, ValueError):
        return 0, 0


def source_fps(path):
    """Frame rate as a float, defaulting to 25.0 when it can't be read."""
    vals = _probe(path, "r_frame_rate", stream=True)
    try:
        num, den = vals[0].split("/")
        fps = float(num) / float(den)
        return fps if fps > 0 else _DEFAULT_FPS
    except (IndexError, ValueError, ZeroDivisionError):
        return _DEFAULT_FPS


def source_field_order(path):
    """Scan type of the first video stream.

    Returns "tt" (top field first), "bb" (bottom field first), "progressive", or
    "" when it can't be read.  Used to keep an interlaced source interlaced and a
    progressive one progressive, so the crop never changes the scan type.
    """
    vals = _probe(path, "field_order", stream=True)
    fo = vals[0].strip().lower() if vals else ""
    if fo in ("tt", "tb"):
        return "tt"
    if fo in ("bb", "bt"):
        return "bb"
    if fo == "progressive":
        return "progressive"
    return ""


def source_duration(path):
    """Duration in seconds, or 0.0 when unknown."""
    vals = _probe(path, "duration")
    try:
        return float(vals[0])
    except (IndexError, ValueError):
        return 0.0


def _audio_kbps(path):
    """Combined declared bitrate of the audio streams, in kbps (0 if unknown)."""
    cmd = [
        "ffprobe", "-hide_banner", "-loglevel", "error",
        "-select_streams", "a", "-show_entries", "stream=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
    except Exception:
        return 0
    total = 0
    for line in out.splitlines():
        try:
            total += int(line.strip())
        except ValueError:
            pass
    return total // 1000


def target_cap_kbps(path):
    """Bitrate ceiling for the crop re-encode: the source video's own budget.

    The video stream's own bitrate is often unreadable (notably broadcast .ts and
    .mkv), so instead we take the container's total bitrate - which is reliable -
    and subtract the audio.  This is used only as a *ceiling*: the encode is
    quality-driven (CRF) and on an inefficient source lands well below it, so the
    cropped file ends up no larger than the source and usually smaller.
    """
    total = 0
    vals = _probe(path, "bit_rate")          # format (container) bitrate
    try:
        total = int(vals[0]) // 1000
    except (IndexError, ValueError):
        total = 0
    if total <= 0:
        size = _probe(path, "size")
        dur = source_duration(path)
        try:
            total = int((int(size[0]) * 8) / dur / 1000)
        except (IndexError, ValueError, ZeroDivisionError):
            total = _DEFAULT_KBPS
    cap = total - _audio_kbps(path)
    return max(500, cap)


def _even(n):
    """Round down to the nearest even number (4:2:0 needs even dimensions)."""
    n = int(n)
    return n - (n % 2)


def even_rect(src_w, src_h, top, bottom, left, right, interlaced=False):
    """Turn edge crop amounts into an ffmpeg crop rectangle (w, h, x, y).

    Values are forced even and clamped inside the frame; if the crop would leave
    nothing, returns the full frame unchanged.  For an interlaced source the
    height is kept a multiple of 4 (and the top offset even) so field parity is
    preserved through the crop.
    """
    top, bottom = max(0, _even(top)), max(0, _even(bottom))
    left, right = max(0, _even(left)), max(0, _even(right))
    w = _even(src_w - left - right)
    h = src_h - top - bottom
    if interlaced:
        h = h - (h % 4)                     # multiple of 4 for interlaced
    else:
        h = _even(h)
    if w <= 0 or h <= 0:
        full_h = src_h - (src_h % 4) if interlaced else _even(src_h)
        return _even(src_w), full_h, 0, 0
    return w, h, left, top


def detect_crop(path, src_w=0, src_h=0, samples=8):
    """Auto-detect the black-bar crop with ffmpeg's ``cropdetect``.

    Samples several points across the file - cropdetect converges on the
    smallest bars (largest picture) it sees within each window - then takes the
    most common result across windows, so a single dark or full-frame scene
    can't skew it.  Returns ``(top, bottom, left, right)`` in pixels, or
    ``(0, 0, 0, 0)`` if nothing was detected.
    """
    if not src_w or not src_h:
        src_w, src_h = source_dimensions(path)
    if not src_w or not src_h:
        return (0, 0, 0, 0)
    dur = source_duration(path)
    # Spread sample points across the middle 90% (skip intros/credits edges).
    if dur > 0:
        starts = [dur * (0.05 + 0.9 * i / max(1, samples - 1))
                  for i in range(samples)]
    else:
        starts = [0.0]

    found = {}
    for start in starts:
        cmd = ["ffmpeg", "-hide_banner", "-nostats",
               "-ss", "%.2f" % start, "-i", path,
               "-vf", "cropdetect=24:2:0", "-frames:v", "150",
               "-an", "-sn", "-f", "null", "-"]
        try:
            err = subprocess.run(cmd, capture_output=True, text=True).stderr
        except Exception:
            continue
        last = None
        for m in _CROP_RE.finditer(err):
            last = tuple(int(g) for g in m.groups())   # (w, h, x, y)
        if last:
            found[last] = found.get(last, 0) + 1

    if not found:
        # Seeking yielded nothing (an awkward or non-seekable file): fall back to
        # one bounded pass from the start rather than give up on the crop.
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
               "-vf", "cropdetect=24:2:0", "-t", "90",
               "-an", "-sn", "-f", "null", "-"]
        try:
            err = subprocess.run(cmd, capture_output=True, text=True).stderr
            last = None
            for m in _CROP_RE.finditer(err):
                last = tuple(int(g) for g in m.groups())
            if last:
                found[last] = 1
        except Exception:
            pass

    if not found:
        return (0, 0, 0, 0)
    # Most common; ties broken towards the largest kept area (least cropping).
    best = sorted(found.items(), key=lambda kv: (kv[1], kv[0][0] * kv[0][1]))[-1][0]
    w, h, x, y = best
    top = y
    bottom = src_h - h - y
    left = x
    right = src_w - w - x
    return (max(0, top), max(0, bottom), max(0, left), max(0, right))


def _run_progress(cmd, total_seconds, progress_cb, cancel_cb):
    """Run ffmpeg with ``-progress`` on stdout, driving a percentage callback.

    Parses ffmpeg's machine-readable progress (``out_time_us``) against the known
    duration to report 0-100%.  Works with ``progress_cb`` None too (batch with
    no UI) - it just runs.  Returns True on success.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    last = -1
    try:
        for line in proc.stdout:
            if cancel_cb is not None:
                try:
                    if cancel_cb():
                        proc.terminate()
                        break
                except Exception:
                    pass
            if (total_seconds > 0 and progress_cb is not None
                    and line.startswith("out_time_us=")):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                pct = max(0.0, min(100.0, us / 1e6 / total_seconds * 100.0))
                if int(pct) != last:
                    last = int(pct)
                    progress_cb(pct)
    finally:
        proc.wait()
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
    return proc.returncode == 0


def crop_reencode(in_path, out_path, rect, *, cap_kbps, fps, sar=None,
                  field_order="", total_seconds=0.0, progress_cb=None,
                  cancel_cb=None):
    """Re-encode ``in_path`` to ``out_path`` with the given crop rectangle.

    ``rect`` is (w, h, x, y) from :func:`even_rect`.  The video is re-encoded
    with libx264 at constant quality (CRF), capped at ``cap_kbps`` so it can
    never exceed the source's budget; audio and subtitles are copied.  ``sar``
    optionally sets the output sample aspect ratio as ``(num, den)``.

    ``field_order`` keeps the scan type unchanged: "tt"/"bb" encode interlaced
    with that field order (so an interlaced source stays interlaced), anything
    else encodes progressive.  ``total_seconds`` and ``progress_cb`` drive a
    0-100% progress report as the encode runs.  Returns True on success.
    """
    w, h, x, y = rect
    vf = "crop=%d:%d:%d:%d" % (w, h, x, y)
    if sar:
        vf += ",setsar=%d/%d" % (sar[0], sar[1])
    fps_i = max(1, int(round(fps)))
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-y",
        "-i", in_path, "-map", "0",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "faster",
        "-crf", "20",
        "-maxrate", "%dk" % cap_kbps,
        "-bufsize", "%dk" % (cap_kbps * 2),
        "-g", str(fps_i), "-keyint_min", str(fps_i),
        "-pix_fmt", "yuv420p",
    ]
    # Keep interlaced sources interlaced, with the original field order.
    if field_order in ("tt", "bb"):
        cmd += ["-flags", "+ilme+ildct",
                "-x264-params", "tff=1" if field_order == "tt" else "bff=1"]
    cmd += ["-c:a", "copy", "-c:s", "copy", "-progress", "pipe:1", out_path]
    return _run_progress(cmd, total_seconds, progress_cb, cancel_cb)


def detect_crop_window(path, at_seconds=0.0, src_w=0, src_h=0, frames=120):
    """Detect bars over a short run of frames from ``at_seconds`` (one pass).

    Far steadier than a single frame: cropdetect accumulates the largest picture
    it sees across the window, so brighter frames reveal the true edges and a
    momentarily dark frame can't fool it into over-cropping.  Still one ffmpeg
    pass, so it's quick enough for the preview's Auto-detect.  Returns
    ``(top, bottom, left, right)``.
    """
    if not src_w or not src_h:
        src_w, src_h = source_dimensions(path)
    if not src_w or not src_h:
        return (0, 0, 0, 0)
    cmd = ["ffmpeg", "-hide_banner", "-nostats",
           "-ss", "%.2f" % max(0.0, at_seconds), "-i", path,
           "-vf", "cropdetect=24:2:0", "-frames:v", str(frames),
           "-an", "-sn", "-f", "null", "-"]
    try:
        err = subprocess.run(cmd, capture_output=True, text=True).stderr
    except Exception:
        return (0, 0, 0, 0)
    last = None
    for m in _CROP_RE.finditer(err):
        last = tuple(int(g) for g in m.groups())
    if not last:
        return (0, 0, 0, 0)
    w, h, x, y = last
    return (max(0, y), max(0, src_h - h - y),
            max(0, x), max(0, src_w - w - x))


def extract_frame(path, out_png, at_seconds=None):
    """Pull a single representative frame from ``path`` to ``out_png`` (PNG).

    Defaults to ~40% into the file, past any black intro.  Returns True on
    success.  Used by the crop preview to show a real frame to crop against.
    """
    if at_seconds is None:
        dur = source_duration(path)
        at_seconds = dur * 0.4 if dur > 0 else 0.0
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", "%.2f" % at_seconds, "-i", path,
        "-frames:v", "1", out_png,
    ]
    try:
        rc = subprocess.run(cmd, capture_output=True).returncode
        return rc == 0 and os.path.exists(out_png) and os.path.getsize(out_png) > 0
    except Exception:
        return False


def resolve_crop_box(profile, in_path, src_w=0, src_h=0):
    """Work out the crop rectangle for a profile against ``in_path``.

    For "auto" the bars are detected; for "fixed" the profile's pixel amounts are
    used.  Returns (w, h, x, y) or ``None`` when the profile asks for no crop or
    nothing is croppable.
    """
    mode = getattr(profile, "crop_mode", "none")
    if mode not in ("auto", "fixed"):
        return None
    if not src_w or not src_h:
        src_w, src_h = source_dimensions(in_path)
    if not src_w or not src_h:
        return None
    if mode == "auto":
        top, bottom, left, right = detect_crop(in_path, src_w, src_h)
    else:
        top, bottom, left, right = profile.crop
    if not any((top, bottom, left, right)):
        return None
    interlaced = source_field_order(in_path) in ("tt", "bb")
    return even_rect(src_w, src_h, top, bottom, left, right, interlaced=interlaced)
