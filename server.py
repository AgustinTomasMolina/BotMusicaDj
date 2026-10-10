"""
Servidor Web del Bot de Música
================================
Sirve una página única con:
  - Buscador de canciones
  - Playlist de resultados estilo YouTube
  - Descarga de canciones con progreso en tiempo real

Ejecutar:  py server.py
Abrir:     http://localhost:8000
"""

import asyncio
import hashlib
import ipaddress
import logging
import math
import os
import re
import socket
import sqlite3
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

# Consola de Windows en UTF-8 para que los emojis/logs no rompan el proceso
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import db
import jobs
import track_identity
from fuente_errores import FuenteError, FuenteNoConfigurada
from search_agent import SearchAgent
from recommendation_agent import RecommendationAgent
from scrapers import FUENTES_SCRAPER

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"               # frontend viejo (vanilla), queda en /legacy
DIST_DIR = BASE_DIR / "frontend" / "dist"  # build de React (Vite), se sirve en /
DOWNLOADS_DIR = Path(os.getenv("MUSIFLIX_DOWNLOADS", str(BASE_DIR / "downloads")))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


# Logging global a la consola normal (stderr). La consola en vivo por WebSocket se sacó en f42.
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
logger = logging.getLogger("bot_web")


# ============================================================
#  AGENTES
# ============================================================
search_agent = SearchAgent(
    os.getenv("SPOTIFY_CLIENT_ID", ""),
    os.getenv("SPOTIFY_CLIENT_SECRET", ""),
    os.getenv("SOUNDCLOUD_CLIENT_ID", ""),
)
recommendation_agent = RecommendationAgent()


# ============================================================
#  APP
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(db.init_db)  # crea las tablas del historial si faltan
    logger.info("🌐 Servidor web iniciado. Buscador listo.")
    threading.Thread(target=_calentar_librosa, name="calentar-librosa", daemon=True).start()
    yield
    # f53: el análisis de playlists en segundo plano corta después del archivo en curso.
    _analizador.detener()


def _calentar_librosa() -> None:
    """Primera llamada a librosa en segundo plano, al arrancar (f32).

    Medido: en un proceso nuevo, el primer análisis tarda 11-17 s (importar librosa y cargar
    lo que numba compila) y el segundo 0,2 s. Sin esto, ese costo lo pagaba el primer
    "parecidas" después de levantar el server (y con 16 análisis a la vez, más). Se analiza
    una señal sintética de 2 s en un temporal; si algo falla no pasa nada: el primer pedido
    real paga el costo como antes."""
    if os.getenv("MUSIFLIX_SIN_CALENTAR"):
        return
    import tempfile
    from contextlib import suppress
    ruta = None
    try:
        import analisis_audio
        import numpy as np
        import soundfile as sf
        sr = 22050
        t = np.arange(sr * 2) / sr
        fd, ruta = tempfile.mkstemp(suffix=".wav", prefix="musiflix-calentar-")
        os.close(fd)
        sf.write(ruta, (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr)
        t0 = time.perf_counter()
        analisis_audio.bpm_y_tono(ruta, dur=2)
        logger.info(f"🔥 Análisis de audio listo ({time.perf_counter() - t0:.1f} s de arranque).")
    except Exception as e:
        logger.debug(f"calentar librosa: {e}")
    finally:
        if ruta:
            with suppress(OSError):
                os.remove(ruta)


app = FastAPI(title="Bot de Música - Web", lifespan=lifespan)


@app.get("/")
async def index():
    # App React (build de Vite). Si todavía no se buildeó, cae a la vanilla.
    build = DIST_DIR / "index.html"
    return FileResponse(build if build.exists() else WEB_DIR / "index.html")


@app.get("/legacy")
async def index_legacy():
    """Frontend viejo (vanilla), para comparar durante la migración a React."""
    return FileResponse(WEB_DIR / "index.html")


# Tope de espera por fuente en una búsqueda (f32). Antes era `fut.result(timeout=30)` futuro
# por futuro DENTRO del `with ThreadPoolExecutor`: el tope se reiniciaba con cada fuente y, al
# salir del `with`, se esperaba igual a la que colgaba — medido, una búsqueda de YouTube de
# 56 s se descartaba por "timeout" y además se la esperaba entera. Ahora es un plazo único:
# lo que no contestó a tiempo queda afuera y NO se espera.
_SOURCE_DEADLINE_S = 20.0
# Cache de búsquedas (f32): la misma consulta se repite (reabrir parecidas del mismo tema,
# buscar de nuevo). Solo se guarda si contestaron TODAS las fuentes: un resultado recortado
# por una fuente caída no se cachea. TTL corto porque los MP3 directos traen URLs firmadas.
_MIX_TTL_S = 10 * 60
_MIX_MAX = 500
_MIX_CACHE: dict = {}
_MIX_LOCK = threading.Lock()


def _buscar_mix(q: str, limite: int) -> list:
    """Consulta todas las fuentes EN PARALELO y las intercala (round-robin)."""
    return _buscar_mix_detalle(q, limite)[0]


def _buscar_mix_detalle(q: str, limite: int, fuentes: tuple | None = None) -> tuple:
    """(mezcla, fuentes que no contestaron). `fuentes` (f36) limita a qué plataformas se
    pregunta: las versiones de la Station no le vuelven a preguntar a SoundCloud por cada tema.
    Cacheado como `_buscar_mix` (solo si contestaron todas). f40-r2: sale el parámetro
    `con_sin_configurar` (y su lugar en la caché): desde 47f4754 ninguna fila dice "Sin
    configurar", nadie lo pedía."""
    clave = (q, limite) if fuentes is None else (q, limite, tuple(sorted(fuentes)))
    with _MIX_LOCK:
        hit = _MIX_CACHE.get(clave)
        if hit and time.monotonic() - hit[0] < _MIX_TTL_S:
            return [dict(c) for c in hit[1]], []
    mezcla, fallidas, _ = _mix_fuentes(q, limite, fuentes)
    if not fallidas:
        # "Sin configurar" no es una caída: no cambia hasta reiniciar con otra config, se cachea.
        with _MIX_LOCK:
            if len(_MIX_CACHE) >= _MIX_MAX:
                _MIX_CACHE.clear()
            _MIX_CACHE[clave] = (time.monotonic(), [dict(c) for c in mezcla])
    return mezcla, fallidas


def _buscar_mix_fuentes(q: str, limite: int) -> tuple[list, bool]:
    """(mezcla, completa): completa = contestaron todas las fuentes a tiempo (una sin
    configurar no la deja incompleta: no hay nada que esperar de ella)."""
    mezcla, fallidas, _ = _mix_fuentes(q, limite)
    return mezcla, not fallidas


def _mix_fuentes(q: str, limite: int, fuentes: tuple | None = None) -> tuple[list, list, list]:
    """(mezcla, nombres de las fuentes que fallaron o no contestaron a tiempo, nombres de las
    que no están configuradas). Una fuente caída SEÑALA el error (`fuente_errores.FuenteCaida`
    o cualquier excepción) en vez de devolver []: si no, "no lo encontré" mentía (f40)."""
    from concurrent.futures import ThreadPoolExecutor, wait

    por_fuente = max(4, limite // 4)

    # (nombre, callable) de cada fuente
    tareas = [
        ("youtube", lambda: search_agent.buscar_en_youtube(q, limit=por_fuente)),
        ("ligaudio", lambda: FUENTES_SCRAPER[0][1](q, limit=por_fuente)),
        ("hitplayer", lambda: FUENTES_SCRAPER[1][1](q, limit=por_fuente)),
        ("spotify", lambda: search_agent.buscar_en_spotify(q, limit=por_fuente)),
        ("soundcloud", lambda: search_agent.buscar_en_soundcloud(q, limit=por_fuente)),
    ]
    if fuentes is not None:
        tareas = [(n, fn) for n, fn in tareas if n in fuentes]

    resultados = {}
    fallidas, sin_config = [], []
    ex = ThreadPoolExecutor(max_workers=max(1, len(tareas)))
    try:
        futuros = {ex.submit(fn): nombre for nombre, fn in tareas}
        wait(futuros, timeout=_SOURCE_DEADLINE_S)
        for fut, nombre in futuros.items():
            if not fut.done():
                logger.warning(f"⚠️ Fuente '{nombre}' no contestó en {_SOURCE_DEADLINE_S:.0f} s: queda afuera.")
                resultados[nombre] = []
                fallidas.append(nombre)
                continue
            try:
                resultados[nombre] = fut.result() or []
            except FuenteNoConfigurada:
                resultados[nombre] = []
                sin_config.append(nombre)
            except Exception as e:
                logger.warning(f"⚠️ Fuente '{nombre}' falló: {e}")
                resultados[nombre] = []
                fallidas.append(nombre)
    finally:
        # Sin esperar a la que cuelga: su hilo termina solo (cada fuente tiene su timeout de red).
        ex.shutdown(wait=False, cancel_futures=True)

    # Orden de intercalado: primero las que se reproducen/descargan directo
    orden = ["youtube", "ligaudio", "hitplayer", "soundcloud", "spotify"]
    grupos = [resultados.get(n, []) for n in orden]

    mezcla, i = [], 0
    while len(mezcla) < limite and any(i < len(g) for g in grupos):
        for g in grupos:
            if i < len(g):
                mezcla.append(g[i])
        i += 1
    return mezcla[:limite], fallidas, sin_config


def _rank_calidad(fuente: str, formato: str) -> int:
    """Prioridad de fuente según el formato de descarga pedido.
    Para destinos lossless (wav/flac) conviene la mejor fuente convertible;
    las que ya vienen en MP3 fijo (scrapers) van al final porque convertir un MP3
    a WAV no recupera calidad. Para MP3 el orden casi no importa, se usa el mismo."""
    f = (fuente or "").lower()
    base = {"youtube": 0, "soundcloud": 1, "spotify": 2, "deezer": 2,
            "ligaudio": 3, "hitplayer": 4}
    return base.get(f, 9)


def _opciones_de(linea: str, formato: str, n: int = 3, identidad=None) -> list:
    """Busca UNA línea de la lista y devuelve hasta N opciones: primero la mejor de cada
    plataforma distinta (en orden de prioridad para el formato), así el usuario compara
    YouTube vs SoundCloud vs MP3 directo con el Spek; si hay menos de N plataformas, completa
    con otros resultados de las mismas (puede repetir plataforma: ['youtube', 'soundcloud',
    'youtube']). Lista vacía si no hubo resultado.

    Con `identidad` (un `track_identity.Identity`, lo pasa parecidas) solo quedan las
    opciones que SON ese tema (mismo artista, título y versión): medido, de 36 opciones de
    parecidas solo 13 lo eran ("Thottie Frutti" por "Thootie", "ruth b - dandelions" por
    "BIA - TWIN"). Una plataforma sin ninguna que pase no aparece: nunca un tema distinto."""
    linea = (linea or "").strip()
    if not linea:
        return []
    cands = _buscar_mix(linea, 12)
    if identidad is not None:
        cands = [c for c in cands if track_identity.is_same_track(
            identidad, track_identity.parse_entry(c.get("titulo") or "", c.get("artista") or ""))]
    if not cands:
        return []
    cands.sort(key=lambda c: _rank_calidad(c.get("fuente", ""), formato))

    # 1) Elegir el mejor candidato de cada fuente distinta (diversidad de plataformas).
    elegidas, fuentes_vistas = [], set()
    for idx, c in enumerate(cands):
        f = (c.get("fuente") or "").lower()
        if f not in fuentes_vistas:
            fuentes_vistas.add(f)
            elegidas.append(idx)
        if len(elegidas) >= n:
            break
    # 2) Si no hubo suficientes fuentes distintas, completar con los mejores restantes.
    if len(elegidas) < n:
        for idx in range(len(cands)):
            if idx not in elegidas:
                elegidas.append(idx)
                if len(elegidas) >= n:
                    break

    top = []
    for idx in elegidas[:n]:
        c = dict(cands[idx])
        c["consulta"] = linea  # la línea original tal cual la pegó el usuario
        top.append(c)
    return top


# Palabras de ruido que NO identifican al tema (tags de YouTube, artículos, etc.).
# OJO: "remix"/"edit"/"mix"/"version" NO están acá a propósito — para un DJ un remix
# es OTRO tema, así que deben mantenerse como tokens que diferencian.
_STOP_TRACK = {
    "official", "video", "oficial", "audio", "lyric", "lyrics", "letra", "hd", "hq",
    "4k", "mv", "m", "v", "remaster", "remastered", "explicit", "clip", "visualizer",
    "feat", "ft", "featuring", "con", "the", "a", "el", "la", "los", "las", "de", "del",
    "y", "and", "x", "vs", "full", "free", "download", "out", "now", "premiere",
}


def _tokens_de(txt: str) -> set:
    """Tokens significativos de un texto (saca tags entre (), [], {} y puntuación)."""
    txt = (txt or "").lower()
    txt = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", txt)      # quitar (...), [...], {...} (tags)
    txt = re.sub(r"[^0-9a-záéíóúüñ\s]", " ", txt, flags=re.IGNORECASE)  # sacar puntuación
    return {t for t in txt.split() if len(t) > 1 and t not in _STOP_TRACK}


def _firma_track(c: dict) -> set:
    """Conjunto de tokens de (título + artista), para agrupar versiones del MISMO tema."""
    return _tokens_de(f"{c.get('titulo','')} {c.get('artista','')}")


def _relevancia(cand_tok: set, q_tok: set) -> float:
    """Qué tan bien matchea un candidato con la consulta (0..1). Coeficiente de
    solapamiento; para consultas de ≥2 palabras exige al menos 2 tokens en común
    (así 'Луиза — Киркоров' da 0 contra 'From Fire Edyom Fidelys')."""
    if not q_tok:
        return 1.0
    if not cand_tok:
        return 0.0
    inter = len(cand_tok & q_tok)
    if len(q_tok) >= 2 and inter < 2:
        return 0.0
    return inter / min(len(cand_tok), len(q_tok))


def _mismo_track(a: set, b: set) -> bool:
    """¿Dos firmas son el mismo tema? Coeficiente de solapamiento sobre el conjunto
    más chico (tolera ruido extra en un lado), exigiendo ≥2 tokens en común.
    Umbral alto (0.72) para NO fusionar de más: preferimos que un tema aparezca dos
    veces antes que mezclar un remix con el original."""
    if not a or not b:
        return False
    inter = len(a & b)
    if inter < 2:
        return False
    return inter / min(len(a), len(b)) >= 0.72


def _agrupar_por_track(cands: list, formato: str, query: str = "") -> list:
    """Agrupa los resultados de búsqueda en UNA fila por tema, con sus versiones de
    distintas plataformas como 'opciones'. FILTRA la basura irrelevante a la consulta
    (los scrapers a veces devuelven temas al azar) y ordena por relevancia.

    Detección automática: si la consulta parece un tema (hay un match fuerte), filtra
    duro; si parece género/mood (nada matchea por tokens), NO filtra (deja explorar)."""
    qtok = _tokens_de(query)
    punt = [(c, _relevancia(_firma_track(c), qtok)) for c in cands]
    mejor = max((s for _, s in punt), default=0.0)
    UMBRAL = 0.6
    if qtok and mejor >= UMBRAL:                  # consulta tipo TEMA → filtrar basura
        cands = [c for c, s in punt if s >= UMBRAL]
    # si es género/mood (mejor < UMBRAL) se deja todo tal cual

    score = {id(c): s for c, s in punt}
    clusters: list = []  # {sig, opciones, fuentes, score}
    for c in cands:
        sig = _firma_track(c)
        destino = None
        for cl in clusters:
            if _mismo_track(sig, cl["sig"]):
                destino = cl
                break
        if destino is None:
            clusters.append({"sig": set(sig), "opciones": [c],
                             "fuentes": {(c.get("fuente") or "").lower()},
                             "score": score.get(id(c), 0.0)})
        else:
            f = (c.get("fuente") or "").lower()
            if f not in destino["fuentes"]:      # una versión por plataforma
                destino["opciones"].append(c)
                destino["fuentes"].add(f)
                destino["sig"] |= sig            # la firma crece con lo que aporta cada fuente
                destino["score"] = max(destino["score"], score.get(id(c), 0.0))

    # Orden: primero los que mejor matchean con lo que buscaste
    clusters.sort(key=lambda cl: cl["score"], reverse=True)
    grupos = []
    for cl in clusters:
        opts = sorted(cl["opciones"], key=lambda o: _rank_calidad(o.get("fuente", ""), formato))
        grupos.append({"opciones": opts})        # sin 'consulta': en búsqueda no hay línea pegada
    return grupos


_LIST_WORKERS = 12


def _buscar_lista(lineas: list, formato: str, identidades: list | None = None) -> list:
    """Busca cada línea EN PARALELO y devuelve sus opciones (lista de candidatos,
    vacía si esa línea no tuvo resultados), conservando el orden de la lista.
    `identidades` (una por línea, parecidas) filtra las opciones que no son ese tema."""
    from concurrent.futures import ThreadPoolExecutor

    out: list = [[] for _ in lineas]
    idents = identidades if identidades is not None else [None] * len(lineas)
    # 12 líneas a la vez (antes 6: las 12 parecidas iban en dos tandas). Cada línea abre 5
    # fuentes, así que el tope real es ~60 pedidos de red simultáneos; más no se gana nada.
    with ThreadPoolExecutor(max_workers=max(1, min(_LIST_WORKERS, len(lineas)))) as ex:
        futs = {ex.submit(_opciones_de, ln, formato, 3, ident): idx
                for idx, (ln, ident) in enumerate(zip(lineas, idents, strict=True))}
        for fut in futs:
            idx = futs[fut]
            try:
                out[idx] = fut.result(timeout=45) or []
            except Exception as e:
                logger.warning(f"⚠️ Línea {idx + 1} falló: {e}")
                out[idx] = []
    return out


@app.post("/api/buscar_lista")
async def buscar_lista(payload: dict):
    """Modo lista (DJ): recibe muchas canciones (una por línea) y devuelve, por
    cada una, hasta 3 opciones priorizadas por fuente según el formato. El usuario
    elige cuál bajar en el front."""
    texto = (payload.get("lista") or "").strip()
    formato = (payload.get("formato") or "wav").lower()
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    if not lineas:
        return JSONResponse({"exito": False, "mensaje": "Pegá una lista de canciones (una por línea)."}, status_code=400)

    logger.info(f"📋 Lista recibida: {len(lineas)} temas. Buscando hasta 3 opciones de cada uno para {formato.upper()}…")
    resultados = await asyncio.to_thread(_buscar_lista, lineas, formato)

    grupos, no_encontradas = [], []
    for ln, opciones in zip(lineas, resultados):
        if opciones:
            grupos.append({"consulta": ln, "opciones": opciones})
        else:
            no_encontradas.append(ln)

    extra = f", {len(no_encontradas)} sin resultado" if no_encontradas else ""
    logger.info(f"✅ Lista lista: {len(grupos)}/{len(lineas)} encontradas{extra}.")
    if grupos:
        nombre = lineas[0][:80] + (f" +{len(lineas) - 1}" if len(lineas) > 1 else "")
        await asyncio.to_thread(db.guardar_playlist, nombre, texto, grupos, len(lineas), len(grupos))
    return {"exito": True, "total": len(lineas), "encontradas": len(grupos),
            "grupos": grupos, "no_encontradas": no_encontradas}


@app.get("/api/buscar")
async def buscar(q: str = "", limite: int = 24, formato: str = "wav", genero: str = ""):
    """Busca una canción/artista/género y devuelve los resultados agrupados por TEMA
    (una fila por track, con sus versiones de cada plataforma como opciones).
    `genero` (opcional) sesga la búsqueda hacia ese estilo (ej. 'hard techno')."""
    q = (q or "").strip()
    genero = (genero or "").strip()
    # El género se agrega a la consulta para orientar a las fuentes (YouTube/SoundCloud).
    busqueda = f"{q} {genero}".strip()
    if not busqueda:
        return JSONResponse({"exito": False, "mensaje": "Escribí algo o elegí un género."}, status_code=400)

    logger.info(f"🔍 Nueva búsqueda: '{busqueda}' (hasta {limite} resultados)")
    # yt-dlp/spotipy son bloqueantes -> correr en hilo aparte para no frenar el server
    resultados = await asyncio.to_thread(_buscar_mix, busqueda, limite)

    if not resultados:
        logger.warning("❌ Sin resultados.")
        return {"exito": False, "mensaje": "No se encontraron canciones.", "canciones": []}

    # La relevancia se mide contra lo que el usuario TIPEÓ (q); si solo eligió género,
    # se usa el género como consulta (así no filtra de más un browse por estilo).
    grupos = _agrupar_por_track(resultados, (formato or "wav").lower(), q or genero)
    logger.info(f"✅ {len(resultados)} resultados → {len(grupos)} temas (agrupados por versión).")
    await asyncio.to_thread(db.registrar_busqueda, q, len(grupos))
    # `canciones` se mantiene por compatibilidad; el front usa `grupos`.
    return {"exito": True, "mensaje": f"{len(grupos)} temas",
            "grupos": grupos, "canciones": resultados}


@app.get("/api/meta")
async def meta(titulo: str, artista: str = ""):
    """BPM + género de un tema (Deezer + fallback librosa). Cacheado. Carga lazy.
    Con cola: el cómputo corre en el worker; el endpoint espera y devuelve igual."""
    if jobs.queue_disponible():
        import tasks
        job = jobs.encolar(tasks.meta_job, titulo, artista, timeout=120)
        return await asyncio.to_thread(jobs.esperar_resultado, job, 90)
    import similares
    return await asyncio.to_thread(similares.meta_de, titulo, artista, True)


@app.get("/api/parecidas")
async def parecidas(titulo: str, artista: str = "", total: int = 25):
    """Arma una playlist de temas parecidos (Deezer + BPM/tono) al tema dado."""
    titulo = (titulo or "").strip()
    if not titulo:
        return JSONResponse({"exito": False, "mensaje": "Falta el título."}, status_code=400)

    logger.info(f"🎵 Buscando {total} parecidas a: '{titulo}' — {artista}")
    import similares
    res = await asyncio.to_thread(similares.construir_playlist, titulo, artista, total, True)
    if not res.get("exito"):
        logger.warning(f"❌ {res.get('mensaje', 'No se pudo armar la playlist parecida.')}")
        return res

    s = res["seed"]
    extra = f", tono {s['tono']} ({s['camelot']})" if s.get("tono") else ""
    logger.info(f"✅ {len(res['canciones'])} parecidas listas. Semilla: {s['bpm'] or '?'} BPM{extra}.")
    return res


@app.get("/api/parecidas_lista")
async def parecidas_lista(titulo: str, artista: str = "", total: int = 12,
                          formato: str = "wav", genero: str = "", fuente: str = "", fuente_id: str = "",
                          duracion: float | None = None):
    """Como /api/parecidas, pero por CADA tema parecido trae hasta 3 opciones de
    plataformas distintas (YouTube, SoundCloud, MP3 directo…) para comparar con el
    Spek y elegir la mejor. Devuelve 'grupos' como el modo lista + la semilla.
    `genero` es una pista opcional para acertar el género de las parecidas.
    `fuente` + `fuente_id` (el id del resultado): si es SoundCloud se le pide el ISRC y la
    semilla se resuelve por ISRC en Deezer (si el track confirma lo que dice el upload).
    `duracion` (segundos del resultado) solo desempata entre lanzamientos del mismo tema;
    nan, inf o ≤ 0 cuentan como "no se sabe".
    Sin semilla verificada contesta
    {exito: false, motivo, mensaje: "Similitud no disponible para este track", detalle}."""
    titulo = (titulo or "").strip()
    if not titulo:
        return JSONResponse({"exito": False, "mensaje": "Falta el título."}, status_code=400)

    logger.info(f"🎵 Parecidas con opciones a: '{titulo}' — {artista} (hasta {total})"
                + (f" [género: {genero}]" if genero else ""))
    import similares
    isrc = None
    if (fuente or "").lower() == "soundcloud" and fuente_id:
        isrc = await asyncio.to_thread(track_identity.fetch_soundcloud_isrc, fuente_id)
    res = await asyncio.to_thread(similares.construir_playlist, titulo, artista, total, True,
                                  genero or None, isrc, track_identity.duration_or_none(duracion, fuente or None),
                                  fuente=(fuente or "").lower() or None)
    if not res.get("exito"):
        logger.warning(f"❌ {res.get('mensaje', 'No se pudo armar la playlist parecida.')}"
                       + (f" ({res['motivo']})" if res.get("motivo") else ""))
        return res

    formato = (formato or "wav").lower()
    lineas = [f"{c['artista']} - {c['titulo']}".strip(" -") for c in res["canciones"]]
    identidades = [track_identity.parse_fields(c["artista"], c["titulo"]) for c in res["canciones"]]
    resultados = await asyncio.to_thread(_buscar_lista, lineas, formato, identidades)

    grupos, no_encontradas = [], []
    for ln, opciones in zip(lineas, resultados):
        if opciones:
            grupos.append({"consulta": ln, "opciones": opciones})
        else:
            no_encontradas.append(ln)

    logger.info(f"✅ Parecidas con opciones: {len(grupos)}/{len(lineas)} con plataformas.")
    respuesta = {"exito": True, "seed": res["seed"], "total": len(lineas),
                 "encontradas": len(grupos), "grupos": grupos, "no_encontradas": no_encontradas}
    if not grupos:
        # Hay semilla pero no hay nada que mostrar. Sin un motivo propio el front lo mostraba
        # con el cartel del modo lista ("revisá que haya un tema por línea"): un pedido que
        # el DJ no hizo. Se dice cuál de los dos vacíos es.
        respuesta.update(_parecidas_vacias(res["seed"], lineas, res.get("relacionados")))
    return respuesta


def _parecidas_vacias(seed: dict, lineas: list[str], relacionados: int | None) -> dict:
    """`motivo`, `mensaje` y `detalle` de una lista de parecidas vacía con semilla."""
    # Se dice lo que DEVOLVIÓ Deezer, no lo que existe en Deezer: `similares._get` contesta
    # vacío también cuando Deezer no respondió, y el pool mira pocos temas por artista (2
    # álbumes recientes × 3 + top 3). "Deezer no tiene…" sería afirmar algo que no se sabe (§6).
    tema, quien = seed.get("titulo") or "", seed.get("artista") or "el artista"
    if not lineas:
        if relacionados == 0:
            detalle = (f"Deezer no devolvió artistas relacionados con {quien} ni otros temas "
                       f"suyos con preview para comparar.")
        elif relacionados:
            s = "s" if relacionados != 1 else ""
            detalle = (f"Deezer devolvió {relacionados} artista{s} relacionado{s} con {quien}, pero "
                       f"ningún tema con preview para comparar (ni suyos ni de ellos).")
        else:
            detalle = "Deezer no devolvió temas para comparar."
        return {"motivo": "sin_candidatos", "mensaje": f"No encontré temas parecidos a «{tema}»",
                "detalle": detalle}
    # Una plataforma que no contestó a tiempo, o una opción que no era el mismo tema, también
    # termina en "no apareció": se dice lo que se encontró, no que no existe.
    faltan = ", ".join(lineas[:5]) + (f" y {len(lineas) - 5} más" if len(lineas) > 5 else "")
    s = "s" if len(lineas) != 1 else ""
    return {"motivo": "sin_plataformas",
            "mensaje": (f"Encontré {len(lineas)} tema{s} parecido{s} a «{tema}», pero no encontré "
                        f"ninguno en YouTube, SoundCloud ni MP3"),
            "detalle": f"No aparecieron (o no eran el mismo tema): {faltan}."}


# Fuentes de las que puede venir un resultado (las de _buscar_mix_fuentes).
_STATION_SOURCES = {"soundcloud", "youtube", "spotify", "deezer", "ligaudio", "hitplayer"}
_STATION_MAX_TEXT = 300


@app.get("/api/station")
async def station(fuente: str = "", fuente_id: str = "", titulo: str = "", artista: str = "",
                  duracion: str = "", sc_ref: str = "", sc_ref_origen: str = ""):
    """Station de SoundCloud del tema (f34): ~50 temas del mismo estilo, en el orden de
    SoundCloud, sin la semilla. Es el recomendador de SoundCloud, no una medición nuestra.
    Con `fuente=soundcloud`, `fuente_id` (id numérico) ES la semilla. Con otra fuente se busca
    el tema en SoundCloud y se acepta solo si pasa la regla de identidad (nunca otra canción).
    f43: `sc_ref` (id numérico de la opción de SoundCloud de la fila) con `sc_ref_origen`
    "busqueda" (candidata: se valida por identidad + duración antes de usarla) o "station"
    (la fila es un tema de la Station: va directo, como fuente=soundcloud).
    `duracion` (s) solo desempata; nan, inf, ≤ 0 o basura cuentan como "no se sabe".
    Respuesta: {exito, origen: "soundcloud_station", semilla, items, total} o
    {exito: false, motivo, codigo, mensaje}; 400 si el pedido es inválido. Nunca 500."""
    import soundcloud_station as sc

    fuente = (fuente or "").strip().lower()
    fuente_id = (fuente_id or "").strip()
    titulo = (titulo or "").strip()
    artista = (artista or "").strip()
    sc_ref = (sc_ref or "").strip()
    sc_ref_origen = (sc_ref_origen or "").strip().lower()
    malo = None
    if fuente not in _STATION_SOURCES:
        malo = "Fuente no soportada."
    elif len(titulo) > _STATION_MAX_TEXT or len(artista) > _STATION_MAX_TEXT or len(duracion or "") > 32:
        malo = "Título, artista o duración demasiado largos."
    elif fuente == "soundcloud" and not sc.SC_ID.fullmatch(fuente_id):
        malo = "Identificador de SoundCloud inválido."
    elif fuente != "soundcloud" and not titulo:
        malo = "Falta el título."
    elif (sc_ref or sc_ref_origen) and not (sc.SC_ID.fullmatch(sc_ref) and sc_ref_origen in sc.REF_ORIGINS):
        malo = "Referencia de SoundCloud inválida."
    if malo:
        return JSONResponse(sc.failure(sc.INVALID_REQUEST, malo), status_code=400)

    logger.info(f"📻 Station de SoundCloud de: '{titulo or fuente_id}' — {artista} [{fuente}]")
    try:
        return await asyncio.to_thread(sc.build_station, fuente, fuente_id, titulo, artista,
                                       track_identity.duration_or_none(duracion, fuente),
                                       sc_ref=sc_ref, sc_ref_origen=sc_ref_origen)
    except Exception as e:           # build_station no debería lanzar; si lo hace, no es un 500
        logger.warning(f"⚠️ Station: error inesperado {type(e).__name__}")
        return sc.failure(sc.UNEXPECTED_RESPONSE, code=sc.SOUNDCLOUD_ERROR)


# ---------------------------------------------------------------- orden "Para mezclar" (f45)
# Opt-in: el orden de SoundCloud sigue siendo el default. El front pide el análisis de cada tema
# (uno por pedido, cola chica) y, cuando están todos, el orden. Detalle en station_mezcla.py.

@app.get("/api/station/analisis")
async def station_analisis(ref: str = ""):
    """BPM (1 decimal), key con su acuerdo y si es dudosa, de UN tema de SoundCloud (id
    numérico), medidos sobre un tramo de su audio. {ok, bpm, key, key_dudosa, preview, ...} o
    {ok: false, motivo}. 400 si el id es inválido. Nunca 500."""
    import soundcloud_station as sc
    import station_mezcla as sm

    ref = (ref or "").strip()
    if not sc.SC_ID.fullmatch(ref):
        return JSONResponse({"ok": False, "motivo": "Identificador de SoundCloud inválido."}, status_code=400)
    return sm.publico(await asyncio.to_thread(sm.analizar, ref))


@app.post("/api/station/orden")
async def station_orden(request: Request):
    """Orden "Para mezclar": {semilla: id, items: [{video_id, titulo, artista, duracion, url}]}
    en el orden de SoundCloud → {exito, orden: [{video_id, grupo, razon | motivo}], ...}. Usa
    solo los análisis ya hechos (no dispara nuevos)."""
    import soundcloud_station as sc
    import station_mezcla as sm

    try:
        body = await request.json()
    except Exception:
        body = None
    items = body.get("items") if isinstance(body, dict) else None
    semilla = str(body.get("semilla") or "") if isinstance(body, dict) else ""
    if not isinstance(items, list) or not items or len(items) > sm.MAX_ITEMS or not sc.SC_ID.fullmatch(semilla):
        return JSONResponse({"exito": False, "mensaje": "Pedido inválido."}, status_code=400)
    limpios = []
    for it in items:
        if not isinstance(it, dict) or not sc.SC_ID.fullmatch(str(it.get("video_id") or "")):
            return JSONResponse({"exito": False, "mensaje": "Pedido inválido."}, status_code=400)
        dur = it.get("duracion")
        limpios.append({"video_id": str(it["video_id"]),
                        "titulo": str(it.get("titulo") or "")[:_STATION_MAX_TEXT],
                        "artista": str(it.get("artista") or "")[:_STATION_MAX_TEXT],
                        "duracion": float(dur) if isinstance(dur, (int, float)) and math.isfinite(dur) and dur > 0 else None,
                        "url": str(it.get("url") or "")[:500]})
    try:
        return await asyncio.to_thread(sm.ordenar, semilla, limpios)
    except Exception as e:
        logger.warning(f"⚠️ Orden para mezclar: error inesperado {type(e).__name__}: {e}")
        return {"exito": False, "mensaje": "No pude armar el orden para mezclar."}


# ---------------------------------------------------------------- versiones de la Station (f36)
# Cada tema de la Station se busca en las otras plataformas para bajarlo de donde mejor suene.
# El front pide UN tema por vez (con una cola de 3) y va mostrando las filas en el orden de la
# Station a medida que están: un pedido chico por fila es más simple y robusto que un stream
# (se cancela con un abort, reintenta solo esa fila y no depende de proxies que bufferean).
#
# Una versión de otra plataforma se ofrece SOLO si `track_identity` dice que es la misma
# grabación que el tema de la Station (artista, título, versión y duración): nunca otro tema,
# un vivo ni un remix. La excepción (f40-r2, decisión del dueño) es el Extended del MISMO tema,
# que se ofrece marcado como otra edición. Ver `_version_aceptada`.

# Plataformas a las que se les pregunta por cada tema. SoundCloud no: el tema YA es de
# SoundCloud, y 49 búsquedas seguidas es justo lo que la auditoría de f34 marcó como riesgo de
# 429. Solo se le pregunta cuando el tema de la Station es Go+ (30 s) o no tiene audio abrible,
# por si otro upload lo tiene completo (y eso por la API v2, que dice si es un preview).
_VERSION_SOURCES = ("youtube", "ligaudio", "hitplayer", "spotify")
_VERSION_SEARCH_LIMIT = 12
_VERSION_MAX = 5                       # opciones por fila (una por plataforma + otro upload de SC)
_VERSION_CAL_DEADLINE_S = 15.0         # tope para las notas de una fila; lo que falte, el front lo pide lazy
_VERSION_MAX_TEXT = 300
# SoundCloud (búsqueda API v2 + nota por yt-dlp): como mucho 2 pedidos a la vez desde versiones
# y, si contesta 429, una pausa: las filas lo dicen y siguen con las otras plataformas.
_SC_GATE = threading.BoundedSemaphore(2)
_SC_GATE_WAIT_S = 10.0
_SC_PAUSE_S = 60.0
_sc_pause_until = 0.0
_SC_URL = re.compile(r"https://(?:soundcloud\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+|api\.soundcloud\.com/tracks/[0-9]{1,20})")
_SOURCE_NAMES = {"youtube": "YouTube", "ligaudio": "MP3", "hitplayer": "MP3",
                 "spotify": "Spotify", "soundcloud": "SoundCloud"}


def _url_http(u) -> bool:
    """¿`u` es una URL http(s)? Lo que llega de los scrapers (y del cliente, en /api/calidad,
    /api/spectro y /api/descargar) termina en ffprobe/ffmpeg/requests: con otro esquema
    (`file:`, `concat:`, `subfile:`…) ffmpeg lee archivos locales. f40-r2."""
    return isinstance(u, str) and re.fullmatch(r"https?://[^\s\x00-\x1f]+", u, re.I) is not None


def _sc_paused() -> bool:
    return time.monotonic() < _sc_pause_until


def _sc_gated(fn):
    """Corre `fn` con el cupo de SoundCloud. None si SoundCloud está en pausa o no hubo cupo."""
    if _sc_paused() or not _SC_GATE.acquire(timeout=_SC_GATE_WAIT_S):
        return None
    try:
        return fn()
    finally:
        _SC_GATE.release()


def _version_identity(c: dict):
    """Identidad de un candidato: Spotify trae artista y título separados; el resto es un
    upload ("Artista - Tema" en el título, el canal como artista)."""
    if (c.get("fuente") or "").lower() in ("spotify", "deezer"):
        return track_identity.parse_fields(c.get("artista") or "", c.get("titulo") or "")
    return track_identity.parse_entry(c.get("titulo") or "", c.get("artista") or "")


def _version_valida(wanted, wanted_s, c: dict) -> str | None:
    """Grado de evidencia de que `c` es el tema de la Station, o None. Ver `_version_aceptada`
    (esto es su grado, para quien solo necesita saber si pasa)."""
    a = _version_aceptada(wanted, wanted_s, c)
    return a["evidencia"] if a else None


def _version_aceptada(wanted, wanted_s, c: dict) -> dict | None:
    """¿Se ofrece `c` como versión del tema de la Station? None si no; si sí, los campos que
    se le agregan a la opción: `evidencia` (grado), `duracion_verificada` y, si es la edición
    larga, `edicion: "extended"`.

    1) La misma grabación (`audio_exacto`: el tema de la Station ES el audio, no un video con
    intro). f40 (decisión del dueño, opción "a"): si las dos duraciones se conocen y NO cuadran
    (±max(10 s, 10 %), `track_identity.duraciones_cuadran`), no es esta edición, sea más corta
    o más larga: un video con intro larga tampoco se ofrece. La duración de cada candidato se
    lee según su fuente (`duration_or_none`): 30 s es "no se sabe" solo en SoundCloud.

    2) f40-r2 (decisión del dueño, "el extended es ORO"): el Extended del MISMO tema
    (`track_identity.es_extended_de`: Extended / Original Mix, o "Remix Extended" del mismo
    remix; "Club Mix" no desde f40-r3) se ofrece aunque no cuadre, si dura MÁS que el tema (hasta 3×): «Hera (Original
    Mix)» de 6:04 contra la «Hera» de 3:26 de la Station. Va marcado (`edicion`) porque es otra
    edición que la que sonó. Lo más corto nunca; lo más largo sin esa etiqueta, tampoco (1).

    `duracion_verificada` (f40-r2, decisión del dueño): False si la duración del candidato o la
    del tema no se conoce. Se ofrece igual, pero el front nunca la elige por defecto: «Argy -
    Aria» de HitPlayer (sin duración) resultó ser una edición de 252 s de un tema de 315 s."""
    cs = track_identity.duration_or_none(c.get("duracion"), c.get("fuente"))
    ident = _version_identity(c)
    verificada = bool(wanted_s and cs)
    if track_identity.es_extended_de(wanted, ident):
        largo = track_identity.dura_como_extended(wanted_s, cs)
        if largo is not False:          # True: confirmado; None: no se sabe (se ofrece sin elegir)
            return {"evidencia": "texto", "edicion": "extended", "duracion_verificada": bool(largo)}
        # False: más corto, igual de largo o demasiado largo; sigue la regla de la misma grabación.
    if wanted_s and cs and not track_identity.duraciones_cuadran(wanted_s, cs):
        return None
    grado = track_identity.evidencia_misma_grabacion(wanted, ident, wanted_s, cs, audio_exacto=True)
    return {"evidencia": grado, "duracion_verificada": verificada} if grado else None


# MP3 directos sin duración publicada (HitPlayer no la trae: `duracion` 0). Hasta f40 se medía
# con ffprobe sobre la URL y NUNCA servía: hotplayer sirve el MP3 por chunked (sin
# Content-Length) y ffprobe no estima la duración de un stream sin largo — imprime "N/A"
# (medido en f40-r2: 0 de 88 en producción, y 6 de 6 URLs reales probadas a mano; ~2 s y hasta
# 8 s por fila tirados). Lo que SÍ trae cada MP3 de HitPlayer es la cabecera "Info" de LAME
# (la de Xing para CBR) en el primer frame: el número EXACTO de frames. Con eso la duración es
# frames × muestras por frame / sample rate, leyendo los primeros KB y cortando la conexión.
# Verificado contra el archivo entero bajado: «Argy - Aria» 10519 frames a 48 kHz = 252,46 s
# (ffprobe del archivo: 252,44 s; paquetes contados: 10519) y «Space Motion - Hera» 8468
# frames a 44,1 kHz = 221,20 s (ffprobe: 221,20 s). Sin esa cabecera (o si no contesta a
# tiempo) la duración queda desconocida: se ofrece por texto, sin elegirla (ver arriba).
_VERSION_DUR_SOURCES = ("ligaudio", "hitplayer")
_VERSION_DUR_WORKERS = 4
_VERSION_DUR_DEADLINE_S = 5.0
_MP3_CABECERA_MAX = 256 * 1024          # ID3 con carátula grande: más que esto, no se sigue leyendo


def _duracion_mp3_cabecera(url: str, ua: str, timeout: float = _VERSION_DUR_DEADLINE_S) -> float:
    """Duración (s) de un MP3 remoto según su cabecera Xing/Info, o 0 si no se puede saber.
    Lee como mucho `_MP3_CABECERA_MAX` bytes (no baja el tema). Solo http(s)."""
    import requests

    if not _url_http(url):
        return 0.0
    buf = b""
    try:
        # Sin seguir redirecciones: `_url_http` valida solo la URL inicial, y un 302 del sitio
        # scrapeado llevaría el pedido a un host interno (SSRF ciego, auditoría final de f40).
        # Verificado el 02/10: los MP3 de hotplayer y lightaudio contestan 200 directo.
        with requests.get(url, headers={"User-Agent": ua} if ua else None, stream=True,
                          timeout=(min(timeout, 4.0), timeout), allow_redirects=False) as r:
            if r.status_code != 200:
                return 0.0
            limite = time.monotonic() + timeout
            for chunk in r.iter_content(16384):
                buf += chunk
                d = _duracion_xing(buf)          # None = todavía no alcanza para decidir
                if d is not None or time.monotonic() > limite:
                    return d or 0.0
    except Exception as e:
        logger.info(f"ℹ️ No pude leer la cabecera de un MP3: {type(e).__name__}")
    return _duracion_xing(buf) or 0.0


# MPEG audio: sample rate por versión (bits 19-20) e índice (bits 10-11). Versión 1 = MPEG-1.
_MP3_SR = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _duracion_xing(buf: bytes) -> float | None:
    """Duración según la cabecera Xing/Info del primer frame MPEG de `buf`, None si todavía no
    hay bytes suficientes para decidir, 0.0 si no la tiene (o no es un MP3 de capa 3, o con
    `_MP3_CABECERA_MAX` bytes todavía no se llegó al primer frame: un ID3 enorme). Con
    Info (CBR) y el campo de bytes, se verifica contra el bitrate: si no coinciden a ±2 %, la
    cabecera miente y la duración es desconocida."""
    import struct

    off = 0
    if buf[:3] == b"ID3":
        if len(buf) < 10:
            return None
        s = buf[6:10]
        off = 10 + ((s[0] & 0x7F) << 21 | (s[1] & 0x7F) << 14 | (s[2] & 0x7F) << 7 | (s[3] & 0x7F))
        if buf[5] & 0x10:                                  # pie de ID3v2.4
            off += 10
    if len(buf) < off + 4096:
        return None if len(buf) < _MP3_CABECERA_MAX else 0.0
    i = off
    while i < off + 4096 - 4 and not (buf[i] == 0xFF and (buf[i + 1] & 0xE0) == 0xE0):
        i += 1
    h = buf[i:i + 4]
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return 0.0
    version, capa = (h[1] >> 3) & 3, (h[1] >> 1) & 3
    sr_idx, br_idx = (h[2] >> 2) & 3, h[2] >> 4
    if version == 1 or capa != 1 or sr_idx == 3 or br_idx in (0, 15):   # solo capa 3, cabecera válida
        return 0.0
    sr = _MP3_SR[version][sr_idx]
    spf = 1152 if version == 3 else 576
    mono = (h[3] >> 6) == 3
    # El tag va después de la info lateral, cuyo largo depende de la versión y los canales.
    lateral = (17 if mono else 32) if version == 3 else (9 if mono else 17)
    p = i + 4 + lateral
    tag = buf[p:p + 4]
    if tag not in (b"Xing", b"Info"):
        return 0.0
    flags = struct.unpack(">I", buf[p + 4:p + 8])[0]
    if not flags & 1:                                       # sin cantidad de frames
        return 0.0
    frames = struct.unpack(">I", buf[p + 8:p + 12])[0]
    dur = frames * spf / sr
    if tag == b"Info" and flags & 2:                        # CBR: los bytes tienen que dar lo mismo
        bitrates = ((0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320) if version == 3
                    else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160))
        nbytes = struct.unpack(">I", buf[p + 12:p + 16])[0]
        por_bytes = nbytes * 8 / (bitrates[br_idx] * 1000)
        if not dur or abs(por_bytes - dur) > 0.02 * dur:
            return 0.0
    return dur if dur > 0 else 0.0


def _medir_duraciones_mp3(wanted, wanted_s, candidatos: list) -> None:
    """Completa en el lugar la `duracion` de los MP3 directos que la traen desconocida y que
    por texto SÍ se ofrecerían (los demás no se miden: se descartan igual). Sin duración de la
    Station no hay con qué comparar: no se mide nada."""
    from concurrent.futures import ThreadPoolExecutor, wait

    from scrapers import HEADERS

    if not wanted_s:
        return
    medir = [c for c in candidatos
             if (c.get("fuente") or "").lower() in _VERSION_DUR_SOURCES and _url_http(c.get("url"))
             and track_identity.duration_or_none(c.get("duracion"), c.get("fuente")) is None
             and _version_aceptada(wanted, wanted_s, c)]
    if not medir:
        return
    ua = HEADERS.get("User-Agent", "")
    ex = ThreadPoolExecutor(max_workers=min(_VERSION_DUR_WORKERS, len(medir)))
    try:
        futs = {ex.submit(_duracion_mp3_cabecera, c["url"], ua, _VERSION_DUR_DEADLINE_S): c for c in medir}
        wait(futs, timeout=_VERSION_DUR_DEADLINE_S)
        for fut, c in futs.items():
            try:
                d = fut.result() if fut.done() else 0.0
            except Exception:
                d = 0.0
            d = track_identity.duration_or_none(d, c.get("fuente"))
            if d:
                c["duracion"] = round(d, 1)
            else:
                logger.info(f"ℹ️ Versiones: no pude medir la duración de un MP3 ({c.get('fuente')}): se ofrece sin elegirla.")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)   # cada lectura tiene su propio tope


