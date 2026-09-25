# Motores

Un motor recibe unos segundos de audio y devuelve **la transcripción y todas las
traducciones juntas**. Esa es toda la interfaz. Cambiar de motor no requiere tocar nada
más: el panel, el overlay, los exports y la API funcionan igual.

```bash
COTORRA_ENGINE=gemini    # en .env, o por sesión en cotorra.yaml
```

Se puede mezclar: el keynote con `gemini` y un escenario chico con `local`, en el mismo
evento.

```yaml
sessions:
  - id: keynote
    engine: gemini
  - id: taller
    engine: local
```

---

## `gemini` — el recomendado

Gemini 2.5 Flash multimodal. Una llamada por enunciado devuelve transcript + cada idioma.

```bash
pip install 'cotorra[gemini]'
```

```bash
GEMINI_API_KEY=...                  # https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-2.5-flash
GEMINI_THINKING_BUDGET=0
GEMINI_TIMEOUT_S=8        # nuestro presupuesto por intento
GEMINI_DEADLINE_S=10      # presupuesto total del enunciado
GEMINI_MAX_RETRIES=3
```

### `GEMINI_THINKING_BUDGET=0` no es un ahorro, es latencia

Transcribir literalmente no tiene nada que razonar. Los tokens de pensamiento agregan
cientos de milisegundos a cada enunciado, y en subtitulado en vivo eso es lo único que
importa. Dejalo en 0.

La excepción: audio muy ruidoso o acentos muy marcados donde el modelo se equivoca
sistemáticamente. Probá 128–512 y medí — si el p95 sube más de lo que baja el error, no
vale la pena.

### Reintentos y timeouts

Backoff corto (0,4 s, 0,8 s, 1,6 s) y, ante un 429, el `retryDelay` que la propia API
devuelve — pero sólo si es menor a `GEMINI_MAX_RETRY_WAIT_S`. Más que eso y el enunciado se
descarta: **un subtítulo que llega 10 segundos tarde no es un subtítulo tarde, es uno
equivocado**, y como la publicación es ordenada, arrastra a todos los que vienen atrás.

Tres errores seguidos ponen el escenario en 🔴 en el panel.

### Cuotas: lo primero que te va a frenar

Medido el 24/09/2026 con una key de tier gratuito:

| Modelo | Cuota gratuita | Sirve para un evento |
|---|---|---|
| `gemini-2.5-flash` | 20 requests/día | no |
| `gemini-3.5-flash-lite` | 15 requests/día | no |

Una charla de una hora necesita entre 400 y 900 llamadas. **El tier gratuito alcanza para
probar que la key funciona y para nada más.** Cada modelo tiene su propio bucket, así que
cambiar de modelo te da otro puñado de llamadas, pero no es una estrategia.

Con billing habilitado los límites pasan a ser por minuto y el costo real es de centavos:
ver [costs.md](costs.md).

Verificalo antes del evento, no durante:

```bash
cotorra doctor --live      # una llamada real: prueba la key y muestra la cuota
```

Cuando se agota, Cotorra lo resume en una línea y pone el escenario en rojo:

```
Free-tier quota exhausted on gemini-3.5-flash-lite (15 requests/day).
Enable billing, or set GEMINI_MODEL to a model whose quota is still free.
```

### Vertex AI: la forma de salir del tier gratuito

El mismo SDK habla con dos backends. La diferencia que importa no es técnica, es de cuotas
y de facturación:

| | AI Studio | Vertex AI |
|---|---|---|
| Credencial | una API key | un proyecto de GCP + ADC |
| Cuota gratuita | 15-20 requests **por día** | por minuto, con billing |
| Créditos de Google Cloud | no aplican | **sí aplican** |
| En Cloud Run | hay que inyectar la key | la service account se autentica sola |

```bash
GEMINI_USE_VERTEX=true
GCP_PROJECT=tu-proyecto
GCP_LOCATION=us-central1
```

