#!/usr/bin/env python3
"""Download real conference talks to test against.

    python scripts/fetch_samples.py                  # the two Nerdearla talks below
    python scripts/fetch_samples.py URL --id my-talk --minutes 10

Talk recordings are not committed to the repo: they are large, and they belong
to the people who gave them. This pulls the audio locally and prints the
session entries to paste into cotorra.yaml.

Needs yt-dlp and ffmpeg:  pip install -U yt-dlp
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "samples"

# Two real talks from Nerdearla's own channel, one per language direction.
# They are the ones used in the demo video, so anyone can reproduce it exactly.
CURATED = [
    {
        "id": "nerdearla-mcp-en",
        "url": "https://www.youtube.com/watch?v=iaG9pHMJ3Y4",
        "lang": "en",
        "targets": ["es", "pt"],
        "title": "Model Context Protocol in Plain English — Nate Barbettini",
        "skip": 120,  # past the intro
    },
    {
        "id": "nerdearla-narrativa-es",
        "url": "https://www.youtube.com/watch?v=Z2EHHfyAyC4",
        "lang": "es",
        "targets": ["en"],
        "title": "Del código a la narrativa — Abigail Carmio",
        "skip": 90,
    },
]


def need(binary: str, hint: str = "") -> str:
    """Prefer the copy in this virtualenv: the one on PATH is often stale."""
    local = Path(sys.executable).parent / binary
    for candidate in (str(local), binary):
        found = shutil.which(candidate)
        if found:
            return found
    sys.exit(f"'{binary}' is not available. {hint}")


def download(url: str, out: Path, skip: int, minutes: int) -> bool:
    """Full download, then trim locally.

    yt-dlp's own ``--download-sections`` hands the media URL to ffmpeg, which
    does not carry the headers the URL was issued for — YouTube answers 403.
    Downloading with yt-dlp and cutting afterwards is slower but it works.
    """
    if out.exists():
        print(f"  already have {out.name}")
        return True

    ytdlp = need("yt-dlp", "Install it with: pip install -U yt-dlp")
    raw = out.with_name(out.stem + ".raw.m4a")

    if not raw.exists():
        print(f"  downloading {url}")
        result = subprocess.run(
            [ytdlp, "-f", "bestaudio[ext=m4a]/bestaudio", "--no-playlist",
             "-o", str(raw.with_suffix("").with_suffix("")) + ".raw.%(ext)s", url],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()
            print(f"  FAILED: {tail[-1] if tail else 'unknown error'}")
            if "403" in (result.stderr or ""):
                print("  A 403 usually means yt-dlp is out of date: pip install -U yt-dlp")
            return False
        # yt-dlp may pick a different container than m4a.
        if not raw.exists():
            found = sorted(out.parent.glob(out.stem + ".raw.*"))
            if not found:
                print("  FAILED: yt-dlp reported success but wrote no file")
                return False
            raw = found[0]

    print(f"  trimming {minutes} min from {skip}s and resampling to 16 kHz mono")
    ffmpeg = need("ffmpeg", "Install it from https://ffmpeg.org/download.html")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(skip)]
    if minutes:
        cmd += ["-t", str(minutes * 60)]
    cmd += ["-i", str(raw), "-ac", "1", "-ar", "16000",
            "-c:a", "libopus", "-b:a", "24k", "-y", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FAILED to trim: {(result.stderr or '').strip()[:200]}")
        return False

    raw.unlink(missing_ok=True)
    print(f"  wrote {out.name} ({out.stat().st_size / 1e6:.1f} MB)")
    return True


def download_poster(url: str, out: Path) -> bool:
    """The talk's thumbnail, used by /demo as the picture behind the captions.

    Served from YouTube's image CDN, which does not apply the bot check that
    video downloads now hit, so this works even when --video does not.
    """
    if out.exists():
        return True
    match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", url)
    if not match:
        return False
    for quality in ("maxresdefault", "hqdefault"):
        try:
            with urllib.request.urlopen(
                f"https://i.ytimg.com/vi/{match.group(1)}/{quality}.jpg", timeout=20
            ) as resp:
                out.write_bytes(resp.read())
            print(f"  wrote {out.name}")
            return True
        except OSError:
            continue
    return False


COOKIES_FROM: str | None = None


def download_video(url: str, out: Path, skip: int, minutes: int) -> bool:
    """The talk's video, cut to exactly the same window as its audio.

    /demo plays this under the live captions. Both files are cut from the same
    original at the same offset, and the video is re-encoded rather than
    stream-copied: a stream copy can only cut on a keyframe, which would put
    the picture a few seconds away from the audio Cotorra is transcribing.
    """
    if out.exists():
        print(f"  already have {out.name}")
        return True
    ytdlp = need("yt-dlp", "Install it with: pip install -U yt-dlp")
    ffmpeg = need("ffmpeg", "Install it from https://ffmpeg.org/download.html")
    stem = out.with_name(out.stem + ".raw-video")

    found = sorted(out.parent.glob(stem.name + ".*"))
    if not found:
        print(f"  downloading video {url}")
        cmd = [ytdlp, "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/b",
               "--merge-output-format", "mp4", "--no-playlist",
               "-o", str(stem) + ".%(ext)s", url]
        if COOKIES_FROM:
            cmd[1:1] = ["--cookies-from-browser", COOKIES_FROM]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = result.stderr or ""
            tail = err.strip().splitlines()
            print(f"  FAILED: {tail[-1][:160] if tail else 'unknown error'}")
            if "not a bot" in err:
                print("  YouTube wants a signed-in session for video downloads from this")
                print("  network. /demo still works with the audio and the thumbnail. For the")
                print("  real video, re-run with your own browser session:")
                print("    python scripts/fetch_samples.py --video --cookies-from-browser chrome")
            return False
        found = sorted(out.parent.glob(stem.name + ".*"))
        if not found:
            print("  FAILED: yt-dlp reported success but wrote no file")
            return False

    print(f"  cutting the same {minutes} min window from {skip}s (re-encode, frame accurate)")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(skip)]
    if minutes:
        cmd += ["-t", str(minutes * 60)]
    cmd += ["-i", str(found[0]),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",  # playable and seekable before it fully loads
            "-y", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FAILED to cut: {(result.stderr or '').strip()[:200]}")
        return False
    found[0].unlink(missing_ok=True)
    print(f"  wrote {out.name} ({out.stat().st_size / 1e6:.1f} MB)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", nargs="?", help="A single video URL instead of the curated set")
    ap.add_argument("--id", default="my-talk")
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--targets", default="es")
    ap.add_argument("--minutes", type=int, default=10, help="0 keeps the whole talk")
    ap.add_argument("--skip", type=int, default=0, help="Seconds to drop from the start")
    ap.add_argument(
        "--video", action="store_true",
        help="Also fetch the video, cut to the same window, for the /demo page",
    )
    ap.add_argument(
        "--cookies-from-browser", metavar="BROWSER",
        help="Use your own browser's YouTube session (chrome, edge, firefox) if "
             "YouTube asks to confirm you are not a bot",
    )
    args = ap.parse_args()
    global COOKIES_FROM
    COOKIES_FROM = args.cookies_from_browser

    SAMPLES.mkdir(exist_ok=True)

    if args.url:
        wanted = [{
            "id": args.id, "url": args.url, "lang": args.lang,
            "targets": args.targets.split(","), "title": args.id, "skip": args.skip,
        }]
    else:
        wanted = CURATED

    entries = []
    for item in wanted:
        print(f"\n{item['id']}:")
        out = SAMPLES / f"{item['id']}.ogg"
        skip = item.get("skip", args.skip)
        if download(item["url"], out, skip, args.minutes):
            entries.append((item, out))
            download_poster(item["url"], out.with_suffix(".jpg"))
            if args.video:
                download_video(item["url"], out.with_suffix(".mp4"), skip, args.minutes)

    if not entries:
        return 1

    print("\n" + "=" * 68)
    print("Paste into cotorra.yaml under `sessions:`\n")
    for item, out in entries:
        print(f"  - id: {item['id']}")
        print(f"    title: \"{item['title']}\"")
        print(f"    source: ./samples/{out.name}")
        print(f"    source_lang: {item['lang']}")
        print(f"    targets: [{', '.join(item['targets'])}]")
        print("    engine: gemini")
        print("    glossary: nerdearla")
        print("    autostart: true")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
