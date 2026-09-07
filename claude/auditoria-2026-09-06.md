# Auditoría — implementación real vs. diseño de referencia (2026-09-06)

Cruce del estado real del código contra `claude/spec-dj-radio.md` y la base de Notion
**DJ Radio — Roadmap**. Regla usada: si no está el código, es NO EMPEZADO; si se duda
entre HECHO y A MEDIAS, es A MEDIAS.

## ⚠️ Salvedad importante — qué repo se auditó
Este repo es **MusiFlix** (bot de adquisición: búsqueda multi-plataforma + descarga +
verificación de calidad + tags + playlists). **El prototipo del motor de DJ Radio NO está
en esta máquina** — se buscó en Escritorio, Documentos, Descargas y el home: no hay
`djradio-prototipo.zip`, ni carpeta con el CLI `scan/list/info/similar/radio`, ni la suite
de audio sintético. Por lo tanto, todo diagnóstico de motor (BPM, scoring) es sobre el
código de MusiFlix, **no** sobre el motor de DJ Radio, que según
`claude/proyecto-web-djs-motor-radio.md §3` ya tendría BPM por autocorrelación (±0.34) y
scoring multiplicativo — sin verificar por ausencia del prototipo.

## Estado por bloque (evidencia archivo:línea)

### F0 — Auditoría
- Repo con git / prototipos unificados / radio punta a punta → **A MEDIAS** (git iniciado esta sesión; sin comando de radio ni 2º prototipo).
- Ground truth XML de Rekordbox → **NO EMPEZADO** (`grep -i rekordbox`: solo docs).
- Benchmark → **NO EMPEZADO** (`test_bot.py` no es benchmark de motor).

### F0.5 — Pipeline de ingesta
- Verificación de calidad por corte espectral → **A MEDIAS** — `analizar_calidad.py:analizar()` mide corte y da nota A–F, pero por archivo suelto (`server.py:_calidad_espectral`), sin comando de carpeta ni validación 10/10.
- Detección de duplicados → **NO EMPEZADO** — `db.py:_ident` deduplica items de playlist por `(fuente,url,titulo,artista)`, no el mismo track con otro nombre por audio.
- Análisis BPM/tonalidad/energía → **A MEDIAS** — BPM+tono en `analisis_audio.py:33-55` (Krumhansl); energía ausente.
- Cues automáticos → **NO EMPEZADO** (`grep -i cue`: solo docs).
- Tags ID3 → **A MEDIAS** — `tagger.py` escribe tags y funciona; sin test, como paso de descarga individual.
- Salida iTunes / XML Rekordbox → **A MEDIAS / NO EMPEZADO** — `.m3u8` en `db.py:armar_m3u8`; sin carpeta auto-iTunes ni generador de XML Rekordbox.
- Reporte final del pipeline → **NO EMPEZADO**.

### F1 — Motor confiable (sobre MusiFlix; ver salvedad)
- BPM por autocorrelación → **MAL ENCARADO** — `analisis_audio.py:29,40` usan `librosa.beat.beat_track` pelado + `round()`. La spec (§7, `proyecto-web-djs-motor-radio.md:50-52`) dice que eso da ~3 BPM de error y hay que autocorrelar.
- Scoring multiplicativo → **MAL ENCARADO** — `similares.py:370`: `_score = harm*1000 + bpm_d*10 - reciente*5` (aditivo). Spec §7 exige `encaje × mezclabilidad`.
- Chroma relativo en embedding → **NO EMPEZADO** — no hay embedding; `analisis_audio.py:43` usa `chroma_stft` solo para detectar tono.
- Energía como percentil → **NO EMPEZADO**.

> **Nota:** BPM y scoring quedan MAL ENCARADO **en MusiFlix**. Si el prototipo de DJ Radio
> ya los tiene bien (según los docs), el problema real no es "mal encarado" sino
> **NO FUSIONADO**: existen bien en otro repo y falta traerlos. No se pudo confirmar
> porque el prototipo no está en la máquina.

### F2 — Herramienta usable
- Export M3U8 → **A MEDIAS** — `db.py:armar_m3u8`; apunta a `downloads/` del bot, sin verificar en Rekordbox.
- Preescucha de la transición (A→B beat-aligned) → **NO EMPEZADO** — hay preview de un track (`common.jsx:PreviewLayer`), no de transición.
- 3 sets reales → **NO EMPEZADO** (actividad).

### F3 — Segundo usuario
- Empaquetado → **A MEDIAS** — `README.md`, `requirements.txt`, `.bat`; sin probar por otro DJ.

## Listas
**a) Avance real que Notion tiene como pendiente** (ninguna llega a HECHO estricto): 5.1
(corte espectral), 5.3 (tags ID3), 5.5 (m3u8 a iTunes), 15 (export M3U8). → pasar a "En curso".

**b) Tareas a reescribir:** F0 #1 (mezcla tres cosas); F2 #18 "Interfaz local mínima"
(ambigua ahora que MusiFlix tiene UI). Las "IDEA CONGELADA" están bien como recordatorio.

**c) En el código pero sin tarea:** todo MusiFlix (buscador multi-fuente, nota A–F +
comparador + Spek, historial SQLite, "Mis Playlists"/crates, frontend Nocturne, filtro de
género, parecidas Deezer). La Roadmap asume biblioteca local; MusiFlix resuelve la
adquisición (el cuello de botella de la spec §0). → definir el contrato MusiFlix↔pipeline
y tratar MusiFlix como **proyecto separado**, no como fase.
