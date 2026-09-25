"""Caption fan-out.

One producer (the worker for a stage) and N consumers (the audience, the OBS
overlay, the ops dashboard). Two implementations behind one interface:

``MemoryHub``
    the default. Everything in one gateway process. Handles a few thousand
    viewers on a small VM and needs no infrastructure at all.

``RedisHub``
    switched on by setting ``REDIS_URL``. Workers and viewers can then land on
    *different* gateway replicas behind a load balancer, which is how you go
    past one machine without changing a line of application code.

Backpressure policy: a viewer on hotel wifi must never slow down the stage.
Each subscriber has a bounded queue and the **oldest** caption is dropped when
it overflows, because in live captioning the newest line is the one that
matters.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Protocol

log = logging.getLogger(__name__)


class Subscription:
    def __init__(self, hub: MemoryHub, session_id: str, maxsize: int) -> None:
        self._hub = hub
        self.session_id = session_id
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def offer(self, event: dict) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
                self.dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(event)

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc) -> None:
        self._hub.unsubscribe(self)

    async def __aiter__(self) -> AsyncIterator[dict]:
        while True:
            yield await self.queue.get()


class Hub(Protocol):
    async def publish(self, session_id: str, event: dict) -> None: ...
    def subscribe(self, session_id: str) -> Subscription: ...
    def viewers(self, session_id: str) -> int: ...
    async def aclose(self) -> None: ...


class MemoryHub:
    def __init__(self, queue_size: int = 200) -> None:
        self.queue_size = queue_size
        self._subs: dict[str, set[Subscription]] = {}

    async def publish(self, session_id: str, event: dict) -> None:
        for sub in list(self._subs.get(session_id, ())):
            sub.offer(event)

    def subscribe(self, session_id: str) -> Subscription:
        sub = Subscription(self, session_id, self.queue_size)
        self._subs.setdefault(session_id, set()).add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.session_id)
        if subs:
            subs.discard(sub)
            if not subs:
                self._subs.pop(sub.session_id, None)

    def viewers(self, session_id: str) -> int:
        return len(self._subs.get(session_id, ()))

    def total_viewers(self) -> int:
        return sum(len(s) for s in self._subs.values())

    async def aclose(self) -> None:
        self._subs.clear()


class RedisHub(MemoryHub):
    """Local delivery + a Redis pub/sub bridge between gateway replicas."""

    CHANNEL = "cotorra:captions"

    def __init__(self, url: str, queue_size: int = 200) -> None:
        super().__init__(queue_size)
        try:
            import redis.asyncio as redis  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "REDIS_URL is set but the redis package is missing: pip install 'cotorra[redis]'"
            ) from exc
        self._redis = redis.from_url(url, decode_responses=True)
        self._pump: asyncio.Task | None = None

    async def start(self) -> None:
        self._pump = asyncio.create_task(self._listen(), name="redis-hub")

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self.CHANNEL)
        log.info("Redis hub listening on %s", self.CHANNEL)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                payload = json.loads(message["data"])
                await super().publish(payload["session_id"], payload["event"])
            except Exception:  # pragma: no cover - never let one bad frame stop the pump
                log.exception("Bad hub frame")

    async def publish(self, session_id: str, event: dict) -> None:
        await self._redis.publish(
            self.CHANNEL, json.dumps({"session_id": session_id, "event": event})
        )

    async def aclose(self) -> None:
        if self._pump:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
        await self._redis.aclose()
        await super().aclose()


async def build_hub(redis_url: str, queue_size: int) -> Hub:
    if redis_url:
        hub = RedisHub(redis_url, queue_size)
        await hub.start()
        return hub
    return MemoryHub(queue_size)
