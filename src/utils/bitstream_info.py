"""Best-effort extraction of stream details that ffprobe doesn't expose:
H.264 entropy mode / VBV / header bit rate (from the SPS/PPS), the MPEG-2
equivalents (from the sequence header), and the PES stream id (from the
transport stream).

Everything here is defensive: any parse failure returns None for that field so
the caller can fall back to "n/a" rather than break the dialog.  The fields that
need a header which sample extracts often strip (the H.264 HRD parameters, the
MPEG-2 sequence header) should be sanity-checked against a reference on a full
recording.
"""


# -- H.264 SPS / PPS -------------------------------------------------------

class _Bits:
    """Bit reader over an RBSP, stripping emulation-prevention 0x03 bytes."""

    def __init__(self, data, strip_emulation=True):
        if strip_emulation:
            out = bytearray()
            zeros = 0
            for b in data:
                if zeros >= 2 and b == 3:
                    zeros = 0
                    continue
                out.append(b)
                zeros = zeros + 1 if b == 0 else 0
            self.d = bytes(out)
        else:
            self.d = bytes(data)
        self.pos = 0

    def u1(self):
        byte = self.d[self.pos >> 3]
        bit = (byte >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return bit

    def u(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.u1()
        return v

    def ue(self):
        zeros = 0
        while self.u1() == 0:
            zeros += 1
            if self.pos > len(self.d) * 8:
                raise ValueError("ue overrun")
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)

    def se(self):
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


def _skip_scaling_list(b, size):
    last = nxt = 8
    for _ in range(size):
        if nxt != 0:
            nxt = (last + b.se() + 256) % 256
        last = nxt if nxt != 0 else last


_H264_HIGH_PROFILES = (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135)


def _parse_sps(rbsp):
    b = _Bits(rbsp)
    profile = b.u(8)
    b.u(8)                      # constraint flags + reserved
    level = b.u(8)
    b.ue()                      # sps id
    chroma = 1
    if profile in _H264_HIGH_PROFILES:
        chroma = b.ue()
        if chroma == 3:
            b.u1()
        b.ue()                  # bit_depth_luma
        b.ue()                  # bit_depth_chroma
        b.u1()                  # qpprime
        if b.u1():              # scaling matrix present
            for i in range(8 if chroma != 3 else 12):
                if b.u1():
                    _skip_scaling_list(b, 16 if i < 6 else 64)
    b.ue()                      # log2_max_frame_num
    poc = b.ue()
    if poc == 0:
        b.ue()
    elif poc == 1:
        b.u1()
        b.se()
        b.se()
        for _ in range(b.ue()):
            b.se()
    b.ue()                      # max_num_ref_frames
    b.u1()                      # gaps_in_frame_num
    b.ue()                      # pic_width_in_mbs_minus1
    b.ue()                      # pic_height_in_map_units_minus1
    frame_mbs_only = b.u1()
    if not frame_mbs_only:
        b.u1()                  # mb_adaptive_frame_field
    b.u1()                      # direct_8x8
    if b.u1():                  # frame_cropping
        b.ue()
        b.ue()
        b.ue()
        b.ue()

    out = {"mbaff": not frame_mbs_only}

    if b.u1():                  # vui_parameters_present
        if b.u1():              # aspect_ratio_info
            if b.u(8) == 255:
                b.u(16)
                b.u(16)
        if b.u1():              # overscan
            b.u1()
        if b.u1():              # video_signal_type
            b.u(3)
            b.u1()
            if b.u1():
                b.u(8)
                b.u(8)
                b.u(8)
        if b.u1():              # chroma_loc_info
            b.ue()
            b.ue()
        if b.u1():              # timing_info
            b.u(32)
            b.u(32)
            b.u1()

        def parse_hrd():
            cpb_cnt = b.ue() + 1
            br_scale = b.u(4)
            cs_scale = b.u(4)
            br = cs = None
            for i in range(cpb_cnt):
                brv = b.ue()
                csv = b.ue()
                b.u1()
                if i == 0:
                    br = (brv + 1) * (1 << (6 + br_scale))
                    cs = (csv + 1) * (1 << (4 + cs_scale))
            b.u(5)
            b.u(5)
            b.u(5)
            b.u(5)
            return br, cs

        hrd = None
        if b.u1():              # nal_hrd
            hrd = parse_hrd()
        if b.u1():              # vcl_hrd
            vcl = parse_hrd()
            if hrd is None:
                hrd = vcl
        if hrd:
            out["header_bitrate"] = hrd[0]     # bits/sec
            out["vbv_bytes"] = hrd[1] // 8      # cpb size in bytes

    return out


def _parse_pps_entropy(rbsp):
    b = _Bits(rbsp)
    b.ue()                      # pps id
    b.ue()                      # sps id
    return "CABAC" if b.u1() else "CAVLC"


def _split_annexb(data):
    nals = []
    i = 0
    n = len(data)
    while i < n:
        if data[i:i + 3] == b"\x00\x00\x01":
            j = i + 3
            while j < n and data[j:j + 3] != b"\x00\x00\x01":
                j += 1
            nals.append(data[i + 3:j])
            i = j
        else:
            i += 1
    return nals


def _h264_from_extradata(extradata):
    """Return {entropy_mode, mbaff, header_bitrate, vbv_bytes} where available."""
    info = {}
    if not extradata:
        return info
    data = bytes(extradata)
    # avcC (length-prefixed) vs annexb (start codes)
    if data[0] == 1 and len(data) > 7:
        nals = _avcc_nals(data)
    else:
        nals = _split_annexb(data)
    for nal in nals:
        if not nal:
            continue
        t = nal[0] & 0x1F
        try:
            if t == 7 and "mbaff" not in info:
                info.update(_parse_sps(nal[1:]))
            elif t == 8 and "entropy_mode" not in info:
                info["entropy_mode"] = _parse_pps_entropy(nal[1:])
        except Exception:
            continue
    return info


def _avcc_nals(data):
    nals = []
    try:
        num_sps = data[5] & 0x1F
        i = 6
        for _ in range(num_sps):
            length = (data[i] << 8) | data[i + 1]
            i += 2
            nals.append(data[i:i + length])
            i += length
        num_pps = data[i]
        i += 1
        for _ in range(num_pps):
            length = (data[i] << 8) | data[i + 1]
            i += 2
            nals.append(data[i:i + length])
            i += length
    except Exception:
        pass
    return nals


# -- transport-stream scanning (PES stream id, MPEG-2 sequence header) -------

def _iter_ts_payloads(path, target_pid, max_bytes=8_000_000):
    """Yield (payload_unit_start, payload_bytes) for packets on target_pid."""
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
    except OSError:
        return
    i = 0
    n = len(data)
    while i + 188 <= n:
        if data[i] != 0x47:
            i += 1
            continue
        b1 = data[i + 1]
        b2 = data[i + 2]
        pid = ((b1 & 0x1F) << 8) | b2
        if pid == target_pid:
            pusi = (b1 >> 6) & 1
            afc = (data[i + 3] >> 4) & 3
            payload = i + 4
            if afc in (2, 3):
                payload += 1 + data[i + 4]   # skip adaptation field
            if afc in (1, 3) and payload < i + 188:
                yield pusi, data[payload:i + 188]
        i += 188


def pes_stream_id(path, pid):
    """The PES stream_id byte for `pid`, or None."""
    if pid is None:
        return None
    try:
        for pusi, payload in _iter_ts_payloads(path, pid):
            if pusi and payload[:3] == b"\x00\x00\x01":
                return payload[3]
    except Exception:
        pass
    return None


def mpeg2_seq_header(path, pid):
    """Header bit rate (bps) and VBV size (bytes) from the MPEG-2 sequence
    header on `pid`, or {} if not found."""
    if pid is None:
        return {}
    try:
        buf = bytearray()
        for _pusi, payload in _iter_ts_payloads(path, pid):
            buf += payload
            idx = buf.find(b"\x00\x00\x01\xb3")
            if idx >= 0 and len(buf) >= idx + 12:
                b = _Bits(bytes(buf[idx + 4:idx + 12]), strip_emulation=False)
                b.u(12)                     # horizontal_size
                b.u(12)                     # vertical_size
                b.u(4)                      # aspect_ratio
                b.u(4)                      # frame_rate_code
                bit_rate_value = b.u(18)
                b.u1()                      # marker
                vbv = b.u(10)
                out = {}
                if bit_rate_value and bit_rate_value != 0x3FFFF:
                    out["header_bitrate"] = bit_rate_value * 400
                if vbv:
                    out["vbv_bytes"] = vbv * 2048
                return out
            if len(buf) > 64_000:           # don't accumulate forever
                buf = buf[-4:]
    except Exception:
        pass
    return {}


# -- MPEG-1/2 audio frame header --------------------------------------------
# ffprobe sometimes can't determine the layer / channel count / sample rate of
# a secondary broadcast audio track (it reports mp3 / 0 channels / 0 Hz).  The
# 4-byte frame header carries all three, so parse it straight from the stream.

_MPA_LAYER = {1: 3, 2: 2, 3: 1}          # layer bits -> layer number
_MPA_SRATE = {                            # version bits -> [by sample-rate idx]
    3: [44100, 48000, 32000],            # MPEG-1
    2: [22050, 24000, 16000],            # MPEG-2
    0: [11025, 12000, 8000],             # MPEG-2.5
}

# Bit-rate tables (kbps), indexed by [version_group][layer][bitrate_idx].
# version_group 3 = MPEG-1; group 2 = MPEG-2 / 2.5 (LSF), which share a table.
# Index 0 ("free") and 15 ("bad") map to None and are treated as unknown.
# UK broadcast MP2 is MPEG-1 Layer II, so the group-3 / layer-2 row is the one
# that matters in practice, but the full tables keep us correct for any stream.
_MPA_BITRATE = {
    3: {                                  # MPEG-1
        1: [None, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, None],
        2: [None, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, None],
        3: [None, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, None],
    },
    2: {                                  # MPEG-2 / 2.5 (low sampling frequency)
        1: [None, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, None],
        2: [None, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, None],
        3: [None, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, None],
    },
}


def _pes_payload_to_es(payload):
    if payload[:3] == b"\x00\x00\x01" and len(payload) > 9:
        if (payload[6] & 0xC0) == 0x80:
            hdr_len = payload[8]
            return payload[9 + hdr_len:]
        return payload[6:]
    return payload


def mpeg_audio_header(path, pid):
    """{layer, sample_rate, channels, bitrate} parsed from the first MPEG audio
    frame on `pid`, or {} if not found.  `bitrate` is in kbps."""
    if pid is None:
        return {}
    try:
        for pusi, payload in _iter_ts_payloads(path, pid):
            if not pusi:
                continue
            es = _pes_payload_to_es(payload)
            for k in range(len(es) - 4):
                if es[k] != 0xFF or (es[k + 1] & 0xE0) != 0xE0:
                    continue
                version = (es[k + 1] >> 3) & 3
                layer_bits = (es[k + 1] >> 1) & 3
                if version == 1 or layer_bits == 0:      # reserved
                    continue
                srate_idx = (es[k + 2] >> 2) & 3
                if srate_idx == 3:
                    continue
                layer = _MPA_LAYER.get(layer_bits)
                br_idx = (es[k + 2] >> 4) & 0xF
                vgroup = 3 if version == 3 else 2
                bitrate = None
                if layer and 0 < br_idx < 15:
                    bitrate = _MPA_BITRATE.get(vgroup, {}).get(
                        layer, [None] * 16)[br_idx]
                channel_mode = (es[k + 3] >> 6) & 3
                return {
                    "layer": layer,
                    "sample_rate": _MPA_SRATE.get(version, [None, None, None])[srate_idx],
                    "channels": 1 if channel_mode == 3 else 2,
                    "bitrate": bitrate,          # kbps, or None if free/unknown
                }
            return {}
    except Exception:
        pass
    return {}
