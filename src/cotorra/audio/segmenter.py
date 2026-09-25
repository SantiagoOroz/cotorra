"""Utterance segmentation: turn a raw PCM stream into speech chunks.

This is the single biggest lever on caption latency, so it is deliberately a
pure, synchronous, dependency-free state machine that can be unit tested
without ffmpeg or a model.

Strategy
--------
* 20 ms frames of 16 kHz mono signed 16-bit PCM.
* Energy VAD with an adaptive noise floor (conference rooms are not quiet, and
  a stage feed has constant room tone).
* An utterance is flushed when the speaker pauses (``silence_ms``) or when it
  grows past ``max_segment_ms`` — nobody should wait 20 s for a caption because
  the speaker did not breathe.
* A pre-roll buffer keeps the few hundred milliseconds *before* speech was
  detected, so word onsets are never clipped.
* While an utterance is still open we periodically emit a ``partial`` so the
  audience sees words appear mid-sentence instead of in one late block.
"""

from __future__ import annotations

import array
import math
from dataclasses import dataclass, field

FRAME_MS = 20
BYTES_PER_SAMPLE = 2


@dataclass
class Segment:
    """A chunk of speech ready to be transcribed."""

    pcm: bytes
    t_start: float
    t_end: float
    kind: str  # "partial" | "final"
    index: int

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start


@dataclass
class SegmenterConfig:
    sample_rate: int = 16000
    min_segment_ms: int = 900
    max_segment_ms: int = 7000
    silence_ms: int = 420
    preroll_ms: int = 300
    partials: bool = True
    partial_interval_ms: int = 1800
    # VAD tuning
    speech_frames_to_open: int = 3
    noise_floor_alpha: float = 0.02
    speech_factor: float = 2.8
    abs_threshold: float = 0.006


