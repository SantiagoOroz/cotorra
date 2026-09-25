"""Session registry + local worker supervision.

This is the piece that turns "a script that captions a talk" into "software an
AV crew can run an event on":

* it knows every stage and its state,
* it spawns, restarts and reaps worker processes,
* it notices dead air and dead workers before the audience does,
* it keeps the schedule on disk so a gateway restart mid-event is a non-event.

``LocalSupervisor`` runs workers as child processes, which is what you want on
one machine or inside one container. For Kubernetes, swap it for a supervisor
that creates Pods — the interface is four methods and the rest of the system
does not care. See docs/deploy.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import Settings
from .hub import Hub
from .models import Caption, SessionMetrics, SessionSpec, SessionStatus
from .store import TranscriptStore

log = logging.getLogger(__name__)


class SupervisorError(RuntimeError):
    pass


class LocalSupervisor:
    """Runs one ``cotorra worker`` subprocess per session."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.procs: dict[str, subprocess.Popen] = {}

    def gateway_url(self) -> str:
        return self.settings.worker_gateway_url or f"ws://127.0.0.1:{self.settings.port}"

    def running(self, session_id: str) -> bool:
        proc = self.procs.get(session_id)
        return proc is not None and proc.poll() is None

    def spawn(self, spec: SessionSpec, seq_base: int = 0, t_base: float = 0.0) -> None:
        if self.running(spec.id):
            return
        live = sum(1 for p in self.procs.values() if p.poll() is None)
        if live >= self.settings.max_local_workers:
            raise SupervisorError(
                f"Refusing to start '{spec.id}': {live} workers already running on this host "
                f"(MAX_LOCAL_WORKERS={self.settings.max_local_workers}). "
                "Scale out with more gateway hosts or the Kubernetes supervisor."
            )
        env = os.environ.copy()
        env["COTORRA_SESSION_SPEC"] = spec.model_dump_json()
        # Where this worker's numbering and clock continue from. See
        # TranscriptStore.tail() for why a restarted worker cannot start at 0.
        env["COTORRA_SEQ_BASE"] = str(seq_base)
        env["COTORRA_T_BASE"] = f"{t_base:.2f}"
        env["PYTHONUNBUFFERED"] = "1"
        cmd = [
            sys.executable,
            "-m",
            "cotorra",
            "worker",
            "--gateway",
            self.gateway_url(),
            "--token",
            self.settings.worker_token,
            "--concurrency",
            str(self.settings.worker_concurrency),
        ]
        log.info("Spawning worker for %s: %s", spec.id, " ".join(cmd))
        self.procs[spec.id] = subprocess.Popen(cmd, env=env, cwd=str(Path.cwd()))

    def kill(self, session_id: str) -> bool:
        """Crash a worker on purpose, and leave the body for the watchdog.

        Unlike stop(), the process stays registered, so reap() reports a
        non-zero exit and the real crash-recovery path runs — the same one a
        segfault or an out-of-memory kill would take during an event.
        """
        proc = self.procs.get(session_id)
        if proc is None or proc.poll() is not None:
            return False
        if sys.platform == "win32":
            self._kill_tree(proc.pid)
        else:
            proc.kill()
        return True

    def wait(self, session_id: str, timeout: float) -> bool:
        """Wait for a worker to exit on its own. True if it did."""
        proc = self.procs.get(session_id)
        if proc is None:
            return True
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        self.procs.pop(session_id, None)
        return True

    def stop(self, session_id: str, timeout: float = 8.0) -> None:
        """Force a worker down. Prefer :meth:`SessionManager.stop`, which asks first."""
        proc = self.procs.pop(session_id, None)
        if proc is None or proc.poll() is not None:
            return
        if sys.platform == "win32":
            # Windows has no SIGTERM, and TerminateProcess does not touch
            # children. A venv's python.exe can be a launcher that re-execs the
            # real interpreter, so killing only the parent would leave an
            # orphan still calling the model API — and still billing for it.
            self._kill_tree(proc.pid)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("Worker %s did not exit, killing", session_id)
            proc.kill()

    @staticmethod
    def _kill_tree(pid: int) -> None:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
            log.warning("taskkill failed for pid %s: %s", pid, exc)

    def reap(self) -> list[tuple[str, int]]:
        """Return ``(session_id, returncode)`` for workers that have exited."""
        dead = []
        for session_id, proc in list(self.procs.items()):
            rc = proc.poll()
            if rc is not None:
                dead.append((session_id, rc))
                self.procs.pop(session_id, None)
        return dead

    def stop_all(self) -> None:
        for session_id in list(self.procs):
            self.stop(session_id, timeout=3)


