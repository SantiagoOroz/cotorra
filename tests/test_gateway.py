"""End-to-end tests of the gateway, driven through its real HTTP/WS surface.

Workers are simulated by opening the same ingest websocket a real worker uses,
so these tests cover the actual wire protocol rather than a mock of it.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cotorra.config import Settings
from cotorra.gateway import create_app
from cotorra.models import Caption


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        manifest=tmp_path / "missing.yaml",  # start from an empty event
        worker_token="test-worker",
        admin_token="test-admin",
        engine="mock",
        port=0,
    )


@pytest.fixture
def client(settings) -> TestClient:
    with TestClient(create_app(settings)) as c:
        yield c


ADMIN = {"X-Cotorra-Token": "test-admin"}

SPEC = {
    "id": "keynote",
    "title": "Opening keynote",
    "stage": "Main",
    "source": "mic",
    "source_lang": "en",
    "targets": ["es"],
    "engine": "mock",
}


def caption(seq: int, **texts) -> dict:
    return {
        "type": "caption",
        "caption": Caption(
            session_id="keynote",
            seq=seq,
            t_start=seq * 2.0,
            t_end=seq * 2.0 + 1.8,
            source_lang="en",
            texts=texts,
            latency_ms=700,
            engine="mock",
        ).model_dump(),
    }


# --- health / config -------------------------------------------------------


def test_health_reports_an_empty_event(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["sessions"] == 0


def test_public_config_never_leaks_the_token(client):
    body = client.get("/api/config").json()
    assert body["auth_required"] is True
    assert "test-admin" not in json.dumps(body)


# --- auth ------------------------------------------------------------------


def test_creating_a_session_requires_the_admin_token(client):
    assert client.post("/api/sessions", json=SPEC).status_code == 401
    assert client.post("/api/sessions", json=SPEC, headers=ADMIN).status_code == 200


def test_reading_sessions_never_requires_a_token(client):
    """The audience must never need credentials to read captions."""
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    assert client.get("/api/sessions").status_code == 200
    assert client.get("/api/sessions/keynote").status_code == 200


def test_an_open_instance_allows_writes(tmp_path):
    """Empty ADMIN_TOKEN keeps the first run frictionless."""
    s = Settings(data_dir=tmp_path, manifest=tmp_path / "none.yaml", admin_token="")
    with TestClient(create_app(s)) as c:
        assert c.post("/api/sessions", json=SPEC).status_code == 200


# --- session lifecycle -----------------------------------------------------


def test_unknown_session_is_a_404(client):
    assert client.get("/api/sessions/nope").status_code == 404


def test_invalid_session_id_is_rejected(client):
    bad = {**SPEC, "id": "../../etc/passwd"}
    assert client.post("/api/sessions", json=bad, headers=ADMIN).status_code == 422


def test_sessions_survive_a_gateway_restart(settings):
    """An event schedule must not evaporate because the process restarted."""
    with TestClient(create_app(settings)) as c:
        c.post("/api/sessions", json=SPEC, headers=ADMIN)
    with TestClient(create_app(settings)) as c:
        assert [s["spec"]["id"] for s in c.get("/api/sessions").json()] == ["keynote"]


# --- the worker protocol ---------------------------------------------------


def test_ingest_rejects_a_bad_worker_token(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/ingest/keynote?token=wrong"):
            pass
    assert exc.value.code == 4401


def test_ingest_rejects_an_unknown_session(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/ingest/ghost?token=test-worker"):
            pass
    assert exc.value.code == 4404


def test_a_caption_reaches_a_viewer_in_the_language_they_asked_for(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    with client.websocket_connect("/ws/captions/keynote?lang=es") as viewer:
        assert viewer.receive_json()["type"] == "hello"
        with client.websocket_connect("/ws/ingest/keynote?token=test-worker") as worker:
            worker.send_json(caption(0, en="Good morning", es="Buenos días"))
            event = viewer.receive_json()

    assert event["type"] == "caption"
    assert event["text"] == "Buenos días"
    assert event["lang"] == "es"
    assert event["source_lang"] == "en"


def test_a_viewer_asking_for_a_missing_language_still_reads_something(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    with client.websocket_connect("/ws/captions/keynote?lang=pt") as viewer:
        viewer.receive_json()
        with client.websocket_connect("/ws/ingest/keynote?token=test-worker") as worker:
            worker.send_json(caption(0, en="Good morning", es="Buenos días"))
            event = viewer.receive_json()
    assert event["text"] == "Good morning"


def test_a_viewer_joining_late_gets_the_backlog(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    with client.websocket_connect("/ws/ingest/keynote?token=test-worker") as worker:
        worker.send_json(caption(0, en="First", es="Primera"))
        worker.send_json(caption(1, en="Second", es="Segunda"))
        # A heartbeat round-trips the socket, so the two captions above are
        # guaranteed to have been processed before we connect the viewer.
        worker.send_json({
            "type": "heartbeat", "session_id": "keynote", "state": "running",
            "metrics": {"captions": 2},
        })
        with client.websocket_connect("/ws/captions/keynote?lang=es") as viewer:
            hello = viewer.receive_json()

    assert [c["text"] for c in hello["backlog"]] == ["Primera", "Segunda"]
    assert hello["session"]["state"] == "running"


def test_heartbeats_drive_the_ops_view(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    with client.websocket_connect("/ws/ingest/keynote?token=test-worker") as worker:
        worker.send_json({
            "type": "heartbeat",
            "session_id": "keynote",
            "state": "degraded",
            "metrics": {"captions": 7, "errors": 2, "latency_p95_ms": 2200, "audio_level": 0.3},
            "last_error": "429 from the model API",
        })
        worker.send_json({"type": "heartbeat", "session_id": "keynote", "state": "degraded",
                          "metrics": {"captions": 7}})

    status = client.get("/api/sessions/keynote").json()
    assert status["state"] == "degraded"
    assert status["last_error"] == "429 from the model API"


def test_recovering_clears_the_error_banner(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    with client.websocket_connect("/ws/ingest/keynote?token=test-worker") as worker:
        worker.send_json({"type": "heartbeat", "session_id": "keynote", "state": "degraded",
                          "metrics": {}, "last_error": "transient blip"})
        worker.send_json({"type": "heartbeat", "session_id": "keynote", "state": "running",
                          "metrics": {"captions": 3}})
        worker.send_json({"type": "heartbeat", "session_id": "keynote", "state": "running",
                          "metrics": {"captions": 3}})

    status = client.get("/api/sessions/keynote").json()
    assert status["state"] == "running"
    assert status["last_error"] is None


# --- transcripts -----------------------------------------------------------


def test_transcript_export_in_every_format(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    with client.websocket_connect("/ws/ingest/keynote?token=test-worker") as worker:
        worker.send_json(caption(0, en="Hello", es="Hola"))
        worker.send_json(caption(1, en="World", es="Mundo"))
        worker.send_json({"type": "heartbeat", "session_id": "keynote",
                          "state": "running", "metrics": {}})

    srt = client.get("/api/sessions/keynote/transcript?lang=es&format=srt").text
    assert "Hola" in srt and "-->" in srt

    vtt = client.get("/api/sessions/keynote/transcript?lang=es&format=vtt").text
    assert vtt.startswith("WEBVTT")

    txt = client.get("/api/sessions/keynote/transcript?lang=en&format=txt").text
    assert txt.splitlines() == ["Hello", "World"]

    headers = client.get("/api/sessions/keynote/transcript?lang=es&format=srt").headers
    assert "keynote.es.srt" in headers["content-disposition"]


def test_transcript_rejects_an_unknown_format(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    assert client.get("/api/sessions/keynote/transcript?format=doc").status_code == 400


def test_transcript_of_a_session_with_no_captions_is_a_404(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    assert client.get("/api/sessions/keynote/transcript").status_code == 404


def test_languages_endpoint_merges_declared_and_recorded(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    with client.websocket_connect("/ws/ingest/keynote?token=test-worker") as worker:
        worker.send_json(caption(0, en="Hi", es="Hola", pt="Oi"))
        worker.send_json({"type": "heartbeat", "session_id": "keynote",
                          "state": "running", "metrics": {}})
    body = client.get("/api/sessions/keynote/languages").json()
    assert set(body["languages"]) >= {"en", "es", "pt"}


# --- metrics ---------------------------------------------------------------


def test_prometheus_metrics_expose_each_session(client):
    client.post("/api/sessions", json=SPEC, headers=ADMIN)
    body = client.get("/metrics").text
    assert 'cotorra_session_up{session="keynote",stage="Main"}' in body
    assert "# TYPE cotorra_latency_p95_ms gauge" in body


# --- two stages at once, the MVP requirement -------------------------------


def test_two_sessions_stay_independent(client):
    for i, sid in enumerate(["stage-a", "stage-b"]):
        client.post("/api/sessions", json={**SPEC, "id": sid, "stage": f"S{i}"}, headers=ADMIN)

    with client.websocket_connect("/ws/captions/stage-a?lang=es") as va, \
         client.websocket_connect("/ws/captions/stage-b?lang=es") as vb:
        va.receive_json()
        vb.receive_json()
        with client.websocket_connect("/ws/ingest/stage-a?token=test-worker") as wa, \
             client.websocket_connect("/ws/ingest/stage-b?token=test-worker") as wb:
            ca = caption(0, en="A", es="Hola desde A")
            ca["caption"]["session_id"] = "stage-a"
            cb = caption(0, en="B", es="Hola desde B")
            cb["caption"]["session_id"] = "stage-b"
            wa.send_json(ca)
            wb.send_json(cb)
            ea = va.receive_json()
            eb = vb.receive_json()

    assert ea["text"] == "Hola desde A"
    assert eb["text"] == "Hola desde B"
