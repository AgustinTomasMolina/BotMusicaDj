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
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
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
from search_agent import SearchAgent
from recommendation_agent import RecommendationAgent
from scrapers import FUENTES_SCRAPER

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"               # frontend viejo (vanilla), queda en /legacy
DIST_DIR = BASE_DIR / "frontend" / "dist"  # build de React (Vite), se sirve en /
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)


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
    Para destinos lossless (wav/flac/aiff) conviene la mejor fuente convertible;
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
    formato = (payload.get("formato") or "mp3").lower()
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
async def buscar(q: str = "", limite: int = 24, formato: str = "mp3", genero: str = ""):
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
    grupos = _agrupar_por_track(resultados, (formato or "mp3").lower(), q or genero)
    logger.info(f"✅ {len(resultados)} resultados → {len(grupos)} temas (agrupados por versión).")
    await asyncio.to_thread(db.registrar_busqueda, q, len(grupos))
    # `canciones` se mantiene por compatibilidad; el front usa `grupos`.
    return {"exito": True, "mensaje": f"{len(grupos)} temas",
            "grupos": grupos, "canciones": resultados}


@app.get("/api/meta")
async def meta(titulo: str, artista: str = ""):
    """BPM + género de un tema (Deezer + fallback librosa). Cacheado. Carga lazy."""
    import similares
    info = await asyncio.to_thread(similares.meta_de, titulo, artista, True)
    return info


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
                          formato: str = "mp3", genero: str = ""):
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

    formato = (formato or "mp3").lower()
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


@app.post("/api/descargar")
async def descargar(payload: dict):
    """Descarga una canción. Si es de Spotify, la busca en YouTube primero."""
    titulo = payload.get("titulo") or "cancion"
    artista = payload.get("artista") or ""
    fuente = payload.get("fuente") or ""
    url = payload.get("url") or ""
    formato = (payload.get("formato") or "mp3").lower()

    logger.info(f"📥 Pedido de descarga: {titulo} — {artista} [{fuente}] como {formato.upper()}")

    # Fuentes con MP3 directo: bajamos el archivo tal cual (sin yt-dlp ni ffmpeg)
    if fuente in ("ligaudio", "hitplayer") and url:
        try:
            res = await asyncio.to_thread(_descargar_directo, url, f"{titulo} - {artista}")
        except Exception as e:
            logger.error(f"❌ Error en descarga directa: {e}")
            return {"exito": False, "mensaje": str(e)}
        if res["ok"]:
            logger.info(f"✅ Descargado: {res['archivo']}")
            # MP3 de scraper: origen desconocido → análisis espectral
            calidad = await asyncio.to_thread(_calidad_espectral, DOWNLOADS_DIR / res["archivo"])
            calidad.update(_grado(calidad))
            _log_calidad(calidad)
            await asyncio.to_thread(_taggear_descarga, res["archivo"], titulo, artista, payload, calidad)
            await asyncio.to_thread(_hist_descarga, res, titulo, artista, fuente, url, formato, payload, calidad)
            return {"exito": True, "mensaje": f"Descargado: {res['archivo']}",
                    "archivo": res["archivo"], "calidad": calidad}
        logger.error("❌ La descarga directa no generó archivo.")
        return {"exito": False, "mensaje": "La descarga falló."}

    # Spotify (DRM) y Deezer (link no descargable): buscamos el equivalente en YouTube
    if fuente in ("spotify", "deezer") or not url:
        consulta = f"{titulo} {artista}".strip()
        logger.info(f"🔁 Fuente sin audio descargable, buscando en YouTube: '{consulta}'")
        yt = await asyncio.to_thread(search_agent.buscar_en_youtube, consulta, 1)
        if not yt:
            logger.error("❌ No encontré una versión descargable.")
            return {"exito": False, "mensaje": "No se pudo encontrar audio descargable."}
        url = yt[0]["url"]

    try:
        res = await asyncio.to_thread(_descargar_sync, url, f"{titulo} - {artista}", formato)
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
        await asyncio.to_thread(_taggear_descarga, res["archivo"], titulo, artista, payload, calidad)
        await asyncio.to_thread(_hist_descarga, res, titulo, artista, fuente, url, formato, payload, calidad)
        return {"exito": True, "mensaje": f"Descargado: {res['archivo']}",
                "archivo": res["archivo"], "calidad": calidad}
    logger.error("❌ La descarga no generó archivo.")
    return {"exito": False, "mensaje": "La descarga falló."}


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
