# Diseño DJ Radio — canvas (Claude Design)

Mockups de la herramienta local del motor DJ Radio, hechos con la design skill.
Canvas publicado (editable, export PNG/PDF): https://claude.ai/artifact/2VR88Xzuizn1vV4GXZWMJ1

Lo que está en esta carpeta es lo mismo que el canvas publicado. Si se edita en el canvas y
se guarda, esta carpeta queda atrás: volver a extraerlo antes de tocar nada acá.

## Páginas y pantallas

**Hoy** — lo que el motor hace de verdad:
- `Biblioteca.dc.html` — La colección: BPM con un decimal, Camelot + clásica, energía como
  percentil, `?` en las tonalidades con acuerdo no unánime, y un botón por fila para armar una
  radio desde ese track (ícono de emisión, no ▶: el ▶ sugeriría reproducir audio, que no existe).
- `Main.dc.html` — Armador de set: semilla y controles de `RadioConfig` (curva, largo,
  randomness, semilla del azar, artist_gap) → set con el porqué de cada transición en el
  formato real (`+X.X% BPM | 8A → 9A (vecino)`), curva de energía (desvío medio + Spearman del
  tramo ascendente), aviso de set corto con su motivo, export M3U8.

**Futuro** — lo que el motor todavía NO hace (marcado así en el canvas):
- `Preescucha.dc.html` — Transición entre dos tracks del set: formas de onda, cues, transporte.
  Las formas de onda y los cues son ilustrativos: no hay preescucha de audio ni cues automáticos
  (tarea 5.4). También van a esta página, como nota: columna Género, calidad real medida, guardar
  sets y marcar transiciones (tarea 16).

`canvas.json` — layout y páginas (hoy / futuro).

## Alineación con el motor (2026-09-15)

Los mockups se corrigieron contra el código para que no muestren datos que mientan (§6):
porqués calculados con `motor.radio.bpm_delta_pct` y `key_relation` sobre las filas mostradas,
clásicas con `motor.tonalidad.camelot_a_clasica`, curva peak de `motor.energia.energy_target`,
desvío y Spearman ascendente con `energy_curve_deviation` / `ascending_spearman`, `?` binario
(unánime → normal; cualquier otro caso → `?`), energía en un solo color como percentil.

Decisiones del dueño en esa pasada: las funciones inexistentes van a la página Futuro; el `?` se
muestra como estado objetivo (la tarea 17 lo lleva al motor); energía neutra; se agregaron aviso de
set corto, controles completos y métrica de energía completa. Licencia y duración por track NO se
agregaron todavía, aunque §6 pide la licencia siempre visible (confirmado de nuevo el
2026-09-16: "por ahora no").

2026-09-16: el botón de cada fila de Biblioteca pasó de ▶ a un ícono de emisión con el texto
"Armar radio"; el Armador muestra por transición solo el renglón del porqué (§6), sin energía
pedida ni mezclabilidad (ese detalle queda en la Preescucha, página Futuro).

Datos: reales de la biblioteca de Rekordbox (store `gt_out/biblioteca.sqlite`, no versionado).
Acento: **cian/teal** `#2dd4bf`; azul `#4b90f7` para "mismo/vecino" y el lado B. Base: design
system Nocturne (dark). Fuente Inter + mono para datos.

## Para editar y volver a guardar (design skill)
1. `/design` para extraer el helper.
2. Si se editó en el canvas: leer el artifact y `seed-canvas.mjs --extract` a una carpeta nueva.
   Si no: editar los `.dc.html` / `canvas.json` de acá.
3. Re-seed: `node <base>/seed-canvas.mjs --template <base>/payload.template.html --out dj-radio-armador-de-set.html --title "DJ Radio — Armador de set" --artboard Main.dc.html --artboard Biblioteca.dc.html --artboard Preescucha.dc.html --canvas canvas.json`
4. Publicar con la Artifact tool sobre la MISMA URL (`url`, contract 0.1.31, sin capabilities).
