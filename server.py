"""
Servidor Web del Bot de Música
================================
Sirve una página única con:
  - Buscador de canciones
  - Consola en vivo (WebSocket) con los logs/comandos del bot
  - Playlist de resultados estilo YouTube
  - Descarga de canciones con progreso en tiempo real

Ejecutar:  py server.py
Abrir:     http://localhost:8000
"""

import asyncio
import hashlib
import logging
import math
import os
import re
import sqlite3
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Set

# Consola de Windows en UTF-8 para que los emojis/logs no rompan el proceso
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import db
import jobs
from search_agent import SearchAgent
from recommendation_agent import RecommendationAgent
from scrapers import FUENTES_SCRAPER

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"               # frontend viejo (vanilla), queda en /legacy
DIST_DIR = BASE_DIR / "frontend" / "dist"  # build de React (Vite), se sirve en /
DOWNLOADS_DIR = Path(os.getenv("MUSIFLIX_DOWNLOADS", str(BASE_DIR / "downloads")))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
#  CONSOLA EN VIVO: puente entre el logging de Python y la web
# ============================================================
class ConsoleBroker:
    """Reparte los mensajes de log a todas las consolas web conectadas."""

    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    def publish(self, message: str, level: str = "INFO"):
        """Seguro para llamar desde CUALQUIER hilo (lo usa el log handler)."""
        if self._loop is None:
            return
        payload = {"msg": message, "level": level}
        try:
            self._loop.call_soon_threadsafe(self._fanout, payload)
        except RuntimeError:
            pass  # loop cerrado

    def _fanout(self, payload: dict):
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass


broker = ConsoleBroker()


class WebSocketLogHandler(logging.Handler):
    """Handler de logging que envía cada línea a la consola web."""

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            broker.publish(msg, record.levelname)
        except Exception:
            pass


# Conectar el logging global a la consola web + consola normal
console_handler = WebSocketLogHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(message)s", "%H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(), console_handler])
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
    broker.set_loop(asyncio.get_running_loop())
    await asyncio.to_thread(db.init_db)  # crea las tablas del historial si faltan
    logger.info("🌐 Servidor web iniciado. Buscador listo.")
    yield


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


@app.websocket("/ws/console")
async def ws_console(websocket: WebSocket):
    await websocket.accept()
    q = broker.subscribe()
    await websocket.send_json({"msg": "🟢 Consola conectada. Esperando comandos del bot...", "level": "INFO"})
    try:
        while True:
            payload = await q.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        broker.unsubscribe(q)


