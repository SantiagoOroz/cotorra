"""Glossaries: the cheapest accuracy win in the whole system.

A conference has a closed vocabulary that generic models get wrong in
predictable ways — speaker names, sponsor names, the project everybody is
talking about that week. Feeding 100 terms into the prompt costs a fraction of
a cent per hour and fixes most of the embarrassing errors.

Files live in ``data/glossaries/<name>.txt``: one term per line, ``#`` for
comments. ``data/glossaries/default.txt`` is applied when a session does not
name one.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Settings, get_settings

log = logging.getLogger(__name__)

MAX_TERMS = 150


def glossary_path(name: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    safe = Path(name).name  # no traversal out of the glossary directory
    if not safe.endswith(".txt"):
        safe += ".txt"
    return settings.glossaries_dir / safe


def load_glossary(name: str | None, settings: Settings | None = None) -> list[str]:
    """Return the terms for ``name``, always merged with ``default``."""
    settings = settings or get_settings()
    terms: list[str] = []
    for candidate in ("default", name):
        if not candidate:
            continue
        path = glossary_path(candidate, settings)
        if not path.exists():
            if candidate != "default":
                log.warning("Glossary '%s' not found at %s", candidate, path)
            continue
        terms.extend(parse_glossary(path.read_text(encoding="utf-8")))
    return dedupe(terms)[:MAX_TERMS]


def parse_glossary(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def dedupe(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            out.append(term)
    return out


def list_glossaries(settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    if not settings.glossaries_dir.exists():
        return {}
    return {
        p.stem: len(parse_glossary(p.read_text(encoding="utf-8")))
        for p in sorted(settings.glossaries_dir.glob("*.txt"))
    }
