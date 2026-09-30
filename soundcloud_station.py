"""Station de SoundCloud de un tema (f34): ~50 temas del mismo estilo, como el "Station" de la web.

Por qué: "Temas parecidos" arma todo desde Deezer, y para escenas chicas (hard bounce, techno
de artistas chicos) Deezer no tiene relacionados: "GIB MIR BOUNCE!" da 2 y "IMMINENT - From
The Top" ni está. La Station de SoundCloud sí tiene esa escena. Es el RECOMENDADOR de
SoundCloud (sus internals no se conocen: co-escucha, uploader, sello), no una medición
nuestra: la pantalla lo dice así y no le agrega BPM ni tonalidad.

Capas:
  - `SoundCloudApi` es el ÚNICO que habla con api-v2 (adaptador): client_id, timeouts,
    renovación ante 401/403 y traducción de cualquier falla a `SoundCloudError(motivo)`.
  - `build_station` es la lógica: resolver la semilla (por id si el resultado es de
    SoundCloud; si no, buscándola y aceptándola SOLO con la regla de identidad de
    `track_identity`), pedir la Station, mapear los temas y armar la respuesta.

La API v2 no está documentada y el client_id es el público que yt-dlp saca de la web
(`track_identity.soundcloud_client_id`). Si cambia o falla, la respuesta es
{exito: false, motivo, mensaje}, nunca un 500. El client_id nunca va al log.
"""
from __future__ import annotations

import logging
import re
import threading
import time

import track_identity

logger = logging.getLogger("soundcloud_station")

API = "https://api-v2.soundcloud.com"
STATION_LIMIT = 50            # lo que pide la web de SoundCloud
SEARCH_LIMIT = 20
TIMEOUT = (5, 10)             # (conectar, leer); medido: la Station contesta en 1,5-2 s
CACHE_TTL_S = 10 * 60
CACHE_MAX = 256
_UA = {"User-Agent": "Mozilla/5.0"}

# Id numérico de SoundCloud: el mismo que acepta el proxy de audio (source_audio._SC_NUM),
# así todo tema que sale de acá se puede pedir a /api/fuente/audio.
SC_ID = re.compile(r"[1-9][0-9]{0,19}")
_PERMALINK = re.compile(r"https://soundcloud\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+")
_IMAGE = re.compile(r"https://[a-z0-9-]+\.sndcdn\.com/[^\s\"'<>]+")

# Por qué no hay Station (va en `motivo`; el front muestra `mensaje`).
NOT_ON_SOUNDCLOUD = "no_esta_en_soundcloud"
SOUNDCLOUD_UNAVAILABLE = "soundcloud_no_disponible"
UNEXPECTED_RESPONSE = "respuesta_inesperada"
EMPTY_STATION = "station_vacia"
INVALID_REQUEST = "pedido_invalido"

MESSAGES = {
    NOT_ON_SOUNDCLOUD: "No encontré este tema en SoundCloud.",
    SOUNDCLOUD_UNAVAILABLE: "SoundCloud no contestó. Probá de nuevo en un rato.",
    UNEXPECTED_RESPONSE: "SoundCloud contestó algo que no entiendo (¿cambió su API?).",
    EMPTY_STATION: "SoundCloud no tiene una Station para este tema.",
    INVALID_REQUEST: "Pedido inválido.",
}


