# Calidad de los subtítulos

Cómo medirla, y qué tocar cuando no alcanza. En orden de impacto real: las primeras dos
secciones mueven mucho más la aguja que las últimas.

> **Antes que nada:** probá con el audio de **tu** evento. Los acentos de tus oradores, el
> ruido de tu sala y la jerga de tu comunidad son lo que decide la calidad, y nada de eso
> aparece en una demo con audio de estudio.

---

## 1. El glosario (el mayor impacto por lejos)

Una conferencia tiene vocabulario cerrado y los modelos genéricos lo escriben mal de forma
predecible: nombres de oradores, sponsors, el proyecto del que todos hablan esa semana.

```
# data/glossaries/nerdearla.txt
Nerdearla
Sysarmy
Ariel Jolo
eBPF
```

Se manda en **cada llamada** y cuesta fracciones de centavo por hora.

**Qué poner, en orden de rendimiento:**

1. **Nombres de los oradores.** Es lo más visible y lo más vergonzoso cuando sale mal.
2. **Nombres de sponsors y del evento.**
3. **Tecnologías con grafía rara**: `eBPF`, `gRPC`, `PostgreSQL`, `Next.js`, `Kubernetes`.
4. **Siglas propias de tu comunidad.**

**Qué NO poner:** palabras comunes. El glosario se corta en 150 términos y cada uno ocupa
lugar en el prompt; llenarlo de `deploy` y `servidor` desplaza los términos que sí importan.

Cargalo **la semana previa**, cuando ya tenés el programa confirmado. Es la tarea de
preparación con mejor relación esfuerzo/resultado que existe en todo el sistema.

`data/glossaries/default.txt` se aplica a todas las sesiones; el que nombre la sesión se
suma encima.

---

## 2. El audio de entrada

**Ningún modelo arregla un audio malo.** Antes de tocar parámetros, verificá la fuente.

Mirá el medidor de nivel en `/ops` con alguien hablando:

| Lo que ves | Qué pasa |
|---|---|
| Barra casi vacía | ganancia baja en origen. Subila, no compenses por software |
| Barra siempre llena | está saturando y clippeando. Bajala |
| Se mueve con el habla, ~40–70% | correcto |

Qué mandarle a Cotorra, en orden de preferencia:

1. **El *program mix* de la consola**, o mejor todavía un aux/mix-minus con sólo los
   micrófonos. Es lo ideal.
2. El audio del encoder de streaming. Anda bien.
3. Un micrófono de ambiente en la sala. Funciona, pero peor: se lleva aplausos, tos y el
   aire acondicionado.

Si el audio del stream ya trae música de transición o el audio de los videos que proyecta
el orador, eso también entra al modelo. Un mix-minus lo evita.

---

## 3. Segmentación

Define cuánto contexto recibe el modelo por llamada, y es un intercambio directo con la
latencia.

| Variable | Default | Subirlo | Bajarlo |
|---|---|---|---|
| `MAX_SEGMENT_MS` | 7000 | frases más completas, más latencia | más reactivo, más cortes a mitad de idea |
| `SILENCE_MS` | 420 | espera más la pausa; mejor con oradores lentos | corta antes; mejor con oradores rápidos |
| `MIN_SEGMENT_MS` | 900 | ignora más ruiditos | capta interjecciones cortas |
| `PREROLL_MS` | 300 | no se come arranques de palabra | menos audio redundante |

**Frases cortadas al medio** → subí `MAX_SEGMENT_MS` a 9000 y `SILENCE_MS` a 600.
**Se comen el principio de las palabras** → `PREROLL_MS=500`.
**Transcribe toses y ruidos** → subí `MIN_SEGMENT_MS` a 1200.

Cambiar esto cuesta latencia percibida. Antes de tocarlo, verificá que el problema no sea
el glosario o el audio.

---

## 4. Elección de modelo

Ver [engines.md](engines.md) para el detalle. El resumen:

- `gemini-2.5-flash` con `GEMINI_THINKING_BUDGET=0` es el punto de partida correcto.
- Subir el thinking budget (128–512) **puede** ayudar con acentos muy marcados o audio
  ruidoso. Medí: si el p95 sube más de lo que baja el error, no vale la pena.
- El motor `local` con `small` es notablemente peor con jerga técnica que `gemini`. Con
  `large-v3` en GPU se acerca, a costa de latencia.

---

## Cómo medir en serio

### La prueba de 10 minutos (hacela siempre)

Antes de cualquier ajuste, y antes del evento:

```bash
# una charla real de tu propia conferencia, 10 minutos
python scripts/fetch_samples.py 'https://www.youtube.com/watch?v=...' \
    --id prueba --lang en --targets es --minutes 10

# agregala a cotorra.yaml y arrancala, después exportá
cotorra export prueba --lang es --formats txt
```

Leé el TXT completo. Buscá específicamente:

- [ ] ¿Los nombres propios están bien escritos? → si no, **glosario**
- [ ] ¿Hay frases cortadas al medio? → `MAX_SEGMENT_MS`
- [ ] ¿Hay texto inventado en los silencios? → problema serio, reportalo
- [ ] ¿Se repite la frase anterior? → problema de contexto, reportalo
- [ ] ¿La traducción suena natural en español rioplatense, o traducida literal?
- [ ] ¿Los términos técnicos quedaron **sin traducir**, como corresponde?

### WER: todavía no lo tenemos

No hay un número de *word error rate* en el repo. Eso significa que hoy podemos decir
"suena bien" pero no "este cambio mejoró un 3%", y es una limitación real.

Hay un [issue abierto](roadmap.md#medir-de-verdad-la-calidad--good-first-issue) para armar
el harness. Lo que haría falta:

1. Tres charlas cortas con transcripción de referencia hecha a mano.
2. Un script que corra Cotorra contra el archivo, exporte el TXT y calcule WER.
3. Reportar además **WER sólo sobre los términos del glosario** — ése es el número que de
   verdad importa en una conferencia técnica.

Si vas a usar esto en un evento grande, armarlo vale la pena.

---

## Qué esperar de forma realista

Con audio limpio de consola, glosario cargado y `gemini-2.5-flash`:

| | Expectativa |
|---|---|
| Habla clara, vocabulario general | muy bueno |
| Jerga técnica **en el glosario** | muy bueno |
| Jerga técnica **fuera del glosario** | aceptable; errores predecibles |
| Acentos regionales marcados | bueno |
| Dos personas hablando encima | pobre — ningún sistema resuelve esto bien |
| Nombres propios sin glosario | **pobre**. Es literalmente para esto que existe el glosario |
| Cambio de idioma a mitad de frase | variable |

**Dónde falla y no lo va a arreglar un parámetro:** paneles con varias personas
interrumpiéndose, audio de sala sin micrófono, y preguntas del público sin micrófono. Si tu
evento tiene preguntas del público, poné un micrófono inalámbrico — ayuda a la accesibilidad
mucho más que cualquier ajuste de software.

---

## Una nota sobre no inventar

El prompt del sistema le dice explícitamente al modelo que devuelva vacío si no hay habla
inteligible, y que no complete frases cortadas.

Esto importa más de lo que parece: **en una herramienta de accesibilidad, quien lee no puede
verificar contra el audio.** Un subtítulo alucinado que suena plausible es peor que una
pantalla en blanco, porque la persona no tiene forma de saber que está mal.

Si ves al modelo inventando texto en los silencios, es un bug que queremos conocer. Abrí un
issue con el audio y la salida.
