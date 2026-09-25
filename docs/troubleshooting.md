# Cuando algo no anda

Ordenado por lo que más pasa. Empezá siempre por:

```bash
cotorra doctor
```

---

## No arranca

### `ffmpeg on PATH: missing`

Es la dependencia de sistema obligatoria. Sin ffmpeg no hay ingesta de audio.

```bash
# Debian / Ubuntu
sudo apt install ffmpeg
# macOS
brew install ffmpeg
# Windows
winget install Gyan.FFmpeg
```

Con Docker ya viene en la imagen.

### `GEMINI_API_KEY is not set`

Falla al arrancar a propósito, no a mitad del keynote. Conseguila en
<https://aistudio.google.com/apikey> y ponela en `.env`.

Para probar sin credenciales: `COTORRA_ENGINE=mock`.

### El puerto 8080 está ocupado

```bash
PORT=9000 cotorra serve
```

Con Docker, `PORT=9000 docker compose up` cambia sólo el puerto del host.

### `Cannot write to .../data` en Docker

El bind mount `./data` es de tu usuario del host y el contenedor corre como uid 10001.

```bash
sudo chown -R 10001:10001 ./data
```

Cotorra **no se cae** por esto: sigue subtitulando en vivo y sólo desactiva la persistencia
y los exports. El log lo dice explícitamente.

---

## Arranca pero no salen subtítulos

Mirá el semáforo en `/ops` antes que cualquier otra cosa.

### 🟡 amarillo + "No audio for Ns"

**Casi siempre es cableado, no software.** El watchdog mira el nivel de audio real, así que
este mensaje significa que ffmpeg está conectado pero no entra señal.

Verificá, en este orden: el canal en la consola de audio, el cable, y el mute del encoder.

Probá la fuente por separado:

```bash
ffmpeg -i rtmp://tu-ingest/live/stage1 -t 10 -f null -
```

### 🟡 amarillo + "Worker exited with code N; restart 1 in 1s"

El worker se cayó y el supervisor ya lo está reiniciando. **No toques nada.** Si vuelve a
verde solo, listo. Si llega a 🔴 "exhausted 5 restarts", mirá el log.

### 🔴 rojo con un error de la API

Tres llamadas fallidas seguidas. El mensaje real está en el panel y en el log.

