# Material de entrega

Esta carpeta **no es documentación del producto**. Es el material para presentar Cotorra a
la Vibeathon de Nerdearla 2026.

| Archivo | Qué es |
|---|---|
| [demo-video.md](demo-video.md) | Guion plano por plano del video de 1:30, con los comandos exactos para preparar cada toma |
| [devpost.md](devpost.md) | Borrador del formulario de Devpost, en inglés, más el checklist de envío |

Podés borrar esta carpeta antes de publicar el repo, o dejarla: no molesta y muestra cómo
se preparó la entrega. Lo que **sí** conviene revisar antes de publicar es que no queden
placeholders (`TU-USUARIO`, links de video vacíos).

## Fechas

| | |
|---|---|
| Ventana de construcción | 24–25 de septiembre de 2026 |
| Cierre de entregas | **25 de septiembre, 15:00 UTC** (12:00 🇦🇷 · 09:00 🇲🇽 · 17:00 🇪🇸) |

Las bases exigen que el proyecto se construya durante esa ventana.

## Criterios del jurado, y dónde los responde el proyecto

| Criterio | Dónde está la respuesta |
|---|---|
| **Calidad** | glosarios, prompt que prohíbe inventar, [docs/quality.md](../docs/quality.md) |
| **Latencia** | `thinking_budget=0`, parciales, segmentación adaptativa, p95 medido |
| **Escalabilidad** | un proceso por escenario, `scripts/scale_test.py`, los tres escalones en [docs/deploy.md](../docs/deploy.md) |
| **Despliegue y operación** | `docker compose up`, panel `/ops`, auto-recuperación, `cotorra doctor` |
| **Innovación** | una llamada multimodal para transcripción + todas las traducciones; detección de *dead air*; contador de costo en vivo |

Dos jurados lideran Developer Experience en Google DeepMind y dos organizan Nerdearla, así
que el README y las funciones de operación en vivo pesan más de lo habitual.
