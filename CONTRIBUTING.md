# Contribuir a Cotorra

Cotorra existe para que **cualquier** conferencia pueda tener subtítulos en vivo, no sólo
las que pueden pagarlos. Si tu evento necesita algo que no está, ese algo es bienvenido acá.

*English speakers: contributions in English are equally welcome — issues, PRs and code
comments in either language are fine.*

## Arrancar

```bash
git clone https://github.com/SantiagoOroz/cotorra.git && cd cotorra
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,gemini]"

cotorra doctor                  # ¿está ffmpeg? ¿están las credenciales?
pytest                          # 140 tests, sin red y sin API key
ruff check src tests scripts
python scripts/check_links.py   # ningún link de la documentación apunta a la nada
cotorra serve                   # http://localhost:8080
```

No hace falta una API key para desarrollar: el motor `mock` recorre el pipeline completo.

---

## Agregar un motor

Es **un archivo y una línea**. Digamos que querés soportar Whisper.cpp:

**1.** `src/cotorra/engines/whispercpp.py`

```python
from .base import Engine, TranscribeRequest, TranscribeResult

class WhisperCppEngine(Engine):
    name = "whispercpp"

    def __init__(self, binary: str, model: str):
        self.model = model
        ...

    async def transcribe(self, req: TranscribeRequest) -> TranscribeResult:
        # req.pcm es PCM 16 kHz mono s16le. req.targets son los idiomas pedidos.
        # Devolvé el transcript y TODAS las traducciones en un solo TranscribeResult.
        return TranscribeResult(source_lang="en", texts={"en": ..., "es": ...})
```

**2.** `src/cotorra/engines/__init__.py`

```python
def _whispercpp(s: Settings) -> Engine:
    from .whispercpp import WhisperCppEngine
    return WhisperCppEngine(binary=s.whispercpp_binary, model=s.whispercpp_model)

ENGINES = {..., "whispercpp": _whispercpp}
```

**3.** Documentá las variables nuevas en `.env.example` y agregá los campos a `Settings`.

Nada más cambia. El worker, el panel, el overlay, los exports y la API ya funcionan con tu
motor.

### Lo que hace bueno a un motor

- **Devolvé todos los idiomas en una sola llamada** si el modelo puede. Es de dónde sale la
  mayor parte de la ventaja de latencia y costo de Cotorra.
- **Nunca inventes texto.** Si el clip no tiene habla inteligible, devolvé vacío. Un
  subtítulo alucinado es peor que ningún subtítulo — especialmente en una herramienta de
  accesibilidad, donde la persona no puede verificar contra el audio.
- **Respetá `req.glossary`.** Son los nombres propios que el evento sabe que vas a escuchar.
- **Implementá `estimate_cost_usd`** si tu motor cobra. El panel lo muestra en vivo.
- **Que `transcribe` sea sin estado.** El worker es dueño del contexto de la conversación.

---

## Agregar un idioma

Para que un idioma aparezca en los selectores hace falta un par de líneas:

1. `LANG_NAMES` en `src/cotorra/engines/gemini.py` — el nombre que ve el modelo. Sé
   específico donde importa: `"Spanish (neutral Latin American)"` da mejores subtítulos que
   `"Spanish"`.
2. `LANG_LABEL` en `web/static/cotorra.js` — lo que lee la audiencia, en su propio idioma.
3. Agregalo a `targets` en `cotorra.yaml` y probalo con una charla real.

Si el idioma necesita tipografía distinta o se escribe de derecha a izquierda, decilo en el
PR: el CSS todavía no lo contempla y es un cambio que queremos hacer bien.

---

## Áreas donde más falta ayuda

Mirá los issues con [`good first issue`](../../issues?q=label%3A%22good+first+issue%22) y
[`help wanted`](../../issues?q=label%3A%22help+wanted%22). Las líneas grandes:

| Área | Por qué importa |
|---|---|
| **Gemini Live API** | streaming bidireccional nativo en vez de chunks; bajaría la latencia a menos de 500 ms |
| **Diarización** | "¿quién está hablando?" en un panel de tres personas |
| **Más idiomas** | portugués ya anda; faltan guaraní, quechua, catalán, lengua de señas |
| **Corrección en vivo** | que producción arregle un nombre mal escrito y se propague |
| **Supervisor de Kubernetes** | `spawn` / `stop` / `wait` / `reap` contra la API de Pods |
| **Calidad de la segmentación** | un VAD neuronal (Silero) en vez del de energía |

---

## Estilo de código

- **Python 3.10+**, `ruff check src tests` tiene que pasar limpio.
- **Type hints** en todo lo público.
- **Los comentarios explican el porqué, no el qué.** Si el código dice *qué* hace, un
  comentario que lo repita es ruido. `# techo de 7 s: nadie espera un subtítulo porque el
  orador no respiró` es útil; `# emitir el segmento` no.
- **Sin dependencias nuevas en el frontend.** El sitio de la audiencia es HTML, CSS y JS
  plano a propósito: carga instantáneo en wifi de venue, y cualquiera puede forkearlo y
  cambiarle un color sin instalar un toolchain. Si creés que hace falta un framework, abrí
  un issue antes del PR.
- Dependencias de Python nuevas van como **extra opcional**, no en el core.

## Tests

```bash
pytest                          # todo
pytest tests/test_segmenter.py  # lo que más impacta en latencia
```

Reglas:

- **Sin red, sin API key, sin GPU.** Toda la suite corre en CI gratis y en un avión.
- Cambios en la segmentación **necesitan** un test: es el componente que más define la
  latencia percibida y el más fácil de romper sin darse cuenta.
- Bugs de protocolo van a `tests/test_gateway.py`, que habla el WebSocket real en vez de
  un mock.
- El nombre del test describe el comportamiento, no la función:
  `test_a_failed_call_does_not_stall_the_ones_behind_it`.

## Pull requests

1. Ramificá desde `main`.
2. Un PR, un tema.
3. Si cambia algo que se ve, poné una captura o un GIF.
4. Si cambia latencia o costo, poné los números antes/después
   (`python scripts/scale_test.py`).
5. `pytest`, `ruff check` y `python scripts/check_links.py` en verde.

## Reportar un bug

Lo que más ayuda:

- salida de `cotorra doctor`
- el motor (`gemini` / `local` / `mock`) y el tipo de fuente (RTMP, archivo, YouTube, mic)
- cuántos escenarios simultáneos
- el log del gateway y el del worker (el worker escribe en el stdout del gateway)
- si aplica: la latencia p50/p95 del panel

## Código de conducta

Ver [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Se resume en: portate bien, es una
conferencia.

## Licencia

Al contribuir aceptás que tu aporte se licencie bajo [Apache 2.0](LICENSE).