class SessionManager:
    """The authoritative view of the event."""

    def __init__(
        self, settings: Settings, hub: Hub, store: TranscriptStore, supervisor: LocalSupervisor
    ) -> None:
        self.settings = settings
        self.hub = hub
        self.store = store
        self.supervisor = supervisor
        self.sessions: dict[str, SessionStatus] = {}
        self.worker_sockets: dict[str, object] = {}
        self._restarts: dict[str, int] = {}
        self._intentional_stop: set[str] = set()
        self._watchdog: asyncio.Task | None = None
        self._state_file = settings.data_dir / "sessions.json"

    # -- the link to each worker -------------------------------------------

    def register_worker(self, session_id: str, socket: object) -> None:
        self.worker_sockets[session_id] = socket

    def unregister_worker(self, session_id: str, socket: object) -> None:
        if self.worker_sockets.get(session_id) is socket:
            self.worker_sockets.pop(session_id, None)

    async def request_shutdown(self, session_id: str) -> bool:
        """Ask a worker to wind down cleanly. False if we could not reach it."""
        socket = self.worker_sockets.get(session_id)
        if socket is None:
            return False
        try:
            await socket.send_text(json.dumps({"type": "shutdown"}))
            return True
        except Exception as exc:
            log.warning("Could not ask %s to shut down: %s", session_id, exc)
            return False

    # -- registry ----------------------------------------------------------

    def upsert(self, spec: SessionSpec) -> SessionStatus:
        existing = self.sessions.get(spec.id)
        if existing:
            existing.spec = spec
        else:
            self.sessions[spec.id] = SessionStatus(spec=spec)
        self.persist()
        return self.sessions[spec.id]

    def get(self, session_id: str) -> SessionStatus | None:
        return self.sessions.get(session_id)

    def require(self, session_id: str) -> SessionStatus:
        status = self.sessions.get(session_id)
        if status is None:
            raise KeyError(session_id)
        return status

    def list(self) -> list[SessionStatus]:
        for status in self.sessions.values():
            status.viewers = self.hub.viewers(status.spec.id)
        return sorted(self.sessions.values(), key=lambda s: (s.spec.stage, s.spec.id))

    async def remove(self, session_id: str) -> None:
        await self.stop(session_id)
        self.sessions.pop(session_id, None)
        self._restarts.pop(session_id, None)
        self.persist()

    # -- lifecycle ---------------------------------------------------------

    async def start(self, session_id: str, restart_transcript: bool = False) -> SessionStatus:
        status = self.require(session_id)
        if status.state in ("starting", "running", "degraded"):
            return status
        if restart_transcript:
            self.store.clear(session_id)
        self._intentional_stop.discard(session_id)
        self._restarts[session_id] = 0
        self._spawn(status, crashed=False)
        status.state = "starting"
        status.started_at = time.time()
        status.stopped_at = None
        status.last_error = None
        status.metrics = SessionMetrics()
        await self._announce(status)
        return status

    async def stop(self, session_id: str, grace: float = 8.0) -> SessionStatus | None:
        """Ask nicely, then insist.

        The polite path lets the worker publish the captions still in flight,
        so a talk that is stopped mid-sentence keeps its last line instead of
        losing it.
        """
        status = self.sessions.get(session_id)
        if status is None:
            return None
        self._intentional_stop.add(session_id)

        if await self.request_shutdown(session_id):
            stopped = await asyncio.to_thread(self.supervisor.wait, session_id, grace)
            if not stopped:
                log.warning("Worker %s ignored the shutdown request; forcing", session_id)
                await asyncio.to_thread(self.supervisor.stop, session_id)
        else:
            await asyncio.to_thread(self.supervisor.stop, session_id)

        status.state = "stopped"
        status.stopped_at = time.time()
        await self._announce(status)
        return status

    def _spawn(self, status: SessionStatus, crashed: bool) -> None:
        """Start a worker that continues the session's numbering and clock."""
        next_seq, last_t = self.store.tail(status.spec.id)
        if next_seq == 0:
            t_base = 0.0
        elif crashed and status.started_at:
            # A live feed kept going while the worker was down, so the session
            # clock is wall time since the start — never behind the last line.
            t_base = max(last_t + 0.5, time.time() - status.started_at)
        else:
            t_base = last_t + 1.0
        self.supervisor.spawn(status.spec, seq_base=next_seq, t_base=t_base)

    async def simulate_crash(self, session_id: str) -> SessionStatus:
        """Fire drill: kill the worker the hard way and let recovery happen.

        Worth rehearsing before doors open. It exercises exactly the path a
        real crash takes, which is the only way to know the path works.
        """
        status = self.require(session_id)
        self._intentional_stop.discard(session_id)
        if not await asyncio.to_thread(self.supervisor.kill, session_id):
            raise SupervisorError(f"'{session_id}' has no running worker to crash")
        log.warning("Simulated crash of %s", session_id)
        return status

    async def start_autostart(self) -> None:
        for status in list(self.sessions.values()):
            if status.spec.autostart:
                try:
                    await self.start(status.spec.id)
                except SupervisorError as exc:
                    log.error("%s", exc)

    async def shutdown(self) -> None:
        """Ctrl-C on the gateway must not leave workers running."""
        if self._watchdog:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
        live = [sid for sid in list(self.sessions) if self.supervisor.running(sid)]
        for session_id in live:
            self._intentional_stop.add(session_id)
            await self.request_shutdown(session_id)
        # Give them all one grace period in parallel rather than one each.
        if live:
            await asyncio.gather(
                *(asyncio.to_thread(self.supervisor.wait, sid, 5.0) for sid in live),
                return_exceptions=True,
            )
        await asyncio.to_thread(self.supervisor.stop_all)

    # -- worker messages ---------------------------------------------------

    async def on_worker_message(self, session_id: str, message: dict) -> None:
        status = self.sessions.get(session_id)
        if status is None:
            log.warning("Message for unknown session %s", session_id)
            return
        kind = message.get("type")

        if kind == "caption":
            caption = Caption(**message["caption"])
            self.store.append(caption)
            status.last_caption_at = time.time()
            if status.state in ("starting", "degraded", "error"):
                status.state = "running"
                status.last_error = None
            await self.hub.publish(session_id, {"type": "caption", "caption": caption.model_dump()})
            # The ops dashboard gets a light preview rather than every caption
            # in every language: 10 stages x 2 languages adds up fast.
            if caption.kind == "final":
                await self.hub.publish(
                    "*",
                    {
                        "type": "preview",
                        "session_id": session_id,
                        "text": caption.text_for(caption.source_lang)[:140],
                        "latency_ms": caption.latency_ms,
                    },
                )

        elif kind == "heartbeat":
            status.metrics = SessionMetrics(**message.get("metrics", {}))
            status.last_heartbeat_at = time.time()
            reported = message.get("state")
            if reported in ("running", "degraded", "error", "starting"):
                status.state = reported
            if reported == "running":
                # Recovered. Leaving a stale red banner on the ops panel after
                # a session healed is how operators learn to ignore the panel.
                status.last_error = None
                self._restarts[session_id] = 0
            else:
                status.last_error = message.get("last_error") or status.last_error
            await self._announce(status)

        elif kind == "hello":
            log.info("Worker %s online (pid %s)", session_id, message.get("pid"))
            status.state = "starting"
            await self._announce(status)

        elif kind == "bye":
            state = message.get("state", "finished")
            status.state = "finished" if state == "finished" else state
            status.stopped_at = time.time()
            if state == "error":
                status.last_error = message.get("reason")
            self._intentional_stop.add(session_id)  # a clean exit is not a crash
            await self._announce(status)

    # -- watchdog ----------------------------------------------------------

    def start_watchdog(self) -> None:
        self._watchdog = asyncio.create_task(self._watch(), name="watchdog")

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                await self._watch_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("Watchdog iteration failed")

    async def _watch_once(self) -> None:
        now = time.time()
        for session_id, rc in self.supervisor.reap():
            status = self.sessions.get(session_id)
            if status is None:
                continue
            if session_id in self._intentional_stop or status.state in ("finished", "stopped"):
                continue
            # The worker died on its own: that is an incident, not a state change.
            count = self._restarts.get(session_id, 0)
            if count < self.settings.worker_restarts:
                self._restarts[session_id] = count + 1
                delay = min(2**count, 30)
                status.state = "degraded"
                status.last_error = f"Worker exited with code {rc}; restart {count + 1} in {delay}s"
                log.warning("%s: %s", session_id, status.last_error)
                await self._announce(status)
                asyncio.create_task(self._delayed_restart(session_id, delay))
            else:
                status.state = "error"
                status.last_error = (
                    f"Worker exited with code {rc} and exhausted "
                    f"{self.settings.worker_restarts} restarts"
                )
                log.error("%s: %s", session_id, status.last_error)
                await self._announce(status)

        for status in self.sessions.values():
            if status.state not in ("running", "degraded"):
                continue
            hb = status.last_heartbeat_at
            if hb and now - hb > self.settings.heartbeat_timeout_s:
                status.state = "error"
                status.last_error = f"No heartbeat for {int(now - hb)}s"
                await self._announce(status)
                continue
            # Dead air: the feed is connected but nothing is coming through.
            # In a real venue this is a pulled cable or a muted desk channel.
            last_audio = status.last_caption_at or status.started_at or now
            quiet_for = now - last_audio
            if (
                quiet_for > self.settings.silence_alarm_s
                and status.metrics.audio_level < 0.002
                and status.state == "running"
            ):
                status.state = "degraded"
                status.last_error = f"No audio for {int(quiet_for)}s — check the feed"
                await self._announce(status)

    async def _delayed_restart(self, session_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        status = self.sessions.get(session_id)
        if status is None or session_id in self._intentional_stop:
            return
        try:
            self._spawn(status, crashed=True)
            status.state = "starting"
            await self._announce(status)
        except SupervisorError as exc:
            status.state = "error"
            status.last_error = str(exc)
            await self._announce(status)

    # -- plumbing ----------------------------------------------------------

    async def _announce(self, status: SessionStatus) -> None:
        status.viewers = self.hub.viewers(status.spec.id)
        payload = {"type": "status", "status": json.loads(status.model_dump_json())}
        await self.hub.publish(status.spec.id, payload)
        await self.hub.publish("*", payload)  # the ops dashboard watches everything

    def persist(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps([s.spec.model_dump() for s in self.sessions.values()], indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not persist session list: %s", exc)

    def restore(self) -> int:
        if not self._state_file.exists():
            return 0
        try:
            specs = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not restore session list: %s", exc)
            return 0
        for raw in specs:
            with contextlib.suppress(Exception):
                spec = SessionSpec(**raw)
                self.sessions.setdefault(spec.id, SessionStatus(spec=spec))
        return len(specs)
