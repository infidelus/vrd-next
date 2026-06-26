"""Playback audio for VRD Next.

Audio is decoded with PyAV (only the audio stream - cheap, and crucially no
video decoding to fight the video preview or spam the console) and played
through Qt's QAudioSink in PULL mode:

    PyAV decode thread  ->  PCM ring buffer  ->  QAudioSink pulls (its thread)

A background thread seeks to the requested position, resamples everything to a
fixed s16 / stereo / 48 kHz format, and tops up a small ring buffer.  Qt's
audio sink pulls PCM straight from that buffer on its own thread via a
QIODevice, so the main/UI thread does no audio work during playback (which is
what kept the video preview from stuttering).

Every play_from() is a fresh start (resume after a pause is always a play_from
with an explicit position), which keeps the state simple and robust.

If PyAV or Qt Multimedia isn't available the controller becomes a silent no-op
so the rest of the application is unaffected.  (On Linux, Qt Multimedia ships
in the full PySide6 / PySide6-Addons package.)
"""

import time
import threading

try:
    import av
    from av.audio.resampler import AudioResampler
    _HAVE_AV = True
except Exception:
    _HAVE_AV = False

try:
    from PySide6.QtMultimedia import (
        QAudioFormat,
        QAudioSink,
        QMediaDevices,
    )
    from PySide6.QtCore import QIODevice
    _HAVE_QT_AUDIO = True
except Exception:
    _HAVE_QT_AUDIO = False


# Fixed output format - the resampler downmixes/converts any source to this.
_RATE = 48000
_CHANNELS = 2
_BYTES_PER_SAMPLE = 2          # s16
_FRAME_BYTES = _CHANNELS * _BYTES_PER_SAMPLE

# Keep the decode roughly half a second ahead - enough to avoid underruns,
# small enough that a seek doesn't leave much stale audio to discard.
_MAX_BUFFER_BYTES = _RATE * _FRAME_BYTES // 2


class _PcmBuffer:
    """A thread-safe FIFO of raw PCM bytes."""

    def __init__(self):
        self._buf = bytearray()
        self._lock = threading.Lock()

    def write(self, data):
        with self._lock:
            self._buf.extend(data)

    def read(self, n):
        with self._lock:
            if n >= len(self._buf):
                chunk = bytes(self._buf)
                self._buf.clear()
            else:
                chunk = bytes(self._buf[:n])
                del self._buf[:n]
            return chunk

    def size(self):
        with self._lock:
            return len(self._buf)

    def clear(self):
        with self._lock:
            self._buf.clear()


class _DecodeThread(threading.Thread):
    """Decodes the audio stream from `start_seconds`, resampling to the fixed
    output format, and feeds the PCM buffer (throttled to stay ~just ahead)."""

    def __init__(self, buffer, path, start_seconds):
        super().__init__(daemon=True)
        self._buffer = buffer
        self._path = path
        self._start = max(0.0, float(start_seconds))
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            container = av.open(self._path)
        except Exception:
            return

        try:
            astream = next(
                (s for s in container.streams if s.type == "audio"),
                None,
            )
            if astream is None:
                return

            try:
                resampler = AudioResampler(
                    format="s16",
                    layout="stereo",
                    rate=_RATE,
                )
            except Exception:
                return

            start_offset = astream.start_time or 0
            tb = float(astream.time_base) if astream.time_base else 0.0

            if self._start > 0 and astream.time_base:
                try:
                    container.seek(
                        start_offset + int(self._start / astream.time_base),
                        stream=astream,
                        backward=True,
                    )
                except Exception:
                    pass

            # Decode packet-by-packet so a single undecodable packet (common in
            # broadcast TS, and after a QSF remux) is skipped rather than
            # aborting the whole decode - the previous container.decode() loop
            # threw on the first bad packet, which silently killed the decode
            # thread and dropped audio for the rest of playback (sometimes for
            # many seconds).  This mirrors how the video fetcher demuxes.
            for packet in container.demux(astream):
                if self._stop_event.is_set():
                    break

                try:
                    frames = packet.decode()
                except Exception:
                    continue    # skip the bad packet, keep going

                for frame in frames:
                    # Seeking lands on a packet at or before the target; skip
                    # the few frames that precede where we actually want to
                    # start.  Compare in container-relative (0-based) seconds.
                    if frame.pts is not None and tb:
                        base0 = (frame.pts - start_offset) * tb
                        if base0 < self._start - 0.05:
                            continue

                    try:
                        chunks = resampler.resample(frame)
                    except Exception:
                        continue

                    if not isinstance(chunks, list):
                        chunks = [chunks]

                    for out in chunks:
                        if out is None:
                            continue
                        try:
                            self._buffer.write(out.to_ndarray().tobytes())
                        except Exception:
                            pass

                # Stay only just ahead - wait here if we've buffered enough.
                while (
                        not self._stop_event.is_set()
                        and self._buffer.size() > _MAX_BUFFER_BYTES
                ):
                    time.sleep(0.02)
        except Exception:
            pass
        finally:
            try:
                container.close()
            except Exception:
                pass


