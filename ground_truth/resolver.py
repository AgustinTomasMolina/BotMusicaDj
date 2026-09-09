"""Resuelve la ruta de un track del XML de Rekordbox a un archivo real en disco.

El XML guarda rutas absolutas de cuando se exportó (ej. .../OneDrive/Escritorio/GROOOOVE/x.wav).
Si esos archivos se movieron (p. ej. a un pendrive D:\\, o quedaron como placeholders de
OneDrive sin bajar), hay que reubicarlos. Estrategia, en orden:
  1. La ruta original tal cual.
  2. Mapear la parte relativa a una carpeta 'ancla' (por defecto 'Escritorio') dentro de
     cada raíz de búsqueda: .../Escritorio/GROOOOVE/x.wav → <raíz>/GROOOOVE/x.wav.
  3. Por nombre de archivo (basename) indexando las raíces — última red, tolera reorganización.

El paso 3 es el peligroso: en una biblioteca de DJ los homónimos son NORMALES (original vs
edit, master v1 vs v2, el mismo track en WAV y en MP3). Si se elige uno al azar, el motor
analiza otro audio y el resultado se ve idéntico a un error de algoritmo. Por eso, cuando
el basename tiene más de un candidato, se devuelve estado 'ambiguo' y el track queda FUERA
del cómputo en vez de resolverse a la suerte.

NO copia ni modifica los audios (regla del proyecto: los originales no se tocan). Solo lee.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

# Carpeta 'ancla': lo que va después de ella en la ruta original se cuelga de cada raíz.
_ANCLA = "Escritorio"

OK = "ok"
AMBIGUO = "ambiguo"
NO_ENCONTRADO = "no-encontrado"


@dataclass(frozen=True)
class Resolucion:
    """Resultado de resolver un track.

    `ruta` solo viene con estado OK. En 'ambiguo', `candidatos` trae los homónimos
    encontrados para que el reporte pueda mostrarlos y el usuario desambigüe.
    """

    ruta: str | None
    estado: str
    candidatos: tuple[str, ...] = field(default=())

    def __bool__(self) -> bool:
        return self.estado == OK


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


def _distintos(rutas: list[str]) -> list[str]:
    """Homónimos que apuntan al MISMO archivo (por ruta normalizada) no son ambigüedad."""
    vistos, unicos = set(), []
    for r in rutas:
        try:
            clave = os.path.normcase(str(Path(r).resolve()))
        except OSError:
            clave = os.path.normcase(os.path.abspath(r))
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(r)
    return unicos


def resolver(location: str, raices: list[str],
             indice: dict[str, list[str]] | None = None,
             ancla: str = _ANCLA) -> Resolucion:
    """Ubica el audio real. Devuelve una `Resolucion` (ok / ambiguo / no-encontrado)."""
    if not location:
        return Resolucion(None, NO_ENCONTRADO)

    # 1 y 2 son inequívocos: apuntan a UNA ruta concreta que existe.
    if os.path.exists(location):
        return Resolucion(location, OK)

    cola = _cola_desde_ancla(location, ancla)
    if cola:
        for raiz in raices:
            cand = os.path.join(raiz, cola.replace("/", os.sep))
            if os.path.exists(cand):
                return Resolucion(cand, OK)

    # 3: por basename. Acá sí puede haber varios candidatos distintos.
    if indice is None:
        indice = construir_indice(raices)
    hits = indice.get(os.path.basename(location).lower())
    if not hits:
        return Resolucion(None, NO_ENCONTRADO)

    unicos = _distintos(hits)
    if len(unicos) > 1:
        return Resolucion(None, AMBIGUO, tuple(unicos))
    return Resolucion(unicos[0], OK)
