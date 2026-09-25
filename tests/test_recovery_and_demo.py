"""Crash recovery continuity, the fire drill, the stage screen and the demo page.

The continuity tests pin down a bug that would have shown up on camera: a
worker restarted after a crash used to number its captions from 0 again, and
every client keys lines by that number — so the new worker's first caption
silently replaced the first line of the talk on every screen, and the export
lost it.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cotorra import gateway as gw
from cotorra.config import Settings
from cotorra.gateway import create_app
from cotorra.models import Caption, SessionSpec, SessionStatus
from cotorra.store import TranscriptStore
from cotorra.supervisor import SessionManager

ADMIN = {"X-Cotorra-Token": "t"}


def cap(seq: int, t0: float, t1: float, sid: str = "s") -> Caption:
    return Caption(session_id=sid, seq=seq, t_start=t0, t_end=t1, texts={"en": f"line {seq}"})


# --- where a new worker picks up ------------------------------------------


def test_tail_of_an_empty_session_starts_from_zero(tmp_path):
    assert TranscriptStore(tmp_path).tail("s") == (0, 0.0)


def test_tail_continues_after_the_last_final(tmp_path):
    store = TranscriptStore(tmp_path)
    store.append(cap(0, 0.0, 2.0))
    store.append(cap(1, 2.5, 5.2))
    assert store.tail("s") == (2, 5.2)


def test_tail_survives_a_gateway_restart(tmp_path):
    """The in-memory ring is gone after a restart; the file is not."""
    TranscriptStore(tmp_path).append(cap(7, 30.0, 33.3))
    assert TranscriptStore(tmp_path).tail("s") == (8, 33.3)


def test_a_reset_starts_the_numbering_over(tmp_path):
    store = TranscriptStore(tmp_path)
    store.append(cap(4, 10.0, 12.0))
    store.clear("s")
    assert store.tail("s") == (0, 0.0)


class RecordingSupervisor:
    """Stands in for LocalSupervisor and remembers what it was asked to spawn."""

    def __init__(self):
        self.spawns: list[tuple[str, int, float]] = []

    def spawn(self, spec, seq_base=0, t_base=0.0):
        self.spawns.append((spec.id, seq_base, t_base))

    def running(self, _):
        return False


class NullHub:
    async def publish(self, *_):
        pass

    def viewers(self, _):
        return 0


def manager(tmp_path) -> tuple[SessionManager, TranscriptStore, RecordingSupervisor]:
    settings = Settings(data_dir=tmp_path, manifest=tmp_path / "none.yaml")
    store = TranscriptStore(tmp_path / "t")
    sup = RecordingSupervisor()
    mgr = SessionManager(settings, NullHub(), store, sup)
    mgr.sessions["s"] = SessionStatus(spec=SessionSpec(id="s", source="mic"))
    return mgr, store, sup


def test_a_first_start_numbers_from_zero(tmp_path):
    mgr, _, sup = manager(tmp_path)
    mgr._spawn(mgr.sessions["s"], crashed=False)
    assert sup.spawns == [("s", 0, 0.0)]


def test_a_worker_restarted_after_a_crash_continues_the_numbering(tmp_path):
    mgr, store, sup = manager(tmp_path)
    for i in range(3):
        store.append(cap(i, i * 3.0, i * 3.0 + 2.5))
    status = mgr.sessions["s"]
    status.started_at = time.time() - 5  # the talk has been running 5 s

    mgr._spawn(status, crashed=True)

    _, seq_base, t_base = sup.spawns[-1]
    assert seq_base == 3                 # no collision with lines 0, 1, 2
    assert t_base >= 8.5                 # never behind the last line on screen


def test_a_crash_restart_follows_the_wall_clock_on_a_live_feed(tmp_path):
    """The feed kept going while the worker was down; so does the timeline."""
    mgr, store, sup = manager(tmp_path)
    store.append(cap(0, 0.0, 2.0))
    status = mgr.sessions["s"]
    status.started_at = time.time() - 120

    mgr._spawn(status, crashed=True)

    assert sup.spawns[-1][2] == pytest.approx(120, abs=2)


def test_a_manual_restart_continues_without_overlapping(tmp_path):
    mgr, store, sup = manager(tmp_path)
    store.append(cap(0, 0.0, 4.0))
    mgr._spawn(mgr.sessions["s"], crashed=False)
    assert sup.spawns[-1][1:] == (1, 5.0)


async def test_the_worker_applies_the_bases_it_was_given(tmp_path):
    from cotorra.audio.segmenter import Segment
    from cotorra.engines.mock import MockEngine
    from cotorra.worker import CaptionWorker

    settings = Settings(data_dir=tmp_path, manifest=tmp_path / "none.yaml")
    worker = CaptionWorker(
        SessionSpec(id="s", source="mic", source_lang="en", targets=["es"]),
        "ws://unused", "tok", settings,
        engine=MockEngine(latency_ms=0, jitter_ms=0),
        seq_base=42, t_base=100.0,
    )
    await worker._submit(Segment(pcm=b"\0" * 3200, t_start=1.0, t_end=3.0, kind="final", index=0))
    import asyncio

    task = asyncio.create_task(worker._publisher())
    msg = await asyncio.wait_for(worker._outbox.get(), 5)
    task.cancel()

    assert msg["caption"]["seq"] == 42
    assert msg["caption"]["t_start"] == 101.0


# --- gateway: drill, share, QR, demo preview -------------------------------


@pytest.fixture
def audio_file(tmp_path) -> Path:
    path = tmp_path / "talk.ogg"
    path.write_bytes(b"OggS fake audio")
    return path


@pytest.fixture
def client(tmp_path, audio_file):
    s = Settings(data_dir=tmp_path / "d", manifest=tmp_path / "none.yaml",
                 admin_token="t", worker_token="w")
    with TestClient(create_app(s)) as c:
        c.post("/api/sessions", headers=ADMIN, json={
            "id": "talk", "source": str(audio_file), "source_lang": "en",
            "targets": ["es"], "engine": "mock"})
        c.post("/api/sessions", headers=ADMIN, json={"id": "live", "source": "mic"})
        yield c


def test_a_drill_needs_the_admin_token(client):
    assert client.post("/api/sessions/talk/drill").status_code == 401


def test_a_drill_on_a_stopped_session_explains_itself(client):
    r = client.post("/api/sessions/talk/drill", headers=ADMIN)
    assert r.status_code == 409
    assert "no running worker" in r.json()["detail"]


def test_share_points_at_the_audience_page(client):
    body = client.get("/api/sessions/talk/share?lang=es").json()
    assert "/viewer?session=talk&lang=es" in body["url"]
    assert body["qr"].endswith("/qr.svg?lang=es")


def test_the_qr_is_a_real_svg(client):
    r = client.get("/api/sessions/talk/qr.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in r.content
    assert "/viewer?session=talk" in r.headers["x-cotorra-url"]


def test_a_qr_for_localhost_would_be_useless_so_the_lan_address_is_used(monkeypatch):
    monkeypatch.setattr(gw, "_lan_ip", lambda: "192.168.1.50")

    class Req:
        base_url = "http://localhost:8080/"

        class url:
            hostname = "localhost"

    assert gw.public_base(Req, Settings()) == "http://192.168.1.50:8080"


def test_public_url_always_wins(monkeypatch):
    monkeypatch.setattr(gw, "_lan_ip", lambda: "192.168.1.50")

    class Req:
        base_url = "http://localhost:8080/"

        class url:
            hostname = "localhost"

    s = Settings(public_url="https://subtitulos.nerdear.la/")
    assert gw.public_base(Req, s) == "https://subtitulos.nerdear.la"


def test_preview_needs_the_admin_token(client):
    assert client.get("/api/sessions/talk/preview").status_code == 401


def test_preview_falls_back_to_the_talks_own_audio(client, audio_file):
    audio_file.with_suffix(".jpg").write_bytes(b"\xff\xd8 fake jpeg")
    body = client.get("/api/sessions/talk/preview", headers=ADMIN).json()
    assert body["kind"] == "audio"
    assert body["poster"].endswith("kind=poster")

    r = client.get(body["media"], headers=ADMIN)
    assert r.status_code == 200
    assert r.content == b"OggS fake audio"
    assert r.headers["content-type"] == "audio/ogg"


def test_preview_prefers_a_video_when_there_is_one(client, audio_file):
    audio_file.with_suffix(".mp4").write_bytes(b"fake mp4")
    body = client.get("/api/sessions/talk/preview", headers=ADMIN).json()
    assert body["kind"] == "video"
    assert client.get(body["media"], headers=ADMIN).content == b"fake mp4"


def test_media_only_serves_files_next_to_a_configured_source(client):
    """Nothing but the session's own siblings is reachable."""
    assert client.get("/api/sessions/live/preview", headers=ADMIN).status_code == 404
    assert client.get("/api/sessions/talk/media?kind=../../etc", headers=ADMIN).status_code == 404


def test_the_demo_page_is_served(client):
    r = client.get("/demo")
    assert r.status_code == 200
    assert "Empezar" in r.text


# --- offline subtitling ----------------------------------------------------


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_subtitle_a_recording_offline(tmp_path):
    from typer.testing import CliRunner

    from cotorra.cli import app

    sample = Path(__file__).resolve().parents[1] / "samples" / "pipeline-test.ogg"
    result = CliRunner().invoke(app, [
        "subtitle", str(sample), "--from", "en", "--to", "es",
        "--engine", "mock", "--offset", "3", "--out", str(tmp_path), "--formats", "srt",
    ])
    assert result.exit_code == 0, result.output
    srt = (tmp_path / "pipeline-test.es.srt").read_text(encoding="utf-8")
    assert "-->" in srt
    # --offset shifts every cue: the first one cannot start before 3 s.
    first = srt.split("\n")[1]
    assert first.startswith("00:00:03") or first > "00:00:03"
