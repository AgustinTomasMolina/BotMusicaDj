"""Contrato de una fuente externa de audio (f64).

Una fuente es un `ExternalAudioSourceProvider`: busca (`search`), trae el detalle de un tema
(`get_details`) y, SOLO si la fuente lo permite, lo baja (`download_item`; por defecto no:
NotImplementedError "no permite descargas"). Lo que devuelve es un `SourceTrack` inmutable.

Por qué SÍNCRONO y no async: todo lo que va a usar esto ya corre en hilos. La búsqueda de
`server._mix_fuentes` llama a cada plataforma desde un ThreadPoolExecutor con funciones
bloqueantes (`search_agent`, `scrapers`, todas con `requests`), y la medición de la Station
(`station_mezcla`) corre en su propio pool de hilos. Una fuente async obligaría a levantar un
event loop adentro de esos hilos para no ganar nada: el cuello es la red de UNA fuente, no la
concurrencia entre muchas.

Errores: una fuente nunca devuelve [] para decir "no pude" (eso es "no lo tiene", f40). Lanza
un `ProviderError` cuyo `motivo` es la razón REAL en español, sin URLs ni claves: sin clave,
no contestó (timeout), bloqueó (HTTP 498), no tiene el tema, no permite descargas o falló.
"""
from __future__ import annotations

import abc
import re
from dataclasses import dataclass
from pathlib import Path

# Lo que se guarda cuando la fuente no dice licencia u origen (CLAUDE.md: nunca uno inventado).
NO_DECLARADO = "no declarado"


@dataclass(frozen=True)
class SourceTrack:
    source: str                     # nombre de la fuente en el registro ("deezer", "jamendo")
    id: str                         # id del tema EN esa fuente
    title: str
    artist: str
    duration_s: float | None        # la del TEMA según la fuente (no la del preview)
    license: str = NO_DECLARADO
    origin: str = NO_DECLARADO      # de dónde sale: la página del tema en la fuente
    preview_url: str | None = None
    downloadable: bool = False


# ---------------------------------------------------------------- errores con motivo real

class ProviderError(Exception):
    """La fuente no pudo hacer lo pedido. `kind` dice qué pasó; `motivo`, cómo se lo cuenta."""

    kind = "error"

    def __init__(self, display: str, detail: str = ""):
        self.display = display
        self.detail = detail
        super().__init__(self.motivo)

    @property
    def motivo(self) -> str:
        return f"{self.display} falló ({self.detail})" if self.detail else f"{self.display} falló"


class MissingKey(ProviderError):
    kind = "sin_clave"

    @property
    def motivo(self) -> str:
        return f"{self.display}: sin clave"


class ProviderTimeout(ProviderError):
    kind = "timeout"

    @property
    def motivo(self) -> str:
        return f"{self.display} no contestó"


class ProviderBlocked(ProviderError):
    """La fuente contestó, pero con un rechazo (HTTP 4xx/5xx o un error de cuota en el cuerpo)."""

    kind = "bloqueo"

    def __init__(self, display: str, status: int | None = None, detail: str = ""):
        self.status = status
        super().__init__(display, detail)

    @property
    def motivo(self) -> str:
        if self.status is not None:
            return f"{self.display} bloqueó (HTTP {self.status})"
        return f"{self.display} bloqueó ({self.detail})" if self.detail else f"{self.display} bloqueó"


class NotFound(ProviderError):
    kind = "no_encontrado"

    @property
    def motivo(self) -> str:
        return self.detail or f"{self.display} no tiene este tema"


class DownloadNotAllowed(ProviderError):
    kind = "sin_descarga"

    @property
    def motivo(self) -> str:
        return self.detail or f"{self.display} no permite descargas"


# ---------------------------------------------------------------- HTTP común

def http_json(display: str, url: str, params: dict, timeout_s: float, http_get=None):
    """GET que devuelve el JSON o lanza el `ProviderError` que corresponde.

    El mensaje NUNCA lleva la URL ni los parámetros: en Jamendo la clave viaja en la query
    (`client_id`) y un `str(e)` de requests la imprime entera."""
    if http_get is None:
        import requests
        http_get = requests.get
    try:
        r = http_get(url, params=params, timeout=(min(5.0, timeout_s), timeout_s))
    except Exception as e:  # noqa: BLE001 — timeout, conexión, DNS: el tipo alcanza
        if any(k.__name__ == "TimeoutError" or k.__name__.endswith("Timeout") for k in type(e).__mro__):
            raise ProviderTimeout(display) from None
        raise ProviderError(display, type(e).__name__) from None
    status = getattr(r, "status_code", None)
    if status == 404:
        raise NotFound(display)
    if status != 200:
        raise ProviderBlocked(display, status if isinstance(status, int) else None)
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        raise ProviderError(display, "respuesta que no es JSON") from None


_NOMBRE_INVALIDO = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_stem(name: str) -> str:
    """Nombre de archivo sin extensión que Windows acepta (la misma regla que `server._safe_name`)."""
    return _NOMBRE_INVALIDO.sub("_", name or "")[:60].strip(" .") or "cancion"


# ---------------------------------------------------------------- el contrato

class ExternalAudioSourceProvider(abc.ABC):
    name: str = ""                       # clave en el registro
    display: str = ""                    # cómo la ve el usuario
    capabilities: frozenset = frozenset()  # "search", "preview", "download"
    requires_key: bool = False
    timeout_s: float = 8.0               # por pedido de red (lo usa `http_json`)
    preview_hosts: tuple = ()            # sufijos de host de donde puede venir el preview

    def enabled(self) -> bool:
        """¿Se puede usar con esta instalación? Las que piden clave dicen False sin ella."""
        return True

    @abc.abstractmethod
    def search(self, query: str, limit: int = 10) -> list[SourceTrack]:
        """Temas para un texto libre. [] = la fuente contestó y no tiene nada."""

    @abc.abstractmethod
    def get_details(self, track_id: str) -> SourceTrack:
        """Un tema por su id; `NotFound` si no existe."""

    def search_identity(self, identity, limit: int = 10) -> list[SourceTrack]:
        """Candidatos para una `track_identity.Identity` (lo usa el plan B de la medición).
        Por defecto, artista + título base como texto libre."""
        return self.search(f"{identity.artist_text} {identity.query_title}".strip(), limit)

    def download_item(self, track_id: str, destination: Path, *, stem: str | None = None) -> Path:
        """Baja el tema a la carpeta `destination` y devuelve la ruta. Por defecto no se puede."""
        raise NotImplementedError(f"{self.display or self.name} no permite descargas")
