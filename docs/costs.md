# Costos

Dos formas de llegar al número, a propósito: si coinciden, probablemente ninguna esté muy
equivocada.

```bash
python scripts/cost_model.py --talks 30 --hours 1 --languages 2   # modelo analítico
python scripts/scale_test.py --sessions 10 --seconds 60           # medición real
```

| | US$ por hora de escenario |
|---|---|
| Medición sobre charlas reales, **sin** parciales | **0,31** |
| Modelo analítico, mismas condiciones | 0,29 |
| Medición sobre charlas reales, **con** parciales | 0,96 |
| Modelo analítico, mismas condiciones | 0,97 |

Medido el 24/09/2026 con Vertex AI y `gemini-2.5-flash` sobre dos charlas reales de
Nerdearla, 2 idiomas. El modelo coincide dentro del 0,4% en el caso con parciales.

### Cómo se calibró

La primera versión de este modelo estaba **mal por un factor de 2**, y sólo se notó al
medir contra la API de verdad. Dos errores:

1. **El prompt pesa 610 tokens, no 260.** El prompt de sistema más un glosario de 85
   términos más el contexto ocupan mucho más de lo estimado, y eso se paga en *cada*
   llamada.
2. **Los parciales cuestan 3,1x, no 1,44x.** La fórmula asumía enunciados de 4 s; los
   reales son de ~5,5 s, y con un parcial cada 1,8 s eso son 3 parciales por enunciado en
   vez de 1. Cada uno reenvía todo el audio acumulado.

Las constantes de `scripts/cost_model.py` ahora salen de tokens medidos, no de estimaciones.

---

## De dónde sale

Gemini cobra distinto el audio que el texto, y por eso una estimación que los mezcla se
equivoca por un factor de ~3. El contador en vivo separa las dos cosas leyendo
`prompt_tokens_details` de la respuesta.

Para 30 charlas de 1 hora, 2 idiomas, **sin** parciales:

| Componente | US$ |
|---:|---:|
| Audio de entrada | 3,54 |
| Prompt (sistema + glosario + contexto) | 3,59 |
| Texto de salida (transcript + traducción) | 2,11 |
| **Total** | **9,24** |

Con parciales: **US$28,90**.

Las tres palancas, en orden de impacto:

1. **`PARTIALS=false` → 3,1x más barato.** Los parciales reenvían el audio acumulado y
   vuelven a generar el texto. A cambio, la audiencia espera a que la frase cierre. Es por
   lejos la decisión de producto más cara del sistema, y por eso es una variable de entorno
   y no una constante.
2. **Menos idiomas.** El costo marginal de un idioma más es sólo texto de salida: pasar de
   2 a 3 idiomas sube 11%, no 50%. Ésa es la ventaja de hacer todo en una sola llamada.
3. **Segmentos más largos.** `MAX_SEGMENT_MS` más alto = menos llamadas = menos prompt
   repetido. Cuesta latencia.

El prompt es la segunda partida más grande porque se repite en **cada** llamada. Si tu
glosario tiene 150 términos, eso son ~200 tokens extra por llamada, 45.000 veces. Vale la
pena — arregla justo los errores que más molestan — pero no lo llenes de basura.

---

## Comparar con lo que hay hoy

| | US$/hora/stream |
|---|---|
| Estenotipista o intérprete profesional | 50 – 250 |
| Servicios comerciales de subtitulado en vivo | 20 – 100 |
| **Cotorra (Gemini 2.5 Flash)** | **0,31** |
| **Cotorra (motor `local`)** | electricidad |

El punto no es reemplazar intérpretes humanos donde hacen falta. El punto es que con este
costo podés subtitular **los otros 29 escenarios** que hoy no tienen absolutamente nada.

Para Nerdearla: 30 charlas en inglés, subtituladas y traducidas, por menos de lo que sale
un café en Buenos Aires.

---

## Advertencia

Las tarifas cambian. Las de acá son de septiembre de 2026:

```
PRICE_AUDIO_IN=1.00    # US$ por 1M tokens de audio de entrada
PRICE_TEXT_IN=0.30
PRICE_TEXT_OUT=2.50
```

Verificá en <https://ai.google.dev/pricing> y actualizá tu `.env`. El script, el contador
del panel y el endpoint `/metrics` leen esos mismos valores, así que las tres cosas se
mantienen consistentes solas.

Antes de un evento grande, corré una charla de verdad durante 10 minutos con el motor real
y extrapolá desde el contador. Es el único número que no discute nadie.
