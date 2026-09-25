#!/usr/bin/env python3
"""What would captioning your event actually cost?

    python scripts/cost_model.py --talks 30 --hours 1 --languages 2

Every assumption is a flag, and every flag is printed back, so you can argue
with the model instead of trusting a number someone put in a README. The rates
default to the ones in your .env, which are the same ones the live cost counter
on the ops panel uses.

Cross-check against a real measurement any time:

    python scripts/scale_test.py --sessions 10 --seconds 60
"""

from __future__ import annotations

import argparse

from cotorra.config import get_settings

# Gemini bills audio at a fixed token rate per second of input.
AUDIO_TOKENS_PER_SECOND = 32
# System prompt + glossary + rolling context, per call. Measured, not guessed:
# 608 tokens with an 85-term glossary. The first version of this model assumed
# 260 and was wrong by a factor of two, which is exactly the sort of thing a
# README number hides until someone gets a bill.
PROMPT_OVERHEAD_TOKENS = 610
# ~4 characters per token, ~5.5 characters per word, so ~1.4 tokens per word.
TOKENS_PER_WORD = 1.4


def model(
    talks: int,
    hours: float,
    languages: int,
    words_per_minute: int,
    speech_ratio: float,
    segment_s: float,
    partials: bool,
    partial_interval_s: float,
    price_audio: float,
    price_in: float,
    price_out: float,
) -> dict:
    total_hours = talks * hours
    speech_seconds = total_hours * 3600 * speech_ratio

    # How many model calls: one per utterance, plus the interim ones.
    final_calls = speech_seconds / segment_s

    # Count the partials exactly rather than approximating. A segment that
    # lasts segment_s emits one at partial_interval_s, 2x, 3x ... and each one
    # re-sends every second of audio accumulated so far. Approximating this as
    # "half a segment on average" understated the real bill by about 2x, which
    # measurement on two real talks caught: the model said partials cost +44%,
    # the meter said +213%.
    offsets = []
    if partials:
        t = partial_interval_s
        while t < segment_s:
            offsets.append(t)
            t += partial_interval_s

    partials_per_segment = len(offsets)
    partial_calls = final_calls * partials_per_segment
    calls = final_calls + partial_calls

    audio_per_segment = segment_s + sum(offsets)
    audio_seconds_sent = final_calls * audio_per_segment
    audio_tokens = audio_seconds_sent * AUDIO_TOKENS_PER_SECOND

    text_in_tokens = calls * PROMPT_OVERHEAD_TOKENS

    spoken_words = total_hours * 60 * words_per_minute * speech_ratio
    # One transcript + (languages - 1) translations, each roughly as long.
    out_tokens = spoken_words * TOKENS_PER_WORD * languages
    if partials:
        # A partial regenerates the text produced so far, so on average it
        # emits about half a segment's worth of output on top of the final.
        out_tokens *= 1 + partials_per_segment * 0.5

    cost_audio = audio_tokens * price_audio / 1e6
    cost_in = text_in_tokens * price_in / 1e6
    cost_out = out_tokens * price_out / 1e6
    total = cost_audio + cost_in + cost_out

    return {
        "stage_hours": total_hours,
        "calls": calls,
        "audio_tokens": audio_tokens,
        "text_in_tokens": text_in_tokens,
        "out_tokens": out_tokens,
        "cost_audio": cost_audio,
        "cost_in": cost_in,
        "cost_out": cost_out,
        "total": total,
    }


def main() -> None:
    s = get_settings()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--talks", type=int, default=30)
    ap.add_argument("--hours", type=float, default=1.0, help="Length of each talk")
    ap.add_argument("--languages", type=int, default=2, help="Transcript + translations")
    ap.add_argument("--wpm", type=int, default=140, help="Words per minute of speech")
    ap.add_argument(
        "--speech-ratio", type=float, default=0.75,
        help="Fraction of the talk that is actually speech (pauses, demos, Q&A gaps)",
    )
    ap.add_argument(
        "--segment-s", type=float, default=5.5,
        help="Mean utterance length. 5.5 s is what two real conference talks measured.",
    )
    ap.add_argument("--no-partials", action="store_true", help="Cheaper, slower to appear")
    ap.add_argument("--partial-interval-s", type=float, default=s.partial_interval_ms / 1000)
    ap.add_argument("--price-audio", type=float, default=s.price_audio_in)
    ap.add_argument("--price-in", type=float, default=s.price_text_in)
    ap.add_argument("--price-out", type=float, default=s.price_text_out)
    a = ap.parse_args()

    both = {}
    for partials in (True, False):
        both[partials] = model(
            a.talks, a.hours, a.languages, a.wpm, a.speech_ratio, a.segment_s,
            partials, a.partial_interval_s, a.price_audio, a.price_in, a.price_out,
        )

    r = both[not a.no_partials]
    print()
    print("  ASSUMPTIONS")
    print(f"    talks                 {a.talks} x {a.hours} h = {r['stage_hours']:.0f} stage-hours")
    print(f"    languages delivered   {a.languages} "
          f"(transcript + {a.languages - 1} translation(s))")
    print(f"    speaking rate         {a.wpm} wpm, {a.speech_ratio:.0%} of the time")
    print(f"    mean utterance        {a.segment_s:.1f} s")
    interim = "off" if a.no_partials else f"every {a.partial_interval_s:.1f} s"
    print(f"    interim captions      {interim}")
    print(f"    rates (USD / 1M tok)  audio {a.price_audio} · in {a.price_in} · out {a.price_out}")
    print()
    print("  BREAKDOWN")
    print(f"    model calls           {r['calls']:,.0f}")
    print(f"    audio tokens          {r['audio_tokens']:,.0f}   ${r['cost_audio']:.2f}")
    print(f"    prompt tokens         {r['text_in_tokens']:,.0f}   ${r['cost_in']:.2f}")
    print(f"    output tokens         {r['out_tokens']:,.0f}   ${r['cost_out']:.2f}")
    print()
    print("  RESULT")
    print(f"    per stage-hour        ${r['total'] / r['stage_hours']:.3f}")
    print(f"    whole event           ${r['total']:.2f}")
    print()
    on, off = both[True]["total"], both[False]["total"]
    print(f"    with interim captions ${on:.2f}   ({on / off:.1f}x, for ~half the felt delay)")
    print(f"    without               ${off:.2f}   (PARTIALS=false)")
    print()
    print("  Rates change. Check https://ai.google.dev/pricing and update PRICE_* in .env;")
    print("  this script and the live counter on /ops both read those values.")
    print()


if __name__ == "__main__":
    main()
