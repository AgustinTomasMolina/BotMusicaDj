"""Deezer como plan B de la MEDICIÓN (f64): su preview de 30 s, sin clave.

No baja nada (`download_item` queda en el NotImplementedError del contrato): el catálogo de
Deezer es comercial y lo único abierto es el preview MP3 de 30 s. Sirve para medir BPM y key
de un tema de la Station cuyo audio de SoundCloud no se puede abrir (solo HLS, otro formato).

Licencia: Deezer no dice bajo qué licencia está cada tema → `no declarado` (nunca inventada).
Origen: la página del tema en Deezer (`link`).
"""
from __future__ import annotations

import track_identity

from .base import (
    NO_DECLARADO,
    ExternalAudioSourceProvider,
    NotFound,
    ProviderBlocked,
    ProviderError,
    SourceTrack,
    http_json,
)

API = "https://api.deezer.com"

# Errores que Deezer manda con HTTP 200 en el cuerpo (`{"error": {"code": N}}`).
_QUOTA = 4          # "Quota limit exceeded": es un bloqueo, no "no lo tiene"
_NO_DATA = 800      # "no data": el id no existe


def map_track(t) -> SourceTrack | None:
    """Un track de la API pública → `SourceTrack`, o None si no tiene la forma esperada."""
    if not isinstance(t, dict) or t.get("type", "track") != "track":
        return None
    tid, title = t.get("id"), t.get("title")
    artist = (t.get("artist") or {}).get("name") if isinstance(t.get("artist"), dict) else None
    if isinstance(tid, bool) or not isinstance(tid, int | str) or not str(tid).isdigit():
        return None
    if not isinstance(title, str) or not title.strip() or not isinstance(artist, str):
        return None
    dur = t.get("duration")
    dur = float(dur) if isinstance(dur, int | float) and not isinstance(dur, bool) and dur > 0 else None
    preview = t.get("preview") if isinstance(t.get("preview"), str) and t["preview"].startswith("https://") else None
    link = t.get("link") if isinstance(t.get("link"), str) and t["link"].startswith("https://") else None
    return SourceTrack(source="deezer", id=str(tid), title=title.strip(), artist=artist.strip(),
                       duration_s=dur, license=NO_DECLARADO, origin=link or NO_DECLARADO,
                       preview_url=preview, downloadable=False)


class DeezerProvider(ExternalAudioSourceProvider):
    name = "deezer"
    display = "Deezer"
    capabilities = frozenset({"search", "preview"})
    requires_key = False
    timeout_s = 8.0
    preview_hosts = (".dzcdn.net",)     # cdns-preview-*.dzcdn.net / cdnt-preview.dzcdn.net

    def __init__(self, http_get=None):
        self._http_get = http_get

    def _get(self, path: str, params: dict):
        body = http_json(self.display, API + path, params, self.timeout_s, self._http_get)
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            code = err.get("code")
            if code == _QUOTA:
                raise ProviderBlocked(self.display, detail="cuota de pedidos agotada")
            if code == _NO_DATA:
                raise NotFound(self.display)
            raise ProviderError(self.display, f"error {code}")
        if not isinstance(body, dict):
            raise ProviderError(self.display, "respuesta inesperada")
        return body

    def search(self, query: str, limit: int = 10) -> list[SourceTrack]:
        body = self._get("/search", {"q": query, "limit": int(limit)})
        data = body.get("data")
        if not isinstance(data, list):
            raise ProviderError(self.display, "respuesta sin 'data'")
        return [m for m in map(map_track, data) if m]

    def search_identity(self, identity, limit: int = 10) -> list[SourceTrack]:
        """Solo las consultas ESTRICTAS (`artist:"a" track:"t"`) de `track_identity.deezer_queries`:
        para medir no hace falta el texto libre, que trae más ruido. La primera que trae algo
        gana; como mucho tres pedidos (una por artista nombrado)."""
        for q in [q for q in track_identity.deezer_queries(identity) if q.startswith("artist:")][:3]:
            found = self.search(q, limit)
            if found:
                return found
        return []

    def get_details(self, track_id: str) -> SourceTrack:
        if not str(track_id).isdigit():
            raise NotFound(self.display)
        t = map_track(self._get(f"/track/{track_id}", {}))
        if t is None:
            raise NotFound(self.display)
        return t