def _sc_otros_uploads(q: str, tema_id: str) -> tuple[list, str | None]:
    """Otros uploads COMPLETOS del tema en SoundCloud (para un tema Go+). (lista, motivo)."""
    global _sc_pause_until
    import soundcloud_station as sc

    def buscar():
        return sc.default_api().search_tracks(q, limit=10)

    try:
        crudos = _sc_gated(buscar)
    except sc.SoundCloudError as e:
        if "429" in (e.detail or ""):
            _sc_pause_until = time.monotonic() + _SC_PAUSE_S
            logger.warning("⚠️ SoundCloud contestó 429: pausa de versiones en SoundCloud.")
            return [], "SoundCloud frenó los pedidos (demasiados seguidos): no busqué otro upload completo"
        return [], "SoundCloud no contestó la búsqueda de otro upload completo"
    if crudos is None:
        return [], ("SoundCloud está en pausa por demasiados pedidos: no busqué otro upload completo"
                    if _sc_paused() else "SoundCloud estaba ocupado: no busqué otro upload completo")
    out = []
    for raw in crudos:
        m = sc.map_track(raw)
        # Solo sirve si se puede bajar entero: otro preview de 30 s no suma nada.
        if m and m["video_id"] != tema_id and not m["solo_preview"]:
            out.append(m)
    return out, None


def presupuesto_fila_s() -> float:
    """Peor caso (s) de UNA fila de versiones en el server, con las fases en serie: búsqueda en
    las plataformas (con el otro upload de SoundCloud en paralelo, mismo plazo) + medición de
    los MP3 sin duración + notas de calidad (el cupo de SoundCloud se espera DENTRO del plazo
    de las notas). Tiene que quedar por debajo de `TIMEOUT_FILA_MS` del front
    (frontend/src/stationVersions.js) con margen; si no, una fila que el server sí contesta se
    mostraría como "Tardó más de 45 s…". Lo fija un test (f40-r2: antes eran 43-53 s)."""
    return _SOURCE_DEADLINE_S + _VERSION_DUR_DEADLINE_S + _VERSION_CAL_DEADLINE_S


def _versiones_de(tema: dict, formato: str) -> dict:
    """Las versiones de UN tema de la Station: el tema mismo (SoundCloud) + la mejor de cada
    otra plataforma que pase la regla de identidad, cada una con su nota de /api/calidad.
    {exito: true, opciones, motivo, fallidas}. `motivo` (o None) dice por qué falta algo."""
    from concurrent.futures import ThreadPoolExecutor, wait

    t0 = time.perf_counter()
    base = dict(tema, estacion=True)
    wanted = track_identity.parse_entry(tema["titulo"], tema["artista"])
    wanted_s = track_identity.duration_or_none(tema.get("duracion"), "soundcloud")
    motivos, fallidas = [], []
    candidatos = []
    if not wanted.query_title or not wanted.artists:
        motivos.append("No pude leer artista y título de este tema para buscarlo en otras plataformas")
    else:
        q = " ".join(p for p in (wanted.artist_text, wanted.query_title, wanted.version_text) if p)
        # Otro upload completo en SoundCloud (tema Go+ o sin audio): EN PARALELO con las otras
        # plataformas y con el mismo plazo (f40-r2). En serie sumaba la espera del cupo (10 s)
        # y la búsqueda (hasta 15 s) a los 20 s de las plataformas: la fila podía pasar el tope
        # de 45 s del front (ver `presupuesto_fila_s`).
        sc_ex = fut_sc = None
        if tema.get("solo_preview") or tema.get("reproducible") is False:
            sc_ex = ThreadPoolExecutor(max_workers=1)
            fut_sc = sc_ex.submit(_sc_otros_uploads, q, tema.get("video_id") or "")
        t_busqueda = time.monotonic()
        try:
            mezcla, fallidas = _buscar_mix_detalle(q, _VERSION_SEARCH_LIMIT, _VERSION_SOURCES)
            candidatos = list(mezcla)
            if fut_sc is not None:
                wait([fut_sc], timeout=max(0.0, _SOURCE_DEADLINE_S - (time.monotonic() - t_busqueda)))
                otros, motivo_sc = [], "SoundCloud no contestó a tiempo la búsqueda de otro upload completo"
                if fut_sc.done():
                    try:
                        otros, motivo_sc = fut_sc.result()
                    except Exception as e:
                        logger.warning(f"⚠️ Versiones: otro upload de SoundCloud falló: {type(e).__name__}")
                        motivo_sc = "SoundCloud no contestó la búsqueda de otro upload completo"
                candidatos += otros
                if motivo_sc:
                    motivos.append(motivo_sc)
        finally:
            if sc_ex is not None:
                sc_ex.shutdown(wait=False, cancel_futures=True)   # la que cuelga termina sola
        _medir_duraciones_mp3(wanted, wanted_s, candidatos)

    # La mejor de cada plataforma que ES el tema (en el orden de prioridad de fuente). Dentro de
    # una plataforma (f40-r2): primero un Extended con la duración verificada (el que el DJ
    # quiere), después una de la duración del tema, y al final las de duración desconocida (se
    # ofrecen pero el front no las elige). A igualdad, la primera de la búsqueda (sort estable).
    aceptadas = []
    for c in candidatos:
        extra = _version_aceptada(wanted, wanted_s, c)
        if extra:
            aceptadas.append(dict(c, **extra))
    aceptadas.sort(key=lambda c: (_rank_calidad(c.get("fuente", ""), formato),
                                  0 if c["duracion_verificada"] and c.get("edicion") == "extended"
                                  else 1 if c["duracion_verificada"] else 2))
    elegidas, vistas = [], set()
    for c in aceptadas:
        f = (c.get("fuente") or "").lower()
        if f not in vistas:
            vistas.add(f)
            elegidas.append(c)
    # El tema de la Station primero entre las de SoundCloud; el orden final es el de fuente.
    opciones = sorted([base] + elegidas, key=lambda c: _rank_calidad(c.get("fuente", ""), formato))[:_VERSION_MAX]

    # Notas de calidad en paralelo (las de SoundCloud con cupo). Sin nota: un preview de 30 s
    # (no es el tema) y Spotify (se baja buscando en YouTube: la nota sería de otro audio).
    def nota(c):
        args = (c["titulo"], c.get("artista") or "", c.get("fuente") or "", c.get("url") or "")
        # Con Redis la nota espera al worker: como mucho el tope de la fila, no los 90 s de
        # /api/calidad (el hilo quedaba vivo mucho después de que la fila se entregó).
        espera = _VERSION_CAL_DEADLINE_S
        if (c.get("fuente") or "").lower() == "soundcloud":
            return _sc_gated(lambda: _calidad_cacheada(*args, espera=espera))
        return _calidad_cacheada(*args, espera=espera)

    medir = [i for i, c in enumerate(opciones)
             if not c.get("solo_preview") and (c.get("fuente") or "").lower() not in ("spotify", "deezer")]
    ex = ThreadPoolExecutor(max_workers=max(1, len(medir)))
    try:
        futs = {ex.submit(nota, opciones[i]): i for i in medir}
        wait(futs, timeout=_VERSION_CAL_DEADLINE_S)
        for fut, i in futs.items():
            try:
                opciones[i]["calidad"] = fut.result() if fut.done() else None
            except Exception as e:
                logger.warning(f"⚠️ Versiones: la nota de {opciones[i].get('fuente')} falló: {type(e).__name__}")
                opciones[i]["calidad"] = None
    finally:
        ex.shutdown(wait=False, cancel_futures=True)   # la que cuelga termina sola y queda en caché

    if fallidas:
        # dict.fromkeys: dos MP3 que no contestaron dicen "MP3" una sola vez (f38).
        nombres = ", ".join(dict.fromkeys(_SOURCE_NAMES.get(f, f) for f in fallidas))
        motivos.append(f"No contestó a tiempo: {nombres}")
    # Con la versión de SoundCloud sola NO se agrega ningún motivo (decisión del dueño, f40): la
    # pastilla de SoundCloud ya dice que es la única, y "No lo encontré en otras plataformas" era
    # ruido. Tampoco "Sin configurar: Spotify" (no buscar ahí no es algo que el DJ tenga que leer
    # en cada fila). Lo que SÍ se dice es una caída ("No contestó a tiempo"): sin eso, una
    # plataforma rota se vería igual que un tema que no está.
    logger.info(f"🎚️ Versiones de «{tema['titulo']}»: {len(opciones)} en {time.perf_counter() - t0:.1f} s"
                + (f" ({'; '.join(motivos)})" if motivos else ""))
    return {"exito": True, "opciones": opciones, "motivo": ". ".join(motivos) or None, "fallidas": fallidas}


@app.post("/api/versiones")
async def versiones(payload: dict):
    """Versiones de un tema de la Station de SoundCloud (f36). Cuerpo:
    {tema: {titulo, artista, duracion, video_id, url, permalink, thumbnail, reproducible,
    solo_preview}, formato}. Respuesta: {exito, opciones, motivo, fallidas}; 400 si el pedido
    es inválido. Nunca 500: si algo falla, la fila queda con la versión de SoundCloud."""
    import soundcloud_station as sc

    tema = payload.get("tema") if isinstance(payload, dict) else None
    formato = str((payload or {}).get("formato") or "wav").lower()[:8]
    malo = None
    if not isinstance(tema, dict):
        malo = "Falta el tema."
    else:
        texto = {k: tema.get(k) for k in ("titulo", "artista")}
        if not all(isinstance(v, str) for v in texto.values()) or not texto["titulo"].strip():
            malo = "Falta el título."
        elif any(len(v) > _VERSION_MAX_TEXT for v in texto.values()):
            malo = "Título o artista demasiado largos."
        elif not sc.SC_ID.fullmatch(str(tema.get("video_id") or "")):
            malo = "Identificador de SoundCloud inválido."
        elif not isinstance(tema.get("url"), str) or not _SC_URL.fullmatch(tema["url"]):
            malo = "URL de SoundCloud inválida."
    if malo:
        return JSONResponse({"exito": False, "motivo": "pedido_invalido", "mensaje": malo}, status_code=400)

    # Solo los campos conocidos vuelven en la respuesta (nada de lo que mande el cliente de más).
    permalink = tema.get("permalink") if isinstance(tema.get("permalink"), str) and _SC_URL.fullmatch(tema["permalink"]) else None
    # La carátula vuelve al front y se pinta: solo una imagen del CDN de SoundCloud, con la misma
    # regla que `map_track` usa al leer la Station (f40; antes cualquier texto < 500 caracteres).
    thumb = tema.get("thumbnail") if (isinstance(tema.get("thumbnail"), str) and len(tema["thumbnail"]) < 500
                                      and sc._IMAGE.fullmatch(tema["thumbnail"])) else None
    limpio = {
        "titulo": tema["titulo"].strip(), "artista": tema["artista"].strip(),
        "duracion": track_identity.duration_or_none(tema.get("duracion"), "soundcloud") or None,
        "url": tema["url"], "fuente": "soundcloud", "video_id": str(tema["video_id"]),
        "thumbnail": thumb, "permalink": permalink,
        "reproducible": tema.get("reproducible") is not False, "solo_preview": tema.get("solo_preview") is True,
    }
    try:
        return await asyncio.to_thread(_versiones_de, limpio, formato)
    except Exception as e:          # un bug acá no es un 500: la fila queda con SoundCloud
        logger.warning(f"⚠️ Versiones de «{limpio['titulo']}»: error inesperado {type(e).__name__}: {e}")
        return {"exito": True, "opciones": [dict(limpio, estacion=True)], "fallidas": [],
                "motivo": "No pude buscar versiones de este tema (error del servidor)"}


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name)[:60].strip() or "cancion"


