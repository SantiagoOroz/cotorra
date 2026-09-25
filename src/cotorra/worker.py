"""The caption worker: one OS process per stage.

    audio source -> segmenter -> engine (bounded, ordered) -> gateway websocket

Why a separate process and not an asyncio task inside the gateway:

* **Blast radius.** A stage whose ffmpeg dies, whose model call hangs or whose
  RAM blows up takes down that stage only. Stage 5 crashing must never darken
  the captions on stage 1.
* **Scheduling.** ASR is CPU/GPU heavy. Processes spread across cores; tasks in
  one event loop do not.
* **Placement.** The same worker binary runs as a subprocess on a laptop, a
  container in Compose, or a Pod in Kubernetes. Only the supervisor changes.

Ordering guarantee
------------------
Model calls overlap (up to ``--concurrency``) but results are published in
submission order, because subtitles that arrive shuffled are worse than
subtitles that arrive slightly later.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field

import websockets

from .audio import AudioSourceError, FFmpegSource, Segment, Segmenter, SegmenterConfig
from .config import Settings, get_settings
from .engines import Engine, TranscribeRequest, build_engine
from .glossary import load_glossary
from .models import Caption, SessionMetrics, SessionSpec, SessionState

log = logging.getLogger("cotorra.worker")

CONTEXT_CAPTIONS = 3
CONTEXT_CHARS = 420
HEARTBEAT_S = 2.0


@dataclass
class _Pending:
    segment: Segment
    task: asyncio.Task
    submitted_at: float


@dataclass
class WorkerStats:
    captions: int = 0
    words: int = 0
    errors: int = 0
    engine_calls: int = 0
    audio_seconds: float = 0.0
    cost_usd: float = 0.0
    latencies: list[int] = field(default_factory=list)

    def record_latency(self, ms: int) -> None:
        self.latencies.append(ms)
        del self.latencies[:-400]  # rolling window, bounded memory

    def snapshot(self, level: float) -> SessionMetrics:
        ordered = sorted(self.latencies)
        return SessionMetrics(
            captions=self.captions,
            words=self.words,
            errors=self.errors,
            audio_seconds=round(self.audio_seconds, 1),
            latency_p50_ms=_pct(ordered, 0.50),
            latency_p95_ms=_pct(ordered, 0.95),
            last_latency_ms=self.latencies[-1] if self.latencies else 0,
            audio_level=round(level, 4),
            cost_usd=round(self.cost_usd, 6),
            engine_calls=self.engine_calls,
        )


def _pct(ordered: list[int], q: float) -> int:
    if not ordered:
        return 0
    idx = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return ordered[idx]


class CaptionWorker:
    def __init__(
        self,
        spec: SessionSpec,
        gateway_url: str,
        token: str,
        settings: Settings | None = None,
        concurrency: int = 3,
        engine: Engine | None = None,
        seq_base: int = 0,
        t_base: float = 0.0,
    ) -> None:
        self.spec = spec
        self.gateway_url = gateway_url.rstrip("/")
        self.token = token
        self.settings = settings or get_settings()
        self.concurrency = concurrency
        self.engine = engine
        self.stats = WorkerStats()
        self.state: SessionState = "starting"
        self.last_error: str | None = None
        self.glossary: list[str] = []
        # Handed down by the supervisor so a restarted worker continues the
        # session instead of starting over at 0 (see TranscriptStore.tail).
        self._seq = seq_base
        self.t_base = t_base
        self._recent: list[str] = []
        self._pending: asyncio.Queue[_Pending] = asyncio.Queue()
        self._sem = asyncio.Semaphore(concurrency)
        self._outbox: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
        # Two separate signals. ``_stop`` means "stop pulling audio"; the
        # sender must stay alive past that point so the last captions of the
        # talk still reach the audience. ``_closing`` retires the sender, and
        # is only set once the outbox has drained.
        self._stop = asyncio.Event()
        self._closing = asyncio.Event()
        self._segmenter = Segmenter(
            SegmenterConfig(
                sample_rate=self.settings.sample_rate,
                min_segment_ms=self.settings.min_segment_ms,
                max_segment_ms=self.settings.max_segment_ms,
                silence_ms=self.settings.silence_ms,
                preroll_ms=self.settings.preroll_ms,
                partials=self.settings.partials,
                partial_interval_ms=self.settings.partial_interval_ms,
            )
        )
        self._consecutive_errors = 0

    # -- lifecycle ----------------------------------------------------------

    async def run(self) -> int:
        self.glossary = load_glossary(self.spec.glossary, self.settings)
        if self.glossary:
            log.info("Glossary '%s': %d terms", self.spec.glossary, len(self.glossary))
        if self.engine is None:
            self.engine = build_engine(self.spec.engine, self.settings)
        log.info(
            "Session %s | engine=%s model=%s | source=%s",
            self.spec.id,
            self.engine.name,
            self.engine.model,
            self.spec.source,
        )

        tasks = [
            asyncio.create_task(self._sender(), name="sender"),
            asyncio.create_task(self._publisher(), name="publisher"),
            asyncio.create_task(self._heartbeat(), name="heartbeat"),
        ]
        rc = 0
        try:
            await self._pump_audio()
            self.state = "finished"
        except AudioSourceError as exc:
            self.state = "error"
            self.last_error = str(exc)
            log.error("Audio source failed: %s", exc)
            rc = 2
        except asyncio.CancelledError:
            self.state = "stopped"
            raise
        except Exception as exc:  # pragma: no cover - last-resort guard
            self.state = "error"
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("Worker crashed")
            rc = 1
        finally:
            await self._drain(tasks)
        return rc

    async def _drain(self, tasks: list[asyncio.Task]) -> None:
        # Let in-flight model calls land so the last words of a talk are not lost.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._pending.join(), timeout=15)
        await self._emit(
            {"type": "bye", "session_id": self.spec.id, "reason": self.last_error or "finished",
             "state": self.state}
        )
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._outbox.join(), timeout=5)
        self._stop.set()
        self._closing.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.engine:
            await self.engine.aclose()

    def stop(self) -> None:
        """Stop ingesting audio and wind down. Safe to call from a signal handler."""
        self._stop.set()

    # -- stage 1: audio -> segments ----------------------------------------

    async def _pump_audio(self) -> None:
        source = FFmpegSource(
            self.spec.source, self.settings.sample_rate, loop_file=self.spec.loop
        )
        try:
            async for chunk in source.stream():
                if self._stop.is_set():
                    break
                self.stats.audio_seconds += len(chunk) / 2 / self.settings.sample_rate
                for segment in self._segmenter.feed(chunk):
                    await self._submit(segment)
                if self.state == "starting":
                    self.state = "running"
        finally:
            await source.aclose()
        tail = self._segmenter.flush()
        if tail:
            await self._submit(tail)

    # -- stage 2: segments -> engine (bounded, ordered) --------------------

    async def _submit(self, segment: Segment) -> None:
        # Partials are disposable: if the pipeline is saturated, drop them
        # rather than delaying the finals behind them.
        if segment.kind == "partial" and self._sem.locked():
            return
        await self._sem.acquire()
        task = asyncio.create_task(self._transcribe(segment))
        await self._pending.put(_Pending(segment, task, time.time()))

    async def _transcribe(self, segment: Segment):
        try:
            assert self.engine is not None
            req = TranscribeRequest(
                pcm=segment.pcm,
                sample_rate=self.settings.sample_rate,
                source_lang=self.spec.source_lang,
                targets=self.spec.targets,
                context="\n".join(self._recent[-CONTEXT_CAPTIONS:])[-CONTEXT_CHARS:],
                glossary=self.glossary,
                partial=segment.kind == "partial",
            )
            return await self.engine.transcribe(req)
        finally:
            self._sem.release()

    # -- stage 3: ordered publication --------------------------------------

    async def _publisher(self) -> None:
        while True:
            item = await self._pending.get()
            try:
                result = await item.task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._on_engine_error(exc)
                continue
            finally:
                self._pending.task_done()

            self._consecutive_errors = 0
            self.stats.engine_calls += 1
            assert self.engine is not None
            self.stats.cost_usd += self.engine.estimate_cost_usd(result)

            if result.is_empty:
                continue
            seg = item.segment
            latency_ms = int((time.time() - item.submitted_at) * 1000)
            caption = Caption(
                session_id=self.spec.id,
                seq=self._next_seq(seg),
                kind=seg.kind,
                t_start=round(seg.t_start + self.t_base, 2),
                t_end=round(seg.t_end + self.t_base, 2),
                source_lang=result.source_lang,
                texts=result.texts,
                latency_ms=latency_ms,
                engine=self.engine.name,
                model=result.model or self.engine.model,
                demo=self.engine.demo,
            )
            if seg.kind == "final":
                self._remember(caption)
                self.stats.captions += 1
                self.stats.words += len(caption.text_for(caption.source_lang).split())
            self.stats.record_latency(latency_ms)
            if self.state in ("running", "degraded", "starting"):
                self.state = "running"
            await self._emit({"type": "caption", "caption": caption.model_dump()})

    def _next_seq(self, seg: Segment) -> int:
        # Partials share the seq of the final they will become, so the UI can
        # replace in place instead of appending a duplicate line.
        if seg.kind == "partial":
            return self._seq
        seq = self._seq
        self._seq += 1
        return seq

    def _remember(self, caption: Caption) -> None:
        text = caption.text_for(caption.source_lang)
        if text:
            self._recent.append(text)
            del self._recent[:-CONTEXT_CAPTIONS]

    def _on_engine_error(self, exc: Exception) -> None:
        self.stats.errors += 1
        self._consecutive_errors += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:300]
        log.warning("Engine call failed (%d in a row): %s", self._consecutive_errors, exc)
        # One bad call is a blip; three in a row is an outage the ops desk
        # should see on the dashboard before the audience notices.
        if self._consecutive_errors >= 3:
            self.state = "error"
        elif self._consecutive_errors >= 1:
            self.state = "degraded"

    # -- stage 4: the wire to the gateway ----------------------------------

    async def _emit(self, message: dict) -> None:
        try:
            self._outbox.put_nowait(message)
        except asyncio.QueueFull:
            log.warning("Outbox full, dropping %s", message.get("type"))

    async def _heartbeat(self) -> None:
        while not self._closing.is_set():
            await asyncio.sleep(HEARTBEAT_S)
            # No audio energy for a while on a running session = dead feed.
            # This is the check that catches "the cable came out" during an event.
            await self._emit(
                {
                    "type": "heartbeat",
                    "session_id": self.spec.id,
                    "state": self.state,
                    "metrics": self.stats.snapshot(self._segmenter.level).model_dump(),
                    "last_error": self.last_error,
                }
            )

    async def _listen(self, ws) -> None:
        """Commands from the gateway.

        A cooperative shutdown over the socket we already have means an
        operator's Stop button behaves identically on Windows, on Linux and in
        a container — no signals, no process-tree games — and the worker gets
        to flush its last captions before exiting.
        """
        async for raw in ws:
            try:
                command = json.loads(raw).get("type")
            except (ValueError, AttributeError):
                continue
            if command == "shutdown":
                log.info("Shutdown requested by the gateway")
                self.state = "stopped"
                self.stop()
                return

    async def _sender(self) -> None:
        url = (
            f"{self.gateway_url}/ws/ingest/{self.spec.id}"
            f"?token={self.token}&engine={self.spec.engine}"
        )
        backoff = 0.5
        while not self._closing.is_set():
            listener: asyncio.Task | None = None
            try:
                async with websockets.connect(url, max_queue=None, ping_interval=20) as ws:
                    log.info("Connected to gateway %s", self.gateway_url)
                    backoff = 0.5
                    listener = asyncio.create_task(self._listen(ws))
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "session_id": self.spec.id,
                                "engine": self.spec.engine,
                                "model": self.engine.model if self.engine else "",
                                "pid": os.getpid(),
                            }
                        )
                    )
                    while not self._closing.is_set():
                        message = await self._outbox.get()
                        try:
                            await ws.send(json.dumps(message, ensure_ascii=False))
                        finally:
                            self._outbox.task_done()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The gateway restarting must not kill a running stage: keep
                # captioning, keep buffering, reconnect.
                log.warning("Gateway link down (%s); retrying in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)
            finally:
                if listener:
                    listener.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await listener


async def run_worker(
    spec: SessionSpec, gateway_url: str, token: str, settings: Settings | None = None
) -> int:
    worker = CaptionWorker(spec, gateway_url, token, settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(sig, worker.stop)
    return await worker.run()
