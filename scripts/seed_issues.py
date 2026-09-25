#!/usr/bin/env python3
"""Create the starter issues and labels on GitHub, once the repo is public.

    python scripts/seed_issues.py --dry-run     # see what it would create
    python scripts/seed_issues.py               # actually create them

An open source project with no issues looks finished, and a finished project
gets no contributors. These are real, scoped pieces of work taken from
docs/roadmap.md, each one naming the files to touch.

Needs the GitHub CLI, authenticated:  gh auth login
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

LABELS = [
    ("good first issue", "7057ff", "Acotado y con los archivos señalados"),
    ("help wanted", "008672", "Necesitamos manos acá"),
    ("engine", "1d76db", "Motores de transcripción/traducción"),
    ("i18n", "fbca04", "Idiomas y localización"),
    ("accessibility", "d93f0b", "El punto del proyecto"),
    ("ops", "5319e7", "Operación durante un evento"),
    ("latency", "e99695", "Hacerlo más rápido"),
]

ISSUES = [
    {
        "title": "VAD neuronal (Silero) en vez del detector por energía",
        "labels": ["good first issue", "latency"],
        "body": """\
El VAD actual mide energía por frame de 20 ms con un piso de ruido adaptativo. Funciona,
pero se confunde con aplausos, música de transición y el ruido de fondo de un venue lleno.

