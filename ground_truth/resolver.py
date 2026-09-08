"""Resuelve la ruta de un track del XML de Rekordbox a un archivo real en disco.

El XML guarda rutas absolutas de cuando se exportó (ej. .../OneDrive/Escritorio/GROOOOVE/x.wav).
Si esos archivos se movieron (p. ej. a un pendrive D:\\, o quedaron como placeholders de
OneDrive sin bajar), hay que reubicarlos. Estrategia, en orden:
  1. La ruta original tal cual.
  2. Mapear la parte relativa a una carpeta 'ancla' (por defecto 'Escritorio') dentro de
     cada raíz de búsqueda: .../Escritorio/GROOOOVE/x.wav → <raíz>/GROOOOVE/x.wav.
  3. Por nombre de archivo (basename) indexando las raíces — última red, tolera reorganización.

NO copia ni modifica los audios (regla del proyecto: los originales no se tocan). Solo lee.
"""
import os
from pathlib import Path

# Carpeta 'ancla': lo que va después de ella en la ruta original se cuelga de cada raíz.
_ANCLA = "Escritorio"


def construir_indice(raices: list[str]) -> dict[str, list[str]]:
    """basename.lower() → [rutas absolutas] recorriendo las raíces una sola vez."""
    idx: dict[str, list[str]] = {}
    for raiz in raices:
        if not os.path.isdir(raiz):
            continue
        for root, _, files in os.walk(raiz):
            for f in files:
                idx.setdefault(f.lower(), []).append(os.path.join(root, f))
    return idx


def _cola_desde_ancla(ruta: str, ancla: str = _ANCLA) -> str | None:
    """De '.../Escritorio/GROOOOVE/x.wav' devuelve 'GROOOOVE/x.wav'."""
    partes = ruta.replace("\\", "/").split("/")
    if ancla in partes:
        i = len(partes) - 1 - partes[::-1].index(ancla)  # último match del ancla
        return "/".join(partes[i + 1:])
    return None


def resolver(location: str, raices: list[str],
             indice: dict[str, list[str]] | None = None,
             ancla: str = _ANCLA) -> str | None:
    """Devuelve la ruta real del audio, o None si no se encuentra en ninguna raíz."""
    if not location:
        return None
    if os.path.exists(location):
        return location

    cola = _cola_desde_ancla(location, ancla)
    if cola:
        for raiz in raices:
            cand = os.path.join(raiz, cola.replace("/", os.sep))
            if os.path.exists(cand):
                return cand

    if indice is None:
        indice = construir_indice(raices)
    hits = indice.get(os.path.basename(location).lower())
    if hits:
        return hits[0]  # si hay varios homónimos, el primero (raro; los tracks son únicos)
    return None
