"""Playlist de canciones parecidas (Nivel 1 + Nivel 2), sin API key.

Nivel 1 — datos: usa la API pública de Deezer (sin key) para traer candidatos
por artistas relacionados (mismo estilo/escena) + el propio artista.
Nivel 2 — audio: ordena por cercanía de BPM, y analiza el tono/key del tema
semilla con librosa (sobre el preview de 30s) para mostrar mezcla armónica.
"""
import copy
import logging
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests
import track_identity

logger = logging.getLogger("similares")
DEEZER = "https://api.deezer.com"
_UA = {"User-Agent": "Mozilla/5.0"}

# Cache de respuestas de Deezer (f32). Medido: armar parecidas hacía 30-39 pedidos a la API y
# varios se repiten entre semillas de la misma escena (los related, sus álbumes). Solo se
# guardan respuestas buenas: un error o una cuota agotada no se cachea (volver a pedir es lo
# correcto). TTL corto: el catálogo cambia poco, pero "lanzamientos recientes" es por fecha.
_GET_TTL_S = 30 * 60
_GET_MAX = 4000
_GET_CACHE: dict[str, tuple[float, dict]] = {}
_GET_LOCK = threading.Lock()
# Deezer contesta 200 con {"error": {"code": 4, ...}} cuando se pasa de ~50 pedidos cada 5 s.
# Sin reintento ese álbum quedaba afuera en silencio, y ahora que los pedidos van en
# paralelo (ver construir_playlist) es más fácil llegar a la cuota.
_QUOTA_RETRIES = 3
# Pedidos a la API en paralelo. Medido desde acá cada pedido tarda ~2,4 s, así que 10 a la
# vez son ~4 pedidos por segundo: lejos de la cuota de Deezer (~50 cada 5 s).
_DEEZER_WORKERS = 10
_PREVIEW_WORKERS = 16     # previews bajando + librosa en paralelo


def _get(url: str) -> dict:
    now = time.monotonic()
    with _GET_LOCK:
        hit = _GET_CACHE.get(url)
        if hit and now - hit[0] < _GET_TTL_S:
            # Copia: construir_playlist le escribe campos (_release, _bpm, _score…) a los
            # tracks que recibe; sin copia, dos pedidos se pisarían el mismo dict.
            return copy.deepcopy(hit[1])
    for attempt in range(_QUOTA_RETRIES + 1):
        try:
            data = requests.get(url, headers=_UA, timeout=20).json()
        except Exception as e:
            logger.warning(f"⚠️ Deezer falló: {e}")
            return {}
        if not isinstance(data, dict):
            return {}
        err = data.get("error")
        if isinstance(err, dict) and err.get("code") == 4 and attempt < _QUOTA_RETRIES:
            time.sleep(1.0 + attempt)          # cuota agotada: esperar y reintentar
            continue
        if not err:
            with _GET_LOCK:
                if len(_GET_CACHE) >= _GET_MAX:
                    _GET_CACHE.clear()
                _GET_CACHE[url] = (time.monotonic(), copy.deepcopy(data))
        return data
    return {}


# Familias de género (agrupan los géneros finos de Deezer). Sirven para NO mezclar
# hard techno con rap: comparamos por familia, no por el nombre exacto.
_FAMILIAS = {
    "electronic": {"dance", "electro", "techno", "house", "trance", "dubstep", "drum",
                   "dnb", "edm", "electronic", "electro", "hardstyle", "hardcore",
                   "rave", "progressive", "big room", "garage", "breakbeat"},
    "rap": {"rap", "hip hop", "hip-hop", "hiphop", "trap", "drill"},
    "pop": {"pop"},
    "rock": {"rock", "metal", "punk", "indie", "alternative", "grunge"},
    "latin": {"latin", "reggaeton", "salsa", "cumbia", "bachata", "merengue", "tango"},
    "rnb": {"r&b", "rnb", "soul", "funk"},
    "jazz": {"jazz", "blues"},
    "reggae": {"reggae", "dancehall", "ska"},
    "classical": {"classical", "clásica"},
    "folk": {"folk", "country", "acoustic"},
}


