"""Core data model shared by the gateway, the workers and the web clients.

Everything that crosses a process boundary is defined here so that the wire
format has exactly one source of truth.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

SessionState = Literal[
    "idle",  # configured, no worker running
    "starting",  # worker spawned, no audio yet
    "running",  # captions flowing
    "degraded",  # captions flowing but the engine is erroring / audio is silent
    "error",  # worker alive but unable to produce captions
    "stopped",  # stopped on purpose
    "finished",  # source ended (file / stream EOF)
]

CaptionKind = Literal["partial", "final"]


class Caption(BaseModel):
    """One unit of subtitle, in every language at once.

    A ``partial`` is a best-effort guess for an utterance that is still being
    spoken; it is superseded by the ``final`` carrying the same ``seq``.
    """

    session_id: str
    seq: int
    kind: CaptionKind = "final"
    t_start: float = Field(description="Seconds since the start of the session")
    t_end: float
    source_lang: str = "auto"
    texts: dict[str, str] = Field(
        default_factory=dict,
        description="Language code -> text. Always includes the source language.",
    )
    latency_ms: int = 0
    engine: str = ""
    model: str = ""
    demo: bool = Field(default=False, description="True when produced by the mock engine")
    created_at: float = Field(default_factory=time.time)

    def text_for(self, lang: str) -> str:
        """Best available text for ``lang``, falling back to the source language."""
        if lang in self.texts:
            return self.texts[lang]
        if self.source_lang in self.texts:
            return self.texts[self.source_lang]
        return next(iter(self.texts.values()), "")


class SessionSpec(BaseModel):
    """The operator-facing description of one stage / talk."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    title: str = "Untitled session"
    stage: str = ""
    source: str = Field(
        description=(
            "ffmpeg input: file path, HLS/RTMP/SRT url, YouTube url, "
            "'mic' or 'device:<name>'"
        )
    )
    source_lang: str = "auto"
    targets: list[str] = Field(default_factory=lambda: ["es"])
    engine: str = "gemini"
    glossary: str | None = None
    autostart: bool = False
    loop: bool = Field(default=False, description="Restart file sources on EOF (demo mode)")

    def languages(self) -> list[str]:
        """All languages a viewer may pick, source first when it is known."""
        langs = list(self.targets)
        if self.source_lang != "auto" and self.source_lang not in langs:
            langs.insert(0, self.source_lang)
        return langs


class SessionMetrics(BaseModel):
    captions: int = 0
    words: int = 0
    errors: int = 0
    audio_seconds: float = 0.0
    latency_p50_ms: int = 0
    latency_p95_ms: int = 0
    last_latency_ms: int = 0
    audio_level: float = Field(default=0.0, description="0..1 RMS of the last second of audio")
    cost_usd: float = 0.0
    engine_calls: int = 0


class SessionStatus(BaseModel):
    spec: SessionSpec
    state: SessionState = "idle"
    started_at: float | None = None
    stopped_at: float | None = None
    last_caption_at: float | None = None
    last_heartbeat_at: float | None = None
    last_error: str | None = None
    viewers: int = 0
    metrics: SessionMetrics = Field(default_factory=SessionMetrics)

    @property
    def uptime_s(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.stopped_at or time.time()
        return max(0.0, end - self.started_at)


# --- worker -> gateway messages -------------------------------------------------


class WorkerHello(BaseModel):
    type: Literal["hello"] = "hello"
    session_id: str
    engine: str
    model: str
    pid: int


class WorkerHeartbeat(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    session_id: str
    state: SessionState
    metrics: SessionMetrics
    last_error: str | None = None


class WorkerCaption(BaseModel):
    type: Literal["caption"] = "caption"
    caption: Caption


class WorkerBye(BaseModel):
    type: Literal["bye"] = "bye"
    session_id: str
    reason: str = "finished"
    state: SessionState = "finished"