def _buscar_mix(q: str, limite: int) -> list:
    """Consulta todas las fuentes EN PARALELO y las intercala (round-robin)."""
    from concurrent.futures import ThreadPoolExecutor

    por_fuente = max(4, limite // 4)

    # (nombre, callable) de cada fuente
    tareas = [
        ("youtube", lambda: search_agent.buscar_en_youtube(q, limit=por_fuente)),
        ("ligaudio", lambda: FUENTES_SCRAPER[0][1](q, limit=por_fuente)),
        ("hitplayer", lambda: FUENTES_SCRAPER[1][1](q, limit=por_fuente)),
        ("spotify", lambda: search_agent.buscar_en_spotify(q, limit=por_fuente)),
        ("soundcloud", lambda: search_agent.buscar_en_soundcloud(q, limit=por_fuente)),
    ]

    resultados = {}
    with ThreadPoolExecutor(max_workers=len(tareas)) as ex:
        futuros = {ex.submit(fn): nombre for nombre, fn in tareas}
        for fut, nombre in futuros.items():
            try:
                resultados[nombre] = fut.result(timeout=30) or []
            except Exception as e:
                logger.warning(f"⚠️ Fuente '{nombre}' falló: {e}")
                resultados[nombre] = []

    # Orden de intercalado: primero las que se reproducen/descargan directo
    orden = ["youtube", "ligaudio", "hitplayer", "soundcloud", "spotify"]
    grupos = [resultados.get(n, []) for n in orden]

    mezcla, i = [], 0
    while len(mezcla) < limite and any(i < len(g) for g in grupos):
        for g in grupos:
            if i < len(g):
                mezcla.append(g[i])
        i += 1
    return mezcla[:limite]


def _rank_calidad(fuente: str, formato: str) -> int:
    """Prioridad de fuente según el formato de descarga pedido.
    Para destinos lossless (wav/flac) conviene la mejor fuente convertible;
    las que ya vienen en MP3 fijo (scrapers) van al final porque convertir un MP3
    a WAV no recupera calidad. Para MP3 el orden casi no importa, se usa el mismo."""
    f = (fuente or "").lower()
    base = {"youtube": 0, "soundcloud": 1, "spotify": 2, "deezer": 2,
            "ligaudio": 3, "hitplayer": 4}
    return base.get(f, 9)


def _opciones_de(linea: str, formato: str, n: int = 3) -> list:
    """Busca UNA línea de la lista y devuelve N opciones de PLATAFORMAS DISTINTAS
    (una por fuente, en orden de prioridad para el formato). Así el usuario compara
    YouTube vs SoundCloud vs MP3 directo con el Spek y elige la de mejor calidad.
    Lista vacía si no hubo resultado."""
    linea = (linea or "").strip()
    if not linea:
        return []
    cands = _buscar_mix(linea, 12)
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


def _buscar_lista(lineas: list, formato: str) -> list:
    """Busca cada línea EN PARALELO y devuelve sus opciones (lista de candidatos,
    vacía si esa línea no tuvo resultados), conservando el orden de la lista."""
    from concurrent.futures import ThreadPoolExecutor

    out: list = [[] for _ in lineas]
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_opciones_de, ln, formato): idx for idx, ln in enumerate(lineas)}
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
    # yt-dlp/spotipy son bloqueantes -> correr en hilo aparte para no frenar la consola
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
                          formato: str = "wav", genero: str = ""):
    """Como /api/parecidas, pero por CADA tema parecido trae hasta 3 opciones de
    plataformas distintas (YouTube, SoundCloud, MP3 directo…) para comparar con el
    Spek y elegir la mejor. Devuelve 'grupos' como el modo lista + la semilla.
    `genero` es una pista opcional para acertar el género de las parecidas."""
    titulo = (titulo or "").strip()
    if not titulo:
        return JSONResponse({"exito": False, "mensaje": "Falta el título."}, status_code=400)

    logger.info(f"🎵 Parecidas con opciones a: '{titulo}' — {artista} (hasta {total})"
                + (f" [género: {genero}]" if genero else ""))
    import similares
    res = await asyncio.to_thread(similares.construir_playlist, titulo, artista, total, True, genero or None)
    if not res.get("exito"):
        logger.warning(f"❌ {res.get('mensaje', 'No se pudo armar la playlist parecida.')}")
        return res

    formato = (formato or "wav").lower()
    lineas = [f"{c['artista']} - {c['titulo']}".strip(" -") for c in res["canciones"]]
    resultados = await asyncio.to_thread(_buscar_lista, lineas, formato)

    grupos, no_encontradas = [], []
    for ln, opciones in zip(lineas, resultados):
        if opciones:
            grupos.append({"consulta": ln, "opciones": opciones})
        else:
            no_encontradas.append(ln)

    logger.info(f"✅ Parecidas con opciones: {len(grupos)}/{len(lineas)} con plataformas.")
    return {"exito": True, "seed": res["seed"], "total": len(lineas),
            "encontradas": len(grupos), "grupos": grupos, "no_encontradas": no_encontradas}


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
                   formato: str, payload: dict, calidad: dict):
    """Registra la descarga en el historial y, si hay crate activa, la suma ahí (best-effort)."""
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
    # Auto-add a la playlist/crate activa (si hay una)
    activa = db.get_playlist_activa()
    if activa:
        track = {"titulo": titulo, "artista": artista, "fuente": fuente, "url": url,
                 "thumbnail": payload.get("thumbnail"), "duracion": payload.get("duracion"),
                 "bpm": payload.get("bpm"), "camelot": payload.get("camelot"),
                 "genero": payload.get("genero")}
        db.marcar_descargado(activa["id"], track, archivo, ruta, formato,
                             cal.get("grade"), cal.get("color"))