def _familia(genero: str | None) -> str | None:
    """Familia amplia de un género de Deezer (o None si desconocido)."""
    if not genero:
        return None
    gl = genero.lower()
    for fam, kws in _FAMILIAS.items():
        if any(k in gl for k in kws):
            return fam
    return "otros"


# Por qué no hay semilla (va en la respuesta de /api/parecidas_lista, el front lo muestra).
SEED_NOT_FOUND = "semilla_no_resuelta"          # Deezer contestó y ningún resultado es el tema
DEEZER_UNAVAILABLE = "deezer_no_disponible"     # Deezer no contestó: no se sabe si está
NOT_SEARCHABLE = "sin_artista_o_titulo"         # no quedó artista o título para preguntarle a Deezer


def _buscar_verificado(entrada: track_identity.Identity,
                       duracion: float | None = None) -> tuple[dict | None, bool | None]:
    """(track de Deezer que ES la entrada o None, Deezer contestó alguna consulta; None si
    no había nada que consultar).

    Todas las consultas de la entrada van en paralelo y se juntan (sin repetir ids, en el
    orden de las consultas); de ese pool se acepta solo lo que pasa la regla de identidad
    (`track_identity.pick_track`, con la duración solo para desempatar). Nunca el primer
    resultado "porque sí"."""
    consultas = track_identity.deezer_queries(entrada)
    if not consultas:
        return None, None
    limite = track_identity.search_limit(entrada)
    with ThreadPoolExecutor(max_workers=min(4, len(consultas))) as ex:
        respuestas = list(ex.map(lambda q: _get(f"{DEEZER}/search?q={quote(q)}&limit={limite}"), consultas))
    contesto = any(isinstance(r, dict) and "data" in r for r in respuestas)
    pool, vistos = [], set()
    for r in respuestas:
        for t in (r or {}).get("data") or []:
            if t.get("id") not in vistos:
                vistos.add(t.get("id"))
                pool.append(t)
    return track_identity.pick_track(entrada, pool, duracion), contesto


def resolver_seed_detalle(titulo: str, artista: str, isrc: str | None = None,
                          duracion: float | None = None) -> tuple[dict | None, str | None]:
    """(semilla de Deezer, None) o (None, motivo). La semilla es el tema pedido o nada:
    1) si hay ISRC (lo trae SoundCloud) y el track de Deezer CONFIRMA lo que dice el upload
       (misma versión; mismo título o duración a ±5 %), es ese track;
    2) si no, título y artista normalizados y la regla de identidad de `track_identity`.
    Medido sobre 40 + 160 entradas reales: 0 sustituciones."""
    entrada = track_identity.parse_entry(titulo, artista)
    consulto = contesto = False
    if isrc:
        consulto = True
        t = _get(f"{DEEZER}/track/isrc:{quote(isrc)}")
        contesto = bool(t)
        if t.get("id") and (t.get("artist") or {}).get("id"):
            if track_identity.isrc_confirms(entrada, track_identity.deezer_identity(t), duracion, t.get("duration")):
                logger.info(f"🎯 Semilla por ISRC {isrc}: '{t.get('title')}' — {t['artist'].get('name')}")
                return t, None
            logger.info(f"🔎 El ISRC {isrc} es '{t.get('title')}' y el upload dice '{titulo}': no lo tomo.")
    seed, contesto_busqueda = _buscar_verificado(entrada, duracion)
    if seed:
        return seed, None
    if contesto_busqueda is not None:
        consulto = True
    if not consulto:
        motivo = NOT_SEARCHABLE
    else:
        motivo = SEED_NOT_FOUND if (contesto or contesto_busqueda) else DEEZER_UNAVAILABLE
    logger.info(f"🚫 Sin semilla para '{titulo}' — {artista}: {motivo}")
    return None, motivo


