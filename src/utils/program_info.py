"""Gather human-readable stream/format information for a media file.

Built on ffprobe (already a dependency via ffmpeg), this collects the same kind
of details VideoReDo's "Program Information" dialog shows - file, video and
audio stream properties - and returns them as ordered sections ready to render
or copy to the clipboard.

A few fields VideoReDo derives from the raw H.264 bitstream (VBV buffer, entropy
mode, the audio PES stream id, and the decoded sample size) aren't exposed by
ffprobe, so they're parsed from the bitstream where possible and otherwise
reported as "N/A".
"""

import json
import os
import subprocess


_NA = "N/A"


def _run_ffprobe(path):
    """Return ffprobe's parsed JSON (format + streams), or {} on any failure."""
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-probesize", "50M", "-analyzeduration", "50M",
                "-show_format", "-show_streams",
                "-of", "json", path,
            ],
            capture_output=True, text=True,
        )
        if proc.returncode == 0 and proc.stdout:
            return json.loads(proc.stdout)
    except Exception:
        pass
    return {}


# -- formatting helpers ----------------------------------------------------

def _human_size(num_bytes):
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return _NA
    gb = n / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.3f} GB"
    mb = n / (1024 ** 2)
    if mb >= 1.0:
        return f"{mb:.3f} MB"
    return f"{n / 1024:.1f} KB"


def _ratio(value):
    """Parse a 'num/den' string into a float, or None."""
    try:
        if "/" in str(value):
            num, den = str(value).split("/")
            den = float(den)
            return float(num) / den if den else None
        return float(value)
    except (TypeError, ValueError):
        return None


