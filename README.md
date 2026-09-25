<div align="center">

# 🦜 Cotorra

**Subtítulos en vivo, en cualquier idioma, para cualquier conferencia.**

Transcripción y traducción simultánea en tiempo real. Open source, multi-escenario,
y barata: **US$0,31 por hora de charla**, medido.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-140%20passing-brightgreen.svg)](tests/)
[![CI](https://github.com/SantiagoOroz/cotorra/actions/workflows/ci.yml/badge.svg)](https://github.com/SantiagoOroz/cotorra/actions/workflows/ci.yml)

▶️ **[Mirá el video demo](https://www.youtube.com/watch?v=yGi2xt14FJ0)**

**Español** · [🇬🇧 English](README.en.md)

[Arrancar en 60 segundos](#-arrancar-en-60-segundos) ·
[Arquitectura](#-arquitectura) ·
[Escalar](#-escalar-a-30-escenarios) ·
[Costos](#-cuánto-cuesta-de-verdad) ·
[OBS](#-integración-con-obs--vmix) ·
[Contribuir](CONTRIBUTING.md)

*Una cotorra escucha y repite todo. Esta lo repite en el idioma que cada persona lee.*

</div>

---

## El problema

Nerdearla tiene **más de 30 sesiones en inglés**, muchas en simultáneo. Hoy se resuelven
con dos herramientas comerciales distintas: una transcribe español→español y otra traduce
inglés→español en vivo. Funciona, pero **es cara, depende de operación manual escenario por
escenario, y no se puede replicar en otra conferencia**.

Casi todas las conferencias tienen exactamente el mismo problema y ninguna tiene una
solución abierta. Cotorra es esa solución.

| | Solución comercial típica | Cotorra |
|---|---|---|
| Costo por hora de charla | US$50 – 250 | **US$0,31** |
| Escenarios en simultáneo | uno por licencia/operador | **limitado por CPU, no por licencia** |
| Operación durante el evento | manual, por escenario | **un panel, semáforo, auto-recuperación** |
| Replicable en otro evento | no | **`docker compose up`** |
| Licencia | propietaria | **Apache 2.0** |

---

## ⚡ Arrancar en 60 segundos

```bash
git clone https://github.com/SantiagoOroz/cotorra.git && cd cotorra
docker compose up
```

Abrí **<http://localhost:8080>**. Dos escenarios ya están subtitulando el audio de ejemplo
que viene en el repo, en paralelo.

> **Sin credenciales todavía.** Arranca con el motor `mock`, que recorre el pipeline
> completo (ffmpeg → segmentación → fan-out → UI → export) con texto de ejemplo. Todas las
> pantallas muestran un cartel de "modo demo" para que nadie confunda eso con transcripción
> real. Es también cómo se corre el test de carga gratis.

### Con transcripción real

Conseguí una API key en **<https://aistudio.google.com/apikey>** y:

```bash
cp .env.example .env
# editá .env: COTORRA_ENGINE=gemini y GEMINI_API_KEY=...
docker compose up
```

Eso es todo. No hay base de datos que migrar, ni cola de mensajes que levantar, ni build de
frontend. Un contenedor.

> **Para un evento, habilitá billing** (o usá Vertex AI): la key gratuita sirve para probar
> unos minutos, no para una conferencia. Con billing cuesta **US$0,31 por hora de charla**,
> medido. `cotorra doctor --live` verifica tu key y tu cuota antes del evento.


### Sin Docker

```bash
pip install -e ".[gemini]"      # o ".[local]" para 100% offline
cotorra doctor                  # verifica ffmpeg, credenciales y manifiesto
cotorra serve
```

### Probá con una charla real

```bash
python scripts/fetch_samples.py                    # baja charlas de Nerdearla de YouTube
cotorra serve
```

O apuntá directo a YouTube sin descargar nada:

```bash
curl -X POST localhost:8080/api/sessions -H 'Content-Type: application/json' -d '{
  "id":"charla","title":"Una charla en inglés","source":"https://www.youtube.com/watch?v=VIDEO_ID",
  "source_lang":"en","targets":["es"],"engine":"gemini"}'
curl -X POST localhost:8080/api/sessions/charla/start
```

---

## 🏗 Arquitectura

```
                                       ┌──────────────────────────────┐
  Escenario 1  ──RTMP/HLS/SRT──┐       │  AUDIENCIA                   │
  Escenario 2  ──YouTube───────┤       │  /viewer?session=X&lang=es   │
  Escenario N  ──mic / archivo─┤       │  el teléfono de cada persona │
                               │       └──────────────┬───────────────┘
                               ▼                      │ WebSocket
                    ┌──────────────────┐              │
                    │  WORKER (1 x     │              │
                    │  escenario)      │     ┌────────┴────────┐
                    │                  │     │                 │
                    │  ffmpeg          │     │    GATEWAY      │◄── /ops   panel de producción
                    │    ↓ PCM 16k     │     │    (FastAPI)    │◄── /overlay  OBS / vMix
                    │  segmentador VAD │     │                 │◄── /metrics  Prometheus
                    │    ↓ ~2-5 s      │ WS  │  · registro     │
                    │  MOTOR ──────────┼────►│  · fan-out      │
                    │    ↓             │     │  · supervisión  │
                    │  orden garantiz. │     │  · transcript   │
                    └────────┬─────────┘     └────────┬────────┘
                             │                        │
                             ▼                        ▼
                   ┌──────────────────┐    data/transcripts/*.jsonl
                   │ Gemini / Gemma / │    → SRT · VTT · TXT · JSON
                   │ Whisper (mock)   │
                   └──────────────────┘
```

### Las cuatro decisiones que importan

**1. Una sola llamada al modelo devuelve transcripción *y* todas las traducciones.**
El pipeline clásico es ASR → traductor: dos viajes de red, dos prompts, y el traductor
nunca escucha el audio. Cotorra manda el audio una vez y pide un JSON estructurado con el
transcript y cada idioma. Mitad de latencia, menos costo, y el modelo usa la prosodia para
desambiguar mientras traduce. Agregar portugués no agrega una llamada: agrega un campo.

**2. Un proceso del sistema operativo por escenario.**
Si el ffmpeg del Escenario 5 muere, si su llamada al modelo se cuelga o si se le va la RAM,
**el Escenario 1 no se entera**. Los procesos además se reparten entre cores; las tareas
asyncio dentro de un solo event loop no. Y el mismo binario del worker corre como
subproceso en una laptop, como contenedor en Compose o como Pod en Kubernetes: lo único que
cambia es el supervisor.

**3. Las llamadas se solapan pero los subtítulos salen en orden.**
Hasta `WORKER_CONCURRENCY` llamadas viajan en paralelo, y un publicador las espera en orden
de envío. Subtítulos desordenados son peores que subtítulos un poco más lentos.
([test](tests/test_worker.py))

**4. Segmentación adaptativa, no chunks fijos.**
Un VAD con piso de ruido adaptativo corta en las pausas del orador, con un *pre-roll* de
300 ms para no comerse el arranque de las palabras y un techo de 7 s para que nadie espere
un subtítulo porque el orador no respiró. Mientras la frase sigue abierta se emiten
*parciales*, que es lo que hace que las palabras aparezcan mientras se hablan.
([tests](tests/test_segmenter.py))

### Latencia: de qué estamos hablando

```
  palabra dicha
       │
       ├─ 0,9–7 s ──► fin del enunciado (el orador hace una pausa, o se corta a los 7 s)
       │                   └─► el PARCIAL ya apareció a los ~1,8 s
       ├─ + 2,5–3 s ────► respuesta del modelo (medido, ver tabla abajo)
       ├─ + ~5 ms ──────► fan-out WebSocket
       ▼
  subtítulo en pantalla
```

Lo que la audiencia percibe es **`latencia del modelo + media frase`**, y eso es
exactamente lo que arreglan los parciales: el texto aparece mientras se habla y se
reemplaza en el lugar cuando la frase cierra. Medido en este repo con `mock`
(latencia simulada) y con Gemini 2.5 Flash real:

| | p50 | p95 |
|---|---|---|
| Transporte + fan-out (motor `mock`, 10 escenarios, 30 lectores) | 419 ms | 480 ms |
| **Gemini 2.5 Flash**, charla real, EN→ES | **2 581 ms** | 3 163 ms |
| **Gemini 3.5 Flash-Lite**, charla real, EN→ES+PT | **2 988 ms** | 4 139 ms |

Medido el 24/09/2026 contra
[esta charla de Nerdearla](https://www.youtube.com/watch?v=iaG9pHMJ3Y4), no contra audio de
estudio. Reproducilo en tu hardware y con tu key:

```bash
python scripts/benchmark_models.py --source samples/nerdearla-mcp-en.ogg --targets es
```

`thinking_budget=0` es deliberado: para transcribir literalmente no hay nada que razonar y
los tokens de pensamiento son latencia pura. Verificado — la respuesta trae
`thoughts_token_count=0`.

---

## 📈 Escalar a 30 escenarios

**Escenario por escenario, un proceso.** El límite en una máquina es CPU, no arquitectura.

```bash
# medilo en tu propio hardware antes del evento
python scripts/scale_test.py --sessions 10 --seconds 60
```

Resultado en una laptop común (10 escenarios en paralelo, 30 lectores simulados):

```
  sessions running      10/10
  engine errors         0
  latency p50           419 ms
  latency p95           480 ms
  cost per stage-hour   $0.358
  -> 30 talks x 1 h     $10.75
```

### Los tres escalones

| Escala | Cómo | Qué cambia |
|---|---|---|
| **1–12 escenarios** | `docker compose up` | nada. Es el default. Subí `MAX_LOCAL_WORKERS`. |
| **12–50 escenarios** | varias réplicas del gateway + `REDIS_URL` | workers y lectores pueden caer en réplicas distintas; Redis puentea el fan-out. Una línea en `.env`. |
| **50+ / multi-sede** | un worker por Pod | reemplazá `LocalSupervisor` por uno que cree Pods. Son cuatro métodos: `spawn`, `stop`, `wait`, `reap`. Todo lo demás queda igual. Ver [docs/deploy.md](docs/deploy.md). |

El cuello de botella nunca es Cotorra: es tu cuota de la API del modelo. Con el motor
`local` no hay cuota, hay GPU.

---

## 💸 Cuánto cuesta de verdad

Números medidos, no estimados a ojo: el gateway cuenta los tokens reales de cada llamada y
los multiplica por las tarifas que configuraste, y el panel muestra el acumulado en vivo.

```bash
python scripts/cost_model.py --talks 30 --hours 1 --languages 2
```

| Evento | Sin parciales | Con parciales |
|---|---|---|
| 1 charla de 1 h, 2 idiomas | **US$0,31** | US$0,96 |
| **30 charlas de 1 h (Nerdearla)** | **US$9,24** | US$28,90 |
| 30 charlas, 3 idiomas (+ portugués) | US$12,21 | US$36,00 |

**Los parciales cuestan 3,1x.** No es una diferencia menor y por eso está medida, no
estimada: generan 4,3 veces más llamadas (81 contra 19 en dos minutos de charla) porque cada
uno reenvía todo el audio acumulado hasta ese momento. A cambio, la audiencia ve las
palabras mientras se hablan en vez de esperar a que la frase cierre.

La decisión es tuya y es una variable de entorno: `PARTIALS=false`.

Medido el 24/09/2026 sobre dos charlas reales de Nerdearla con Vertex AI y
`gemini-2.5-flash`. El modelo analítico calibrado coincide dentro del 0,4% en el caso de dos
idiomas:

```bash
python scripts/cost_model.py --talks 30 --languages 2 --no-partials
```

> Tarifas de Gemini 2.5 Flash asumidas: audio in US$1,00 / texto in US$0,30 / texto out
> US$2,50 por millón de tokens. **Verificá las tarifas vigentes** en
> <https://ai.google.dev/pricing> y actualizá `PRICE_*` en tu `.env` — el contador y el
> script usan esos valores, así que las cuentas se mantienen honestas solas.

Para comparar: subtitulado humano profesional cuesta entre US$50 y US$250 por hora y por
stream. Cotorra no reemplaza intérpretes humanos donde hacen falta; hace posible subtitular
**los otros 29 escenarios** que hoy no tienen nada.

---

## 📺 Las cuatro pantallas

| | Para quién |
|---|---|
| `/viewer` | cada persona en su celular: elige charla e idioma, modo bilingüe, letra grande |
| `/overlay?…&bg=000&qr=1` | la **pantalla frente al escenario**, con un QR para seguirlo desde el asiento |
| `/overlay` | el **overlay transparente para vMix u OBS**: la audiencia virtual también tiene traducción |
| `/ops` | el equipo de producción |

Y para mostrarlo: `/demo` reproduce una charla real con los subtítulos planchados encima,
sincronizados con lo que Cotorra está procesando en ese momento.

Subtitular una grabación después del evento es un comando:

```bash
cotorra subtitle charla.mp4 --from en --to es
```

---

## 🎛 Panel de producción

`http://localhost:8080/ops` — pensado para el equipo de AV durante el evento, no para una
demo.

- **Semáforo por escenario.** 🟢 transmitiendo · 🟡 degradado · 🔴 caído.
- **Medidor de audio en vivo.** Detecta *dead air*: el cable que se salió, el canal muteado
  en la consola. Si no entra audio 30 s, el escenario se pone amarillo **antes** de que la
  audiencia se queje.
- **Latencia p50 / p95 por escenario**, en vivo.
- **Costo acumulado** del evento, en dólares.
- **Auto-recuperación**: si un worker muere, el supervisor lo reinicia con backoff
  exponencial. Después de 5 intentos queda en rojo con el motivo. Con
  `?simulacro=1`, un botón 💥 por sala tira el worker a propósito para ensayarlo.
- **Un click**: iniciar, detener, copiar la URL del overlay para OBS, bajar el SRT.
- `/metrics` en formato Prometheus, para apuntarle un Grafana.

---

## 🎥 Integración con OBS / vMix

Agregá un **Browser Source** con esta URL:

```
http://localhost:8080/overlay?session=stage-red&lang=es&lines=2&size=42
```

Fondo transparente, sin nada más que el texto. Parámetros: `lines`, `size`, `align`
(`bottom`/`top`/`center`), `box`, `shadow`, `font`, `partials`.
Detalle completo y recetas para vMix en [docs/obs.md](docs/obs.md).

---

## 🧩 Motores

| Motor | Qué usa | Cuándo |
|---|---|---|
| `gemini` | Gemini 2.5 Flash multimodal | **default recomendado**. Mejor calidad/latencia/costo. |
| `local` | faster-whisper + Gemma vía Ollama | sin internet, o si el audio no puede salir del venue. |
| `mock` | guion fijo | demos sin credenciales, CI, y tests de carga gratis. |

Agregar uno es **un archivo y una línea** en `ENGINES`. Ver
[CONTRIBUTING.md](CONTRIBUTING.md#agregar-un-motor).

---

## 📚 Comandos

```bash
cotorra serve                          # gateway + las tres UIs
cotorra doctor                         # verificá la máquina el día ANTES del evento
cotorra sessions                       # qué está corriendo ahora
cotorra start keynote / stop keynote
cotorra watch keynote --lang es        # subtítulos en la terminal, sin navegador
cotorra export keynote --lang es --formats srt,vtt,txt
cotorra worker --id x --source mic --targets es   # un escenario suelto, para depurar
```

---

## 🗂 Configuración del evento

Todo el evento vive en un archivo que el equipo de producción edita y commitea:

```yaml
# cotorra.yaml
defaults:
  engine: gemini
  glossary: nerdearla
  source_lang: auto

sessions:
  - id: konex-main
    title: "Keynote de apertura"
    stage: "Konex Principal"
    source: rtmp://ingest.local/live/main
    targets: [es, en, pt]
    autostart: true
```

`source` acepta archivo, HLS, RTMP, SRT, YouTube, `mic` o `device:<nombre>`.

### Glosario: la mejora de calidad más barata que existe

Una conferencia tiene vocabulario cerrado. Los nombres de los oradores, los sponsors, el
proyecto del que todos hablan esa semana: eso es lo que los modelos genéricos escriben mal,
y de forma predecible. `data/glossaries/nerdearla.txt` se manda en cada llamada y cuesta
fracciones de centavo por hora.

```
# data/glossaries/nerdearla.txt
Nerdearla
Sysarmy
Konex
Ariel Jolo
eBPF
```

---

## 📤 Exportar

Cada sesión se persiste como JSONL append-only mientras ocurre (`tail -f` funciona), así
que un crash pierde a lo sumo la última línea, nunca la charla.

```bash
curl -O localhost:8080/api/sessions/keynote/transcript?lang=es&format=srt
cotorra export keynote --lang en --formats srt,vtt,txt
```

Subí el SRT a YouTube y la charla queda subtitulada y buscable para siempre.

---

## ♿ Accesibilidad

Es el punto del proyecto, así que no es una sección decorativa:

- La página de audiencia es **HTML plano**: carga instantáneo en el wifi saturado de un
  venue, sin app, sin cuenta, sin JavaScript de terceros.
- `role="log"` + `aria-live="polite"`: los lectores de pantalla anuncian las líneas nuevas
  sin interrumpir.
- **Tamaño de letra ajustable** y persistente, tema claro/oscuro, contraste alto.
- Respeta `prefers-reduced-motion`.
- **Modo bilingüe**: original y traducción lado a lado, para quien está aprendiendo el
  idioma o quiere verificar un término.
- Navegación completa por teclado con foco visible.

---

## 🔐 Antes de abrir las puertas

```bash
cotorra doctor
```

- Poné `ADMIN_TOKEN`. Vacío deja la API de control abierta a toda la red del venue.
- Cambiá `WORKER_TOKEN`.
- Los lectores **nunca** necesitan credenciales — y no deberían.
- El panel `/ops` sin token es de solo lectura.

Ver [SECURITY.md](SECURITY.md).

---

## 🛣 Roadmap

Issues marcados [`good first issue`](../../issues?q=label%3A%22good+first+issue%22) para
sumarse. Lo grande que viene: Gemini Live API (streaming bidireccional nativo), diarización
de oradores, corrección en vivo desde el panel, y subtítulos para la audiencia sorda vía
lengua de señas. Ver [docs/roadmap.md](docs/roadmap.md).

---

## 📖 Documentación

| | |
|---|---|
| [Arquitectura](docs/architecture.md) | el camino de un subtítulo, el protocolo de cable, qué pasa si algo se cae |
| [Motores](docs/engines.md) | configurar `gemini`, `local` y `mock`; qué modelo de Whisper elegir |
| [Calidad](docs/quality.md) | cómo medirla y qué tocar cuando no alcanza |
| [Desplegar y operar](docs/deploy.md) | dimensionar el hardware, escalar, y el runbook del día del evento |
| [Costos](docs/costs.md) | de dónde sale el número y cómo recalcularlo |
| [OBS / vMix](docs/obs.md) | quemar los subtítulos en el stream |
| [API](docs/api.md) | REST y WebSockets, con recetas |
| [Cuando algo no anda](docs/troubleshooting.md) | ordenado por lo que más pasa |
| [Cloud Run](deploy/README.md) | una URL pública con HTTPS, sin API keys |
| [Roadmap](docs/roadmap.md) | lo que falta, y lo que deliberadamente no vamos a hacer |

---

## 🤝 Contribuir

Agregar un idioma, un motor, o mejorar la segmentación: [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
pip install -e ".[dev,gemini]"
pytest                    # 140 tests, sin red, sin credenciales
ruff check src tests
```

---

## 📄 Licencia

[Apache 2.0](LICENSE) — aprobada por la OSI. Usala, forkeala, vendela, andá a subtitular
tu conferencia.

Construido para la [Vibeathon de Nerdearla 2026](https://nerdearla.com).