| Mensaje | Qué es |
|---|---|
| `Free-tier quota exhausted` | la key es del tier gratuito: 15-20 requests **por día**. No alcanza para un evento; habilitá billing. Ver [engines.md](engines.md#cuotas-lo-primero-que-te-va-a-frenar) |
| `429` / `RESOURCE_EXHAUSTED` | límite por minuto. Bajá `WORKER_CONCURRENCY` y/o `PARTIALS=false` |
| `Manually set deadline is too short` | `GEMINI_TIMEOUT_S` por debajo del mínimo de la API. Cotorra ya pone un piso; si lo ves, actualizá |
| `403` / `PERMISSION_DENIED` | la API key es inválida o no tiene habilitada la API |
| `503` / timeouts | la API está teniendo un mal momento. Se recupera solo |
| `Audio source not found` | la ruta del archivo está mal en `cotorra.yaml` |

### Verde pero cero líneas

El VAD no está detectando habla. Causas habituales:

- **El audio está muy bajo.** Mirá el medidor en `/ops`. Si está casi vacío con alguien
  hablando, subí la ganancia en origen.
- **Hay mucho ruido de fondo constante.** El piso de ruido adaptativo se acostumbra y sube
  la vara. Bajá `ABS_THRESHOLD`, o mejor, arreglá el audio.
- **Realmente no hay habla** — un video, una demo muda. Es el comportamiento correcto.

---

## Sale, pero mal

### Nombres propios y términos técnicos mal escritos

**Es para lo que existe el glosario**, y es la mejora más barata que hay.

```
# data/glossaries/nerdearla.txt
Nerdearla
Ariel Jolo
eBPF
```

Reiniciá la sesión para que lo tome. Cargá los nombres de los oradores la semana previa:
es exactamente lo que los modelos genéricos escriben mal.

### Frases cortadas al medio

El orador habla sin pausar y se llega al techo de 7 segundos.

```bash
MAX_SEGMENT_MS=9000    # frases más completas, más latencia
SILENCE_MS=600         # espera más antes de dar por cerrada una frase
```

Es un intercambio directo: cada milisegundo que agregás acá es un milisegundo que la
audiencia espera.

### Se comen el principio de las palabras

```bash
PREROLL_MS=500    # default 300
```

### Repite la frase anterior

El modelo está ignorando la instrucción de no repetir el contexto. Si pasa seguido con tu
modelo, abrí un issue con ejemplos — puede requerir ajustar el prompt del sistema.

### Los subtítulos parpadean o se reescriben mucho

Son los parciales haciendo su trabajo: se reemplazan en el lugar cuando la frase cierra.
Si molesta (sobre todo en un stream grabado):

```bash
PARTIALS=false
```

o en el overlay: `?partials=0`. Bonus: sale 44% más barato.

---

## Latencia alta

Mirá el p95 en `/ops`, por escenario.

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| p95 alto, p50 normal | picos de la API o rate limiting | bajar `WORKER_CONCURRENCY` |
| p50 y p95 altos parejo | el modelo está lento, o es `local` en CPU | modelo más chico, o GPU |
| Sube con más escenarios | te quedaste sin CPU | menos escenarios por host, o escalar horizontal |
| Se siente lento pero p95 es bajo | estás midiendo mal | ver abajo |

**La latencia que reporta Cotorra es del fin del enunciado al subtítulo.** Lo que la
audiencia percibe incluye además esperar a que la frase termine. Si el p95 es de 800 ms pero
"se siente" en 3 segundos, no es un bug: es la mitad de la frase. Eso lo arreglan los
parciales, no un modelo más rápido.

---

## Fuentes de audio

### YouTube no anda

```bash
pip install yt-dlp
yt-dlp --version
```

yt-dlp se rompe cuando YouTube cambia algo; actualizalo antes del evento. Alternativa
robusta para un evento: descargá el audio antes y apuntá a un archivo.

### RTMP se corta a cada rato

Cotorra ya pasa `-reconnect` a ffmpeg, pero si el encoder corta la conexión, no hay nada que
hacer del lado receptor. Revisá la red y el bitrate del encoder.

### El micrófono no se encuentra

Listá los dispositivos:

```bash
# Windows
ffmpeg -list_devices true -f dshow -i dummy
# macOS
ffmpeg -f avfoundation -list_devices true -i ""
# Linux
arecord -l
```

Después: `source: "device:Nombre Exacto Del Dispositivo"`.

---

## Web y red

### Los lectores no ven nada, la API responde bien

Casi siempre es un reverse proxy que no hace upgrade de WebSocket. Ver la
[config de nginx](deploy.md#detrás-de-un-reverse-proxy).

### El overlay se reinicia al cambiar de escena en OBS

Destildá **"Shutdown source when not visible"** y **"Refresh browser when scene becomes
active"** en el browser source.

### El panel `/ops` no deja iniciar ni detener

Necesita el token en la URL: `?token=TU_ADMIN_TOKEN`. Sin él es de solo lectura, a propósito.

### Se cortan las conexiones después de un rato

Un proxy o balanceador está matando sockets inactivos. Una charla puede tener minutos de
silencio:

```nginx
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
```

---

## Docker

### `docker compose up` construye pero el contenedor se reinicia

```bash
docker compose logs gateway
```

Lo más común es un `.env` malformado. Probá sin él primero: los defaults funcionan.

### Docker Desktop no arranca (Windows)

Si ves un error sobre el *Inference manager* y un socket en
`AppData\Local\Docker\run\dockerInference`, es un problema de Docker Desktop, no de Cotorra.
Mientras tanto, la instalación sin Docker funciona igual:

```bash
pip install -e ".[gemini]"
cotorra serve
```

---

## Reportar un bug

Lo que hace falta para que alguien pueda ayudarte:

- salida de `cotorra doctor`
- motor y tipo de fuente
- cuántos escenarios simultáneos
- el log del gateway (los workers escriben en el mismo stdout)
- p50/p95 del panel si el problema es de latencia

Hay una [plantilla de issue](../.github/ISSUE_TEMPLATE/bug_report.yml) que pide justo eso.