class SoundCloudError(Exception):
    """Falla de SoundCloud ya traducida a un motivo. `detail` va al log (nunca el client_id)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def failure(reason: str, message: str | None = None) -> dict:
    return {"exito": False, "motivo": reason, "mensaje": message or MESSAGES[reason]}


# ---------------------------------------------------------------- adaptador de api-v2

class SoundCloudApi:
    """Cliente mínimo de api-v2. `http_get` (firma de `requests.get`) y `client_id_provider`
    (refresh: bool → client_id | None) se inyectan en los tests; por defecto son `requests` y
    `track_identity.soundcloud_client_id`."""

    def __init__(self, http_get=None, client_id_provider=None, timeout=TIMEOUT):
        self._http_get = http_get
        self._provider = client_id_provider or (lambda refresh: track_identity.soundcloud_client_id(refresh))
        self._timeout = timeout
        self._cid: str | None = None
        self._lock = threading.Lock()

    def _client_id(self, refresh: bool) -> str | None:
        with self._lock:
            if self._cid and not refresh:
                return self._cid
        try:
            cid = self._provider(refresh)
        except Exception as e:          # red, yt-dlp que cambió…: el mensaje puede traer URLs
            logger.info(f"ℹ️ No pude obtener el client_id de SoundCloud: {type(e).__name__}")
            cid = None
        with self._lock:
            self._cid = cid if isinstance(cid, str) and cid else None
            return self._cid

    def get_json(self, path: str, params: dict):
        """GET a api-v2 con el client_id; con 401/403 lo renueva UNA vez y reintenta."""
        http_get = self._http_get
        if http_get is None:
            import requests
            http_get = requests.get
        for attempt in (0, 1):
            cid = self._client_id(refresh=attempt == 1)
            if not cid:
                raise SoundCloudError(SOUNDCLOUD_UNAVAILABLE, "sin client_id")
            try:
                r = http_get(API + path, params={**params, "client_id": cid}, headers=_UA, timeout=self._timeout)
            except Exception as e:      # timeout, conexión: el mensaje trae la URL con el client_id
                raise SoundCloudError(SOUNDCLOUD_UNAVAILABLE, type(e).__name__) from None
            status = getattr(r, "status_code", None)
            if status in (401, 403) and attempt == 0:
                continue
            if status != 200:
                raise SoundCloudError(SOUNDCLOUD_UNAVAILABLE, f"HTTP {status}")
            try:
                return r.json()
            except Exception as e:
                raise SoundCloudError(UNEXPECTED_RESPONSE, f"cuerpo no JSON ({type(e).__name__})") from None
        raise SoundCloudError(SOUNDCLOUD_UNAVAILABLE, "client_id rechazado dos veces")

    def search_tracks(self, query: str, limit: int = SEARCH_LIMIT) -> list:
        return _collection(self.get_json("/search/tracks", {"q": query, "limit": limit}))

    def station_tracks(self, track_id: str, limit: int = STATION_LIMIT) -> list:
        return _collection(self.get_json(f"/stations/soundcloud:track-stations:{track_id}/tracks", {"limit": limit}))


def _collection(body) -> list:
    col = body.get("collection") if isinstance(body, dict) else None
    if not isinstance(col, list):
        raise SoundCloudError(UNEXPECTED_RESPONSE, "sin 'collection'")
    return col


# ---------------------------------------------------------------- mapeo de un tema

def _ms_to_s(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float) or not v > 0 or v != v or v == float("inf"):
        return None
    return round(v / 1000, 1)


def map_track(t) -> dict | None:
    """Un track de api-v2 → la forma de un resultado de la tabla (como los de SoundCloud de
    `search_agent`: titulo, artista = uploader, duracion en s, url, fuente, video_id,
    thumbnail) + `permalink`, `reproducible` y `solo_preview`. None si no es un tema usable.

    - `solo_preview`: Go+ o sin stream completo. SoundCloud lo marca con policy "SNIP" y con
      transcodings `snipped`; lo que suena son 30 s. `duracion` es la del tema entero
      (full_duration): la fila dice cuánto dura el tema y que acá solo hay un fragmento.
    - `reproducible`: hay un stream "progressive" sin cifrar. El proxy de audio
      (source_audio) solo abre audio por HTTP directo: un tema que solo trae HLS (o HLS
      cifrado, como algunos de sellos grandes) no suena en la barra."""
    if not isinstance(t, dict) or t.get("kind", "track") != "track":
        return None
    tid = t.get("id")
    if isinstance(tid, bool) or not isinstance(tid, int) or not SC_ID.fullmatch(str(tid)):
        return None
    title = t.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    user = t.get("user") if isinstance(t.get("user"), dict) else {}
    uploader = user.get("username") if isinstance(user.get("username"), str) else ""
    media = t.get("media") if isinstance(t.get("media"), dict) else {}
    trans = [x for x in (media.get("transcodings") or []) if isinstance(x, dict)] \
        if isinstance(media.get("transcodings"), list) else []
    protocols = {(x.get("format") or {}).get("protocol") for x in trans if isinstance(x.get("format"), dict)}
    preview = t.get("policy") == "SNIP" or any(x.get("snipped") is True for x in trans)
    playable = (t.get("policy") != "BLOCK" and t.get("streamable") is not False and "progressive" in protocols)
    permalink = t.get("permalink_url")
    permalink = permalink if isinstance(permalink, str) and _PERMALINK.fullmatch(permalink) else None
    thumb = next((u for u in (t.get("artwork_url"), user.get("avatar_url"))
                  if isinstance(u, str) and _IMAGE.fullmatch(u)), None)
    return {
        "titulo": title.strip(), "artista": uploader.strip(),
        "duracion": _ms_to_s(t.get("full_duration")) or _ms_to_s(t.get("duration")),
        # Descargar y el plan B del reproductor usan la URL; el audio de la barra, el id.
        "url": permalink or f"https://api.soundcloud.com/tracks/{tid}",
        "fuente": "soundcloud", "video_id": str(tid), "thumbnail": thumb, "permalink": permalink,
        "reproducible": playable, "solo_preview": preview,
    }


# ---------------------------------------------------------------- semilla

def _identity(c: dict) -> track_identity.Identity:
    return track_identity.parse_entry(c["title"], c["artist"])


def find_seed(api: SoundCloudApi, titulo: str, artista: str, duracion: float | None) -> tuple[dict, str]:
    """(tema de SoundCloud que ES el pedido, grado de evidencia) o SoundCloudError.

    Una búsqueda por "artista título [versión]" y, del pool, SOLO lo que acepta
    `track_identity.pick_track_evidencia` (las mismas reglas que la semilla de parecidas:
    mismo título, versión y artista; invitados; ambigüedad; la duración que delata otra
    edición). Si no, la otra lectura de "A - B" sobre el mismo pool. Nunca el primero
    "porque sí": sin aceptado → NOT_ON_SOUNDCLOUD."""
    entrada = track_identity.parse_entry(titulo, artista)
    if not entrada.query_title or not entrada.artists:
        raise SoundCloudError(NOT_ON_SOUNDCLOUD, "sin artista o título para buscar")
    q = " ".join(p for p in (entrada.artist_text, entrada.query_title, entrada.version_text) if p)
    pool = []
    for raw in api.search_tracks(q):
        m = map_track(raw)
        if m:
            # `duration` en segundos, como la espera la regla de identidad.
            pool.append({"title": m["titulo"], "artist": m["artista"], "duration": m["duracion"], "item": m})
    lecturas = [entrada]
    al_reves = track_identity.parse_entry_swapped(titulo, artista)
    if al_reves is not None:
        lecturas.append(al_reves)
    for lectura in lecturas:
        track, grado = track_identity.pick_track_evidencia(lectura, pool, duracion, identify=_identity)
        if track:
            return track["item"], grado
    raise SoundCloudError(NOT_ON_SOUNDCLOUD, f"{len(pool)} resultados, ninguno es el tema")


# ---------------------------------------------------------------- Station + caché

_cache: dict[str, tuple[float, dict | None, list]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def station_for(api: SoundCloudApi, track_id: str) -> tuple[dict | None, list]:
    """(la semilla tal como la trae la Station o None, los demás temas EN EL ORDEN de
    SoundCloud). La Station repite la semilla primero: se saca (y cualquier repetido).
    Cacheado 10 min por id; los fallos y las Stations vacías no se guardan."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(track_id)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1], list(hit[2])
    col = api.station_tracks(track_id)
    mapped = [m for m in map(map_track, col) if m]
    if col and not mapped:
        raise SoundCloudError(UNEXPECTED_RESPONSE, f"{len(col)} temas sin la forma esperada")
    seed, items, seen = None, [], {track_id}
    for m in mapped:
        if m["video_id"] == track_id:
            seed = seed or m
            continue
        if m["video_id"] in seen:
            continue
        seen.add(m["video_id"])
        items.append(m)
    if items:
        with _cache_lock:
            if len(_cache) >= CACHE_MAX:
                _cache.clear()
            _cache[track_id] = (now, seed, list(items))
    return seed, items


