"""``cotorra`` — the command line.

    cotorra serve                      run the gateway + the three web UIs
    cotorra worker --id x --source y   run one stage's worker by hand
    cotorra watch x --lang es          follow a session's captions in the terminal
    cotorra export x --lang es         write out SRT / VTT / TXT
    cotorra sessions                   what is running right now
    cotorra doctor                     check this machine before the event
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import get_settings, load_manifest
from .models import SessionSpec

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Open source real-time transcription and translation for conferences.",
)
console = Console()

STATE_STYLE = {
    "running": "bold green",
    "starting": "yellow",
    "degraded": "bold yellow",
    "error": "bold red",
    "idle": "dim",
    "stopped": "dim",
    "finished": "blue",
}


def _log_level(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _api(base: str) -> str:
    return base.rstrip("/")


def _ws(base: str) -> str:
    return _api(base).replace("https://", "wss://").replace("http://", "ws://")


# --------------------------------------------------------------------- serve


@app.command()
def serve(
    host: str = typer.Option(None, help="Bind address (default from settings)"),
    port: int = typer.Option(None, help="Port (default from settings)"),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (development)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the gateway: REST API, websockets, audience page, overlay and ops panel."""
    import uvicorn

    _log_level(verbose)
    settings = get_settings()
    host = host or settings.host
    port = port or settings.port

    console.print(
        Panel.fit(
            f"[bold]Cotorra[/bold] is up\n\n"
            f"  Audience   [cyan]http://localhost:{port}/[/cyan]\n"
            f"  Ops panel  [cyan]http://localhost:{port}/ops[/cyan]\n"
            f"  API docs   [cyan]http://localhost:{port}/docs[/cyan]\n\n"
            f"  engine=[yellow]{settings.engine}[/yellow]  "
            f"sessions=[yellow]{len(load_manifest())}[/yellow] from {settings.manifest.name}",
            border_style="green",
        )
    )
    uvicorn.run(
        "cotorra.gateway:app",
        host=host,
        port=port,
        reload=reload,
        log_level="debug" if verbose else "info",
    )


# -------------------------------------------------------------------- worker


