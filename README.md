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
- **Frontend:** React 19 + Vite (`frontend/`), design system "Nocturne".
- **Infra:** Docker (multi-stage) + `docker-compose` con **web + worker + Redis** (cola de trabajos RQ).

## Cómo correrlo

### Opción A — Docker (recomendada, reproducible)
Requiere **Docker Desktop encendido**.
```bash
docker compose up -d --build      # → http://localhost:8000
docker compose logs -f worker     # ver el worker procesando descargas/análisis
docker compose down               # frenar
```
Levanta 3 servicios: `web` (API + UI), `worker` (descargas/análisis en cola aparte) y
`cache` (Redis). Los datos (DB + descargas) viven en el volumen `musiflix-data`.

### Opción B — Local (sin Docker)
```bash
pip install -r requirements.txt
# Requiere ffmpeg en el PATH. Node solo para buildear el front:
cd frontend && npm install && npm run build && cd ..
py server.py                      # → http://localhost:8000
```
Sin Redis, todo corre en proceso (la cola es opcional: `queue_disponible()=False`).

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
frontend/              React + Vite (UI)
claude/                Spec, docs y auditoría del proyecto DJ Radio
Dockerfile docker-compose.yml   Contenerización (web + worker + redis)
```

## Nota legal
Herramienta de uso personal. Descargar contenido con copyright puede violar los ToS de las
plataformas y la ley de tu país. Usá material libre o con licencia cuando corresponda.