Hay un script que hace todo el camino de una pasada — crea el proyecto, lo vincula a la
cuenta de facturación, habilita la API, escribe el `.env` y deja todo listo:

```bash
gcloud auth login                        # abre tu navegador
gcloud auth application-default login    # esto es lo que usa Vertex
python scripts/setup_vertex.py
cotorra doctor --live
```

Si preferís hacerlo a mano, son las mismas cuatro cosas:

```bash
gcloud projects create cotorra-<algo>
gcloud billing projects link cotorra-<algo> --billing-account <ID>
gcloud services enable aiplatform.googleapis.com --project cotorra-<algo>
gcloud auth application-default set-quota-project cotorra-<algo>
```

Si tenés créditos de Google Cloud (hackathon, GDP, startup), **éste es el camino que los
usa**. Una API key de AI Studio los ignora salvo que vincules el proyecto que la emitió a
una cuenta de facturación.

En Cloud Run no hace falta credencial ninguna: ver [deploy/README.md](../deploy/README.md).

### Los modelos Flash-Lite no tienen modo *thinking*

Y rechazan el parámetro con un `400 INVALID_ARGUMENT` genérico, sin decir cuál argumento.
Cotorra lo detecta en la primera llamada, descarta `thinking_config` y reintenta, así que
podés cambiar de modelo sin tocar nada. Lo vas a ver una vez en el log:

```
gemini-3.5-flash-lite does not accept thinking_config
(Flash-Lite models have no thinking mode); dropping it and retrying
```

### Dos relojes distintos

La API rechaza un deadline HTTP menor a 10 segundos. Pero para subtitular en vivo querés
abandonar una llamada mucho antes que eso. Por eso hay dos valores:

| | Qué es | Default |
|---|---|---|
| `GEMINI_TIMEOUT_S` | el presupuesto que a Cotorra le importa, por intento | 8 s |
| `GEMINI_DEADLINE_S` | presupuesto total del enunciado, sumando reintentos | 10 s |

El transporte siempre recibe al menos los 10 s que la API exige; el que decide si un
subtítulo todavía vale la pena es el nuestro. **Sin este límite un timeout producía
subtítulos de 26 segundos** que, además, frenaban a todos los que venían atrás, porque la
publicación es ordenada.

### Elegir el modelo

| Modelo | Cuándo |
|---|---|
| `gemini-2.5-flash` | default. El mejor balance calidad/latencia/costo. |
| `gemini-flash-latest` | te sigue la última Flash automáticamente. Cómodo, pero el comportamiento puede cambiar bajo tus pies en medio de un evento. |
| Un modelo Pro | sólo si medís que la calidad lo justifica. Más latencia y bastante más caro. |

> **Para un evento, fijá la versión del modelo.** Que el modelo cambie solo el día de la
> conferencia es exactamente el tipo de sorpresa que no querés.

---

## `local` — sin internet, sin API

faster-whisper para ASR + Gemma vía Ollama para traducir. Nada sale de la máquina.

Cuándo usarlo: venue sin uplink confiable, evento con requisitos de privacidad, o
simplemente porque no querés depender de una API paga.

### Instalación

```bash
pip install 'cotorra[local]'

# Ollama para la traducción
curl -fsSL https://ollama.com/install.sh | sh     # o docker compose --profile local up
ollama pull gemma3:4b
```

```bash
COTORRA_ENGINE=local
LOCAL_WHISPER_MODEL=small
LOCAL_WHISPER_DEVICE=auto          # auto | cpu | cuda
LOCAL_WHISPER_COMPUTE_TYPE=int8    # int8 | int8_float16 | float16
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:4b
```

### Elegir el modelo de Whisper