def procesar_descarga(payload: dict) -> dict:
    """TODO el flujo de descarga, SÍNCRONO (sin async): bajar → calidad → tags →
    historial/crate. Lo llaman el worker (vía tasks.descargar_job) y el server en
    modo local. Si es de Spotify/Deezer, busca el equivalente en YouTube."""
    titulo = payload.get("titulo") or "cancion"
    artista = payload.get("artista") or ""
    fuente = payload.get("fuente") or ""
    url = payload.get("url") or ""
    formato = (payload.get("formato") or "wav").lower()

    logger.info(f"📥 Descargando: {titulo} — {artista} [{fuente}] como {formato.upper()}")

    # Fuentes con MP3 directo: bajamos el archivo tal cual (sin yt-dlp ni ffmpeg)
    if fuente in ("ligaudio", "hitplayer") and url:
        try:
            res = _descargar_directo(url, f"{titulo} - {artista}")
        except Exception as e:
            logger.error(f"❌ Error en descarga directa: {e}")
            return {"exito": False, "mensaje": str(e)}
        if res["ok"]:
            logger.info(f"✅ Descargado: {res['archivo']}")
            calidad = _calidad_espectral(DOWNLOADS_DIR / res["archivo"])
            calidad.update(_grado(calidad))
            _log_calidad(calidad)
            _taggear_descarga(res["archivo"], titulo, artista, payload, calidad)
            _hist_descarga(res, titulo, artista, fuente, url, formato, payload, calidad)
            return {"exito": True, "mensaje": f"Descargado: {res['archivo']}",
                    "archivo": res["archivo"], "calidad": calidad}
        logger.error("❌ La descarga directa no generó archivo.")
        return {"exito": False, "mensaje": "La descarga falló."}

    # Spotify (DRM) y Deezer (link no descargable): buscamos el equivalente en YouTube
    if fuente in ("spotify", "deezer") or not url:
        consulta = f"{titulo} {artista}".strip()
        logger.info(f"🔁 Fuente sin audio descargable, buscando en YouTube: '{consulta}'")
        yt = search_agent.buscar_en_youtube(consulta, 1)
        if not yt:
            logger.error("❌ No encontré una versión descargable.")
            return {"exito": False, "mensaje": "No se pudo encontrar audio descargable."}
        url = yt[0]["url"]

    try:
        res = _descargar_sync(url, f"{titulo} - {artista}", formato)
    except Exception as e:
        logger.error(f"❌ Error en descarga: {e}")
        msg = str(e)
        if "ffmpeg" in msg.lower() or "ffprobe" in msg.lower():
            msg = "Falta ffmpeg para convertir el audio. Instalá ffmpeg (ver iniciar_web.bat)."
        return {"exito": False, "mensaje": msg}

    if res["ok"]:
        logger.info(f"✅ Descargado: {res['archivo']}")
        calidad = res.get("calidad")
        if calidad:
            calidad.update(_grado(calidad))
            _log_calidad(calidad)
        _taggear_descarga(res["archivo"], titulo, artista, payload, calidad)
        _hist_descarga(res, titulo, artista, fuente, url, formato, payload, calidad)
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
        from scrapers import HEADERS
        return url, HEADERS.get("User-Agent", "")

    # Qué URL(s) intentar resolver con yt-dlp. Para Spotify/Deezer (o si no hay
    # URL directa) buscamos el audio en YouTube; pedimos VARIOS candidatos por si
    # el primero falla (p. ej. video restringido por edad).
    if url and f not in ("spotify", "deezer"):
        targets = [url]
    else:
        targets = [c["url"] for c in search_agent.buscar_en_youtube(f"{titulo} {artista}".strip(), 3)]
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


