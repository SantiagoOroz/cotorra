"""The engine contract.

An engine takes a chunk of speech and returns the transcript **and every
translation in one shot**. That single decision is why Cotorra is fast and
cheap: the classic pipeline (ASR call -> translation call) pays two round
trips and two prompts per utterance, and the translator never hears the audio.
A multimodal model can use prosody and the acoustic context to disambiguate
while it translates.

Adding a new engine is one file and one entry in ``ENGINES``.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass
class TranscribeRequest:
    pcm: bytes
    sample_rate: int
    source_lang: str  # "auto" or a BCP-47 code
    targets: list[str]  # languages the audience can pick
    context: str = ""  # the last few captions, for continuity across chunks
    glossary: list[str] = field(default_factory=list)
    partial: bool = False  # mid-utterance: favour speed over polish

    @property
    def duration_s(self) -> float:
        return len(self.pcm) / 2 / self.sample_rate


@dataclass
class TranscribeResult:
    source_lang: str
    texts: dict[str, str]
    model: str = ""
    audio_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def is_empty(self) -> bool:
        return not any(t.strip() for t in self.texts.values())


class Engine(abc.ABC):
    """Stateless per call: workers own the conversation context, not engines."""

    name: str = "base"
    model: str = ""
    demo: bool = False

    @abc.abstractmethod
    async def transcribe(self, req: TranscribeRequest) -> TranscribeResult: ...

    async def aclose(self) -> None:  # pragma: no cover - most engines need nothing
        return None

    def estimate_cost_usd(self, result: TranscribeResult) -> float:
        return 0.0


SYSTEM_PROMPT = """You are the live captioning engine for a software \
engineering conference. You receive a few seconds of audio from a stage \
microphone and you produce broadcast-quality subtitles.

Rules:
1. Transcribe ONLY what is spoken in the audio clip. Never invent, complete or \
continue a sentence that is not there. If the clip contains no intelligible \
speech, return empty strings.
2. Keep the speaker's own words. Do not summarise, do not clean up meaning, do \
not add commentary.
3. Write natural punctuation and capitalisation. No speaker labels, no \
timestamps, no "[music]" style annotations.
4. Technical vocabulary (Kubernetes, PostgreSQL, gRPC, CI/CD, Rust, LLM...), \
product names and people's names are spelled correctly and are NOT translated.
5. Translations are subtitles, not literature: same register, natural in the \
target language, similar length, and they keep every technical term intact.
6. The preceding captions are given only so you can resolve pronouns and \
sentence continuations. Never repeat text that already appeared in them.
"""
