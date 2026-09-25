# Desplegar en Google Cloud Run

Una URL pública con HTTPS en unos minutos, y sin ninguna API key: en Cloud Run el contenedor
se autentica a Vertex AI con su propia service account.

```bash
gcloud auth login
./deploy/cloudrun.sh TU-PROYECTO-GCP
```

El script imprime la URL, el token de admin y el comando para borrarlo todo.

## Los cuatro flags que no son opcionales

`deploy/cloudrun.sh` los pone, pero si desplegás a mano, estos cuatro son la diferencia
entre que funcione y que no:

| Flag | Por qué |
|---|---|
| `--no-cpu-throttling` | los workers son subprocesos en segundo plano. Sin esto, Cloud Run les corta la CPU entre requests HTTP y **los subtítulos se frenan** |
| `--min-instances 1 --max-instances 1` | sin Redis, un worker en la instancia A y un lector en la B no se ven. Una sola instancia, o configurá `REDIS_URL` |
| `--timeout 3600` | una charla mantiene el WebSocket abierto una hora. 3600 s es el máximo que permite Cloud Run |
| `--service-account` con `roles/aiplatform.user` | es lo que reemplaza a la API key |

## Cuánto cuesta

Una instancia siempre encendida ronda **US$0,10–0,15 por hora**, más las llamadas al modelo
(ver [costs.md](../docs/costs.md)). Un día entero de evento: unos US$3 de cómputo.

**Borrala cuando termines:**

```bash
gcloud run services delete cotorra --region us-central1
```

## Antes de poner la URL en el README o en un video

> ⚠️ **Si estás usando créditos con vencimiento**, fijate cuándo expiran. Una URL muerta en
> el README es peor que ninguna URL: el jurado hace clic, no carga, y esa es la impresión
> que queda. Si el crédito vence antes de que te evalúen, dejá el despliegue como
> instrucciones reproducibles (este archivo) en vez de como un link.

Si la dejás arriba:

- [ ] `ADMIN_TOKEN` configurado (el script genera uno)
- [ ] `WORKER_TOKEN` cambiado
- [ ] probá `/api/health` desde otra red, no sólo desde tu máquina
- [ ] probá la vista de audiencia **desde un celular con datos móviles**
- [ ] `MAX_LOCAL_WORKERS` acorde a la CPU que le diste

## Alternativas

Cloud Run es cómodo pero no es la única opción, y para un evento real quizás no sea la
mejor: la ingesta de audio suele estar en la red del venue, y mandar RTMP a la nube para
que vuelva como subtítulos agrega latencia de ida y vuelta.

Para un evento de verdad, lo normal es correr Cotorra **en la misma red que el switcher de
audio**. Ver [docs/deploy.md](../docs/deploy.md).
