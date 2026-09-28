"""Audio de YouTube y SoundCloud para la barra, sin el video (f32).

Antes la barra tocaba YouTube con la IFrame API (el video visible en un "monitor") y
SoundCloud con su widget. El dueño no quiere ver el video: acá el backend saca con yt-dlp la
URL del audio del tema y lo sirve al navegador como un archivo más, con Range (se puede
adelantar), igual que la biblioteca local.

Por qué PROXY y no bajar el tema a un cache (medido 2026-09-28, red real, esta máquina):

    fuente       resolver  1er bloque por proxy  bajar entero (yt-dlp)
    YouTube      2,3 s     1,0 s                 3,0 s (4 MB)
    SoundCloud   3,8 s     1,1 s                 12,5 s (6 MB, su CDN da ~660 KB/s)

Con proxy empieza a sonar en ~3,5-5 s en las dos fuentes; bajando entero, SoundCloud tarda
~12 s. El proxy además no escribe nada a disco (no hay cache de archivos que limpiar). Lo
que se guarda es la RESOLUCIÓN (URL directa + headers) hasta que vence la URL.

Seguridad (SSRF / inyección):
  - El cliente NO manda una URL: manda `fuente` + `ref` (id de 11 caracteres de YouTube, id
    numérico de SoundCloud o `usuario/tema`). La URL de la fuente se ARMA acá con eso; lo que
    no matchea exactamente se rechaza con 400 sin llamar a yt-dlp.
  - yt-dlp se usa como biblioteca (sin shell ni argumentos de línea de comandos), con los
    extractores limitados a youtube/soundcloud, sin playlists y con timeout de red.
  - La URL directa que devuelve yt-dlp se valida (https y host de la CDN de la fuente) antes
    de pedirla, y las redirecciones se siguen a mano validando cada salto.
  - Ningún error sale como 500: todo termina en JSON con `error` en español.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from urllib3.util import parse_url

logger = logging.getLogger("bot_web")
router = APIRouter()

SOURCES = {"youtube": "YouTube", "soundcloud": "SoundCloud"}

_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_SC_NUM = re.compile(r"[1-9][0-9]{0,19}")
_SC_SLUG = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_-]{0,99})/([A-Za-z0-9_][A-Za-z0-9_-]{0,199})")
# Rutas de SoundCloud que no son un tema (perfil, listas): con ellas yt-dlp traería una lista.
_SC_NOT_TRACK = {"sets", "likes", "reposts", "tracks", "albums", "popular-tracks", "followers",
                 "following", "comments", "spotlight", "toptracks", "stations", "discover",
                 "search", "you", "upload", "pages", "mobile", "charts"}

# CDNs de las que puede venir el audio. Cualquier otro host (incluido localhost, una IP o un
# esquema que no sea https) se rechaza aunque venga de yt-dlp.
_ALLOWED_HOST_SUFFIXES = (".googlevideo.com", ".sndcdn.com")
# Un nombre DNS en minúsculas: letras, dígitos, punto y guion. Ver `host_allowed`.
_HOST_DNS = re.compile(r"[a-z0-9.-]+")

# Formato: solo audio, por HTTP directo (no HLS: un <audio> de Chrome no abre .m3u8 y el
# proxy no reescribe listas). webm/opus y m4a los reproduce cualquier navegador moderno.
_FORMAT = ("bestaudio[protocol^=http][ext=webm]/bestaudio[protocol^=http][ext=m4a]/"
           "bestaudio[protocol^=http][ext=mp3]/bestaudio[protocol^=http]")

RESOLVE_TIMEOUT_S = 25.0      # resolver con yt-dlp (medido: 2-4 s; con carga, hasta ~10)
SOCKET_TIMEOUT_S = 15.0       # cada pedido de red de yt-dlp
UPSTREAM_TIMEOUT = (10, 30)   # (conectar, leer) al pedir el audio a la CDN
MAX_DURATION_S = 3 * 3600     # un set de 3 h entra; algo más largo no es un tema
MAX_BYTES = 400 * 1024 * 1024
CHUNK = 64 * 1024
_CACHE_TTL_S = 30 * 60        # tope aunque la URL diga que vence más tarde
_CACHE_MAX = 256
_NEG_TTL_S = 120.0            # un fallo se recuerda 2 min (el front vuelve a preguntar el motivo)
_MAX_REDIRECTS = 3

_MIME = {"webm": "audio/webm", "m4a": "audio/mp4", "mp4": "audio/mp4", "mp3": "audio/mpeg",
         "ogg": "audio/ogg", "opus": "audio/ogg"}


class SourceAudioError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Resolved:
    url: str
    headers: dict
    mime: str
    ext: str
    duration: float | None
    size: int | None
    preview: bool
    codec: str | None
    abr: float | None
    format_id: str | None
    expires_at: float           # time.monotonic() a partir del cual se vuelve a resolver


def canonical_url(source: str, ref: str) -> str | None:
    """URL de la fuente armada ACÁ a partir de un id validado, o None si no es válido."""
    if not isinstance(source, str) or not isinstance(ref, str):
        return None
    if source == "youtube" and _YT_ID.fullmatch(ref):
        return f"https://www.youtube.com/watch?v={ref}"
    if source == "soundcloud":
        if _SC_NUM.fullmatch(ref):
            return f"https://api.soundcloud.com/tracks/{ref}"
        m = _SC_SLUG.fullmatch(ref)
        if m and m.group(1).lower() not in _SC_NOT_TRACK and m.group(2).lower() not in _SC_NOT_TRACK:
            return f"https://soundcloud.com/{m.group(1)}/{m.group(2)}"
    return None


def host_allowed(url: str) -> bool:
    # Dos cerrojos contra la diferencia de parsers: `urlparse` (que valida) y urllib3 (con el
    # que `requests` se CONECTA) no cortan la autoridad igual. Con
    # "https://127.0.0.1\.googlevideo.com/x", `urlparse` ve un host que termina en el sufijo
    # permitido y urllib3 se conecta a 127.0.0.1. Por eso: (1) el host solo puede tener los
    # caracteres de un nombre DNS —fuera `\`, `%`, `@`…— y (2) el host que ve urllib3 tiene
    # que ser exactamente el validado.
    try:
        u = urlparse(url)
        host_conexion = (parse_url(url).host or "").lower()
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and not u.username and not u.password and u.port in (None, 443) \
        and _HOST_DNS.fullmatch(host) is not None and host_conexion == host \
        and any(host.endswith(s) and len(host) > len(s) for s in _ALLOWED_HOST_SUFFIXES)


def _expiry(url: str) -> float:
    """Cuándo re-resolver: la URL de YouTube trae `expire=` (epoch); SoundCloud, `Expires=`."""
    ttl = _CACHE_TTL_S
    try:
        q = parse_qs(urlparse(url).query)
        raw = (q.get("expire") or q.get("Expires") or [None])[0]
        if raw:
            ttl = min(ttl, max(0.0, float(raw) - time.time() - 60))
    except (ValueError, TypeError):
        pass
    return time.monotonic() + ttl


def _friendly(msg: str, source_name: str) -> tuple[str, int]:
    """Mensaje de yt-dlp → texto de la app (§6: que diga qué pasó, sin el código crudo)."""
    m = (msg or "").lower()
    if "private" in m:
        return f"El tema es privado en {source_name}.", 404
    if "unavailable" in m or "does not exist" in m or "not found" in m:
        return f"El tema ya no está disponible en {source_name}.", 404
    if "sign in" in m or ("age" in m and "confirm" in m):
        return f"{source_name} pide iniciar sesión para este tema (restricción de edad o región).", 403
    if "requested format is not available" in m:
        return f"{source_name} no ofrece un audio que se pueda reproducir acá para este tema.", 502
    if "timed out" in m or "timeout" in m:
        return f"{source_name} no contestó a tiempo.", 504
    return f"No pude sacar el audio de {source_name}.", 502


def _resolve_sync(url: str, source: str) -> Resolved:
    """yt-dlp como biblioteca: sin descargar, sin playlists, solo youtube/soundcloud."""
    import yt_dlp

    name = SOURCES[source]
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
        "format": _FORMAT, "socket_timeout": SOCKET_TIMEOUT_S,
        "allowed_extractors": ["youtube", "soundcloud"],
        # Sin `player_client` a propósito: medido, con ['android', 'web'] YouTube solo ofrece
        # el formato 18 (video 360p con audio) y ningún audio solo.
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:           # DownloadError, ExtractorError, red…
        text, status = _friendly(str(e), name)
        raise SourceAudioError(text, status) from e
    if not isinstance(info, dict) or info.get("_type") in ("playlist", "multi_video") or info.get("entries"):
        raise SourceAudioError(f"Eso no es un tema de {name} (es una lista).", 400)
    direct = info.get("url")
    if not direct or not host_allowed(direct):
        raise SourceAudioError(f"{name} devolvió un audio de un lugar que no reconozco; no lo pido.", 502)
    if not str(info.get("protocol") or "").startswith("http"):
        raise SourceAudioError(f"{name} solo ofrece este tema en un formato que la barra no puede abrir.", 502)
    duration = info.get("duration")
    if duration and duration > MAX_DURATION_S:
        raise SourceAudioError(f"El tema dura más de {MAX_DURATION_S // 3600} h: no lo paso por la barra.", 413)
    size = info.get("filesize") or info.get("filesize_approx")
    if size and size > MAX_BYTES:
        raise SourceAudioError("El audio es demasiado grande para la barra.", 413)
    ext = (info.get("ext") or "").lower()
    fmt = str(info.get("format_id") or "")
    headers = {k: v for k, v in (info.get("http_headers") or {}).items()
               if isinstance(k, str) and isinstance(v, str) and k.lower() in ("user-agent", "accept", "accept-language", "referer", "origin")}
    return Resolved(
        url=direct, headers=headers, mime=_MIME.get(ext, "application/octet-stream"),
        ext=ext, duration=float(duration) if duration else None, size=int(size) if size else None,
        preview="preview" in fmt.lower() or (urlparse(direct).hostname or "").startswith("cf-preview-media."),
        codec=info.get("acodec"), abr=info.get("abr"), format_id=fmt or None, expires_at=_expiry(direct),
    )


# Hilos PROPIOS para resolver y abrir la CDN. Medido con red real: con el pool por defecto de
# asyncio (12 hilos en esta máquina) el primer play de YouTube después de una búsqueda tardó
# 27,7 s, porque esperaba detrás de los /api/meta y /api/calidad que la pantalla dispara por
# cada fila (cada uno, 5-15 s de yt-dlp/Deezer). Lo que el usuario está esperando oír no hace
# cola detrás de los análisis de fondo.
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fuente-audio")


def _in_pool(fn, *args):
    return asyncio.get_running_loop().run_in_executor(_POOL, fn, *args)


# --- cache de resoluciones (en memoria) -------------------------------------------------
_cache: dict[str, Resolved] = {}
_neg: dict[str, tuple[float, SourceAudioError]] = {}
_inflight: dict[str, asyncio.Future] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _neg.clear()


def _cache_get(key: str) -> Resolved | SourceAudioError | None:
    now = time.monotonic()
    with _lock:
        r = _cache.get(key)
        if r and now < r.expires_at:
            return r
        _cache.pop(key, None)
        n = _neg.get(key)
        if n and now - n[0] < _NEG_TTL_S:
            return n[1]
        _neg.pop(key, None)
    return None


def _cache_put(key: str, value: Resolved | SourceAudioError) -> None:
    with _lock:
        if isinstance(value, Resolved):
            if len(_cache) >= _CACHE_MAX:              # el que vence primero se va
                oldest = min(_cache, key=lambda k: _cache[k].expires_at)
                _cache.pop(oldest, None)
            _cache[key] = value
            _neg.pop(key, None)
        else:
            if len(_neg) >= _CACHE_MAX:
                _neg.clear()
            _neg[key] = (time.monotonic(), value)


def _cache_drop(key: str) -> None:
    with _lock:
        _cache.pop(key, None)


async def resolve(source: str, url: str) -> Resolved:
    """Resolución cacheada. Pedidos simultáneos del mismo tema (el <audio> abre varios al
    arrancar y al adelantar) esperan la MISMA resolución en vez de lanzar una cada uno."""
    hit = _cache_get(url)
    if isinstance(hit, Resolved):
        return hit
    if isinstance(hit, SourceAudioError):
        raise hit
    fut = _inflight.get(url)
    if fut is None:
        fut = asyncio.get_running_loop().create_future()
        _inflight[url] = fut
        try:
            res = await asyncio.wait_for(_in_pool(_resolve_sync, url, source), RESOLVE_TIMEOUT_S)
            _cache_put(url, res)
            fut.set_result(res)
        except TimeoutError:
            err = SourceAudioError(f"{SOURCES[source]} no contestó a tiempo ({RESOLVE_TIMEOUT_S:.0f} s).", 504)
            _cache_put(url, err)
            fut.set_exception(err)
        except SourceAudioError as err:
            _cache_put(url, err)
            fut.set_exception(err)
        except Exception as e:                         # nada sale como 500
            logger.warning(f"⚠️ Audio de {source}: error inesperado resolviendo: {e}")
            err = SourceAudioError(f"No pude sacar el audio de {SOURCES[source]}.", 502)
            fut.set_exception(err)
        finally:
            _inflight.pop(url, None)
        fut.exception()                                # marcar como leída (sin warning)
    return await asyncio.shield(fut)


# --- pedido a la CDN ----------------------------------------------------------------------
_RANGE = re.compile(r"bytes=(\d{0,15})-(\d{0,15})")


def _open_upstream(url: str, headers: dict):
    """GET a la CDN siguiendo redirecciones A MANO (cada salto validado). Devuelve la
    respuesta de requests en modo stream; el que llama la cierra."""
    import requests

    for _ in range(_MAX_REDIRECTS + 1):
        if not host_allowed(url):
            raise SourceAudioError("La fuente redirigió el audio a un lugar que no reconozco; no lo sigo.", 502)
        r = requests.get(url, headers=headers, stream=True, timeout=UPSTREAM_TIMEOUT, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            url = urljoin(url, r.headers["Location"])
            r.close()
            continue
        return r
    raise SourceAudioError("La fuente redirigió el audio demasiadas veces.", 502)


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers={"Cache-Control": "no-store"})


def _params(fuente: str, ref: str):
    source = (fuente or "").strip().lower()
    if source not in SOURCES:
        return None, None, _error("Fuente no soportada: solo YouTube o SoundCloud.", 400)
    url = canonical_url(source, (ref or "").strip())
    if not url:
        return None, None, _error(f"Identificador de {SOURCES[source]} inválido.", 400)
    return source, url, None


@router.get("/api/fuente/audio")
async def source_audio(request: Request, fuente: str = "", ref: str = ""):
    """El audio del tema, con Range. Errores: 400 pedido inválido, 404/403 el tema no está o
    pide sesión, 413 demasiado largo, 502 la fuente falló, 504 la fuente no contestó."""
    source, url, bad = _params(fuente, ref)
    if bad:
        return bad
    rng = request.headers.get("range")
    fwd_range = None
    if rng:
        m = _RANGE.fullmatch(rng.strip())
        if not m or (not m.group(1) and not m.group(2)):
            return _error("Rango inválido.", 416)
        fwd_range = f"bytes={m.group(1)}-{m.group(2)}"

    for attempt in range(2):
        try:
            res = await resolve(source, url)
        except SourceAudioError as e:
            return _error(e.message, e.status)
        headers = dict(res.headers)
        if fwd_range:
            headers["Range"] = fwd_range
        try:
            up = await _in_pool(_open_upstream, res.url, headers)
        except SourceAudioError as e:
            return _error(e.message, e.status)
        except Exception as e:
            logger.warning(f"⚠️ Audio de {source}: la CDN no contestó: {e}")
            return _error(f"{SOURCES[source]} no entregó el audio (sin respuesta).", 504)
        if up.status_code in (403, 404, 410) and attempt == 0:
            # La URL directa venció o quedó atada a otra sesión: se resuelve de nuevo una vez.
            up.close()
            _cache_drop(url)
            continue
        break

    if up.status_code == 416:
        up.close()
        return _error("Rango fuera del audio.", 416)
    if up.status_code not in (200, 206):
        up.close()
        return _error(f"{SOURCES[source]} no entregó el audio (HTTP {up.status_code}).", 502)

    out = {"Content-Type": res.mime, "Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    for h in ("Content-Length", "Content-Range"):
        if up.headers.get(h):
            out[h] = up.headers[h]

    def body():
        try:
            yield from up.iter_content(CHUNK)
        except Exception as e:             # la CDN cortó a mitad: se cierra la respuesta
            logger.info(f"Audio de {source}: la CDN cortó el envío ({e}).")
        finally:
            up.close()

    return StreamingResponse(body(), status_code=up.status_code, headers=out, background=BackgroundTask(up.close))


@router.get("/api/fuente/audio/info")
async def source_audio_info(fuente: str = "", ref: str = ""):
    """Lo que se sabe del audio que suena (misma resolución cacheada que el audio): la barra
    lo usa para decir la verdad, p. ej. que SoundCloud solo entrega un fragmento de 30 s."""
    source, url, bad = _params(fuente, ref)
    if bad:
        return bad
    try:
        res = await resolve(source, url)
    except SourceAudioError as e:
        return _error(e.message, e.status)
    return {"fuente": source, "preview": res.preview, "duracion": res.duration, "ext": res.ext,
            "codec": res.codec, "abr": round(res.abr) if res.abr else None}