def _duracion_audio(audio: str, ua: str) -> float:
    """Duración (segundos) del stream, o 0 si no se puede medir."""
    import subprocess
    from analizar_calidad import FFPROBE

    cmd = [FFPROBE, "-v", "error"]
    if ua:
        cmd += ["-user_agent", ua]
    cmd += ["-show_entries", "format=duration", "-of", "csv=p=0", audio]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", timeout=20)
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
    dur = _duracion_audio(audio, ua)
    if dur and dur < ini + ventana:
        ventana = min(ventana, max(10.0, dur - 1))
        ini = max(0.0, dur - ventana - 1)

    cmd = [FFMPEG, "-hide_banner", "-nostats"]
    if ua:
        cmd += ["-user_agent", ua]
    cmd += [
        "-ss", str(ini), "-t", str(ventana), "-i", audio,
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
        from scrapers import HEADERS
        ua = HEADERS.get("User-Agent", "")
        dur = _duracion_audio(url, ua)
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
            targets = [c["url"] for c in search_agent.buscar_en_youtube(f"{titulo} {artista}".strip(), 3)]
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


@app.get("/api/calidad")
async def calidad(titulo: str, artista: str = "", fuente: str = "", url: str = ""):
    """Nota de calidad (A/B/C/D/F) del audio real, ANTES de descargar. Cacheada."""
    clave = f"{fuente}|{url}|{titulo}|{artista}"
    cal = _CALIDAD_CACHE.get(clave)
    if cal is None:
        if jobs.queue_disponible():
            import tasks
            job = jobs.encolar(tasks.calidad_job, titulo, artista, fuente, url, timeout=120)
            cal = await asyncio.to_thread(jobs.esperar_resultado, job, 90)
        else:
            cal = await asyncio.to_thread(_calidad_preview, titulo, artista, fuente, url)
        if cal:
            _CALIDAD_CACHE[clave] = cal
    if not cal:
        return {"ok": False, "grade": "?", "color": _GRADO_COLOR["?"], "calidad": "no analizable"}
    return {"ok": True, **cal}


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


def _leer_biblioteca_motor() -> tuple[list, str, str | None]:
    """`(tracks, estado, motivo)` de la biblioteca del motor. Nunca levanta."""
    try:
        from motor.cli import db_por_defecto, esta_bloqueada
        from motor.store import EsquemaIncompatible, Store
    except ImportError as e:
        logger.warning(f"⚠️ Radio: falta el paquete motor ({e}); la radio queda desactivada.")
        return [], _RADIO_SIN_MOTOR, (
            "Esta instalación no incluye el motor de radio (motor/), así que la radio está "
            "desactivada.")

    db = db_por_defecto()
    if not db.exists():
        return [], _RADIO_SIN_BASE, (
            f"No hay biblioteca del motor en {db}. Analizá una carpeta con "
            f"`python -m motor scan <carpeta> --licencia ... --origen ...`, o apuntá "
            f"DJRADIO_DB a la base que ya tengas.")
    try:
        # Espera corta, no la de la CLI: ver `_RADIO_ESPERA_S`.
        with Store(db, espera_bloqueo_s=_RADIO_ESPERA_S) as store:
            biblioteca = store.load_library()
    except EsquemaIncompatible as e:
        return [], _RADIO_ESQUEMA, str(e)
    except sqlite3.OperationalError as e:
        # "Ocupada" se separa de "ilegible" con la MISMA condición que la CLI
        # (`cli.esta_bloqueada`): no es una base rota, es que hay un scan corriendo, y se
        # arregla esperando. Con el motivo crudo de SQLite ("database is locked") en
        # pantalla, el DJ no tiene forma de saber que lo único que tiene que hacer es
        # reintentar en un rato.
        if not esta_bloqueada(e):
            logger.warning(f"⚠️ Radio: no pude leer la base del motor {db}: {e}")
            return [], _RADIO_ILEGIBLE, f"No pude leer la biblioteca del motor ({db}): {e}"
        return [], _RADIO_OCUPADA, (
            f"La biblioteca del motor ({db}) está ocupada: hay un scan u otra instancia "
            f"usándola (se esperó {_RADIO_ESPERA_S:g} s). Reintentá en un rato.")
    except (sqlite3.Error, ValueError) as e:
        # sqlite3.Error: la base no es SQLite o está corrupta.
        # ValueError: una fila sin licencia/origen o con un BPM infinito — `Track` la rechaza
        # al construirla y se lleva puesta la biblioteca entera. Ninguna de las dos es un bug
        # del server, y las dos son arreglables sabiendo qué base se está leyendo.
        logger.warning(f"⚠️ Radio: no pude leer la base del motor {db}: {e}")
        return [], _RADIO_ILEGIBLE, f"No pude leer la biblioteca del motor ({db}): {e}"
    if not biblioteca:
        return [], _RADIO_VACIA, (
            f"La biblioteca del motor ({db}) no tiene ningún track analizado. Corré "
            f"`python -m motor scan <carpeta> --licencia ... --origen ...`.")
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
    }


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