[Silero VAD](https://github.com/snakers4/silero-vad) es un modelo de ~2 MB que corre en CPU
sin problema y distingue voz de ruido mucho mejor.

**Archivos:** `src/cotorra/audio/segmenter.py`

**Qué hacer**
1. Extraer una interfaz de detector: hoy la decisión está en `_feed_frame` usando `_rms()`.
2. Dejar el detector de energía como default (cero dependencias).
3. Agregar el detector Silero como extra opcional en `pyproject.toml`.
4. Elegirlo con una variable de entorno, `VAD=energy|silero`.

**Cómo saber que está bien:** los tests de `tests/test_segmenter.py` definen el contrato y
tienen que seguir pasando con ambos detectores. Sumá un test con audio que tenga aplausos.
""",
    },
    {
        "title": "Soporte de idiomas de derecha a izquierda (árabe, hebreo)",
        "labels": ["good first issue", "i18n", "accessibility"],
        "body": """\
El motor probablemente ya traduce bien al árabe, pero la interfaz lo va a renderizar mal:
el CSS no contempla `direction: rtl`, y el modo bilingüe va a quedar con las columnas al
revés.

**Archivos:** `web/static/cotorra.css`, `web/viewer.html`, `web/overlay.html`

**Qué hacer**
1. Una lista de códigos RTL en `web/static/cotorra.js`.
2. Poner `dir="rtl"` en la columna de subtítulos cuando corresponda.
3. Revisar el overlay: la alineación y el `box-decoration-break` necesitan atención.
4. Probarlo de verdad con una charla traducida al árabe y poner una captura en el PR.
""",
    },
    {
        "title": "Dashboard de Grafana listo para importar",
        "labels": ["good first issue", "ops"],
        "body": """\
`/metrics` ya expone todo lo necesario en formato Prometheus: `cotorra_session_up`,
`cotorra_latency_p50_ms`, `cotorra_latency_p95_ms`, `cotorra_captions_total`,
`cotorra_errors_total`, `cotorra_viewers`, `cotorra_cost_usd`, `cotorra_audio_seconds`.

Falta un `grafana/dashboard.json` en el repo que alguien pueda importar y tener el evento
en pantalla en un minuto.

**Paneles útiles:** semáforo por escenario, p95 de latencia en el tiempo, costo acumulado,
lectores conectados, tasa de errores.

Poné una captura en el PR — es la mitad del valor de este issue.
""",
    },
    {
        "title": "Exportar subtítulos en formato .ass (Aegisub)",
        "labels": ["good first issue"],
        "body": """\
Ya exportamos SRT, VTT, TXT y JSON. Quien edita subtítulos en serio usa Aegisub, y su
formato es `.ass`.

**Archivos:** `src/cotorra/exporters.py` — agregar `to_ass()`, sumarlo a `FORMATS` y a
`MIME`. Los tests de `tests/test_exporters.py` muestran el patrón.

Ojo con los casos borde que ya están cubiertos para los otros formatos: parciales que no
deben aparecer, seq duplicados por reconexión, y subtítulos de duración cero.
""",
    },
    {
        "title": "Traducir la interfaz de audiencia a inglés y portugués",
        "labels": ["good first issue", "i18n", "accessibility"],
        "body": """\
Irónico pero cierto: la herramienta traduce charlas y su propia interfaz está hardcodeada
en español.

**Archivos:** `web/index.html`, `web/viewer.html`, `web/ops.html`

**Qué hacer:** un diccionario chico de strings en `web/static/cotorra.js`, elegido por
`navigator.language` con override por query param (`?ui=en`). Sin librería de i18n —
son unas 40 cadenas.

Empezá por la página de audiencia, que es la que ve más gente.
""",
    },
    {
        "title": "cotorra doctor --source: probar una fuente sin crear una sesión",
        "labels": ["good first issue", "ops"],
        "body": """\
El día antes del evento hay que verificar cada feed RTMP/HLS. Hoy eso implica crear una
sesión, arrancarla, mirar el panel y borrarla.

**Qué hacer:** `cotorra doctor --source rtmp://ingest.local/live/main` que abra ffmpeg unos
segundos, informe si llega audio, con qué nivel, y salga.

**Archivos:** `src/cotorra/cli.py` (comando `doctor`), reutilizando `FFmpegSource` y
`Segmenter` de `src/cotorra/audio/`.

Salida útil: "audio OK, nivel medio 0.08, 3 enunciados detectados en 10 s" o el error de
ffmpeg tal cual.
""",
    },
    {
        "title": "Motor con la Gemini Live API (streaming bidireccional)",
        "labels": ["help wanted", "engine", "latency"],
        "body": """\
**El cambio de mayor impacto del roadmap.**

Hoy cortamos el audio en enunciados y mandamos uno por llamada. Eso nos cuesta latencia:
hay que esperar a que el orador pause (o llegar al techo de 7 s) antes de saber qué dijo.
Los parciales lo mitigan, pero cuestan plata.

La Live API es un stream bidireccional: el audio entra continuo y el texto sale a medida
que se reconoce. Debería bajar la latencia por debajo de los 500 ms y eliminar el
compromiso de los parciales.

**Archivos:** `src/cotorra/engines/gemini_live.py` (nuevo) + una línea en `ENGINES`.

**La parte difícil** es que la interfaz `Engine` de hoy es petición/respuesta
(`transcribe(req) -> result`). Un motor de streaming no encaja. Probablemente haga falta
una segunda interfaz, `StreamingEngine`, que el worker use en lugar del
segmentador+publicador, manteniendo el contrato de `Caption` con `seq` que el resto del
sistema espera (el `seq` es lo que permite que el UI reemplace una línea en el lugar).

Abrí un issue de diseño antes de escribir código; vale la pena discutirlo primero.
""",
    },
    {
        "title": "Supervisor de Kubernetes: un worker por Pod",
        "labels": ["help wanted", "ops"],
        "body": """\
Para 50+ escenarios o eventos multi-sede, el supervisor local no alcanza y querés que
Kubernetes programe los workers.

**Son cuatro métodos.** `SessionManager` habla con el supervisor a través de una interfaz
chica: `spawn`, `stop`, `wait`, `reap` (+ `running` y `stop_all`). Ver
`src/cotorra/supervisor.py` para la implementación local y `docs/deploy.md` para la
interfaz y un manifest de ejemplo.

El worker ya reconecta solo al gateway, así que reprogramar un Pod es transparente.

**Archivos:** `src/cotorra/supervisors/kubernetes.py` (nuevo), y elegir el supervisor por
variable de entorno en `src/cotorra/gateway.py`.
""",
    },
    {
        "title": "Medir la calidad de verdad: un harness de WER",
        "labels": ["help wanted", "engine"],
        "body": """\
No tenemos ningún número de calidad. Podemos decir "suena bien" pero no "este cambio
mejoró un 3%", y eso hace imposible comparar motores o evaluar un cambio en el prompt.

**Qué hacer**
1. Un puñado de charlas cortas con transcripción de referencia hecha a mano (empezá con 3).
2. `scripts/benchmark.py` que corra una sesión contra un archivo, exporte el TXT y calcule
   WER contra la referencia.
3. Reportar también WER **sólo sobre los términos del glosario** — es el número que de
   verdad importa en una conferencia técnica.

Con eso podemos comparar `gemini` vs `local`, medir cuánto aporta el glosario, y justificar
cambios en el prompt del sistema con datos.
""",
    },
]


def run(args: list[str], dry: bool) -> None:
    if dry:
        print("  $ " + " ".join(args[:6]) + (" …" if len(args) > 6 else ""))
        return
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "already exists" in stderr:
            print(f"  (already exists) {args[3] if len(args) > 3 else ''}")
            return
        print(f"  FAILED: {stderr}", file=sys.stderr)
    elif result.stdout.strip():
        print("  " + result.stdout.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo", default=None, help="owner/name (default: the current repo)")
    args = ap.parse_args()

    if not shutil.which("gh") and not args.dry_run:
        sys.exit("The GitHub CLI is not installed. https://cli.github.com/  then: gh auth login")

    repo = ["--repo", args.repo] if args.repo else []

    print(f"\nLabels ({len(LABELS)}):")
    for name, color, description in LABELS:
        run(["gh", "label", "create", name, "--color", color,
             "--description", description, *repo], args.dry_run)

    print(f"\nIssues ({len(ISSUES)}):")
    for issue in ISSUES:
        print(f"  {issue['title']}")
        cmd = ["gh", "issue", "create", "--title", issue["title"],
               "--body", issue["body"], *repo]
        for label in issue["labels"]:
            cmd += ["--label", label]
        run(cmd, args.dry_run)

    first = sum(1 for i in ISSUES if "good first issue" in i["labels"])
    print(f"\n{len(ISSUES)} issues ({first} marked 'good first issue').")
    if args.dry_run:
        print("Dry run — nothing was created. Drop --dry-run to do it for real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
