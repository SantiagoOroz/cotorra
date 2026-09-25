# Arquitectura

Complementa el diagrama del [README](../README.md#-arquitectura). Acá está el detalle que
hace falta para modificar el sistema, no sólo para usarlo.

---

## El camino de un subtítulo

```
 1. ffmpeg                  cualquier fuente  ->  PCM 16 kHz mono s16le
 2. Segmenter               PCM  ->  enunciados (VAD con piso de ruido adaptativo)
 3. Engine                  audio + prompt  ->  {transcript, traducciones}
 4. Publisher               resultados  ->  Captions, en orden de envío
 5. WebSocket -> Gateway    Caption  ->  store (JSONL) + hub
 6. Hub                     Caption  ->  cada lector, sólo en su idioma
```

Los pasos 1–4 viven en el **worker**, uno por escenario. Los pasos 5–6 en el **gateway**.

### 1. Ingesta

`FFmpegSource` traduce un string de fuente a un argv de ffmpeg
([source.py](../src/cotorra/audio/source.py)). Dos detalles:

- **`-re` para archivos.** Reproduce a velocidad real, así que una charla grabada se
  comporta exactamente como un feed en vivo. Sin eso, las demos mienten: el archivo se
  procesa a 50x y la latencia medida no significa nada.
- **`-reconnect` para streams.** Un HLS que parpadea no mata el escenario.

### 2. Segmentación

Es lo que más define la latencia percibida, así que es una máquina de estados pura y
sincrónica, testeable sin ffmpeg y sin modelo
([segmenter.py](../src/cotorra/audio/segmenter.py)).

Frames de 20 ms. VAD por energía con **piso de ruido adaptativo**: sólo los frames
silenciosos mueven el piso, para que un monólogo largo no vaya subiendo la vara hasta que
el habla deje de registrar.

| Parámetro | Default | Por qué |
|---|---|---|
| `min_segment_ms` | 900 | menos que eso suele ser una tos; no vale una llamada al modelo |
| `max_segment_ms` | 7000 | techo duro: nadie espera porque el orador no respiró |
| `silence_ms` | 420 | una pausa natural entre frases, no entre palabras |
| `preroll_ms` | 300 | guarda el audio *anterior* a detectar habla, o se comen los arranques |
| `partial_interval_ms` | 1800 | cada cuánto se manda un parcial mientras la frase sigue abierta |

Subir `silence_ms` da frases más completas y más latencia. Bajar `max_segment_ms` da
latencia más pareja y más cortes en medio de una idea.

### 3. El motor

Una llamada devuelve transcript **y todas las traducciones**
([base.py](../src/cotorra/engines/base.py)). El prompt lleva:

- el idioma fuente (o "detectalo"),
- los códigos de idioma exactos que se esperan de vuelta,
- el **glosario** del evento,
- los **últimos 3 subtítulos** como contexto, marcados explícitamente como *no los repitas*
  — sin eso el modelo vuelve a transcribir la frase anterior,
- si el clip está **cortado al medio**, una instrucción de no completar la frase. Es la
  diferencia entre un parcial honesto y una alucinación.

La salida es JSON estructurado (`response_schema`), así que no hay que limpiar fences ni
reintentar por JSON roto.

### 4. Orden

Hasta `WORKER_CONCURRENCY` llamadas viajan a la vez, pero `_publisher` las espera **en
orden de envío**. Un `asyncio.Queue` de tareas pendientes hace de pipeline: la tarea 2
puede terminar antes que la 1, pero se publica después.

Consecuencia deliberada: una llamada lenta **retrasa** las siguientes en vez de dejarlas
pasar. Es lo correcto para subtítulos. Un error no bloquea: se descarta ese enunciado, se
cuenta y se sigue.

Los **parciales se descartan** si el pipeline está saturado (`_sem.locked()`). Bajo carga
lo que importa son los finales.

### 5–6. Fan-out

El `Hub` ([hub.py](../src/cotorra/hub.py)) tiene una cola acotada por suscriptor. Al
desbordar se tira **el más viejo**, porque en subtitulado en vivo la línea que importa es la
última. Un lector con mala conexión nunca frena al escenario.

Cada lector recibe **sólo su idioma** (`_for_lang`), no el objeto completo. Con 10
escenarios, 3 idiomas y 500 personas eso es la diferencia entre mover unos kB/s y unos
cientos.

---

## Protocolo de cable

### Worker → Gateway (`/ws/ingest/{id}?token=…`)

```jsonc
{"type":"hello",     "session_id":"s","engine":"gemini","model":"gemini-2.5-flash","pid":123}
{"type":"caption",   "caption":{ /* modelo Caption completo */ }}
{"type":"heartbeat", "session_id":"s","state":"running","metrics":{…},"last_error":null}
{"type":"bye",       "session_id":"s","reason":"finished","state":"finished"}
```

### Gateway → Worker

```jsonc
{"type":"shutdown"}   // apagado cooperativo: drená y salí
```

Se usa el socket que ya existe en vez de señales del sistema operativo. Así el botón
"Detener" funciona igual en Windows, en Linux y en un contenedor, y el worker alcanza a
publicar lo que tenía en vuelo.

### Gateway → Lector (`/ws/captions/{id}?lang=es`)

```jsonc
{"type":"hello","session":{…},"lang":"es","backlog":[…]}
{"type":"caption","seq":42,"kind":"final","t_start":128.4,"t_end":131.2,
 "lang":"es","source_lang":"en","text":"…","latency_ms":740,"demo":false}
{"type":"status","status":{…}}
```

**`seq` es la clave de todo el UI.** Un `partial` comparte el `seq` del `final` que va a
llegar, así que la interfaz reemplaza esa línea en el lugar en vez de agregar una nueva.

---

## Estado y qué pasa si algo se cae

| Qué | Dónde vive | Si se cae |
|---|---|---|
| Subtítulos en vuelo | en ningún lado (streaming) | se pierden los de ese instante |
| Transcripción | `data/transcripts/*.jsonl`, append-only | se pierde a lo sumo la última línea |
| Lista de sesiones | `data/sessions.json` + `cotorra.yaml` | se restaura al arrancar |
| Backlog para quien llega tarde | anillo en memoria del gateway | se rearma con los subtítulos siguientes |
| Métricas | en el worker, reportadas por heartbeat | se reinician con el worker |

- **Se cae el gateway**: los workers siguen transcribiendo y **bufferean**; reconectan
  solos. Los lectores reconectan con backoff. Se pierden los subtítulos de la ventana
  exacta de caída.
- **Se cae un worker**: el supervisor lo nota al hacer `reap`, lo reinicia con backoff
  exponencial (1, 2, 4, 8, 16 s) hasta `WORKER_RESTARTS`. El escenario aparece 🟡 mientras
  tanto y 🔴 si se agotan los intentos.
- **Se cae la API del modelo**: 3 errores seguidos ponen el escenario en 🔴 con el mensaje
  real. El audio se sigue consumiendo, así que cuando la API vuelve el escenario retoma sin
  intervención.
- **Se queda sin audio la fuente**: el watchdog lo detecta por el medidor de nivel y lo
  marca 🟡 con "No audio for Ns". Esto atrapa el cable salido, que es el incidente más común
  de verdad.

---

## Por qué no un framework en el frontend

La página de la audiencia tiene que cargar **instantáneo en el wifi saturado de un venue**,
en un teléfono viejo, en la fila del fondo. HTML + una hoja de estilos + un módulo de JS.
Sin bundler, sin CDN, sin node_modules.

Es además una decisión de gobernanza: cualquier conferencia puede forkear esto y cambiar
los colores sin instalar un toolchain.
