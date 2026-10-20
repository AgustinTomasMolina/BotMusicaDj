"""Registro de fuentes externas (f64): `{nombre: proveedor}` y la razón REAL de cada fallo.

Cada proveedor declara sus capacidades ("search", "preview", "download"), si pide clave y su
timeout por pedido. El registro:

- saltea las que no están habilitadas (sin clave) y lo dice UNA vez en el log
  ("Jamendo: sin clave"), sin romper nada;
- convierte cualquier falla en un `ProviderError` con su motivo: sin clave, no contestó
  (timeout), bloqueó (HTTP 498), no tiene el tema, no permite descargas, falló (otra cosa).
  Nunca se mezclan: un 498 no es "no contestó a tiempo";
- busca el preview de un tema para la medición con la regla de identidad (`find_preview`).

La identidad (`match_preview`) es estricta a propósito: medir el tema EQUIVOCADO y mostrar su
BPM es mentir, peor que un "?" (spec §6). Se reusa `track_identity` sin tocar sus listas
congeladas:
1. `pick_track_evidencia(..., audio_exacto=True)`: mismo título base, misma versión, artista en
   común, ambigüedad entre obras, invitados y la duración que delata otra edición. Es la
   misma regla con la que la Station elige su semilla.
2. Encima, la versión del elegido tiene que ser IGUAL a la del tema (`Identity.version`): la
   regla de la Radio Edit por duración (`_edicion_por_duracion`) acepta otra etiqueta y acá
   no se acepta: un "Radio Edit" no se mide en nombre del original, ni un remix, ni un Extended.
3. Con las dos duraciones conocidas, tienen que cuadrar a ±max(5 s, 3 %) (`TOLERANCIA_*`).
   La duración que cuenta es la del TEMA que informa la fuente, no la del preview (30 s).
   Por qué esa tolerancia: Deezer redondea a segundos y un upload de SoundCloud del mismo
   master suele diferir en 1-3 s de silencio; 3 % deja a un tema de 6 min en ±11 s. La de
   `duraciones_cuadran` (±max(10 s, 10 %)) es para ofrecer una versión a bajar, donde el
   usuario elige; acá nadie mira antes de que se muestre el número, y un 5:00 contra un 5:30
   puede ser otra edición con otro tempo de intro.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import track_identity

from .base import (
    DownloadNotAllowed,
    ExternalAudioSourceProvider,
    MissingKey,
    NotFound,
    ProviderError,
    ProviderTimeout,
    SourceTrack,
)

logger = logging.getLogger("bot_web")

TOLERANCIA_S = 5.0
TOLERANCIA_REL = 0.03


def duracion_cuadra(tema_s: float, fuente_s: float) -> bool:
    return abs(tema_s - fuente_s) <= max(TOLERANCIA_S, TOLERANCIA_REL * tema_s)


@dataclass(frozen=True)
class PreviewMatch:
    track: SourceTrack | None
    motivo: str | None = None        # por qué no hay (None si hay)
    grado: str | None = None         # el de `track_identity.EVIDENCE_GRADES`
    display: str = ""                # la fuente que lo dio ("Deezer")


def _as_dict(t: SourceTrack) -> dict:
    """La forma que leen `deezer_identity` / `pick_track_evidencia` (title, artist.name, duration)."""
    return {"title": t.title, "artist": {"name": t.artist}, "duration": t.duration_s, "_st": t}


def match_preview(lecturas: list, duration_s, candidates: list[SourceTrack], display: str) -> PreviewMatch:
    """El candidato que ES el tema (ver el docstring del módulo) o None con el motivo preciso.

    `lecturas`: las `Identity` del tema (la normal y, si la hay, la de "A - B" al revés; lo que
    arma `soundcloud_station._lecturas`). `duration_s`: la del tema (None = no se sabe)."""
    no_tiene = f"{display} no tiene este tema"
    if not candidates:
        return PreviewMatch(None, no_tiene, display=display)
    dur = track_identity.duration_or_none(duration_s)
    pool = [_as_dict(c) for c in candidates]
    motivo = no_tiene
    for lectura in lecturas:
        best, grado = track_identity.pick_track_evidencia(lectura, pool, dur, audio_exacto=True)
        if best is None:
            # Más preciso que "no tiene": había el tema pero no se pudo confirmar, u otra versión.
            idents = [track_identity.deezer_identity(t) for t in pool]
            if any(track_identity.is_same_track(lectura, i) for i in idents):
                motivo = f"{display} tiene un tema con ese nombre pero no pude confirmar que sea la misma grabación"
            elif any(track_identity.same_title(lectura, i) and track_identity.artists_match(lectura, i)
                     and i.version != lectura.version for i in idents):
                motivo = f"{display} solo tiene otra versión de este tema"
            continue
        ident = track_identity.deezer_identity(best)
        if ident.version != lectura.version:
            motivo = f"{display} solo tiene otra versión de este tema"
            continue
        st: SourceTrack = best["_st"]
        if dur is not None and st.duration_s is not None and not duracion_cuadra(dur, st.duration_s):
            motivo = (f"{display} tiene un tema con ese nombre pero dura distinto "
                      f"({st.duration_s:.0f} s contra {dur:.0f} s): no lo mido")
            continue
        if not st.preview_url:
            motivo = f"{display} no tiene preview de este tema"
            continue
        return PreviewMatch(st, None, grado, display)
    return PreviewMatch(None, motivo, display=display)


class Registry:
    def __init__(self, providers=()):
        self._providers: dict[str, ExternalAudioSourceProvider] = {}
        self._avisado: set[str] = set()
        for p in providers:
            self.register(p)

    def register(self, provider: ExternalAudioSourceProvider) -> None:
        if not provider.name or provider.name in self._providers:
            raise ValueError(f"fuente sin nombre o repetida: {provider.name!r}")
        self._providers[provider.name] = provider

    def names(self) -> list[str]:
        return list(self._providers)

    def get(self, name: str) -> ExternalAudioSourceProvider | None:
        return self._providers.get(name)

    def _habilitada(self, p: ExternalAudioSourceProvider) -> bool:
        if p.enabled():
            return True
        if p.name not in self._avisado:
            self._avisado.add(p.name)
            logger.info(f"ℹ️ {p.display}: sin clave (la fuente queda apagada).")
        return False

    def enabled(self, capability: str | None = None) -> list[ExternalAudioSourceProvider]:
        """Las fuentes habilitadas (con clave si la piden) que tienen esa capacidad."""
        return [p for p in self._providers.values()
                if (capability is None or capability in p.capabilities) and self._habilitada(p)]

    def _usable(self, name: str, capability: str) -> ExternalAudioSourceProvider:
        p = self._providers.get(name)
        if p is None:
            raise ProviderError(name, "fuente desconocida")
        if capability not in p.capabilities:
            if capability == "download":
                raise DownloadNotAllowed(p.display)
            raise ProviderError(p.display, f"no sabe hacer «{capability}»")
        if not self._habilitada(p):
            raise MissingKey(p.display)
        return p

    @staticmethod
    def _run(p: ExternalAudioSourceProvider, fn):
        """Corre `fn` y deja cualquier falla como un `ProviderError` con su motivo real."""
        try:
            return fn()
        except ProviderError:
            raise
        except NotImplementedError as e:
            raise DownloadNotAllowed(p.display, str(e) or "") from None
        except Exception as e:  # noqa: BLE001 — un proveedor roto no tumba al que llama
            if any(k.__name__ == "TimeoutError" or k.__name__.endswith("Timeout") for k in type(e).__mro__):
                raise ProviderTimeout(p.display) from None
            logger.warning(f"⚠️ {p.display}: error inesperado ({type(e).__name__}).")
            raise ProviderError(p.display, type(e).__name__) from None

    def search(self, name: str, query: str, limit: int = 10) -> list[SourceTrack]:
        p = self._usable(name, "search")
        return self._run(p, lambda: p.search(query, limit))

    def get_details(self, name: str, track_id: str) -> SourceTrack:
        p = self._usable(name, "search")
        return self._run(p, lambda: p.get_details(track_id))

    def download(self, name: str, track_id: str, destination: Path, *, stem: str | None = None) -> Path:
        p = self._usable(name, "download")
        return self._run(p, lambda: p.download_item(track_id, destination, stem=stem))

    def find_preview(self, lecturas: list, duration_s) -> PreviewMatch:
        """El preview de una fuente que ES el tema, o el motivo por el que no hay.

        Prueba las fuentes con "preview" en orden de registro; si ninguna lo tiene, el motivo
        es el de la primera (hoy hay una sola: Deezer). Un fallo de red se informa tal cual
        ("Deezer no contestó"), nunca como "no lo tiene"."""
        provs = self.enabled("preview")
        if not provs:
            return PreviewMatch(None, "No hay ninguna fuente de previews habilitada")
        motivos = []
        for p in provs:
            try:
                cands, vistos = [], set()
                for lectura in lecturas:
                    for c in self._run(p, lambda lec=lectura, prov=p: prov.search_identity(lec, 10)):
                        if c.id not in vistos:
                            vistos.add(c.id)
                            cands.append(c)
                m = match_preview(lecturas, duration_s, cands, p.display)
            except NotFound as e:
                m = PreviewMatch(None, e.motivo, display=p.display)
            except ProviderError as e:
                m = PreviewMatch(None, e.motivo, display=p.display)
            if m.track is not None:
                return m
            motivos.append(m.motivo)
        return PreviewMatch(None, motivos[0])


_default: Registry | None = None


def default() -> Registry:
    """El registro de la app: Deezer (siempre) y Jamendo (habilitado solo con su clave).
    Se arma al primer uso, después de que `server` cargó el .env."""
    global _default
    if _default is None:
        from .deezer import DeezerProvider
        from .jamendo import JamendoProvider
        _default = Registry([DeezerProvider(), JamendoProvider()])
    return _default