@dataclass
class Segmenter:
    """Feed it bytes, get back segments. Stateful, not thread safe."""

    cfg: SegmenterConfig = field(default_factory=SegmenterConfig)

    def __post_init__(self) -> None:
        self.frame_bytes = int(self.cfg.sample_rate * FRAME_MS / 1000) * BYTES_PER_SAMPLE
        self._carry = b""
        self._samples_seen = 0
        self._active = False
        self._voiced_run = 0
        self._silent_run = 0
        self._noise_floor = 0.004
        self._buf = bytearray()
        self._buf_start_s = 0.0
        self._preroll = bytearray()
        self._preroll_max = int(
            self.cfg.sample_rate * self.cfg.preroll_ms / 1000
        ) * BYTES_PER_SAMPLE
        self._index = 0
        self._last_partial_s = 0.0
        self.level = 0.0  # last frame RMS, surfaced to the ops dashboard

    # -- public API ---------------------------------------------------------

    @property
    def clock(self) -> float:
        """Seconds of audio consumed so far."""
        return self._samples_seen / self.cfg.sample_rate

    def feed(self, chunk: bytes) -> list[Segment]:
        """Consume PCM, return zero or more segments to transcribe."""
        out: list[Segment] = []
        data = self._carry + chunk
        n_frames, rest = divmod(len(data), self.frame_bytes)
        self._carry = data[len(data) - rest :] if rest else b""

        for i in range(n_frames):
            frame = data[i * self.frame_bytes : (i + 1) * self.frame_bytes]
            out.extend(self._feed_frame(frame))
        return out

    def flush(self) -> Segment | None:
        """End of stream: emit whatever is buffered if it is long enough."""
        if self._buf and self._buffered_ms() >= self.cfg.min_segment_ms:
            return self._emit("final")
        self._reset_utterance()
        return None

    # -- internals ----------------------------------------------------------

    def _feed_frame(self, frame: bytes) -> list[Segment]:
        out: list[Segment] = []
        rms = _rms(frame)
        self.level = rms
        self._samples_seen += len(frame) // BYTES_PER_SAMPLE

        threshold = max(self._noise_floor * self.cfg.speech_factor, self.cfg.abs_threshold)
        is_speech = rms > threshold

        if not is_speech:
            # Only quiet frames move the noise floor, so a long monologue does
            # not slowly raise the bar until speech stops registering.
            self._noise_floor = (
                1 - self.cfg.noise_floor_alpha
            ) * self._noise_floor + self.cfg.noise_floor_alpha * rms

        if not self._active:
            self._preroll.extend(frame)
            if len(self._preroll) > self._preroll_max:
                del self._preroll[: len(self._preroll) - self._preroll_max]

            self._voiced_run = self._voiced_run + 1 if is_speech else 0
            if self._voiced_run >= self.cfg.speech_frames_to_open:
                self._active = True
                self._silent_run = 0
                self._buf = bytearray(self._preroll)
                preroll_s = len(self._preroll) / BYTES_PER_SAMPLE / self.cfg.sample_rate
                self._buf_start_s = max(0.0, self.clock - preroll_s)
                self._last_partial_s = self.clock
                self._preroll.clear()
            return out

        self._buf.extend(frame)
        self._silent_run = 0 if is_speech else self._silent_run + 1

        buffered_ms = self._buffered_ms()
        silence_reached = self._silent_run * FRAME_MS >= self.cfg.silence_ms

        if silence_reached and buffered_ms >= self.cfg.min_segment_ms:
            out.append(self._emit("final"))
        elif silence_reached:
            # Too short to be worth a model call (a cough, a mic bump).
            self._reset_utterance()
        elif buffered_ms >= self.cfg.max_segment_ms:
            out.append(self._emit("final"))
        elif (
            self.cfg.partials
            and (self.clock - self._last_partial_s) * 1000 >= self.cfg.partial_interval_ms
            and buffered_ms >= self.cfg.min_segment_ms
        ):
            self._last_partial_s = self.clock
            out.append(
                Segment(
                    pcm=bytes(self._buf),
                    t_start=self._buf_start_s,
                    t_end=self.clock,
                    kind="partial",
                    index=self._index,
                )
            )
        return out

    def _buffered_ms(self) -> float:
        return len(self._buf) / BYTES_PER_SAMPLE / self.cfg.sample_rate * 1000

    def _emit(self, kind: str) -> Segment:
        seg = Segment(
            pcm=bytes(self._buf),
            t_start=self._buf_start_s,
            t_end=self.clock,
            kind=kind,
            index=self._index,
        )
        self._index += 1
        self._reset_utterance()
        return seg

    def _reset_utterance(self) -> None:
        self._active = False
        self._voiced_run = 0
        self._silent_run = 0
        self._buf = bytearray()
        self._preroll.clear()


def _rms(frame: bytes) -> float:
    """Root mean square of a signed 16-bit frame, normalised to 0..1."""
    if not frame:
        return 0.0
    samples = array.array("h")
    samples.frombytes(frame)
    total = 0
    for s in samples:
        total += s * s
    return math.sqrt(total / len(samples)) / 32768.0


def pcm_to_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Wrap raw PCM in a 44-byte WAV header (what the model APIs expect)."""
    byte_rate = sample_rate * channels * BYTES_PER_SAMPLE
    block_align = channels * BYTES_PER_SAMPLE
    header = b"RIFF"
    header += (36 + len(pcm)).to_bytes(4, "little")
    header += b"WAVEfmt "
    header += (16).to_bytes(4, "little")
    header += (1).to_bytes(2, "little")  # PCM
    header += channels.to_bytes(2, "little")
    header += sample_rate.to_bytes(4, "little")
    header += byte_rate.to_bytes(4, "little")
    header += block_align.to_bytes(2, "little")
    header += (16).to_bytes(2, "little")  # bits per sample
    header += b"data"
    header += len(pcm).to_bytes(4, "little")
    return header + pcm
