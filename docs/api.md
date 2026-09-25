# API

Hay documentación interactiva y siempre actualizada en **`/docs`** (OpenAPI, generada por
FastAPI). Esto es la referencia narrada, con los ejemplos que se usan de verdad.

**Regla que gobierna todo el diseño:** leer subtítulos nunca requiere credenciales.
Autenticar sólo lo que controla el evento.

| | Auth |
|---|---|
| Leer sesiones, subtítulos, transcripciones | ninguna |
| Crear, borrar, iniciar, detener | `ADMIN_TOKEN` |
| Panel `/ws/ops` | `ADMIN_TOKEN` (sin él, solo lectura) |
| Ingesta de workers `/ws/ingest/*` | `WORKER_TOKEN` |

El token va como header `X-Cotorra-Token: …` o como query `?token=…`. El header es
preferible: no queda en los logs del proxy. En los WebSocket sólo se puede por query,
porque el navegador no deja poner headers — **por eso el gateway va detrás de TLS**.

Si `ADMIN_TOKEN` está vacío, la API de control queda abierta. Cómodo para el primer `docker
compose up`, inaceptable en la red de un venue. El gateway loguea una advertencia y el panel
muestra un cartel.

---

## REST

### `GET /api/health`

```json
{"ok": true, "uptime_s": 1820.4, "sessions": 4,
 "states": {"running": 3, "stopped": 1},
 "engine_default": "gemini", "redis": false}
```

`ok` es `false` si algún escenario está en `error`. Sirve directo como health check de
Kubernetes o de un balanceador.

### `GET /api/config`

Configuración pública para el frontend. **Nunca devuelve tokens** ([hay un test que lo
verifica](../tests/test_gateway.py)).

### `GET /api/sessions`

Todos los escenarios con su estado en vivo. Es lo que consume la página de audiencia.

```jsonc
[{
  "spec": {"id": "keynote", "title": "Apertura", "stage": "Konex Principal",
           "source": "rtmp://…", "source_lang": "en", "targets": ["es", "pt"],
           "engine": "gemini", "glossary": "nerdearla", "autostart": true, "loop": false},
  "state": "running",          // idle|starting|running|degraded|error|stopped|finished
  "started_at": 1758400000.0,
  "last_caption_at": 1758401820.5,
  "last_error": null,
  "viewers": 143,
  "metrics": {"captions": 412, "words": 5230, "errors": 0, "audio_seconds": 1810.0,
              "latency_p50_ms": 690, "latency_p95_ms": 1240, "audio_level": 0.081,
              "cost_usd": 0.164, "engine_calls": 1204}
}]
```

### `POST /api/sessions` — admin

Crea o actualiza (upsert por `id`). Es idempotente: mandar el mismo id dos veces actualiza
la configuración sin duplicar.

```bash
curl -X POST localhost:8080/api/sessions \
  -H 'X-Cotorra-Token: TU_TOKEN' -H 'Content-Type: application/json' -d '{
    "id": "stage-3",
    "title": "Observabilidad con eBPF",
    "stage": "Sala 3",
    "source": "rtmp://ingest.local/live/stage3",
    "source_lang": "en",
    "targets": ["es", "pt"],
    "engine": "gemini",
    "glossary": "nerdearla",
    "autostart": false
  }'
```

`id` está validado con `^[a-z0-9][a-z0-9._-]{0,63}$`: aparece en URLs y en nombres de
archivo, así que un id con `../` se rechaza con 422.

### `POST /api/sessions/{id}/start` — admin

| Query | |
|---|---|
| `reset=true` | archiva la transcripción anterior y empieza limpio |

Devuelve **409** si el host ya llegó a `MAX_LOCAL_WORKERS`, con el motivo en `detail`. Eso
es deliberado: es mejor una negativa clara que una máquina que se cae en medio del evento.

### `POST /api/sessions/{id}/stop` — admin

Apagado cooperativo: se le pide al worker que drene y salga, así **no se pierde la última
frase**. Si no responde en 8 segundos, se lo fuerza.

### `DELETE /api/sessions/{id}` — admin

Detiene y saca la sesión del registro. **No borra la transcripción del disco.**

### `GET /api/sessions/{id}/transcript`

| Query | Default | |
|---|---|---|
| `lang` | `es` | idioma. Si no hay texto en ese idioma, cae al original |
| `format` | `srt` | `srt`, `vtt`, `txt`, `json` |
| `download` | `true` | `false` lo devuelve inline, para leerlo en el navegador |

```bash
curl -O 'localhost:8080/api/sessions/keynote/transcript?lang=es&format=srt'
```

Sólo incluye subtítulos finales, deduplicados por `seq` y ordenados por tiempo: una
reconexión de worker no ensucia el archivo.

**404** si todavía no hay subtítulos grabados. **400** si el formato no existe.

### `GET /api/sessions/{id}/languages`

```json
{"languages": ["en", "es", "pt"], "recorded": ["en", "es"]}
```

