<div align="center">

# 🦜 Cotorra

**Live subtitles, in any language, for any conference.**

Real-time transcription and translation. Open source, multi-stage, and cheap:
**US$0.31 per hour of talk**, measured.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-140%20passing-brightgreen.svg)](tests/)
[![CI](https://github.com/SantiagoOroz/cotorra/actions/workflows/ci.yml/badge.svg)](https://github.com/SantiagoOroz/cotorra/actions/workflows/ci.yml)

🇦🇷 **English** · [Español](README.md)

[60-second start](#-60-second-start) ·
[Architecture](#-architecture) ·
[Scaling](#-scaling-to-30-stages) ·
[Cost](#-what-it-actually-costs) ·
[OBS](#-obs--vmix) ·
[Contributing](CONTRIBUTING.md)

*A "cotorra" is a parrot: it hears you and says it back. This one says it back in
every language your audience reads.*

</div>

---

## The problem

Nerdearla runs **30+ sessions in English**, many of them at the same time. Today that is
solved with two different commercial tools: one transcribes Spanish→Spanish, another
translates English→Spanish live. It works, but **it is expensive, it needs a human
operator per stage, and it cannot be replicated at another conference**.

Almost every conference has exactly this problem and none of them has an open solution.

| | Typical commercial setup | Cotorra |
|---|---|---|
| Cost per hour of talk | US$50 – 250 | **US$0.31** |
| Concurrent stages | one per licence/operator | **bounded by CPU, not by licences** |
| Operation during the event | manual, per stage | **one dashboard, traffic lights, self-healing** |
| Reusable at another event | no | **`docker compose up`** |
| Licence | proprietary | **Apache 2.0** |

---

## ⚡ 60-second start

```bash
git clone https://github.com/SantiagoOroz/cotorra.git && cd cotorra
docker compose up
```

Open **<http://localhost:8080>**. Two stages are already captioning the bundled sample
audio, in parallel.

> **No credentials yet.** It boots with the `mock` engine, which walks the entire pipeline
> (ffmpeg → segmentation → fan-out → UI → export) using scripted text. Every screen shows a
> "demo mode" banner so nobody mistakes it for real transcription. It is also how the load
> test runs for free.

### With real transcription

Get a key at **<https://aistudio.google.com/apikey>**, then:

```bash
cp .env.example .env
# edit .env: COTORRA_ENGINE=gemini and GEMINI_API_KEY=...
docker compose up
```

That is the whole setup. No database to migrate, no message queue to stand up, no frontend
build. One container.

> ### ⚠️ A free key is not enough to run an event
>
> The Gemini API free tier allows on the order of **15 to 20 requests per day per model**.
> A single one-hour talk needs between 400 and 900. Measured, not guessed: the quota runs
> out in under two minutes of captioning.
>
> ```
> Free-tier quota exhausted on gemini-3.5-flash-lite (15 requests/day).
> Enable billing, or set GEMINI_MODEL to a model whose quota is still free.
> ```
>
> Cotorra detects this, boils it down to one actionable line and turns the stage red on the
> dashboard instead of failing silently. But the fix is a billing page, not a code change.
> **Enable billing**: captioning all 30 talks costs under US$10.
>
> To try it for free use `COTORRA_ENGINE=mock` (full pipeline, scripted text) or
> `COTORRA_ENGINE=local` (Whisper + Gemma on your own machine, no quotas).
> Check your key and your quota **before** the event with `cotorra doctor --live`.


### Without Docker

```bash
pip install -e ".[gemini]"      # or ".[local]" for fully offline
cotorra doctor                  # checks ffmpeg, credentials and the manifest
cotorra serve
```

### Try it on a real talk

```bash
python scripts/fetch_samples.py    # downloads conference talks from YouTube
cotorra serve
```

Or point straight at YouTube without downloading anything:

```bash
curl -X POST localhost:8080/api/sessions -H 'Content-Type: application/json' -d '{
  "id":"talk","title":"An English talk","source":"https://www.youtube.com/watch?v=VIDEO_ID",
  "source_lang":"en","targets":["es"],"engine":"gemini"}'
curl -X POST localhost:8080/api/sessions/talk/start
```

---

## 🏗 Architecture

```
                                       ┌──────────────────────────────┐
  Stage 1  ──RTMP/HLS/SRT──────┐       │  AUDIENCE                    │
  Stage 2  ──YouTube───────────┤       │  /viewer?session=X&lang=es   │
  Stage N  ──mic / file────────┤       │  everyone's own phone        │
                               │       └──────────────┬───────────────┘
                               ▼                      │ WebSocket
                    ┌──────────────────┐              │
                    │  WORKER (one per │              │
                    │  stage)          │     ┌────────┴────────┐
                    │                  │     │                 │
                    │  ffmpeg          │     │    GATEWAY      │◄── /ops      production panel
                    │    ↓ PCM 16k     │     │    (FastAPI)    │◄── /overlay  OBS / vMix
                    │  VAD segmenter   │     │                 │◄── /metrics  Prometheus
                    │    ↓ ~2-5 s      │ WS  │  · registry     │
                    │  ENGINE ─────────┼────►│  · fan-out      │
                    │    ↓             │     │  · supervision  │
                    │  ordered publish │     │  · transcripts  │
                    └────────┬─────────┘     └────────┬────────┘
                             │                        │
                             ▼                        ▼
                   ┌──────────────────┐    data/transcripts/*.jsonl
                   │ Gemini / Gemma / │    → SRT · VTT · TXT · JSON
                   │ Whisper (mock)   │
                   └──────────────────┘
```

### The four decisions that matter

**1. One model call returns the transcript *and* every translation.**
The classic pipeline is ASR → translator: two round trips, two prompts, and the translator
never hears the audio. Cotorra sends the audio once and asks for structured JSON containing
the transcript plus each language. Half the latency, lower cost, and the model can use
prosody to disambiguate while it translates. Adding Portuguese does not add a call — it
adds a field.

**2. One OS process per stage.**
If stage 5's ffmpeg dies, if its model call hangs, if its memory blows up — **stage 1 never
notices**. Processes also spread across cores; asyncio tasks in one event loop do not. And
the same worker binary runs as a subprocess on a laptop, a container under Compose, or a
Pod in Kubernetes. Only the supervisor changes.

**3. Calls overlap, subtitles stay ordered.**
Up to `WORKER_CONCURRENCY` calls are in flight, and a publisher awaits them in submission
order. Subtitles arriving shuffled are worse than subtitles arriving slightly later.
([test](tests/test_worker.py))

**4. Adaptive segmentation, not fixed chunks.**
An energy VAD with an adaptive noise floor cuts on the speaker's pauses, with a 300 ms
pre-roll so word onsets are never clipped and a 7 s ceiling so nobody waits for a caption
because the speaker did not breathe. While an utterance is still open, *partials* are
emitted — that is what makes words appear as they are spoken.
([tests](tests/test_segmenter.py))

### Latency, concretely

```
  word is spoken
       │
       ├─ 0.9–7 s ──► utterance ends (speaker pauses, or the 7 s ceiling hits)
       │                   └─► the PARTIAL already appeared at ~1.8 s
       ├─ + 2.5–3 s ────► model responds (measured, table below)
       ├─ + ~5 ms ──────► websocket fan-out
       ▼
  subtitle on screen
```

What the audience perceives is **`model latency + half a sentence`**, and that is exactly
what partials fix: text appears while the sentence is still being spoken, then gets
replaced in place when it closes. Measured in this repo with `mock` (simulated model
latency) and with real Gemini 2.5 Flash:

| | p50 | p95 |
|---|---|---|
| Transport + fan-out (`mock`, 10 stages, 30 readers) | 419 ms | 480 ms |
| **Gemini 2.5 Flash**, real talk, EN→ES | **2,581 ms** | 3,163 ms |
| **Gemini 3.5 Flash-Lite**, real talk, EN→ES+PT | **2,988 ms** | 4,139 ms |

Measured on 2026-09-24 against
[this Nerdearla talk](https://www.youtube.com/watch?v=iaG9pHMJ3Y4), not against studio
audio. Reproduce it on your hardware with your key:

```bash
python scripts/benchmark_models.py --source samples/nerdearla-mcp-en.ogg --targets es
```

`thinking_budget=0` is deliberate: verbatim transcription has nothing to reason about, and
thinking tokens are pure added delay. Verified — responses come back with
`thoughts_token_count=0`.

---

## 📈 Scaling to 30 stages

**One process per stage.** The limit on a single box is CPU, not architecture.

```bash
# measure it on your own hardware before the event
python scripts/scale_test.py --sessions 10 --seconds 60
```

Result on an ordinary laptop (10 stages in parallel, 30 simulated readers):

```
  sessions running      10/10
  engine errors         0
  latency p50           419 ms
  latency p95           480 ms
  cost per stage-hour   $0.358
  -> 30 talks x 1 h     $10.75
```

### Three rungs

| Scale | How | What changes |
|---|---|---|
| **1–12 stages** | `docker compose up` | nothing. This is the default. Raise `MAX_LOCAL_WORKERS`. |
| **12–50 stages** | several gateway replicas + `REDIS_URL` | workers and readers may land on different replicas; Redis bridges the fan-out. One line in `.env`. |
| **50+ / multi-venue** | one worker per Pod | replace `LocalSupervisor` with one that creates Pods. Four methods: `spawn`, `stop`, `wait`, `reap`. Nothing else changes. See [docs/deploy.md](docs/deploy.md). |

The bottleneck is never Cotorra — it is your model API quota. With the `local` engine there
is no quota, there is GPU.

---

## 💸 What it actually costs

Measured, not guessed: the gateway counts the real tokens of every call and multiplies by
the rates you configured, and the dashboard shows the running total live.

```bash
python scripts/cost_model.py --talks 30 --hours 1 --languages 2
```

| Event | Without partials | With partials |
|---|---|---|
| 1 talk, 1 h, 2 languages | **US$0.31** | US$0.96 |
| **30 talks of 1 h (Nerdearla)** | **US$9.24** | US$28.90 |
| 30 talks, 3 languages (+ Portuguese) | US$12.21 | US$36.00 |

**Partials cost 3.1x.** That is not a rounding difference, which is why it is measured
rather than estimated: they produce 4.3x the calls (81 against 19 over two minutes of talk)
because each one re-sends all the audio accumulated so far. In exchange the audience sees
words as they are spoken instead of waiting for the sentence to close.

It is your call, and it is one environment variable: `PARTIALS=false`.

Measured on 2026-09-24 against two real Nerdearla talks through Vertex AI with
`gemini-2.5-flash`. The calibrated analytical model agrees within 0.4% for the two-language
case:

```bash
python scripts/cost_model.py --talks 30 --languages 2 --no-partials
```

> Assumed Gemini 2.5 Flash rates: audio in US$1.00 / text in US$0.30 / text out US$2.50 per
> million tokens. **Check current rates** at <https://ai.google.dev/pricing> and update
> `PRICE_*` in your `.env` — the counter and the script read those values, so the arithmetic
> stays honest on its own.

For comparison: professional human captioning runs US$50–250 per hour per stream. Cotorra
does not replace human interpreters where they are needed; it makes it possible to caption
**the other 29 stages** that today have nothing at all.

---

## 📺 Four screens

| | For whom |
|---|---|
| `/viewer` | everyone on their own phone: pick a talk and a language, bilingual mode, large type |
| `/overlay?…&bg=000&qr=1` | the **screen in front of the stage**, with a QR to follow from your seat |
| `/overlay` | the **transparent overlay for vMix or OBS**, so the online audience gets translation too |
| `/ops` | the production crew |

And to show it off: `/demo` plays a real talk with the live captions burned on top, in
sync with what Cotorra is processing at that moment.

Subtitling a recording after the event is one command:

```bash
cotorra subtitle talk.mp4 --from en --to es
```

---

## 🎛 Production dashboard

`http://localhost:8080/ops` — built for the AV crew during an event, not for a demo.

- **Traffic light per stage.** 🟢 streaming · 🟡 degraded · 🔴 down.
- **Live audio meter.** Detects dead air: the cable that came out, the muted channel on the
  desk. Thirty seconds without audio and the stage goes amber **before** the audience
  complains.
- **p50 / p95 latency per stage**, live.
- **Running cost** for the event, in dollars.
- **Self-healing**: a dead worker is restarted with exponential backoff. After 5 attempts it
  goes red with the reason. With `?simulacro=1`, a 💥 button per stage kills a worker on
  purpose so you can rehearse it.
- **One click**: start, stop, copy the OBS overlay URL, download the SRT.
- `/metrics` in Prometheus format — point Grafana at it.

---

## 🎥 OBS / vMix

Add a **Browser Source** with this URL:

```
http://localhost:8080/overlay?session=stage-red&lang=es&lines=2&size=42
```

Transparent background, nothing but the text. Parameters: `lines`, `size`, `align`
(`bottom`/`top`/`center`), `box`, `shadow`, `font`, `partials`. Full detail and vMix recipes
in [docs/obs.md](docs/obs.md).

---

## 🧩 Engines

| Engine | Uses | When |
|---|---|---|
| `gemini` | Gemini 2.5 Flash multimodal | **recommended default**. Best quality/latency/cost. |
| `local` | faster-whisper + Gemma via Ollama | no internet, or audio that cannot leave the venue. |
| `mock` | fixed script | credential-free demos, CI, and free load tests. |

Adding one is **one file and one line** in `ENGINES`. See
[CONTRIBUTING.md](CONTRIBUTING.md#agregar-un-motor) and [docs/engines.md](docs/engines.md).

---

## 📚 Commands

```bash
cotorra serve                          # gateway + the three web UIs
cotorra doctor                         # check the machine the day BEFORE the event
cotorra sessions                       # what is running right now
cotorra start keynote / stop keynote
cotorra watch keynote --lang es        # captions in your terminal, no browser
cotorra export keynote --lang es --formats srt,vtt,txt
cotorra worker --id x --source mic --targets es   # one stage standalone, for debugging
```

---

## 🗂 Event configuration

The whole event lives in one file the production crew edits and commits:

```yaml
# cotorra.yaml
defaults:
  engine: gemini
  glossary: nerdearla
  source_lang: auto

sessions:
  - id: konex-main
    title: "Opening keynote"
    stage: "Konex Main"
    source: rtmp://ingest.local/live/main
    targets: [es, en, pt]
    autostart: true
```

`source` accepts a file, HLS, RTMP, SRT, YouTube, `mic`, or `device:<name>`.

### Glossaries: the cheapest accuracy win there is

A conference has a closed vocabulary. Speaker names, sponsor names, the project everybody
is talking about that week — that is what generic models get wrong, and they get it wrong
predictably. `data/glossaries/nerdearla.txt` rides along in every call and costs fractions
of a cent per hour.

---

## 📤 Exports

Every session is persisted as append-only JSONL while it happens (`tail -f` works), so a
crash loses at most the last line, never the talk.

```bash
curl -O localhost:8080/api/sessions/keynote/transcript?lang=es&format=srt
cotorra export keynote --lang en --formats srt,vtt,txt
```

Upload the SRT to YouTube and the talk is captioned and searchable forever.

---

## ♿ Accessibility

This is the point of the project, so it is not a decorative section:

- The audience page is **plain HTML**: it loads instantly on saturated venue wifi, with no
  app, no account, and no third-party JavaScript.
- `role="log"` + `aria-live="polite"`: screen readers announce new lines without
  interrupting.
- **Adjustable, persistent font size**, light/dark theme, high contrast.
- Respects `prefers-reduced-motion`.
- **Bilingual mode**: original and translation side by side, for people learning the
  language or checking a term.
- Full keyboard navigation with visible focus.

---

## 🔐 Before the doors open

```bash
cotorra doctor
```

- Set `ADMIN_TOKEN`. Empty leaves the control API open to the whole venue network.
- Change `WORKER_TOKEN`.
- Readers **never** need credentials — and they should not.
- The `/ops` panel without a token is read-only.

See [SECURITY.md](SECURITY.md).

---

## 🛣 Roadmap

Issues tagged [`good first issue`](../../issues?q=label%3A%22good+first+issue%22) are ready
to pick up. The big ones ahead: Gemini Live API (native bidirectional streaming), speaker
diarization, live correction from the dashboard, and sign-language output. See
[docs/roadmap.md](docs/roadmap.md).

---

## 📖 Documentation

Written in Spanish — the event's language — but code, identifiers and error messages are in
English throughout, and issues and pull requests are welcome in either language.

| | |
|---|---|
| [Architecture](docs/architecture.md) | the path of a caption, the wire protocol, what happens when things fail |
| [Engines](docs/engines.md) | configuring `gemini`, `local` and `mock`; picking a Whisper model |
| [Quality](docs/quality.md) | how to measure it and what to tune when it falls short |
| [Deploy & operate](docs/deploy.md) | hardware sizing, scaling, and the event-day runbook |
| [Cost](docs/costs.md) | where the number comes from and how to recompute it |
| [OBS / vMix](docs/obs.md) | burning captions into the stream |
| [API](docs/api.md) | REST and WebSockets, with recipes |
| [Troubleshooting](docs/troubleshooting.md) | ordered by what actually happens |
| [Cloud Run](deploy/README.md) | a public HTTPS URL, with no API keys |
| [Roadmap](docs/roadmap.md) | what is missing, and what we deliberately will not do |

---

## 🤝 Contributing

Adding a language, an engine, or improving segmentation:
[CONTRIBUTING.md](CONTRIBUTING.md).

```bash
pip install -e ".[dev,gemini]"
pytest                    # 140 tests, no network, no credentials
ruff check src tests
```

---

## 📄 Licence

[Apache 2.0](LICENSE) — OSI approved. Use it, fork it, sell it, go caption your conference.

Built for the [Nerdearla 2026 Vibeathon](https://nerdearla.com).
