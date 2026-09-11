# MusiFlix

Buscador/descargador de música multi-plataforma para DJs: busca un tema en varias fuentes
(YouTube, SoundCloud, Spotify + sitios de MP3), **compara la calidad real** de cada versión
(nota A–F por análisis espectral), lo **descarga con tags** (BPM, key/Camelot, género,
carátula), y arma **crates** ("Mis Playlists") exportables a `.m3u8` (iTunes/Rekordbox/Serato).

Es la capa de **adquisición** del proyecto **DJ Radio** (herramienta propia para DJs). El
contexto, las reglas y el roadmap están en [`claude/`](claude/) y en Notion — arrancá por
[`CLAUDE.md`](CLAUDE.md) y [`claude/spec-dj-radio.md`](claude/spec-dj-radio.md).

## Stack
- **Backend:** Python + FastAPI (`server.py`), SQLAlchemy + SQLite (`db.py`), yt-dlp, librosa, ffmpeg.
- **Motor DJ Radio:** `motor/` (BPM, tonalidad, energía, scoring), `ground_truth/` (parser del
  XML de Rekordbox) y `benchmark/` (umbrales de la spec §4).
- **Frontend:** React 19 + Vite (`frontend/`), design system "Nocturne".
- **Infra:** Docker (multi-stage) + `docker-compose` con **web + worker + Redis** (cola de trabajos RQ).

## Cómo correrlo

El entrypoint del servidor es **`server:app`** (FastAPI en `server.py`) — el mismo que usa el
`Dockerfile`. `api.py` es una API anterior que quedó sin uso.

### Opción A — Docker (recomendada, reproducible)
Requiere **Docker Desktop encendido**.
```bash
docker compose up -d --build      # → http://localhost:8000
docker compose logs -f worker     # ver el worker procesando descargas/análisis
docker compose down               # frenar
```
Levanta 3 servicios: `web` (API + UI), `worker` (descargas/análisis en cola aparte) y
`cache` (Redis). Los datos (DB + descargas) viven en el volumen `musiflix-data`.

### Opción B — Windows, con doble clic (`scripts/`)
La forma más corta en Windows. Todos los `.bat` viven en [`scripts/`](scripts/), se ubican
solos en la raíz del repo y usan el venv (`.venv\` o `venv\`) si existe.

```
scripts\PANEL_CONTROL.bat        menú con todo (punto de entrada recomendado)
scripts\setup_y_ejecutar.bat     instalación completa la primera vez
scripts\iniciar_web.bat          levanta el server y abre el navegador
scripts\iniciar_api.bat          levanta el server sin abrir el navegador
scripts\ejecutar_pruebas.bat     pytest motor/tests + test_bot.py
scripts\recompilar-frontend.bat  npm run build en frontend/
```

El detalle de cada uno está en [`scripts/README.md`](scripts/README.md).

### Opción C — Local, a mano (sin Docker)
```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)

pip install -r requirements.txt
pip install -e .                  # opcional: motor/, benchmark/, ground_truth/ importables

# Requiere ffmpeg en el PATH. Node solo para buildear el front:
cd frontend && npm install && npm run build && cd ..

python -m uvicorn server:app --host 127.0.0.1 --port 8000   # → http://localhost:8000
```
Sin Redis, todo corre en proceso (la cola es opcional: `queue_disponible()=False`).

## Tests
```bash
pip install -r requirements-dev.txt   # producción + pytest y ruff (pytest NO está en requirements.txt)
python -m pytest                  # toda la suite: motor, benchmark, ground_truth, calidad, pipeline
python -m pytest motor/tests      # solo el motor (BPM, tonalidad, energía, scoring)
python test_bot.py                # chequeos del bot de adquisición
python -m benchmark               # umbrales de calidad del motor (spec §4)
```

## Configuración
Copiá `.env.example` a `.env` y completá lo que uses (credenciales de Spotify son opcionales;
sin ellas, Spotify se resuelve buscando el equivalente en YouTube).

Variables útiles (con defaults que replican el modo local):
- `MUSIFLIX_DATA_DIR` — dónde vive la DB SQLite (default: raíz del repo).
- `MUSIFLIX_DOWNLOADS` — carpeta de descargas (default: `./downloads`).
- `REDIS_URL` — si está, encola el trabajo pesado en el worker; si no, corre local.

## Estructura
```
server.py              API FastAPI (search, download, calidad, spectro, playlists…)
db.py                  SQLite: historial + "Mis Playlists" (crates)
jobs.py tasks.py worker.py   Cola de trabajos (Redis + RQ) — worker separado
similares.py           "Parecidas" (Deezer + Camelot/BPM)
analizar_calidad.py    Nota de calidad A–F (corte espectral)
analisis_audio.py      BPM + tonalidad (librosa)
tagger.py              Tags ID3/FLAC/MP4
search_agent.py scrapers.py   Fuentes de búsqueda
motor/                 Motor DJ Radio: bpm, tonalidad, energia, scoring (+ motor/tests)
ground_truth/          Parser del XML de Rekordbox → CSV, y la sesión completa
                       de ground truth (ver ground_truth/README.md)
benchmark/             Umbrales de la spec §4 y runner (`python -m benchmark`)
frontend/              React + Vite (UI)
scripts/               Lanzadores .bat de Windows
claude/                Spec, docs y auditoría del proyecto DJ Radio
Dockerfile docker-compose.yml   Contenerización (web + worker + redis)
```

## Nota legal
Herramienta de uso personal. Descargar contenido con copyright puede violar los ToS de las
plataformas y la ley de tu país. Usá material libre o con licencia cuando corresponda.