`languages` es lo declarado más lo grabado; `recorded` es lo que realmente hay en la
transcripción. La diferencia importa si cambiaste `targets` a mitad de una charla.

### `GET /api/glossaries`

```json
{"glossaries": {"default": 51, "nerdearla": 22}}
```

### `GET /metrics`

Exposición Prometheus. Ver [deploy.md](deploy.md#monitoreo) para alertas útiles.

```
cotorra_session_up{session="keynote",stage="Konex Principal"} 1
cotorra_captions_total{session="keynote",stage="Konex Principal"} 412
cotorra_latency_p95_ms{session="keynote",stage="Konex Principal"} 1240
cotorra_cost_usd{session="keynote",stage="Konex Principal"} 0.164
```

---

## WebSockets

### `/ws/captions/{id}?lang=es` — la audiencia

Sin auth. El primer mensaje siempre es `hello` con el backlog, para que quien llega tarde no
vea una pantalla en blanco.

```jsonc
{"type": "hello", "session": {…}, "lang": "es",
 "backlog": [{"seq": 40, "kind": "final", "text": "…"}, …]}

{"type": "caption", "seq": 42, "kind": "final",
 "t_start": 128.4, "t_end": 131.2,
 "lang": "es", "source_lang": "en",
 "text": "Lo desplegamos con Kubernetes.",
 "latency_ms": 740, "demo": false}

{"type": "status", "status": {…}}
```

**`seq` es la clave de toda la interfaz.** Un `partial` comparte el `seq` del `final` que va
a llegar, así que el cliente reemplaza esa línea en el lugar en vez de agregar una nueva.
La publicación ordenada garantiza que un parcial nunca llegue *después* de su final, así que
sólo se reemplaza hacia adelante.

Cada lector recibe **sólo su idioma**, no todos. Con 10 escenarios, 3 idiomas y 500 personas
esa es la diferencia entre unos kB/s y unos cientos.

Cliente mínimo:

```js
const ws = new WebSocket(`wss://subtitulos.tu-evento.org/ws/captions/keynote?lang=es`);
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.type === "caption") console.log(m.kind, m.seq, m.text);
};
```

### `/ws/ops?token=…` — producción

Estado de todos los escenarios en un solo socket. Manda `snapshot` al conectar y cada 2
segundos si no pasa nada, más `status` y `preview` cuando ocurren.

```jsonc
{"type": "snapshot", "sessions": [ …todos los SessionStatus… ]}
{"type": "status",  "status": {…}}
{"type": "preview", "session_id": "keynote", "text": "…", "latency_ms": 740}
```

`preview` es a propósito una versión liviana: mandar cada subtítulo en cada idioma al panel
no escala a 10 escenarios.

### `/ws/ingest/{id}?token=…` — workers

No la vas a usar a mano salvo que estés escribiendo un worker. El protocolo completo está en
[architecture.md](architecture.md#protocolo-de-cable).

---

## Recetas

### Levantar el evento entero desde un script

```bash
#!/usr/bin/env bash
set -euo pipefail
API=https://subtitulos.tu-evento.org
AUTH="X-Cotorra-Token: $ADMIN_TOKEN"

for n in 1 2 3 4 5; do
  curl -fsS -X POST "$API/api/sessions" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"id\":\"stage-$n\",\"title\":\"Escenario $n\",\"stage\":\"Sala $n\",
         \"source\":\"rtmp://ingest.local/live/stage$n\",\"source_lang\":\"auto\",
         \"targets\":[\"es\",\"en\"],\"engine\":\"gemini\",\"glossary\":\"nerdearla\"}" > /dev/null
  curl -fsS -X POST "$API/api/sessions/stage-$n/start" -H "$AUTH" > /dev/null
  echo "stage-$n arriba"
done
```

Para un evento estable es mejor `cotorra.yaml` con `autostart: true` — queda versionado.
La API es para lo que cambia sobre la marcha.

### Exportar todo al terminar

```bash
curl -s localhost:8080/api/sessions | jq -r '.[].spec.id' | while read -r id; do
  for lang in es en; do
    curl -fsS "localhost:8080/api/sessions/$id/transcript?lang=$lang&format=srt" \
      -o "transcripciones/$id.$lang.srt" || true
  done
done
```

### Esperar a que un escenario esté realmente emitiendo

```bash
until curl -fsS localhost:8080/api/sessions/keynote | jq -e '.state == "running"' > /dev/null; do
  sleep 2
done
```

---

## Errores

| Código | Cuándo |
|---|---|
| `401` | falta el token de admin o es incorrecto |
| `404` | no existe esa sesión, o no tiene subtítulos todavía |
| `409` | el host llegó a `MAX_LOCAL_WORKERS` |
| `422` | el cuerpo no valida (típicamente un `id` inválido) |
| `400` | formato de export desconocido |

Siempre `{"detail": "..."}` con un mensaje accionable, no un código interno.

Cierres de WebSocket: `4401` token inválido, `4404` sesión inexistente.
