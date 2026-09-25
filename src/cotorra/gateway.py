"""The gateway: control plane, caption fan-out and the three web UIs.

It is deliberately stateless with respect to captions in flight — everything
durable is either on disk (transcripts) or in the worker. That is what lets you
run several replicas behind a load balancer with ``REDIS_URL`` set, and what
makes a gateway restart during a talk survivable: workers reconnect and keep
going.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import socket
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from . import exporters
from .config import Settings, get_settings, load_manifest
from .glossary import list_glossaries
from .hub import build_hub
from .models import Caption, SessionSpec, SessionStatus
from .store import TranscriptStore
from .supervisor import LocalSupervisor, SessionManager, SupervisorError

log = logging.getLogger("cotorra.gateway")

STARTED_AT = time.time()


def _manager(request: Request) -> SessionManager:
    return request.app.state.manager


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _lan_ip() -> str | None:
    """This machine's address on the local network, or None.

    Connecting a UDP socket sends no packet; it only asks the OS which
    interface it would route through, which is exactly the one phones in the
    room can reach.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            return probe.getsockname()[0]
    except OSError:
        return None


def public_base(request: Request, settings: Settings) -> str:
    """The URL the audience should use to reach this gateway.

    PUBLIC_URL wins when it is set. Otherwise, if the operator opened the page
    on localhost, a QR code pointing at localhost would be useless to every
    phone in the room — so swap in the LAN address.
    """
    if settings.public_url:
        return settings.public_url.rstrip("/")
    base = str(request.base_url).rstrip("/")
    host = request.url.hostname or ""
    if host in ("localhost", "127.0.0.1"):
        ip = _lan_ip()
        if ip:
            base = base.replace(host, ip, 1)
    return base


