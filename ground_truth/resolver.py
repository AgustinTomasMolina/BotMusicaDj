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

Un ambiguo puede serlo por dos razones distintas: que sean copias del MISMO audio (da
igual cuál se use) o versiones DISTINTAS (la ambigüedad es real). Este módulo no las
separa a propósito — hacerlo requiere decodificar audio y acá no entra ni numpy. Si en
algún caso hace falta desambiguar, `calidad.duplicados.desambiguar(list(r.candidatos))`
lo resuelve y explica por qué no se llama desde acá.

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


def dentro_de(ruta: str, raices: list[str]) -> bool:
    """¿El archivo REAL (symlinks y junctions resueltos) está adentro de alguna raíz real?

    Para quien recibe la ubicación de afuera (el XML que sube el navegador, f53): un Location
    puede apuntar a cualquier archivo de la PC, y un symlink adentro de una raíz puede salir de
    ella. Lo que no está adentro de una raíz permitida no se usa."""
    try:
        real = Path(ruta).resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    for raiz in raices:
        try:
            if real.is_relative_to(Path(raiz).resolve(strict=True)):
                return True
        except (OSError, RuntimeError):
            continue
    return False


def _por_cola(location: str, raices: list[str]) -> list[str]:
    """Los archivos que existen colgando de cada raíz la COLA de la ruta original, de la más
    larga a la más corta (mínimo carpeta + archivo: el nombre solo es el paso 3). Devuelve los
    de la cola más larga que exista; si en ese largo hay varios (dos raíces con la misma
    carpeta), los devuelve todos y quien llama decide si es ambiguo.

    Generaliza el ancla: '.../Users/x/Music/Techno/a.wav' se encuentra en '<raíz>/Techno/a.wav'
    sin saber cómo se llamaba la carpeta de arriba en la PC donde se exportó el XML."""
    partes = [p for p in location.replace("\\", "/").split("/") if p and p != "."]
    # Un ':' fuera de la unidad haría que `join` saltara a otra unidad: no es una cola.
    if len(partes) < 2 or ".." in partes or any(":" in p for p in partes[1:]):
        return []
    for largo in range(len(partes) - 1, 1, -1):
        cola = partes[-largo:]
        hits = [c for raiz in raices
                if os.path.isfile(c := os.path.join(raiz, *cola))]
        if hits:
            return hits
    return []


def resolver(location: str, raices: list[str],
             indice: dict[str, list[str]] | None = None,
             ancla: str | None = _ANCLA, *, por_cola: bool = False,
             confinar: bool = False) -> Resolucion:
    """Ubica el audio real. Devuelve una `Resolucion` (ok / ambiguo / no-encontrado).

    `ancla`: la carpeta de la ruta original a partir de la cual se cuelga de cada raíz (paso 2;
    None = no se usa). `por_cola=True` agrega el paso 2b: la cola de la ruta, sin ancla
    (`_por_cola`). `confinar=True` (f53, el XML llega desde el navegador): solo vale un archivo
    que esté adentro de una raíz (`dentro_de`), también en el paso 1 y para los homónimos. Los
    defaults son los de siempre: el benchmark y la sesión de ground truth no cambian."""
    if not location:
        return Resolucion(None, NO_ENCONTRADO)

    def vale(ruta: str) -> bool:
        return not confinar or dentro_de(ruta, raices)

    # 1 y 2 son inequívocos: apuntan a UNA ruta concreta que existe.
    # isfile y no exists: un Location que es una CARPETA no es un audio (auditoría f53).
    if os.path.isfile(location) and vale(location):
        return Resolucion(location, OK)

    cola = _cola_desde_ancla(location, ancla) if ancla else None
    if cola:
        for raiz in raices:
            cand = os.path.join(raiz, cola.replace("/", os.sep))
            if os.path.isfile(cand) and vale(cand):
                return Resolucion(cand, OK)

    if por_cola:
        hits = [h for h in _distintos(_por_cola(location, raices)) if vale(h)]
        if len(hits) > 1:
            return Resolucion(None, AMBIGUO, tuple(hits))
        if hits:
            return Resolucion(hits[0], OK)

    # 3: por basename. Acá sí puede haber varios candidatos distintos.
    if indice is None:
        indice = construir_indice(raices)
    hits = [h for h in indice.get(os.path.basename(location).lower()) or [] if vale(h)]
    if not hits:
        return Resolucion(None, NO_ENCONTRADO)

    unicos = _distintos(hits)
    if len(unicos) > 1:
        return Resolucion(None, AMBIGUO, tuple(unicos))
    return Resolucion(unicos[0], OK)
