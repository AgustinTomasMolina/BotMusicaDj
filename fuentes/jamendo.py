"""Jamendo: búsqueda y descarga LEGALES (f64), solo si hay clave.

Jamendo publica música con licencias Creative Commons y su API v3 dice, tema por tema, la
licencia (`license_ccurl`) y si el autor permite bajarlo (`audiodownload_allowed`). Sin
`JAMENDO_CLIENT_ID` la fuente queda apagada: no se busca ahí y no se rompe nada.

La clave viaja en la query (`client_id`), así que ningún mensaje ni log lleva la URL.

Descarga:
- solo si `audiodownload_allowed` es true, y se vuelve a preguntar a la API justo antes (no se
  confía en lo que mande el cliente);
- el host del archivo tiene que ser de Jamendo (https, `*.jamendo.com`);
- se baja a `<nombre>.part` y al terminar se renombra de forma atómica sobre un nombre que se
  RESERVÓ antes (creado en exclusiva): nunca se pisa un archivo que ya estaba;
- como mucho `MAX_DESCARGAS` a la vez (semáforo) y con tope de tiempo y de tamaño.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import suppress
from pathlib import Path

from .base import (
    NO_DECLARADO,
    DownloadNotAllowed,
    ExternalAudioSourceProvider,
    MissingKey,
    NotFound,
    ProviderBlocked,
    ProviderError,
    ProviderTimeout,
    SourceTrack,
    http_json,
    safe_stem,
)

logger = logging.getLogger("bot_web")

API = "https://api.jamendo.com/v3.0"
ENV_KEY = "JAMENDO_CLIENT_ID"
MAX_DESCARGAS = 2
DOWNLOAD_TIMEOUT_S = 60.0        # por lectura de red; un MP3 de Jamendo pesa ~5-15 MB
DOWNLOAD_MAX_S = 600.0           # tope de la descarga entera (el de arriba es por lectura)
MAX_BYTES = 300 * 1024 * 1024    # un tema; más es otra cosa (o una respuesta rota)
_ESPERA_TURNO_S = 300.0          # esperar turno detrás de las otras dos descargas
_DOWNLOAD_HOSTS = (".jamendo.com",)
_CHUNK = 64 * 1024


def _https(u) -> str | None:
    return u if isinstance(u, str) and u.startswith("https://") else None


def map_track(t) -> SourceTrack | None:
    if not isinstance(t, dict):
        return None
    tid, title, artist = t.get("id"), t.get("name"), t.get("artist_name")
    if not isinstance(tid, str | int) or isinstance(tid, bool) or not str(tid).isdigit():
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    dur = t.get("duration")
    dur = float(dur) if isinstance(dur, int | float) and not isinstance(dur, bool) and dur > 0 else None
    lic = t.get("license_ccurl")
    return SourceTrack(
        source="jamendo", id=str(tid), title=title.strip(),
        artist=artist.strip() if isinstance(artist, str) else "",
        duration_s=dur,
        license=lic.strip() if isinstance(lic, str) and lic.strip() else NO_DECLARADO,
        origin=_https(t.get("shareurl")) or _https(t.get("shorturl")) or NO_DECLARADO,
        preview_url=_https(t.get("audio")),
        downloadable=t.get("audiodownload_allowed") is True,
    )


class JamendoProvider(ExternalAudioSourceProvider):
    name = "jamendo"
    display = "Jamendo"
    capabilities = frozenset({"search", "download"})
    requires_key = True
    timeout_s = 8.0

    # Compartido entre instancias: el tope es de la PC, no de cada objeto.
    _turnos = threading.BoundedSemaphore(MAX_DESCARGAS)

    def __init__(self, client_id: str | None = None, http_get=None, http_stream=None):
        self._cid = (os.getenv(ENV_KEY, "") if client_id is None else client_id).strip()
        self._http_get = http_get
        self._http_stream = http_stream

    def enabled(self) -> bool:
        return bool(self._cid)

    def _tracks(self, params: dict) -> list[dict]:
        if not self._cid:
            raise MissingKey(self.display)
        body = http_json(self.display, API + "/tracks/",
                         {"client_id": self._cid, "format": "json", **params}, self.timeout_s, self._http_get)
        head = body.get("headers") if isinstance(body, dict) else None
        if not isinstance(head, dict):
            raise ProviderError(self.display, "respuesta inesperada")
        if head.get("status") != "success":
            # El texto del error no se muestra: podría repetir lo que se mandó (la clave).
            raise ProviderBlocked(self.display, detail=f"código {head.get('code')}")
        res = body.get("results")
        if not isinstance(res, list):
            raise ProviderError(self.display, "respuesta sin 'results'")
        return res

    def search(self, query: str, limit: int = 10) -> list[SourceTrack]:
        return [m for m in map(map_track, self._tracks({"search": query, "limit": int(limit)})) if m]

    def _raw(self, track_id: str) -> dict:
        if not str(track_id).isdigit():
            raise NotFound(self.display)
        res = self._tracks({"id": str(track_id)})
        if not res or not isinstance(res[0], dict) or map_track(res[0]) is None:
            raise NotFound(self.display)
        return res[0]

    def get_details(self, track_id: str) -> SourceTrack:
        return map_track(self._raw(track_id))

    # ------------------------------------------------------------ descarga

    def download_item(self, track_id: str, destination: Path, *, stem: str | None = None) -> Path:
        raw = self._raw(track_id)
        t = map_track(raw)
        if not t.downloadable:
            raise DownloadNotAllowed(self.display, "Jamendo no permite descargar este tema (lo decidió el autor)")
        url = raw.get("audiodownload")
        import source_audio
        if not isinstance(url, str) or not source_audio.host_allowed(url, _DOWNLOAD_HOSTS):
            raise DownloadNotAllowed(self.display, "Jamendo no dio un link de descarga válido para este tema")
        dest = Path(destination)
        dest.mkdir(parents=True, exist_ok=True)
        if not self._turnos.acquire(timeout=_ESPERA_TURNO_S):
            raise ProviderTimeout(self.display)
        try:
            final = _reservar(dest, safe_stem(stem or f"{t.title} - {t.artist}".strip(" -")), ".mp3")
            try:
                self._bajar(url, final)
            except BaseException:
                with suppress(OSError):
                    final.unlink()          # era la reserva vacía: nadie más la usa
                raise
        finally:
            self._turnos.release()
        logger.info(f"⬇️  Jamendo: bajado «{final.name}» ({t.license}).")
        return final

    def _bajar(self, url: str, final: Path) -> None:
        part = final.with_name(final.name + ".part")
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
        try:
            with os.fdopen(fd, "wb") as f:
                total, t0 = 0, time.monotonic()
                for trozo in self._stream(url):
                    if time.monotonic() - t0 > DOWNLOAD_MAX_S:
                        raise ProviderTimeout(self.display)
                    total += len(trozo)
                    if total > MAX_BYTES:
                        raise ProviderError(self.display, "el archivo es demasiado grande")
                    f.write(trozo)
            if total == 0:
                raise ProviderError(self.display, "el archivo vino vacío")
            os.replace(part, final)      # atómico; `final` es la reserva propia (vacía)
        except BaseException:
            with suppress(OSError):
                part.unlink()
            raise

    def _stream(self, url: str):
        """Los bytes del archivo. Redirecciones seguidas A MANO, cada salto validado."""
        if self._http_stream is not None:
            yield from self._http_stream(url)
            return
        from urllib.parse import urljoin

        import requests
        import source_audio
        for _ in range(4):
            if not source_audio.host_allowed(url, _DOWNLOAD_HOSTS):
                raise DownloadNotAllowed(self.display, "Jamendo redirigió la descarga a un lugar que no reconozco")
            try:
                r = requests.get(url, stream=True, timeout=(10, DOWNLOAD_TIMEOUT_S), allow_redirects=False)
            except requests.Timeout:
                raise ProviderTimeout(self.display) from None
            except requests.RequestException as e:
                raise ProviderError(self.display, type(e).__name__) from None
            with r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                    url = urljoin(url, r.headers["Location"])
                    continue
                if r.status_code != 200:
                    raise ProviderBlocked(self.display, r.status_code)
                try:
                    yield from r.iter_content(_CHUNK)
                except requests.Timeout:
                    raise ProviderTimeout(self.display) from None
                except requests.RequestException as e:
                    raise ProviderError(self.display, type(e).__name__) from None
                return
        raise ProviderError(self.display, "demasiadas redirecciones")


def _reservar(dest: Path, stem: str, ext: str) -> Path:
    """Crea en EXCLUSIVA un archivo vacío con un nombre libre («x.mp3», «x (2).mp3»…) y lo
    devuelve: lo que ya existía (de otra descarga o del usuario) no se toca nunca. También se
    saltea un nombre cuyo `.part` exista (otra descarga en curso)."""
    for k in range(1, 1000):
        name = f"{stem}{ext}" if k == 1 else f"{stem[:54].rstrip()} ({k}){ext}"
        p = dest / name
        if p.with_name(p.name + ".part").exists():
            continue
        try:
            os.close(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL))
        except FileExistsError:
            continue
        return p
    raise ProviderError("Jamendo", "no encontré un nombre libre para el archivo")
