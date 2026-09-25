"""Turn a finished session into the files a conference actually needs.

SRT and WebVTT go straight to YouTube, Vimeo or a video editor; the plain text
version is what goes into the blog recap and makes the talk searchable. One
file per language, generated from the same captions the audience saw.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from .models import Caption

FORMATS = ("srt", "vtt", "txt", "json")
MIME = {
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


def _clock(seconds: float, sep: str) -> str:
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _usable(captions: Iterable[Caption], lang: str) -> list[Caption]:
    """Finals only, deduplicated by seq, in time order, with text in ``lang``."""
    by_seq: dict[int, Caption] = {}
    for c in captions:
        if c.kind != "final":
            continue
        if c.text_for(lang).strip():
            by_seq[c.seq] = c
    return sorted(by_seq.values(), key=lambda c: (c.t_start, c.seq))


def to_srt(captions: Iterable[Caption], lang: str) -> str:
    lines: list[str] = []
    for i, c in enumerate(_usable(captions, lang), start=1):
        lines.append(str(i))
        lines.append(f"{_clock(c.t_start, ',')} --> {_clock(_end(c), ',')}")
        lines.append(c.text_for(lang).strip())
        lines.append("")
    return "\n".join(lines)


def to_vtt(captions: Iterable[Caption], lang: str) -> str:
    lines = ["WEBVTT", ""]
    for c in _usable(captions, lang):
        lines.append(f"{_clock(c.t_start, '.')} --> {_clock(_end(c), '.')}")
        lines.append(c.text_for(lang).strip())
        lines.append("")
    return "\n".join(lines)


def to_txt(captions: Iterable[Caption], lang: str) -> str:
    return "\n".join(c.text_for(lang).strip() for c in _usable(captions, lang)) + "\n"


def to_json(captions: Iterable[Caption], lang: str) -> str:
    return json.dumps(
        [c.model_dump() for c in _usable(captions, lang)], ensure_ascii=False, indent=2
    )


def _end(caption: Caption) -> float:
    """Guarantee a positive, readable duration even for one-word captions."""
    return max(caption.t_end, caption.t_start + 0.8)


def export(captions: Iterable[Caption], lang: str, fmt: str) -> str:
    captions = list(captions)
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format '{fmt}'. Available: {', '.join(FORMATS)}")
    return {"srt": to_srt, "vtt": to_vtt, "txt": to_txt, "json": to_json}[fmt](captions, lang)
