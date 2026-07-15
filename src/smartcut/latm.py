"""LOAS/LATM depacketising for broadcast AAC.

UK DVB broadcasts carry AAC wrapped in LATM/LOAS framing with the decoder
configuration transmitted in-band, which most containers and tools can't copy
directly (ffmpeg can only stream-copy aac_latm back into another transport
stream).  This module unwraps each LOAS frame to the raw AAC payload and
re-wraps it as ADTS - the plain, self-describing AAC framing that every
container, player and mkvmerge accept - completely losslessly: the compressed
audio bytes are untouched, only the wrapper changes.

Scope is deliberately the broadcast profile: audioMuxVersion 0, one program,
one layer, all streams same time framing, frameLengthType 0.  Anything else
raises LatmError, and the caller falls back to the plain packet copy exactly
as before (the exporter's decode check and audio graft remain as the safety
net behind that).
"""

import numpy as np


class LatmError(Exception):
    """The stream isn't the broadcast LATM profile this parser supports."""


class _Bits:
    """Minimal big-endian bit reader over a bytes object."""

    def __init__(self, data):
        self.data = data
        self.pos = 0          # bit position

    def read(self, n):
        v = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v


# samplingFrequencyIndex -> Hz (ISO 14496-3 table 1.18)
_SR_TABLE = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
             16000, 12000, 11025, 8000, 7350, 0, 0, 0)


def _parse_audio_specific_config(bits):
    """Parse an AudioSpecificConfig; returns (object_type, sr_index, sr_hz,
    channel_config, asc_bit_length).  Only the GA (AAC) family without core
    coder or extension flags is supported - the DVB broadcast case."""
    start = bits.pos
    object_type = bits.read(5)
    if object_type == 31:
        raise LatmError("escape audioObjectType not supported")
    sr_index = bits.read(4)
    if sr_index == 15:
        sr_hz = bits.read(24)
    else:
        sr_hz = _SR_TABLE[sr_index]
    channel_config = bits.read(4)

    # Explicit SBR/PS signalling (object types 5/29) wraps the real object.
    if object_type in (5, 29):
        ext_sr_index = bits.read(4)
        if ext_sr_index == 15:
            bits.read(24)
        object_type = bits.read(5)
        if object_type == 31:
            raise LatmError("escape audioObjectType not supported")

    if object_type not in (1, 2, 3, 4):
        raise LatmError("unsupported audioObjectType %d" % object_type)

    # GASpecificConfig
    bits.read(1)                       # frameLengthFlag
    if bits.read(1):                   # dependsOnCoreCoder
        raise LatmError("core coder not supported")
    if bits.read(1):                   # extensionFlag
        raise LatmError("GASpecificConfig extensionFlag not supported")

    return object_type, sr_index, sr_hz, channel_config, bits.pos - start


