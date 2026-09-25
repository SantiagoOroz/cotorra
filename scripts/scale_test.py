#!/usr/bin/env python3
"""Spin up N simultaneous sessions and report what the box actually did.

    python scripts/scale_test.py --sessions 10 --seconds 60

This is the number to quote when someone asks "does it scale?". It exercises
the real path — one ffmpeg and one worker process per session, real
segmentation, real websocket fan-out — and only the model call is stubbed, so
what it measures is the part that a conference operator owns. Cost is reported
using the configured Gemini rates, so the dollar figure is the one you would
actually have paid.

Run it against the mock engine (free, hermetic) to size a host, or against
``gemini`` with a small ``--sessions`` to sanity check real latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time

import httpx
import websockets


async def viewer(api: str, session_id: str, lang: str, stop: asyncio.Event, out: list) -> None:
    """One audience member, reading captions like a phone in the third row."""
    url = api.replace("http", "ws", 1) + f"/ws/captions/{session_id}?lang={lang}"
    try:
        async with websockets.connect(url) as ws:
            while not stop.is_set():
                try:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                except asyncio.TimeoutError:
                    continue
                if event.get("type") == "caption" and event["kind"] == "final":
                    out.append(event["latency_ms"])
    except Exception as exc:
        print(f"  viewer {session_id} died: {exc}", file=sys.stderr)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8080")
    ap.add_argument("--token", default="")
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument("--viewers-per-session", type=int, default=3)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--source", default="./samples/pipeline-test.ogg")
    ap.add_argument("--engine", default="mock")
    ap.add_argument("--prefix", default="scale")
    ap.add_argument("--keep", action="store_true", help="Do not delete the sessions afterwards")
    args = ap.parse_args()

    headers = {"X-Cotorra-Token": args.token} if args.token else {}
    ids = [f"{args.prefix}-{i:02d}" for i in range(args.sessions)]

    async with httpx.AsyncClient(base_url=args.api, headers=headers, timeout=30) as http:
        print(f"Creating {len(ids)} sessions…")
        for i, sid in enumerate(ids):
            body = {
                "id": sid,
                "title": f"Scale test stage {i}",
                "stage": f"S{i:02d}",
                "source": args.source,
                "source_lang": "en" if i % 2 == 0 else "es",
                "targets": ["es"] if i % 2 == 0 else ["en"],
                "engine": args.engine,
                "loop": True,
            }
            r = await http.post("/api/sessions", json=body)
            r.raise_for_status()

        t_spawn = time.time()
        for sid in ids:
            r = await http.post(f"/api/sessions/{sid}/start")
            if r.status_code == 409:
                print(f"  refused: {r.json()['detail']}")
            else:
                r.raise_for_status()
        print(f"All start requests accepted in {time.time() - t_spawn:.1f}s")

        stop = asyncio.Event()
        latencies: list[int] = []
        viewers = [
            asyncio.create_task(viewer(args.api, sid, "es", stop, latencies))
            for sid in ids
            for _ in range(args.viewers_per_session)
        ]
        print(f"{len(viewers)} simulated viewers connected. Measuring for {args.seconds}s…")

        for remaining in range(args.seconds, 0, -10):
            await asyncio.sleep(min(10, remaining))
            health = (await http.get("/api/health")).json()
            print(f"  t-{remaining:>3}s  states={health['states']}  captions={len(latencies)}")

        stop.set()
        await asyncio.gather(*viewers, return_exceptions=True)

        sessions = (await http.get("/api/sessions")).json()
        mine = [s for s in sessions if s["spec"]["id"].startswith(args.prefix)]
        running = sum(1 for s in mine if s["state"] == "running")
        captions = sum(s["metrics"]["captions"] for s in mine)
        errors = sum(s["metrics"]["errors"] for s in mine)
        audio = sum(s["metrics"]["audio_seconds"] for s in mine)
        cost = sum(s["metrics"]["cost_usd"] for s in mine)

        # ``audio`` is already summed across sessions, so it is expressed in
        # stage-hours: 10 stages captioned for 6 minutes is 1 stage-hour.
        stage_hours = audio / 3600

        print("\n" + "=" * 62)
        print(f"  sessions running      {running}/{len(mine)}")
        print(f"  captions produced     {captions}")
        print(f"  caption deliveries    {len(latencies)} "
              f"(each caption x {args.viewers_per_session} viewers)")
        print(f"  engine errors         {errors}")
        print(f"  audio processed       {audio / 60:.1f} min = {stage_hours:.2f} stage-hours")
        print(f"  viewer connections    {len(viewers)}")
        if latencies:
            ordered = sorted(latencies)
            print(f"  latency p50           {statistics.median(ordered):.0f} ms")
            print(f"  latency p95           {ordered[int(0.95 * (len(ordered) - 1))]} ms")
            print(f"  latency max           {max(ordered)} ms")
        print(f"  cost measured         ${cost:.4f}")
        if stage_hours:
            per_hour = cost / stage_hours
            print(f"  cost per stage-hour   ${per_hour:.3f}")
            print(f"  -> 30 talks x 1 h     ${per_hour * 30:.2f}")
        print("=" * 62)
        if args.engine == "mock":
            print("  (mock engine: latency is simulated; cost is what Gemini would have")
            print("   charged for this exact token volume at the configured rates)")

        if not args.keep:
            print("Cleaning up…")
            for sid in ids:
                await http.delete(f"/api/sessions/{sid}")

        return 0 if running == len(mine) and errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