@app.get("/api/radio/audio/{track_id}")
async def radio_audio(track_id: str):
    """Sirve el archivo de un track de la biblioteca del motor, para reproducir el set.
    Solo LEE: nunca escribe ni mueve el original (regla del proyecto).

    El id no es una ruta (`_radio_id`) y lo único que se sirve es lo que está en el índice
    que armó la base, así que no hay forma de pedir un archivo de afuera de la biblioteca.
    """
    ruta = _radio_audio.get(track_id)
    if ruta is None and (not _radio_audio
                         or time.monotonic() - _radio_recarga_ts >= _RADIO_RECARGA_MIN_S):
        # Puede ser un id nuevo (se escaneó algo desde el último pedido). Acotado: sin el
        # mínimo entre recargas, una tanda de ids desconocidos hace un `load_library()` por
        # pedido. Con el índice vacío se recarga igual — ahí no hay nada que proteger.
        await asyncio.to_thread(_cargar_radio)
        ruta = _radio_audio.get(track_id)
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
                      f"con `docker compose exec web python -m motor scan /musica "
                      f"--licencia ... --origen ...`."}, status_code=404)
    return FileResponse(ruta)             # FileResponse maneja Range → el <audio> puede buscar


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
@app.get("/api/playlists")
async def playlists_listar():
    return {"exito": True, "playlists": await asyncio.to_thread(db.listar_playlists)}


@app.get("/api/playlists/activa")
async def playlists_activa():
    return {"activa": await asyncio.to_thread(db.get_playlist_activa)}


@app.post("/api/playlists")
async def playlists_crear(payload: dict):
    nombre = (payload.get("nombre") or "").strip() or "Nueva playlist"
    p = await asyncio.to_thread(db.crear_playlist, nombre)
    return {"exito": bool(p), "playlist": p}


@app.get("/api/playlists/{pid}")
async def playlists_get(pid: int):
    data = await asyncio.to_thread(db.get_playlist_mia, pid)
    if not data:
        return JSONResponse({"exito": False, "mensaje": "Playlist no encontrada."}, status_code=404)
    return {"exito": True, "data": data}


@app.patch("/api/playlists/{pid}")
async def playlists_editar(pid: int, payload: dict):
    if "nombre" in payload:
        await asyncio.to_thread(db.renombrar_playlist, pid, payload.get("nombre") or "")
    if payload.get("activar"):
        await asyncio.to_thread(db.activar_playlist, pid)
    return {"exito": True}


@app.delete("/api/playlists/{pid}")
async def playlists_borrar(pid: int):
    return {"exito": await asyncio.to_thread(db.borrar_playlist_mia, pid)}


@app.post("/api/playlists/{pid}/items")
async def playlists_agregar_item(pid: int, payload: dict):
    r = await asyncio.to_thread(db.agregar_item, pid, payload.get("track") or payload)
    if r is None:
        return JSONResponse({"exito": False, "mensaje": "Playlist no encontrada."}, status_code=404)
    return {"exito": True, **r}


@app.delete("/api/playlists/{pid}/items/{item_id}")
async def playlists_quitar_item(pid: int, item_id: int):
    return {"exito": await asyncio.to_thread(db.quitar_item, item_id)}


@app.post("/api/playlists/{pid}/orden")
async def playlists_reordenar(pid: int, payload: dict):
    return {"exito": await asyncio.to_thread(db.reordenar, pid, payload.get("orden") or [])}


@app.post("/api/playlists/{pid}/export")
async def playlists_export(pid: int):
    recibo = await asyncio.to_thread(db.armar_m3u8, pid)
    if not recibo:
        return JSONResponse({"exito": False, "mensaje": "No pude exportar la playlist."}, status_code=502)
    logger.info(f"📃 Playlist exportada: {recibo['archivo']} ({recibo['incluidos']} temas)")
    return {"exito": True, **recibo}


# ============================================================
#  Frontend React (build de Vite). Se registra AL FINAL para que
#  el catch-all no tape las rutas /api/* ni el WebSocket /ws/console.
# ============================================================
if (DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """Sirve archivos sueltos del build (favicon.svg, icons.svg…) y hace fallback a
    index.html para cualquier ruta desconocida (SPA). No intercepta /api ni /ws:
    esas rutas se registran antes y tienen prioridad."""
    if full_path.startswith(("api/", "ws/")):
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
