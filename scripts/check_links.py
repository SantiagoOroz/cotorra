#!/usr/bin/env python3
"""Fail if a Markdown link in this repo points at nothing.

    python scripts/check_links.py

Docs that lie are worse than no docs, and a README is the first thing anyone
sees. This runs in CI so a renamed heading cannot rot a link silently.

Anchor slugs follow GitHub's rules: lowercase, drop everything that is not a
word character, space or hyphen (so emoji and backticks vanish), then replace
each space with a hyphen WITHOUT collapsing runs. That is why "## 🎥 Demo"
becomes "#-demo" with a leading hyphen.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
SKIP_DIRS = {".venv", "node_modules", ".git", ".pytest_cache", ".ruff_cache", "__pycache__"}
IN_CODE_FENCE = re.compile(r"^\s*```")


def slug(heading: str) -> str:
    text = heading.lstrip("#").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def headings(text: str) -> set[str]:
    found, fenced = set(), False
    for line in text.splitlines():
        if IN_CODE_FENCE.match(line):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            found.add(slug(line))
    return found


def main() -> int:
    files = sorted(
        p for p in ROOT.rglob("*.md")
        if not any(part in SKIP_DIRS for part in p.parts)
    )
    anchors = {p: headings(p.read_text(encoding="utf-8")) for p in files}

    problems: list[str] = []
    checked = 0

    for md in files:
        text = md.read_text(encoding="utf-8")
        for label, target in LINK.findall(text):
            # Skip external links and GitHub's own ../../issues shorthand,
            # which only resolves once the repo is published.
            if target.startswith(("http://", "https://", "mailto:", "../../")):
                continue
            checked += 1
            path, _, frag = target.partition("#")
            dest = md if not path else (md.parent / path).resolve()

            if path and not dest.exists():
                problems.append(f"{md.relative_to(ROOT)} -> {target} (no such file)")
            elif frag and dest in anchors and frag not in anchors[dest]:
                problems.append(
                    f"{md.relative_to(ROOT)} -> [{label}]({target}) (no such heading)"
                )

    print(f"checked {checked} internal links across {len(files)} files")
    for p in problems:
        print(f"  BROKEN  {p}")
    if problems:
        print(f"\n{len(problems)} broken link(s)")
        return 1
    print("  all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
