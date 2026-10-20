"""Jamendo: búsqueda y descarga LEGALES (f64), solo si hay clave.

Jamendo publica música con licencias Creative Commons y su API v3 dice, tema por tema, la
licencia (`license_ccurl`) y si el autor permite bajarlo (`audiodownload_allowed`). Sin
`JAMENDO_CLIENT_ID` la fuente queda apagada: no se busca ahí y no se rompe nada.

La clave viaja en la query (`client_id`), así que ningún mensaje ni log lleva la URL.

Descarga:
- solo si `audiodownload_allowed` es true, y se vuelve a preguntar a la API justo antes (no se
  confía en lo que mande el cliente);
- el host del archivo tiene que ser de Jamendo (https, `*.jamendo.com`);
- se baja a `<nombre>.mp3.part`, creado en EXCLUSIVA (es la reserva del nombre), y al terminar
  se publica con una operación atómica que FALLA si el destino ya existe (`_publicar`): nunca
  se pisa un archivo que ya estaba. Si el proceso se corta a la mitad, lo único que queda es
  ese `.part` (que nadie confunde con un tema), nunca un `.mp3` vacío: por eso no hace falta
  un barrido al arrancar, que tendría que adivinar qué `.part` son nuestros;
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
            base = safe_stem(stem or f"{t.title} - {t.artist}".strip(" -"))
            part, fd = _reservar_part(dest, base, ".mp3")
            final = self._bajar(url, part, fd, dest, base, ".mp3")
        finally:
            self._turnos.release()
        logger.info(f"⬇️  Jamendo: bajado «{final.name}» ({t.license}).")
        return final

    def _bajar(self, url: str, part: Path, fd: int, dest: Path, base: str, ext: str) -> Path:
        """Baja a `part` (ya creado en exclusiva, abierto en `fd`) y lo publica con nombre libre.
        Ante cualquier falla borra SOLO ese `.part`, que es nuestro."""
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
            return _publicar(part, dest, base, ext)
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


def _nombres(dest: Path, stem: str, ext: str):
    """«x.mp3», «x (2).mp3», «x (3).mp3»… (el sufijo entra en los 60 de `safe_stem`)."""
    for k in range(1, 1000):
        yield dest / (f"{stem}{ext}" if k == 1 else f"{stem[:54].rstrip()} ({k}){ext}")


def _reservar_part(dest: Path, stem: str, ext: str) -> tuple[Path, int]:
    """Crea en EXCLUSIVA «<nombre libre>.part» y devuelve (ruta, fd abierto para escribir). Se
    saltea un nombre cuyo final ya existe (del usuario u otra descarga) o cuyo `.part` existe
    (otra descarga en curso, o un resto de una cortada: no se toca, no es seguro que sea nuestro)."""
    for p in _nombres(dest, stem, ext):
        if p.exists():
            continue
        part = p.with_name(p.name + ".part")
        try:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
        except FileExistsError:
            continue
        return part, fd
    raise ProviderError("Jamendo", "no encontré un nombre libre para el archivo")


def _publicar(part: Path, dest: Path, stem: str, ext: str) -> Path:
    """Renombra `part` a un nombre final libre SIN pisar nunca: en Windows `os.rename` falla si
    el destino existe; en el resto, `os.link` (atómico, falla si existe) y después se borra el
    `.part`. Si entre la reserva y el final alguien creó ese nombre, se prueba el siguiente.
    Sin hard links (un disco FAT, uno de red): se comprueba que no exista y se renombra; ahí
    queda una ventana mínima, que es lo mejor que ese disco permite."""
    preferido = part.with_name(part.name.removesuffix(".part"))
    for p in [preferido, *(q for q in _nombres(dest, stem, ext) if q != preferido)]:
        if p.exists():
            continue
        try:
            if os.name == "nt":
                os.rename(part, p)
            else:
                try:
                    os.link(part, p)
                except FileExistsError:
                    raise
                except OSError:
                    if p.exists():
                        continue
                    os.rename(part, p)
                    return p
                os.unlink(part)
            return p
        except FileExistsError:
            continue
    raise ProviderError("Jamendo", "no encontré un nombre libre para el archivo")
