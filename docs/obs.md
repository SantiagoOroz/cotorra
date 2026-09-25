# Quemar los subtítulos en el stream (OBS, vMix, Streamlabs)

El overlay es una página web con fondo transparente. Cualquier software de streaming que
tenga un "browser source" la puede componer sobre el video.

```
http://localhost:8080/overlay?session=stage-red&lang=es
```

El panel `/ops` tiene un botón **OBS** por escenario que copia esa URL ya armada.

---

## OBS Studio

1. **Sources → + → Browser**
2. URL: la de arriba.
3. Width `1920`, Height `1080` (igual que tu canvas).
4. **Destildá "Shutdown source when not visible"**. Si queda tildado, OBS cierra el
   WebSocket al cambiar de escena y los subtítulos arrancan de cero al volver.
5. **Destildá "Refresh browser when scene becomes active"**, por lo mismo.

No hace falta ningún CSS custom: la página ya viene con fondo transparente.

### Si querés el mismo escenario en dos idiomas

Dos browser sources, misma `session`, distinto `lang`. Una queda en el stream en español y
la otra en un stream alternativo en inglés.

---

## vMix

1. **Add Input → Web Browser**
2. Pegá la URL, resolución igual a la del proyecto.
3. En el input: **Layer → Key → Transparent background**.

---

## Parámetros

| Parámetro | Default | Qué hace |
|---|---|---|
| `session` | *requerido* | id del escenario |
| `lang` | `es` | idioma del subtítulo |
| `lines` | `2` | cuántas líneas quedan en pantalla |
| `size` | `42` | tamaño en píxeles |
| `align` | `bottom` | `bottom`, `top` o `center` |
| `box` | `1` | placa semitransparente detrás del texto |
| `shadow` | `0` | sombra en vez de placa (mejor sobre fondos claros) |
| `font` | del sistema | cualquier tipografía instalada en la máquina de streaming |
| `partials` | `1` | mostrar las palabras mientras se hablan |

---

## Recetas

**Broadcast clásico, dos líneas abajo con placa negra:**
```
/overlay?session=keynote&lang=es&lines=2&size=44&box=1
```

**Sobre una slide blanca — la placa tapa demasiado, mejor sombra:**
```
/overlay?session=keynote&lang=es&shadow=1&box=0&size=40
```

**Una sola línea, estilo lower-third, para no tapar la demo:**
```
/overlay?session=keynote&lang=es&lines=1&size=36&partials=0
```

> `partials=0` es la elección correcta para un stream grabado: los parciales se reescriben
> en pantalla, lo que en vivo se lee bien pero en una grabación queda inquieto.

**Vertical (9:16) para redes:**
```
/overlay?session=keynote&lang=es&lines=3&size=52&align=center
```

---

## La pantalla del escenario

La misma página, con fondo sólido, sirve para la TV o el proyector frente al escenario:

```
/overlay?session=keynote&lang=es&bg=000&lines=3&size=64&qr=1&title=1
```

| Parámetro | Qué hace |
|---|---|
| `bg` | color de fondo (hex, sin `#`): la convierte en pantalla en vez de overlay |
| `qr` | un QR en la esquina para seguir los subtítulos desde el celular |
| `title` | el título de la charla y la sala, en la otra esquina |

El QR apunta a la vista de audiencia. Si abriste la pantalla en `localhost`, Cotorra lo
reemplaza por la IP de la máquina en la red local, para que los celulares de la sala
puedan llegar; en un evento, poné `PUBLIC_URL` con la dirección real. El panel `/ops`
tiene un botón 📺 por escenario que abre esta pantalla ya armada.

---

## Detalles que importan en vivo

- **Si el gateway se cae**, el overlay muestra un cartelito chico arriba a la izquierda
  (`cotorra: reconnecting…`) y reconecta solo. Es visible en el monitor de preview pero
  discreto; si te molesta en el programa, poné el browser source en una escena que no esté
  al aire mientras lo revisás.
- **El `lang` del overlay y el de la audiencia son independientes.** Podés quemar español
  en el stream y dejar que cada persona en la sala elija otro idioma en su teléfono.
- **Latencia del video vs. del subtítulo.** Si tu pipeline de video tiene delay (encoder,
  CDN), los subtítulos de Cotorra van a llegar *antes* que la imagen. Compensá con el delay
  del browser source o poniendo el subtítulo después del encoder.

---

## Otra opción: archivo de subtítulos al final

Si no querés quemarlos en vivo, exportá al terminar y subilos al video:

```bash
cotorra export keynote --lang es --formats srt,vtt
```

YouTube, Vimeo y cualquier editor aceptan esos archivos directo.
