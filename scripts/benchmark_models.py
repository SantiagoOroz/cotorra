#!/usr/bin/env python3
"""Measure latency, cost and output of each candidate model on YOUR audio.

    python scripts/benchmark_models.py --source samples/nerdearla-mcp-en.ogg \
        --source-lang en --targets es

Model names and speeds change constantly, and a number in a README ages badly.
This runs the real engine against real segments of a real talk and prints a
table you can act on. Run it the week of your event and pick the winner.

Calls are serialised with a pause between them so a free-tier key does not
trip its rate limit mid-benchmark; 429s are retried, and a model that keeps
failing is reported rather than silently skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

from cotorra.audio import FFmpegSource, Segmenter, SegmenterConfig
from cotorra.config import Settings
from cotorra.engines import TranscribeRequest
from cotorra.engines.gemini import GeminiEngine
from cotorra.glossary import load_glossary

# Reasonable candidates for live captioning: fast, multimodal, generally
# available. Pass --models to override.
DEFAULT_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]


async def collect_segments(source: str, count: int, sample_rate: int) -> list:
    """Real utterances from real audio — not synthetic tones."""
    segmenter = Segmenter(SegmenterConfig(partials=False, sample_rate=sample_rate))
    feed = FFmpegSource(source, sample_rate)
    out: list = []
    async for chunk in feed.stream():
        out += segmenter.feed(chunk)
        if len(out) >= count:
            break
    await feed.aclose()
    return out[:count]


async def one_call(engine: GeminiEngine, req: TranscribeRequest, retries: int = 2):
    for attempt in range(retries + 1):
        started = time.time()
        try:
            result = await engine.transcribe(req)
            return int((time.time() - started) * 1000), result, None
        except Exception as exc:
            message = str(exc)
            if "429" in message and attempt < retries:
                await asyncio.sleep(20 * (attempt + 1))
                continue
            short = message.split("\n")[0][:110]
            return None, None, f"{type(exc).__name__}: {short}"
    return None, None, "exhausted retries"


async def benchmark(model: str, segments, args, glossary, settings) -> dict:
    try:
        engine = GeminiEngine(
            api_key=settings.gemini_api_key,
            model=model,
            thinking_budget=args.thinking,
            max_retries=1,  # the retry loop above owns backoff
            timeout_s=args.timeout,
            price_audio_in=settings.price_audio_in,
            price_text_in=settings.price_text_in,
            price_text_out=settings.price_text_out,
        )
    except Exception as exc:
        return {"model": model, "error": str(exc)[:110]}

    latencies, cost, context, samples, error = [], 0.0, [], [], None
    for segment in segments:
        req = TranscribeRequest(
            pcm=segment.pcm,
            sample_rate=args.sample_rate,
            source_lang=args.source_lang,
            targets=args.targets,
            context="\n".join(context[-3:]),
            glossary=glossary,
        )
        ms, result, err = await one_call(engine, req)
        if err:
            error = err
            break
        latencies.append(ms)
        cost += engine.estimate_cost_usd(result)
        text = result.texts.get(result.source_lang, "")
        if text:
            context.append(text)
            samples.append(text)
        await asyncio.sleep(args.pause)

    await engine.aclose()
    if not latencies:
        return {"model": model, "error": error or "no successful calls"}

    ordered = sorted(latencies)
    audio_s = sum(s.duration for s in segments[: len(latencies)])
    return {
        "model": model,
        "n": len(latencies),
        "p50": int(statistics.median(ordered)),
        "p95": ordered[int(0.95 * (len(ordered) - 1))],
        "min": min(ordered),
        "max": max(ordered),
        "usd_per_speech_hour": cost / audio_s * 3600 if audio_s else 0.0,
        "sample": samples[0] if samples else "",
        "error": error,
    }


async def main() -> int:
    settings = Settings()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="samples/nerdearla-mcp-en.ogg")
    ap.add_argument("--source-lang", default="en")
    ap.add_argument("--targets", default="es")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--segments", type=int, default=5)
    ap.add_argument("--thinking", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument(
        "--pause", type=float, default=4.0,
        help="Seconds between calls. Raise it if you are on a free-tier key.",
    )
    ap.add_argument("--sample-rate", type=int, default=16000)
    args = ap.parse_args()
    args.targets = [t.strip() for t in args.targets.split(",") if t.strip()]

    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set (see .env.example)", file=sys.stderr)
        return 2

    print(f"\nsource     {args.source}")
    print(f"direction  {args.source_lang} -> {', '.join(args.targets)} "
          f"({len(args.targets) + 1} languages per call)")
    segments = await collect_segments(args.source, args.segments, args.sample_rate)
    if not segments:
        print("No speech found in that source.", file=sys.stderr)
        return 1
    audio = sum(s.duration for s in segments)
    print(f"segments   {len(segments)} real utterances, {audio:.1f}s of speech")
    glossary = load_glossary("nerdearla", settings)
    print(f"glossary   {len(glossary)} terms")
    print(f"thinking   {args.thinking}\n")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    rows = []
    for model in models:
        print(f"  testing {model} …", flush=True)
        rows.append(await benchmark(model, segments, args, glossary, settings))

    ok = [r for r in rows if "p50" in r]
    ok.sort(key=lambda r: r["p50"])

    print(f"\n{'model':28s} {'p50':>8} {'p95':>8} {'min':>8} {'max':>8} {'US$/speech-h':>13}")
    print("-" * 79)
    for r in ok:
        print(f"{r['model']:28s} {r['p50']:>6}ms {r['p95']:>6}ms {r['min']:>6}ms "
              f"{r['max']:>6}ms {r['usd_per_speech_hour']:>13.3f}")
    for r in rows:
        if "p50" not in r:
            print(f"{r['model']:28s}  unavailable: {r['error']}")

    if ok:
        best = ok[0]
        print(f"\nfastest: {best['model']}  ({best['p50']} ms p50, "
              f"${best['usd_per_speech_hour']:.3f} per hour of speech)")
        print("  Note: per hour of SPEECH, not per hour of talk. A talk is roughly")
        print("  75% speech, so a stage-hour costs about a quarter less.")
        print(f'  sample output: "{best["sample"][:100]}"')
        print(f"\nSet it with:  GEMINI_MODEL={best['model']}")
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