def _descargar_directo(url: str, titulo: str) -> dict:
    """Descarga un MP3 directo (ligaudio/hitplayer) en streaming a downloads/."""
    import requests
    from scrapers import HEADERS

    nombre = _safe_name(titulo)
    destino = DOWNLOADS_DIR / f"{nombre}.mp3"
    logger.info(f"⬇️  Bajando MP3 directo: {nombre}…")

    with requests.get(url, headers=HEADERS, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        bajado = 0
        ultimo = -1
        with open(destino, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                f.write(chunk)
                bajado += len(chunk)
                if total:
                    pct = int(bajado * 100 / total)
                    if pct >= ultimo + 20:  # loguear cada ~20%
                        ultimo = pct
                        logger.info(f"⬇️  {nombre} … {pct}%")

    ok = destino.exists() and destino.stat().st_size > 0
    return {"ok": ok, "archivo": destino.name}


def _tiene_ffmpeg() -> bool:
    """Detecta ffmpeg en el PATH."""
    from shutil import which
    return which("ffmpeg") is not None


def _calidad_desde_ydl(info: dict) -> dict:
    """Calidad REAL a partir del metadato que yt-dlp ya conoce de la fuente.
    Exacto (~100%) porque es el dato verdadero del codec/bitrate de origen."""
    acodec = (info.get("acodec") or "").lower()
    abr = info.get("abr") or info.get("tbr") or 0
    ext = (info.get("ext") or "").lower()

    if "opus" in acodec:
        codec = "Opus"
    elif "aac" in acodec or "mp4a" in acodec:
        codec = "AAC"
    elif "mp3" in acodec:
        codec = "MP3"
    else:
        codec = (acodec or ext or "?").upper()

    # Opus/AAC rinden ~1.5x un MP3 al mismo bitrate → bitrate "efectivo" para el color
    eficiente = codec in ("Opus", "AAC")
    efectivo = abr * 1.5 if eficiente else abr
    if efectivo >= 256:
        badge = "🟢"
    elif efectivo >= 150:
        badge = "🟡"
    else:
        badge = "🔴"

    txt = f"{codec} ~{round(abr)}k" if abr else codec
    lossless_codec = codec.lower() in ("flac", "alac", "wav", "pcm")
    return {"badge": badge, "calidad": txt, "metodo": "metadato yt-dlp",
            "codec": codec, "abr": round(abr) if abr else None,
            "efectivo": round(efectivo) if abr else None, "lossless_codec": lossless_codec}


# Nota A/B/C/D/F + color a partir de un dict de calidad (metadato o espectral).
# La idea (roadmap v2 "Verdad de calidad"): que el DJ vea el veredicto, no que lo
# tenga que leer de un espectrograma. Colores alineados a la paleta del front.
_GRADO_COLOR = {"A": "#1ed760", "A-": "#4ade80", "B": "#a3e635", "C": "#facc15",
                "D": "#fb923c", "F": "#f87171", "?": "#9a9aac"}


def _grado(cal: dict) -> dict:
    """Devuelve {grade, color, lossy} donde grade ∈ A/A-/B/C/D/F/?.
    lossy = el ORIGEN es lossy (corte duro / bitrate bajo) → si el usuario pide
    WAV/FLAC sobre esto, es 'fake lossless' y hay que avisarlo."""
    if not cal:
        return {"grade": "?", "color": _GRADO_COLOR["?"], "lossy": False}

    ef = cal.get("efectivo")          # metadato: bitrate efectivo (Opus/AAC ×1.5)
    cut = cal.get("cutoff_hz")        # espectral: frecuencia de corte real
    muro = bool(cal.get("es_muro"))

    if cal.get("lossless_codec"):
        return {"grade": "A", "color": _GRADO_COLOR["A"], "lossy": False}

    grade, lossy = "?", False
    if ef is not None:                # camino metadato (exacto)
        if ef >= 300:   grade = "A"
        elif ef >= 256: grade = "A-"
        elif ef >= 200: grade = "B"
        elif ef >= 150: grade = "C"
        elif ef >= 96:  grade = "D"
        else:           grade = "F"
        lossy = ef < 700  # todo lo que viene de estas fuentes con abr es lossy
    elif cut:                         # camino espectral (scrapers)
        if cut >= 20000 and not muro: grade = "A"
        elif cut >= 20000:            grade = "A-"
        elif cut >= 19500:            grade = "B"
        elif cut >= 18000:            grade = "C"
        elif cut >= 15500:            grade = "D"
        elif cut >= 16000:            grade = "C"   # rampa suave (Opus) sin muro
        else:                         grade = "F"
        lossy = muro or cut < 20000
    else:                             # solo tenemos el emoji
        grade = {"🟢": "A", "🟡": "C", "🔴": "F"}.get(cal.get("badge"), "?")
        lossy = grade in ("C", "D", "F")

    return {"grade": grade, "color": _GRADO_COLOR.get(grade, _GRADO_COLOR["?"]), "lossy": lossy}


def _calidad_espectral(ruta) -> dict:
    """Calidad REAL por análisis espectral (frecuencia de corte). Para MP3 de
    scrapers sin metadato confiable. ~95% en MP3 (que cortan limpio)."""
    try:
        import analizar_calidad
        r = analizar_calidad.analizar(str(ruta))
        tipo = "muro→lossy" if r.get("es_muro") else "rampa"
        return {"badge": r["badge"], "calidad": r["calidad"],
                "metodo": f"espectral ({r['khz']:.1f} kHz, {tipo})",
                "bandas": r.get("bandas"), "es_muro": r.get("es_muro"),
                "cutoff_hz": r.get("cutoff_hz")}
    except Exception as e:
        logger.warning(f"⚠️ No pude analizar calidad espectral: {e}")
        return {"badge": "⚪", "calidad": "desconocida", "metodo": "n/d"}


def _log_calidad(c: dict) -> None:
    logger.info(f"🎚️  Calidad real: {c['badge']} {c['calidad']}  ({c['metodo']})")


def _descargar_sync(url: str, titulo: str, formato: str) -> dict:
    """Descarga una pista con yt-dlp, logueando progreso a la consola.
    Con ffmpeg: convierte al formato elegido. Sin ffmpeg: baja el audio nativo."""
    import yt_dlp

    nombre = _safe_name(titulo)
    convertir = _tiene_ffmpeg()

    def hook(d):
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "").strip()
            speed = d.get("_speed_str", "").strip()
            logger.info(f"⬇️  {nombre} … {pct} {speed}")
        elif d["status"] == "finished":
            logger.info(f"🎛️  Procesando audio de {nombre}…")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(DOWNLOADS_DIR / f"{nombre}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
    }
    if convertir:
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": formato,
            "preferredquality": "192",
        }]
    else:
        logger.warning(f"⚠️ ffmpeg no instalado: bajo el audio original (sin convertir a {formato.upper()}).")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    calidad = _calidad_desde_ydl(info)

    if convertir:
        final = DOWNLOADS_DIR / f"{nombre}.{formato}"
        return {"ok": final.exists(), "archivo": final.name, "calidad": calidad}

    # Sin conversión: el archivo conserva su extensión original
    ext = info.get("ext", "m4a")
    final = DOWNLOADS_DIR / f"{nombre}.{ext}"
    if not final.exists():
        # buscar cualquier archivo que empiece con el nombre
        candidatos = list(DOWNLOADS_DIR.glob(f"{nombre}.*"))
        if candidatos:
            final = candidatos[0]
    return {"ok": final.exists(), "archivo": final.name, "calidad": calidad}


def _taggear_descarga(archivo_name: str, titulo: str, artista: str, payload: dict, calidad: dict):
    """Escribe los tags (BPM/key/género/carátula/nota de calidad) en el archivo recién
    bajado. Usa lo que ya mandó el front; completa BPM/género con Deezer si falta."""
    import tagger

    ruta = DOWNLOADS_DIR / archivo_name
    bpm = payload.get("bpm")
    genero = payload.get("genero")
    camelot = payload.get("camelot")
    thumb = payload.get("thumbnail")
    if not bpm or not genero:
        try:
            import similares
            meta = similares.meta_de(titulo, artista, True)
            bpm = bpm or meta.get("bpm")
            genero = genero or meta.get("genero")
        except Exception:
            pass
    grade = (calidad or {}).get("grade") or "?"
    detalle = (calidad or {}).get("calidad") or ""
    comentario = f"MusiFlix · calidad {grade}" + (f" · {detalle}" if detalle else "")
    tagger.taggear(ruta, titulo=titulo, artista=artista, genero=genero, bpm=bpm,
                   camelot=camelot, cover_url=thumb, comentario=comentario)


def _hist_descarga(res: dict, titulo: str, artista: str, fuente: str, url: str,
                   formato: str, payload: dict, calidad: dict, destino: dict | None = None):
    """Registra la descarga en el historial y, si hay crate activa, la suma ahí (best-effort).

    Con `destino` ({"playlist_id", "item_id"}: la descarga se pidió desde una playlist, f41)
    el archivo va a ESE item, por id, y la activa NO se toca: si la activa es otra, se
    ensuciaría con un tema que el usuario no bajó para ella; si es la misma, la búsqueda por
    identidad aproximada de `marcar_descargado` podía agregar un duplicado. `destino` nunca
    sale del payload del cliente: lo arma el server desde el item guardado."""
    cal = calidad or {}
    archivo = res.get("archivo", "")
    ruta = str(DOWNLOADS_DIR / archivo)
    db.registrar_descarga(
        titulo=titulo, artista=artista, fuente=fuente, url=url, formato=formato,
        archivo=archivo, ruta=ruta,
        grade=cal.get("grade"), color=cal.get("color"), calidad_txt=cal.get("calidad"),
        bpm=payload.get("bpm"), camelot=payload.get("camelot"),
        genero=payload.get("genero"), thumbnail=payload.get("thumbnail"),
    )
    if destino:
        db.marcar_item_descargado(destino["playlist_id"], destino["item_id"], archivo, ruta,
                                  formato, cal.get("grade"), cal.get("color"),
                                  duracion=destino.get("duracion_medida"))
        return
    # Auto-add a la playlist/crate activa (si hay una)
    activa = db.get_playlist_activa()
    if activa:
        track = {"titulo": titulo, "artista": artista, "fuente": fuente, "url": url,
                 "thumbnail": payload.get("thumbnail"), "duracion": payload.get("duracion"),
                 "bpm": payload.get("bpm"), "camelot": payload.get("camelot"),
                 "genero": payload.get("genero")}
        db.marcar_descargado(activa["id"], track, archivo, ruta, formato,
                             cal.get("grade"), cal.get("color"))


# Tolerancia para "es el mismo tema" por duración: ±max(10 s, 10 %). Es la de
# `track_identity.evidencia_misma_grabacion` ("cuadra"): un video de YouTube del tema trae a
# veces unos segundos de intro/outro, pero 605,7 s contra 420 s (+44 %) ya es otra cosa (un
# mix, una versión extendida, otro tema), y 30 s contra 250 s es un preview.
def _duracion_cuadra(esperada: float, medida: float) -> bool:
    return abs(esperada - medida) <= max(10.0, 0.10 * esperada)


def _equivalente_youtube(resultados: list, titulo: str, artista: str, duracion) -> tuple[dict | None, str]:
    """El primer resultado de YouTube que ES el tema (mismo título, versión y artista, por
    `track_identity.is_same_track`) y cuya duración cuadra con la del tema si las dos se saben.
    Devuelve (resultado, "") o (None, motivo). Antes se bajaba el primero sin mirar: un video de
    605,7 s para un tema de 420 s quedaba "descargado"."""
    buscado = track_identity.parse_fields(artista, titulo)
    dur = track_identity.duration_or_none(duracion)
    largos = []
    for c in resultados or []:
        cand = track_identity.parse_entry(c.get("titulo") or "", c.get("artista") or "")
        if not track_identity.is_same_track(buscado, cand):
            continue
        dc = track_identity.duration_or_none(c.get("duracion"))
        if dur and dc and not _duracion_cuadra(dur, dc):
            largos.append(dc)
            continue
        return c, ""
    quien = f"«{titulo}»" + (f" de {artista}" if artista else "")
    if largos:
        return None, (f"No encontré en YouTube el mismo tema: {quien} dura {dur:.0f} s y lo más "
                      f"parecido que apareció dura {largos[0]:.1f} s.")
    return None, f"No encontré en YouTube el mismo tema ({quien}): ningún resultado tenía ese título y artista."


def _duracion_archivo(ruta: Path) -> float | None:
    """Duración (s) del archivo bajado medida con ffprobe, o None si no se pudo medir."""
    d = _duracion_audio(str(ruta), "")
    return d if d and d > 0 else None


def _verificar_bajado(archivo: str, payload: dict, destino: dict) -> dict | None:
    """Después de bajar para un item de playlist: ¿lo bajado es el tema? Compara la duración
    MEDIDA del archivo con la del item (si se sabe). Si no cuadra, borra el archivo (el nombre es
    único para esta descarga, `_nombre_libre`: no es de nadie más) y devuelve el fallo. Si
    cuadra, o el item no tenía duración, deja la medida en `destino` para completar el item.
    Si ffprobe no puede medir, no se puede verificar y se acepta (como antes de este chequeo)."""
    ruta = DOWNLOADS_DIR / archivo
    medida = _duracion_archivo(ruta)
    esperada = track_identity.duration_or_none(payload.get("duracion"))
    if medida and esperada and not _duracion_cuadra(esperada, medida):
        try:
            ruta.unlink()
        except OSError as e:
            logger.warning(f"⚠️ No pude borrar {archivo}: {e}")
        logger.error(f"❌ Lo bajado no era el tema: {archivo} dura {medida:.1f} s y el tema {esperada:.0f} s")
        return {"exito": False,
                "mensaje": f"Lo que se bajó no era el tema ({medida:.1f} s vs {esperada:.0f} s): no lo guardé."}
    destino["duracion_medida"] = medida
    return None


# Nombres que se están bajando ahora en este proceso: dos items distintos con el mismo título y
# artista que bajan a la vez no eligen el mismo nombre libre.
_nombres_reservados: set[str] = set()
_nombres_lock = threading.Lock()


def _nombre_libre(nombre: str) -> str:
    """Un nombre de archivo (sin extensión) que no pisa nada de downloads/ (C2). Dos items con
    el mismo título y artista bajaban al MISMO archivo: el segundo pisaba al primero y el .m3u8
    repetía la ruta. Si ya hay un archivo con ese nombre (cualquier extensión: yt-dlp elige la
    suya), es de otra descarga (el item que baja no tiene archivo; si lo tuviera sería 409):
    se usa «nombre (2)», «(3)»… Lo reserva hasta `_soltar_nombre`."""
    base = _safe_name(nombre)
    existentes = {p.name.lower() for p in DOWNLOADS_DIR.iterdir()} if DOWNLOADS_DIR.is_dir() else set()

    def ocupado(n: str) -> bool:
        pref = n.lower() + "."
        return n.lower() in _nombres_reservados or any(e.startswith(pref) for e in existentes)

    with _nombres_lock:
        elegido, k = base, 2
        while ocupado(elegido):
            # [:54] deja lugar al sufijo dentro de los 60 de `_safe_name` (que se vuelve a aplicar)
            elegido, k = f"{base[:54].rstrip()} ({k})", k + 1
        _nombres_reservados.add(elegido.lower())
    return elegido


def _soltar_nombre(nombre: str) -> None:
    with _nombres_lock:
        _nombres_reservados.discard(nombre.lower())


def procesar_descarga(payload: dict, destino: dict | None = None) -> dict:
    """TODO el flujo de descarga, SÍNCRONO (sin async): bajar → calidad → tags →
    historial/crate. Lo llaman el worker (vía tasks.descargar_job) y el server en
    modo local. Si es de Spotify/Deezer, busca el equivalente en YouTube.
    `destino`: el item de playlist que recibe el archivo (ver `_hist_descarga`)."""
    titulo = payload.get("titulo") or "cancion"
    artista = payload.get("artista") or ""
    fuente = payload.get("fuente") or ""
    url = payload.get("url") or ""
    formato = (payload.get("formato") or "wav").lower()
    # Sin artista, el nombre es solo el título: antes quedaba «Test -.mp3» (C8).
    nombre = f"{titulo} - {artista}" if artista.strip() else titulo
    if not destino:
        return _procesar_descarga(payload, titulo, artista, fuente, url, formato, nombre, None)
    # Desde una playlist, el archivo no puede pisar el de otra descarga (C2).
    nombre = _nombre_libre(nombre)
    try:
        return _procesar_descarga(payload, titulo, artista, fuente, url, formato, nombre, destino)
    finally:
        _soltar_nombre(nombre)


def _procesar_descarga(payload: dict, titulo: str, artista: str, fuente: str, url: str,
                       formato: str, nombre: str, destino: dict | None) -> dict:
    logger.info(f"📥 Descargando: {titulo} — {artista} [{fuente}] como {formato.upper()}")

    # Fuentes con MP3 directo: bajamos el archivo tal cual (sin yt-dlp ni ffmpeg)
    if fuente in ("ligaudio", "hitplayer") and url:
        if not _url_http(url):           # f40-r2: requests/ffmpeg solo con http(s)
            logger.error("❌ Descarga directa: la URL no es http(s).")
            return {"exito": False, "mensaje": "La URL del MP3 no es válida."}
        try:
            res = _descargar_directo(url, nombre)
        except Exception as e:
            logger.error(f"❌ Error en descarga directa: {e}")
            return {"exito": False, "mensaje": str(e)}
        if res["ok"]:
            logger.info(f"✅ Descargado: {res['archivo']}")
            if destino and (fallo := _verificar_bajado(res["archivo"], payload, destino)):
                return fallo
            calidad = _calidad_espectral(DOWNLOADS_DIR / res["archivo"])
            calidad.update(_grado(calidad))
            _log_calidad(calidad)
            _taggear_descarga(res["archivo"], titulo, artista, payload, calidad)
            _hist_descarga(res, titulo, artista, fuente, url, formato, payload, calidad, destino)
            return {"exito": True, "mensaje": f"Descargado: {res['archivo']}",
                    "archivo": res["archivo"], "calidad": calidad}
        logger.error("❌ La descarga directa no generó archivo.")
        return {"exito": False, "mensaje": "La descarga falló."}

    # Spotify (DRM) y Deezer (link no descargable): buscamos el equivalente en YouTube
    if fuente in ("spotify", "deezer") or not url:
        consulta = f"{titulo} {artista}".strip()
        logger.info(f"🔁 Fuente sin audio descargable, buscando en YouTube: '{consulta}'")
        # Varios resultados y no el primero a ciegas: se baja el primero que ES el tema (B2).
        try:
            yt = search_agent.buscar_en_youtube(consulta, 5)
        except FuenteError as e:
            logger.error(f"❌ YouTube no pudo buscar el equivalente: {e}")
            return {"exito": False, "mensaje": "YouTube no contestó: no pude buscar audio descargable."}
        if not yt:
            logger.error("❌ No encontré una versión descargable.")
            return {"exito": False, "mensaje": "No se pudo encontrar audio descargable."}
        elegido, motivo = _equivalente_youtube(yt, titulo, artista, payload.get("duracion"))
        if not elegido:
            logger.error(f"❌ {motivo}")
            return {"exito": False, "mensaje": motivo}
        url = elegido["url"]

    try:
        res = _descargar_sync(url, nombre, formato)
    except Exception as e:
        logger.error(f"❌ Error en descarga: {e}")
        msg = str(e)
        # "Falta ffmpeg" solo si de verdad falta (B6): con ffmpeg instalado, un error que lo
        # nombra (una conversión que falló, un archivo raro) se muestra tal cual.
        if ("ffmpeg" in msg.lower() or "ffprobe" in msg.lower()) and not _tiene_ffmpeg():
            msg = "Falta ffmpeg para convertir el audio. Instalá ffmpeg (ver iniciar_web.bat)."
        return {"exito": False, "mensaje": msg}

    if res["ok"]:
        logger.info(f"✅ Descargado: {res['archivo']}")
        if destino and (fallo := _verificar_bajado(res["archivo"], payload, destino)):
            return fallo
        calidad = res.get("calidad")
        if calidad:
            calidad.update(_grado(calidad))
            _log_calidad(calidad)
        _taggear_descarga(res["archivo"], titulo, artista, payload, calidad)
        _hist_descarga(res, titulo, artista, fuente, url, formato, payload, calidad, destino)
        return {"exito": True, "mensaje": f"Descargado: {res['archivo']}",
                "archivo": res["archivo"], "calidad": calidad}
    logger.error("❌ La descarga no generó archivo.")
    return {"exito": False, "mensaje": "La descarga falló."}


@app.post("/api/descargar")
async def descargar(payload: dict):
    """Encola la descarga en el worker (si hay Redis) o la corre local (fallback)."""
    if jobs.queue_disponible():
        import tasks
        job = jobs.encolar(tasks.descargar_job, payload)
        return {"encolado": True, "job_id": job.id}
    return await asyncio.to_thread(procesar_descarga, payload)


@app.get("/api/jobs/{job_id}")
async def job_estado(job_id: str):
    """Estado de un trabajo encolado: queued | started | finished | failed."""
    return await asyncio.to_thread(jobs.estado_job, job_id)


_SPEK_CACHE: dict = {}


def _audio_para_spek(titulo: str, artista: str, fuente: str, url: str):
    """Devuelve (input_para_ffmpeg, user_agent) apuntando al audio REAL que el bot
    bajaría, para poder dibujar su espectrograma sin descargar el tema entero."""
    import yt_dlp

    f = (fuente or "").lower()
    if f in ("ligaudio", "hitplayer") and url:
        if not _url_http(url):           # f40-r2: a ffmpeg solo le llega http(s)
            return None, None
        from scrapers import HEADERS
        return url, HEADERS.get("User-Agent", "")

    # Qué URL(s) intentar resolver con yt-dlp. Para Spotify/Deezer (o si no hay
    # URL directa) buscamos el audio en YouTube; pedimos VARIOS candidatos por si
    # el primero falla (p. ej. video restringido por edad).
    if url and f not in ("spotify", "deezer"):
        targets = [url]
    else:
        try:
            targets = [c["url"] for c in search_agent.buscar_en_youtube(f"{titulo} {artista}".strip(), 3)]
        except FuenteError as e:
            logger.warning(f"⚠️ Spek: YouTube no pudo buscar el audio: {e}")
            return None, None
    if not targets:
        return None, None

    # player_client 'android' esquiva el "Sign in to confirm your age" de YouTube.
    opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best",
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}}}
    for t in targets:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(t, download=False)
            audio = info.get("url")
            if audio:
                return audio, None
        except Exception as e:
            logger.warning(f"⚠️ Spek: no pude resolver el audio de {t}: {e}")
    return None, None


def _duracion_audio(audio: str, ua: str, timeout: float = 20, scraper: bool = False) -> float:
    """Duración (segundos) del stream, o 0 si no se puede medir. `timeout` (s) corta ffprobe.
    `scraper` (f40-r2): la URL viene de un sitio de MP3 (o del cliente); ffprobe solo con
    http(s) y con el whitelist de protocolos (`analizar_calidad.opciones_entrada`).

    Ojo (medido en f40-r2): con un MP3 servido por chunked (sin Content-Length), como los de
    HitPlayer, ffprobe NO da la duración ("N/A") aunque el MP3 traiga la cabecera Info: acá
    devuelve 0. Para eso está `_duracion_mp3_cabecera`."""
    import subprocess
    from analizar_calidad import FFPROBE, opciones_entrada

    cmd = [FFPROBE, "-v", "error"]
    if ua:
        cmd += ["-user_agent", ua]
    try:
        if scraper:
            if not _url_http(audio):
                return 0.0
            cmd += opciones_entrada(audio)
        cmd += ["-show_entries", "format=duration", "-of", "csv=p=0", audio]
        out = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", timeout=timeout)
        return float(out.stdout.strip().splitlines()[0])
    except Exception:
        return 0.0


def _spectrograma(titulo: str, artista: str, fuente: str, url: str):
    """PNG (bytes) de un espectrograma estilo Spek de una ventana de 30s del tema."""
    import subprocess
    from analizar_calidad import FFMPEG

    audio, ua = _audio_para_spek(titulo, artista, fuente, url)
    if not audio:
        return None

    # Ventana de 30s a partir del segundo 45. Pero algunos streams duran menos
    # (p. ej. SoundCloud sin OAuth solo entrega un preview de ~30s): si buscáramos
    # el segundo 45, ffmpeg no leería nada y no generaría imagen. Ajustamos el
    # arranque a lo que realmente dura el audio.
    ini, ventana = 45.0, 30.0
    scraper = (fuente or "").lower() in ("ligaudio", "hitplayer")
    dur = _duracion_audio(audio, ua, scraper=scraper)
    if dur and dur < ini + ventana:
        ventana = min(ventana, max(10.0, dur - 1))
        ini = max(0.0, dur - ventana - 1)

    from analizar_calidad import opciones_entrada
    cmd = [FFMPEG, "-hide_banner", "-nostats"]
    if ua:
        cmd += ["-user_agent", ua]
    cmd += [
        "-ss", str(ini), "-t", str(ventana),
        *(opciones_entrada(audio) if scraper else []),     # f40-r2: URL de un scraper, solo http(s)
        "-i", audio,
        # Espectrograma con eje de frecuencia LINEAL (como Spek) y leyenda visible
        "-lavfi", "showspectrumpic=s=760x340:legend=1:fscale=lin:color=intensity:gain=3:saturation=1",
        "-frames:v", "1", "-c:v", "png", "-f", "image2pipe", "-",
    ]
    out = subprocess.run(cmd, capture_output=True)
    if out.returncode != 0 or not out.stdout:
        logger.warning(f"⚠️ Spek: ffmpeg no generó imagen (rc={out.returncode}).")
        return None
    return out.stdout


@app.get("/api/spectro")
async def spectro(titulo: str, artista: str = "", fuente: str = "", url: str = ""):
    """Espectrograma (PNG) estilo Spek del audio real del tema. Cacheado."""
    clave = f"{fuente}|{url}|{titulo}|{artista}"
    png = _SPEK_CACHE.get(clave)
    if png is None:
        logger.info(f"📊 Generando espectrograma (Spek) de: {titulo} — {artista}")
        if jobs.queue_disponible():
            import tasks
            job = jobs.encolar(tasks.spectro_job, titulo, artista, fuente, url, timeout=120)
            png = await asyncio.to_thread(jobs.esperar_resultado, job, 90)
        else:
            png = await asyncio.to_thread(_spectrograma, titulo, artista, fuente, url)
        if png:
            _SPEK_CACHE[clave] = png
    if not png:
        return JSONResponse({"exito": False, "mensaje": "No pude generar el espectrograma."}, status_code=502)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


_CALIDAD_CACHE: dict = {}


def _calidad_preview(titulo: str, artista: str, fuente: str, url: str):
    """Calidad REAL del tema SIN descargarlo entero (para el badge de las cards).
    - Scrapers (MP3 directo): análisis espectral de una ventana leída por streaming.
    - YouTube/SoundCloud/Spotify/Deezer: metadato de yt-dlp (rápido y exacto)."""
    f = (fuente or "").lower()

    if f in ("ligaudio", "hitplayer") and url:
        if not _url_http(url):           # f40-r2: a ffprobe/ffmpeg solo les llega http(s)
            return None
        from scrapers import HEADERS
        ua = HEADERS.get("User-Agent", "")
        dur = _duracion_audio(url, ua, scraper=True)
        ss, ventana = 45.0, 30.0
        if dur and dur < ss + ventana:
            ventana = min(ventana, max(10.0, dur - 1))
            ss = max(0.0, dur - ventana - 1)
        try:
            import analizar_calidad
            r = analizar_calidad.analizar(url, ss=ss, dur=ventana, ua=ua)
            cal = {"badge": r["badge"], "calidad": r["calidad"],
                   "metodo": f"espectral ({r['khz']:.1f} kHz)",
                   "es_muro": r.get("es_muro"), "cutoff_hz": r.get("cutoff_hz")}
        except Exception as e:
            logger.warning(f"⚠️ Calidad: análisis espectral falló: {e}")
            return None
    else:
        import yt_dlp
        if url and f not in ("spotify", "deezer"):
            targets = [url]
        else:
            try:
                targets = [c["url"] for c in search_agent.buscar_en_youtube(f"{titulo} {artista}".strip(), 3)]
            except FuenteError as e:
                logger.warning(f"⚠️ Calidad: YouTube no pudo buscar el audio: {e}")
                return None
        if not targets:
            return None
        opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best",
                "extractor_args": {"youtube": {"player_client": ["android", "web"]}}}
        info = None
        for t in targets:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(t, download=False)
                break
            except Exception as e:
                logger.warning(f"⚠️ Calidad: no pude resolver {t}: {e}")
        if not info:
            return None
        cal = _calidad_desde_ydl(info)

    cal.update(_grado(cal))
    return cal


def _calidad_cacheada(titulo: str, artista: str, fuente: str, url: str, espera: float = 90) -> dict:
    """La respuesta de /api/calidad (bloqueante): misma caché, misma cola. La usan el
    endpoint y las versiones de la Station (f36), así una nota no se calcula dos veces.
    `espera` (s): cuánto se espera al worker cuando hay Redis (versiones pasa su tope)."""
    clave = f"{fuente}|{url}|{titulo}|{artista}"
    cal = _CALIDAD_CACHE.get(clave)
    if cal is None:
        if jobs.queue_disponible():
            import tasks
            job = jobs.encolar(tasks.calidad_job, titulo, artista, fuente, url, timeout=120)
            cal = jobs.esperar_resultado(job, espera)
        else:
            cal = _calidad_preview(titulo, artista, fuente, url)
        if cal:
            _CALIDAD_CACHE[clave] = cal
    if not cal:
        return {"ok": False, "grade": "?", "color": _GRADO_COLOR["?"], "calidad": "no analizable"}
    return {"ok": True, **cal}


