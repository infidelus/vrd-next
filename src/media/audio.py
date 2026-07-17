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
import logging

log = logging.getLogger("vrd-next")

try:
    import av
    from av.audio.resampler import AudioResampler
    _HAVE_AV = True
except Exception:
    _HAVE_AV = False

try:
    from PySide6.QtMultimedia import (
        QAudio,
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

    def _pump(self, gen, resampler, start_offset, tb):
        """Decode/resample/write audio from `gen` until stopped or EOF.

        Returns (decoded, written, skipped, decode_errs, resample_errs).
        Decodes packet-by-packet so a single undecodable packet (common in
        broadcast TS, and after a QSF remux) is skipped rather than aborting the
        whole decode - aborting would silently kill playback for many seconds.
        """
        n_decoded = n_written = n_skipped = dec_err = res_err = 0
        for packet in gen:
            if self._stop_event.is_set():
                break

            try:
                frames = packet.decode()
            except Exception:
                dec_err += 1
                continue    # skip the bad packet, keep going

            for frame in frames:
                n_decoded += 1
                # Seeking lands on a packet at or before the target; skip the
                # few frames that precede where we actually want to start.
                # Compare in container-relative (0-based) seconds.
                if frame.pts is not None and tb:
                    base0 = (frame.pts - start_offset) * tb
                    if base0 < self._start - 0.05:
                        n_skipped += 1
                        continue

                try:
                    chunks = resampler.resample(frame)
                except Exception:
                    res_err += 1
                    continue

                if not isinstance(chunks, list):
                    chunks = [chunks]

                for out in chunks:
                    if out is None:
                        continue
                    try:
                        self._buffer.write(out.to_ndarray().tobytes())
                        n_written += 1
                    except Exception:
                        pass

            # Stay only just ahead - wait here if we've buffered enough.
            while (
                    not self._stop_event.is_set()
                    and self._buffer.size() > _MAX_BUFFER_BYTES
            ):
                time.sleep(0.02)

        return n_decoded, n_written, n_skipped, dec_err, res_err

    def run(self):
        import os
        name = os.path.basename(self._path)
        try:
            container = av.open(self._path)
        except Exception as exc:
            log.warning("audio: could not open %s (%s)", name, exc)
            return

        try:
            astream = next(
                (s for s in container.streams if s.type == "audio"),
                None,
            )
            if astream is None:
                log.warning("audio: %s has no audio stream", name)
                return

            try:
                resampler = AudioResampler(
                    format="s16",
                    layout="stereo",
                    rate=_RATE,
                )
            except Exception as exc:
                log.warning("audio: resampler init failed (%s)", exc)
                return

            codec = getattr(astream.codec_context, "name", "?")
            start_offset = astream.start_time or 0
            tb = float(astream.time_base) if astream.time_base else 0.0
            seek_mode = "none"
            totals = (0, 0, 0, 0, 0)

            if self._start <= 0:
                totals = self._pump(
                    container.demux(astream), resampler, start_offset, tb
                )
            else:
                # Seek to the requested time, then decode the audio stream from
                # there.  Two seek strategies, ordered by container:
                #
                #   * audio-stream seek - lands right on the target and seeks in
                #     the audio timeline, so it copes natively with an audio/
                #     video start-time offset (common in broadcast MPEG-TS) and
                #     keeps preview A/V perfectly in sync.
                #   * video-stream seek - uses the video keyframe index.  Needed
                #     for disc rips (Matroska), whose audio has no seek index -
                #     there an audio seek blocks for tens of seconds and lands
                #     nowhere.  Disc rips have no start-time offset, so leading
                #     with the video index introduces no drift.
                #
                # So: audio-first for MPEG-TS captures (tight sync), video-first
                # for everything else (robust).  A whole-container seek is the
                # last resort.  We keep the first strategy that produces sound.
                vstream = next(
                    (s for s in container.streams if s.type == "video"), None
                )
                fmt = (getattr(container.format, "name", "") or "").lower()
                ts_like = "mpegts" in fmt or "mpeg2video" in fmt

                audio_attempt = (
                    ("stream", astream,
                     start_offset + int(self._start / astream.time_base))
                    if astream.time_base else None
                )
                video_attempt = (
                    ("video", vstream,
                     (vstream.start_time or 0)
                     + int(self._start / vstream.time_base))
                    if (vstream is not None and vstream.time_base) else None
                )

                if ts_like:
                    ordered = [audio_attempt, video_attempt]
                else:
                    ordered = [video_attempt, audio_attempt]

                attempts = [a for a in ordered if a is not None]
                attempts.append(
                    ("container", None, int(self._start * 1_000_000))
                )
                for mode, strm, offset in attempts:
                    if self._stop_event.is_set():
                        break
                    try:
                        if strm is not None:
                            container.seek(offset, stream=strm, backward=True)
                        else:
                            container.seek(offset, backward=True)
                    except Exception as exc:
                        log.debug(
                            "audio: %s-seek to %.2fs failed (%s)",
                            mode, self._start, exc,
                        )
                        continue
                    totals = self._pump(
                        container.demux(astream), resampler, start_offset, tb
                    )
                    seek_mode = mode
                    # Keep the first strategy that produced sound (or stop early
                    # if a newer seek has already superseded this one).
                    if totals[1] > 0 or self._stop_event.is_set():
                        break

            n_decoded, n_written, n_skipped, dec_err, res_err = totals

            if n_written == 0 and (n_decoded or dec_err or n_skipped):
                log.warning(
                    "audio: no sound from %s [codec=%s start=%.2fs seek=%s "
                    "decoded=%d skipped=%d wrote=%d decode_err=%d resample_err=%d]",
                    name, codec, self._start, seek_mode,
                    n_decoded, n_skipped, n_written, dec_err, res_err,
                )
            else:
                log.debug(
                    "audio: %s start=%.2fs seek=%s decoded=%d wrote=%d skipped=%d",
                    name, self._start, seek_mode, n_decoded, n_written, n_skipped,
                )
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

            self._create_sink()

    def _create_sink(self):
        """(Re)create the audio sink against the current default output device.

        Done at startup and again lazily before playback if the sink is
        missing or in an error state - which happens when the audio device
        wasn't ready at launch (another app held it, or PulseAudio/PipeWire was
        mid-switch).  Previously such a sink stayed dead and silent for the
        whole session with no way back but restarting the app; rebuilding it on
        demand recovers automatically.  Returns True if a usable sink exists.
        """
        if not (_HAVE_AV and _HAVE_QT_AUDIO):
            log.warning("audio: unavailable (PyAV=%s, Qt multimedia=%s)",
                        _HAVE_AV, _HAVE_QT_AUDIO)
            return False
        # Tear down any existing (possibly dead) sink first.
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
        self._sink = None
        self._src = None
        try:
            device = QMediaDevices.defaultAudioOutput()
            if device is None or device.isNull():
                self.available = False
                log.warning(
                    "audio: no default output device found - sound is off. "
                    "If a device is connected, it may not have been ready at "
                    "startup; playback will retry when you press play.")
                return False
            self._sink = QAudioSink(device, self._fmt)
            self._sink.setVolume(self._volume)
            try:
                self._sink.setBufferSize(_RATE * _FRAME_BYTES // 4)
            except Exception:
                pass
            self._src = _AudioSource(self._buffer)
            self._src.open(QIODevice.OpenModeFlag.ReadOnly)
            self.available = True
            try:
                dev_name = device.description()
            except Exception:
                dev_name = "?"
            log.info("audio: output device ready (%s) — %d Hz, %d ch",
                     dev_name, _RATE, _CHANNELS)
            return True
        except Exception as exc:
            self.available = False
            self._sink = None
            self._src = None
            log.warning("audio: could not open output device (%s) - sound is "
                        "off; will retry on play", exc)
            return False

    def _sink_is_healthy(self):
        """Whether the current sink exists and isn't in an error state."""
        if self._sink is None:
            return False
        try:
            return self._sink.error() == QAudio.Error.NoError
        except Exception:
            return True    # can't tell - assume usable rather than thrash

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
                        # The broadcast audio/video offset is always small (well
                        # under a second to a couple of seconds).  Some files -
                        # notably Blu-ray MKVs that carry a large first-frame
                        # timecode - report a huge video start_time, which would
                        # otherwise push the audio seek far past the end of the
                        # file so nothing decodes and playback is silent.  Ignore
                        # any implausibly large value and play from the start.
                        if abs(self._av_start_delay) > 10.0:
                            self._av_start_delay = 0.0
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
        if not self._source or not (_HAVE_AV and _HAVE_QT_AUDIO):
            return

        # If the sink failed to come up at launch (device not ready) or has
        # since gone into an error state, rebuild it now - the device is very
        # likely available by the time the user actually presses play.  This is
        # what lets audio recover without restarting the whole app.
        if not self._sink_is_healthy():
            self._create_sink()
        if not self.available or self._sink is None or self._src is None:
            return

        self._halt()

        self._play_start = max(0.0, float(seconds))

        # Seek the audio relative to its OWN stream start: the requested
        # position is on the video timeline, so shift it by the video/audio
        # start-time difference to land on the matching sound.
        decode_from = max(0.0, float(seconds) + self._av_start_delay)
        log.debug(
            "audio: play_from seconds=%.2f av_start_delay=%.3f decode_from=%.2f",
            float(seconds), self._av_start_delay, decode_from,
        )

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
