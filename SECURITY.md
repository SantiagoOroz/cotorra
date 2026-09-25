# Seguridad

## Reportar una vulnerabilidad

Abrí un [security advisory privado](../../security/advisories/new) en GitHub.
No abras un issue público para algo explotable.

Respondemos en lo que podamos: esto es un proyecto comunitario, no un producto con SLA.

## Modelo de amenaza

Cotorra corre en la red de un evento, con una API de control que puede iniciar procesos
que consumen una API paga. Los límites a tener en cuenta:

| Superficie | Quién debería llegar | Cómo se protege |
|---|---|---|
| `/` `/viewer` `/overlay` `/ws/captions/*` | **cualquiera** | sin auth, a propósito: la audiencia no debe necesitar credenciales |
| `/api/sessions` (POST/DELETE), `/start`, `/stop` | producción | `ADMIN_TOKEN` |
| `/ws/ops` | producción | `ADMIN_TOKEN` (solo lectura sin él) |
| `/ws/ingest/*` | los workers | `WORKER_TOKEN` |

## Antes de un evento real

1. **Poné `ADMIN_TOKEN`.** Vacío = cualquiera en el wifi del venue puede iniciar y detener
   escenarios, y gastar tu cuota de API. El gateway loguea una advertencia y el panel
   muestra un cartel, pero no te va a detener.
2. **Cambiá `WORKER_TOKEN`.** El default `cotorra-dev` es un placeholder.
3. **Poné el gateway detrás de TLS.** Los tokens viajan en headers y query strings; sin
   HTTPS van en claro. Cualquier reverse proxy sirve.
4. **No expongas el gateway a internet** salvo que quieras que la audiencia remota lea los
   subtítulos. Si lo hacés, exponé sólo las rutas públicas de la tabla de arriba.
5. Corré `cotorra doctor`.

## Lo que Cotorra hace con el audio

- Con el motor `gemini`, **fragmentos de audio del escenario salen hacia la API de Google**.
  Si eso no es aceptable para tu evento, usá `COTORRA_ENGINE=local`: nada sale de la
  máquina.
- El audio **no se guarda en disco**. Se procesa en memoria y se descarta.
- Las transcripciones **sí** se guardan, en `data/transcripts/`. Son el texto de lo que se
  dijo en público en un escenario; tratalas con el mismo criterio que la grabación de la
  charla.
- No hay analytics, ni telemetría, ni llamadas salientes fuera del motor que elegiste.

## Endurecimiento

- El contenedor corre como uid 10001, no root.
- Los nombres de glosario y de sesión se sanitizan contra path traversal
  ([test](tests/test_glossary_and_config.py)).
- `MAX_LOCAL_WORKERS` limita cuántos procesos puede disparar la API de control en un host.
- Las colas por suscriptor tienen tope: un lector lento se descarta, no consume memoria sin
  límite.