def _duration_timecode(seconds, fps):
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return _NA
    if total < 0:
        return _NA
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = int(total % 60)
    frac = total - int(total)
    if fps and fps > 0:
        frames = int(round(frac * fps))
        if frames >= int(round(fps)):
            frames = int(round(fps)) - 1
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{frames:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{int(frac * 100):02d}"


def _stream_id(stream):
    """VideoReDo-style 'xNNN' hex id from the stream's id field."""
    raw = stream.get("id")
    if raw is None:
        idx = stream.get("index")
        return f"#{idx}" if idx is not None else _NA
    try:
        n = int(str(raw), 0)        # handles '0x101' and plain ints
        return f"x{n:X}"
    except (TypeError, ValueError):
        return str(raw)


_VIDEO_CODECS = {
    "h264": "H.264", "hevc": "HEVC", "mpeg2video": "MPEG-2",
    "mpeg1video": "MPEG-1", "mpeg4": "MPEG-4", "vc1": "VC-1", "av1": "AV1",
}
_AUDIO_CODECS = {
    "aac": "AAC", "aac_latm": "AAC", "ac3": "AC-3", "eac3": "E-AC-3",
    "mp2": "MP2", "mp3": "MP3", "dts": "DTS", "opus": "Opus", "flac": "FLAC",
}
_CHROMA = {
    "yuv420p": "4:2:0", "yuvj420p": "4:2:0", "yuv422p": "4:2:2",
    "yuvj422p": "4:2:2", "yuv444p": "4:4:4", "yuvj444p": "4:4:4",
    "yuv420p10le": "4:2:0 (10-bit)", "yuv422p10le": "4:2:2 (10-bit)",
}
_MUX = {
    "mpegts": "MPEG-TS", "matroska,webm": "MKV", "matroska": "MKV",
    "mov,mp4,m4a,3gp,3g2,mj2": "MP4", "mpeg": "MPEG-PS", "avi": "AVI",
}


def _field_order(value):
    if not value:
        return "Unknown"
    v = str(value).lower()
    if v == "progressive":
        return "Progressive"
    if v in ("tt", "tb"):
        return "Interlaced (top field first)"
    if v in ("bb", "bt"):
        return "Interlaced (bottom field first)"
    return "Interlaced"


def _bitrate(bps, audio=False):
    try:
        n = float(bps)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if audio:
        return f"{n / 1000:.0f} Kbps"
    return f"{n / 1_000_000:.3f} Mbps"


def _channels(n):
    mapping = {1: "1.0 (mono)", 2: "2.0 (stereo)", 6: "5.1", 8: "7.1"}
    try:
        n = int(n)
    except (TypeError, ValueError):
        return _NA
    return mapping.get(n, f"{n}.0")


# -- main entry ------------------------------------------------------------

# MPEG-2 level (ffprobe's numeric enum) -> VideoReDo-style name.
_MPEG2_LEVELS = {10: "Low", 8: "Main", 6: "High 1440", 4: "High"}

# H.264 streams from UK broadcast carry no HRD parameters, so there's no
# per-file header bit rate / VBV in the bitstream.  VideoReDo instead shows the
# ceiling implied by the profile@level:
#
#   * Header bit rate = MaxBR x VCL factor (1250 for High profile).
#   * VBV buffer scales with the level's MaxCPB ceiling, NOT the bit rate.
#     Confirmed against VideoReDo: Level 3.0 -> 228 KBytes, Level 4.0 ->
#     572 KBytes.  The ratio 572/228 = 2.51 matches the MaxCPB ratio
#     (25000/10000 = 2.5), not the MaxBR ratio (20000/10000 = 2.0).
#
# Levels 3.0 and 4.0 are confirmed against VideoReDo; the rest are derived from
# the same MaxCPB proportion (anchored on Level 4.0) and flagged as such.
# Sanity-check a derived level against VideoReDo the first time you meet a file
# that uses it, and correct the figure here if it differs.
_H264_VCL_FACTOR = 1250                      # High-profile cpbBrVclFactor
_H264_MAXBR = {30: 10000, 31: 14000, 32: 20000, 40: 20000, 41: 50000, 42: 50000}
_H264_VBV_BYTES = {
    30:  228 * 1024,                         # confirmed vs VideoReDo
    31:  320 * 1024,                         # derived (MaxCPB proportion)
    32:  458 * 1024,                         # derived (MaxCPB proportion)
    40:  572 * 1024,                         # confirmed vs VideoReDo
    41: 1430 * 1024,                         # derived (MaxCPB proportion)
    42: 1430 * 1024,                         # derived (MaxCPB proportion)
}


def _h264_level_limits(level):
    """(header_bitrate_bps, vbv_bytes_or_None) derived from the H.264 level."""
    br = _H264_MAXBR.get(level)
    bitrate = br * _H264_VCL_FACTOR if br else None
    return bitrate, _H264_VBV_BYTES.get(level)


# Audio codec label fallbacks (when the frame header can't be parsed).
# MPEG audio layer -> the label VideoReDo shows in the audio "Format" field.
# (VideoReDo splits MPEG audio into Codec="MPEG" + Format="Layer N"; we follow
# that, but use the clearer "MPEG Audio" for the codec.)
_MPA_FORMAT = {1: "Layer 1", 2: "Layer 2", 3: "Layer 3"}
_MPA_FORMAT_FALLBACK = {"mp1": "Layer 1", "mp2": "Layer 2", "mp3": "Layer 3"}


def _profile_level(codec_name, profile, level):
    """Format 'Profile/Level' (H.264/HEVC) or 'Profile@Level' (MPEG-2)."""
    if not profile:
        return _NA
    if codec_name in ("h264", "hevc") and level and level > 0:
        return f"{profile}/{level / 10:.1f}"
    if codec_name in ("mpeg2video", "mpeg1video") and level in _MPEG2_LEVELS:
        return f"{profile}@{_MPEG2_LEVELS[level]}"
    return str(profile)


def _video_header_extras(path, codec_name, pid):
    """Header-only fields ffprobe doesn't expose: entropy mode, VBV buffer,
    header bit rate, and (H.264) whether the stream is MBAFF-coded.  Returns a
    dict with whatever could be parsed."""
    from utils import bitstream_info as bs
    try:
        if codec_name in ("h264", "hevc"):
            import av
            with av.open(path) as c:
                v = next((s for s in c.streams if s.type == "video"), None)
                extradata = v.codec_context.extradata if v else None
            return bs._h264_from_extradata(extradata)
        if codec_name in ("mpeg2video", "mpeg1video"):
            return bs.mpeg2_seq_header(path, pid)
    except Exception:
        pass
    return {}


def _pid_int(stream):
    raw = stream.get("id")
    if raw is None:
        return None
    try:
        return int(str(raw), 0)
    except (TypeError, ValueError):
        return None


# Audio codecs that decode to 16-bit PCM, so report a 16-bit sample size the
# way VideoReDo does when ffprobe can't tell us (compressed formats report 0).
_PCM16_AUDIO = {"mp2", "mp3", "aac", "aac_latm", "ac3", "eac3", "dts"}


def gather_program_info(path, frame_count=None, fps=None):
    """Return a list of (section_title, [(label, value), ...]) for `path`.

    Always returns at least a File section; missing pieces are filled with
    "N/A" so the dialog layout stays stable.
    """
    data = _run_ffprobe(path)
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []

    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]

    # fps for the duration's frame component / the frame-rate display: prefer
    # the editor's index fps, fall back to ffprobe.
    video_fps = None
    if videos:
        video_fps = _ratio(videos[0].get("avg_frame_rate")) \
            or _ratio(videos[0].get("r_frame_rate"))
    disp_fps = fps or video_fps

    sections = []

    # ---- File -------------------------------------------------------------
    # Prefer the editor's own frame-accurate length (frame_count / fps) so this
    # matches the editor's timeline - and VideoReDo, which also reports the
    # video duration.  The container duration spans the audio too, which on
    # broadcast .ts often starts a second or two before the video and so reads
    # a touch longer.
    if frame_count and fps:
        duration = frame_count / fps
    elif videos and videos[0].get("duration"):
        duration = videos[0].get("duration")
    else:
        duration = fmt.get("duration")

    mux = _MUX.get(fmt.get("format_name", ""), (fmt.get("format_name") or _NA).upper())
    try:
        size = os.path.getsize(path)
    except OSError:
        size = fmt.get("size")
    sections.append(("File", [
        ("Name", os.path.basename(path)),
        ("Size", _human_size(size)),
        ("Duration", _duration_timecode(duration, disp_fps)),
        ("Mux type", mux),
    ]))

    # ---- Video ------------------------------------------------------------
    if videos:
        v = videos[0]
        codec_name = v.get("codec_name", "")
        codec = _VIDEO_CODECS.get(codec_name, (codec_name or _NA).upper())

        extras = _video_header_extras(path, codec_name, _pid_int(v))

        avg = _ratio(v.get("avg_frame_rate"))
        frame_rate = f"{avg:.2f} fps" if avg else _NA
        # Broadcast .ts is constant frame rate; a differing r_frame_rate just
        # reflects field coding (50 fields vs 25 frames), not VFR.
        rate_flag = "Constant" if avg else _NA

        w, h = v.get("width"), v.get("height")
        enc_size = f"{w} x {h}" if w and h else _NA

        prof = _profile_level(codec_name, v.get("profile"), v.get("level"))

        bit = _bitrate(v.get("bit_rate"))
        if bit is None:
            # ffprobe often omits per-stream rate for .ts; fall back to the
            # overall file rate so the field isn't blank.
            bit = _bitrate(fmt.get("bit_rate")) or _NA

        header_bit_bps = extras.get("header_bitrate")
        vbv = extras.get("vbv_bytes")
        # H.264/HEVC streams here carry no HRD, so fall back to the level ceiling
        # (what VideoReDo shows).
        if header_bit_bps is None and codec_name in ("h264", "hevc"):
            lvl_br, lvl_vbv = _h264_level_limits(v.get("level"))
            header_bit_bps = header_bit_bps or lvl_br
            vbv = vbv or lvl_vbv

        header_bit = _bitrate(header_bit_bps) or _NA
        vbv_str = f"{round(vbv / 1024)} KBytes" if vbv else _NA

        if extras.get("mbaff"):
            progressive = "Interlaced (MBAFF)"
        else:
            progressive = _field_order(v.get("field_order"))

        # Entropy coding mode is an H.264/HEVC concept (CABAC/CAVLC); MPEG-2
        # has no equivalent, so it reads N/A there.
        entropy = extras.get("entropy_mode") or _NA

        sections.append(("Video", [
            ("Encoding", codec),
            ("Stream ID", _stream_id(v)),
            ("Frame rate", frame_rate),
            ("Frame rate flag", rate_flag),
            ("Encoding size", enc_size),
            ("Aspect ratio", v.get("display_aspect_ratio") or _NA),
            ("Header bit rate", header_bit),
            ("VBV buffer", vbv_str),
            ("Profile", prof),
            ("Progressive", progressive),
            ("Chroma", _CHROMA.get(v.get("pix_fmt", ""), v.get("pix_fmt") or _NA)),
            ("Entropy mode", entropy),
            ("Bit rate", bit),
        ]))

    # ---- Audio (one section per stream) ----------------------------------
    from utils.bitstream_info import pes_stream_id, mpeg_audio_header

    for i, a in enumerate(audios, start=1):
        codec_name = a.get("codec_name", "")
        pid = _pid_int(a)

        # ffprobe can mis-identify a secondary MPEG audio track (reporting mp3
        # with 0 channels / 0 Hz), so read the frame header from the stream and
        # prefer it for the codec/channels/rate.
        mpa = {}
        if codec_name in ("mp1", "mp2", "mp3"):
            mpa = mpeg_audio_header(path, pid)

        # Codec vs Format split, matching VideoReDo's two-field layout:
        #   * MPEG audio -> Codec "MPEG Audio", Format "Layer 1/2/3".
        #   * AAC        -> Codec "AAC",        Format "LATM/LOAS" or "ADTS / Raw".
        # The layer / transport wrapper is the distinguishing "format" for each.
        if codec_name in ("mp1", "mp2", "mp3"):
            codec = "MPEG Audio"
            layer = mpa.get("layer")
            fmt_label = _MPA_FORMAT.get(layer) \
                or _MPA_FORMAT_FALLBACK.get(codec_name, _NA)
        elif codec_name in ("aac", "aac_latm"):
            codec = "AAC"
            fmt_label = "LATM/LOAS" if codec_name == "aac_latm" else "ADTS / Raw"
        else:
            codec = _AUDIO_CODECS.get(codec_name, (codec_name or _NA).upper())
            fmt_label = _NA

        lang = (a.get("tags", {}) or {}).get("language", _NA)

        channels = a.get("channels") or mpa.get("channels")

        # Audio bit rate: ffprobe rarely fills it for .ts, so for MPEG audio fall
        # back to the value read from the frame header.  AAC has no simple CBR
        # field, so it stays N/A there (as VideoReDo also leaves it blank).
        audio_bitrate = _bitrate(a.get("bit_rate"), audio=True)
        if audio_bitrate is None and mpa.get("bitrate"):
            audio_bitrate = _bitrate(mpa["bitrate"] * 1000, audio=True)
        audio_bitrate = audio_bitrate or _NA

        rate = a.get("sample_rate")
        try:
            rate_ok = rate is not None and int(rate) > 0
        except (TypeError, ValueError):
            rate_ok = False
        if not rate_ok:
            rate = mpa.get("sample_rate")
        sample_rate = f"{rate} Hz" if rate else _NA

        bits = a.get("bits_per_raw_sample") or a.get("bits_per_sample")
        if bits and int(bits) > 0:
            sample_size = f"{bits} bits"
        elif codec_name in _PCM16_AUDIO:
            sample_size = "16 bits"
        else:
            sample_size = _NA

        pes = pes_stream_id(path, pid)
        pes_str = f"x{pes:02X}" if pes is not None else _NA

        title = "Audio Stream: %d%s" % (i, " (Primary)" if i == 1 else "")
        sections.append((title, [
            ("Codec", codec),
            ("Format", fmt_label),
            ("Channels", _channels(channels)),
            ("Language", lang),
            ("PID", _stream_id(a)),
            ("PES Stream Id", pes_str),
            ("Bit rate", audio_bitrate),
            ("Sampling rate", sample_rate),
            ("Sample size", sample_size),
        ]))

    return sections


def to_plaintext(sections):
    """Render gathered sections as plain text for the clipboard."""
    lines = []
    for title, rows in sections:
        lines.append(title + ":")
        width = max((len(label) for label, _ in rows), default=0)
        for label, value in rows:
            lines.append(f"  {label.ljust(width)}  {value}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
