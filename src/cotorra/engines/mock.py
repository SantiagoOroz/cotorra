"""Mock engine: the whole pipeline, zero credentials, zero GPU.

It exists for three unglamorous but important reasons:

1. ``docker compose up`` works for a reviewer who has no API key yet.
2. CI and the load test are hermetic and free.
3. You can measure the *transport* (fan-out, backpressure, dashboard, exports)
   without paying for model calls — that is how the 10-stage scale test runs
   on a laptop.

Captions it produces are clearly flagged ``demo: true`` and every UI shows a
warning banner, because fake subtitles that look real would be worse than none.
"""

from __future__ import annotations

import asyncio
import random

from .base import Engine, TranscribeRequest, TranscribeResult

SCRIPT: list[dict[str, str]] = [
    {
        "en": "So the first thing we did was move every stage to a single ingest pipeline.",
        "es": "Lo primero que hicimos fue mover cada escenario a un único pipeline de ingesta.",
        "pt": "A primeira coisa que fizemos foi mover cada palco para um único pipeline.",
    },
    {
        "en": "That gave us about four hundred milliseconds of end to end latency.",
        "es": "Eso nos dio unos cuatrocientos milisegundos de latencia de punta a punta.",
        "pt": "Isso nos deu cerca de quatrocentos milissegundos de latência ponta a ponta.",
    },
    {
        "en": "We deploy it with Kubernetes, and each stage is just one more pod.",
        "es": "Lo desplegamos con Kubernetes, y cada escenario es simplemente un pod más.",
        "pt": "Implantamos com Kubernetes, e cada palco é apenas mais um pod.",
    },
    {
        "en": "Accessibility is not a feature you add at the end of the project.",
        "es": "La accesibilidad no es una funcionalidad que agregás al final del proyecto.",
        "pt": "Acessibilidade não é uma funcionalidade que você adiciona no fim do projeto.",
    },
    {
        "en": "Any question so far? Great, let's look at the benchmark numbers.",
        "es": "¿Alguna pregunta hasta acá? Genial, veamos los números del benchmark.",
        "pt": "Alguma pergunta até aqui? Ótimo, vamos ver os números do benchmark.",
    },
    {
        "en": "The open source version runs on a single machine with Gemma.",
        "es": "La versión open source corre en una sola máquina con Gemma.",
        "pt": "A versão open source roda em uma única máquina com Gemma.",
    },
]


class MockEngine(Engine):
    name = "mock"
    demo = True

    def __init__(
        self,
        latency_ms: int = 350,
        jitter_ms: int = 150,
        seed: int | None = None,
        prices: tuple[float, float, float] = (1.00, 0.30, 2.50),
    ) -> None:
        self.model = "mock"
        self.latency_ms = latency_ms
        self.jitter_ms = jitter_ms
        self._rng = random.Random(seed)
        self._prices = prices
        self._i = 0

    def estimate_cost_usd(self, result: TranscribeResult) -> float:
        """What this workload *would* have cost on Gemini.

        Nothing is actually billed in mock mode, but a zero on the ops panel
        teaches an operator nothing. Running the 10-stage load test and reading
        a realistic dollar figure is the point.
        """
        audio_p, text_p, out_p = self._prices
        return (
            result.audio_tokens * audio_p
            + result.input_tokens * text_p
            + result.output_tokens * out_p
        ) / 1_000_000

    async def transcribe(self, req: TranscribeRequest) -> TranscribeResult:
        # Pretend to be a network round trip so latency graphs are meaningful.
        delay = (self.latency_ms + self._rng.uniform(0, self.jitter_ms)) / 1000
        await asyncio.sleep(delay)

        line = SCRIPT[self._i % len(SCRIPT)]
        if not req.partial:
            self._i += 1

        source = req.source_lang if req.source_lang in line else "en"
        texts = {source: line[source]}
        for target in req.targets:
            if target in line:
                texts.setdefault(target, line[target])
            else:
                texts.setdefault(target, f"[{target}] {line[source]}")

        if req.partial:  # a partial is a prefix of the eventual final
            texts = {k: _prefix(v) for k, v in texts.items()}

        return TranscribeResult(
            source_lang=source,
            texts=texts,
            model=self.model,
            audio_tokens=int(req.duration_s * 32),
            input_tokens=220,
            output_tokens=len(" ".join(texts.values())) // 4,
        )


def _prefix(text: str, ratio: float = 0.6) -> str:
    words = text.split()
    keep = max(1, int(len(words) * ratio))
    return " ".join(words[:keep])