def _resolver_seed(titulo: str, artista: str, genero_hint: str | None = None,
                   isrc: str | None = None, duracion: float | None = None) -> dict | None:
    """Resuelve la semilla en Deezer, o None. Nunca otra canción: antes devolvía el primer
    resultado de la primera búsqueda con resultados (From The Top de IMMINENT → WAP de Cardi B).
    `genero_hint` ya no elige entre resultados: con la regla de identidad no hace falta y
    elegir "el de la familia" era otra forma de aceptar un tema distinto."""
    return resolver_seed_detalle(titulo, artista, isrc, duracion)[0]


def _bpm(track_id: int) -> int:
    return int(_get(f"{DEEZER}/track/{track_id}").get("bpm") or 0)


def _dist_bpm(seed: int, otro: int) -> float:
    """Distancia de BPM tolerante a mitad/doble tiempo (half/double-time)."""
    if not seed or not otro:
        return 999.0
    return min(abs(seed - otro), abs(seed - otro * 2), abs(seed - otro / 2))


def _bajar_preview(preview_url: str) -> str | None:
    """Descarga el preview de 30s a un archivo temporal único (thread-safe)."""
    import os
    if not preview_url:
        return None
    r = requests.get(preview_url, headers=_UA, timeout=30)
    r.raise_for_status()
    fd, ruta = tempfile.mkstemp(suffix=".mp3", prefix="dz_prev_")
    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    return ruta


# Análisis de previews ya hechos (f32): repetir parecidas (o dos semillas de la misma escena)
# volvía a bajar y analizar los mismos 20-30 previews, ~13-17 s medidos. La clave es la ruta
# del preview SIN la query (la query es un token firmado que cambia; el archivo es el mismo).
_ANALYSIS_CACHE: dict[tuple[str, int], dict] = {}
_ANALYSIS_MAX = 5000


def _analizar_preview(preview_url: str, dur: int = 30) -> dict | None:
    """Baja el preview y saca BPM+tono+camelot con librosa."""
    clave = ((preview_url or "").split("?", 1)[0], dur)
    with _GET_LOCK:
        hit = _ANALYSIS_CACHE.get(clave)
    if hit is not None:
        return dict(hit)
    res = _analizar_preview_sin_cache(preview_url, dur)
    if res:
        with _GET_LOCK:
            if len(_ANALYSIS_CACHE) >= _ANALYSIS_MAX:
                _ANALYSIS_CACHE.clear()
            _ANALYSIS_CACHE[clave] = dict(res)
    return res


def _analizar_preview_sin_cache(preview_url: str, dur: int) -> dict | None:
    ruta = None
    try:
        import analisis_audio
        ruta = _bajar_preview(preview_url)
        return analisis_audio.bpm_y_tono(ruta, dur=dur) if ruta else None
    except Exception as e:
        logger.warning(f"⚠️ No pude analizar tono del preview: {e}")
        return None
    finally:
        if ruta:
            try:
                __import__("os").remove(ruta)
            except OSError:
                pass


def _bpm_de_preview(preview_url: str) -> int | None:
    """BPM calculado con librosa sobre el preview (fallback cuando Deezer da 0)."""
    ruta = None
    try:
        import analisis_audio
        ruta = _bajar_preview(preview_url)
        return analisis_audio.bpm_rapido(ruta, dur=30) if ruta else None
    except Exception as e:
        logger.warning(f"⚠️ Fallback de BPM por preview falló: {e}")
        return None
    finally:
        if ruta:
            try:
                __import__("os").remove(ruta)
            except OSError:
                pass


_META_CACHE: dict[tuple, dict] = {}


def _genero_de_album(album_id) -> str | None:
    if not album_id:
        return None
    gens = (_get(f"{DEEZER}/album/{album_id}").get("genres") or {}).get("data") or []
    return gens[0]["name"] if gens else None


