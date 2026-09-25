# Borrador de la entrega en Devpost

Texto listo para pegar en el formulario. **Está en inglés** porque parte del jurado no habla
español; las bases lo advierten explícitamente.

Revisalo y hacelo tuyo antes de enviar — esto es un punto de partida, no un texto final.

---

## Elevator pitch (200 caracteres)

> Open source live captioning and translation for conferences. 30 stages in parallel for
> under US$10, on a laptop, with one Docker command.

*(172 caracteres)*

---

## Inspiration

Nerdearla runs more than 30 sessions in English, many of them simultaneously. The current
setup uses two different commercial tools, needs a human operator per stage, and costs
hundreds of dollars per hour. It works, but it cannot scale to 30 stages and it cannot be
handed to another conference.

That last part is what bothered us most. Almost every conference in the region has exactly
this problem, and none of them has an open solution. Accessibility should not be something
only well-funded events can afford.

We asked on the event's Discord how it works today, and the answer is honest and very
familiar: each room's mixer feeds a 3.5 mm jack into a mini PC, a paid service transcribes
in a browser, the screens in the room show that browser, a QR lets people follow on their
phone, and when it freezes someone remotes in to press F5. The online audience gets no
translation at all. Cotorra is built to replace exactly that, piece by piece — including a
stage screen with a QR, and an overlay you drop into vMix.

## What it does

Cotorra takes live audio from any number of stages — RTMP, HLS, SRT, YouTube, a file or a
microphone — and produces real-time subtitles in the original language *and* in translation.

- **The audience** opens a web page, picks their talk and their language. No app, no
  account, no install. There is a bilingual mode that shows the original and the
  translation side by side.
- **The stream** gets a transparent overlay for OBS or vMix, so captions can be burned in
  live.
- **The production crew** gets a dashboard: a traffic light per stage, live latency, a
  live audio meter that catches dead air, a running cost counter, and workers that restart
  themselves when they crash.
- **Afterwards**, every talk exports to SRT, VTT, TXT or JSON, so it can be uploaded to
  YouTube and become searchable forever.

## How we built it

The core decision is that **one multimodal Gemini call returns the transcript and every
translation at once**. The classic pipeline is ASR → translator: two round trips, two
prompts, and the translator never hears the audio. Doing it in one call halves the latency,
costs less, and lets the model use prosody to disambiguate while it translates. Adding
Portuguese does not add a call — it adds a field.

The architecture is a **gateway plus one OS process per stage**:

- `ffmpeg` normalises any source to 16 kHz mono PCM.
- An energy VAD with an adaptive noise floor cuts on the speaker's pauses, with a 300 ms
  pre-roll so word onsets are not clipped and a 7 s ceiling so nobody waits for a caption
  because the speaker did not breathe.
- Model calls overlap, but results are published **in submission order** — subtitles that
  arrive shuffled are worse than subtitles that arrive slightly later.
- The gateway fans out over WebSockets, sending each viewer only the language they asked
  for, with bounded per-viewer queues so one reader on bad wifi never slows down a stage.

One process per stage means blast radius: if stage 5's ffmpeg dies or its model call hangs,
stage 1 never notices. The same worker binary runs as a subprocess on a laptop, a container
under Compose, or a Pod in Kubernetes.

Stack: Python, FastAPI, ffmpeg, Gemini 2.5 Flash (via AI Studio or Vertex AI). The frontend is plain HTML, CSS and
JavaScript — no framework, no bundler, no CDN — so the audience page loads instantly on
saturated venue wifi and any conference can fork it without installing a toolchain.

## Challenges we ran into

**Chunked ASR loses context.** Cutting audio into utterances means the model sees each
chunk in isolation and loses pronouns across boundaries. We feed the last three captions
back as context, explicitly labelled "do not repeat these" — without that instruction the
model happily re-transcribes the previous sentence.

**Ordering versus concurrency.** Overlapping model calls is what keeps up with a fast
speaker, but calls finish out of order. We built a pipeline that submits concurrently and
publishes in order, which means a slow call deliberately delays the ones behind it. That is
the right trade for subtitles.

**Graceful shutdown across platforms.** Terminating a worker on Windows left orphan
processes still calling the paid API. We replaced signals with a cooperative shutdown over
the websocket the gateway already has, so the operator's Stop button behaves identically on
Windows, Linux and in containers — and the worker gets to flush its last captions instead
of dropping the final sentence of a talk.

**A caption has a shelf life, and retrying past it is worse than failing.** Running against
the real API on a real talk, we saw captions arriving 26 seconds late. They were not errors:
a call timed out, the retry succeeded, and the answer was correct and useless. Worse,
because captions publish in order, that one doomed retry held up every line behind it. The
fix was to give each utterance a total time budget rather than a per-attempt timeout, and
to abandon it when the budget runs out. Max latency went from 26.4 s to 9.0 s.

