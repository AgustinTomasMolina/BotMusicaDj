"""Playlist de canciones parecidas (Nivel 1 + Nivel 2), sin API key.

Nivel 1 — datos: usa la API pública de Deezer (sin key) para traer candidatos
por artistas relacionados (mismo estilo/escena) + el propio artista.
Nivel 2 — audio: ordena por cercanía de BPM, y analiza el tono/key del tema
semilla con librosa (sobre el preview de 30s) para mostrar mezcla armónica.
"""
import logging
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests

logger = logging.getLogger("similares")
DEEZER = "https://api.deezer.com"
_UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str) -> dict:
    try:
        return requests.get(url, headers=_UA, timeout=20).json()
    except Exception as e:
        logger.warning(f"⚠️ Deezer falló: {e}")
        return {}


def _seed_limpio(t: str) -> str:
    """Saca corchetes de sello/tags y paréntesis de ruido (conserva ' - ' y remixes)."""
    t = re.sub(r"\[[^\]]*\]", " ", t or "")   # [Ultra Records], [Free DL]…
    t = _RUIDO.sub(" ", t)                    # (Official Video), (HD)…
    return re.sub(r"\s+", " ", t).strip(" -")


def _variantes_seed(titulo: str, artista: str) -> list[str]:
    """Distintas formas de buscar el tema en Deezer, de la más específica a la más
    laxa. Los títulos de YouTube vienen sucios ('Artista - Tema [Sello]', 'A x B'…),
    así que probamos varias hasta que una matchee."""
    titulo, artista = (titulo or "").strip(), (artista or "").strip()
    limpio = _seed_limpio(titulo)
    out: list[str] = []

    def add(q: str):
        q = re.sub(r"\s+", " ", q or "").strip(" -")
        if q and q.lower() not in {v.lower() for v in out}:
            out.append(q)

    add(f"{artista} {limpio}")
    add(limpio)
    # Título tipo 'Artista - Tema': separar y quedarse con el artista principal
    if " - " in limpio:
        izq, der = (p.strip() for p in limpio.split(" - ", 1))
        principal = re.split(r"\s*(?:\bx\b|&|,|\bfeat\.?\b|\bft\.?\b)\s*", izq, flags=re.I)[0].strip()
        add(f"{izq} {der}")
        add(f"{principal} {der}")
        add(der)
    add(f"{artista} {titulo}")   # crudo, por si la limpieza sacó de más
    add(titulo)
    return out


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


def _resolver_seed(titulo: str, artista: str, genero_hint: str | None = None) -> dict | None:
    """Resuelve la semilla en Deezer. Si hay pista de género, entre los primeros
    resultados elige el que sea de esa familia (evita agarrar el 'Rise of the
    Machines' de rap cuando buscás el de hard techno)."""
    fam = _familia(genero_hint) if genero_hint else None
    fallback = None
    for q in _variantes_seed(titulo, artista):
        data = _get(f"{DEEZER}/search?q={quote(q)}&limit=5").get("data") or []
        if not data:
            continue
        if fallback is None:
            fallback = data[0]
        if not fam:
            return data[0]
        for t in data:
            g = _genero_de_album((t.get("album") or {}).get("id"))
            if _familia(g) == fam:
                return t
    return fallback


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


def _analizar_preview(preview_url: str, dur: int = 30) -> dict | None:
    """Baja el preview y saca BPM+tono+camelot con librosa."""
    ruta = None
    try:
        import os
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


_RUIDO = re.compile(
    r"[\(\[][^\)\]]*?(official|video|audio|lyric|visualizer|\bhd\b|\b4k\b|\bmv\b|"
    r"remaster|explicit|full\s*album|monstercat|free\s*download)[^\)\]]*?[\)\]]",
    re.I,
)


def _limpiar_titulo(titulo: str, artista: str) -> tuple[str, str]:
    """Devuelve (artista, track) limpios para matchear en Deezer."""
    t = _RUIDO.sub(" ", titulo)
    t = re.sub(r"\s+", " ", t).strip(" -")
    art = (artista or "").strip()
    if " - " in t:  # típico "Artista - Tema"
        izq, der = (s.strip() for s in t.split(" - ", 1))
        if not art:
            art, t = izq, der
        elif izq.lower() == art.lower():
            t = der
    if art and art.lower() in t.lower():  # no repetir el artista dentro del track
        t = re.sub(re.escape(art), " ", t, flags=re.I)
        t = re.sub(r"\s+", " ", t).strip(" -")
    return art, t