def _buscar_meta(entrada: track_identity.Identity, usar_preview: bool) -> dict:
    """Busca el tema en Deezer y devuelve {bpm, genero}. Con la MISMA regla de identidad que
    la semilla: si no se puede demostrar que el resultado es el tema, los dos quedan vacíos
    (la fila no muestra nada; antes mostraba el BPM y el género del primer resultado, que
    podía ser otra canción)."""
    t, _ = _buscar_verificado(entrada)
    if not t:
        return {"bpm": None, "genero": None}
    bpm = int(t.get("bpm") or 0) or _bpm(t["id"])
    if not bpm and usar_preview and t.get("preview"):  # Deezer sin BPM → librosa
        bpm = _bpm_de_preview(t["preview"])
    genero = _genero_de_album((t.get("album") or {}).get("id"))
    return {"bpm": bpm or None, "genero": genero}


def meta_de(titulo: str, artista: str = "", usar_preview: bool = True) -> dict:
    """BPM + género de un tema. 1º metadato de Deezer; si no tiene BPM, lo
    calcula con librosa sobre el preview de 30s. Cacheado."""
    entrada = track_identity.parse_entry(titulo, artista)
    if not entrada.base_title:
        return {"bpm": None, "genero": None}
    clave = (entrada.artists, entrada.feat, entrada.base_title, entrada.version)
    if clave not in _META_CACHE:
        _META_CACHE[clave] = _buscar_meta(entrada, usar_preview)
    return _META_CACHE[clave]


def bpm_de(titulo: str, artista: str = "", usar_preview: bool = True) -> int | None:
    return meta_de(titulo, artista, usar_preview).get("bpm")


def _camelot_dist(a: str | None, b: str | None) -> int:
    """Distancia armónica (rueda Camelot). 0=misma key, 1=compatible, 2=lejos."""
    if not a or not b or a == "?" or b == "?":
        return 2
    try:
        na, la = int(a[:-1]), a[-1]
        nb, lb = int(b[:-1]), b[-1]
    except (ValueError, IndexError):
        return 2
    if a == b:
        return 0
    if la == lb and abs(na - nb) in (1, 11):  # ±1 en la rueda (mismo lado)
        return 1
    if na == nb and la != lb:                  # relativo mayor/menor
        return 1
    return 2


def _albums_recientes(artist_id, cutoff: str, max_albums: int = 2) -> list[tuple]:
    """(album_id, release_date, cover) de los álbumes del artista posteriores a cutoff."""
    data = _get(f"{DEEZER}/artist/{artist_id}/albums?limit=25").get("data") or []
    rec = [(al["id"], al.get("release_date") or "", al.get("cover_medium") or "")
           for al in data if (al.get("release_date") or "") >= cutoff]
    rec.sort(key=lambda x: x[1], reverse=True)
    return rec[:max_albums]


def _tracks_de_album(album_id, release: str, cover: str, max_tracks: int = 2) -> list[dict]:
    al = _get(f"{DEEZER}/album/{album_id}")
    gens = (al.get("genres") or {}).get("data") or []  # el género viene gratis acá
    genero = gens[0]["name"] if gens else None
    out = []
    for t in ((al.get("tracks") or {}).get("data") or [])[:max_tracks]:
        t["_release"] = release
        t["_cover"] = cover or (al.get("cover_medium") or "")
        t["_genero"] = genero
        out.append(t)
    return out


_DETALLE_SIN_SEMILLA = {
    SEED_NOT_FOUND: "No está en Deezer: no encontré este tema con el mismo artista y título, "
                    "y sin el tema no hay con qué comparar.",
    DEEZER_UNAVAILABLE: "No pude consultar Deezer (no contestó), así que no sé si el tema está. "
                        "Probá de nuevo en un rato.",
    NOT_SEARCHABLE: "No pude sacar artista y título de este resultado, así que no busqué el tema "
                    "en Deezer.",
}


def sin_semilla(motivo: str) -> dict:
    """Respuesta cuando no hay semilla verificada: nunca una lista armada con otro tema."""
    return {"exito": False, "motivo": motivo, "mensaje": "Similitud no disponible para este track",
            "detalle": _DETALLE_SIN_SEMILLA.get(motivo, _DETALLE_SIN_SEMILLA[SEED_NOT_FOUND])}


