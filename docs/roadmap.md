# Roadmap

Cotorra hace hoy lo que una conferencia necesita para subtitular todos sus escenarios.
Esto es lo que falta para que lo haga mejor.

Cada punto es un issue. Los marcados **`good first issue`** están acotados a propósito y
tienen los archivos señalados.

---

## Latencia

### Gemini Live API en vez de chunks — `help wanted`
Hoy cortamos el audio en enunciados y mandamos uno por llamada. La Live API es un stream
bidireccional: el audio entra continuo y el texto sale a medida que se reconoce. Debería
bajar la latencia por debajo de los 500 ms y eliminar el compromiso de los parciales.

Es un motor nuevo, `src/cotorra/engines/gemini_live.py`, y una línea en `ENGINES`. La parte
difícil es mapear un stream continuo a los `Caption` con `seq` que espera el resto del
sistema. **Este es el cambio de mayor impacto del roadmap.**

### VAD neuronal (Silero) — `good first issue`
El VAD por energía se confunde con aplausos, música de transición y el ruido de un venue.
Silero VAD es un modelo de ~2 MB que corre en CPU sin problema.

`src/cotorra/audio/segmenter.py` — reemplazar `_rms()` por una interfaz de detector y
agregar la implementación Silero como extra opcional. Los
[tests existentes](../tests/test_segmenter.py) definen el contrato.

---

## Calidad

### Diarización de oradores — `help wanted`
En un panel de tres personas los subtítulos son una pared de texto sin saber quién habla.
Gemini puede etiquetar oradores si se le pide en el schema. Requiere: un campo `speaker` en
`Caption`, mostrarlo en el visor y en el overlay, y decidir qué hacer cuando la etiqueta
cambia a mitad de enunciado.

### Corrección en vivo desde el panel — `help wanted`
El nombre de un orador sale mal escrito. Producción debería poder arreglarlo una vez y que
se propague: reemplazar el subtítulo ya publicado en los lectores conectados y agregar el
término al glosario para que no vuelva a pasar. Toca `hub`, `store` y el panel.

### Glosario por charla con carga automática — `good first issue`
Hoy el glosario se asigna a mano en `cotorra.yaml`. Si tu conferencia tiene el programa en
JSON, se podría generar un glosario por charla con el nombre del orador, el título y las
tecnologías del abstract. `scripts/` — un script nuevo que lea un programa y escriba
`data/glossaries/`.

### Medir de verdad la calidad — `good first issue`
No hay número de WER (word error rate). Hace falta: un puñado de charlas con transcripción
de referencia y un script que compare la salida de Cotorra contra ellas para poder decir
"este cambio mejoró un 3%" en vez de "suena mejor".

---

## Idiomas

### Portugués, guaraní, quechua, catalán — `good first issue`
Agregar un idioma son dos líneas (ver
[CONTRIBUTING](../CONTRIBUTING.md#agregar-un-idioma)) más probarlo con audio real. El
portugués ya está en `LANG_NAMES`; falta que alguien lo valide con una charla de verdad.

### Idiomas de derecha a izquierda — `good first issue`
Árabe y hebreo van a renderizar mal: el CSS no contempla `direction: rtl`.
`web/static/cotorra.css` y `web/viewer.html`.

### Lengua de señas — `help wanted`
La accesibilidad real para la audiencia sorda no es texto, es lengua de señas. Un avatar
generado a partir del transcript es una línea de investigación abierta y difícil, pero es
el objetivo correcto a largo plazo.

---

## Operación

### Supervisor de Kubernetes — `help wanted`
Para 50+ escenarios o multi-sede. Son cuatro métodos contra la API de Pods; la interfaz y
el YAML de ejemplo están en [docs/deploy.md](deploy.md#3-un-worker-por-pod-50-escenarios-multi-sede).


### Dashboard de Grafana — `good first issue`
`/metrics` ya expone todo. Falta un `grafana/dashboard.json` en el repo que alguien pueda
importar y tener el evento en pantalla en un minuto.

### Modo multi-evento — `help wanted`
Una sola instancia de Cotorra sirviendo varias conferencias a la vez, con aislamiento entre
ellas. Hoy el manifiesto es global.

---

## Cosas chicas que ayudan mucho

- **`good first issue`** — Guardar el audio opcionalmente (`SAVE_AUDIO=true`) para poder
  re-transcribir una charla con un modelo mejor después.
- **`good first issue`** — Exportar a `.ass` para quien edita con Aegisub.
- **`good first issue`** — Un botón "copiar la última línea" en el visor, para quien quiere
  citar algo.
- **`good first issue`** — `cotorra doctor --source rtmp://…`: probar una fuente puntual sin
  crear una sesión.
- **`good first issue`** — Traducir la interfaz de audiencia a inglés y portugués. Hoy los
  textos están hardcodeados en español en los HTML.

---

## Lo que deliberadamente NO vamos a hacer

- **Un framework en el frontend.** La página de la audiencia carga en wifi de venue en un
  teléfono viejo. Ver [architecture.md](architecture.md#por-qué-no-un-framework-en-el-frontend).
- **Una base de datos.** JSONL append-only cubre el caso y se debuggea con `tail -f`.
- **Cuentas de usuario para la audiencia.** Leer subtítulos no requiere identificarse y no
  lo va a requerir.
- **Telemetría.** Cero llamadas salientes fuera del motor que elegiste.
