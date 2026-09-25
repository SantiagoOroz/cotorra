"""Gemini engine: one multimodal call per utterance returns ASR + every translation.

Latency notes (they matter more than anything else here):
* ``thinking_budget=0`` — for verbatim transcription there is nothing to reason
  about, and thinking tokens are pure added delay.
* structured output — no markdown fences to strip, no retry on malformed JSON.
* ``temperature=0`` — captions should be reproducible; creativity is a bug.
* one call for N languages — 2 languages cost one round trip, not two.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from pydantic import BaseModel

from ..audio.segmenter import pcm_to_wav
from .base import SYSTEM_PROMPT, Engine, TranscribeRequest, TranscribeResult

log = logging.getLogger(__name__)

# The API rejects a shorter HTTP deadline with a 400: "Manually set deadline
# 8s is too short. Minimum allowed deadline is 10s."
MIN_HTTP_TIMEOUT_S = 10.0

LANG_NAMES = {
    "es": "Spanish (neutral Latin American)",
    "en": "English",
    "pt": "Portuguese (Brazilian)",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "zh": "Chinese (Simplified)",
    "ko": "Korean",
    "ca": "Catalan",
    "gn": "Guarani",
    "qu": "Quechua",
}


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code, code)


class _Translation(BaseModel):
    lang: str
    text: str


class _AsrPayload(BaseModel):
    """The structured output schema handed to the model."""

    source_language: str
    transcript: str
    translations: list[_Translation]


class GeminiEngine(Engine):
    name = "gemini"

    def __init__(
        self,
        api_key: str = "",
        model: str = "gemini-2.5-flash",
        vertex: bool = False,
        project: str = "",
        location: str = "us-central1",
        thinking_budget: int = 0,
        max_retries: int = 3,
        timeout_s: float = 8.0,
        deadline_s: float = 10.0,
        max_retry_wait_s: float = 2.0,
        price_audio_in: float = 1.00,
        price_text_in: float = 0.30,
        price_text_out: float = 2.50,
    ) -> None:
        try:
            from google import genai  # noqa: PLC0415
            from google.genai import types  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "The Gemini engine needs the google-genai SDK: pip install 'cotorra[gemini]'"
            ) from exc
        # Two ways in, same SDK, same everything downstream.
        #
        #   AI Studio  an API key. Easiest to get; the free tier allows on the
        #              order of 15-20 requests per DAY per model, which is not
        #              a typo and not enough to caption anything.
        #   Vertex AI  a Google Cloud project with billing. This is where
        #              hackathon and startup credits land, and where the quota
        #              is measured per minute instead of per day.
        if vertex:
            if not project:
                raise RuntimeError(
                    "GEMINI_USE_VERTEX is on but GCP_PROJECT is empty. Set it to your "
                    "Google Cloud project id, and authenticate with "
                    "`gcloud auth application-default login` (or point "
                    "GOOGLE_APPLICATION_CREDENTIALS at a service account key)."
                )
            self._client = genai.Client(vertexai=True, project=project, location=location)
            log.info("Using Vertex AI: project=%s location=%s", project, location)
        else:
            if not api_key:
                raise RuntimeError(
                    "GEMINI_API_KEY is not set. Either set it (see .env.example), or "
                    "switch to Vertex AI with GEMINI_USE_VERTEX=true and GCP_PROJECT."
                )
            self._client = genai.Client(api_key=api_key)

        self._types = types
        self.vertex = vertex
        self.model = model
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self.deadline_s = deadline_s
        self.max_retry_wait_s = max_retry_wait_s
        self._prices = (price_audio_in, price_text_in, price_text_out)
        self._cfg_kwargs = dict(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=_AsrPayload,
            max_output_tokens=2048,
            # Two different clocks. The API refuses an HTTP deadline under
            # MIN_HTTP_TIMEOUT_S, but live captioning wants to abandon a call
            # much sooner than that — so the transport gets the legal minimum
            # and asyncio.wait_for enforces the deadline we actually care
            # about. See _call().
            http_options=types.HttpOptions(
                timeout=int(max(timeout_s, MIN_HTTP_TIMEOUT_S) * 1000)
            ),
        )
        # Flash-Lite models have no thinking mode at all, and reject the
        # parameter outright with a generic 400. Rather than make every user
        # discover that by hand, we send it, notice the rejection once, and
        # drop it for the rest of the run. See _call().
        self._thinking_supported = True
        try:
            self._cfg_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking_budget
            )
        except Exception:  # pragma: no cover - SDK/model variance
            self._thinking_supported = False
            log.warning("This SDK build does not support thinking_config; continuing without it")

    # -- prompt -------------------------------------------------------------

    @staticmethod
    def build_prompt(req: TranscribeRequest) -> str:
        src = "auto-detect it" if req.source_lang == "auto" else lang_name(req.source_lang)
        wanted = [lang_name(t) for t in req.targets]
        parts = [
            f"Source language: {src}.",
            "Return the verbatim transcript plus one translation per requested "
            f"language, using these exact language codes: {', '.join(req.targets)} "
            f"({'; '.join(wanted)}).",
        ]
        if req.glossary:
            parts.append(
                "Spell these exactly when you hear them: " + ", ".join(req.glossary[:120]) + "."
            )
        if req.context:
            parts.append(
                "Captions already shown (context only, do NOT repeat them):\n" + req.context
            )
        if req.partial:
            parts.append(
                "This clip is cut mid-sentence. Transcribe what is audible and stop; "
                "do not finish the sentence yourself."
            )
        return "\n\n".join(parts)

    # -- engine API ---------------------------------------------------------

    def _drop_thinking(self) -> bool:
        """Retire ``thinking_config`` after a model rejects it. True if we changed anything."""
        if not self._thinking_supported:
            return False
        self._thinking_supported = False
        self._cfg_kwargs.pop("thinking_config", None)
        log.info(
            "%s does not accept thinking_config (Flash-Lite models have no thinking "
            "mode); dropping it and retrying",
            self.model,
        )
        return True

    async def _call(self, contents, budget: float | None = None):
        timeout = min(self.timeout_s, budget) if budget else self.timeout_s
        try:
            return await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=self._types.GenerateContentConfig(**self._cfg_kwargs),
                ),
                timeout=timeout,
            )
        except Exception as exc:
            # The API answers a rejected thinking_config with a generic
            # "invalid argument", so the only way to identify it is to drop
            # the parameter and see whether the call then succeeds.
            if "400" in str(exc) and self._drop_thinking():
                return await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=self._types.GenerateContentConfig(**self._cfg_kwargs),
                    ),
                    timeout=timeout,
                )
            raise

    async def transcribe(self, req: TranscribeRequest) -> TranscribeResult:
        wav = pcm_to_wav(req.pcm, req.sample_rate)
        contents = [
            self._types.Part.from_bytes(data=wav, mime_type="audio/wav"),
            self._types.Part.from_text(text=self.build_prompt(req)),
        ]

        # A caption has a shelf life. Retrying past it produces something that
        # is technically correct and practically useless — and because captions
        # publish in order, it holds up every utterance queued behind it. The
        # budget is for the whole utterance, not per attempt.
        deadline = time.monotonic() + self.deadline_s
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            budget = deadline - time.monotonic()
            if attempt and budget <= 0:
                raise TooLate(
                    f"Gave up after {self.deadline_s:.0f}s: {_summarise(last_error)}"
                )
            try:
                return self._to_result(await self._call(contents, budget), req)
            except asyncio.TimeoutError as exc:
                last_error = exc
                log.warning("Gemini timeout (attempt %d/%d)", attempt + 1, self.max_retries)
            except Exception as exc:  # network blips, 429, 5xx
                last_error = exc
                retry_after = _retry_delay_s(exc)
                log.warning(
                    "Gemini error (attempt %d/%d): %s",
                    attempt + 1,
                    self.max_retries,
                    _summarise(exc),
                )
                if retry_after is not None:
                    # Captions are published in order, so sleeping here stalls
                    # every utterance queued behind this one. A caption that
                    # lands 10 s late is not a late caption, it is a wrong
                    # one — and it drags the whole stage with it. Wait only if
                    # the API says the window reopens almost immediately.
                    if retry_after > self.max_retry_wait_s:
                        raise QuotaExceeded(_summarise(exc)) from exc
                    await asyncio.sleep(retry_after)
                    continue
            if attempt < self.max_retries - 1:
                # Backoff stays short: a caption that arrives 8 s late is useless
                # to the audience, better to drop it and catch the next chunk.
                await asyncio.sleep(0.4 * (2**attempt))
        raise RuntimeError(
            f"Gemini call failed after {self.max_retries} attempts: {_summarise(last_error)}"
        )

    def _to_result(self, resp, req: TranscribeRequest) -> TranscribeResult:
        payload = getattr(resp, "parsed", None)
        if payload is None:
            raw = (getattr(resp, "text", "") or "").strip()
            if not raw:
                return TranscribeResult(source_lang=req.source_lang, texts={}, model=self.model)
            payload = _AsrPayload(**json.loads(_strip_fences(raw)))

        source_lang = _normalise_lang(payload.source_language) or req.source_lang
        texts: dict[str, str] = {}
        transcript = payload.transcript.strip()
        if transcript:
            texts[source_lang] = transcript
        for tr in payload.translations:
            code = _normalise_lang(tr.lang)
            text = tr.text.strip()
            # Never let a translation overwrite the verbatim transcript.
            if text and code and code not in texts:
                texts[code] = text

        usage = getattr(resp, "usage_metadata", None)
        audio_tokens, input_tokens = _split_usage(usage, req.duration_s)
        return TranscribeResult(
            source_lang=source_lang,
            texts=texts,
            model=self.model,
            audio_tokens=audio_tokens,
            input_tokens=input_tokens,
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
        )

    def estimate_cost_usd(self, result: TranscribeResult) -> float:
        audio_p, text_p, out_p = self._prices
        return (
            result.audio_tokens * audio_p
            + result.input_tokens * text_p
            + result.output_tokens * out_p
        ) / 1_000_000


class TooLate(RuntimeError):
    """The utterance spent its whole budget and is no longer worth publishing."""


class QuotaExceeded(RuntimeError):
    """The model API refused because of quota, not because of anything we did.

    Worth its own type: the fix is a billing page, not a code change, and the
    ops panel should say so instead of showing a stack trace.
    """


_RETRY_RE = re.compile(r"retry in ([0-9.]+)s", re.IGNORECASE)
_RETRY_FIELD_RE = re.compile(r"'retryDelay':\s*'(\d+)s'")


def _retry_delay_s(exc: Exception) -> float | None:
    """How long the API asked us to wait, if it said so.

    Gemini returns a precise ``retryDelay`` on 429. Honouring it beats a made
    up exponential backoff: too short and every retry is wasted, too long and
    the captions fall behind the speaker.
    """
    text = str(exc)
    if "429" not in text and "RESOURCE_EXHAUSTED" not in text:
        return None
    for pattern in (_RETRY_RE, _RETRY_FIELD_RE):
        found = pattern.search(text)
        if found:
            return float(found.group(1))
    return 30.0  # rate limited but no hint: assume a per-minute window


def _summarise(exc: Exception | None) -> str:
    """One readable line. The raw quota errors are 1500 characters of JSON."""
    if exc is None:
        return "unknown error"
    text = str(exc)
    if "generate_content_free_tier_requests" in text:
        limit = re.search(r"limit: (\d+)", text)
        model = re.search(r"model: ([\w.-]+)", text)
        where = f" on {model.group(1)}" if model else ""
        cap = f" ({limit.group(1)} requests/day)" if limit else ""
        return (
            f"Free-tier quota exhausted{where}{cap}. Enable billing, or set "
            "GEMINI_MODEL to a model whose quota is still free."
        )
    if "requires billing to be enabled" in text:
        return (
            "Billing is not active on the Google Cloud project — the credit may have "
            "expired, or billing was just linked and is still propagating (retry in a "
            "few minutes)."
        )
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        return "Rate limited by the model API (429). Lower WORKER_CONCURRENCY or PARTIALS."
    if "503" in text:
        return "Model temporarily unavailable (503). It usually clears on its own."
    return text.split("\n")[0][:200]


def _split_usage(usage, duration_s: float) -> tuple[int, int]:
    """Separate audio tokens from text tokens in the usage report.

    Gemini bills audio at a different rate, so a cost estimate that lumps them
    together is wrong by ~3x. When the SDK does not break it down we fall back
    to the documented 32 tokens/second of audio.
    """
    if usage is None:
        return int(duration_s * 32), 0
    total_in = int(getattr(usage, "prompt_token_count", 0) or 0)
    audio = 0
    for detail in getattr(usage, "prompt_tokens_details", None) or []:
        modality = str(getattr(detail, "modality", "")).upper()
        if "AUDIO" in modality:
            audio += int(getattr(detail, "token_count", 0) or 0)
    if not audio:
        audio = min(total_in, int(duration_s * 32))
    return audio, max(0, total_in - audio)


def _strip_fences(text: str) -> str:
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _normalise_lang(code: str) -> str:
    """``English`` / ``es-AR`` / ``spa`` -> ``en`` / ``es`` / ``es``."""
    code = (code or "").strip().lower()
    if not code:
        return ""
    by_name = {
        "spanish": "es",
        "english": "en",
        "portuguese": "pt",
        "french": "fr",
        "german": "de",
        "italian": "it",
        "spa": "es",
        "eng": "en",
        "por": "pt",
    }
    if code in by_name:
        return by_name[code]
    return code.split("-")[0].split("_")[0]
