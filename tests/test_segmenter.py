"""The segmenter decides the latency of the whole system, so it gets the most tests."""

from __future__ import annotations

import math
import struct

import pytest

from cotorra.audio.segmenter import FRAME_MS, Segmenter, SegmenterConfig, pcm_to_wav

SR = 16000


def tone(ms: int, amplitude: float = 0.35, freq: float = 180.0, sr: int = SR) -> bytes:
    n = int(sr * ms / 1000)
    return b"".join(
        struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / sr)))
        for i in range(n)
    )


def silence(ms: int, sr: int = SR) -> bytes:
    return b"\x00\x00" * int(sr * ms / 1000)


@pytest.fixture
def seg() -> Segmenter:
    return Segmenter(SegmenterConfig(partials=False))


def test_silence_alone_produces_nothing(seg):
    assert seg.feed(silence(3000)) == []
    assert seg.flush() is None


def test_speech_then_pause_emits_one_final(seg):
    out = seg.feed(silence(200) + tone(1600) + silence(900))
    finals = [s for s in out if s.kind == "final"]
    assert len(finals) == 1
    assert 1.4 < finals[0].duration < 2.4


def test_short_blip_is_discarded(seg):
    """A cough or a mic bump must not cost a model call."""
    out = seg.feed(silence(200) + tone(250) + silence(900))
    assert [s for s in out if s.kind == "final"] == []


def test_long_monologue_is_cut_at_max_segment():
    """Nobody waits 20 s for a caption because the speaker did not breathe."""
    seg = Segmenter(SegmenterConfig(partials=False, max_segment_ms=3000))
    out = seg.feed(tone(10_000))
    finals = [s for s in out if s.kind == "final"]
    assert len(finals) >= 3
    for s in finals:
        assert s.duration <= 3.2


def test_preroll_keeps_the_word_onset():
    """The buffer must start before speech was *detected*, or words get clipped."""
    seg = Segmenter(SegmenterConfig(partials=False, preroll_ms=300))
    out = seg.feed(silence(1000) + tone(1500) + silence(900))
    final = [s for s in out if s.kind == "final"][0]
    # Speech starts at t=1.0 s; with pre-roll the segment must open earlier.
    assert final.t_start < 1.0
    assert final.t_start > 0.55


def test_partials_appear_before_the_final():
    seg = Segmenter(
        SegmenterConfig(partials=True, partial_interval_ms=500, max_segment_ms=9000)
    )
    out = seg.feed(tone(4000) + silence(900))
    kinds = [s.kind for s in out]
    assert "partial" in kinds
    assert kinds[-1] == "final"
    # Every partial belongs to the final that follows it.
    assert len({s.index for s in out}) == 1


def test_partial_and_final_share_an_index_so_the_ui_can_replace_in_place():
    seg = Segmenter(SegmenterConfig(partials=True, partial_interval_ms=500))
    first = seg.feed(tone(3000) + silence(900))
    second = seg.feed(tone(3000) + silence(900))
    assert {s.index for s in first} == {0}
    assert {s.index for s in second} == {1}


def test_feed_is_agnostic_to_chunk_boundaries(seg):
    """ffmpeg hands us arbitrary buffer sizes; results must not depend on them."""
    audio = silence(200) + tone(1600) + silence(900)
    fresh = Segmenter(SegmenterConfig(partials=False))
    one_shot = [(s.kind, round(s.duration, 2)) for s in fresh.feed(audio)]

    dribbled = []
    s2 = Segmenter(SegmenterConfig(partials=False))
    for i in range(0, len(audio), 777):  # deliberately not a frame multiple
        dribbled += [(s.kind, round(s.duration, 2)) for s in s2.feed(audio[i : i + 777])]
    assert one_shot == dribbled


def test_noise_floor_adapts_to_a_noisy_room():
    """Constant room tone must not be transcribed as endless speech."""
    seg = Segmenter(SegmenterConfig(partials=False))
    seg.feed(tone(4000, amplitude=0.02))  # room tone only
    before = len(seg.feed(tone(500, amplitude=0.02)))
    loud = seg.feed(tone(1600, amplitude=0.5) + silence(900))
    assert before == 0
    assert [s for s in loud if s.kind == "final"]


def test_flush_emits_the_tail_of_a_talk(seg):
    """The last sentence before the stream ends must still be captioned."""
    seg.feed(silence(200) + tone(2000))
    tail = seg.flush()
    assert tail is not None and tail.kind == "final"


def test_level_tracks_the_signal(seg):
    seg.feed(silence(200))
    assert seg.level < 0.01
    seg.feed(tone(200, amplitude=0.5))
    assert seg.level > 0.2


def test_clock_counts_every_sample(seg):
    seg.feed(silence(1000))
    assert seg.clock == pytest.approx(1.0, abs=FRAME_MS / 1000)


def test_pcm_to_wav_header_is_valid():
    pcm = tone(100)
    wav = pcm_to_wav(pcm, SR)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert len(wav) == len(pcm) + 44
    assert struct.unpack("<I", wav[24:28])[0] == SR
    assert struct.unpack("<H", wav[22:24])[0] == 1  # mono
    assert struct.unpack("<I", wav[40:44])[0] == len(pcm)
