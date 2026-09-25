"""Transcript persistence.

Append-only JSONL, one file per session, written as captions arrive. Chosen
over a database on purpose:

* a crash loses at most the last line, never the talk;
* ``tail -f data/transcripts/keynote.jsonl`` is a debugging tool everyone on
  the AV crew already knows how to use;
* exports are a pure function of the file, so a talk can be re-exported to a
  new language or format years later.

A small in-memory ring per session serves the backlog to viewers who join
mid-talk, so nobody arrives at a blank screen.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path

from .models import Caption

log = logging.getLogger(__name__)


class TranscriptStore:
    def __init__(self, directory: Path, backlog: int = 40) -> None:
        self.directory = directory
        self.backlog_size = backlog
        self.writable = True
        self._backlog: dict[str, deque[Caption]] = {}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # The in-memory backlog still serves late-joining viewers, so the
            # audience never notices; only persistence is lost.
            self.writable = False
            log.error("Transcripts disabled, %s is not writable: %s", directory, exc)

    def path_for(self, session_id: str) -> Path:
        return self.directory / f"{Path(session_id).name}.jsonl"

    def append(self, caption: Caption) -> None:
        if caption.kind != "final":
            return  # partials are ephemeral by definition
        ring = self._backlog.setdefault(caption.session_id, deque(maxlen=self.backlog_size))
        ring.append(caption)
        if not self.writable:
            return
        try:
            with self.path_for(caption.session_id).open("a", encoding="utf-8") as fh:
                fh.write(caption.model_dump_json() + "\n")
        except OSError as exc:  # never let disk trouble stop the live captions
            log.error("Could not persist caption for %s: %s", caption.session_id, exc)

    def backlog(self, session_id: str, limit: int | None = None) -> list[Caption]:
        ring = list(self._backlog.get(session_id, ()))
        return ring[-limit:] if limit else ring

    def read_all(self, session_id: str) -> list[Caption]:
        path = self.path_for(session_id)
        if not path.exists():
            return list(self._backlog.get(session_id, ()))
        out: list[Caption] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(Caption(**json.loads(line)))
                except Exception:
                    log.warning("Skipping malformed transcript line in %s", path)
        return out

    def tail(self, session_id: str) -> tuple[int, float]:
        """``(next_seq, last_t_end)`` — where a new worker must pick up from.

        A worker numbers its captions from its own counter. Without a base
        handed down from here, a worker restarted after a crash starts again at
        seq 0, and because every client keys lines by seq, its first caption
        would silently overwrite the first line of the talk on every screen —
        and the export would lose it.
        """
        ring = self._backlog.get(session_id)
        last = ring[-1] if ring else None
        if last is None:
            captions = self.read_all(session_id)
            last = captions[-1] if captions else None
        if last is None:
            return 0, 0.0
        return last.seq + 1, last.t_end

    def languages(self, session_id: str) -> list[str]:
        langs: set[str] = set()
        for caption in self.read_all(session_id):
            langs.update(caption.texts)
        return sorted(langs)

    def clear(self, session_id: str) -> None:
        """Used when an operator restarts a session from the top."""
        self._backlog.pop(session_id, None)
        path = self.path_for(session_id)
        if path.exists():
            archive = path.with_suffix(f".jsonl.{int(path.stat().st_mtime)}.bak")
            path.rename(archive)
            log.info("Archived previous transcript to %s", archive.name)
