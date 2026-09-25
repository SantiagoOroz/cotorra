# Audios de prueba

## `pipeline-test.ogg` (incluido en el repo)

44 segundos de ruido rosa modulado, con el mismo perfil de energía que el habla: ráfagas de
~4,6 s separadas por ~2,4 s de silencio.

**No es voz y no produce transcripción real.** Sirve para ejercitar el pipeline completo
—ffmpeg, la segmentación por VAD, el fan-out, el panel, los exports— sin descargar nada y
sin gastar un centavo. Es lo que hace que `docker compose up` muestre algo funcionando a
los 5 segundos de clonar el repo.

Con el motor `mock` vas a ver subtítulos de ejemplo. Con `gemini` vas a ver poco o nada,
que es lo correcto: un buen motor no inventa palabras donde no hay habla.

Regenerarlo:

```bash
ffmpeg -f lavfi -i "anoisesrc=d=44:c=pink:r=16000:a=0.28" \
  -af "tremolo=f=6.5:d=0.85,volume='if(lt(mod(t,7),4.6),1,0.004)':eval=frame,highpass=f=180,lowpass=f=3600" \
  -ac 1 -ar 16000 -c:a libopus -b:a 24k samples/pipeline-test.ogg
```

## Charlas reales

Las grabaciones no se commitean: son pesadas y son de quienes las dieron.

```bash
python scripts/fetch_samples.py 'https://www.youtube.com/watch?v=...' \
    --id keynote --lang en --targets es --minutes 5
```

El script imprime la entrada lista para pegar en `cotorra.yaml`.

También podés saltear la descarga y apuntar una sesión directo a YouTube:

```yaml
  - id: charla
    source: https://www.youtube.com/watch?v=VIDEO_ID
    source_lang: en
    targets: [es]
    engine: gemini
```

## Qué probar antes de un evento

No alcanza con que ande con audio limpio de estudio. Probá con:

- **El acento de tus oradores.** Es lo que más mueve la calidad.
- **Ruido de sala**: aplausos, gente entrando, el aire acondicionado.
- **Jerga técnica de tu evento** — y después cargala en `data/glossaries/`.
- **Cambios de idioma en la misma charla.** Pasa todo el tiempo en LatAm.
- **Momentos sin habla**: demos, videos, pausas largas. El sistema no debería inventar nada.
