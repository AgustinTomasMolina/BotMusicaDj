# BotMusicaDj

Motor de recomendación musical para DJs: analiza cada track (BPM, tonalidad Camelot, energía,
embedding de timbre) y arma sets tipo "radio" priorizando que los temas **se puedan mezclar**,
no solo que suenen parecido.

> Spotify y SoundCloud recomiendan para escuchar. Esto recomienda para mezclar.

**Estado:** prototipo funcional (CLI). Ver [Roadmap](#roadmap).

---

## Cómo funciona

Tres capas:

1. **Features por track** (offline, una vez por archivo)
   - BPM con refinamiento por autocorrelación de la envolvente de onsets (±0.3 BPM;
     `librosa.beat_track` solo queda cuantizado por la grilla de frames y erra ~3 BPM a 150).
   - Tonalidad → rueda Camelot (perfiles Krumhansl-Schmuckler sobre chroma).
   - Energía percibida: RMS + onsets/seg + ratio percusivo (HPSS).
   - Embedding de timbre (MFCC / chroma relativo / tonnetz / contraste espectral).

2. **Búsqueda vectorial** — z-score por dimensión sobre la biblioteca, después L2 por fila,
   producto punto = coseno. Hoy SQLite + numpy; a futuro Postgres + pgvector.

3. **Re-ranking de DJ**

   ```
   score = encaje_musical × mezclabilidad
   ```

   Es un **producto, no una suma**: multiplicando, la mezclabilidad funciona como compuerta y
   un track que suena parecidísimo pero está fuera de tempo no entra igual.
   `mezclabilidad = bpm_score^1.0 × key_score^0.6` (un choque de tonalidad se disimula con EQ;
   uno de tempo no).

   Además: curva de energía del set, MMR contra la redundancia, gap mínimo entre tracks del
   mismo artista y soporte de half/double time.

---

## Requisitos

- Python 3.11+
- `ffmpeg` en el PATH (decodificación de audio)

```bash
# Debian/Ubuntu
sudo apt install ffmpeg
# macOS
brew install ffmpeg
# Windows
winget install Gyan.FFmpeg
```

> **No hace falta Node/npm.** El proyecto es Python puro. `npm` solo va a entrar si más
> adelante se suma un frontend con build propio (React/Vite), y viviría aislado en `web/`.

## Instalación

```bash
git clone https://github.com/AgustinTomasMolina/BotMusicaDj.git
cd BotMusicaDj

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[dev]"          # instala el paquete en modo editable + tooling
```

En modo editable (`-e`) los cambios en `src/` se toman sin reinstalar nada.

## Uso

```bash
# 1. Analizar una carpeta de audio (cachea en SQLite; ~5 s por track)
djradio scan ~/Music/crate --db djradio.sqlite

# 2. Ver qué hay en la biblioteca
djradio list
djradio info "track.mp3"          # BPM, Camelot, energía

# 3. Tracks similares a uno dado
djradio similar "track.mp3" -n 10

# 4. Armar un set y exportarlo a Rekordbox/Traktor
djradio radio --seed "track.mp3" --len 20 --curve peak -o set.m3u8
```

Opciones útiles de `radio`:

| Flag | Qué hace |
|---|---|
| `--curve peak\|flat\|warmup` | forma de la curva de energía del set |
| `--bpm-tol 3` | tolerancia de BPM entre temas consecutivos |
| `--allow-double-time` | permite saltos de mitad/doble tempo |
| `--artist-gap 4` | mínimo de tracks entre temas del mismo artista |

## Estructura

```
BotMusicaDj/
├── pyproject.toml          # dependencias y entry point de la CLI
├── src/djradio/
│   ├── cli.py              # comandos scan / list / info / similar / radio
│   ├── features.py         # BPM, tonalidad, energía
│   ├── embeddings.py       # vector de timbre por track
│   ├── store.py            # caché SQLite + búsqueda vectorial
│   ├── radio.py            # re-ranking y armado del set
│   └── export.py           # M3U8 para Rekordbox / Traktor
├── tests/                  # incluye ground truth con audio sintético
└── scripts/
```

## Desarrollo

```bash
pytest                 # tests (los de audio sintético verifican BPM/tonalidad)
ruff check . && ruff format .
```

La suite genera audio sintético con BPM y tonalidad conocidos, así que los errores de
detección se miden en vez de estimarse a ojo.

## Roadmap

- [x] Prototipo del motor con CLI y export a M3U8
- [ ] Correr contra una biblioteca real y ajustar pesos escuchando los sets
- [ ] Reemplazar el embedding hand-crafted por **CLAP** (habilita búsqueda por texto:
      *"techno hipnótico con bajo rodante"*) o Discogs-EffNet
- [ ] Migrar el store a **Postgres + pgvector** (HNSW)
- [ ] API en FastAPI/Django + análisis en cola (Celery/arq); `/radio?seed=X` responde desde
      embeddings precalculados
- [ ] Ingesta de fuentes libres (Free Music Archive, Jamendo, netlabels) con licencia y URL
      de origen obligatorias por track
- [ ] Señales implícitas (skip, descarga, add-to-crate) para la capa colaborativa

## Nota legal

El proyecto no distribuye música comercial. Todo track ingestado lleva **licencia y URL de
origen obligatorias** (`CC-BY`, `CC-BY-SA`, `CC0`, `free-download-artista`) y la atribución se
muestra en la ficha y en la playlist exportada. Hay flujo de claim/takedown previsto.

## Licencia

MIT — ver [LICENSE](LICENSE).
