# Cómo grabar el video demo, paso a paso

Pensado para alguien que **nunca vio Cotorra**. Seguilo en orden, sin saltear pasos.

- **Duración objetivo:** 1:40 (las bases piden entre 1 y 2 minutos).
- **Método:** primero grabás la **pantalla** (7 tomas cortas). Después grabás la **voz**
  aparte, leyendo un guion. Al final lo juntás en un editor.
- **Tiempo total estimado:** 2 a 3 horas la primera vez.

> ### ⏰ Grabá temprano
>
> La transcripción real usa un crédito de Google Cloud que figura con vencimiento el
> **25/09 a las 8:27** (si esa hora es UTC, son las 5:27 en Argentina). Grabá antes las
> tomas con subtítulos (1, 2, 3, 4 y 5) y generá los subtítulos en inglés de tu voz
> (paso 3.1). Editar y subir se puede hacer después.

---

## Parte 0 · Qué es Cotorra, en 30 segundos

Cotorra escucha el audio de una charla y muestra **subtítulos en vivo**, en el idioma
original y traducidos. Tiene cuatro pantallas, y vas a grabar todas:

| Pantalla | Para quién | Qué muestra |
|---|---|---|
| **Demo** (`/demo`) | el jurado | la charla real con los subtítulos encima, como en una transmisión |
| **Pantalla del escenario** (`/overlay` con fondo negro) | el público en la sala | subtítulos grandes + un QR |
| **Audiencia** (`/viewer`) | cada persona, en su celular | elige charla e idioma |
| **Producción** (`/ops`) | el equipo técnico | semáforo, latencia, costo |

**Por qué el guion está armado así:** el equipo de Nerdearla contó en el Discord cómo lo
hacen hoy. El audio de cada sala sale por un cable a una mini PC, un servicio pago
transcribe en el navegador, las pantallas de la sala muestran ese navegador, hay un QR para
seguirlo en el celular, y cuando algo se traba alguien entra por escritorio remoto a
reiniciarlo. **La audiencia virtual no tiene traducción**, y les gustaría poder
"planchar" los subtítulos en vMix. Dos de los cinco jurados son esas personas: el video les
muestra, pantalla por pantalla, que Cotorra resuelve exactamente eso.

---

## Parte 1 · Preparar la computadora (15 min)

### 1.1 Abrir una terminal en la carpeta del proyecto

En el Explorador de Windows, entrá a la carpeta `Cotorra`, hacé click en la
barra de dirección, escribí `powershell` y apretá Enter.

### 1.2 Activar el entorno y verificar

Copiá y pegá, una línea por vez:

```powershell
.venv\Scripts\activate
```

```powershell
cotorra doctor --live
```