@app.command()
def worker(
    id: str = typer.Option(None, help="Session id (or set COTORRA_SESSION_SPEC)"),
    source: str = typer.Option(
        None, help="File, stream URL, YouTube URL, 'mic' or 'device:<name>'"
    ),
    title: str = typer.Option("", help="Human readable title"),
    source_lang: str = typer.Option("auto", help="'auto', 'en', 'es', ..."),
    targets: str = typer.Option("es", help="Comma separated target languages"),
    engine: str = typer.Option(None, help="gemini | local | mock"),
    glossary: str = typer.Option(None, help="Glossary name under data/glossaries"),
    loop: bool = typer.Option(False, help="Replay file sources forever (demos)"),
    gateway: str = typer.Option("ws://127.0.0.1:8080", help="Gateway websocket base URL"),
    token: str = typer.Option(None, help="Worker token (defaults to settings)"),
    concurrency: int = typer.Option(3, help="Overlapping model calls"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Caption one session. Normally spawned by the gateway; useful standalone for debugging."""
    from .worker import CaptionWorker

    _log_level(verbose)
    settings = get_settings()

    raw = os.environ.get("COTORRA_SESSION_SPEC")
    if raw:
        spec = SessionSpec(**json.loads(raw))
    else:
        if not id or not source:
            console.print("[red]--id and --source are required[/red]")
            raise typer.Exit(2)
        spec = SessionSpec(
            id=id,
            title=title or id,
            source=source,
            source_lang=source_lang,
            targets=[t.strip() for t in targets.split(",") if t.strip()],
            engine=engine or settings.engine,
            glossary=glossary,
            loop=loop,
        )

    w = CaptionWorker(
        spec,
        gateway,
        token or settings.worker_token,
        settings,
        concurrency=concurrency,
        seq_base=int(os.environ.get("COTORRA_SEQ_BASE", "0") or 0),
        t_base=float(os.environ.get("COTORRA_T_BASE", "0") or 0),
    )
    raise typer.Exit(asyncio.run(w.run()))


# --------------------------------------------------------------------- watch


@app.command()
def watch(
    session_id: str = typer.Argument(..., help="Session to follow"),
    lang: str = typer.Option("es", help="Language to display"),
    api: str = typer.Option("http://127.0.0.1:8080", help="Gateway base URL"),
) -> None:
    """Follow a session's captions in the terminal (no browser needed)."""
    import websockets

    url = f"{_ws(api)}/ws/captions/{session_id}?lang={lang}"

    async def _run() -> None:
        lines: list[str] = []
        partial = ""
        async with websockets.connect(url) as ws:
            with Live(console=console, refresh_per_second=8, screen=False) as live:
                while True:
                    event = json.loads(await ws.recv())
                    if event.get("type") == "hello":
                        lines = [c["text"] for c in event.get("backlog", []) if c.get("text")]
                    elif event.get("type") == "caption":
                        if event["kind"] == "partial":
                            partial = event["text"]
                        else:
                            partial = ""
                            lines.append(f"{event['text']}  [dim]({event['latency_ms']} ms)[/dim]")
                    del lines[:-12]
                    body = Text.from_markup("\n".join(lines))
                    if partial:
                        body.append("\n" + partial, style="dim italic")
                    live.update(
                        Panel(body, title=f"{session_id} · {lang}", border_style="green")
                    )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None


# ------------------------------------------------------------------ sessions


@app.command()
def sessions(api: str = typer.Option("http://127.0.0.1:8080", help="Gateway base URL")) -> None:
    """Show every session and its live status."""
    try:
        data = httpx.get(f"{_api(api)}/api/sessions", timeout=10).json()
    except httpx.HTTPError as exc:
        console.print(f"[red]Cannot reach the gateway at {api}: {exc}[/red]")
        raise typer.Exit(1) from None

    table = Table(title="Cotorra sessions", header_style="bold")
    for col in ("id", "stage", "state", "langs", "captions", "p50", "p95", "viewers", "US$"):
        table.add_column(col)
    for s in data:
        m = s["metrics"]
        table.add_row(
            s["spec"]["id"],
            s["spec"]["stage"] or "-",
            Text(s["state"], style=STATE_STYLE.get(s["state"], "")),
            ",".join(s["spec"]["targets"]),
            str(m["captions"]),
            f"{m['latency_p50_ms']} ms",
            f"{m['latency_p95_ms']} ms",
            str(s["viewers"]),
            f"{m['cost_usd']:.4f}",
        )
    console.print(table)


@app.command()
def start(
    session_id: str,
    api: str = typer.Option("http://127.0.0.1:8080"),
    token: str = typer.Option(None),
    reset: bool = typer.Option(False, help="Archive the old transcript and start clean"),
) -> None:
    """Start a session."""
    _post(api, f"/api/sessions/{session_id}/start", token, params={"reset": reset})


@app.command()
def stop(
    session_id: str,
    api: str = typer.Option("http://127.0.0.1:8080"),
    token: str = typer.Option(None),
) -> None:
    """Stop a session."""
    _post(api, f"/api/sessions/{session_id}/stop", token)


def _post(api: str, path: str, token: str | None, params: dict | None = None) -> None:
    headers = {"X-Cotorra-Token": token or get_settings().admin_token}
    try:
        r = httpx.post(f"{_api(api)}{path}", headers=headers, params=params, timeout=30)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    body = r.json()
    console.print(f"[green]{body.get('state', 'ok')}[/green] {body.get('spec', {}).get('id', '')}")


# -------------------------------------------------------------------- export


@app.command()
def export(
    session_id: str,
    lang: str = typer.Option("es"),
    formats: str = typer.Option("srt,vtt,txt", help="Comma separated"),
    out: Path = typer.Option(Path("."), help="Output directory"),
    api: str = typer.Option(None, help="Fetch through a running gateway instead of local files"),
) -> None:
    """Write the full transcript to disk once a talk is over."""
    from . import exporters
    from .store import TranscriptStore

    out.mkdir(parents=True, exist_ok=True)
    wanted = [f.strip() for f in formats.split(",") if f.strip()]

    for fmt in wanted:
        if api:
            r = httpx.get(
                f"{_api(api)}/api/sessions/{session_id}/transcript",
                params={"lang": lang, "format": fmt, "download": False},
                timeout=60,
            )
            r.raise_for_status()
            body = r.text
        else:
            store = TranscriptStore(get_settings().transcripts_dir)
            captions = store.read_all(session_id)
            if not captions:
                console.print(f"[red]No transcript on disk for '{session_id}'[/red]")
                raise typer.Exit(1)
            body = exporters.export(captions, lang, fmt)
        path = out / f"{session_id}.{lang}.{fmt}"
        path.write_text(body, encoding="utf-8")
        console.print(f"[green]wrote[/green] {path} ({len(body):,} bytes)")


# ------------------------------------------------------------------ subtitle


@app.command()
def subtitle(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Audio or video file"),
    source_lang: str = typer.Option("es", "--from", help="Spoken language, or 'auto'"),
    to: str = typer.Option("en", "--to", help="Comma separated target languages"),
    formats: str = typer.Option("srt,vtt", help="Comma separated: srt, vtt, txt, json"),
    offset: float = typer.Option(
        0.0, help="Seconds to add to every timestamp: where this audio starts in your edit"
    ),
    glossary: str = typer.Option("nerdearla", help="Glossary under data/glossaries"),
    engine: str = typer.Option(None, help="gemini | local | mock (default from settings)"),
    out: Path = typer.Option(None, help="Output directory (default: next to the file)"),
) -> None:
    """Subtitle a recording — a past talk, or your own demo video. No gateway needed.

        cotorra subtitle demo-voz.m4a --from es --to en --offset 3
    """
    from . import exporters
    from .audio import FFmpegSource, Segmenter, SegmenterConfig
    from .engines import TranscribeRequest, build_engine
    from .glossary import load_glossary
    from .models import Caption

    settings = get_settings()
    targets = [t.strip() for t in to.split(",") if t.strip()]
    wanted = [f.strip() for f in formats.split(",") if f.strip()]
    out_dir = out or file.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    engine_name = engine or settings.engine
    if engine_name == "mock":
        console.print(
            "[yellow]engine=mock: the output will be scripted text, not your audio. "
            "Set COTORRA_ENGINE=gemini for real subtitles.[/yellow]"
        )

    async def _run() -> tuple[list[Caption], int]:
        eng = build_engine(engine_name, settings)
        # Offline there is no audience waiting, so trade latency for patience:
        # a transient 403/503 should cost a few seconds, not a missing line.
        for attr, value in (
            ("max_retries", 6),
            ("timeout_s", 30.0),
            ("deadline_s", 180.0),
            ("max_retry_wait_s", 60.0),  # honour the API's own retry-after on a 429
        ):
            if hasattr(eng, attr):
                setattr(eng, attr, value)
        terms = load_glossary(glossary, settings)
        segmenter = Segmenter(
            SegmenterConfig(
                sample_rate=settings.sample_rate,
                min_segment_ms=settings.min_segment_ms,
                max_segment_ms=settings.max_segment_ms,
                silence_ms=settings.silence_ms,
                preroll_ms=settings.preroll_ms,
                partials=False,
            )
        )
        segments = []
        # Not realtime: a recording can be decoded as fast as the disk allows.
        async for chunk in FFmpegSource(str(file), settings.sample_rate, realtime=False).stream():
            segments += segmenter.feed(chunk)
        tail = segmenter.flush()
        if tail:
            segments.append(tail)
        console.print(f"{len(segments)} utterances found in {file.name}")

        captions: list[Caption] = []
        recent: list[str] = []
        failures = 0
        for i, seg in enumerate(segments, start=1):
            try:
                result = await eng.transcribe(
                    TranscribeRequest(
                        pcm=seg.pcm,
                        sample_rate=settings.sample_rate,
                        source_lang=source_lang,
                        targets=targets,
                        context="\n".join(recent[-3:]),
                        glossary=terms,
                    )
                )
            except Exception as exc:
                failures += 1
                console.print(f"  [red]{i:>3}/{len(segments)} failed:[/red] {str(exc)[:110]}")
                continue
            if result.is_empty:
                continue
            caption = Caption(
                session_id=file.stem,
                seq=len(captions),
                t_start=round(seg.t_start + offset, 2),
                t_end=round(seg.t_end + offset, 2),
                source_lang=result.source_lang,
                texts=result.texts,
                engine=eng.name,
                model=result.model,
            )
            captions.append(caption)
            recent.append(caption.text_for(caption.source_lang))
            shown = caption.text_for(targets[0]) if targets else caption.text_for(source_lang)
            stamp = f"[dim]{caption.t_start:7.1f}s[/dim]"
            console.print(f"  {i:>3}/{len(segments)} {stamp} {shown[:90]}")
        await eng.aclose()
        return captions, failures

    captions, failures = asyncio.run(_run())
    if not captions:
        console.print("[red]No speech was transcribed. Nothing written.[/red]")
        raise typer.Exit(1)

    langs = list(dict.fromkeys([captions[0].source_lang, *targets]))
    for lang in langs:
        for fmt in wanted:
            path = out_dir / f"{file.stem}.{lang}.{fmt}"
            path.write_text(exporters.export(captions, lang, fmt), encoding="utf-8")
            console.print(f"[green]wrote[/green] {path}")
    if failures:
        console.print(
            f"[yellow]{failures} utterance(s) failed and are missing. Re-run to retry.[/yellow]"
        )
    console.print("[dim]Read the file before you publish it: fix any name it got wrong, "
                  "then add that name to the glossary so it never happens again.[/dim]")


# -------------------------------------------------------------------- doctor


@app.command()
def doctor(
    live: bool = typer.Option(
        False,
        "--live",
        help="Also make one real model call: proves the key works and shows your quota",
    ),
) -> None:
    """Check this machine can run an event. Run it the day before, not during."""
    settings = get_settings()
    rows: list[tuple[str, bool, str]] = []

    rows.append(("python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0]))
    ff = shutil.which("ffmpeg")
    rows.append(("ffmpeg on PATH", bool(ff), ff or "missing — audio ingest will not work"))
    rows.append(
        (
            "yt-dlp (optional)",
            bool(shutil.which("yt-dlp")),
            shutil.which("yt-dlp") or "only needed to caption YouTube",
        )
    )

    engine = settings.engine
    if engine == "gemini":
        try:
            import google.genai  # noqa: F401

            sdk = True
        except ImportError:
            sdk = False
        hint = "ok" if sdk else "pip install 'cotorra[gemini]'"
        rows.append(("google-genai installed", sdk, hint))
        if settings.gemini_use_vertex:
            rows.append((
                "Vertex AI project set",
                bool(settings.gcp_project),
                settings.gcp_project or "set GCP_PROJECT in .env",
            ))
            explicit = settings.google_application_credentials
            found = settings.has_google_credentials()
            rows.append((
                "Google Cloud credentials",
                found,
                explicit
                or ("application default credentials" if found else
                    "set GOOGLE_APPLICATION_CREDENTIALS, or run "
                    "`gcloud auth application-default login`"),
            ))
        else:
            rows.append((
                "GEMINI_API_KEY set",
                bool(settings.gemini_api_key),
                "set it in .env, or use GEMINI_USE_VERTEX=true"
                if not settings.gemini_api_key
                else "ok (AI Studio: free tier is ~15-20 requests/day)",
            ))
    elif engine == "local":
        try:
            import faster_whisper  # noqa: F401

            sdk = True
        except ImportError:
            sdk = False
        rows.append(("faster-whisper installed", sdk, "pip install 'cotorra[local]'"))
        try:
            ok = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3).status_code == 200
        except httpx.HTTPError:
            ok = False
        rows.append(("ollama reachable", ok, settings.ollama_url))

    writable = os.access(settings.data_dir.parent, os.W_OK)
    rows.append(("data dir writable", writable, str(settings.data_dir)))
    rows.append(
        (
            "admin token set",
            bool(settings.admin_token),
            "empty = unauthenticated control API (fine locally, not in a venue)",
        )
    )
    specs = load_manifest()
    rows.append((f"manifest: {settings.manifest.name}", bool(specs), f"{len(specs)} session(s)"))

    has_credentials = settings.gemini_api_key or (
        settings.gemini_use_vertex and settings.gcp_project
    )
    if live and engine == "gemini" and has_credentials:
        ok, detail = _probe_gemini(settings)
        rows.append((f"live call: {settings.gemini_model}", ok, detail))
    elif engine == "gemini" and has_credentials:
        rows.append((
            "live call (optional)",
            True,
            "run `cotorra doctor --live` to prove the key works and see your quota",
        ))

    table = Table(title="cotorra doctor", header_style="bold")
    table.add_column("check")
    table.add_column("")
    table.add_column("detail", overflow="fold")
    failures = 0
    for name, ok, detail in rows:
        optional = "optional" in name
        if not ok and not optional:
            failures += 1
        # Text() so hints like "pip install 'cotorra[gemini]'" are not eaten as rich markup
        table.add_row(name, "[green]OK[/green]" if ok else "[red]--[/red]", Text(detail))
    console.print(table)
    if failures:
        console.print(f"[red]{failures} check(s) need attention before the event.[/red]")
        raise typer.Exit(1)
    console.print("[green]Ready.[/green]")


def _probe_gemini(settings) -> tuple[bool, str]:
    """One real transcription call against one second of silence.

    Cheaper than any other way of answering the two questions that actually
    matter the night before an event: does this key work at all, and how much
    quota does it have? A free-tier key allows on the order of 15-20 requests
    per day per model, which is fine for kicking the tyres and nowhere near
    enough to caption a talk — and that is far better to learn now.
    """
    import time

    from .engines import build_engine
    from .engines.base import TranscribeRequest
    from .engines.gemini import _summarise

    try:
        # Through the factory on purpose: that is what a worker uses, so the
        # probe exercises the same credential path instead of a parallel one.
        engine = build_engine("gemini", settings)
        engine.max_retries = 1
        started = time.time()
        asyncio.run(
            engine.transcribe(
                TranscribeRequest(
                    pcm=b"\x00" * (settings.sample_rate * 2),
                    sample_rate=settings.sample_rate,
                    source_lang="en",
                    targets=["es"],
                )
            )
        )
        backend = "Vertex AI" if settings.gemini_use_vertex else "AI Studio"
        return True, f"{backend} ok, {int((time.time() - started) * 1000)} ms round trip"
    except Exception as exc:
        return False, _summarise(exc)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