@app.get("/api/calidad")
async def calidad(titulo: str, artista: str = "", fuente: str = "", url: str = ""):
    """Nota de calidad (A/B/C/D/F) del audio real, ANTES de descargar. Cacheada."""
    return await asyncio.to_thread(_calidad_cacheada, titulo, artista, fuente, url)


@app.get("/api/descargas")
async def listar_descargas():
    archivos = [
        {"nombre": f.name, "mb": round(f.stat().st_size / (1024 * 1024), 2)}
        for f in DOWNLOADS_DIR.glob("*") if f.is_file()
    ]
    return {"exito": True, "total": len(archivos), "archivos": archivos}


# --- Biblioteca local (colección analizada): estantes por género + audio para el preview ---
# Rutas por ENTORNO, sin hardcodear (#5.16): MUSIFLIX_LIBRARY_XML (el XML de Rekordbox) y
# MUSIFLIX_LIBRARY_ROOTS (carpetas de audio separadas por os.pathsep). Sin config → vacía.
_LIB_XML = os.getenv("MUSIFLIX_LIBRARY_XML", "")
_LIB_ROOTS = [r for r in os.getenv("MUSIFLIX_LIBRARY_ROOTS", "").split(os.pathsep) if r]
_lib_audio: dict[str, str] = {}          # id de track → ruta real (para /api/audio)

# Estado de la última carga. "sin-configurar" y "sin-lector" cuentan como NO configurada
# (no hay nada que el usuario pueda arreglar tocando rutas); los demás sí lo están.
_LIB_OK, _LIB_SIN_CONFIG, _LIB_SIN_LECTOR, _LIB_XML_ILEGIBLE = (
    "ok", "sin-configurar", "sin-lector", "xml-ilegible")
_LIB_MOTIVOS = {
    _LIB_SIN_LECTOR: "Esta instalación no incluye el lector de la biblioteca (ground_truth), "
                     "así que la biblioteca local está desactivada.",
    _LIB_XML_ILEGIBLE: "No pude leer el XML de Rekordbox. Revisá MUSIFLIX_LIBRARY_XML.",
}


def _cargar_biblioteca() -> tuple[list[dict], str]:
    """Lee el XML de Rekordbox y resuelve cada track a su archivo. Devuelve (tracks, estado):
    solo los tracks que tienen audio (para poder escucharlos), con género/BPM/tonalidad, y
    el estado de la carga (_LIB_*). Cachea id→ruta."""
    global _lib_audio
    if not _LIB_XML or not _LIB_ROOTS:
        return [], _LIB_SIN_CONFIG
    # ground_truth/ no entra en la imagen Docker (el Dockerfile copia solo los *.py de la
    # raíz): sin el lector, degradar como "sin configurar" en vez de tirar un 500.
    try:
        from ground_truth.rekordbox import parsear
        from ground_truth.resolver import construir_indice, resolver
    except ImportError as e:
        logger.warning(f"⚠️ Biblioteca: falta el lector de ground_truth ({e}); queda desactivada.")
        return [], _LIB_SIN_LECTOR
    try:
        tracks = parsear(Path(_LIB_XML))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"⚠️ Biblioteca: no pude leer el XML: {e}")
        return [], _LIB_XML_ILEGIBLE
    indice = construir_indice(_LIB_ROOTS)
    audio, out = {}, []
    for t in tracks:
        r = resolver(t["location"], _LIB_ROOTS, indice)
        if not r:                        # sin audio no se puede escuchar → fuera del browse
            continue
        tid = str(t["track_id"]) or str(len(out))
        audio[tid] = r.ruta
        out.append({
            "id": tid, "titulo": t["name"] or Path(r.ruta).stem, "artista": t["artist"] or "",
            "bpm": round(t["bpm"], 1) if t["bpm"] else None,
            "camelot": t["camelot"] or None, "tonalidad": t["tonality"] or None,
            "genero": (t["genre"] or "").strip() or "Sin género", "dur": t["duration_s"] or 0,
            # Contenedor real del archivo (la extensión del que se resolvió), para que el
            # reproductor diga qué suena. Sin extensión → None, no un formato adivinado.
            "formato": Path(r.ruta).suffix.lstrip(".").lower() or None,
        })
    _lib_audio = audio
    return out, _LIB_OK


@app.get("/api/biblioteca")
async def biblioteca():
    """Estantes de la biblioteca local agrupados por género (para la home).
    `motivo` explica por qué está vacía cuando no es solo falta de configuración."""
    from collections import defaultdict
    tracks, estado = await asyncio.to_thread(_cargar_biblioteca)
    por_genero: dict[str, list] = defaultdict(list)
    for t in tracks:
        por_genero[t["genero"]].append(t)
    generos = [{"genero": g, "tracks": ts} for g, ts in por_genero.items()]
    generos.sort(key=lambda s: -len(s["tracks"]))     # los géneros con más temas primero
    return {"total": len(tracks), "configurada": estado not in (_LIB_SIN_CONFIG, _LIB_SIN_LECTOR),
            "motivo": _LIB_MOTIVOS.get(estado), "generos": generos}


@app.get("/api/audio/{track_id}")
async def audio(track_id: str):
    """Sirve el archivo de un track de la biblioteca para el preview. Solo lee, nunca escribe."""
    if not _lib_audio:
        await asyncio.to_thread(_cargar_biblioteca)
    ruta = _lib_audio.get(track_id)
    if not ruta or not Path(ruta).exists():
        return JSONResponse({"error": "track no encontrado"}, status_code=404)
    return FileResponse(ruta)             # FileResponse maneja Range → el <audio> puede buscar


# Audio de YouTube/SoundCloud para la barra, sin video (f32): /api/fuente/audio[/info].
# Todo el detalle (proxy con Range, validación anti-SSRF, cache) está en source_audio.py.
import source_audio  # noqa: E402

app.include_router(source_audio.router)


# Motivos de /api/cover cuando no hay imagen que servir. El front dibuja el placeholder en
# todos los casos; el motivo es para quien mire la respuesta (y los tests).
_SIN_CARATULA = "el archivo no trae carátula"
_CARATULA_GRANDE = "carátula demasiado grande"
_CARATULA_FORMATO = "la carátula no es JPEG, PNG, GIF ni WebP"
_ARCHIVO_ILEGIBLE = "no pude leer el archivo"
_SIN_MUTAGEN = "esta instalación no tiene mutagen: no se pueden leer carátulas"
# Tope de lo que se devuelve: una tapa normal pesa < 1 MB. Un APIC de 40 MB iba entero a
# memoria y a la red (medido en la auditoría de f28).
_CARATULA_MAX_BYTES = 10 * 1024 * 1024
# Cuántos archivos se abren a la vez: la home pide ~18 carátulas juntas.
_caratulas_sem = asyncio.Semaphore(4)
_aviso_sin_mutagen = False


def _cabeceras_plausibles(ruta: str) -> bool:
    """¿Los tamaños que DECLARA el archivo entran en el archivo? Se mira antes de mutagen.

    Medido en la auditoría de f28: un MP3 de 3 KB con la cabecera ID3 corrupta (tamaño
    declarado de 256 MB) hacía que mutagen pidiera `read(256 MB)`, y Python reserva el búfer
    entero antes de leer. Se revisan las dos cabeceras que mutagen lee de una: la ID3 del
    principio (MP3) y los chunks de RIFF/FORM (WAV/AIFF, donde vive el chunk 'id3 ').
    FLAC no lo necesita (sus bloques declaran 24 bits: ≤ 16 MB). MP4/M4A no se revisa acá:
    sus átomos se leen de a partes y no se midió el mismo problema."""
    try:
        total = os.path.getsize(ruta)
        with open(ruta, "rb") as f:
            cab = f.read(12)
            if cab[:3] == b"ID3" and len(cab) >= 10:
                b = cab[6:10]
                if any(x & 0x80 for x in b):             # syncsafe: el bit alto va en 0
                    return False
                tam = (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]
                return 10 + tam <= total
            if cab[:4] in (b"RIFF", b"FORM"):
                orden = "little" if cab[:4] == b"RIFF" else "big"
                pos = 12
                for _ in range(10_000):                  # tope de chunks: un archivo real tiene pocos
                    if pos + 8 > total:
                        return True
                    f.seek(pos)
                    h = f.read(8)
                    tam = int.from_bytes(h[4:8], orden)
                    if pos + 8 + tam > total:
                        return False
                    pos += 8 + tam + (tam & 1)           # los chunks se alinean a 2 bytes
                return False
        return True
    except OSError:
        return False


def _caratula_embebida(ruta: str) -> tuple[bytes, str] | str:
    """Carátula embebida en el archivo de audio: APIC de ID3 (MP3, y el chunk ID3 de WAV y
    AIFF), PICTURE de FLAC o `covr` de MP4/M4A. Prefiere la de tipo 3 (tapa). Solo lee: el
    archivo original no se toca.

    Devuelve (bytes, mime) o el MOTIVO por el que no hay nada que servir. El MIME sale SOLO
    del contenido (tagger.mime_por_contenido: JPEG/PNG/GIF/WebP), nunca del tag: un APIC
    "image/svg+xml" con <script> ejecutaba en el origen de la app (auditoría de f28).
    No se leen carátulas de OGG/Opus (METADATA_BLOCK_PICTURE en base64) ni de APEv2: esos
    archivos responden "no trae carátula" aunque tengan una."""
    global _aviso_sin_mutagen
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        if not _aviso_sin_mutagen:
            logger.warning("⚠️ Carátulas: falta mutagen; /api/cover no puede leer ninguna.")
            _aviso_sin_mutagen = True
        return _SIN_MUTAGEN
    from tagger import mime_por_contenido
    if not _cabeceras_plausibles(ruta):
        logger.debug(f"Carátula: cabeceras que no entran en el archivo, no se parsea {ruta}")
        return _ARCHIVO_ILEGIBLE
    try:
        audio = MutagenFile(ruta)
    except Exception as e:  # noqa: BLE001 — archivo raro o ilegible: sin carátula, no un 500
        logger.debug(f"Carátula: no pude leer {ruta}: {e}")
        return _ARCHIVO_ILEGIBLE
    if audio is None:
        return _SIN_CARATULA
    candidatos: list[tuple[int, bytes, str | None]] = []   # (tipo, datos, MIME declarado)
    for p in getattr(audio, "pictures", None) or []:       # FLAC
        candidatos.append((p.type, p.data, p.mime))
    tags = audio.tags
    if tags is not None:
        if hasattr(tags, "getall"):                          # ID3
            candidatos += [(a.type, a.data, a.mime) for a in tags.getall("APIC")]
        else:
            try:
                covr = tags.get("covr")                      # MP4
            except Exception:  # noqa: BLE001
                covr = None
            candidatos += [(3, bytes(c), None) for c in covr or []]
    candidatos.sort(key=lambda c: c[0] != 3)                 # la tapa primero
    motivo = _SIN_CARATULA
    for _, data, _declarado in candidatos:
        if not data:
            continue
        if len(data) > _CARATULA_MAX_BYTES:
            motivo = _CARATULA_GRANDE
            continue
        mime = mime_por_contenido(bytes(data))              # el declarado NO se usa
        if not mime:
            if motivo == _SIN_CARATULA:
                motivo = _CARATULA_FORMATO
            continue
        return bytes(data), mime
    return motivo


# La respuesta es una imagen y nada más: sin sniffing del navegador y, si algo se colara
# igual, sin permiso para ejecutar ni cargar nada (CSP con sandbox).
_CABECERAS_CARATULA = {
    "Cache-Control": "private, max-age=3600",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


@app.get("/api/cover/{track_id}")
async def caratula(track_id: str, request: Request):
    """Carátula EMBEBIDA en el archivo de un track de la biblioteca (el XML de Rekordbox no
    trae imágenes, así que la home no mostraba ninguna). 404 con motivo cuando el track no
    existe o no hay imagen que servir: el front dibuja su placeholder, nunca una imagen
    inventada. Mismo criterio que /api/audio: el id no es una ruta.

    `Last-Modified` = mtime del archivo: re-taggear un tema cambia la tapa al recargar (con
    solo max-age se veía la vieja una hora). `If-Modified-Since` igual o posterior → 304."""
    if not _lib_audio:
        await asyncio.to_thread(_cargar_biblioteca)
    ruta = _lib_audio.get(track_id)
    if not ruta or not Path(ruta).exists():
        return JSONResponse({"error": "track no encontrado"}, status_code=404)
    mtime = int(os.path.getmtime(ruta))
    cabeceras = {**_CABECERAS_CARATULA, "Last-Modified": formatdate(mtime, usegmt=True)}
    ims = request.headers.get("if-modified-since")
    if ims:
        try:
            if int(parsedate_to_datetime(ims).timestamp()) >= mtime:
                return Response(status_code=304, headers=cabeceras)
        except (TypeError, ValueError):
            pass                                          # fecha ilegible: se sirve entera
    async with _caratulas_sem:
        art = await asyncio.to_thread(_caratula_embebida, ruta)
    if isinstance(art, str):
        return JSONResponse({"error": art}, status_code=404)
    data, mime = art
    return Response(content=data, media_type=mime, headers=cabeceras)


# --- Radio DJ (motor/): biblioteca del motor, set armado por `motor.radio.build_set` y el
#     audio de cada paso --------------------------------------------------------------
# La biblioteca de acá NO es la de arriba: aquella sale del XML de Rekordbox
# (MUSIFLIX_LIBRARY_*) y esta de la base SQLite del motor, que es la que tiene embeddings,
# energía en percentil y la confianza de la key. Son dos colecciones distintas, con otras
# rutas y otros ids, así que la radio tiene sus propios endpoints y no reusa /api/audio.
#
# Config por ENTORNO, sin rutas hardcodeadas (#5.16): la base sale de `DJRADIO_DB` y, si no
# está, de `~/.djradio/biblioteca.sqlite` — vía `motor.cli.db_por_defecto`, o sea la MISMA
# regla que la CLI. Una segunda variable propia del server haría que `python -m motor radio`
# y la pantalla web pudieran mirar bases distintas sin que nadie se entere.
#
# Se lee en cada pedido y no al importar (a diferencia de _LIB_XML): así un scan nuevo se ve
# sin reiniciar el server, y en Docker la variable la fija docker-compose.yml.
#
# Sin el paquete `motor`, sin base, con la base vacía o de otro esquema: 200 con
# `configurada: false`/`motivo`, como /api/biblioteca. La radio es una pantalla más: no
# puede tirar un 500 ni, peor, devolver un set inventado.
_RADIO_OK, _RADIO_SIN_MOTOR, _RADIO_SIN_BASE = "ok", "sin-motor", "sin-base"
_RADIO_VACIA, _RADIO_ESQUEMA, _RADIO_ILEGIBLE = "base-vacia", "esquema-incompatible", "base-ilegible"
_RADIO_OCUPADA = "base-ocupada"

# Cuánto espera la API a que otro proceso suelte la base. La CLI espera
# `store.ESPERA_BLOQUEO_S` (30 s) porque ahí esperar a que termine un scan es lo correcto;
# una pantalla no puede quedarse congelada medio minuto por un GET. Se prefiere contestar
# "la base está ocupada, reintentá" enseguida (hallazgo BAJA-1 de la auditoría).
_RADIO_ESPERA_S = 2.0

# Cada cuánto, como mucho, un id desconocido puede hacer recargar la biblioteca entera.
# Sin esto, pedir ids inventados en loop hace un `load_library()` por pedido.
_RADIO_RECARGA_MIN_S = 5.0

# Cuáles cuentan como NO configurada: los dos casos en los que no hay nada que el usuario
# haya apuntado todavía. Con la base vacía o ilegible SÍ configuró algo, y el motivo dice qué
# le pasa (mismo criterio que `_LIB_SIN_CONFIG` / `_LIB_XML_ILEGIBLE` arriba).
_RADIO_SIN_CONFIGURAR = (_RADIO_SIN_MOTOR, _RADIO_SIN_BASE)

_radio_audio: dict[str, str] = {}        # id opaco → ruta real (para /api/radio/audio)
_radio_recarga_ts: float = 0.0           # cuándo se recargó por última vez (ver _RADIO_RECARGA_MIN_S)


def _radio_id(path) -> str:
    """Id opaco y estable de un track del motor.

    Hash de la ruta canónica y NO la ruta: el id viaja en la URL de /api/radio/audio, y un
    id que fuera una ruta convertiría ese endpoint en un lector de archivos arbitrarios. Es
    estable entre reinicios (mismo archivo → mismo id) para que un enlace no se pudra, y
    `normcase` lo hace insensible a mayúsculas como el resto del motor en Windows.
    """
    return hashlib.sha1(os.path.normcase(str(path)).encode("utf-8")).hexdigest()[:16]


def _num(valor) -> float | None:
    """float listo para JSON, o `None` si no es finito.

    Dos motivos, y los dos importan: `json.dumps` escribe `NaN`/`Infinity`, que son JSON
    inválido y hacen explotar a `JSON.parse` en el front; y §6 pide un dato ausente antes
    que uno que miente — `bpm_delta_pct` es NaN justamente cuando el BPM no se pudo medir.
    """
    if valor is None:
        return None
    v = float(valor)
    return v if math.isfinite(v) else None


def _usar_store_motor(accion) -> tuple[object, str, str | None]:
    """`(accion(store), estado, motivo)` sobre la base del motor. Nunca levanta por la base.

    Una sola función para abrir la base y clasificar lo que puede salir mal (sin motor, sin
    base, esquema de otro código, ocupada, ilegible): la usan la biblioteca de la radio y los
    sets guardados, y dos copias de esta clasificación terminarían diciendo "ilegible" en una
    pantalla y "ocupada" en otra para la misma base. Con `estado != ok` el resultado es None.

    Lo que SÍ levanta son los errores del pedido (`InvalidSavedSet`, `SavedSetNotFound`):
    no son de la base, y cada endpoint los contesta con su 400 / 404.
    """
    try:
        from motor.cli import db_por_defecto, esta_bloqueada
        from motor.cue_marks import CueMarkNotFound, InvalidCueMark
        from motor.saved_sets import InvalidSavedSet, SavedSetNotFound
        from motor.store import EsquemaIncompatible, Store
    except ImportError as e:
        logger.warning(f"⚠️ Radio: falta el paquete motor ({e}); la radio queda desactivada.")
        return None, _RADIO_SIN_MOTOR, (
            "Esta instalación no incluye el motor de radio (motor/), así que la radio está "
            "desactivada.")

    db = db_por_defecto()
    if not db.exists():
        return None, _RADIO_SIN_BASE, (
            f"No hay biblioteca del motor en {db}. Analizá una carpeta con "
            f"`python -m motor scan <carpeta>`, o apuntá "
            f"DJRADIO_DB a la base que ya tengas.")
    try:
        # Espera corta, no la de la CLI: ver `_RADIO_ESPERA_S`.
        with Store(db, espera_bloqueo_s=_RADIO_ESPERA_S) as store:
            return accion(store), _RADIO_OK, None
    except (InvalidSavedSet, SavedSetNotFound, InvalidCueMark, CueMarkNotFound):
        # `InvalidCueMark` es un ValueError: sin esta línea caía abajo como "base ilegible".
        raise
    except EsquemaIncompatible as e:
        return None, _RADIO_ESQUEMA, str(e)
    except sqlite3.OperationalError as e:
        # "Ocupada" se separa de "ilegible" con la MISMA condición que la CLI
        # (`cli.esta_bloqueada`): no es una base rota, es que hay un scan corriendo, y se
        # arregla esperando. Con el motivo crudo de SQLite ("database is locked") en
        # pantalla, el DJ no tiene forma de saber que lo único que tiene que hacer es
        # reintentar en un rato.
        if not esta_bloqueada(e):
            logger.warning(f"⚠️ Radio: no pude leer la base del motor {db}: {e}")
            return None, _RADIO_ILEGIBLE, f"No pude leer la biblioteca del motor ({db}): {e}"
        return None, _RADIO_OCUPADA, (
            f"La biblioteca del motor ({db}) está ocupada: hay un scan u otra instancia "
            f"usándola (se esperó {_RADIO_ESPERA_S:g} s). Reintentá en un rato.")
    except (sqlite3.Error, ValueError) as e:
        # sqlite3.Error: la base no es SQLite o está corrupta.
        # ValueError: una fila con un BPM infinito (o una licencia que no es texto) — `Track` la rechaza
        # al construirla y se lleva puesta la biblioteca entera. Ninguna de las dos es un bug
        # del server, y las dos son arreglables sabiendo qué base se está leyendo.
        logger.warning(f"⚠️ Radio: no pude leer la base del motor {db}: {e}")
        return None, _RADIO_ILEGIBLE, f"No pude leer la biblioteca del motor ({db}): {e}"


def _leer_biblioteca_motor() -> tuple[list, str, str | None]:
    """`(tracks, estado, motivo)` de la biblioteca del motor. Nunca levanta."""
    biblioteca, estado, motivo = _usar_store_motor(lambda store: store.load_library())
    if estado != _RADIO_OK:
        return [], estado, motivo
    if not biblioteca:
        from motor.cli import db_por_defecto

        db = db_por_defecto()
        return [], _RADIO_VACIA, (
            f"La biblioteca del motor ({db}) no tiene ningún track analizado. Corré "
            f"`python -m motor scan <carpeta>`.")
    return biblioteca, _RADIO_OK, None


def _cargar_radio() -> tuple[list, str, str | None]:
    """Igual que `_leer_biblioteca_motor`, y además deja el índice id→ruta al día.

    El índice se REEMPLAZA siempre, también cuando la carga falla: si quedara el de la
    última carga buena, /api/radio/audio seguiría sirviendo archivos de una base que ya no
    es la configurada.
    """
    global _radio_audio, _radio_recarga_ts
    tracks, estado, motivo = _leer_biblioteca_motor()
    _radio_audio = {_radio_id(t.path): str(t.path) for t in tracks}
    _radio_recarga_ts = time.monotonic()
    return tracks, estado, motivo


def _radio_track(t) -> dict:
    """Un track del motor como lo muestra la CLI (§6): BPM con UN decimal, las dos
    notaciones de key, el `?` de confianza y la energía.

    El `?` sale de `motor.cli.key_dudosa`, el percentil de energía de
    `motor.cli.percentil_energia` y la clásica de `motor.tonalidad.camelot_a_clasica`: las
    mismas funciones que la terminal, no una copia de la regla. `es_track` viaja para que
    el front pueda avisar antes de pedir el set que ese archivo no sirve de semilla.
    """
    from motor.cli import key_dudosa, percentil_energia
    from motor.modelos import es_track
    from motor.tonalidad import camelot_a_clasica

    clasica = camelot_a_clasica(t.key)
    tid = _radio_id(t.path)
    return {
        "id": tid,
        "label": t.label,
        "titulo": t.title or t.path.stem,
        "artista": t.artist or "",
        "bpm": round(float(t.bpm), 1),
        "camelot": t.key or None,
        # "" = la key no es un código Camelot (silencio, "?"): dato ausente, no un "?" dibujado.
        "tonalidad": clasica or None,
        "key_dudosa": key_dudosa(t.key_acuerdo),
        "energia": round(float(t.energy), 3),      # percentil 0..1 dentro de la biblioteca
        # El mismo percentil, 0-100 y redondeado por el motor: es EXACTAMENTE el número que
        # imprimen `list`, `info` y `radio` en la terminal. Va calculado desde acá y no en el
        # front porque redondear del otro lado son dos redondeos para un solo dato (§6).
        "energia_pct": percentil_energia(t.energy),
        "dur": round(float(t.duration), 1),
        "es_track": es_track(t.duration),
        "audio": f"/api/radio/audio/{tid}",
    }


def _radio_paso(n: int, paso) -> dict:
    """Un paso del set: el track, el POR QUÉ tal cual lo escribe el motor y los números
    con los que está hecho.

    `motivo` es `Transition.reason()` sin tocar. Recalcular el porcentaje acá es
    exactamente lo que el módulo de la radio prohíbe en su punto 2: la pantalla terminaría
    diciendo "+7.9%" sobre una transición que la compuerta midió en 8.1%.
    """
    tr = paso.transition
    return {
        "n": n,
        "track": _radio_track(paso.track),
        "motivo": tr.reason(),
        "es_semilla": tr.is_seed,
        "transicion": {
            "from_bpm": _num(tr.from_bpm), "to_bpm": _num(tr.to_bpm),
            "bpm_delta_pct": _num(tr.bpm_delta_pct), "bpm_octava": tr.bpm_octave,
            "from_key": tr.from_key, "to_key": tr.to_key,
            "key_relacion": tr.key_relation, "key_compat": _num(tr.key_compat),
            "mezclabilidad": _num(tr.mixability), "encaje_musical": _num(tr.musical_fit),
            "score": _num(tr.total),
            "energia": _num(tr.energy), "energia_objetivo": _num(tr.energy_goal),
        },
    }


def _radio_envoltura(estado: str, motivo: str | None) -> dict:
    """Los campos de estado que llevan TODAS las respuestas de la radio, configurada o no,
    para que el front tenga una sola forma que leer."""
    return {"configurada": estado not in _RADIO_SIN_CONFIGURAR, "estado": estado,
            "motivo": motivo}


def _radio_config(config) -> dict:
    """Un `RadioConfig` como lo lee la pantalla.

    Una sola función para los dos lugares que lo devuelven —el que se usó en
    /api/radio/set y los de fábrica en /api/radio/biblioteca—: dos copias de esta lista
    de campos se desincronizan en silencio, que es lo mismo que evita no escribir los
    defaults en la firma del endpoint.
    """
    return {"largo": config.length, "curva": config.curve, "artist_gap": config.artist_gap,
            "mmr_lambda": config.mmr_lambda, "semilla": config.seed,
            "randomness": config.randomness}


def _radio_opciones() -> dict | None:
    """Lo que la pantalla necesita del motor y no es un track: las curvas que acepta, los
    valores de fábrica de `RadioConfig` y la leyenda del `?`.

    Va acá por el mismo motivo por el que /api/radio/set no escribe los defaults en su
    firma: un front con `['peak','warmup','flat']` y `largo: 20` escritos a mano es un
    segundo juego de defaults que se desincroniza sin que nadie se entere, y una curva que
    el motor ya no acepta se descubriría recién cuando el set vuelve 400. La leyenda viaja
    con esto porque el `?` de la confianza también se dibuja en la lista para elegir la
    semilla, y un `?` sin explicación al lado es un símbolo mudo (§6).

    `None` si no está el paquete motor: ahí no hay radio que configurar.
    """
    try:
        from motor.cli import LEYENDA_KEY
        from motor.energia import CURVES
        from motor.radio import RadioConfig
    except ImportError:
        return None
    return {"curvas": list(CURVES), "config_default": _radio_config(RadioConfig()),
            "leyenda_key": LEYENDA_KEY}


@app.get("/api/radio/biblioteca")
async def radio_biblioteca():
    """La biblioteca del motor, para elegir la semilla del set.

    Trae TODO lo que hay en la base, también lo que dura menos de 90 s (marcado con
    `es_track: false`): es lo mismo que hace `python -m motor list`, y esconderlo haría que
    el DJ no encuentre un archivo que sabe que escaneó. Lo que no puede es ser semilla.
    """
    tracks, estado, motivo = await asyncio.to_thread(_cargar_radio)
    return {**_radio_envoltura(estado, motivo), "total": len(tracks),
            "tracks": [_radio_track(t) for t in tracks],
            # Sin el paquete motor no hay curvas ni defaults que ofrecer: null, no un
            # juego inventado (el `estado` ya dice por qué).
            "opciones": _radio_opciones() if estado != _RADIO_SIN_MOTOR else None}


@dataclass(frozen=True)
class _SetArmado:
    """Lo que devuelve `_armar_set_radio` cuando el pedido no fue inválido.

    Con `estado != ok` (sin base, base ocupada...) `config`, `elegida` y `rset` son None:
    no hay set, y cada endpoint degrada a su manera (ver `radio_set` y `radio_set_m3u8`).
    """
    estado: str
    motivo: str | None
    config: object = None
    elegida: object = None
    rset: object = None


async def _armar_set_radio(track: str, largo, curva, artist_gap, mmr_lambda, semilla,
                           randomness) -> "_SetArmado | JSONResponse":
    """Arma el set que piden /api/radio/set y /api/radio/set.m3u8, o el 400 que corresponda.

    UNA sola función para los dos endpoints a propósito: el .m3u8 tiene que ser el MISMO
    set que la pantalla acaba de mostrar, y dos copias de esta lógica (defaults, cómo se
    resuelve la semilla, qué se rechaza) se desincronizan en silencio — el día que una
    cambie, el archivo que se lleva el DJ sería otro set que el que escuchó.
    """
    tracks, estado, motivo = await asyncio.to_thread(_cargar_radio)
    if estado != _RADIO_OK:
        return _SetArmado(estado, motivo)

    from motor.cli import ErrorDeUso, motivo_semilla_no_track, resolver_track
    from motor.modelos import es_track
    from motor.radio import RadioConfig, build_set

    pedidos = {"length": largo, "curve": curva, "artist_gap": artist_gap,
               "mmr_lambda": mmr_lambda, "seed": semilla, "randomness": randomness}
    try:
        config = RadioConfig(**{k: v for k, v in pedidos.items() if v is not None})
    except ValueError as e:      # curva inexistente, largo 0, randomness fuera de 0..1...
        return JSONResponse({"error": str(e)}, status_code=400)

    if not track.strip():
        return JSONResponse(
            {"error": "Falta la semilla: pasá `track` con el id, la ruta o un fragmento del "
                      "nombre (los ids salen de /api/radio/biblioteca)."}, status_code=400)
    por_id = {_radio_id(t.path): t for t in tracks}
    try:
        # El id primero: es lo que manda el front. Si no es un id, se resuelve con la MISMA
        # función que la CLI, que acepta ruta o fragmento y se niega a elegir entre homónimos
        # — pero con `consultar_disco=False`, porque `track` viene de afuera. Con el default,
        # `resolver_track` le hace `is_file()` a la cadena del cliente: el endpoint no tiene
        # CORS y escucha en localhost, así que cualquier página abierta en el navegador podía
        # usarlo para saber qué archivos existen en la máquina (y, con una ruta UNC, para
        # provocar un intento SMB saliente). Todo lo que la API mira es la biblioteca que ya
        # está cargada en memoria.
        elegida = por_id.get(track) or resolver_track(track, tracks, consultar_disco=False)
    except ErrorDeUso as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    # Una semilla que no es un track se rechaza, igual que en la CLI y por el mismo motivo:
    # todo el set se arma contra su BPM y su key, y en 10 s de audio esos dos valores no son
    # una medición. El texto sale del motor (`motivo_semilla_no_track`), no de acá.
    if not es_track(elegida.duration):
        return JSONResponse({"error": motivo_semilla_no_track(elegida),
                             "semilla": _radio_track(elegida)}, status_code=400)

    rset = await asyncio.to_thread(build_set, elegida, tracks, config)
    return _SetArmado(estado, motivo, config, elegida, rset)


@app.get("/api/radio/set")
async def radio_set(track: str = "", largo: int | None = None, curva: str | None = None,
                    artist_gap: int | None = None, mmr_lambda: float | None = None,
                    semilla: int | None = None, randomness: float | None = None):
    """Arma un set desde `track` con `motor.radio.build_set`.

    OJO con los dos sentidos de "semilla", que son los mismos que en la CLI: `track` es el
    track semilla (id de /api/radio/biblioteca, ruta o fragmento del nombre) y `semilla` es
    la semilla del GENERADOR ALEATORIO (`RadioConfig.seed`), que solo cuenta con
    `randomness > 0`.

    Los defaults NO se escriben acá: cada parámetro que no venga se omite y lo pone
    `RadioConfig`. Copiarlos en la firma sería tener dos juegos de defaults que se
    desincronizan en silencio — la request contesta con los que se usaron, en `config`.
    """
    armado = await _armar_set_radio(track, largo, curva, artist_gap, mmr_lambda, semilla,
                                    randomness)
    if isinstance(armado, JSONResponse):
        return armado
    estado, motivo = armado.estado, armado.motivo
    if armado.rset is None:
        return {**_radio_envoltura(estado, motivo), "config": None, "semilla": None,
                "pasos": [], "total": 0, "pedidos": None, "completo": False, "corte": None,
                "fragmentos": 0, "aviso_fragmentos": None, "leyenda_key": None}

    from motor.cli import LEYENDA_KEY, aviso_fragmentos, titular_corte

    config, elegida, rset = armado.config, armado.elegida, armado.rset
    corte = None if rset.is_complete else {
        "codigo": rset.stop, "titular": titular_corte(rset.stop), "detalle": rset.stop_detail}
    return {
        **_radio_envoltura(estado, motivo),
        # Lo que efectivamente se usó, leído del propio RadioConfig.
        "config": _radio_config(config),
        "semilla": _radio_track(elegida),
        "pasos": [_radio_paso(i, p) for i, p in enumerate(rset, 1)],
        "total": len(rset),
        "pedidos": config.length,
        "completo": rset.is_complete,
        "corte": corte,
        # Se dice SIEMPRE, como en la CLI: es la resta entre lo que lista la biblioteca y
        # entre lo que la radio eligió, y sin eso no se explica sola.
        "fragmentos": rset.fragments,
        "aviso_fragmentos": (aviso_fragmentos(rset.fragments, "La radio ignoró")
                             if rset.fragments else None),
        "leyenda_key": LEYENDA_KEY,
        # La huella de lo que se muestra (`saved_sets.fingerprint`): el front la devuelve al
        # guardar el set (POST /api/radio/sets) y, si el re-armado ya no muestra lo mismo, el
        # guardado es 409 en vez de guardar otra cosa. Ver `radio_sets_guardar`.
        "huella": _huella(rset, config),
    }


def _huella(rset, config) -> str:
    from motor.saved_sets import set_fingerprint

    return set_fingerprint(rset, config)


# Caracteres que Windows no acepta en un nombre de archivo, más los de control. El nombre
# del .m3u8 sale del título de la semilla, que es texto de un tag: puede traer cualquiera.
_NOMBRE_INVALIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]+')
_NOMBRE_MAX = 120        # sin la extensión: holgado para MAX_PATH con la carpeta Descargas


