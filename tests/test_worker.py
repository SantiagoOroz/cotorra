"""The worker's two promises: captions come out in order, and one bad call is survivable."""

from __future__ import annotations

import asyncio

import pytest

from cotorra.audio.segmenter import Segment
from cotorra.config import Settings
from cotorra.engines.base import Engine, TranscribeRequest, TranscribeResult
from cotorra.models import SessionSpec
from cotorra.worker import CaptionWorker


class ScriptedEngine(Engine):
    """Returns canned results, optionally slowly or by raising."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, script: list) -> None:
        self.script = script
        self.calls = 0
        self.seen: list[TranscribeRequest] = []

    async def transcribe(self, req: TranscribeRequest) -> TranscribeResult:
        self.seen.append(req)
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        delay, payload = item
        await asyncio.sleep(delay)
        if isinstance(payload, Exception):
            raise payload
        return TranscribeResult(
            source_lang="en", texts={"en": payload, "es": payload}, model=self.model
        )


@pytest.fixture
def spec() -> SessionSpec:
    return SessionSpec(id="t", title="T", source="mic", source_lang="en", targets=["es"])


def make(spec, engine, tmp_path, **kw) -> CaptionWorker:
    settings = Settings(data_dir=tmp_path, manifest=tmp_path / "none.yaml")
    return CaptionWorker(spec, "ws://unused", "tok", settings, engine=engine, **kw)


def seg(index: int, kind: str = "final") -> Segment:
    return Segment(pcm=b"\x00" * 3200, t_start=index * 2.0, t_end=index * 2.0 + 1.5,
                   kind=kind, index=index)


async def drain(worker: CaptionWorker, expected: int, timeout: float = 5.0) -> list[dict]:
    """Run the publisher until ``expected`` messages are queued for the gateway."""
    task = asyncio.create_task(worker._publisher())
    out: list[dict] = []

    async def collect() -> None:
        while len(out) < expected:
            out.append(await worker._outbox.get())
            worker._outbox.task_done()

    try:
        await asyncio.wait_for(collect(), timeout)  # asyncio.timeout() needs 3.11
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return out


async def test_captions_are_published_in_order_even_when_calls_finish_out_of_order(spec, tmp_path):
    """The first call is the slowest. Subtitles must still arrive 1, 2, 3."""
    engine = ScriptedEngine([(0.30, "first"), (0.01, "second"), (0.01, "third")])
    w = make(spec, engine, tmp_path, concurrency=3)

    for i in range(3):
        await w._submit(seg(i))
    messages = await drain(w, 3)

    assert [m["caption"]["texts"]["en"] for m in messages] == ["first", "second", "third"]
    assert [m["caption"]["seq"] for m in messages] == [0, 1, 2]


async def test_a_failed_call_does_not_stall_the_ones_behind_it(spec, tmp_path):
    engine = ScriptedEngine([(0.01, RuntimeError("503 from the model")), (0.01, "kept going")])
    w = make(spec, engine, tmp_path)

    await w._submit(seg(0))
    await w._submit(seg(1))
    messages = await drain(w, 1)

    assert messages[0]["caption"]["texts"]["en"] == "kept going"
    assert w.stats.errors == 1
    assert w.last_error and "503" in w.last_error


async def test_three_consecutive_failures_raise_the_alarm(spec, tmp_path):
    engine = ScriptedEngine([(0.0, RuntimeError("down"))])
    w = make(spec, engine, tmp_path)
    for i in range(3):
        await w._submit(seg(i))

    task = asyncio.create_task(w._publisher())
    await asyncio.sleep(0.25)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert w.state == "error"          # the ops panel goes red
    assert w.stats.errors == 3


async def test_one_failure_only_degrades(spec, tmp_path):
    engine = ScriptedEngine([(0.0, RuntimeError("blip")), (0.0, "recovered")])
    w = make(spec, engine, tmp_path)
    await w._submit(seg(0))

    task = asyncio.create_task(w._publisher())
    await asyncio.sleep(0.15)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert w.state == "degraded"


async def test_a_partial_is_superseded_by_its_final(spec, tmp_path):
    """The interim guess shows first, then the same seq is overwritten.

    Ordered publication is what makes this safe: the partial can never arrive
    after the final it belongs to, so the UI only ever replaces forwards.
    """
    engine = ScriptedEngine([(0.30, "the full"), (0.01, "the full sentence")])
    w = make(spec, engine, tmp_path, concurrency=3)

    await w._submit(seg(0, kind="partial"))
    await w._submit(seg(0, kind="final"))
    messages = await drain(w, 2)

    assert [m["caption"]["kind"] for m in messages] == ["partial", "final"]
    assert {m["caption"]["seq"] for m in messages} == {0}


async def test_partials_share_the_sequence_number_of_their_final(spec, tmp_path):
    engine = ScriptedEngine([(0.0, "growing"), (0.0, "grown"), (0.0, "next one")])
    w = make(spec, engine, tmp_path, concurrency=1)

    await w._submit(seg(0, kind="partial"))
    await w._submit(seg(0, kind="final"))
    await w._submit(seg(1, kind="final"))
    messages = await drain(w, 3)

    assert [(m["caption"]["kind"], m["caption"]["seq"]) for m in messages] == [
        ("partial", 0), ("final", 0), ("final", 1)
    ]


async def test_partials_are_dropped_when_the_pipeline_is_saturated(spec, tmp_path):
    """Under load, finals matter and interim guesses do not."""
    engine = ScriptedEngine([(0.4, "slow")])
    w = make(spec, engine, tmp_path, concurrency=1)

    await w._submit(seg(0))                    # takes the only slot
    await w._submit(seg(1, kind="partial"))    # must be discarded, not queued
    assert w._pending.qsize() == 1

    await w._submit(seg(2, kind="final"))      # a final always waits its turn
    assert w._pending.qsize() == 2


async def test_previous_captions_are_fed_back_as_context(spec, tmp_path):
    """Chunked ASR needs continuity or it loses pronouns across boundaries."""
    engine = ScriptedEngine([(0.0, "We deploy with Kubernetes."), (0.0, "It scales well.")])
    w = make(spec, engine, tmp_path, concurrency=1)

    await w._submit(seg(0))
    await drain(w, 1)
    await w._submit(seg(1))
    await drain(w, 1)

    assert "We deploy with Kubernetes." in engine.seen[1].context
    assert engine.seen[0].context == ""


async def test_the_glossary_rides_along_with_every_call(spec, tmp_path):
    engine = ScriptedEngine([(0.0, "hi")])
    w = make(spec, engine, tmp_path)
    w.glossary = ["Nerdearla", "Kubernetes"]

    await w._submit(seg(0))
    await drain(w, 1)

    assert engine.seen[0].glossary == ["Nerdearla", "Kubernetes"]


async def test_empty_results_are_not_published(spec, tmp_path):
    """Silence must not produce a blank subtitle line."""
    engine = ScriptedEngine([(0.0, ""), (0.0, "real words")])
    w = make(spec, engine, tmp_path)

    await w._submit(seg(0))
    await w._submit(seg(1))
    messages = await drain(w, 1)

    assert messages[0]["caption"]["texts"]["en"] == "real words"
    assert messages[0]["caption"]["seq"] == 0  # the empty one did not burn a seq


async def test_metrics_track_what_the_ops_panel_shows(spec, tmp_path):
    engine = ScriptedEngine([(0.0, "one two three")])
    w = make(spec, engine, tmp_path)
    await w._submit(seg(0))
    await drain(w, 1)

    snap = w.stats.snapshot(0.4)
    assert snap.captions == 1
    assert snap.words == 3
    assert snap.engine_calls == 1
    assert snap.audio_level == 0.4
    assert snap.latency_p50_ms >= 0
