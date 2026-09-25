# Desplegar y operar

Tres escalones, según cuántos escenarios tengas. Empezá por el primero: alcanza para la
enorme mayoría de las conferencias.

---

## El setup de Nerdearla, con Cotorra

El equipo de Nerdearla contó cómo subtitula hoy: la placa de audio de cada sala sale por un
jack de 3,5 mm a una mini PC, un servicio pago transcribe en el navegador, las pantallas de
la sala muestran ese navegador, hay un QR para seguirlo en el celular, y si algo se traba
alguien entra por escritorio remoto a reiniciarlo. La audiencia virtual no tiene
traducción.

| Hoy | Con Cotorra |
|---|---|
| jack 3,5 mm → mini PC | igual: `source: "device:<nombre de la entrada>"` en `cotorra.yaml` (ver [cómo listar dispositivos](troubleshooting.md#el-micrófono-no-se-encuentra)) |
| un servicio pago por sala | un proceso por sala, US$0,31 por hora |
| la pantalla muestra el navegador que transcribe | la [pantalla del escenario](obs.md#la-pantalla-del-escenario), con QR |
| QR para el celular | el mismo QR, que abre la vista de audiencia con elección de idioma |
| sin traducción en el stream | el overlay en vMix: [docs/obs.md](obs.md) |
| escritorio remoto para apretar F5 | el panel `/ops`: semáforo por sala, y los workers caídos se reinician solos |

La mini PC puede correr Cotorra completo, o sólo el worker de esa sala apuntando a un
gateway central (`cotorra worker --source "device:Line In" --gateway ws://central:8080`),
así el panel y las pantallas de todas las salas quedan en un solo lugar.

Para ensayar la recuperación antes del evento: `/ops?token=…&simulacro=1` muestra un botón
💥 por sala que tira el worker a la fuerza, exactamente como lo haría una caída real.

---

## 1. Una máquina (1–12 escenarios)

Lo que hace `docker compose up`. Un contenedor, sin Redis, sin base de datos.

### Cuánto hardware

Medido con el motor `gemini`, donde el trabajo pesado está en la API y la máquina sólo
decodifica audio y mueve bytes:

| Por escenario | |
|---|---|
| CPU | ~0,2 vCPU (casi todo ffmpeg decodificando) |
| RAM | ~120 MB (worker + buffers) |
| Red saliente | ~35 kB/s de audio hacia la API |
| Red entrante por lector | ~0,5 kB/s |

| Escenarios | Máquina razonable |
|---|---|
| 1–4 | cualquier laptop |
| 5–12 | 4 vCPU / 8 GB |
| 12–30 | 8 vCPU / 16 GB, o repartir en dos hosts |

Con `COTORRA_ENGINE=local` la cuenta cambia por completo: Whisper corre en tu máquina.
Contá una GPU por cada 2–4 escenarios, o `small`+`int8` en CPU para 1–2.

`MAX_LOCAL_WORKERS` (default 12) es un freno a propósito: si alguien intenta arrancar el
escenario 13 en un host que no da, la API responde 409 con el motivo en vez de tirar la
máquina abajo en el medio del evento.

### Detrás de un reverse proxy

WebSockets necesitan el upgrade explícito:

```nginx
server {
    listen 443 ssl http2;
    server_name subtitulos.tu-conferencia.org;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # Una charla puede tener minutos de silencio: no cortes el socket.
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

Con Caddy son dos líneas y el TLS sale solo:

```caddy
subtitulos.tu-conferencia.org {
    reverse_proxy 127.0.0.1:8080
}
```

---

## 2. Varias réplicas del gateway (12–50 escenarios)

El gateway no guarda nada de los subtítulos en vuelo, así que se replica sin más. Lo único
que hace falta es que un worker conectado a la réplica A pueda alimentar a un lector
conectado a la réplica B. Eso lo resuelve Redis:

```bash
# .env
REDIS_URL=redis://redis:6379/0
```

```bash
docker compose --profile scale up --scale gateway=3
```

Qué cambia internamente: `MemoryHub` pasa a ser `RedisHub`, que publica cada subtítulo en
un canal y lo reparte localmente en cada réplica. **Ni una línea de la aplicación cambia.**

Cosas a tener en cuenta:

- Los workers se reparten entre réplicas. Cada réplica supervisa sus propios workers, así
  que `MAX_LOCAL_WORKERS` aplica por réplica.
- El *sticky session* no hace falta para los lectores, pero sí ayuda: reconectar a la misma
  réplica evita re-enviar el backlog.
- `data/` tiene que ser un volumen compartido si querés exportar desde cualquier réplica.
  Si no, exportá desde la réplica que corrió la sesión.

---

## 3. Un worker por Pod (50+ escenarios, multi-sede)

Acá el supervisor local ya no alcanza y querés que Kubernetes programe los workers.

**Lo que hay que escribir son cuatro métodos.** `SessionManager` habla con el supervisor a
través de esta interfaz y nada más:

```python
class KubernetesSupervisor:
    def spawn(self, spec: SessionSpec) -> None:
        """Creá un Job/Pod con la imagen de cotorra y COTORRA_SESSION_SPEC en el env."""

    def stop(self, session_id: str, timeout: float = 8.0) -> None:
        """Borrá el Pod."""

    def wait(self, session_id: str, timeout: float) -> bool:
        """True si el Pod terminó dentro del timeout."""

    def reap(self) -> list[tuple[str, int]]:
        """(session_id, exit_code) de los Pods que terminaron desde la última llamada."""

    def running(self, session_id: str) -> bool: ...
    def stop_all(self) -> None: ...
```

El Pod corre exactamente el mismo comando que un worker local:

```yaml
containers:
  - name: worker
    image: ghcr.io/santiagooroz/cotorra:latest
    command: ["cotorra", "worker", "--gateway", "ws://cotorra-gateway:8080",
              "--token", "$(WORKER_TOKEN)"]
    env:
      - name: COTORRA_SESSION_SPEC
        value: '{"id":"stage-1","source":"rtmp://...","targets":["es"],"engine":"gemini"}'
      - name: GEMINI_API_KEY
        valueFrom: { secretKeyRef: { name: cotorra, key: gemini-api-key } }
    resources:
      requests: { cpu: "200m", memory: "128Mi" }
      limits:   { cpu: "1",    memory: "512Mi" }
```

El worker ya se reconecta solo al gateway, así que reprogramar un Pod es transparente.

> Este supervisor todavía no está implementado — es
> [un issue abierto](../../../issues?q=label%3A%22help+wanted%22) y un buen primer aporte
> grande. La interfaz está estable y testeada contra `LocalSupervisor`.

---

## El día del evento

### La noche anterior

```bash
cotorra doctor
```

Verifica ffmpeg, el SDK, la API key, el manifiesto y los permisos de escritura. Corrélo
cuando todavía hay tiempo de arreglar algo.

Después:

1. Cargá los nombres de los oradores y sponsors en `data/glossaries/`. Es la mejora de
   calidad más barata que hay.
2. `ADMIN_TOKEN` y `WORKER_TOKEN` configurados. Sin excepción.
3. Probá **cada** fuente RTMP/HLS con una sesión de prueba. La mayoría de los problemas de
   un evento en vivo son de cableado, no de software.
4. Corré `python scripts/scale_test.py --sessions N` con tu N real, en el hardware real.

### Durante

Tené `/ops` en una pantalla. Qué mirar:

| Señal | Qué pasó | Qué hacer |
|---|---|---|
| 🟡 + "No audio for Ns" | el feed está mudo | revisar la consola de audio, no el software |
| 🟡 + "Worker exited… restart N" | el worker se cayó y volvió solo | mirar, no tocar; se recupera |
| 🔴 + "exhausted N restarts" | algo sistemático | ver el log; suele ser la fuente o la API key |
| p95 subiendo sin errores | la API está lenta o hay rate limit | bajar `WORKER_CONCURRENCY`, o `PARTIALS=false` |
| costo acumulado disparado | más escenarios de los previstos | está bien, es un dólar; verificalo igual |

Un escenario se reinicia sin tocar nada más:

```bash
cotorra stop stage-3 && cotorra start stage-3
```

El `stop` es cooperativo: el worker publica los subtítulos que tenía en vuelo antes de
salir, así que no se pierde la última frase.

### Después

```bash
for s in $(cotorra sessions --api http://localhost:8080 | ...); do
  cotorra export "$s" --lang es --formats srt,vtt,txt --out ./transcripciones
done
```

Subí los SRT a YouTube y las charlas quedan subtituladas y buscables.

---

## Monitoreo

`/metrics` habla Prometheus:

```yaml
scrape_configs:
  - job_name: cotorra
    static_configs: [{ targets: ["cotorra:8080"] }]
```

Alertas que valen la pena:

```yaml
- alert: EscenarioCaido
  expr: cotorra_session_up == 0
  for: 30s

- alert: LatenciaAlta
  expr: cotorra_latency_p95_ms > 3000
  for: 2m

- alert: ErroresDeMotor
  expr: rate(cotorra_errors_total[5m]) > 0.1
  for: 1m
```