**Two different clocks.** The API rejects an HTTP deadline under 10 seconds, but live
captioning wants to give up on a call long before that. So the transport gets the legal
minimum and we enforce our own, much shorter, budget on top. Discovering this cost us a run
where every single call failed with `Manually set deadline 8s is too short`.

**Flash-Lite models have no thinking mode** and reject `thinking_config` with a generic
`400 INVALID_ARGUMENT` that does not say which argument. Switching models silently broke
every call. Cotorra now notices the rejection on the first call, drops the parameter and
retries, so changing models is a one-line config edit.

**Perceived latency is not measured latency.** What the audience feels is model latency
*plus half a sentence*. A faster model does not fix that; interim captions do. So the
system emits partials that get replaced in place when the sentence closes — and they are a
single environment variable, because measurement showed they cost **3.1x**, not the 44% our
first cost model claimed. That model was wrong in two places at once, and only running
against the real API found it: the prompt is 610 tokens rather than 260, and real
utterances are 5.5 s rather than 4 s, which triples the number of interim calls. A README
number that nobody has checked against a bill is a guess wearing a suit.

## Accomplishments we're proud of

- **Under US$10 to caption a 30-talk conference.** US$0.31 per stage-hour, measured on two
  real Nerdearla talks through Vertex AI, not extrapolated. A calibrated analytical model
  agrees within 0.4%.
- **10 stages in parallel on a laptop**, 30 simulated readers, zero errors, p95 latency of
  480 ms on the transport. With real Gemini on a real talk, p50 is ~2.6-3.0 s end to end —
  measured, and reproducible with `scripts/benchmark_models.py`, not quoted from a datasheet.
- **An operations dashboard built for the AV crew**, not for a demo. The audio meter that
  catches a pulled cable is the feature we are proudest of, because it is the thing that
  actually goes wrong at an event.
- **140 tests that run with no network, no API key and no GPU**, so CI is free and anyone
  can contribute from a plane.
- **It runs with zero credentials.** `docker compose up` shows the whole pipeline working
  in five seconds using a demo engine — clearly labelled as demo in every screen, because
  fake subtitles that look real would be worse than none.

## What we learned

**Free-tier API keys allow 15-20 requests per day per model.** One hour of captioning needs
400-900. We found this the only way anyone finds it — by running out mid-run — and it is now
the first thing the README says, a check in `cotorra doctor --live`, and a one-line
actionable error on the dashboard instead of 1,500 characters of JSON. The failure mode of a
live captioning system is almost never bad transcription; it is quota, dead air and cables.

That the hard part of live captioning is not the model. It is segmentation, ordering,
backpressure, and knowing when the audio stopped arriving. The model is one call in the
middle of a lot of unglamorous plumbing, and the plumbing is what decides whether an event
runs smoothly.

We also learned how much a glossary matters. A hundred terms in the prompt — speaker names,
sponsor names, the project everybody is talking about that week — costs fractions of a cent
per hour and fixes most of the errors that are actually embarrassing.

## What's next for Cotorra

- **Gemini Live API** for native bidirectional streaming, which should push latency under
  500 ms and remove the partials trade-off entirely.
- **Speaker diarization**, so a three-person panel is not a wall of undifferentiated text.
- **Live correction from the dashboard**: fix a misspelled name once, propagate it to
  connected readers, and add it to the glossary so it never happens again.
- **A Kubernetes supervisor** for 50+ stages and multi-venue events.
- **Sign language output.** Real accessibility for Deaf audiences is not text. It is hard
  and it is the right long-term target.

Nine issues are open, six of them tagged `good first issue`.

---

## Built With

`python` · `fastapi` · `google-gemini` · `ffmpeg` · `websockets` · `docker` ·
`faster-whisper` · `gemma` · `ollama` · `prometheus` · `javascript` · `html` · `css`

## Try it out

- Repository: https://github.com/SantiagoOroz/cotorra
- Video: *(pegá el link de YouTube)*

---

## Checklist antes de enviar

- [x] Repo público, con licencia Apache 2.0 visible
- [ ] README con Quick Start que funcione tal cual está escrito
- [ ] Video de 1–2 min subido a YouTube, link puesto
- [ ] Subtítulos en inglés del video, **generados con Cotorra**
- [x] Los placeholders `TU-USUARIO` reemplazados en README, README.en y CONTRIBUTING
- [x] `scripts/seed_issues.py` ejecutado (9 issues, 7 labels)
- [ ] El badge de tests dice el número real
- [ ] `docker compose up` probado en una máquina limpia
- [ ] Enviado **antes** del 25 de septiembre, 15:00 UTC (12:00 🇦🇷)
