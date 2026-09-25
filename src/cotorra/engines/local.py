"""100% local engine: faster-whisper for ASR + Gemma (via Ollama) for translation.

For venues with no uplink, air-gapped events, or organisers who cannot send
stage audio to a third party. Slower and heavier than the Gemini path, same
wire format, so every UI and export works unchanged.

    ollama pull gemma3:4b
    pip install 'cotorra[local]'
    COTORRA_ENGINE=local cotorra serve
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .base import Engine, TranscribeRequest, TranscribeResult
from .gemini import lang_name

log = logging.getLogger(__name__)


class LocalEngine(Engine):
    name = "local"

    def __init__(
        self,
        whisper_model: str = "small",
        device: str = "auto",
        compute_type: str = "int8",
        ollama_url: str = "http://localhost:11434",
        ollama_model: str = "gemma3:4b",
    ) -> None:
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "The local engine needs faster-whisper: pip install 'cotorra[local]'"
            ) from exc

        log.info("Loading faster-whisper '%s' on %s (%s)", whisper_model, device, compute_type)
        self._whisper = WhisperModel(whisper_model, device=device, compute_type=compute_type)
        self.model = f"faster-whisper:{whisper_model}+{ollama_model}"
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self._http = httpx.AsyncClient(timeout=60.0)
        # One GPU/CPU, one decode at a time: queueing beats thrashing.
        self._asr_lock = asyncio.Lock()

    async def transcribe(self, req: TranscribeRequest) -> TranscribeResult:
        text, detected = await self._asr(req)
        texts: dict[str, str] = {}
        if text:
            texts[detected] = text
            todo = [t for t in req.targets if t != detected]
            if todo:
                translated = await asyncio.gather(
                    *(self._translate(text, detected, t, req) for t in todo),
                    return_exceptions=True,
                )
                for target, out in zip(todo, translated, strict=False):
                    if isinstance(out, Exception):
                        log.warning("Local translation to %s failed: %s", target, out)
                    elif out:
                        texts[target] = out
        return TranscribeResult(source_lang=detected, texts=texts, model=self.model)

    async def _asr(self, req: TranscribeRequest) -> tuple[str, str]:
        import numpy as np  # noqa: PLC0415 - comes with faster-whisper

        audio = np.frombuffer(req.pcm, dtype=np.int16).astype(np.float32) / 32768.0
        language = None if req.source_lang == "auto" else req.source_lang
        prompt = ", ".join(req.glossary[:60]) if req.glossary else None

        def _run() -> tuple[str, str]:
            segments, info = self._whisper.transcribe(
                audio,
                language=language,
                beam_size=1,  # greedy: ~2x faster, fine for live captions
                vad_filter=False,  # Cotorra already segmented the audio
                condition_on_previous_text=False,  # stops runaway repetition loops
                initial_prompt=prompt,
            )
            return " ".join(s.text.strip() for s in segments).strip(), info.language

        async with self._asr_lock:
            return await asyncio.to_thread(_run)

    async def _translate(self, text: str, src: str, target: str, req: TranscribeRequest) -> str:
        glossary = ""
        if req.glossary:
            glossary = (
                "\nKeep these terms exactly as written: " + ", ".join(req.glossary[:80]) + "."
            )
        prompt = (
            f"Translate this live conference subtitle from {lang_name(src)} to "
            f"{lang_name(target)}. Reply with the translation only, no notes, no quotes."
            f"{glossary}\n\nSubtitle: {text}"
        )
        resp = await self._http.post(
            f"{self.ollama_url}/api/generate",
            json={
                "model": self.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 256},
            },
        )
        resp.raise_for_status()
        return (resp.json().get("response") or "").strip().strip('"')

    async def aclose(self) -> None:
        await self._http.aclose()