def _nombre_m3u8(semilla_label: str, curva: str) -> str:
    r"""Nombre del archivo que baja el navegador: seguro en Windows y sin rutas.

    Lleva la semilla y la curva porque es lo que distingue un set de otro en la carpeta
    Descargas ("DJ Radio - 4000Hz - Real Love - peak.m3u8"). Todo lo que Windows rechaza
    (``<>:"/\|?*``, controles, un punto o espacio al final) se saca: la barra en
    particular, porque un título con "/" no puede volverse una carpeta del nombre. El
    prefijo fijo "DJ Radio - " hace que nunca quede vacío ni sea un nombre reservado
    (CON, NUL, ...), así que esos dos casos no necesitan código.
    """
    base = _NOMBRE_INVALIDO.sub(" ", f"DJ Radio - {semilla_label} - {curva}")
    base = re.sub(r"\s+", " ", base).strip()[:_NOMBRE_MAX].rstrip(" .")
    return f"{base}.m3u8"


def _content_disposition(nombre: str) -> str:
    """`attachment` con el nombre en las dos formas de RFC 6266: `filename` en ASCII para
    los clientes viejos y `filename*` en UTF-8, para que "Señor Coconut" no llegue roto."""
    import unicodedata
    from urllib.parse import quote

    # "ñ" → "n" (se descarta el acento suelto); lo que no tiene letra base, como la raya del
    # label ("Artista — Título"), pasa a "-" en vez de desaparecer y pegar las palabras.
    ascii_ = "".join(c if c.isascii() else "-"
                     for c in unicodedata.normalize("NFKD", nombre)
                     if not unicodedata.combining(c))
    ascii_ = re.sub(r'["\\]', "", ascii_)
    # Después de NFKD: un "＜" o "：" de ancho completo es válido en Windows, pero al
    # normalizarse vuelve a ser "<" o ":", que no. Se sacan también acá, no solo en el nombre.
    ascii_ = re.sub(r"\s+", " ", _NOMBRE_INVALIDO.sub(" ", ascii_)).strip()
    return f"attachment; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nombre, safe='')}"