**Tiene que decir** `live call: … OK`. Si dice otra cosa,
parate acá y mirá [Si algo sale mal](#si-algo-sale-mal).

### 1.3 Levantar Cotorra con las charlas de la demo

```powershell
$env:MANIFEST="demo.yaml"; $env:ADMIN_TOKEN="demo"; cotorra serve
```

Aparece un recuadro verde que dice **Cotorra is up**. **No cierres esta ventana** durante
toda la grabación: si la cerrás, Cotorra se apaga.

### 1.4 Preparar el navegador (Chrome o Edge)

1. Ocultá la barra de favoritos: `Ctrl + Shift + B`.
2. Cerrá todas las pestañas que no uses. Silenciá notificaciones de Windows
   (Configuración → Sistema → Notificaciones → desactivar).
3. Abrí estas 5 pestañas, **en este orden**:

| # | URL |
|---|---|
| 1 | `http://localhost:8080/demo?session=stage-mcp&lang=es&token=demo` |
| 2 | `http://localhost:8080/overlay?session=stage-mcp&lang=es&bg=000&lines=3&size=64&qr=1&title=1` |
| 3 | `http://localhost:8080/viewer?session=stage-narrativa&lang=en` |
| 4 | `http://localhost:8080/ops?token=demo&simulacro=1` |
| 5 | `http://localhost:8080/` |

### 1.5 Preparar la grabadora de pantalla

Usá la **Barra de juego de Xbox**, que ya viene en Windows:

- `Win + Alt + R` empieza a grabar **la ventana activa**; el mismo atajo la detiene.
- Los videos quedan en `Videos\Capturas`.
- Antes de empezar, apretá `Win + G` → engranaje ⚙ → **Captura**: que grabe el **audio del
  sistema** y que el **micrófono esté apagado** (la voz va aparte).

> Si preferís OBS Studio, sirve igual. Lo importante: 1080p, audio del sistema sí,
> micrófono no.

---

## Parte 2 · Grabar la pantalla (7 tomas)

**Reglas para todas las tomas:**
- Poné el navegador en **pantalla completa con F11** antes de grabar.
- Grabá **el doble** de lo que dice la duración: sobra para cortar.
- Mové el mouse **despacio**. Si una toma sale mal, repetila: no se edita, se regraba.
- Cada toma es un archivo aparte. Anotá cuál es cuál.

---

### Toma 1 · La charla con subtítulos en vivo — ⭐ la más importante
**Pestaña 1** (`/demo`) · **duración final: 15 s** · **grabá: 40 s** · **con audio**

1. Vas a ver la foto de la charla y un botón verde **▶ Empezar**.
2. Apretá **F** (pantalla completa de la página).
3. Empezá a grabar (`Win + Alt + R`).
4. Hacé click en **▶ Empezar**. Esperá. En unos segundos arranca el audio de la charla y
   aparecen los subtítulos: **en inglés arriba**, **en español abajo**.
5. **No toques nada** durante 40 segundos. Después detené la grabación.

**Tiene que verse así:** la foto del orador de fondo, arriba a la izquierda un recuadro
"● EN VIVO — Subtítulos generados en vivo por Cotorra — latencia del modelo: 2,3 s", y los
subtítulos apareciendo mientras se escucha al orador en inglés.

> **No es un truco:** Cotorra está escuchando ese audio en este momento y los subtítulos
> salen de ahí. La distancia entre lo que se oye y lo que aparece **es** la latencia real.
>
> Para repetir desde el principio: apretá **R**.

---

### Toma 2 · Cambio de idioma en el celular
**Pestaña 3** (`/viewer`) · **duración final: 10 s** · **grabá: 25 s** · sin audio

1. Esta es la charla en **español**, traducida al **inglés**.
2. Empezá a grabar. Esperá 3 segundos.
3. Abajo a la izquierda hay botones de idioma. Hacé click en **ESPAÑOL**, esperá 4 s,
   después en **ENGLISH**, esperá 4 s.
4. Hacé click en **⇄ Bilingüe**: aparecen las dos columnas. Esperá 6 s.
5. Hacé click dos veces en **A+** (agranda la letra). Detené.

> **Toma bonus (opcional, muy buena):** escaneá con tu celular el QR de la toma 3 y grabá
> la pantalla del celular (iPhone y Android tienen grabación de pantalla). El celular y la
> compu tienen que estar en **el mismo wifi**. Si Windows pregunta si permitir el acceso a
> la red, decí que sí.

---

### Toma 3 · La pantalla del escenario
**Pestaña 2** (`/overlay` con fondo negro) · **duración final: 8 s** · **grabá: 20 s**

1. Es lo que se proyectaría en las pantallas frente al escenario: título arriba a la
   izquierda, **un QR arriba a la derecha**, subtítulos grandes abajo.
2. Grabá 20 segundos sin tocar nada.

---

### Toma 4 · El panel de producción
**Pestaña 4** (`/ops`) · **duración final: 12 s** · **grabá: 30 s**

1. Grabá el panel completo 5 segundos quieto.
2. Pasá el mouse **despacio** por: los **puntos verdes** de la izquierda (el semáforo), la
   columna **Audio** (las barritas se mueven con el sonido), la **latencia**, y el
   recuadro de arriba a la derecha **"costo del evento"**.

---

### Toma 5 · Se cae y se levanta solo — ⭐ la segunda más importante
**Pestaña 4** (`/ops`) · **duración final: 12 s** · **grabá: 30 s**

Esto simula que el programa de una sala se cae (como cuando hoy hay que entrar por
escritorio remoto a reiniciarlo).

1. Empezá a grabar con el panel quieto 3 segundos.
2. En la fila **Escenario Rojo**, al final a la derecha, hay un botón **💥**. Hacé click.
3. Mirá lo que pasa **sin tocar nada**: el punto se pone **amarillo**, aparece
   **DEGRADADO** y abajo un aviso "Worker exited… restart 1 in 1s", y a los pocos segundos
   vuelve solo a **verde / EN VIVO**.
4. Seguí grabando hasta que esté verde de nuevo. Detené.

> Si al hacer click aparece un error "no running worker", es que la sala no estaba en
> vivo: esperá a que esté verde e intentá de nuevo.

---

### Toma 6 · Se levanta con un comando
**La terminal** · **duración final: 8 s** · **grabá: 25 s** · sin audio

1. En la terminal donde corre Cotorra apretá `Ctrl + C` para apagarlo.
2. Escribí `cls` y Enter (limpia la pantalla). Agrandá la letra: `Ctrl` + rueda del mouse.
3. Empezá a grabar **la ventana de la terminal**.
4. Escribí despacio y dale Enter:
   ```powershell
   cotorra serve
   ```
5. Grabá hasta que aparezca el recuadro verde **Cotorra is up** y 3 segundos más.

> **Importante:** después de esta toma, dejá Cotorra corriendo **con el comando del paso
> 1.3** (el largo, con `MANIFEST=demo.yaml`), porque las tomas 1 a 5 y la parte 4 lo
> necesitan.

---

### Toma 7 · La portada
**Pestaña 5** (`/`) · **duración final: 5 s** · **grabá: 15 s**

1. Es la lista de charlas que ve el público. Grabá quieto. Sirve de cierre.

---

## Parte 3 · Grabar la voz

**Con el celular alcanza.** App de notas de voz, en una habitación **sin eco** (un
placard con ropa es ideal). Celular a un palmo de la boca, sin tocarlo mientras hablás.

**Reglas:**
- Leé **más despacio** de lo que te parece natural.
- **Un solo archivo, de corrido,** con 2 segundos de silencio entre párrafos. Si te
  equivocás, repetí **el párrafo** y seguí: los errores se cortan después.
- Tono de conversación, no de publicidad.

### El guion

Cada párrafo va con una toma. La duración es orientativa.

---

**[Toma 1 · la charla] — 12 s**
> *(En el video, los primeros 3 segundos se escucha sólo al orador. Recién ahí empezás.)*
>
> Esta es una charla real de Nerdearla, en inglés. Los subtítulos en español los está
> generando Cotorra en vivo, mientras el orador habla.

**[Placa negra con texto] — 13 s**
> Hoy, cada sala de Nerdearla depende de una mini PC con un servicio pago en el navegador.
> Si se traba, alguien entra por escritorio remoto a reiniciarlo. Y la audiencia virtual
> no tiene traducción.

**[Toma 2 · el celular] — 10 s**
> Con Cotorra, cada persona elige la charla y el idioma desde su celular. Sin app, sin
> cuenta, y con letra grande para quien la necesite.

**[Toma 3 · pantalla del escenario] — 8 s**
> En la sala, la pantalla del escenario muestra los subtítulos y un QR para seguirlos
> desde el asiento.

**[Toma 1 otra vez, otro fragmento] — 9 s**
> Y para la transmisión, los mismos subtítulos se planchan en vMix u OBS. La audiencia
> virtual, por fin, también tiene traducción.

**[Toma 4 · panel] — 10 s**
> Para producción, un solo panel: semáforo por sala, latencia, nivel de audio y costo en
> vivo.

**[Toma 5 · la caída] — 10 s**
> Si algo se cae, se levanta solo. Nadie tiene que ir hasta la mini PC.

**[Placa con el diagrama] — 12 s**
> Cada sala corre en su propio proceso: si una falla, las otras ni se enteran. Y una sola
> llamada a Gemini devuelve la transcripción y todas las traducciones juntas.

**[Placa con el costo] — 5 s**
> Subtitular las treinta charlas cuesta menos de treinta dólares.

**[Toma 6 · terminal] — 6 s**
> Se levanta con un comando, y es open source.

**[Toma 7 · portada + logo] — 4 s**
> Cotorra. Subtítulos en vivo para cualquier conferencia.

---

Total: **~1:40**.

> **Sobre los números que decís:** "menos de treinta dólares" es cierto en las dos
> configuraciones medidas (US$9 sin subtítulos parciales, US$29 con). No digas "medio
> segundo de latencia": ese número del README es del transporte, no del modelo. Si alguien
> del jurado pregunta, la latencia real del modelo es de ~2,5 segundos, y se ve en pantalla.

### 3.1 Los subtítulos en inglés de tu voz, hechos con Cotorra

Parte del jurado no habla español. Las bases sugieren subtitular el video en inglés **con
tu propio proyecto** — hacerlo demuestra confianza en el producto.

**Hacelo apenas grabes la voz** (necesita el crédito de Google vigente).

1. Pasá el archivo de voz del celular a la compu y ponelo en la carpeta `submission` con
   el nombre `voz.m4a` (si es otro formato, como `.mp3` o `.wav`, sirve igual).
2. En una terminal **nueva** (dejá la de Cotorra como está):
   ```powershell
   .venv\Scripts\activate
   cotorra subtitle submission\voz.m4a --from es --to en --offset 3
   ```
   `--offset 3` corre todos los tiempos 3 segundos, porque en el video la voz arranca en
   el segundo 3 (antes se escucha sólo al orador).
3. Se crean `submission\voz.en.srt` (inglés) y `submission\voz.es.srt` (español).
4. **Abrí `voz.en.srt` con el Bloc de notas y leelo entero.** Si algún nombre salió mal,
   corregilo a mano y guardá.

---

## Parte 4 · Editar

### Qué editor usar

**CapCut para escritorio** (gratis, en español, fácil). Alternativa más potente y también
gratuita: DaVinci Resolve. Los dos importan archivos `.srt`.

> **No uses los subtítulos automáticos del editor.** El punto es que los hizo Cotorra.

### El método: primero la voz, después las imágenes

1. Creá un proyecto **1920×1080, 30 fps**.
2. **Pista de voz:** arrastrá el archivo de voz a la línea de tiempo, empezando en el
   **segundo 3**. Cortá los errores y los silencios largos (dejá ~0,5 s entre frases).
   *Si movés partes de la voz, los subtítulos van a quedar corridos: cortá sólo silencios
   al final, o volvé a correr el paso 3.1 con el audio ya editado y `--offset` correcto.*
3. **Imágenes:** poné cada toma **encima del párrafo que le corresponde** (ver el guion).
   Recortá cada toma para que dure lo mismo que su párrafo.
4. **Audio de las tomas:** sólo la toma 1 tiene audio útil (el orador en inglés).
   - Segundos 0 a 3: volumen **100 %** (se escucha al orador solo).
   - Desde que entra tu voz: bajalo al **15 %**. En las demás tomas, silenciá su audio.
5. **Subtítulos:** importá `voz.en.srt` (en CapCut: *Texto → Subtítulos → Importar*; en
   DaVinci: *Archivo → Importar → Subtítulos*). Estilo: letra blanca con fondo negro
   semitransparente, **arriba** del cuadro en la toma 1 (abajo ya están los de Cotorra) y
   abajo en el resto.

### Las ediciones que recomiendo, en orden de importancia

**1. Una placa de apertura de 2 segundos — sí, obligatoria.**
Fondo negro, el 🦜 grande, **"Cotorra"** y debajo *"Live subtitles for conferences —
open source"*. Es lo que el jurado ve como miniatura y lo que hace que se acuerde del
nombre. Ponela **antes** de la toma 1 (y corré la voz y los subtítulos 2 s).

**2. La marca en una esquina — sí, pero chiquita.**
"🦜 Cotorra" abajo a la derecha, blanco al 60 % de opacidad, durante todo el video. No
arriba a la izquierda: ahí está el recuadro "EN VIVO" de la toma 1 y el logo propio de las
otras pantallas.

**3. Rótulos que expliquen qué se está viendo — la edición que más suma.**
El jurado no conoce las pantallas. Un texto corto, arriba, **en inglés** (así lo entienden
todos), 3 segundos al comienzo de cada toma:

| Toma | Rótulo |
|---|---|
| 1 | *Real Nerdearla talk · live subtitles by Cotorra* |
| 2 | *Audience view · any phone, any language* |
| 3 | *Stage screen · QR to follow along* |
| 4 | *Production dashboard* |
| 5 | *A stage crashes… and recovers on its own* |
| 6 | *One command to run it* |

**4. Dos placas de texto** (fondo negro, letra blanca grande):
- La del problema: *"Today: a mini PC per room · a paid service in a browser · remote
  desktop to press F5 · no translation for the virtual audience"*.
- La del costo: *"30 talks · under US$30 · measured, not estimated"*.

**5. La placa del diagrama.** Hacé una captura de la sección *Architecture* del
[README en inglés](../README.en.md) y usala tal cual, o armá una simple: *"one process per
stage → one Gemini call → transcript + every translation"*.

**6. Zoom en los números — sí, con moderación.**
En la toma 4, un zoom suave (acercamiento de 1 s) sobre el **costo del evento**. En la
toma 5, sobre el **punto que se pone amarillo y vuelve a verde**. Sólo esos dos.

**7. Placa final de 4 segundos.** 🦜 Cotorra + la URL del repositorio + "Apache 2.0".
Si ya tenés el link del repo, ponelo; si no, dejá un espacio y agregalo al final.

### Lo que NO recomiendo

| No | Por qué |
|---|---|
| Acelerar la toma 1 | Cambia la latencia que se ve. El jurado evalúa latencia: tiene que ser la real. |
| Música fuerte o con letra | Compite con la voz y con el orador. Si ponés música: instrumental, al 5 %. |
| Transiciones vistosas | Corte seco entre tomas. Se ve más profesional. |
| Pasarse de 2:00 | Las bases lo prohíben. Apuntá a 1:40. |
| Mostrar código | El jurado de DX quiere ver cuánta fricción hay, no cómo está programado. |

### Exportar

1080p, 30 fps, MP4 (H.264). Nombre: `cotorra-demo.mp4`.

---

## Parte 5 · Subir a YouTube

1. **Título:** `Cotorra — Live subtitles for conferences (Nerdearla Vibeathon 2026)`
2. **Visibilidad:** *No listado* alcanza (el jurado entra con el link).
3. **Miniatura:** una captura de la toma 1 con los subtítulos, o de la placa de apertura.
4. **Subtítulos:** además de los que quemaste, subí `voz.en.srt` como pista
   (*Subtítulos → Agregar idioma → Inglés → Subir archivo*).
5. **Descripción** (copiala tal cual):

> Cotorra is open source real-time transcription and translation for conferences, built
> for the Nerdearla 2026 Vibeaton.
>
> The talk in this video is a real Nerdearla talk; the subtitles on it were generated live
> by Cotorra while it played. **The English subtitles of this video were generated by
> Cotorra itself.**
>
> • Every talk, any language, on any phone — no app, no account
> • Stage screen with a QR, and an overlay for vMix / OBS
> • Production dashboard: per-stage status, latency, audio level, live cost; stages recover
>   on their own
> • One Gemini call returns the transcript and every translation
> • Under US$30 to subtitle 30 talks (measured)
> • Apache 2.0
>
> Code: (link del repositorio)

---

## Si algo sale mal

| Problema | Qué hacer |
|---|---|
| `doctor --live` no dice OK | Mirá el mensaje: si dice "Billing is not active", ver la última fila; si dice 429, esperá un minuto y repetí. |
| La toma 1 no arranca / "No llega audio" | Mirá la pestaña 4: la sala tiene que estar verde. Apretá **R** en la toma 1. |
| La toma 1 dice "Falta el token" | La URL tiene que terminar en `&token=demo`, igual a la del paso 1.4. |
| Los subtítulos salen muy tarde (más de 10 s) | Es la API lenta en ese momento. Apretá **R** y repetí la toma. |
| El 💥 no aparece | La URL de la pestaña 4 tiene que tener `&simulacro=1`. |
| El QR no abre en el celular | Mismo wifi en los dos, y permitir a Python en el firewall de Windows. |
| No hay sonido en la grabación | Configuración de la Barra de juego: audio del sistema activado. |
| `doctor --live` o el panel dicen "Billing is not active" | Se terminó el crédito de Google. Si canjeás otro, hay que vincularlo al proyecto: ver `HANDOFF.md`, sección 2. Sin crédito, `GEMINI_USE_VERTEX=false` en `.env` usa la key gratuita, que alcanza sólo para una toma corta: priorizá la toma 1 y el paso 3.1. |
