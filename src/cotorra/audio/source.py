"""Audio ingest: anything ffmpeg can open becomes 16 kHz mono PCM.

One code path for every input a conference can throw at us:

====================  =========================================================
``talk.mp3``          a file (replayed at real time, so demos behave like live)
``https://…/x.m3u8``  the HLS output of the streaming rig
``rtmp://…``          what OBS / vMix push
``srt://…``           what a hardware encoder pushes
``https://youtu.be/…``a YouTube stream or VOD (needs ``yt-dlp``)
``mic``               the default system input
``device:Yeti``       a named capture device
====================  =========================================================
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

log = logging.getLogger(__name__)

READ_SIZE = 3200  # 100 ms at 16 kHz mono s16le


class AudioSourceError(RuntimeError):
    pass


def _mic_args(device: str | None) -> list[str]:
    """Platform-specific capture flags, because every OS names inputs its own way."""
    if sys.platform == "win32":
        name = device or "default"
        return ["-f", "dshow", "-i", f"audio={name}"]
    if sys.platform == "darwin":
        return ["-f", "avfoundation", "-i", f":{device or '0'}"]
    if device:
        return ["-f", "pulse", "-i", device]
    return ["-f", "alsa", "-i", "default"]


async def _resolve_youtube(url: str) -> str:
    """Ask yt-dlp for a direct audio URL ffmpeg can read."""
    if not shutil.which("yt-dlp"):
        raise AudioSourceError(
            "YouTube sources need yt-dlp on PATH (pip install yt-dlp). "
            "Alternatively download the audio first and point --source at the file."
        )
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "-f",
        "bestaudio/best",
        "-g",
        "--no-warnings",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0 or not out.strip():
        raise AudioSourceError(f"yt-dlp failed for {url}: {err.decode(errors='replace')[:400]}")
    return out.decode().strip().splitlines()[0]


def is_live(source: str) -> bool:
    return source.split("://", 1)[0] in {"rtmp", "rtmps", "srt", "udp", "rtp", "rtsp"} or (
        source.startswith("http") and ".m3u8" in source
    )


async def build_ffmpeg_command(
    source: str, sample_rate: int = 16000, realtime: bool = True
) -> list[str]:
    """Translate a Cotorra source string into an ffmpeg argv.

    ``realtime=False`` decodes a file as fast as the disk allows, for offline
    subtitling where nobody is waiting on a live caption.
    """
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    pre: list[str] = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    inp: list[str]

    if source == "mic":
        inp = _mic_args(None)
    elif source.startswith("device:"):
        inp = _mic_args(source.split(":", 1)[1])
    elif "youtube.com" in source or "youtu.be" in source:
        inp = ["-i", await _resolve_youtube(source)]
    elif source.startswith(("http://", "https://", "rtmp", "rtsp", "srt", "udp", "rtp", "file:")):
        if is_live(source):
            pre += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        inp = ["-i", source]
    else:
        path = Path(source)
        if not path.exists():
            raise AudioSourceError(f"Audio source not found: {source}")
        # -re paces a file at wall-clock speed: a recorded talk behaves
        # exactly like a live feed, which is what makes offline demos honest.
        if realtime:
            pre += ["-re"]
        inp = ["-i", str(path)]

    return pre + inp + [
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]


class FFmpegSource:
    """Async iterator of PCM chunks, with the stderr tail kept for diagnostics."""

    def __init__(
        self,
        source: str,
        sample_rate: int = 16000,
        loop_file: bool = False,
        realtime: bool = True,
    ) -> None:
        self.source = source
        self.sample_rate = sample_rate
        self.loop_file = loop_file
        self.realtime = realtime
        self.proc: asyncio.subprocess.Process | None = None
        self.stderr_tail: list[str] = []

    async def _spawn(self) -> asyncio.subprocess.Process:
        cmd = await build_ffmpeg_command(self.source, self.sample_rate, self.realtime)
        log.info("ffmpeg: %s", " ".join(cmd))
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode(errors="replace").strip()
            if text:
                log.warning("ffmpeg: %s", text)
                self.stderr_tail.append(text)
                del self.stderr_tail[:-10]

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield PCM until the source ends (or forever, when ``loop_file``)."""
        while True:
            proc = self.proc = await self._spawn()
            drain = asyncio.create_task(self._drain_stderr(proc))
            assert proc.stdout is not None
            try:
                while True:
                    chunk = await proc.stdout.read(READ_SIZE)
                    if not chunk:
                        break
                    yield chunk
            finally:
                drain.cancel()
                await self.aclose()

            rc = proc.returncode
            if rc not in (0, None) and not self.loop_file:
                raise AudioSourceError(
                    f"ffmpeg exited with {rc}: {' | '.join(self.stderr_tail) or 'no output'}"
                )
            if not self.loop_file:
                return
            log.info("Looping source %s", self.source)

    async def aclose(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            with_kill = getattr(proc, "kill", None)
            if with_kill:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