if _HAVE_QT_AUDIO:

    class _AudioSource(QIODevice):
        """Pull-mode source: Qt's audio sink reads PCM from here on its own
        thread.  Pads with silence if the decoder is momentarily behind, so
        the sink never stalls."""

        def __init__(self, buffer):
            super().__init__()
            self._buffer = buffer

        def isSequential(self):
            return True

        def bytesAvailable(self):
            # Claim plenty so the sink keeps pulling; readData pads with
            # silence when the decoder hasn't caught up.  The sink still paces
            # consumption by the audio clock regardless of this value.
            return _RATE * _FRAME_BYTES + super().bytesAvailable()

        def readData(self, maxlen):
            n = int(maxlen)
            if n <= 0:
                return bytes()
            data = self._buffer.read(n)
            if len(data) < n:
                data = data + bytes(n - len(data))
            return data

        def writeData(self, data):
            return 0


class AudioController:
    """Plays the loaded source's audio during real-time playback."""

    def __init__(self, volume=0.8, latency_ms=0):
        self.available = _HAVE_AV and _HAVE_QT_AUDIO

        self._source = None
        self._volume = max(0.0, min(1.0, float(volume)))
        self._latency = float(latency_ms) / 1000.0

        self._sink = None
        self._src = None
        self._buffer = _PcmBuffer()
        self._decoder = None
        self._play_start = 0.0
        self._active = False

        # Difference between the video and audio streams' start times
        # (video_start - audio_start), in seconds.  Broadcast .ts files mux
        # the audio starting a little before the video by a per-recording
        # amount; we add this to every audio seek so the sound lines up with
        # the picture without any manual per-file tuning.  Set in set_source.
        self._av_start_delay = 0.0

        if self.available:
            self._fmt = QAudioFormat()
            self._fmt.setSampleRate(_RATE)
            self._fmt.setChannelCount(_CHANNELS)
            self._fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            try:
                device = QMediaDevices.defaultAudioOutput()
                self._sink = QAudioSink(device, self._fmt)
                self._sink.setVolume(self._volume)
                # A small internal buffer (~quarter second) for smooth pacing.
                try:
                    self._sink.setBufferSize(_RATE * _FRAME_BYTES // 4)
                except Exception:
                    pass

                self._src = _AudioSource(self._buffer)
                self._src.open(QIODevice.OpenModeFlag.ReadOnly)
            except Exception:
                self.available = False
                self._sink = None
                self._src = None

    # -- source ----------------------------------------------------------

    def set_source(self, path):
        """Point at a file (playback starts later, on play_from)."""
        self.clear()
        self._source = path or None
        self._av_start_delay = 0.0

        if self._source and _HAVE_AV:
            try:
                container = av.open(self._source)
                try:
                    v = next(
                        (s for s in container.streams if s.type == "video"),
                        None,
                    )
                    a = next(
                        (s for s in container.streams if s.type == "audio"),
                        None,
                    )
                    if v is not None and a is not None:
                        v_start = (v.start_time or 0) * float(
                            v.time_base or 0
                        )
                        a_start = (a.start_time or 0) * float(
                            a.time_base or 0
                        )
                        self._av_start_delay = v_start - a_start
                finally:
                    container.close()
            except Exception:
                self._av_start_delay = 0.0

    def clear(self):
        self._halt()
        self._source = None

    # -- transport -------------------------------------------------------

    def play_from(self, seconds):
        """Seek to `seconds` and start playing audio from there."""
        if not self.available or not self._source:
            return

        self._halt()

        self._play_start = max(0.0, float(seconds))

        # Seek the audio relative to its OWN stream start: the requested
        # position is on the video timeline, so shift it by the video/audio
        # start-time difference to land on the matching sound.
        decode_from = max(0.0, float(seconds) + self._av_start_delay)

        self._decoder = _DecodeThread(self._buffer, self._source, decode_from)
        self._decoder.start()

        try:
            if not self._src.isOpen():
                self._src.open(QIODevice.OpenModeFlag.ReadOnly)
            self._sink.start(self._src)        # pull mode
            self._active = True
        except Exception:
            pass

    def position_seconds(self):
        """Approximate current playback position (container-relative seconds),
        or None if audio isn't actively playing.  Used to keep the video
        gently locked to the audio clock."""
        if not self.available or self._sink is None or not self._active:
            return None
        try:
            processed = self._sink.processedUSecs() / 1_000_000.0
            return self._play_start + processed - self._latency
        except Exception:
            return None

    def pause(self):
        # Resume is always a fresh play_from with an explicit position, so a
        # full stop here keeps the state simple.
        self._halt()

    def stop(self):
        self._halt()

    # -- volume ----------------------------------------------------------

    def set_volume(self, vol01):
        self._volume = max(0.0, min(1.0, float(vol01)))
        if self._sink is not None:
            try:
                self._sink.setVolume(self._volume)
            except Exception:
                pass

    def volume(self):
        return self._volume

    # -- internals -------------------------------------------------------

    def _halt(self):
        self._active = False

        if self._decoder is not None:
            self._decoder.stop()
            self._decoder.join(timeout=0.5)
            self._decoder = None

        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass

        self._buffer.clear()