_default_api: SoundCloudApi | None = None


def default_api() -> SoundCloudApi:
    global _default_api
    if _default_api is None:
        _default_api = SoundCloudApi()
    return _default_api


def build_station(fuente: str, fuente_id: str, titulo: str, artista: str,
                  duracion: float | None, api: SoundCloudApi | None = None) -> dict:
    """La respuesta de /api/station. Los parámetros ya vienen validados por el endpoint.

    {exito: true, origen: "soundcloud_station", semilla: {...}, items: [...], total} o
    {exito: false, motivo, mensaje}. Ninguna falla sale como excepción."""
    api = api or default_api()
    t0 = time.perf_counter()
    try:
        if fuente == "soundcloud":
            track_id, seed_from_search, evidencia = fuente_id, None, "id"
        else:
            seed_from_search, evidencia = find_seed(api, titulo, artista, duracion)
            track_id = seed_from_search["video_id"]
            logger.info(f"🎯 En SoundCloud ({evidencia}): «{seed_from_search['titulo']}» — {seed_from_search['artista']}")
        seed_station, items = station_for(api, track_id)
    except SoundCloudError as e:
        logger.info(f"📻 Station de «{titulo}»: {e.reason} ({e.detail})")
        return failure(e.reason)
    except Exception as e:              # un bug acá no puede ser un 500
        logger.warning(f"⚠️ Station de «{titulo}»: error inesperado {type(e).__name__}")
        return failure(UNEXPECTED_RESPONSE)
    if not items:
        return failure(EMPTY_STATION)
    s = seed_station or seed_from_search or {}
    semilla = {
        "titulo": s.get("titulo") or titulo, "artista": s.get("artista") or artista,
        "video_id": track_id, "permalink": s.get("permalink"), "thumbnail": s.get("thumbnail"),
        "evidencia": evidencia,
    }
    logger.info(f"📻 Station de «{semilla['titulo']}»: {len(items)} temas en {time.perf_counter() - t0:.1f} s.")
    return {"exito": True, "origen": "soundcloud_station", "semilla": semilla, "items": items, "total": len(items)}
