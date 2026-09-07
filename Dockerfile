# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# MusiFlix — imagen única (monolito) preparada para dividir en servicios luego.
# Stage 1: buildea el front (Node). Stage 2: runtime Python con ffmpeg.
# ---------------------------------------------------------------------------

# ---------- Stage 1: build del frontend (React + Vite) ----------
FROM node:22-slim AS frontend-build
WORKDIR /front
# Primero los manifiestos (cache: no se reinstala al cambiar el código)
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci
COPY frontend/ ./
RUN npm run build          # genera /front/dist

# ---------- Stage 2: runtime (FastAPI + ffmpeg + análisis de audio) ----------
# Python 3.12 (no 3.14 local): tiene wheels estables de librosa/numba.
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MUSIFLIX_DATA_DIR=/data \
    MUSIFLIX_DOWNLOADS=/data/downloads

# Binarios del sistema: ffmpeg/ffprobe (audio) y libsndfile (soundfile/librosa)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libsndfile1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Usuario non-root
RUN useradd -m -u 10001 app
WORKDIR /app

# Dependencias Python primero (capa cacheable e independiente del código)
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Código de la app y el front ya buildeado
COPY --chown=app:app *.py ./
COPY --chown=app:app web ./web
COPY --from=frontend-build --chown=app:app /front/dist ./frontend/dist

# Carpeta de datos (DB + descargas) — se monta como volumen; owner = app
RUN mkdir -p /data/downloads && chown -R app:app /data

USER app
EXPOSE 8000
STOPSIGNAL SIGTERM

# Salud real: la raíz sirve el SPA. Sin curl en slim → usamos python.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/',timeout=4).status==200 else 1)"

# Forma exec para recibir SIGTERM. Host 0.0.0.0 (el __main__ usa 127.0.0.1, no sirve en Docker).
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

LABEL org.opencontainers.image.title="MusiFlix" \
      org.opencontainers.image.description="Buscador/descargador de música multi-plataforma para DJs (FastAPI + React)"