| Modelo | VRAM / RAM | Velocidad relativa | Calidad |
|---|---|---|---|
| `tiny` | ~1 GB | ~10x | sólo para probar que anda |
| `base` | ~1 GB | ~7x | pobre con jerga técnica |
| `small` | ~2 GB | ~4x | **el mínimo usable en vivo** |
| `medium` | ~5 GB | ~2x | bueno; necesita GPU para tiempo real |
| `large-v3` | ~10 GB | 1x | el mejor; GPU obligatoria |

"Velocidad relativa" es respecto a tiempo real. **Para subtítulos en vivo necesitás al
menos 2–3x**, porque el enunciado tiene que procesarse mientras entra el siguiente.

Configuraciones que funcionan:

- **CPU sola:** `small` + `int8`. Alcanza para 1–2 escenarios en una máquina decente.
- **Una GPU:** `medium` o `large-v3` + `float16`. Unos 2–4 escenarios por GPU.

`beam_size=1` (greedy) está fijo a propósito: es ~2x más rápido y para subtítulos en vivo
la diferencia de calidad contra beam search no justifica la latencia.

### Traducción con Gemma

Acá está la diferencia real contra `gemini`: son **dos pasos**, no uno. Whisper transcribe
y después Gemma traduce texto ya transcripto, sin escuchar el audio. Se pierde la prosodia
y el contexto acústico, y cada idioma extra **sí** agrega una llamada.

Modelos de Ollama razonables:

| Modelo | RAM | Notas |
|---|---|---|
| `gemma3:4b` | ~4 GB | default. Bien para es↔en. |
| `gemma3:12b` | ~10 GB | notablemente mejor, más lento. |
| `qwen2.5:7b` | ~6 GB | fuerte en idiomas asiáticos. |

### Expectativa honesta de latencia

El motor `local` es **2–4x más lento** que `gemini` en hardware comparable, y bastante peor
con jerga técnica salvo que uses `large-v3`. Es la opción correcta cuando *no podés* usar
una API, no cuando simplemente preferirías no hacerlo.

Un decode de Whisper por vez está serializado con un lock a propósito: encolar es mejor que
hacer thrashing contra una sola GPU.

---

## `mock` — sin credenciales

Recorre el pipeline completo con texto de guion. No es un placeholder inútil, resuelve tres
problemas concretos:

1. Alguien clona el repo y ve el sistema andando en 5 segundos, sin API key.
2. CI corre completo, gratis y sin red.
3. El test de carga de 10 escenarios se mide sin pagar un centavo — y aun así reporta el
   **costo que Gemini habría cobrado** por ese volumen real de tokens.

Cada subtítulo que produce lleva `demo: true` y **todas las interfaces muestran un cartel de
advertencia**. Subtítulos falsos que parezcan reales serían peores que ningún subtítulo,
especialmente en una herramienta de accesibilidad.

---

## Comparar los tres

| | `gemini` | `local` | `mock` |
|---|---|---|---|
| Llamadas por enunciado | 1 (todos los idiomas) | 1 + una por idioma | 1 |
| Latencia típica p50 | ~700 ms | 1,5–4 s | 350 ms (simulada) |
| Costo por hora-escenario | ~US$0,33 | electricidad | US$0 |
| El audio sale de la máquina | sí | **no** | no |
| Necesita GPU | no | recomendado | no |
| Calidad con jerga técnica | muy buena | depende del modelo | n/a |
| Funciona sin internet | no | **sí** | sí |

---

## Agregar el tuyo

Un archivo y una línea. Ver
[CONTRIBUTING.md](../CONTRIBUTING.md#agregar-un-motor) para el paso a paso completo.

Lo que hace bueno a un motor, en orden de importancia:

1. **Todos los idiomas en una sola llamada**, si el modelo puede. De ahí sale la mayor parte
   de la ventaja.
2. **Nunca inventar texto.** Si no hay habla inteligible, devolvé vacío.
3. **Respetar `req.glossary`.**
4. **`estimate_cost_usd`** si cobra, para que el panel diga la verdad.
5. **Sin estado.** El worker es dueño del contexto de la conversación.
