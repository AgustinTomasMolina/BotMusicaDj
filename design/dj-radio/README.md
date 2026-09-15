# Diseño DJ Radio — canvas (Claude Design)

Mockups de la herramienta local del motor DJ Radio, hechos con la design skill.
Artifact publicado (editable, export PNG/PDF): https://claude.ai/code/artifact/0c10d49b-df63-4098-9e7a-aac8e7e51f32

## Pantallas (artboards)
- `Main.dc.html` — Armador de radio/set: semilla → set con el porqué de cada transición, curva de energía, `?` de confianza, export M3U8.
- `Biblioteca.dc.html` — La colección: BPM, Camelot+clásica, energía, género, `?` en tonalidades no unánimes, ▶ por fila.
- `Preescucha.dc.html` — Transición Apache→Spiral: formas de onda + solape, cues reales, transporte, "por qué".
- `canvas.json` — layout de los 3 artboards.

Datos: reales de la biblioteca de Rekordbox (store `gt_out/biblioteca.sqlite`, no versionado).
Acento: **cian/teal** `#2dd4bf`; azul `#4b90f7` para "mismo/vecino" y el lado B (para no chocar con el teal). Base: design system Nocturne (dark). Fuente Inter + mono para datos. Accesible (WCAG 2.2).

## Para editar/re-publicar (design skill)
1. `/design` para extraer el helper.
2. Editar los `.dc.html` / `canvas.json` acá.
3. Re-seed: `node <base>/seed-canvas.mjs --template <base>/payload.template.html --out dj-radio-set-builder.html --title "DJ Radio — Armador de set" --artboard Main.dc.html --artboard Biblioteca.dc.html --artboard Preescucha.dc.html --canvas canvas.json`
4. Publicar con la Artifact tool sobre la MISMA URL (contract 0.1.31).

Formas de onda de la preescucha: ilustrativas (el dibujo real sale al implementar con audio).