class LatmRepacketiser:
    """Rewrap LOAS/LATM frames as ADTS, byte-for-byte lossless on the payload.

    Built from the first LOAS frame (which must carry the StreamMuxConfig);
    subsequent frames reuse the parsed configuration.  A mid-stream
    configuration change is re-parsed transparently.
    """

    def __init__(self, first_frame):
        self.object_type = None
        self.sr_index = None
        self.sample_rate = None
        self.channel_config = None
        # Cached raw config bits (as bytes prefix) and total header bit length
        # so identical frames skip the full parse.
        self._config_probe = None
        self._config_bits = None
        self._parse_config(first_frame)

    # -- configuration --------------------------------------------------

    def _parse_config(self, frame):
        """Parse the StreamMuxConfig of a config-carrying LOAS frame and cache
        everything needed to unwrap follow-up frames quickly."""
        if len(frame) < 6 or frame[0] != 0x56 or (frame[1] & 0xE0) != 0xE0:
            raise LatmError("no LOAS sync")
        bits = _Bits(frame)
        bits.pos = 24                         # skip LOAS header
        if bits.read(1):                      # useSameStreamMux
            raise LatmError("first frame carries no StreamMuxConfig")
        if bits.read(1):                      # audioMuxVersion
            raise LatmError("audioMuxVersion 1 not supported")
        if not bits.read(1):                  # allStreamsSameTimeFraming
            raise LatmError("allStreamsSameTimeFraming=0 not supported")
        if bits.read(6):                      # numSubFrames
            raise LatmError("numSubFrames != 0 not supported")
        if bits.read(4):                      # numProgram
            raise LatmError("multiple programs not supported")
        if bits.read(3):                      # numLayer
            raise LatmError("multiple layers not supported")

        (self.object_type, self.sr_index, self.sample_rate,
         self.channel_config, _asc_len) = _parse_audio_specific_config(bits)

        if bits.read(3):                      # frameLengthType
            raise LatmError("frameLengthType != 0 not supported")
        bits.read(8)                          # latmBufferFullness

        if bits.read(1):                      # otherDataPresent
            # otherDataLenBits accumulates in (esc, 8-bit) chunks.
            other = 0
            while True:
                esc = bits.read(1)
                other = (other << 8) | bits.read(8)
                if not esc:
                    break
            bits.read(other)                  # skip the other data
        if bits.read(1):                      # crcCheckPresent
            bits.read(8)                      # crcCheckSum

        # Cache: the config prefix (whole bytes covering the header bits) lets
        # identical follow-up frames skip re-parsing; the bit length tells us
        # where PayloadLengthInfo starts.  _config_bits deliberately excludes
        # the leading useSameStreamMux bit (accounted for by the caller).
        self._config_bits = bits.pos - 25
        n_bytes = (bits.pos + 7) // 8
        self._config_probe = bytes(frame[3:n_bytes])

    # -- per-frame unwrap -------------------------------------------------

    def _payload_span(self, frame):
        """(payload_start_bit, payload_len_bytes) within a LOAS frame."""
        bits = _Bits(frame)
        bits.pos = 24
        if bits.read(1):                      # useSameStreamMux == 1
            hdr_bits = 1
        else:
            # Config present: identical to the cached one?  (Compare the raw
            # prefix bytes - cheap - before falling back to a full re-parse.)
            if frame[3:3 + len(self._config_probe)] != self._config_probe:
                self._parse_config(frame)     # configuration changed
            hdr_bits = 1 + self._config_bits
            bits.pos = 24 + hdr_bits

        # PayloadLengthInfo: 8-bit chunks of 255 accumulate until a chunk < 255.
        length = 0
        while True:
            chunk = bits.read(8)
            length += chunk
            if chunk != 255:
                break
        return bits.pos, length

    def _extract(self, frame):
        """The raw AAC payload bytes of one LOAS frame (bit-aligned copy)."""
        start_bit, length = self._payload_span(frame)
        if length <= 0:
            raise LatmError("empty payload")
        first_byte = start_bit >> 3
        shift = start_bit & 7
        end_byte = first_byte + length + (1 if shift else 0)
        if end_byte > len(frame):
            raise LatmError("payload overruns frame")
        raw = np.frombuffer(frame, dtype=np.uint8, count=end_byte - first_byte,
                            offset=first_byte)
        if shift == 0:
            return raw[:length].tobytes()
        # Bit-shift the whole span left by `shift` bits, vectorised.
        a = raw[:-1].astype(np.uint16)
        b = raw[1:].astype(np.uint16)
        out = ((a << shift) | (b >> (8 - shift))) & 0xFF
        return out[:length].astype(np.uint8).tobytes()

    def to_adts(self, frame):
        """One LOAS frame -> one ADTS frame (7-byte header + raw payload)."""
        payload = self._extract(frame)
        n = len(payload) + 7
        profile = self.object_type - 1        # ADTS profile field
        hdr = bytes((
            0xFF,
            0xF1,                             # MPEG-4, no CRC
            ((profile & 3) << 6) | ((self.sr_index & 0xF) << 2)
            | ((self.channel_config >> 2) & 1),
            ((self.channel_config & 3) << 6) | ((n >> 11) & 3),
            (n >> 3) & 0xFF,
            ((n & 7) << 5) | 0x1F,
            0xFC,
        ))
        return hdr + payload