def construir_playlist(titulo: str, artista: str = "", total: int = 25,
                       analizar_tono: bool = True, genero_hint: str | None = None,
                       isrc: str | None = None, duracion: float | None = None) -> dict:
    import datetime
    seed, motivo = resolver_seed_detalle(titulo, artista, isrc, duracion)
    if not seed:
        return sin_semilla(motivo or SEED_NOT_FOUND)

    artist_id = seed["artist"]["id"]
    seed_gen = _genero_de_album((seed.get("album") or {}).get("id"))
    fam_obj = _familia(genero_hint) or _familia(seed_gen)   # familia objetivo de las parecidas
    logger.info(f"🎚️  Semilla género: {seed_gen or '?'} → familia '{fam_obj or '?'}'"
                + (f" (pista: {genero_hint})" if genero_hint else ""))
    # El análisis de la semilla (bajar el preview + librosa, 3-6 s medidos) no depende de la
    # búsqueda de candidatos: corre en paralelo con ella y se espera recién para el puntaje.
    seed_pool = ThreadPoolExecutor(max_workers=1)
    seed_future = seed_pool.submit(_analizar_preview, seed.get("preview")) if analizar_tono else None

    cutoff = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
    related = _get(f"{DEEZER}/artist/{artist_id}/related?limit=12").get("data") or []
    artistas = [artist_id] + [a["id"] for a in related]
    logger.info(f"🔎 Buscando lanzamientos recientes (desde {cutoff}) de {len(artistas)} artistas…")

    # Candidatos = tracks de álbumes RECIENTES de esos artistas (no hits viejos).
    # Dedup por (artista, título) para no repetir el mismo tema en otro álbum.
    pool: dict[int, dict] = {}
    vistos: set[tuple[str, str]] = set()

    def _agregar(t: dict, release: str, cover: str):
        if t["id"] == seed["id"] or not t.get("preview"):
            return
        k = (t["artist"]["name"].lower(), re.sub(r"\s+", " ", t["title"].lower()).strip())
        if k in vistos:
            return
        vistos.add(k)
        t["_release"] = release
        t["_cover"] = cover or (t.get("album") or {}).get("cover_medium", "")
        pool[t["id"]] = t

    # Los pedidos a Deezer van EN PARALELO (antes en serie: medido, 30-39 pedidos de ~2 s =
    # 50-75 s de los 150-190 s de parecidas). El pool se arma DESPUÉS, recorriendo en el mismo
    # orden de siempre (artista → álbum → track): el dedup por (artista, título) se queda con
    # el mismo tema que antes y la lista no depende de qué pedido contesta primero.
    # Dos tandas: primero los álbumes recientes de TODOS los artistas, después el detalle de
    # TODOS esos álbumes (con una tanda por artista, cada uno esperaba sus dos pedidos en serie).
    with ThreadPoolExecutor(max_workers=_DEEZER_WORKERS) as ex:
        per_artist = list(ex.map(lambda aid: _albums_recientes(aid, cutoff, max_albums=2), artistas))
        albums = [al for als in per_artist for al in als]          # mismo orden que antes
        details = list(ex.map(lambda al: _tracks_de_album(al[0], al[1], al[2], max_tracks=3), albums))
    for (_alb_id, rd, cover), tracks in zip(albums, details, strict=True):
        for t in tracks:
            _agregar(t, rd, cover)

    # Backfill con hits si hay pocos recientes (quedan al final por menos recencia)
    if len(pool) < total:
        with ThreadPoolExecutor(max_workers=_DEEZER_WORKERS) as ex:
            tops = list(ex.map(lambda aid: _get(f"{DEEZER}/artist/{aid}/top?limit=3").get("data") or [],
                               artistas))
        for top in tops:
            for t in top:
                _agregar(t, "", (t.get("album") or {}).get("cover_medium", ""))

    # Semilla: BPM + key con librosa (mismo método que los candidatos)
    seed_tono = seed_future.result() if seed_future else None
    seed_pool.shutdown(wait=False)
    seed_bpm = (seed_tono or {}).get("bpm") or _bpm(seed["id"])
    seed_camelot = (seed_tono or {}).get("camelot")
    logger.info(f"🎚️  Semilla: '{seed['title']}' — {seed_bpm or '?'} BPM"
                + (f", {seed_tono['tono']} ({seed_camelot})" if seed_tono else ""))

    candidatos = list(pool.values())

    # Filtro por familia de género: sacar lo que sea de OTRA familia (ej. rap cuando
    # la semilla es hard techno). Los de género desconocido se dejan (suelen ser del
    # mismo artista). Solo se aplica si quedan suficientes, para no vaciar la lista.
    if fam_obj and fam_obj != "otros":
        mismos = [t for t in candidatos
                  if _familia(t.get("_genero")) in (None, fam_obj)]
        if len(mismos) >= 6:
            descartados = len(candidatos) - len(mismos)
            candidatos = mismos
            if descartados:
                logger.info(f"🧹 Descarté {descartados} candidatos de otra familia (≠ {fam_obj}).")

    candidatos = candidatos[:30]  # acotar para el análisis de audio
    logger.info(f"🎧 Analizando key + BPM de {len(candidatos)} candidatos (librosa)…")
    # 16 y no 8 (f32): medido, cada preview tarda ~4 s en bajar y librosa ~0,5 s; con 8 la
    # tanda esperaba a la red, no a la CPU.
    with ThreadPoolExecutor(max_workers=_PREVIEW_WORKERS) as ex:
        analisis = list(ex.map(lambda t: _analizar_preview(t.get("preview"), dur=20), candidatos))

    hoy = datetime.date.today().isoformat()
    for t, a in zip(candidatos, analisis):
        a = a or {}
        t["_bpm"] = a.get("bpm")
        t["_tono"] = a.get("tono")
        t["_camelot"] = a.get("camelot")
        harm = _camelot_dist(seed_camelot, t["_camelot"])
        bpm_d = min(_dist_bpm(seed_bpm, t["_bpm"] or 0), 40)
        reciente = 1 if t.get("_release") else 0
        # menor score = mejor: domina la compatibilidad armónica, luego BPM, luego recencia
        t["_score"] = harm * 1000 + bpm_d * 10 - reciente * 5

    candidatos.sort(key=lambda t: t["_score"])
    elegidos = candidatos[:total]

    etiqueta = {0: "🎯 misma key", 1: "✓ compatible", 2: ""}
    canciones = [{
        "titulo": t["title"],
        "artista": t["artist"]["name"],
        "duracion": t.get("duration", 0),
        "fuente": "deezer",
        "url": t.get("link", ""),
        "preview_url": t.get("preview", ""),
        "thumbnail": t.get("_cover") or (t.get("album") or {}).get("cover_medium", ""),
        "bpm": t.get("_bpm") or None,
        "tono": t.get("_tono"),
        "camelot": t.get("_camelot"),
        "compat": etiqueta.get(_camelot_dist(seed_camelot, t.get("_camelot")), ""),
        "genero": t.get("_genero"),
        "anio": (t.get("_release") or "")[:4] or None,
    } for t in elegidos]

    return {
        "exito": True,
        "seed": {
            "titulo": seed["title"], "artista": seed["artist"]["name"],
            "bpm": seed_bpm or None,
            "tono": (seed_tono or {}).get("tono"),
            "camelot": seed_camelot,
        },
        "canciones": canciones,
    }


if __name__ == "__main__":
    import sys, json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t = sys.argv[1] if len(sys.argv) > 1 else "Bangarang"
    a = sys.argv[2] if len(sys.argv) > 2 else "Skrillex"
    print(json.dumps(construir_playlist(t, a), ensure_ascii=False, indent=2))