def require_admin(
    request: Request,
    x_cotorra_token: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
) -> None:
    """Write operations need the admin token — when one is configured.

    Leaving ADMIN_TOKEN empty keeps ``docker compose up`` friction-free for a
    first run; the gateway logs a loud warning and the ops UI shows a banner.
    """
    expected = request.app.state.settings.admin_token
    if not expected:
        return
    supplied = x_cotorra_token or token
    if supplied != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    try:
        settings.ensure_dirs()
    except OSError as exc:
        # A bind-mounted data/ owned by another uid is a classic first-run
        # papercut. Losing transcripts and exports is bad; refusing to caption
        # a live event over a directory permission is worse.
        log.error(
            "Cannot write to %s (%s). Live captions will work, but transcripts and "
            "exports are disabled. Fix with: chown -R 10001:10001 ./data",
            settings.data_dir,
            exc,
        )

    hub = await build_hub(settings.redis_url, settings.subscriber_queue)
    store = TranscriptStore(settings.transcripts_dir, settings.backlog_size)
    supervisor = LocalSupervisor(settings)
    manager = SessionManager(settings, hub, store, supervisor)

    restored = manager.restore()
    for spec in load_manifest(settings.manifest):
        manager.upsert(spec)
    log.info(
        "Loaded %d session(s) (%d restored from disk). Engine default: %s",
        len(manager.sessions),
        restored,
        settings.engine,
    )
    if not settings.admin_token:
        log.warning("ADMIN_TOKEN is empty: the control API is unauthenticated on this instance")

    app.state.hub = hub
    app.state.store = store
    app.state.manager = manager

    manager.start_watchdog()
    await manager.start_autostart()
    try:
        yield
    finally:
        await manager.shutdown()
        await hub.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Cotorra",
        version="0.1.0",
        summary="Open source real-time transcription and translation for conferences.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # ---------------------------------------------------------------- health

    @app.get("/api/health", tags=["ops"])
    async def health(request: Request) -> dict:
        manager = _manager(request)
        states: dict[str, int] = {}
        for status in manager.sessions.values():
            states[status.state] = states.get(status.state, 0) + 1
        broken = sum(states.get(s, 0) for s in ("error",))
        return {
            "ok": broken == 0,
            "uptime_s": round(time.time() - STARTED_AT, 1),
            "sessions": len(manager.sessions),
            "states": states,
            "engine_default": request.app.state.settings.engine,
            "redis": bool(request.app.state.settings.redis_url),
        }

    @app.get("/api/config", tags=["ops"])
    async def public_config(request: Request) -> dict:
        s = _settings(request)
        return {
            "public_url": s.public_url,
            "default_engine": s.engine,
            "auth_required": bool(s.admin_token),
            "partials": s.partials,
        }

    @app.get("/metrics", response_class=PlainTextResponse, tags=["ops"])
    async def metrics(request: Request) -> str:
        """Prometheus exposition. Point Grafana at it and you have an event dashboard."""
        manager = _manager(request)
        lines = [
            "# HELP cotorra_up Gateway liveness",
            "# TYPE cotorra_up gauge",
            "cotorra_up 1",
            "# HELP cotorra_session_up 1 when the session is producing captions",
            "# TYPE cotorra_session_up gauge",
        ]
        gauges = [
            ("cotorra_captions_total", lambda st: st.metrics.captions, "counter"),
            ("cotorra_errors_total", lambda st: st.metrics.errors, "counter"),
            ("cotorra_latency_p50_ms", lambda st: st.metrics.latency_p50_ms, "gauge"),
            ("cotorra_latency_p95_ms", lambda st: st.metrics.latency_p95_ms, "gauge"),
            ("cotorra_viewers", lambda st: st.viewers, "gauge"),
            ("cotorra_cost_usd", lambda st: round(st.metrics.cost_usd, 6), "gauge"),
            ("cotorra_audio_seconds", lambda st: st.metrics.audio_seconds, "counter"),
        ]
        for status in manager.list():
            label = f'{{session="{status.spec.id}",stage="{status.spec.stage}"}}'
            lines.append(
                f"cotorra_session_up{label} {1 if status.state == 'running' else 0}"
            )
        for name, fn, kind in gauges:
            lines.append(f"# TYPE {name} {kind}")
            for status in manager.list():
                label = f'{{session="{status.spec.id}",stage="{status.spec.stage}"}}'
                lines.append(f"{name}{label} {fn(status)}")
        return "\n".join(lines) + "\n"

    # -------------------------------------------------------------- sessions

    @app.get("/api/sessions", tags=["sessions"])
    async def list_sessions(request: Request) -> list[SessionStatus]:
        return _manager(request).list()

    @app.post("/api/sessions", dependencies=[Depends(require_admin)], tags=["sessions"])
    async def create_session(request: Request, spec: SessionSpec) -> SessionStatus:
        return _manager(request).upsert(spec)

    @app.get("/api/sessions/{session_id}", tags=["sessions"])
    async def get_session(request: Request, session_id: str) -> SessionStatus:
        status = _manager(request).get(session_id)
        if status is None:
            raise HTTPException(404, f"No session '{session_id}'")
        status.viewers = request.app.state.hub.viewers(session_id)
        return status

    @app.post(
        "/api/sessions/{session_id}/start",
        dependencies=[Depends(require_admin)],
        tags=["sessions"],
    )
    async def start_session(
        request: Request, session_id: str, reset: bool = False
    ) -> SessionStatus:
        try:
            return await _manager(request).start(session_id, restart_transcript=reset)
        except KeyError:
            raise HTTPException(404, f"No session '{session_id}'") from None
        except SupervisorError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post(
        "/api/sessions/{session_id}/stop",
        dependencies=[Depends(require_admin)],
        tags=["sessions"],
    )
    async def stop_session(request: Request, session_id: str) -> SessionStatus:
        status = await _manager(request).stop(session_id)
        if status is None:
            raise HTTPException(404, f"No session '{session_id}'")
        return status

    @app.post(
        "/api/sessions/{session_id}/drill",
        dependencies=[Depends(require_admin)],
        tags=["sessions"],
    )
    async def drill_session(request: Request, session_id: str) -> SessionStatus:
        """Fire drill: kill the worker hard and let crash recovery bring it back.

        Rehearse this before doors open. It runs the exact path a real crash
        takes — and a recovery path nobody has ever exercised is a hope, not a
        feature.
        """
        try:
            return await _manager(request).simulate_crash(session_id)
        except KeyError:
            raise HTTPException(404, f"No session '{session_id}'") from None
        except SupervisorError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/sessions/{session_id}/share", tags=["sessions"])
    async def share(request: Request, session_id: str, lang: str = "es") -> dict:
        """The audience URL for a session, and a QR code that points at it."""
        if _manager(request).get(session_id) is None:
            raise HTTPException(404, f"No session '{session_id}'")
        base = public_base(request, _settings(request))
        url = f"{base}/viewer?session={quote(session_id)}&lang={quote(lang)}"
        return {
            "url": url,
            "qr": f"/api/sessions/{quote(session_id)}/qr.svg?lang={quote(lang)}",
            "reachable_from_phones": "localhost" not in url and "127.0.0.1" not in url,
        }

    @app.get("/api/sessions/{session_id}/qr.svg", tags=["sessions"])
    async def qr(request: Request, session_id: str, lang: str = "es") -> Response:
        """A QR for the stage screen, so people follow along from their seat."""
        import segno  # noqa: PLC0415

        info = await share(request, session_id, lang)
        buf = io.BytesIO()
        segno.make(info["url"], error="m").save(
            buf, kind="svg", scale=10, border=2, dark="#000", light="#fff", xmldecl=False
        )
        return Response(
            buf.getvalue(),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store", "X-Cotorra-URL": info["url"]},
        )

    def _preview_files(request: Request, session_id: str) -> dict[str, Path]:
        """Media sitting next to a session's local audio file, by kind.

        Only files that share a configured session's source name can ever be
        served, so the demo endpoints cannot be used to read anything else.
        """
        status = _manager(request).get(session_id)
        if status is None:
            raise HTTPException(404, f"No session '{session_id}'")
        source = Path(status.spec.source)
        if not source.is_file():
            raise HTTPException(404, "Only sessions fed from a local file have a preview")
        found: dict[str, Path] = {"audio": source}
        for ext in (".mp4", ".webm"):
            if source.with_suffix(ext).is_file():
                found["video"] = source.with_suffix(ext)
                break
        for ext in (".jpg", ".png", ".webp"):
            if source.with_suffix(ext).is_file():
                found["poster"] = source.with_suffix(ext)
                break
        return found

    @app.get(
        "/api/sessions/{session_id}/preview",
        dependencies=[Depends(require_admin)],
        tags=["demo"],
    )
    async def preview(request: Request, session_id: str) -> dict:
        """What /demo can show: the talk's video if there is one, else its audio
        with a still picture. Either way it is the very file Cotorra is
        transcribing, which is what makes the sync exact."""
        files = _preview_files(request, session_id)
        base = f"/api/sessions/{quote(session_id)}/media?kind="
        kind = "video" if "video" in files else "audio"
        return {
            "kind": kind,
            "media": base + kind,
            "poster": base + "poster" if "poster" in files else None,
        }

    @app.get(
        "/api/sessions/{session_id}/media",
        dependencies=[Depends(require_admin)],
        tags=["demo"],
    )
    async def media(request: Request, session_id: str, kind: str = "video") -> FileResponse:
        files = _preview_files(request, session_id)
        path = files.get(kind)
        if path is None:
            raise HTTPException(
                404,
                f"No {kind} next to {files['audio'].name}. "
                "Fetch it with: python scripts/fetch_samples.py --video",
            )
        mime = {
            ".mp4": "video/mp4", ".webm": "video/webm", ".jpg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp", ".ogg": "audio/ogg",
            ".opus": "audio/ogg", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
        }.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=mime)

    @app.delete(
        "/api/sessions/{session_id}", dependencies=[Depends(require_admin)], tags=["sessions"]
    )
    async def delete_session(request: Request, session_id: str) -> dict:
        await _manager(request).remove(session_id)
        return {"deleted": session_id}

    # ------------------------------------------------------------- transcript

    @app.get("/api/sessions/{session_id}/transcript", tags=["transcript"])
    async def transcript(
        request: Request,
        session_id: str,
        lang: str = "es",
        format: str = "srt",
        download: bool = True,
    ):
        store: TranscriptStore = request.app.state.store
        manager = _manager(request)
        if manager.get(session_id) is None and not store.path_for(session_id).exists():
            raise HTTPException(404, f"No session '{session_id}'")
        if format not in exporters.FORMATS:
            raise HTTPException(
                400, f"Unknown format '{format}'. Available: {', '.join(exporters.FORMATS)}"
            )
        captions = store.read_all(session_id)
        if not captions:
            raise HTTPException(404, "No captions recorded for this session yet")
        body = exporters.export(captions, lang, format)
        headers = {}
        if download:
            headers["Content-Disposition"] = (
                f'attachment; filename="{session_id}.{lang}.{format}"'
            )
        return PlainTextResponse(body, media_type=exporters.MIME[format], headers=headers)

    @app.get("/api/sessions/{session_id}/languages", tags=["transcript"])
    async def languages(request: Request, session_id: str) -> dict:
        status = _manager(request).get(session_id)
        available = request.app.state.store.languages(session_id)
        declared = status.spec.languages() if status else []
        merged = list(dict.fromkeys(declared + available))
        return {"languages": merged, "recorded": available}

    @app.get("/api/glossaries", tags=["ops"])
    async def glossaries(request: Request) -> dict:
        return {"glossaries": list_glossaries(_settings(request))}

    # ------------------------------------------------------------ websockets

    @app.websocket("/ws/ingest/{session_id}")
    async def ws_ingest(websocket: WebSocket, session_id: str, token: str = "") -> None:
        """Workers push captions and heartbeats here."""
        settings: Settings = websocket.app.state.settings
        if token != settings.worker_token:
            await websocket.close(code=4401, reason="bad worker token")
            return
        manager: SessionManager = websocket.app.state.manager
        if manager.get(session_id) is None:
            await websocket.close(code=4404, reason="unknown session")
            return
        await websocket.accept()
        log.info("Worker connected: %s", session_id)
        manager.register_worker(session_id, websocket)
        try:
            while True:
                message = json.loads(await websocket.receive_text())
                await manager.on_worker_message(session_id, message)
        except WebSocketDisconnect:
            log.info("Worker disconnected: %s", session_id)
        except Exception:
            log.exception("Ingest socket for %s failed", session_id)
            with contextlib.suppress(Exception):
                await websocket.close(code=1011)
        finally:
            manager.unregister_worker(session_id, websocket)

    @app.websocket("/ws/captions/{session_id}")
    async def ws_captions(websocket: WebSocket, session_id: str, lang: str = "es") -> None:
        """What the audience connects to. No auth, no account, no app."""
        manager: SessionManager = websocket.app.state.manager
        status = manager.get(session_id)
        if status is None:
            await websocket.close(code=4404, reason="unknown session")
            return
        await websocket.accept()
        hub = websocket.app.state.hub
        store: TranscriptStore = websocket.app.state.store

        async with hub.subscribe(session_id) as sub:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "hello",
                        "session": json.loads(status.model_dump_json()),
                        "lang": lang,
                        "backlog": [
                            _for_lang(c, lang) for c in store.backlog(session_id)
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            pump = asyncio.create_task(_pump(websocket, sub, lang))
            try:
                # Reading is how we notice the viewer closing the tab.
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump

    @app.websocket("/ws/ops")
    async def ws_ops(websocket: WebSocket, token: str = "") -> None:
        """Every session's status on one socket, for the production dashboard."""
        settings: Settings = websocket.app.state.settings
        if settings.admin_token and token != settings.admin_token:
            await websocket.close(code=4401, reason="bad admin token")
            return
        await websocket.accept()
        manager: SessionManager = websocket.app.state.manager
        hub = websocket.app.state.hub

        def snapshot() -> str:
            return json.dumps(
                {
                    "type": "snapshot",
                    "sessions": [json.loads(s.model_dump_json()) for s in manager.list()],
                },
                ensure_ascii=False,
            )

        async with hub.subscribe("*") as sub:
            try:
                await websocket.send_text(snapshot())
                while True:
                    try:
                        event = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
                        await websocket.send_text(json.dumps(event, ensure_ascii=False))
                    except asyncio.TimeoutError:
                        # A quiet event is still an event: a periodic snapshot
                        # keeps viewer counts and "seconds since last caption"
                        # honest even when nothing is being published.
                        await websocket.send_text(snapshot())
            except (WebSocketDisconnect, RuntimeError):
                return

    # ------------------------------------------------------------------ web

    web_dir = settings.web_dir
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=web_dir / "static"), name="static")

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(web_dir / "index.html")

        for page in ("viewer", "overlay", "ops", "demo"):

            def _make(page_name: str):
                async def _page() -> FileResponse:
                    return FileResponse(web_dir / f"{page_name}.html")

                return _page

            app.get(f"/{page}", response_class=HTMLResponse, include_in_schema=False)(_make(page))
    else:  # pragma: no cover - only when running from a stripped install
        log.warning("Web directory %s not found; API-only mode", web_dir)

    @app.exception_handler(404)
    async def not_found(request: Request, exc) -> JSONResponse:
        return JSONResponse({"detail": getattr(exc, "detail", "Not found")}, status_code=404)

    return app


def _for_lang(caption: Caption, lang: str) -> dict:
    """Send viewers only the language they asked for: less bandwidth on venue wifi."""
    return {
        "seq": caption.seq,
        "kind": caption.kind,
        "t_start": caption.t_start,
        "t_end": caption.t_end,
        "lang": lang if lang in caption.texts else caption.source_lang,
        "source_lang": caption.source_lang,
        "text": caption.text_for(lang),
        "latency_ms": caption.latency_ms,
        "demo": caption.demo,
    }


async def _pump(websocket: WebSocket, sub, lang: str) -> None:
    while True:
        event = await sub.queue.get()
        if event.get("type") == "caption":
            caption = Caption(**event["caption"])
            payload = {"type": "caption", **_for_lang(caption, lang)}
        else:
            payload = event
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))


app = create_app()