_META_CACHE: dict[tuple[str, str], dict] = {}


def _genero_de_album(album_id) -> str | None:
    if not album_id:
        return None
    gens = (_get(f"{DEEZER}/album/{album_id}").get("genres") or {}).get("data") or []
    return gens[0]["name"] if gens else None


def _buscar_meta(art: str, track: str, usar_preview: bool) -> dict:
    """Busca el tema en Deezer y devuelve {bpm, genero}."""
    consultas = []
    if art:
        consultas.append(f'artist:"{art}" track:"{track}"')  # estructurada
        consultas.append(f"{art} {track}".strip())           # libre
    else:
        consultas.append(track)
    for q in consultas:
        data = _get(f"{DEEZER}/search?q={quote(q)}&limit=1").get("data") or []
        if not data:
            continue
        t = data[0]
        bpm = int(t.get("bpm") or 0) or _bpm(t["id"])
        if not bpm and usar_preview and t.get("preview"):  # Deezer sin BPM → librosa
            bpm = _bpm_de_preview(t["preview"])
        genero = _genero_de_album((t.get("album") or {}).get("id"))
        return {"bpm": bpm or None, "genero": genero}
    return {"bpm": None, "genero": None}


def _meta_core(art: str, track: str, usar_preview: bool = True) -> dict:
    if not track:
        return {"bpm": None, "genero": None}
    clave = (art.lower(), track.lower())
    if clave not in _META_CACHE:
        _META_CACHE[clave] = _buscar_meta(art, track, usar_preview)
    return _META_CACHE[clave]


def meta_de(titulo: str, artista: str = "", usar_preview: bool = True) -> dict:
    """BPM + género de un tema. 1º metadato de Deezer; si no tiene BPM, lo
    calcula con librosa sobre el preview de 30s. Cacheado."""
    art, track = _limpiar_titulo(titulo, artista)
    return _meta_core(art, track, usar_preview)


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


def construir_playlist(titulo: str, artista: str = "", total: int = 25,
                       analizar_tono: bool = True, genero_hint: str | None = None) -> dict:
    import datetime
    seed = _resolver_seed(titulo, artista, genero_hint)
    if not seed:
        return {"exito": False, "mensaje": "No encontré el tema en Deezer."}

    artist_id = seed["artist"]["id"]
    seed_gen = _genero_de_album((seed.get("album") or {}).get("id"))
    fam_obj = _familia(genero_hint) or _familia(seed_gen)   # familia objetivo de las parecidas
    logger.info(f"🎚️  Semilla género: {seed_gen or '?'} → familia '{fam_obj or '?'}'"
                + (f" (pista: {genero_hint})" if genero_hint else ""))
    # Semilla: BPM + key con librosa (mismo método que los candidatos)
    seed_tono = _analizar_preview(seed.get("preview")) if analizar_tono else None
    seed_bpm = (seed_tono or {}).get("bpm") or _bpm(seed["id"])
    seed_camelot = (seed_tono or {}).get("camelot")
    logger.info(f"🎚️  Semilla: '{seed['title']}' — {seed_bpm or '?'} BPM"
                + (f", {seed_tono['tono']} ({seed_camelot})" if seed_tono else ""))

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

    for aid in artistas:
        for alb_id, rd, cover in _albums_recientes(aid, cutoff, max_albums=2):
            for t in _tracks_de_album(alb_id, rd, cover, max_tracks=3):
                _agregar(t, rd, cover)

    # Backfill con hits si hay pocos recientes (quedan al final por menos recencia)
    if len(pool) < total:
        for aid in artistas:
            for t in _get(f"{DEEZER}/artist/{aid}/top?limit=3").get("data") or []:
                _agregar(t, "", (t.get("album") or {}).get("cover_medium", ""))

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
    with ThreadPoolExecutor(max_workers=8) as ex:
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