@app.get("/api/radio/set.m3u8")
async def radio_set_m3u8(track: str = "", largo: int | None = None, curva: str | None = None,
                         artist_gap: int | None = None, mmr_lambda: float | None = None,
                         semilla: int | None = None, randomness: float | None = None,
                         esperado: str = ""):
    r"""El set de /api/radio/set como .m3u8, para llevarlo a Rekordbox.

    Mismos parámetros y mismo armado (`_armar_set_radio`), y el archivo lo escribe
    `motor.export.m3u8_text`, que es lo que usa `python -m motor radio --m3u8`: el mismo
    contenido byte a byte (CRLF, sin BOM, ``#DJRADIO`` con el BPM a un decimal).

    RUTAS: absolutas, tal cual las guardó el scan (`Store._ruta`), igual que la CLI. No se
    relativizan: el archivo termina en la carpeta Descargas, y una ruta relativa se
    resolvería contra ESA carpeta. Con una base escaneada en Windows salen como
    ``C:\...\x.wav`` también si el server corre en Docker: el .m3u8 es para la PC donde
    está la música, no para el contenedor, así que acá no se chequea que existan.

    `esperado`: los ids del set que muestra la pantalla, separados por coma. Si el set
    re-armado no es ese (se escaneó algo entre medio y la radio ahora elige otra cosa), 409
    en vez de bajar otro set: el DJ se llevaría a Rekordbox una lista que nunca escuchó (§6).

    Errores con la misma forma que /set: 400 con el motivo del motor. Sin base (o base
    ocupada, ilegible...) no hay set que exportar: 409 con `estado`/`motivo`, y NO un 200,
    porque un 200 con JSON el navegador lo guardaría como si fuera el .m3u8.
    """
    armado = await _armar_set_radio(track, largo, curva, artist_gap, mmr_lambda, semilla,
                                    randomness)
    if isinstance(armado, JSONResponse):
        return armado
    if armado.rset is None:
        return JSONResponse({**_radio_envoltura(armado.estado, armado.motivo),
                             "error": armado.motivo}, status_code=409)

    from motor.export import m3u8_text

    rset = armado.rset
    ids = [_radio_id(t.path) for t in rset.tracks]
    pedidos = [i.strip() for i in esperado.split(",") if i.strip()]
    if pedidos and pedidos != ids:
        return JSONResponse(
            {"error": "El set cambió desde que lo armaste: la biblioteca del motor ya no es "
                      "la misma (¿un scan nuevo?). Armalo de nuevo y exportá ese.",
             "esperado": pedidos, "armado": ids}, status_code=409)

    nombre = _nombre_m3u8(armado.elegida.label, armado.config.curve)
    return Response(
        content=m3u8_text(rset.tracks).encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(nombre),
                 "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


async def _radio_ruta(track_id: str) -> str | None:
    """La ruta real de un id de la radio, o None. La comparten el audio, la onda y las marcas:
    el cliente nunca manda una ruta, solo el id opaco de /api/radio/biblioteca."""
    ruta = _radio_audio.get(track_id)
    if ruta is None and (not _radio_audio
                         or time.monotonic() - _radio_recarga_ts >= _RADIO_RECARGA_MIN_S):
        # Puede ser un id nuevo (se escaneó algo desde el último pedido). Acotado: sin el
        # mínimo entre recargas, una tanda de ids desconocidos hace un `load_library()` por
        # pedido. Con el índice vacío se recarga igual — ahí no hay nada que proteger.
        await asyncio.to_thread(_cargar_radio)
        ruta = _radio_audio.get(track_id)
    return ruta


@app.get("/api/radio/audio/{track_id}")
async def radio_audio(track_id: str):
    """Sirve el archivo de un track de la biblioteca del motor, para reproducir el set.
    Solo LEE: nunca escribe ni mueve el original (regla del proyecto).

    El id no es una ruta (`_radio_id`) y lo único que se sirve es lo que está en el índice
    que armó la base, así que no hay forma de pedir un archivo de afuera de la biblioteca.
    """
    ruta = await _radio_ruta(track_id)
    if ruta is None:
        return JSONResponse({"error": "track no encontrado"}, status_code=404)
    if not Path(ruta).exists():
        # Distinto de "no encontrado" a propósito (§6): el track SÍ está en la base y el
        # archivo no está donde lo dejó el scan. El caso más común no es que se haya movido:
        # es Docker con una base escaneada en Windows, donde las rutas guardadas son `C:\...`
        # y adentro del contenedor no existen ni pueden existir. El mensaje dice las dos
        # cosas en una línea porque es lo único que se ve en pantalla.
        return JSONResponse(
            {"error": f"el archivo de este track no está en esta máquina: la base lo tiene "
                      f"en {ruta}. Se movió, o la base se escaneó en otro sistema (típico en "
                      f"Docker con una base de Windows): re-escaneá la música desde adentro "
                      f"con `docker compose exec web python -m motor scan /musica`."},
            status_code=404)
    return FileResponse(ruta)             # FileResponse maneja Range → el <audio> puede buscar


# --- Sets guardados y calificación de transiciones (tarea 16, `motor/saved_sets.py`) -------
#
# Un set guardado es la FOTO de lo que se mostró (ver el docstring de `motor/saved_sets.py`):
# acá nada lo re-arma ni recalcula al leerlo. Las rutas de los tracks salen de la base, nunca
# del cliente: el cliente manda ids opacos (`_radio_id`) y números de set/transición.
#
#   POST   /api/radio/sets                          guardar el set que se está viendo
#   GET    /api/radio/sets                          listar (con el resumen de calificaciones)
#   GET    /api/radio/sets/{id}                     uno, con su foto y sus calificaciones
#   PATCH  /api/radio/sets/{id}                     renombrar: {"nombre": "..."} ("" o null = sin nombre)
#   DELETE /api/radio/sets/{id}                     borrar el set y sus calificaciones
#   PUT    /api/radio/sets/{id}/transiciones/{n}    calificar: {"calificacion": "ok|regular|mala", "motivo": "..."}
#   DELETE /api/radio/sets/{id}/transiciones/{n}    dejarla sin calificar
#
# La transición `n` va de la posición `n` a la `n + 1` (las posiciones son las `n` de los pasos).
# Degradación: los GET contestan 200 con `estado`/`motivo` (como el resto de la radio) y las
# escrituras 409 con lo mismo más `error`: sin base no hay nada que escribir, y un 200 diría
# que se guardó. Pedido inválido → 400 con el motivo del motor; set inexistente → 404.


def _set_guardado_resumen(info) -> dict:
    return {"id": info.id, "nombre": info.name, "guardado": info.created_at,
            "semilla": info.seed_label, "total": info.tracks, "pedidos": info.requested,
            "curva": info.curve, "resumen": info.summary, "faltan": info.missing}


def _set_guardado_json(s) -> dict:
    """Un set guardado como lo lee la pantalla: la MISMA forma de paso que /api/radio/set
    (`_radio_paso`) para que el front reuse cómo lo dibuja, más lo propio del set guardado.

    Todo sale de la foto: `bpm` es el `bpm_shown` que se vio, `energia_pct` el percentil que
    se imprimió, `motivo` el `Transition.reason()` de ese momento. `en_biblioteca: false`
    dice que el archivo ya no está en la biblioteca (se borró o se movió y se re-escaneó):
    el paso se muestra igual, entero, y sin audio — no hay qué reproducir.
    """
    from motor.cli import LEYENDA_KEY, aviso_fragmentos, titular_corte

    pasos = []
    for paso in s.steps:
        f = paso.snapshot
        tid = _radio_id(f.path)
        pasos.append({
            "n": f.position,
            "track": {
                "id": tid, "label": f.label, "titulo": f.title_shown, "artista": f.artist or "",
                "bpm": float(f.bpm_shown), "camelot": f.key or None, "tonalidad": f.key_classic,
                "key_dudosa": f.key_doubtful, "energia": round(f.energy, 3),
                "energia_pct": f.energy_pct, "dur": round(f.duration, 1), "es_track": f.is_track,
                "licencia": f.license, "origen": f.source_url,
                "en_biblioteca": paso.in_library,
                "audio": f"/api/radio/audio/{tid}" if paso.in_library else None,
            },
            "motivo": f.reason,
            "es_semilla": f.is_seed,
            "transicion": {
                "from_bpm": f.from_bpm, "to_bpm": f.bpm, "bpm_delta_pct": f.bpm_delta_pct,
                "bpm_octava": f.bpm_octave, "from_key": f.from_key, "to_key": f.key,
                "key_relacion": f.key_relation, "key_compat": f.key_compat,
                "mezclabilidad": f.mixability, "encaje_musical": f.musical_fit,
                "score": f.score, "energia": f.energy, "energia_objetivo": f.energy_goal,
            },
        })
    transiciones = []
    for n in range(1, s.transitions + 1):
        r = s.rating_of(n)
        transiciones.append({
            "n": n, "desde": n, "hasta": n + 1, "motivo_motor": s.steps[n].snapshot.reason,
            "calificacion": r.rating if r else None, "motivo": r.reason if r else None,
            "calificada": r.rated_at if r else None})
    c = s.config
    return {
        "id": s.id, "nombre": s.name, "guardado": s.created_at,
        # Con los nombres de /api/radio/set; `config_motor` es el RadioConfig entero (pesos
        # incluidos), tal cual se guardó.
        "config": {"largo": c.get("length"), "curva": c.get("curve"),
                   "artist_gap": c.get("artist_gap"), "mmr_lambda": c.get("mmr_lambda"),
                   "semilla": c.get("seed"), "randomness": c.get("randomness")},
        "config_motor": c,
        "pasos": pasos, "total": len(pasos), "pedidos": s.requested,
        "completo": s.stop is None,
        "corte": None if s.stop is None else {"codigo": s.stop, "titular": titular_corte(s.stop),
                                               "detalle": s.stop_detail},
        "fragmentos": s.fragments,
        "aviso_fragmentos": (aviso_fragmentos(s.fragments, "La radio ignoró")
                             if s.fragments else None),
        "transiciones": transiciones, "resumen": s.summary(), "faltan": s.missing,
        "leyenda_key": LEYENDA_KEY,
    }


def _sets_sin_base(estado: str, motivo: str | None) -> JSONResponse:
    """Una escritura sin base utilizable: 409 con el estado, nunca un 200 ni un 500."""
    return JSONResponse({**_radio_envoltura(estado, motivo), "error": motivo}, status_code=409)


async def _sets_escribir(accion) -> tuple[object, JSONResponse | None]:
    """Corre `accion(store)`; `(resultado, None)` o `(None, la respuesta de error)`."""
    from motor.saved_sets import InvalidSavedSet, SavedSetNotFound

    try:
        res, estado, motivo = await asyncio.to_thread(_usar_store_motor, accion)
    except InvalidSavedSet as e:
        return None, JSONResponse({"error": str(e)}, status_code=400)
    except SavedSetNotFound as e:
        return None, JSONResponse({"error": str(e)}, status_code=404)
    if estado != _RADIO_OK:
        return None, _sets_sin_base(estado, motivo)
    return res, None


def _campo(payload: dict, nombre: str, tipo):
    """Un campo opcional del cuerpo con su tipo, o `ValueError`. `True` no es un int acá: un
    `largo: true` no puede armar un set de 1."""
    valor = payload.get(nombre)
    if valor is None:
        return None
    ok = (isinstance(valor, int) and not isinstance(valor, bool)) if tipo is int else \
        (isinstance(valor, int | float) and not isinstance(valor, bool)) if tipo is float else \
        isinstance(valor, tipo)
    if not ok:
        raise ValueError(f"`{nombre}` tiene que ser {tipo.__name__}, recibí {valor!r}")
    # Un float que llega como entero en el JSON (`"randomness": 0`, que es como lo manda un
    # navegador: JSON.stringify(0.0) da "0") se guarda como float, igual que lo parsea
    # /api/radio/set desde la query. Si quedara int, la huella del set (`shown_header`)
    # serializaría `0` en vez de `0.0` y el guardado daría 409 con el mismo set.
    if tipo is not float:
        return valor
    try:
        return float(valor)
    except OverflowError:
        # Un entero de cientos de dígitos no entra en un float: es un pedido inválido (400),
        # no un error del servidor (500).
        raise ValueError(f"`{nombre}` está fuera de rango: {str(valor)[:20]}…") from None


@app.post("/api/radio/sets")
async def radio_sets_guardar(payload: dict):
    """Guarda el set que la pantalla está mostrando.

    Cuerpo: los MISMOS parámetros que /api/radio/set (`track`, `largo`, `curva`,
    `artist_gap`, `mmr_lambda`, `semilla`, `randomness`) más:

    - `esperado` (obligatorio): los ids de los pasos que se mostraron, en orden (lista o
      separados por coma). Es el mecanismo del export .m3u8: el set se re-arma con la misma
      config y, si no da esos ids, 409 en vez de guardar otro set.
    - `huella` (obligatoria): la `huella` que devolvió /api/radio/set. Cubre el caso que los
      ids no ven: el mismo set de tracks pero con un dato distinto (un re-escaneo cambió un
      BPM entre que se mostró y se guardó), o la misma lista con otro encabezado o corte.
      Falta → 400; no coincide → 409.
    - `nombre` (opcional).

    201 con el set guardado (la misma forma que GET /api/radio/sets/{id}).
    """
    if not isinstance(payload, dict):
        return JSONResponse({"error": "el cuerpo tiene que ser un objeto JSON"}, status_code=400)
    try:
        track = _campo(payload, "track", str) or ""
        params = [_campo(payload, "largo", int), _campo(payload, "curva", str),
                  _campo(payload, "artist_gap", int), _campo(payload, "mmr_lambda", float),
                  _campo(payload, "semilla", int), _campo(payload, "randomness", float)]
        huella = _campo(payload, "huella", str)
        nombre = _campo(payload, "nombre", str)
        esperado = payload.get("esperado")
        if isinstance(esperado, str):
            esperado = [i.strip() for i in esperado.split(",") if i.strip()]
        if not (isinstance(esperado, list) and esperado
                and all(isinstance(i, str) for i in esperado)):
            raise ValueError("falta `esperado`: los ids de los pasos que se mostraron, en "
                             "orden. Sin eso no hay forma de saber que se guarda lo que se vio.")
        if not huella:
            raise ValueError("falta `huella`: la que devolvió /api/radio/set con el set que se "
                             "muestra. Sin ella un re-escaneo entre mostrar y guardar haría "
                             "guardar datos que nunca estuvieron en pantalla.")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    armado = await _armar_set_radio(track, *params)
    if isinstance(armado, JSONResponse):
        return armado
    if armado.rset is None:
        return _sets_sin_base(armado.estado, armado.motivo)

    from motor.saved_sets import config_json, fingerprint, shown_header, snapshot_steps

    rset, config = armado.rset, armado.config
    ids = [_radio_id(t.path) for t in rset.tracks]
    if esperado != ids:
        return JSONResponse(
            {"error": "El set cambió desde que lo armaste: la biblioteca del motor ya no es "
                      "la misma (¿un scan nuevo?). Armalo de nuevo y guardá ese.",
             "esperado": esperado, "armado": ids}, status_code=409)
    fotos = snapshot_steps(rset)
    armada = fingerprint(fotos, shown_header(rset, config))
    if huella != armada:
        return JSONResponse(
            {"error": "Los datos de los tracks cambiaron desde que armaste el set (¿un "
                      "re-escaneo?): los mismos tracks, pero no lo que se mostró. Armalo de "
                      "nuevo y guardá ese.", "huella_esperada": huella, "huella_armada": armada},
            status_code=409)

    def guardar(store):
        set_id = store.save_set(fotos, config=config_json(config), requested=config.length,
                                stop=rset.stop, stop_detail=rset.stop_detail,
                                fragments=rset.fragments, name=nombre)
        return store.get_saved_set(set_id)

    guardado, error = await _sets_escribir(guardar)
    if error is not None:
        return error
    return JSONResponse({**_radio_envoltura(_RADIO_OK, None), "set": _set_guardado_json(guardado)},
                        status_code=201)


@app.get("/api/radio/sets")
async def radio_sets_listar():
    """Los sets guardados, del más nuevo al más viejo, con el resumen de calificaciones."""
    sets, estado, motivo = await asyncio.to_thread(
        _usar_store_motor, lambda store: store.list_saved_sets())
    return {**_radio_envoltura(estado, motivo),
            "sets": [_set_guardado_resumen(s) for s in sets or []]}


@app.get("/api/radio/sets/{set_id}")
async def radio_sets_ver(set_id: int):
    """Un set guardado: su foto (sin re-armar ni recalcular), sus calificaciones y el resumen."""
    from motor.saved_sets import SavedSetNotFound

    try:
        s, estado, motivo = await asyncio.to_thread(
            _usar_store_motor, lambda store: store.get_saved_set(set_id))
    except SavedSetNotFound as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return {**_radio_envoltura(estado, motivo),
            "set": None if s is None else _set_guardado_json(s)}


@app.get("/api/radio/sets/{set_id}/m3u8")
async def radio_sets_m3u8(set_id: int):
    """El set guardado como .m3u8 para Rekordbox: su FOTO (rutas y datos que se guardaron,
    en su orden), no un re-armado. Mismo contenido que escribe `motor.export.m3u8_text` (vía
    `saved_sets.snapshot_m3u8`) y mismas cabeceras y nombre de archivo que
    /api/radio/set.m3u8.

    Una ruta que ya no está en la biblioteca va igual: es la que había, y el DJ puede tener el
    archivo en otro lado. `X-DJRadio-Faltan` dice cuántas son, para que la pantalla avise.
    Errores: 404 si el set no existe; sin base utilizable, 409 con `estado`/`motivo` (un 200
    con JSON el navegador lo guardaría como si fuera el .m3u8)."""
    from motor.saved_sets import SavedSetNotFound, snapshot_m3u8

    try:
        s, estado, motivo = await asyncio.to_thread(
            _usar_store_motor, lambda store: store.get_saved_set(set_id))
    except SavedSetNotFound as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    if estado != _RADIO_OK or s is None:
        return _sets_sin_base(estado, motivo)
    nombre = _nombre_m3u8(s.steps[0].snapshot.label if s.steps else f"set {s.id}",
                          s.config.get("curve") or "")
    return Response(
        content=snapshot_m3u8(s.steps).encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(nombre),
                 "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                 "X-DJRadio-Faltan": str(s.missing)})


@app.patch("/api/radio/sets/{set_id}")
async def radio_sets_renombrar(set_id: int, payload: dict):
    if "nombre" not in payload:
        return JSONResponse({"error": "falta `nombre` (\"\" o null lo deja sin nombre)"},
                            status_code=400)
    nombre, error = await _sets_escribir(
        lambda store: store.rename_saved_set(set_id, payload["nombre"]))
    if error is not None:
        return error
    return {**_radio_envoltura(_RADIO_OK, None), "id": set_id, "nombre": nombre}


@app.delete("/api/radio/sets/{set_id}")
async def radio_sets_borrar(set_id: int):
    _, error = await _sets_escribir(lambda store: store.delete_saved_set(set_id))
    if error is not None:
        return error
    return {**_radio_envoltura(_RADIO_OK, None), "borrado": set_id}


@app.put("/api/radio/sets/{set_id}/transiciones/{n}")
async def radio_sets_calificar(set_id: int, n: int, payload: dict):
    """Califica la transición `n` (de la posición `n` a la `n + 1`). Reemplaza la que hubiera.
    `motivo` es obligatorio con `mala` y opcional con `ok` / `regular`."""
    def calificar(store):
        r = store.rate_transition(set_id, n, payload.get("calificacion"), payload.get("motivo"))
        return r, store.get_saved_set(set_id).summary()

    res, error = await _sets_escribir(calificar)
    if error is not None:
        return error
    r, resumen = res
    return {**_radio_envoltura(_RADIO_OK, None),
            "transicion": {"n": r.transition, "desde": r.transition, "hasta": r.transition + 1,
                           "calificacion": r.rating, "motivo": r.reason,
                           "calificada": r.rated_at},
            "resumen": resumen}


@app.delete("/api/radio/sets/{set_id}/transiciones/{n}")
async def radio_sets_descalificar(set_id: int, n: int):
    """Deja la transición `n` sin calificar. `borrada: false` si ya no tenía calificación."""
    def borrar(store):
        return store.delete_rating(set_id, n), store.get_saved_set(set_id).summary()

    res, error = await _sets_escribir(borrar)
    if error is not None:
        return error
    borrada, resumen = res
    return {**_radio_envoltura(_RADIO_OK, None), "n": n, "borrada": borrada,
            "resumen": resumen}


# --- Editor de cues: marcas del dueño y forma de onda (f48, `motor/cue_marks.py`) ----------
#
#   GET    /api/radio/tracks/{id}/marcas              el track (lo medido) y sus marcas
#   POST   /api/radio/tracks/{id}/marcas              crear: {"tipo", "inicio", "fin"?, "num"?, "nombre"?}
#   PATCH  /api/radio/tracks/{id}/marcas/{marca}      mover / renombrar / cambiar de pad
#   DELETE /api/radio/tracks/{id}/marcas/{marca}      borrar UNA marca
#   GET    /api/radio/tracks/{id}/onda                los picos del audio real (cacheados)
#   GET    /api/radio/marcas/conteo?ids=a,b,...       cuántas marcas tiene cada track, y huérfanas
#
# `id` es el id opaco de /api/radio/biblioteca (`_radio_id`): la ruta sale de la base, nunca
# del cliente. Los tiempos van en SEGUNDOS con tres decimales; la base los guarda en ms.
# Errores: 400 pedido inválido (con el motivo), 404 track o marca que no existe (también un id
# con forma imposible o fuera de rango), 403/415 pedido de otra página o que no es JSON (la
# misma regla que `_cuerpo_json_propio`), 409 sin base utilizable. Nunca 500.
#
# Las escrituras devuelven SIEMPRE la lista entera de marcas del track tal como quedó en la
# base: la pantalla dibuja eso, así su «Guardado» es lo que el servidor confirmó.
#
# `num` es el PAD (0..7 = A..H). Desde el esquema v6 lo lleva un hot cue (siempre) y también un
# loop que vive en un pad (hot loop; sin `num`, memory loop); una memory cue nunca. El pad es
# único por track entre los dos, y `limites.hot_cues` cuenta los pads (son los mismos 8).

_RE_TRACK_ID = re.compile(r"[0-9a-f]{16}")
_RE_MARCA_ID = re.compile(r"[0-9]{1,19}")
_CAMPOS_MARCA_POST = {"tipo", "inicio", "fin", "num", "nombre"}
_CAMPOS_MARCA_PATCH = {"inicio", "fin", "num", "nombre"}
_CONTEO_MAX_IDS = 500


def _marca_json(m, duracion: float | None) -> dict:
    """Una marca como la lee la pantalla. `fuera_del_track`: la marca quedó más allá de la
    duración que la base tiene HOY (el archivo cambió y se re-escaneó): se muestra y se avisa,
    no se borra ni se recorta."""
    dur_ms = None if duracion is None else int(round(duracion * 1000))
    fuera = dur_ms is not None and (m.start_ms >= dur_ms
                                    or (m.end_ms is not None and m.end_ms > dur_ms))
    return {"id": m.id, "tipo": m.kind, "num": m.num, "inicio": m.start_ms / 1000,
            "fin": None if m.end_ms is None else m.end_ms / 1000, "nombre": m.name,
            "creada": m.created_at, "modificada": m.updated_at, "fuera_del_track": fuera}


def _track_editor(t) -> dict:
    """El track para el editor: lo MEDIDO por el motor y nada más (§6). Un BPM 0.0 es lo que
    devuelve el análisis cuando no encontró pulso: no es una medición, va `null` y la pantalla
    dibuja «?» (y no ofrece moverse de a un beat). `key_acuerdo` viaja crudo ("2/3") porque el
    editor lo muestra al lado de la key.

    f50: también lo que la pestaña «Información» del detalle muestra del archivo, tal como lo
    tiene la base: la ruta, el formato (la extensión; sin extensión, None y no uno adivinado),
    la licencia y el origen. Licencia y origen son datos OPCIONALES (decisión del dueño,
    2026-10-09, CLAUDE.md): viajan tal cual están en la base, que sin declarar guarda el
    literal «no declarado» (`motor.modelos.NO_DECLARADO`); si igual llegara uno vacío (un
    objeto armado a mano), pasa por la misma compuerta que la base (`declared_text`) y va el
    literal, nunca null ni un valor inventado. La ruta es la del archivo del propio dueño en su
    máquina: la misma que ya viaja en el .m3u8 del set."""
    from motor.modelos import declared_text

    d = _radio_track(t)
    bpm = _num(t.bpm)
    d["bpm"] = round(bpm, 1) if bpm is not None and bpm > 0 else None
    d["key_acuerdo"] = t.key_acuerdo
    ruta = Path(t.path)
    d["ruta"] = str(ruta)
    d["formato"] = ruta.suffix.lstrip(".").lower() or None
    d["licencia"] = declared_text(getattr(t, "license", None), "license")
    d["origen"] = declared_text(getattr(t, "source_url", None), "source_url")
    return d


def _limites_marcas() -> dict:
    from motor.cue_marks import HOT_CUES, MAX_LOOPS, MAX_MEMORY, NAME_MAX

    return {"hot_cues": HOT_CUES, "memory": MAX_MEMORY, "loops": MAX_LOOPS,
            "nombre_max": NAME_MAX}


async def _marcas_ruta(track_id: str) -> tuple[str | None, JSONResponse | None]:
    """La ruta del track o el 404. Un id que no tiene la forma de `_radio_id` es 404 sin
    tocar la base: no puede ser un track y no vale una recarga de la biblioteca."""
    if not _RE_TRACK_ID.fullmatch(track_id or ""):
        return None, JSONResponse({"error": "track no encontrado"}, status_code=404)
    ruta = await _radio_ruta(track_id)
    if ruta is None:
        return None, JSONResponse({"error": "track no encontrado en la biblioteca del motor"},
                                  status_code=404)
    return ruta, None


def _marca_id(texto: str) -> int | None:
    """El id de marca de la URL, o None si no puede ser uno (→ 404, como los sets)."""
    return int(texto) if _RE_MARCA_ID.fullmatch(texto or "") else None


async def _cuerpo_marca(request: Request, permitidos: set[str]):
    """El cuerpo JSON de una escritura de marcas, o la respuesta de rechazo. Misma defensa que
    `_cuerpo_json_propio` (otra página → 403, no JSON → 415), con el motivo en `error` como el
    resto de la radio, y además: campos que la operación no conoce → 400 (un `tipo` en un
    PATCH no se ignora en silencio)."""
    ajeno = _origen_ajeno(request)
    if ajeno:
        logger.warning(f"🛡️ Rechazado un pedido de otra página ({ajeno}) a {request.url.path}")
        return None, JSONResponse({"error": "Pedido rechazado: no viene de MusiFlix."},
                                  status_code=403)
    tipo = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if tipo != "application/json":
        return None, JSONResponse({"error": "El pedido tiene que ser JSON (Content-Type: "
                                            "application/json)."}, status_code=415)
    try:
        cuerpo = await request.json()
    except Exception:
        return None, JSONResponse({"error": "El cuerpo no es JSON válido."}, status_code=400)
    if not isinstance(cuerpo, dict):
        return None, JSONResponse({"error": "El cuerpo tiene que ser un objeto JSON."},
                                  status_code=400)
    sobran = sorted(set(cuerpo) - permitidos)
    if sobran:
        return None, JSONResponse(
            {"error": f"campos desconocidos: {', '.join(map(str, sobran))[:200]}. Se aceptan: "
                      f"{', '.join(sorted(permitidos))}."}, status_code=400)
    return cuerpo, None


async def _marcas_escribir(accion) -> tuple[object, JSONResponse | None]:
    """Corre `accion(store)` y traduce los errores del pedido: 400 / 404 / 409."""
    from motor.cue_marks import CueMarkNotFound, InvalidCueMark

    try:
        res, estado, motivo = await asyncio.to_thread(_usar_store_motor, accion)
    except InvalidCueMark as e:
        return None, JSONResponse({"error": str(e)}, status_code=400)
    except CueMarkNotFound as e:
        return None, JSONResponse({"error": str(e)}, status_code=404)
    if estado != _RADIO_OK:
        return None, _sets_sin_base(estado, motivo)
    return res, None


def _marcas_de(store, ruta) -> list[dict]:
    t = store.get(ruta)
    return [_marca_json(m, None if t is None else t.duration) for m in store.list_cue_marks(ruta)]


@app.get("/api/radio/tracks/{track_id}/marcas")
async def radio_marcas(track_id: str):
    """El track (BPM, key, acuerdo y duración tal como los midió el motor) y sus marcas."""
    ruta, error = await _marcas_ruta(track_id)
    if error is not None:
        return error

    def leer(store):
        t = store.get(ruta)
        return t, _marcas_de(store, ruta)

    res, estado, motivo = await asyncio.to_thread(_usar_store_motor, leer)
    if estado != _RADIO_OK:
        return {**_radio_envoltura(estado, motivo), "track": None, "marcas": [],
                "limites": None}
    t, marcas = res
    if t is None:
        # Estaba en el índice pero ya no en la base (un scan entre medio).
        return JSONResponse({"error": "track no encontrado en la biblioteca del motor"},
                            status_code=404)
    return {**_radio_envoltura(estado, motivo), "track": _track_editor(t), "marcas": marcas,
            "limites": _limites_marcas()}


@app.post("/api/radio/tracks/{track_id}/marcas")
async def radio_marcas_crear(track_id: str, request: Request):
    """Crea una marca. `tipo`: cue | memory | loop. `inicio` (y `fin` en un loop) en segundos.
    `num` (0..7) es el pad: en un hot cue, sin él toma el primer pad libre; en un loop, con él
    es un hot loop en ESE pad (libre) y sin él un memory loop; una memory no lo lleva. 201 con
    la marca y la lista entera."""
    ruta, error = await _marcas_ruta(track_id)
    if error is not None:
        return error
    cuerpo, rechazo = await _cuerpo_marca(request, _CAMPOS_MARCA_POST)
    if rechazo is not None:
        return rechazo

    def crear(store):
        m = store.add_cue_mark(ruta, cuerpo.get("tipo"), cuerpo.get("inicio"), cuerpo.get("fin"),
                               num=cuerpo.get("num"), name=cuerpo.get("nombre"))
        return m, _marcas_de(store, ruta)

    res, error = await _marcas_escribir(crear)
    if error is not None:
        return error
    m, marcas = res
    return JSONResponse({**_radio_envoltura(_RADIO_OK, None),
                         "marca": next(x for x in marcas if x["id"] == m.id), "marcas": marcas},
                        status_code=201)


@app.patch("/api/radio/tracks/{track_id}/marcas/{marca_id}")
async def radio_marcas_cambiar(track_id: str, marca_id: str, request: Request):
    """Mueve (`inicio`, `fin`), renombra (`nombre`; "" o null = sin nombre) o cambia de pad
    (`num`) una marca. Lo que no viene no cambia. Un loop puede tomar un pad libre o soltarlo
    (`num: null`, vuelve a memory loop); un hot cue no puede quedar sin pad."""
    ruta, error = await _marcas_ruta(track_id)
    if error is not None:
        return error
    mid = _marca_id(marca_id)
    if mid is None:
        return JSONResponse({"error": f"no hay una marca con id {marca_id[:40]!r}"},
                            status_code=404)
    cuerpo, rechazo = await _cuerpo_marca(request, _CAMPOS_MARCA_PATCH)
    if rechazo is not None:
        return rechazo
    cambios = {{"inicio": "start_s", "fin": "end_s", "nombre": "name", "num": "num"}[k]: v
               for k, v in cuerpo.items()}

    def cambiar(store):
        m = store.update_cue_mark(ruta, mid, **cambios)
        return m, _marcas_de(store, ruta)

    res, error = await _marcas_escribir(cambiar)
    if error is not None:
        return error
    m, marcas = res
    return {**_radio_envoltura(_RADIO_OK, None),
            "marca": next(x for x in marcas if x["id"] == m.id), "marcas": marcas}


@app.delete("/api/radio/tracks/{track_id}/marcas/{marca_id}")
async def radio_marcas_borrar(track_id: str, marca_id: str, request: Request):
    """Borra UNA marca. Sin cuerpo; igual se rechaza un pedido de otra página."""
    ajeno = _origen_ajeno(request)
    if ajeno:
        logger.warning(f"🛡️ Rechazado un pedido de otra página ({ajeno}) a {request.url.path}")
        return JSONResponse({"error": "Pedido rechazado: no viene de MusiFlix."}, status_code=403)
    ruta, error = await _marcas_ruta(track_id)
    if error is not None:
        return error
    mid = _marca_id(marca_id)
    if mid is None:
        return JSONResponse({"error": f"no hay una marca con id {marca_id[:40]!r}"},
                            status_code=404)

    def borrar(store):
        store.delete_cue_mark(ruta, mid)
        return _marcas_de(store, ruta)

    marcas, error = await _marcas_escribir(borrar)
    if error is not None:
        return error
    return {**_radio_envoltura(_RADIO_OK, None), "borrada": mid, "marcas": marcas}


@app.get("/api/radio/marcas/conteo")
async def radio_marcas_conteo(ids: str = ""):
    """Cuántas marcas tiene cada track de `ids` (separados por coma; los que no tienen van
    con 0, los que no son de la biblioteca no aparecen) y cuántas quedaron huérfanas en toda
    la base (de archivos que se movieron o ya no están: ver `motor/cue_marks.py`)."""
    pedidos = [i.strip() for i in ids.split(",") if i.strip()]
    if len(pedidos) > _CONTEO_MAX_IDS:
        return JSONResponse({"error": f"como mucho {_CONTEO_MAX_IDS} ids por pedido; "
                                      f"llegaron {len(pedidos)}"}, status_code=400)
    rutas = {}
    for i in dict.fromkeys(pedidos):
        if _RE_TRACK_ID.fullmatch(i):
            ruta = await _radio_ruta(i)
            if ruta is not None:
                rutas[i] = ruta

    def contar(store):
        conteos = store.cue_mark_counts(rutas.values())
        # f56: las marcas importadas de Rekordbox de un archivo que está en disco pero el motor
        # todavía no analizó NO son huérfanas: el archivo no «ya no está», solo falta medirlo.
        huerfanas = [(k, n) for k, n in store.orphan_cue_marks() if not os.path.isfile(k)]
        return ({i: conteos.get(os.path.normcase(os.path.abspath(r)), 0)
                 for i, r in rutas.items()}, huerfanas)

    res, estado, motivo = await asyncio.to_thread(_usar_store_motor, contar)
    if estado != _RADIO_OK:
        return {**_radio_envoltura(estado, motivo), "conteos": {}, "huerfanas": None}
    conteos, huerfanas = res
    return {**_radio_envoltura(estado, motivo), "conteos": conteos,
            "huerfanas": {"tracks": len(huerfanas), "marcas": sum(n for _, n in huerfanas),
                          # Solo el nombre del archivo, no la ruta: alcanza para reconocerlo.
                          "archivos": [os.path.basename(k) for k, _ in huerfanas[:20]]}}


@app.get("/api/radio/tracks/{track_id}/onda")
async def radio_onda(track_id: str):
    """Los picos del audio real del track (`motor.peaks`), para dibujar la forma de onda.

    Se calculan leyendo el archivo por bloques (poca memoria) y se cachean en
    `<MUSIFLIX_DATA_DIR>/peaks`; si el archivo cambia (tamaño o mtime) se recalculan. El
    archivo se abre solo para leer. `duracion_audio` es la del audio DECODIFICADO: si no
    coincide con la de la base, la pantalla lo avisa en vez de estirar la onda."""
    ruta, error = await _marcas_ruta(track_id)
    if error is not None:
        return error
    from motor.peaks import UnreadableAudio, cached_peaks

    try:
        picos, de_cache = await asyncio.to_thread(cached_peaks, ruta, Path(db.DATA_DIR) / "peaks")
    except FileNotFoundError:
        return JSONResponse({"error": f"el archivo de este track no está en esta máquina: la "
                                      f"base lo tiene en {ruta}. Se movió, o la base se escaneó "
                                      f"en otro sistema."}, status_code=404)
    except UnreadableAudio as e:
        return JSONResponse({"error": f"no pude leer el audio para dibujar la onda: {e}"},
                            status_code=422)
    except OSError as e:
        return JSONResponse({"error": f"no pude leer el archivo ({type(e).__name__})"},
                            status_code=409)
    except (RuntimeError, ValueError, ArithmeticError) as e:
        # Cinturón: un decodificador que falla de una forma que `motor.peaks` no previó. Es un
        # archivo que no se pudo leer (422), no un 500; queda en el log para mirarlo.
        logger.warning(f"⚠️ Onda: {type(e).__name__} leyendo {ruta}: {e}")
        return JSONResponse({"error": f"no pude leer el audio para dibujar la onda "
                                      f"({type(e).__name__})"}, status_code=422)
    return {"bins": int(picos.peaks.size),
            "picos": [float(v) for v in picos.peaks],          # 5 decimales, de la caché
            "duracion_audio": round(picos.duration_s, 3), "sample_rate": picos.sample_rate,
            "canales": picos.channels, "cache": de_cache}


# --- Onda de 3 bandas para el editor grande (f52, `motor/bandas.py`) -----------------------
#
#   GET /api/radio/tracks/{id}/onda3?desde=<s>&hasta=<s>&puntos=<n>
#
# Graves, medios y agudos del audio REAL a 100 cuadros por segundo (cacheados junto a los
# picos, en `<MUSIFLIX_DATA_DIR>/peaks`), del tramo [desde, hasta] reducido por MÁXIMO a
# `puntos` por banda (un kick no desaparece al achicar), y la grilla de beats ESTIMADA cuando
# el track tiene BPM medido en la base. Sin `desde`/`hasta`: el tema entero.
#
# Mismo id opaco y mismos errores que /onda: 404 track o archivo que no está (también un id
# con forma imposible), 422 audio que no decodifica, 409 sin base utilizable (de la base sale
# el BPM de la grilla). Además 400 con el motivo si el tramo o `puntos` no sirven: un rango
# afuera del audio NO se recorta en silencio. Nunca 500.

_ONDA3_PUNTOS_MAX = 4000
_ONDA3_PUNTOS_DEFECTO = 1000
# `hasta` puede ser la duración que mostró /onda, redondeada a ms (hasta 0,5 ms de más).
_ONDA3_TOLERANCIA_S = 0.001
_RE_ONDA3_PUNTOS = re.compile(r"[0-9]{1,6}")
# Segundos: hasta 6 dígitos enteros (11 días) y hasta 20 decimales (los decimales largos de un
# `String(x)` de JS o un `str(float)` entran). Sin exponente (tampoco el `1e-7` de JS),
# espacios, signo más ni nan/inf.
_RE_ONDA3_SEGUNDOS = re.compile(r"-?[0-9]{1,6}(\.[0-9]{1,20})?")
# 0..255 → 0..1 con 3 decimales (alcanzan: el paso es 0,0039), calculado una vez.
_ONDA3_VALOR = [round(i / 255, 3) for i in range(256)]


def _onda3_segundos(nombre: str, texto: str | None) -> tuple[float | None, str | None]:
    """`(segundos, None)`, `(None, None)` si no vino, o `(None, motivo)` si no sirve.

    Estricto, como `puntos`: dígitos ASCII con punto decimal opcional (y el signo menos, para
    que un negativo diga «no puede ser negativo»). Con `float()` a secas era laxo: aceptaba
    espacios, `1_0`, `5e1`, `+5`, `nan`, `infinity` y dígitos de otros alfabetos
    (`float('١٢')` = 12).
    Lo que pasa la regex es siempre finito."""
    if texto is None:
        return None, None
    if not _RE_ONDA3_SEGUNDOS.fullmatch(texto):
        return None, (f"`{nombre}` tiene que ser un número de segundos (dígitos y punto "
                      f"decimal, ej. 12.5); llegó {texto[:40]!r}")
    return float(texto), None


def _onda3_parametros(desde: str | None, hasta: str | None,
                      puntos: str | None) -> tuple[tuple | None, str | None]:
    """Lo que se puede validar sin abrir el audio: la forma de los tres parámetros."""
    d, motivo = _onda3_segundos("desde", desde)
    if motivo is None:
        h, motivo = _onda3_segundos("hasta", hasta)
    if motivo is not None:
        return None, motivo
    if puntos is None:
        n = _ONDA3_PUNTOS_DEFECTO
    elif _RE_ONDA3_PUNTOS.fullmatch(puntos) and 1 <= int(puntos) <= _ONDA3_PUNTOS_MAX:
        n = int(puntos)
    else:
        return None, (f"`puntos` tiene que ser un entero de 1 a {_ONDA3_PUNTOS_MAX}; llegó "
                      f"{puntos[:40]!r}")
    if d is not None and d < 0:
        return None, f"`desde` no puede ser negativo; llegó {d:g}"
    if h is not None and h <= 0:
        return None, f"`hasta` tiene que ser mayor que 0; llegó {h:g}"
    if d is not None and h is not None and d >= h:
        return None, f"`desde` ({d:g} s) tiene que ser menor que `hasta` ({h:g} s)"
    return (d, h, n), None


def _onda3_rango(d: float | None, h: float | None, dur: float) -> tuple[tuple | None, str | None]:
    """El tramo contra la duración REAL del audio decodificado (la que dibuja la onda)."""
    d = 0.0 if d is None else d
    if h is not None and h > dur + _ONDA3_TOLERANCIA_S:
        return None, f"`hasta` ({h:g} s) está después del final del audio ({dur:.3f} s)"
    h = dur if h is None else min(h, dur)
    if d >= h:
        return None, (f"`desde` ({d:g} s) está en o después del final del audio ({dur:.3f} s)"
                      if d >= dur else f"`desde` ({d:g} s) tiene que ser menor que `hasta` "
                                       f"({h:g} s)")
    return (d, h), None


@app.get("/api/radio/tracks/{track_id}/onda3")
async def radio_onda3(track_id: str, desde: str | None = None, hasta: str | None = None,
                      puntos: str | None = None):
    """Las 3 bandas del audio real (`motor.bandas`) en el tramo pedido, y la grilla estimada.

    `bandas` trae `puntos` valores por banda en 0..1, RELATIVOS dentro del track (lo dice
    `normalizacion`). Si el tramo tiene menos cuadros de 10 ms que los puntos pedidos, vienen
    los cuadros tal cual y `puntos` dice cuántos: no se inventa resolución. `desde`/`hasta` de
    la respuesta son los bordes REALES de lo que se devolvió (los de los cuadros de 10 ms que
    cubren el pedido). `grilla` es None si el track no tiene BPM medido; si lo tiene,
    `grilla.primer_beat_s` puede ser None (con `motivo`) y aun así venir `grilla.bpm_afinado`
    (ver `motor.bandas.grilla`)."""
    params, motivo = _onda3_parametros(desde, hasta, puntos)
    if motivo is not None:
        return JSONResponse({"error": motivo}, status_code=400)
    d, h, n = params
    ruta, error = await _marcas_ruta(track_id)
    if error is not None:
        return error
    t, estado, motivo = await asyncio.to_thread(_usar_store_motor, lambda store: store.get(ruta))
    if estado != _RADIO_OK:
        return _sets_sin_base(estado, motivo)
    if t is None:
        # Estaba en el índice pero ya no en la base (un scan entre medio).
        return JSONResponse({"error": "track no encontrado en la biblioteca del motor"},
                            status_code=404)
    from motor.bandas import (
        BANDAS,
        TASA_HZ,
        UnreadableAudio,
        cached_bandas,
        grilla,
        normalizacion,
        tramo,
    )

    try:
        b, de_cache = await asyncio.to_thread(cached_bandas, ruta, Path(db.DATA_DIR) / "peaks")
    except FileNotFoundError:
        return JSONResponse({"error": f"el archivo de este track no está en esta máquina: la "
                                      f"base lo tiene en {ruta}. Se movió, o la base se escaneó "
                                      f"en otro sistema."}, status_code=404)
    except UnreadableAudio as e:
        return JSONResponse({"error": f"no pude leer el audio para dibujar la onda: {e}"},
                            status_code=422)
    except OSError as e:
        return JSONResponse({"error": f"no pude leer el archivo ({type(e).__name__})"},
                            status_code=409)
    except (RuntimeError, ValueError, ArithmeticError) as e:
        # Cinturón, como en /onda: un decodificador que falla de una forma no prevista.
        logger.warning(f"⚠️ Onda3: {type(e).__name__} leyendo {ruta}: {e}")
        return JSONResponse({"error": f"no pude leer el audio para dibujar la onda "
                                      f"({type(e).__name__})"}, status_code=422)
    rango, motivo = _onda3_rango(d, h, b.duration_s)
    if motivo is not None:
        return JSONResponse({"error": motivo}, status_code=400)
    d0, h0, q = tramo(b, rango[0], rango[1], n)
    bpm = _num(t.bpm)
    # BPM 0.0 = el análisis no encontró pulso: no es una medición y no hay grilla que estimar.
    g = await asyncio.to_thread(grilla, b, bpm) if bpm is not None and bpm > 0 else None
    return {"desde": round(d0, 3), "hasta": round(h0, 3),
            "duracion_audio": round(b.duration_s, 3), "puntos": int(q.shape[1]),
            "tasa_hz": TASA_HZ, "normalizacion": normalizacion(b),
            "bandas": {nombre: [_ONDA3_VALOR[v] for v in q[i].tolist()]
                       for i, nombre in enumerate(BANDAS)},
            "grilla": g, "cache": de_cache}


@app.get("/api/historial")
async def historial(limite: int = 20):
    """Historial persistido: búsquedas, playlists (modo lista) y descargas."""
    return await asyncio.to_thread(db.listar_historial, limite)


@app.get("/api/historial/playlist/{pid}")
async def historial_playlist(pid: int):
    """Devuelve una playlist guardada lista para renderizar en la vista 'lista'."""
    data = await asyncio.to_thread(db.get_playlist, pid)
    if not data:
        return JSONResponse({"exito": False, "mensaje": "Playlist no encontrada."}, status_code=404)
    return {"exito": True, "data": data}


@app.delete("/api/historial/playlist/{pid}")
async def historial_borrar_playlist(pid: int):
    ok = await asyncio.to_thread(db.borrar_playlist, pid)
    return {"exito": ok}


@app.delete("/api/historial")
async def historial_limpiar(que: str = "todo"):
    """Limpia el historial. `que` ∈ busquedas | playlists | descargas | todo."""
    ok = await asyncio.to_thread(db.limpiar_historial, que)
    return {"exito": ok}


# ============================================================
#  Mis Playlists (crates)
# ============================================================
def _metricas_bpm_motor(items_analizados: list[dict]) -> dict:
    """Las métricas de BPM con lo que sabe el motor (items ya pasados por `_analisis_items`):
    si midió algún tema, salen SOLO de lo medido (db.metricas_bpm no mezcla fuentes). La usan
    el crate y el rail, así los dos dicen lo mismo."""
    return db.metricas_bpm((it["analisis"].get("bpm"), it["analisis"].get("dato"))
                           for it in items_analizados)


def _listar_con_motor() -> list[dict]:
    """`db.listar_playlists` con las métricas de BPM del motor (una sola pasada por la base
    del motor para todos los temas de todas las playlists)."""
    ps = db.listar_playlists()
    items = {p["id"]: (db.get_playlist_mia(p["id"], True) or {}).get("items") or [] for p in ps}
    analizados, _ = _analisis_items([it for its in items.values() for it in its])
    por_id = {it["id"]: it for it in analizados}
    for p in ps:
        p.update(_metricas_bpm_motor([por_id[it["id"]] for it in items[p["id"]]]))
    return ps


@app.get("/api/playlists")
async def playlists_listar():
    return {"exito": True, "playlists": await asyncio.to_thread(_listar_con_motor)}


@app.get("/api/playlists/activa")
async def playlists_activa():
    return {"activa": await asyncio.to_thread(db.get_playlist_activa)}


@app.post("/api/playlists")
async def playlists_crear(payload: dict, request: Request):
    if (rechazo := _guarda_ajeno(request)):
        return rechazo
    nombre = (payload.get("nombre") or "").strip() or "Nueva playlist"
    p = await asyncio.to_thread(db.crear_playlist, nombre)
    return {"exito": bool(p), "playlist": p}


@app.get("/api/playlists/{pid}")
async def playlists_get(pid: int):
    data = await asyncio.to_thread(db.get_playlist_mia, pid, True)
    if not data:
        return JSONResponse({"exito": False, "mensaje": "Playlist no encontrada."}, status_code=404)
    # f53: qué sabe el motor de cada tema (estado del análisis, BPM/key medidos, id de la
    # radio, marcas). Necesita la ruta, que `_item_publico` saca antes de responder.
    data["items"], data["motor"] = await asyncio.to_thread(_analisis_items, data.get("items") or [])
    # Las métricas de BPM con lo que sabe el motor: si midió algún tema, el promedio y el rango
    # salen SOLO de lo medido (db.metricas_bpm no mezcla fuentes).
    data["metrics"] = {**(data.get("metrics") or {}), **_metricas_bpm_motor(data["items"])}
    # Cada item dice si se puede bajar desde acá y por qué no (f41): la pantalla muestra el
    # motivo que decide el server en vez de repetir el criterio.
    data["items"] = [_item_publico(it) for it in data.get("items") or []]
    return {"exito": True, "data": data}


@app.patch("/api/playlists/{pid}")
async def playlists_editar(pid: int, payload: dict, request: Request):
    if (rechazo := _guarda_ajeno(request)):
        return rechazo
    if "nombre" in payload:
        await asyncio.to_thread(db.renombrar_playlist, pid, payload.get("nombre") or "")
    if payload.get("activar"):
        await asyncio.to_thread(db.activar_playlist, pid)
    return {"exito": True}


@app.delete("/api/playlists/{pid}")
async def playlists_borrar(pid: int, request: Request):
    if (rechazo := _guarda_ajeno(request)):
        return rechazo
    return {"exito": await asyncio.to_thread(db.borrar_playlist_mia, pid)}


@app.post("/api/playlists/{pid}/items")
async def playlists_agregar_item(pid: int, payload: dict, request: Request):
    """Agrega un tema a la playlist.

    f53 (tomado de f33): un tema de la biblioteca local (la home) llega con `fuente:
    "biblioteca"` y `lib_id` (el id de /api/biblioteca): el SERVER resuelve su archivo con su
    propio índice (`_lib_audio`) y lo guarda en el item, así se puede exportar y analizar con el
    motor (antes quedaba sin archivo y "por bajar" para siempre). Una `ruta` (o `archivo`/
    `formato`) que venga en el cuerpo se IGNORA: este endpoint no tiene CORS y cualquier página
    abierta en el navegador puede pegarle; una ruta suya haría que el motor lea cualquier
    archivo de la PC. `con_archivo` dice si el item quedó con archivo local."""
    if (rechazo := _guarda_ajeno(request)):
        return rechazo
    track = payload.get("track") or payload
    if not isinstance(track, dict):
        return JSONResponse({"exito": False, "mensaje": "El tema tiene que ser un objeto."},
                            status_code=400)
    track = {k: v for k, v in track.items() if k not in ("ruta", "archivo", "formato")}
    ruta_local = None
    lib_id = track.get("lib_id")
    if str(track.get("fuente") or "").strip().casefold() == "biblioteca" and lib_id is not None:
        lib_id = str(lib_id)
        if lib_id not in _lib_audio:
            # Vacío (recién arrancó el server) o un id nuevo (el XML cambió): se relee.
            await asyncio.to_thread(_cargar_biblioteca)
        ruta_local = _lib_audio.get(lib_id)
    r = await asyncio.to_thread(db.agregar_item, pid, track, ruta_local)
    if r is None:
        return JSONResponse({"exito": False, "mensaje": "Playlist no encontrada."}, status_code=404)
    return {"exito": True, **r, "con_archivo": bool(ruta_local)}


@app.delete("/api/playlists/{pid}/items/{item_id}")
async def playlists_quitar_item(pid: int, item_id: int, request: Request):
    if (rechazo := _guarda_ajeno(request)):
        return rechazo
    return {"exito": await asyncio.to_thread(db.quitar_item, item_id)}


# --- Bajar desde la playlist (f41) ------------------------------------------------------
# El dueño guarda temas en una playlist y quiere bajarlos ahí mismo, sin volver a buscarlos.
# Mismo flujo que el buscador (`procesar_descarga`), con dos diferencias a propósito:
#  - qué se baja lo dice el ITEM guardado (url, fuente, título…), nunca el cliente: si el
#    endpoint aceptara una url, cualquier página abierta en el navegador podría hacerle bajar
#    cualquier cosa al server. Del cuerpo solo se lee el formato, y contra una lista cerrada;
#  - el archivo queda en ESE item (por id) y la playlist activa no se toca (`_hist_descarga`).

# Los del selector del buscador (frontend/src/utils.js, FORMATOS). AIFF no: yt-dlp no lo
# acepta como salida (ver el comentario de FORMATOS).
FORMATOS_DESCARGA = ("wav", "flac", "mp3")
SIN_LINK = "Sin link para bajar: el tema se guardó sin una fuente de internet (por ejemplo, desde tu biblioteca)."

# Items que se están bajando EN ESTE PROCESO (modo local): dos clicks seguidos no lanzan dos
# descargas del mismo archivo. Con Redis el trabajo corre en el worker y este registro no lo
# ve; ahí lo evita la pantalla (el botón queda ocupado mientras sigue el job).
_items_bajando: set[int] = set()
_items_bajando_lock = threading.Lock()
# Con Redis: la reserva del item vence sola un poco después del tope del job (jobs.encolar,
# 900 s), por si el worker muere sin soltarla; un item no queda trabado para siempre.
RESERVA_ITEM_S = 960


def _clave_item(item_id: int) -> str:
    return f"musiflix:bajando-item:{item_id}"


SOLO_PREVIEW = ("Solo hay un fragmento de 30 s: SoundCloud no deja bajar el tema completo (Go+). "
                "Bajarlo sería guardar un recorte como si fuera el tema.")
LINK_INTERNO = ("El link guardado apunta a una dirección interna (esta máquina o la red local), "
                "no a un sitio de música: no lo bajo.")

# Fuentes de MP3 directo: el server baja la url TAL CUAL con `requests` (`_descargar_directo`),
# así que la url tiene que ser de ese sitio. Los dominios no son los de la búsqueda
# (web.ligaudio.ru, box.hitplayer.ru): los links de descarga que arma scrapers.py apuntan a
# otros servidores. Medido (oct-2026) con búsquedas reales y con el historial de descargas:
# ligaudio → storageN.lightaudio.ru; hitplayer → dN.hotplayer.ru. Se aceptan los dos dominios
# de cada sitio y sus subdominios. Si un sitio cambia de servidor, el item muestra el motivo
# (no un error genérico) y esta lista se actualiza.
DOMINIOS_DIRECTOS = {"ligaudio": ("ligaudio.ru", "lightaudio.ru"),
                     "hitplayer": ("hitplayer.ru", "hotplayer.ru")}


def _ip_literal(host: str):
    """El host como IP si es una IP escrita a mano (incluye las formas raras que igual resuelven
    a una IP: "2130706433", "127.1", "0x7f.0.0.1"), o None si es un nombre."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if re.fullmatch(r"[0-9a-fA-Fx.]+", host) and any(ch.isdigit() for ch in host):
        try:
            return ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def _ip_interna(ip) -> bool:
    """Loopback, red privada, link-local, reservada, multicast o sin especificar."""
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def _host_interno(host: str) -> bool:
    """Chequeo LITERAL (sin DNS): localhost, *.localhost, *.local, *.internal o una IP interna."""
    h = host.lower().rstrip(".")
    if h in ("localhost", "") or h.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        return True
    ip = _ip_literal(h)
    return ip is not None and _ip_interna(ip)


def _resolver_host(host: str) -> list[str]:
    """Las IPs a las que resuelve el host (DNS). Aparte para que los tests no salgan a la red."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _resuelve_a_interna(host: str) -> bool:
    """¿Algún registro DNS del host es una IP interna? (un nombre público que apunta a 127.0.0.1).

    Límite, documentado: es un chequeo en el momento de pedir la descarga. yt-dlp/requests
    vuelven a resolver después (un DNS que cambia de respuesta en el medio — rebinding — no se
    ataja) y una redirección HTTP hacia una IP interna tampoco. Para eso haría falta fijar la IP
    en la conexión, que ni yt-dlp ni `_descargar_directo` permiten sin reescribirlos. Si el DNS
    no contesta, no se bloquea: la descarga va a fallar sola, con su motivo."""
    try:
        ips = _resolver_host(host)
    except OSError:
        return False
    for txt in ips:
        try:
            if _ip_interna(ipaddress.ip_address(txt.split("%")[0])):
                return True
        except ValueError:
            continue
    return False


def _motivo_no_bajable(item: dict) -> str | None:
    """Por qué el item no se puede bajar, o None si se puede. Sin DNS (se llama por cada item
    al mostrar la playlist); la resolución se mira al bajar (`_motivo_al_bajar`).

    Mismo criterio que la pantalla usaba para `descargable` (fuente y url): los temas que se
    agregan desde la home son archivos locales del dueño y se guardan sin fuente ni url. Un Go+
    de SoundCloud solo da 30 s. La url tiene que ser http(s) (lo único que bajan yt-dlp y la
    descarga directa), no puede apuntar a esta máquina ni a la red local (el server la pediría
    por quien guardó el item: SSRF), y la de una fuente de MP3 directo tiene que ser de ese sitio."""
    url = (item.get("url") or "").strip()
    fuente = (item.get("fuente") or "").strip().lower()
    if not fuente or not url:
        return SIN_LINK
    if item.get("solo_preview"):
        return SOLO_PREVIEW
    if not re.match(r"https?://", url, re.IGNORECASE):
        return "El link guardado no es una dirección web (http/https): no lo puedo bajar."
    try:
        host = (urlsplit(url).hostname or "")
    except ValueError:
        host = ""
    if not host:
        return "El link guardado no tiene un sitio válido: no lo puedo bajar."
    # Solo los caracteres de un nombre de sitio o de una IP. `urlsplit` no decodifica "%xx" pero
    # requests/urllib (yt-dlp, la descarga directa) sí: "%31%32%37.0.0.1" pasaba los chequeos
    # literal y de DNS (que no resolvía) y terminaba conectando a 127.0.0.1 (auditoría f41-r2).
    # Tampoco Unicode: un sitio de verdad llega acá en ASCII.
    if not re.fullmatch(r"[A-Za-z0-9.\-]+|[0-9A-Fa-f:.]+", host):
        return "El link guardado tiene un sitio con caracteres raros: no lo bajo."
    if _host_interno(host):
        return LINK_INTERNO
    dominios = DOMINIOS_DIRECTOS.get(fuente)
    if dominios and not any(host == d or host.endswith("." + d) for d in dominios):
        return (f"El link guardado no es de {fuente} (se esperaba un servidor de "
                f"{' o '.join(dominios)}, es {host}): no lo bajo.")
    return None


def _motivo_al_bajar(item: dict) -> str | None:
    """`_motivo_no_bajable` más lo que necesita DNS: el host no puede resolver a una IP interna.
    Spotify/Deezer no bajan su url (se busca el equivalente en YouTube): no se resuelve."""
    motivo = _motivo_no_bajable(item)
    if motivo:
        return motivo
    if (item.get("fuente") or "").strip().lower() in ("spotify", "deezer"):
        return None
    host = urlsplit((item.get("url") or "").strip()).hostname or ""
    return LINK_INTERNO if _resuelve_a_interna(host) else None


def _item_publico(item: dict | None) -> dict | None:
    """El item como lo ve la pantalla: sin la ruta en disco y con el motivo si no se puede bajar."""
    if not item:
        return None
    out = {k: v for k, v in item.items() if k != "ruta"}
    out["motivo_no_bajable"] = None if item.get("descargado") else _motivo_no_bajable(item)
    return out


def descargar_item_playlist(pid: int, item_id: int, formato: str) -> dict:
    """Baja el item `item_id` de la playlist `pid` con el flujo del buscador. SÍNCRONO: lo
    llaman el worker (tasks.descargar_item_job) y el endpoint en modo local.

    Devuelve lo mismo que `procesar_descarga` más `item` (cómo quedó en la base). Un `exito`
    que no quedó anotado en la playlist se informa como fallo: decir "listo" de un tema que la
    playlist sigue mostrando como "falta bajar" es un dato que miente."""
    item = db.get_item(pid, item_id)
    if not item:
        return {"exito": False, "mensaje": "El tema ya no está en esta playlist.", "item": None,
                "quitado": True}
    # El worker corre esto un rato después de que el endpoint chequeó: entre tanto pudo haberse
    # bajado por otro camino (C1). Bajado = con el archivo en disco (`db._tiene_archivo`).
    if item.get("descargado"):
        return {"exito": False, "mensaje": "Este tema ya está descargado.", "item": _item_publico(item)}
    motivo = _motivo_al_bajar(item)
    if motivo:
        return {"exito": False, "mensaje": motivo, "item": _item_publico(item)}
    payload = {k: item.get(k) for k in ("titulo", "artista", "fuente", "url", "thumbnail",
                                        "duracion", "bpm", "camelot", "genero")}
    # Un link pegado con espacios alrededor (C3) se baja igual; la fuente se compara exacta.
    payload["url"] = (payload.get("url") or "").strip()
    payload["fuente"] = (payload.get("fuente") or "").strip().lower()
    payload["formato"] = formato
    try:
        res = procesar_descarga(payload, destino={"playlist_id": pid, "item_id": item_id})
    except Exception as e:   # procesar_descarga atrapa lo de la red; esto es lo que se le escapa
        logger.error(f"❌ Error inesperado bajando «{item.get('titulo')}»: {type(e).__name__}: {e}")
        res = {"exito": False, "mensaje": f"Error inesperado al bajar ({type(e).__name__}): {e}"[:300]}
    despues = db.get_item(pid, item_id)
    if res.get("exito") and not (despues and despues.get("descargado")):
        res = {**res, "exito": False,
               "mensaje": f"Se bajó {res.get('archivo') or 'el archivo'} pero no quedó anotado en la "
                          "playlist (¿quitaste el tema mientras bajaba?)."}
    out = {**res, "item": _item_publico(despues)}
    if despues is None:
        out["quitado"] = True       # la fila ya no existe: la pantalla no puede mostrar el motivo ahí
    return out


# --- Contra pedidos de otras páginas (CSRF) ----------------------------------------------
# El server no tiene login ni CORS: cualquier página abierta en el navegador del dueño puede
# mandarle un POST. Un <form method=POST> o un fetch no-cors con text/plain no disparan
# preflight, así que el navegador lo envía igual (no puede leer la respuesta, pero el efecto,
# una descarga real, ya pasó). Para lo que dispara trabajo se exige:
#  - Content-Type: application/json. Un form o un text/plain no lo pueden poner sin preflight,
#    y el preflight falla porque no hay CORS (415 si no);
#  - si el navegador dice de dónde viene: Sec-Fetch-Site no "cross-site", y Origin = este
#    server (mismo host:puerto que pidió el navegador). Si no vienen (curl, TestClient, un
#    navegador viejo) no se exige: lo que protege ahí es el Content-Type.
# El proxy de Vite en desarrollo tiene que dejar el Host como lo pidió el navegador: Vite 8
# pone changeOrigin: true si el proxy se escribe como string (Host = 127.0.0.1:8000 y Origin =
# localhost:5173 → 403). Por eso frontend/vite.config.js lo declara con changeOrigin: false.

def _origen_ajeno(request: Request) -> str | None:
    """Por qué el pedido viene de otra página, o None si es de la propia app (o no se sabe)."""
    sitio = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sitio == "cross-site":
        return "cross-site"
    origen = request.headers.get("origin")
    if origen is None:
        return None
    host = (request.headers.get("host") or "").strip().lower()
    try:
        netloc = urlsplit(origen.strip()).netloc.lower()
    except ValueError:
        netloc = ""
    if not netloc or netloc != host:
        return f"Origin {origen[:80]}"
    return None


def _guarda_ajeno(request: Request) -> JSONResponse | None:
    """403 si el pedido viene de otra página (ver `_origen_ajeno`); None si es de la app. Para
    los endpoints que escriben y ya leen su cuerpo con FastAPI (las playlists propias, f53)."""
    ajeno = _origen_ajeno(request)
    if not ajeno:
        return None
    logger.warning(f"🛡️ Rechazado un pedido de otra página ({ajeno}) a {request.url.path}")
    return JSONResponse(_RECHAZO_AJENO, status_code=403)


async def _cuerpo_json_propio(request: Request) -> tuple[dict | None, JSONResponse | None]:
    """El cuerpo JSON de un pedido de la propia app, o la respuesta de rechazo (403/415/400)."""
    ajeno = _origen_ajeno(request)
    if ajeno:
        logger.warning(f"🛡️ Rechazado un pedido de otra página ({ajeno}) a {request.url.path}")
        return None, JSONResponse({"exito": False, "mensaje": "Pedido rechazado: no viene de MusiFlix."},
                                  status_code=403)
    tipo = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if tipo != "application/json":
        return None, JSONResponse({"exito": False, "mensaje": "El pedido tiene que ser JSON "
                                   "(Content-Type: application/json)."}, status_code=415)
    try:
        cuerpo = await request.json()
    except Exception:
        return None, JSONResponse({"exito": False, "mensaje": "El cuerpo no es JSON válido."}, status_code=400)
    if not isinstance(cuerpo, dict):
        return None, JSONResponse({"exito": False, "mensaje": "El cuerpo tiene que ser un objeto JSON."},
                                  status_code=400)
    return cuerpo, None


@app.post("/api/playlists/{pid}/items/{item_id}/descargar")
async def playlists_descargar_item(pid: int, item_id: int, request: Request):
    """Baja un tema guardado en la playlist. Cuerpo JSON: {"formato": "wav"|"flac"|"mp3"} ({}
    = wav). Cualquier otra clave (url, ruta…) se ignora: lo que se baja sale del item guardado.
    403 pedido de otra página · 415 no es JSON · 400 JSON roto, formato inválido o item que no se
    puede bajar (con el motivo) · 404 playlist o item ajeno · 409 ya bajado (con archivo) o
    bajándose. Con Redis encola (como /api/descargar) y contesta {encolado, job_id}; sin Redis,
    el resultado."""
    cuerpo, rechazo = await _cuerpo_json_propio(request)
    if rechazo:
        return rechazo
    formato = str(cuerpo.get("formato") or "wav").lower()
    if formato not in FORMATOS_DESCARGA:
        return JSONResponse({"exito": False, "mensaje": f"Formato «{formato}» no válido: elegí "
                             + ", ".join(f.upper() for f in FORMATOS_DESCARGA) + "."}, status_code=400)
    item = await asyncio.to_thread(db.get_item, pid, item_id)
    if not item:
        existe = await asyncio.to_thread(db.get_playlist_mia, pid)
        mensaje = "Ese tema no está en esta playlist." if existe else "Playlist no encontrada."
        return JSONResponse({"exito": False, "mensaje": mensaje, "quitado": bool(existe)}, status_code=404)
    if item.get("descargado"):
        return JSONResponse({"exito": False, "mensaje": "Este tema ya está descargado.",
                             "item": _item_publico(item)}, status_code=409)
    motivo = await asyncio.to_thread(_motivo_al_bajar, item)
    if motivo:
        return JSONResponse({"exito": False, "mensaje": motivo, "item": _item_publico(item)},
                            status_code=400)
    if jobs.queue_disponible():
        # Con worker, `_items_bajando` (de este proceso) no ve el job: la reserva vive en Redis
        # (SET NX con vencimiento) y la suelta el job al terminar (tasks.descargar_item_job).
        clave = _clave_item(item_id)
        if not jobs.reservar(clave, RESERVA_ITEM_S):
            return JSONResponse({"exito": False, "mensaje": "Este tema ya se está bajando."}, status_code=409)
        try:
            import tasks
            job = jobs.encolar(tasks.descargar_item_job, pid, item_id, formato)
            return {"encolado": True, "job_id": job.id}
        except Exception as e:
            jobs.liberar(clave)
            return JSONResponse({"exito": False, "mensaje": f"No pude encolar la descarga: {e}"[:300]},
                                status_code=503)
    with _items_bajando_lock:
        if item_id in _items_bajando:
            return JSONResponse({"exito": False, "mensaje": "Este tema ya se está bajando."}, status_code=409)
        _items_bajando.add(item_id)
    try:
        return await asyncio.to_thread(descargar_item_playlist, pid, item_id, formato)
    finally:
        with _items_bajando_lock:
            _items_bajando.discard(item_id)


@app.post("/api/playlists/{pid}/orden")
async def playlists_reordenar(pid: int, payload: dict, request: Request):
    if (rechazo := _guarda_ajeno(request)):
        return rechazo
    return {"exito": await asyncio.to_thread(db.reordenar, pid, payload.get("orden") or [])}


@app.post("/api/playlists/{pid}/export")
async def playlists_export(pid: int, request: Request):
    if (rechazo := _guarda_ajeno(request)):
        return rechazo
    recibo = await asyncio.to_thread(db.armar_m3u8, pid)
    if not recibo:
        return JSONResponse({"exito": False, "mensaje": "No pude exportar la playlist."}, status_code=502)
    logger.info(f"📃 Playlist exportada: {recibo['archivo']} ({recibo['incluidos']} temas)")
    return {"exito": True, **recibo}


# ============================================================
#  Playlists como centro (f53): importar de Rekordbox o de una carpeta, editar el género y
#  el puente con el motor (análisis en segundo plano + estado de cada tema)
# ============================================================
#   POST  /api/importar/rekordbox/leer          XML crudo (application/xml | text/xml) o
#                                               {"usar_configurado": true} → resumen + token
#   POST  /api/importar/rekordbox               {token, playlists: [ids], actualizar?}
#   GET   /api/importar/carpetas?raiz=&ruta=    raíces (sin raiz) o subcarpetas con sus audios
#   POST  /api/importar/carpeta                 {raiz, ruta, recursivo?, actualizar?}
#   PATCH /api/playlists/{pid}/items/{item}     {genero}
#   POST  /api/playlists/{pid}/genero           {genero}: a todos los que no tienen
#   POST  /api/playlists/{pid}/analizar         {} → encola lo que el motor no tiene vigente
#   GET   /api/playlists/{pid}/analisis         progreso del análisis
#
# Lo que escribe o dispara trabajo pasa por la defensa contra otras páginas (`_origen_ajeno`
# + tipo de contenido: JSON, o XML en la lectura). Nunca 500: 400/403/404/409/410/413/415 con
# el motivo en `mensaje`. Las respuestas no llevan rutas absolutas de la PC: nombres de
# archivo, de playlist y carpetas relativas a su raíz. La lógica de lectura está en
# `playlist_import.py` y la cola de análisis en `playlist_analisis.py`.

import playlist_analisis  # noqa: E402
import playlist_import  # noqa: E402

_sesiones_xml = playlist_import.Sesiones()
_RE_ID_URL = re.compile(r"[0-9]{1,12}")
_RECHAZO_AJENO = {"exito": False, "mensaje": "Pedido rechazado: no viene de MusiFlix."}


def _raices() -> list[str]:
    """Las carpetas de música permitidas (MUSIFLIX_LIBRARY_ROOTS). Función para que los tests
    puedan cambiarlas sin recargar el módulo."""
    return list(_LIB_ROOTS)


def _sin_raices_motivo() -> str:
    return ("No hay carpetas de música configuradas, así que no puedo buscar tus archivos. Poné "
            "en MUSIFLIX_LIBRARY_ROOTS las carpetas donde tenés los temas (separadas por ';' en "
            "Windows) y reiniciá el servidor.")


def _rechazo(e: "playlist_import.Rechazo") -> JSONResponse:
    return JSONResponse({"exito": False, "mensaje": e.mensaje}, status_code=e.status)


def _id_url(texto: str) -> int | None:
    return int(texto) if _RE_ID_URL.fullmatch(texto or "") else None


# --- el motor: análisis en segundo plano -----------------------------------------------------

def _analizar_y_guardar(ruta: str) -> str | None:
    """Lo que hace el hilo con UN archivo: el análisis del scan y el upsert en la base del motor
    (creándola si no existe, como `scan`). None si anduvo, el motivo si no."""
    from motor.cli import analizar_para_guardar, db_por_defecto, esta_bloqueada, guardar_analisis
    from motor.store import EsquemaIncompatible, Store

    res = analizar_para_guardar(ruta)
    if isinstance(res, str):
        return res
    try:
        with Store(db_por_defecto()) as store:
            guardar_analisis(store, ruta, res)      # licencia y origen: "no declarado"
    except EsquemaIncompatible:
        return "la base del motor es de otra versión: no la toco"
    except sqlite3.OperationalError as e:
        return ("la base del motor está ocupada (un scan u otra instancia): reintentá en un rato"
                if esta_bloqueada(e) else f"no pude escribir en la base del motor ({e})")
    return None


def _al_guardar(ruta: str) -> None:
    """El editor de cues abre el tema por su id de la radio: queda en el índice ya, sin esperar
    a la próxima recarga de la biblioteca (`_RADIO_RECARGA_MIN_S`)."""
    _radio_audio[_radio_id(Path(ruta))] = str(Path(ruta))


_analizador = playlist_analisis.AnalizadorFondo(_analizar_y_guardar, _al_guardar)


def _marcas_por_clase(store, rutas: list[str]) -> dict[str, dict]:
    """{clave del store: {total, pads, memory, loop}} — los puntos de color de la tabla. Primero
    `cue_mark_counts` (una consulta para todos); el detalle solo de los que tienen marcas."""
    from motor.store import Store as _S

    conteos = store.cue_mark_counts(rutas)
    out = {}
    for r in rutas:
        k = _S._key(r)
        if not conteos.get(k):
            continue
        marcas = store.list_cue_marks(r)
        out[k] = {"total": len(marcas),
                  "pads": sorted(m.num for m in marcas if m.kind == "cue" and m.num is not None),
                  "memory": sum(1 for m in marcas if m.kind == "memory"),
                  "loop": sum(1 for m in marcas if m.kind == "loop")}
    return out


def _dato_externo(it: dict) -> dict:
    """BPM/key que NO midió el motor: los del XML de Rekordbox (o de la búsqueda), marcados."""
    camelot = it.get("camelot") or None
    tonalidad = None
    if camelot:
        try:
            from motor.tonalidad import camelot_a_clasica
            tonalidad = camelot_a_clasica(camelot) or None
        except ImportError:
            tonalidad = None
    dato = None
    if it.get("bpm") or camelot:
        dato = "rekordbox" if (it.get("fuente") or "").lower() in ("rekordbox", "biblioteca") else "otro"
    return {"bpm": it.get("bpm"), "camelot": camelot, "tonalidad": tonalidad,
            "key_dudosa": False, "dato": dato}


_MOTIVO_ARCHIVO = {
    "no-existe": "No encuentro el archivo: se movió o se borró desde que se agregó.",
    "no-encontrado": "No encuentro el archivo en tus carpetas de música.",
    "sin-archivo": "Sin archivo en la PC: no hay nada que analizar todavía.",
}


def _analisis_items(items: list[dict]) -> tuple[list[dict], dict]:
    """Cada item con su `analisis` (estado, motivo, radio_id, BPM/key y de dónde salen,
    marcas), y el estado de la base del motor. Nunca levanta por la base: sin base o con la
    base ocupada los temas quedan "pendiente" y `motor.motivo` dice por qué."""
    con_archivo = [it for it in items if it.get("archivo_estado") == "ok" and it.get("ruta")]

    def leer(store):
        vistos = {}
        for it in con_archivo:
            ruta = os.path.abspath(it["ruta"])
            f = store.get_features(ruta)
            vistos[it["id"]] = (f, f is not None and not store.needs_analysis(ruta))
        return vistos, _marcas_por_clase(store, [os.path.abspath(it["ruta"]) for it in con_archivo])

    vistos, marcas, estado, motivo = {}, {}, _RADIO_OK, None
    try:
        from motor.store import Store as _S
    except ImportError:
        # Sin motor (imagen sin motor/): cada tema muestra lo que trajo su origen.
        _, estado, motivo = _usar_store_motor(lambda store: None)
        return ([{**it, "analisis": {"estado": "pendiente" if it.get("archivo_estado") == "ok"
                                     else "sin-archivo", "motivo": motivo, "radio_id": None,
                                     "marcas": None, **_dato_externo(it)}} for it in items],
                _radio_envoltura(estado, motivo))
    if con_archivo:
        res, estado, motivo = _usar_store_motor(leer)
        if estado == _RADIO_OK:
            vistos, marcas = res

    out = []
    for it in items:
        a = {"estado": "sin-archivo", "motivo": None, "radio_id": None, "marcas": None,
             **_dato_externo(it)}
        ae = it.get("archivo_estado")
        if ae == "ambiguo":
            n = it.get("homonimos") or 2
            a["motivo"] = (f"Hay {n} archivos con ese nombre en tus carpetas y no elijo uno a la "
                           f"suerte: dejá uno solo o renombrá los otros, y actualizá la importación.")
        elif ae != "ok":
            a["motivo"] = _MOTIVO_ARCHIVO.get(ae)
        else:
            ruta = os.path.abspath(it["ruta"])
            f, vigente = vistos.get(it["id"], (None, False))
            en_curso = _analizador.estado_de(ruta)
            if en_curso:
                a["estado"] = en_curso
            elif vigente:
                a["estado"] = "analizado"
            elif (m := _analizador.motivo_fallo(ruta)):
                a["estado"], a["motivo"] = "fallo", m
            else:
                a["estado"] = "pendiente"
                if f is not None:
                    a["motivo"] = "El archivo cambió desde que se analizó: hay que volver a analizarlo."
            # f56: las marcas son del archivo, no del análisis: las importadas de Rekordbox se
            # ven (puntos de color) aunque el motor todavía no lo haya medido.
            if estado == _RADIO_OK:
                a["marcas"] = marcas.get(_S._key(ruta), {"total": 0, "pads": [], "memory": 0, "loop": 0})
            if vigente and f is not None:
                from motor.cli import key_dudosa
                from motor.tonalidad import camelot_a_clasica
                rid = _radio_id(Path(ruta))
                _radio_audio.setdefault(rid, str(Path(ruta)))
                bpm = _num(f.bpm)
                a.update({"radio_id": rid,
                          "bpm": round(bpm, 1) if bpm is not None and bpm > 0 else None,
                          "camelot": f.key or None, "tonalidad": camelot_a_clasica(f.key) or None,
                          "key_dudosa": key_dudosa(f.key_acuerdo), "dato": "motor",
                          "marcas": marcas.get(_S._key(ruta),
                                               {"total": 0, "pads": [], "memory": 0, "loop": 0})})
        out.append({**it, "analisis": a})
    return out, _radio_envoltura(estado, motivo)


@app.post("/api/playlists/{pid}/analizar")
async def playlists_analizar(pid: str, request: Request):
    """Encola en el análisis de fondo los temas de la playlist que tienen archivo y que el motor
    no tiene vigente (`needs_analysis`). Idempotente. 409 si la base del motor no se puede usar
    (ocupada, de otra versión, ilegible) o no hay motor."""
    cuerpo, rechazo = await _cuerpo_json_propio(request)
    if rechazo:
        return rechazo
    p = _id_url(pid)
    data = await asyncio.to_thread(db.get_playlist_mia, p, True) if p is not None else None
    if not data:
        return JSONResponse({"exito": False, "mensaje": "Playlist no encontrada."}, status_code=404)

    def preparar():
        try:
            from motor.cli import db_por_defecto
            from motor.store import Store
        except ImportError:
            return None, _RADIO_SIN_MOTOR, ("Esta instalación no incluye el motor (motor/): no "
                                            "se pueden analizar temas.")
        base = db_por_defecto()
        if not base.exists():
            try:                                   # la crea, como `scan`
                Store(base, espera_bloqueo_s=_RADIO_ESPERA_S).close()
            except (sqlite3.Error, OSError) as e:
                return None, _RADIO_ILEGIBLE, f"No pude crear la base del motor: {e}"
        rutas = [(it["id"], os.path.abspath(it["ruta"]),
                  " — ".join(x for x in (it.get("artista"), it.get("titulo")) if x) or "tema")
                 for it in data["items"] if it.get("archivo_estado") == "ok" and it.get("ruta")]
        return _usar_store_motor(lambda store: [t for t in rutas if store.needs_analysis(t[1])])

    pendientes, estado, motivo = await asyncio.to_thread(preparar)
    if estado != _RADIO_OK:
        return JSONResponse({"exito": False, "mensaje": motivo, "estado": estado}, status_code=409)
    progreso = _analizador.encolar(p, pendientes)
    return {"exito": True, "encolados": progreso.pop("encolados"), "progreso": progreso}


@app.get("/api/playlists/{pid}/analisis")
async def playlists_analisis(pid: str):
    """Cómo va el análisis de la playlist: corriendo, hechos/total, tema actual y fallidos (con
    el motivo, sin rutas)."""
    p = _id_url(pid)
    if p is None or not await asyncio.to_thread(db.get_playlist_mia, p):
        return JSONResponse({"exito": False, "mensaje": "Playlist no encontrada."}, status_code=404)
    return {"exito": True, "progreso": _analizador.progreso(p)}


# --- editar el género ------------------------------------------------------------------------

@app.patch("/api/playlists/{pid}/items/{item_id}")
async def playlists_editar_item(pid: str, item_id: str, request: Request):
    """Cambia el género de UN tema: {"genero": "Techno"} ("" o null = sin género). Queda
    marcado como editado a mano: actualizar la importación no lo pisa."""
    cuerpo, rechazo = await _cuerpo_json_propio(request)
    if rechazo:
        return rechazo
    sobran = sorted(set(cuerpo) - {"genero"})
    if sobran or "genero" not in cuerpo:
        return JSONResponse({"exito": False, "mensaje": "Solo se puede cambiar el género "
                             "({\"genero\": \"...\"})."}, status_code=400)
    g = cuerpo["genero"]
    if g is not None and not isinstance(g, str):
        return JSONResponse({"exito": False, "mensaje": "El género tiene que ser un texto."},
                            status_code=400)
    p, i = _id_url(pid), _id_url(item_id)
    item = (await asyncio.to_thread(db.editar_genero_item, p, i, g)
            if p is not None and i is not None else None)
    if item is None:
        return JSONResponse({"exito": False, "mensaje": "Ese tema no está en esta playlist."},
                            status_code=404)
    return {"exito": True, "item": _item_publico(item)}


@app.post("/api/playlists/{pid}/genero")
async def playlists_genero_a_todos(pid: str, request: Request):
    """Pone el mismo género a todos los temas de la playlist que NO tienen uno."""
    cuerpo, rechazo = await _cuerpo_json_propio(request)
    if rechazo:
        return rechazo
    g = cuerpo.get("genero")
    if not isinstance(g, str) or db.sanear_genero(g) is None:
        return JSONResponse({"exito": False, "mensaje": "Escribí el género que querés poner."},
                            status_code=400)
    p = _id_url(pid)
    n = await asyncio.to_thread(db.genero_a_los_sin_genero, p, g) if p is not None else None
    if n is None:
        return JSONResponse({"exito": False, "mensaje": "Playlist no encontrada."}, status_code=404)
    return {"exito": True, "cambiados": n, "genero": db.sanear_genero(g)}


# --- importar desde Rekordbox ------------------------------------------------------------------

_TIPOS_XML = ("application/xml", "text/xml")


async def _leer_cuerpo_xml(request: Request) -> bytes:
    """El cuerpo crudo con tope (`XML_MAX_BYTES`): se corta al pasarse, sin juntarlo entero."""
    tope = playlist_import.XML_MAX_BYTES
    largo = request.headers.get("content-length")
    if largo and largo.isdigit() and int(largo) > tope:
        raise playlist_import.Rechazo(413, f"El XML pesa más de {tope // (1024 * 1024)} MB: no lo leo.")
    partes, total = [], 0
    async for trozo in request.stream():
        total += len(trozo)
        if total > tope:
            raise playlist_import.Rechazo(413, f"El XML pesa más de {tope // (1024 * 1024)} MB: no lo leo.")
        partes.append(trozo)
    return b"".join(partes)


def _nombre_xml(request: Request) -> str:
    """El nombre del archivo elegido (cabecera X-Nombre-Archivo, url-encoded): solo el nombre."""
    from urllib.parse import unquote

    crudo = unquote(request.headers.get("x-nombre-archivo") or "")[:200]
    nombre = playlist_import.nombre_de_ruta(crudo.replace("\\", "/")) if crudo else ""
    nombre = re.sub(r"[\x00-\x1f]", "", nombre).strip()
    return nombre or "rekordbox.xml"


def _leer_y_resolver(datos: bytes, nombre: str) -> dict:
    col = playlist_import.leer_rekordbox(datos, nombre)
    raices = _raices()
    playlist_import.resolver_coleccion(col, raices)
    token = _sesiones_xml.guardar(col)
    playlists = []
    for p in col.playlists:
        r = playlist_import.resumen_playlist(col, p)
        r["ya_importada"] = db.buscar_importada("rekordbox", p["ruta"])
        playlists.append(r)
    return {"exito": True, "token": token, "archivo": col.nombre_xml,
            "temas": sum(1 for t in col.tracks.values() if t["rb_track_id"]),
            "playlists": playlists, "raices_configuradas": bool(raices),
            "motivo_raices": None if raices else _sin_raices_motivo()}


@app.post("/api/importar/rekordbox/leer")
async def importar_rekordbox_leer(request: Request):
    """Lee un XML de Rekordbox y devuelve qué playlists trae y cuántos archivos se encontraron
    de cada una, más un `token` para importarlas (vale unos minutos, en memoria).

    El XML llega como CUERPO CRUDO (Content-Type application/xml o text/xml; nombre en
    X-Nombre-Archivo) o, con {"usar_configurado": true} en JSON, se lee el de
    MUSIFLIX_LIBRARY_XML."""
    ajeno = _origen_ajeno(request)
    if ajeno:
        logger.warning(f"🛡️ Rechazado un pedido de otra página ({ajeno}) a {request.url.path}")
        return JSONResponse(_RECHAZO_AJENO, status_code=403)
    tipo = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    try:
        if tipo in _TIPOS_XML:
            datos, nombre = await _leer_cuerpo_xml(request), _nombre_xml(request)
        elif tipo == "application/json":
            cuerpo, rechazo = await _cuerpo_json_propio(request)
            if rechazo:
                return rechazo
            if cuerpo.get("usar_configurado") is not True:
                return JSONResponse({"exito": False, "mensaje": "Mandá el XML o "
                                     "{\"usar_configurado\": true}."}, status_code=400)
            if not _LIB_XML:
                return JSONResponse({"exito": False, "mensaje": "No hay un XML configurado "
                                     "(MUSIFLIX_LIBRARY_XML): elegí el archivo."}, status_code=409)
            ruta = Path(_LIB_XML)
            try:
                if ruta.stat().st_size > playlist_import.XML_MAX_BYTES:
                    raise playlist_import.Rechazo(413, "El XML configurado es demasiado grande.")
                datos = await asyncio.to_thread(ruta.read_bytes)
            except OSError:
                return JSONResponse({"exito": False, "mensaje": "No pude leer el XML configurado "
                                     "(MUSIFLIX_LIBRARY_XML): revisá que exista."}, status_code=409)
            nombre = ruta.name
        else:
            return JSONResponse({"exito": False, "mensaje": "Mandá el XML (Content-Type: "
                                 "application/xml) o JSON."}, status_code=415)
        return await asyncio.to_thread(_leer_y_resolver, datos, nombre)
    except playlist_import.Rechazo as e:
        return _rechazo(e)
    except ImportError:
        return JSONResponse({"exito": False, "mensaje": "Esta instalación no incluye el lector "
                             "de Rekordbox (ground_truth)."}, status_code=409)
    except Exception as e:  # noqa: BLE001 — un XML raro no es un 500
        logger.warning(f"⚠️ Importar: no pude leer el XML: {type(e).__name__}: {e}")
        return JSONResponse({"exito": False, "mensaje": f"No pude leer el XML ({type(e).__name__})."},
                            status_code=400)


# Buscar-y-crear tiene que ser atómico: dos pedidos a la vez (doble clic, dos pestañas) creaban
# la misma playlist dos veces. Un solo lock alcanza: importar es raro y corre en un hilo.
_LOCK_IMPORTAR = threading.Lock()


def _importar_o_actualizar(origen: str, ref: str, nombre: str, archivo: str | None,
                           temas: list[dict], actualizar: bool) -> dict:
    with _LOCK_IMPORTAR:
        return _importar_o_actualizar_sin_lock(origen, ref, nombre, archivo, temas, actualizar)


def _importar_o_actualizar_sin_lock(origen: str, ref: str, nombre: str, archivo: str | None,
                                    temas: list[dict], actualizar: bool) -> dict:
    previa = db.buscar_importada(origen, ref)
    if previa and not actualizar:
        return {"estado": "ya-importada", "id": previa["id"], "nombre": previa["nombre"]}
    if previa:
        r = db.actualizar_importada(previa["id"], origen, temas, archivo)
        if r is None:
            return {"estado": "error", "mensaje": "No pude actualizar la playlist."}
        return {"estado": "actualizada", **r}
    r = db.crear_importada(nombre, origen, ref, archivo, temas)
    if r is None:
        return {"estado": "error", "mensaje": "No pude guardar la playlist."}
    return {"estado": "creada", **r}


@app.post("/api/importar/rekordbox")
async def importar_rekordbox(request: Request):
    """Importa las playlists elegidas de un XML ya leído: {token, playlists: [ids],
    actualizar?: bool, pisar_cues?: bool}. Una playlist ya importada (misma ruta adentro de
    Rekordbox) no se crea de nuevo: vuelve "ya-importada", y con `actualizar` agrega lo nuevo y
    refresca lo que no editó el dueño.

    f56: los hot cues, memory cues y loops del DJ (POSITION_MARK) se guardan como marcas de la
    página en cada tema con archivo (`_importar_cues`). Lo que pasó con ellos va en `cues` de
    cada resultado; si la base del motor no se puede usar, la playlist se importa igual y
    `cues.error` dice por qué no entraron."""
    cuerpo, rechazo = await _cuerpo_json_propio(request)
    if rechazo:
        return rechazo
    col = _sesiones_xml.leer(cuerpo.get("token"))
    if col is None:
        return JSONResponse({"exito": False, "mensaje": "La lectura del XML venció o no existe: "
                             "elegí el archivo de nuevo."}, status_code=410)
    ids = cuerpo.get("playlists")
    if (not isinstance(ids, list) or not ids or len(ids) > 500
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
        return JSONResponse({"exito": False, "mensaje": "Elegí al menos una playlist."},
                            status_code=400)
    por_id = {p["id"]: p for p in col.playlists}
    if any(i not in por_id for i in ids):
        return JSONResponse({"exito": False, "mensaje": "Esa playlist no está en el XML."},
                            status_code=404)
    actualizar = cuerpo.get("actualizar") is True
    # f56: por defecto los cues de la página ganan (solo se suman los que no están); pisarlos
    # con los de Rekordbox es un pedido explícito.
    pisar_cues = cuerpo.get("pisar_cues") is True

    def importar():
        out = []
        for i in dict.fromkeys(ids):
            p = por_id[i]
            temas = playlist_import.temas_de_playlist(col, p)
            r = {"playlist": i, "ruta": p["ruta"],
                 **_importar_o_actualizar("rekordbox", p["ruta"], p["nombre"],
                                          col.nombre_xml, temas, actualizar)}
            # Una playlist que ya estaba y no se actualizó no toca nada: tampoco sus cues.
            if r["estado"] in ("creada", "actualizada"):
                r["cues"] = _importar_cues(temas, pisar_cues)
            out.append(r)
        return out

    resultados = await asyncio.to_thread(importar)
    return {"exito": all(r["estado"] != "error" for r in resultados), "resultados": resultados}


# --- importar desde una carpeta ----------------------------------------------------------------

@app.get("/api/importar/carpetas")
async def importar_carpetas(raiz: str | None = None, ruta: str = ""):
    """Sin `raiz`: las carpetas de música permitidas, por su nombre. Con `raiz` (su número) y
    `ruta` (relativa a ella): las subcarpetas inmediatas con cuántos audios tiene cada una."""
    raices = _raices()
    if raiz is None:
        return {"exito": True, "configuradas": bool(raices),
                "motivo": None if raices else _sin_raices_motivo(),
                "raices": playlist_import.raices_publicas(raices)}
    try:
        real, base, partes = await asyncio.to_thread(playlist_import.carpeta_segura, raices, raiz, ruta)
        lista = await asyncio.to_thread(playlist_import.listar_carpeta, real, base)
    except playlist_import.Rechazo as e:
        return _rechazo(e)
    publica = playlist_import.raices_publicas(raices)[int(raiz)]
    return {"exito": True, "raiz": publica, "ruta": "/".join(partes),
            "nombre": partes[-1] if partes else publica["nombre"], **lista}


@app.post("/api/importar/carpeta")
async def importar_carpeta(request: Request):
    """Crea una playlist con los audios de una carpeta: {raiz, ruta, recursivo?, actualizar?}.
    Nombre = el de la carpeta; orden alfabético natural; título/artista/género de los tags."""
    cuerpo, rechazo = await _cuerpo_json_propio(request)
    if rechazo:
        return rechazo
    raices = _raices()
    try:
        real, base, partes = await asyncio.to_thread(
            playlist_import.carpeta_segura, raices, cuerpo.get("raiz"), cuerpo.get("ruta") or "")
    except playlist_import.Rechazo as e:
        return _rechazo(e)
    recursivo = cuerpo.get("recursivo") is True
    archivos, pasado = await asyncio.to_thread(playlist_import.audios_de_carpeta, real, base, recursivo)
    if not archivos:
        return JSONResponse({"exito": False, "mensaje": "Esa carpeta no tiene audios"
                             + ("." if recursivo else " (probá incluyendo las subcarpetas).")},
                            status_code=400)
    temas = await asyncio.to_thread(lambda: [playlist_import.tema_de_archivo(p) for p in archivos])
    publica = playlist_import.raices_publicas(raices)[int(cuerpo.get("raiz"))]
    ref = "/".join([publica["nombre"], *partes])
    nombre = partes[-1] if partes else publica["nombre"]
    r = await asyncio.to_thread(_importar_o_actualizar, "carpeta", ref, nombre, None, temas,
                                cuerpo.get("actualizar") is True)
    if r["estado"] == "error":
        return JSONResponse({"exito": False, "mensaje": r["mensaje"]}, status_code=409)
    aviso = (f"La carpeta tiene más de {playlist_import.CARPETA_MAX_ARCHIVOS} audios: se "
             f"importaron los primeros {len(archivos)}.") if pasado else None
    return {"exito": True, **r, "ruta": ref, "aviso": aviso,
            "ilegibles": sum(1 for t in temas if t.get("import_motivo"))}


# ============================================================
#  Rekordbox con cues (f56): exportar una playlist a un XML con sus marcas, e importar las
#  marcas que el DJ ya tiene en Rekordbox
# ============================================================
#   GET   /api/playlists/{pid}/rekordbox    qué se va a exportar: temas, marcas y omitidos con
#                                           su motivo (sin rutas)
#   POST  /api/playlists/{pid}/rekordbox    {incluir_grilla?: bool} → el .xml como descarga
#   (importar: /api/importar/rekordbox guarda los POSITION_MARK, ver `_importar_cues`)
#
# La lógica del XML está en `rekordbox_cues.py`. Las rutas de los archivos salen de la base
# (los items de la playlist), nunca del cliente. Las respuestas JSON no llevan rutas; el .xml
# SÍ lleva la ruta completa de cada tema: es lo que Rekordbox necesita para encontrarlo (la
# pantalla lo avisa). Los audios no se tocan: solo se leen (la grilla, si se pide).

import rekordbox_cues  # noqa: E402

# Cuando la base del motor no se puede usar: el motivo SIN la ruta de la base (los de
# `_usar_store_motor` la llevan, y esto viaja a la pantalla de playlists).
_BASE_MOTOR_MOTIVO = {
    _RADIO_SIN_MOTOR: "Esta instalación no incluye el motor (motor/).",
    _RADIO_SIN_BASE: "Todavía no hay biblioteca del motor: analizá los temas de la playlist.",
    _RADIO_ESQUEMA: "La base del motor es de otra versión: no la toco.",
    _RADIO_OCUPADA: "La base del motor está ocupada (un scan u otra instancia): probá en un rato.",
    _RADIO_ILEGIBLE: "No pude leer la base del motor.",
}


def _motivo_base(estado: str) -> str:
    return _BASE_MOTOR_MOTIVO.get(estado, "No pude usar la base del motor.")


def _duracion_de_archivo(ruta: str) -> float | None:
    """La duración real del audio (mutagen), o None si no se puede leer. Solo se lee."""
    try:
        from mutagen import File as MFile
        largo = getattr(getattr(MFile(ruta), "info", None), "length", None)
    except Exception:  # noqa: BLE001 — un archivo raro no frena la importación
        return None
    return float(largo) if largo and math.isfinite(largo) and largo > 0 else None


def _importar_cues(temas: list[dict], pisar: bool) -> dict | None:
    """Guarda las marcas que traen los temas del XML (`rekordbox_cues.marcas_de_track`) como
    marcas de la página, por la ruta de cada archivo (`Store.import_cue_marks`): idempotente,
    los pads que la página ya usa ganan salvo `pisar`. None si ningún tema trae marcas.

    Las marcas de un tema sin archivo no tienen dónde guardarse (se cuentan en `sin_archivo`).
    El largo con el que se valida es el del análisis del motor si ya lo midió y, si no, el del
    archivo. Si la base del motor no existe se crea (como `scan`); si no se puede usar, `error`
    dice por qué y nada se guarda."""
    ignoradas: dict[str, int] = {}
    for t in temas:
        for k, n in (t.get("marcas_ignoradas") or {}).items():
            ignoradas[k] = ignoradas.get(k, 0) + n
    con = [t for t in temas if t.get("marcas")]
    if not con and not ignoradas:
        return None
    res = {"temas": 0, "agregadas": 0, "ya_estaban": 0, "conservadas": 0, "reemplazadas": 0,
           "fuera_del_tema": 0, "sin_lugar": 0,
           "sin_archivo": sum(len(t["marcas"]) for t in con if not t.get("ruta")),
           "color_distinto": ignoradas.pop("color_distinto", 0),
           "nombre_descartado": ignoradas.pop("nombre_descartado", 0),
           "ignoradas": ignoradas, "pisar": pisar, "error": None}
    con_ruta = [t for t in con if t.get("ruta")]
    if not con_ruta:
        return res
    try:
        from motor.cli import db_por_defecto
        from motor.store import Store
        base = db_por_defecto()
        if not base.exists():
            Store(base, espera_bloqueo_s=_RADIO_ESPERA_S).close()
    except ImportError:
        res["error"] = f"{_motivo_base(_RADIO_SIN_MOTOR)} Los cues no se guardaron."
        return res
    except (sqlite3.Error, OSError):
        res["error"] = "No pude crear la base del motor: los cues no se guardaron."
        return res

    def escribir(store):
        for t in con_ruta:
            ruta = os.path.abspath(t["ruta"])
            dur = store.duration(ruta)
            if dur is None:
                dur = _duracion_de_archivo(ruta)
            c = store.import_cue_marks(ruta, t["marcas"], pisar=pisar, duration_s=dur)
            for k, n in c.items():
                res[k] += n
            res["temas"] += 1

    _, estado, _motivo = _usar_store_motor(escribir)
    if estado != _RADIO_OK:
        res["error"] = f"{_motivo_base(estado)} Los cues no se guardaron."
    return res


def _grilla_export(ruta: str, bpm: float) -> dict:
    """La grilla ESTIMADA del tema (`motor.bandas.grilla`) o {"motivo"} si no se pudo leer el
    audio. Usa la misma caché que la onda de 3 bandas."""
    try:
        from motor.bandas import cached_bandas, grilla
        b, _ = cached_bandas(ruta, Path(db.DATA_DIR) / "peaks")
        return grilla(b, bpm)
    except Exception as e:  # noqa: BLE001 — un audio que no se lee no es un 500
        logger.warning(f"⚠️ Export Rekordbox: no pude estimar la grilla ({type(e).__name__})")
        return {"motivo": "no pude leer el audio para estimar la grilla"}


def _sin_marcas() -> dict:
    return {"hot_cues": 0, "memory": 0, "loops": 0}


def _armar_export(pid, incluir_grilla: bool) -> tuple[dict | None, JSONResponse | None]:
    """Qué va al XML de la playlist `pid` y qué queda afuera (con su motivo).

    Va un tema con archivo en la PC Y analizado por el motor (vigente): de ahí salen el BPM, la
    key y la duración, y sus marcas. Lo demás se lista en `omitidos`. La key dudosa (`?`, sin
    acuerdo entre tramos) NO se escribe: en Rekordbox no hay `?`, y escribirla la mostraría
    como segura; sin `Tonality`, Rekordbox la analiza él. Una marca que cae fuera del audio
    (el archivo cambió después de marcarla) no se escribe y se cuenta."""
    data = db.get_playlist_mia(pid, True) if pid is not None else None
    if not data:
        return None, JSONResponse({"exito": False, "mensaje": "Playlist no encontrada."},
                                  status_code=404)
    items = data["items"]
    rutas = list(dict.fromkeys(os.path.abspath(it["ruta"]) for it in items
                               if it.get("archivo_estado") == "ok" and it.get("ruta")))

    def leer(store):
        out = {}
        for r in rutas:
            f = store.get_features(r)
            out[r] = None if f is None else {
                "f": f, "vigente": not store.needs_analysis(r), "dur": store.duration(r),
                "marcas": store.list_cue_marks(r)}
        return out

    vistos, estado = {}, _RADIO_OK
    if rutas:
        res, estado, _ = _usar_store_motor(leer)
        if estado == _RADIO_OK:
            vistos = res
        elif estado != _RADIO_SIN_BASE:      # sin base = nada analizado todavía: se dice por tema
            return None, JSONResponse({"exito": False, "mensaje": _motivo_base(estado),
                                       "estado": estado}, status_code=409)

    try:
        from motor.cli import key_dudosa
    except ImportError:              # sin motor no hay nada analizado: no se llega a usarla
        def key_dudosa(_acuerdo) -> bool:
            return True

    temas, orden, filas, omitidos = [], [], [], []
    indice: dict[str, int] = {}
    for it in items:
        fila = {"id": it["id"], "titulo": it["titulo"], "artista": it["artista"],
                "incluido": False, "motivo": None, "marcas": _sin_marcas(), "marcas_fuera": 0,
                "bpm": None, "camelot": None, "key_omitida": False, "grilla": None}
        ae = it.get("archivo_estado")
        ruta = os.path.abspath(it["ruta"]) if ae == "ok" and it.get("ruta") else None
        v = vistos.get(ruta) if ruta else None
        if ae == "ambiguo":
            fila["motivo"] = (f"Hay {it.get('homonimos') or 2} archivos con ese nombre en tus "
                              f"carpetas y no elijo uno a la suerte.")
        elif ae != "ok":
            fila["motivo"] = _MOTIVO_ARCHIVO.get(ae) or "Sin archivo en la PC."
        elif v is None:
            fila["motivo"] = ("Todavía no lo analizó el motor: analizalo y exportá de nuevo (sin "
                              "análisis no hay BPM, key ni duración que llevar).")
        elif not v["vigente"]:
            fila["motivo"] = "El archivo cambió desde que se analizó: volvé a analizarlo."
        if fila["motivo"]:
            omitidos.append({"id": it["id"], "titulo": it["titulo"], "motivo": fila["motivo"]})
            filas.append(fila)
            continue
        fila["incluido"] = True
        f, dur = v["f"], v["dur"]
        bpm = _num(f.bpm)
        fila["bpm"] = round(bpm, 1) if bpm is not None and bpm > 0 else None
        if f.key and not key_dudosa(f.key_acuerdo):
            fila["camelot"] = f.key
        elif f.key:
            fila["key_omitida"] = True
        dur_ms = int(round(dur * 1000)) if dur else None
        marcas = []
        for m in v["marcas"]:
            if dur_ms is not None and (m.start_ms >= dur_ms
                                       or (m.end_ms is not None and m.end_ms > dur_ms)):
                fila["marcas_fuera"] += 1
                continue
            marcas.append({"kind": m.kind, "num": m.num, "start_ms": m.start_ms,
                           "end_ms": m.end_ms, "name": m.name})
            clave = "loops" if m.kind == "loop" else "memory" if m.kind == "memory" else "hot_cues"
            fila["marcas"][clave] += 1
        if ruta not in indice:
            tempo = None
            if incluir_grilla:
                if bpm is not None and bpm > 0:
                    g = _grilla_export(ruta, bpm)
                    tempo = rekordbox_cues.tempo_de_grilla(g)
                    fila["grilla"] = {"incluida": tempo is not None,
                                      "motivo": None if tempo else (g.get("motivo") or g.get("compas_motivo")
                                                                    or "no se sabe cuál beat es el 1")}
                else:
                    fila["grilla"] = {"incluida": False, "motivo": "el tema no tiene BPM medido"}
            indice[ruta] = len(temas)
            temas.append({"ruta": ruta, "titulo": it["titulo"], "artista": it["artista"],
                          "bpm": fila["bpm"], "camelot": fila["camelot"], "duracion_s": dur,
                          "marcas": marcas, "tempo": tempo})
        orden.append(indice[ruta])
        filas.append(fila)

    incluidas = [x for x in filas if x["incluido"]]
    return {"nombre": data["nombre"], "archivo": rekordbox_cues.nombre_archivo_xml(data["nombre"]),
            "temas": temas, "orden": orden, "filas": filas, "omitidos": omitidos,
            "incluidos": len(incluidas),
            "marcas": {k: sum(x["marcas"][k] for x in incluidas) for k in _sin_marcas()},
            "marcas_fuera": sum(x["marcas_fuera"] for x in incluidas),
            "keys_omitidas": sum(1 for x in incluidas if x["key_omitida"]),
            "grillas": sum(1 for x in incluidas if (x["grilla"] or {}).get("incluida"))}, None


_AVISO_RUTAS = ("El archivo lleva la ruta completa de cada tema en tu PC: es lo que Rekordbox "
                "necesita para encontrarlos. No lo compartas si no querés mostrar tus carpetas.")
_AVISO_GRILLA = ("La grilla de beats de la página es ESTIMADA. Si la exportás, Rekordbox la usa "
                 "en vez de analizar la suya: revisala en Rekordbox antes de tocar en vivo con "
                 "quantize o sync. Va solo en los temas donde se encontró el beat y el 1 del "
                 "compás con confianza.")


def _export_publico(plan: dict) -> dict:
    return {"exito": True, "nombre": plan["nombre"], "archivo": plan["archivo"],
            "total": len(plan["filas"]), "incluidos": plan["incluidos"],
            "marcas": plan["marcas"], "marcas_fuera": plan["marcas_fuera"],
            "keys_omitidas": plan["keys_omitidas"], "omitidos": plan["omitidos"],
            "temas": [{k: v for k, v in x.items() if k != "grilla"} for x in plan["filas"]],
            "aviso_rutas": _AVISO_RUTAS, "aviso_grilla": _AVISO_GRILLA}


@app.get("/api/playlists/{pid}/rekordbox")
async def playlists_rekordbox_resumen(pid: str):
    """Qué va a llevar el XML de Rekordbox de esta playlist, sin armarlo: cuántos temas y
    marcas, los que quedan afuera con su motivo y los avisos. Sin rutas."""
    plan, error = await asyncio.to_thread(_armar_export, _id_url(pid), False)
    if error is not None:
        return error
    return _export_publico(plan)


@app.post("/api/playlists/{pid}/rekordbox")
async def playlists_rekordbox_exportar(pid: str, request: Request):
    """Arma el XML de Rekordbox de la playlist y lo devuelve como descarga.

    Cuerpo JSON: {"incluir_grilla": true} agrega la grilla ESTIMADA (`TEMPO`) donde se pudo
    estimar con confianza; sin eso (por defecto) no va ninguna grilla. 403 pedido de otra
    página · 415 no es JSON · 400 opción inválida · 404 playlist · 409 nada que exportar o base
    del motor inutilizable (con el motivo). Las cabeceras X-MusiFlix-* dicen cuántos temas,
    marcas y grillas van."""
    cuerpo, rechazo = await _cuerpo_json_propio(request)
    if rechazo:
        return rechazo
    sobran = sorted(set(cuerpo) - {"incluir_grilla"})
    grilla_pedida = cuerpo.get("incluir_grilla", False)
    if sobran or not isinstance(grilla_pedida, bool):
        return JSONResponse({"exito": False, "mensaje": "La única opción es «incluir_grilla» "
                             "(true o false)."}, status_code=400)
    plan, error = await asyncio.to_thread(_armar_export, _id_url(pid), grilla_pedida)
    if error is not None:
        return error
    if not plan["temas"]:
        return JSONResponse({"exito": False, "mensaje": "Ningún tema de la playlist se puede "
                             "exportar todavía (el motivo está en cada uno).",
                             "omitidos": plan["omitidos"]}, status_code=409)
    try:
        xml = await asyncio.to_thread(rekordbox_cues.armar_xml_playlist, plan["nombre"],
                                      plan["temas"], plan["orden"])
    except Exception as e:  # noqa: BLE001 — nunca un 500
        logger.warning(f"⚠️ Export Rekordbox: no pude armar el XML ({type(e).__name__}: {e})")
        return JSONResponse({"exito": False, "mensaje": "No pude armar el XML "
                             f"({type(e).__name__})."}, status_code=409)
    m = plan["marcas"]
    logger.info(f"📤 Rekordbox: {plan['archivo']} ({plan['incluidos']} temas, "
                f"{sum(m.values())} marcas)")
    return Response(content=xml, media_type="application/xml",
                    headers={"Content-Disposition": _content_disposition(plan["archivo"]),
                             "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                             "X-MusiFlix-Temas": str(plan["incluidos"]),
                             "X-MusiFlix-Omitidos": str(len(plan["omitidos"])),
                             "X-MusiFlix-Marcas": str(sum(m.values())),
                             "X-MusiFlix-Grillas": str(plan["grillas"])})


# ============================================================
#  Frontend React (build de Vite). Se registra AL FINAL para que
#  el catch-all no tape las rutas /api/*.
# ============================================================
if (DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """Sirve archivos sueltos del build (favicon.svg, icons.svg…) y hace fallback a
    index.html para cualquier ruta desconocida (SPA). No intercepta /api: esas rutas
    se registran antes y tienen prioridad."""
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    root = DIST_DIR.resolve()
    f = (DIST_DIR / full_path).resolve()
    if f.is_file() and (f == root or root in f.parents):
        return FileResponse(f)
    index = DIST_DIR / "index.html"
    return FileResponse(index if index.exists() else WEB_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    print("\n" + "=" * 60)
    print("  🎵  BOT DE MÚSICA - SERVIDOR WEB")
    print("=" * 60)
    print("  Abrí en el navegador:  http://localhost:8000")
    print("  Para cerrar: Ctrl + C en esta ventana")
    print("=" * 60 + "\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
