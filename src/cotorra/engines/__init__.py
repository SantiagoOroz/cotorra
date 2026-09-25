"""Engine registry.

To add an engine (Whisper.cpp, Vosk, Azure, the Gemini Live API...):

1. subclass :class:`~cotorra.engines.base.Engine` in a new module,
2. add one line to ``ENGINES`` below,
3. document its env vars in ``.env.example``.

Nothing else in the codebase needs to change. See CONTRIBUTING.md.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import Settings
from .base import Engine, TranscribeRequest, TranscribeResult

__all__ = ["Engine", "TranscribeRequest", "TranscribeResult", "ENGINES", "build_engine"]


def _gemini(s: Settings) -> Engine:
    from .gemini import GeminiEngine

    s.apply_google_credentials()
    return GeminiEngine(
        api_key=s.gemini_api_key,
        model=s.gemini_model,
        vertex=s.gemini_use_vertex,
        project=s.gcp_project,
        location=s.gcp_location,
        thinking_budget=s.gemini_thinking_budget,
        max_retries=s.gemini_max_retries,
        timeout_s=s.gemini_timeout_s,
        deadline_s=s.gemini_deadline_s,
        max_retry_wait_s=s.gemini_max_retry_wait_s,
        price_audio_in=s.price_audio_in,
        price_text_in=s.price_text_in,
        price_text_out=s.price_text_out,
    )


def _local(s: Settings) -> Engine:
    from .local import LocalEngine

    return LocalEngine(
        whisper_model=s.local_whisper_model,
        device=s.local_whisper_device,
        compute_type=s.local_whisper_compute_type,
        ollama_url=s.ollama_url,
        ollama_model=s.ollama_model,
    )


def _mock(s: Settings) -> Engine:
    from .mock import MockEngine

    return MockEngine(prices=(s.price_audio_in, s.price_text_in, s.price_text_out))


ENGINES: dict[str, Callable[[Settings], Engine]] = {
    "gemini": _gemini,
    "local": _local,
    "mock": _mock,
}


def build_engine(name: str, settings: Settings) -> Engine:
    try:
        factory = ENGINES[name]
    except KeyError:
        raise ValueError(
            f"Unknown engine '{name}'. Available: {', '.join(sorted(ENGINES))}"
        ) from None
    return factory(settings)
