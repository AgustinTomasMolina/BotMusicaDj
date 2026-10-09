"""Importar playlists a MusiFlix (f53): desde un XML de Rekordbox y desde una carpeta de la PC.

Lógica pura de lectura: no importa `server` ni `db`. El server pone los endpoints, las raíces
permitidas (`MUSIFLIX_LIBRARY_ROOTS`) y la base; acá se decide qué es un XML aceptable, qué
playlists trae, dónde está cada archivo y qué carpeta se puede listar.

Reglas que son el contrato de este módulo:

1. Nunca se modifican los audios del dueño: solo se leen (tags y existencia).
2. Lo que se muestra en pantalla NO lleva rutas absolutas de la PC: nombres de archivo, nombres
   de playlist y carpetas relativas a su raíz. La ruta resuelta viaja solo hacia la base.
3. XML seguro: un cuerpo con `<!DOCTYPE` o `<!ENTITY` se rechaza ANTES de parsear (la expansión
   de entidades es el ataque clásico de "mil millones de risas"; Rekordbox no escribe DTD). XML
   roto, vacío o que no es de Rekordbox: error con el motivo, nunca una excepción suelta.
4. Un archivo se usa solo si está ADENTRO de una raíz permitida, resuelto (symlinks y
   junctions incluidos). Homónimos distintos = ambiguo: no se elige uno a la suerte
   (`ground_truth.resolver`).
5. BPM, key y género son los del XML (o los tags): marcados como de Rekordbox, nunca medidos.
   Un tema sin género queda sin género.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlparse

# Tope del XML que se acepta (el de una colección de ~10k temas pesa ~20 MB).
XML_MAX_BYTES = 150 * 1024 * 1024
# Sesiones de lectura en memoria: el resumen se pide, el dueño tilda, y después importa.
SESION_TTL_S = 15 * 60
SESION_MAX = 4
# Tope de archivos por carpeta importada.
CARPETA_MAX_ARCHIVOS = 2000


class Rechazo(Exception):
    """Un pedido que no se puede atender, con el código HTTP y el motivo para la pantalla."""

    def __init__(self, status: int, mensaje: str) -> None:
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje


# =============================================================================================
#  XML de Rekordbox
# =============================================================================================

def validar_xml(datos: bytes) -> None:
    """Rechaza lo que no se parsea nunca: vacío, enorme, con DTD/entidades, o que no es UTF-8."""
    if not datos or not datos.strip():
        raise Rechazo(400, "El archivo está vacío: exportá de nuevo la colección desde Rekordbox "
                           "(Archivo › Exportar colección en formato xml).")
    if len(datos) > XML_MAX_BYTES:
        raise Rechazo(413, f"El XML pesa más de {XML_MAX_BYTES // (1024 * 1024)} MB: es más "
                           f"grande que cualquier colección de Rekordbox. No lo leo.")
    if datos.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in datos[:4096]:
        raise Rechazo(400, "El XML no está en UTF-8 (Rekordbox lo exporta en UTF-8): no lo leo.")
    if b"<!DOCTYPE" in datos or b"<!ENTITY" in datos:
        raise Rechazo(400, "El XML trae una declaración DOCTYPE/ENTITY. Rekordbox no las escribe "
                           "y sirven para atacar al lector: no lo leo.")


def ruta_de_location(location: str) -> str:
    """'file://localhost/C:/Users/x/a%20b.wav' → 'C:/Users/x/a b.wav' (decodificado).

    Igual que `ground_truth.rekordbox._ruta_local`: Rekordbox codifica espacios (%20), '#'
    (%23), '%' (%25) y los acentos en UTF-8 (%C3%A1). `unquote` no convierte '+' (bien: un '+'
    en un nombre de archivo es un '+')."""
    if not location:
        return ""
    p = urlparse(location)
    return unquote(p.path).lstrip("/") if p.scheme == "file" else unquote(location)


def nombre_de_ruta(ruta: str) -> str:
    """El nombre del archivo de una ruta Windows o POSIX, sin tocar el disco."""
    return PureWindowsPath(ruta).name if "\\" in ruta or re.match(r"^[A-Za-z]:", ruta) \
        else PurePosixPath(ruta).name


def _float(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


@dataclass
class Coleccion:
    """Lo que se leyó del XML: los temas por TrackID y las playlists en orden de aparición."""
    nombre_xml: str
    tracks: dict[str, dict]
    playlists: list[dict]
    # Resolución de cada Location: location → (estado, ruta | None, homónimos)
    resoluciones: dict[str, tuple[str, str | None, int]] = field(default_factory=dict)


def leer_rekordbox(datos: bytes, nombre_xml: str) -> Coleccion:
    """Parsea el XML (ya validado con `validar_xml`). Levanta `Rechazo` con el motivo."""
    from ground_truth.rekordbox import a_camelot

    validar_xml(datos)
    try:
        root = ET.fromstring(datos)
    except ET.ParseError as e:
        raise Rechazo(400, f"El XML está roto o incompleto ({e}). Exportalo de nuevo desde "
                           f"Rekordbox.") from e
    col = root.find("COLLECTION")
    if root.tag != "DJ_PLAYLISTS" or col is None:
        raise Rechazo(400, "No es un XML de Rekordbox (no tiene <DJ_PLAYLISTS> con <COLLECTION>).")

    tracks: dict[str, dict] = {}
    por_location: dict[str, str] = {}
    for tr in col.findall("TRACK"):
        tid = (tr.get("TrackID") or "").strip()
        if not tid:
            continue
        ubic = ruta_de_location(tr.get("Location") or "")
        bpm = _float(tr.get("AverageBpm"))
        tonalidad = (tr.get("Tonality") or "").strip()
        dur = _float(tr.get("TotalTime"))
        # POSITION_MARK y TEMPO se ignoran en esta etapa (los trae la exportación con cues).
        tracks[tid] = {
            "rb_track_id": tid,
            "titulo": (tr.get("Name") or "").strip() or (Path(nombre_de_ruta(ubic)).stem if ubic else ""),
            "artista": (tr.get("Artist") or "").strip(),
            "genero": (tr.get("Genre") or "").strip(),
            # 0.00 = Rekordbox no lo analizó: no es un BPM.
            "bpm": round(bpm, 2) if bpm and bpm > 0 else None,
            # Solo la notación clásica se mapea; un Camelot de otro tag no (ver a_camelot).
            "camelot": a_camelot(tonalidad),
            "tonalidad": tonalidad or None,
            "duracion": int(dur) if dur and dur > 0 else None,
            "location": ubic,
        }
        if ubic:
            por_location.setdefault(ubic.casefold(), tid)

    playlists: list[dict] = []
    raiz = root.find("PLAYLISTS")
    nodos = raiz.findall("NODE") if raiz is not None else []

    def recorrer(nodo, carpetas: list[str]) -> None:
        nombre = (nodo.get("Name") or "").strip()
        tipo = nodo.get("Type")
        if tipo == "0":
            # El nodo ROOT no es una carpeta del dueño: no entra en la ruta.
            sub = carpetas if (nombre == "ROOT" and not carpetas) else [*carpetas, nombre or "Carpeta"]
            for hijo in nodo.findall("NODE"):
                recorrer(hijo, sub)
            return
        if tipo != "1":
            return
        por_ubicacion = nodo.get("KeyType") == "1"
        entradas, inexistentes = [], 0
        for t in nodo.findall("TRACK"):
            clave = t.get("Key") or ""
            if por_ubicacion:
                ubic = ruta_de_location(clave)
                tid = por_location.get(ubic.casefold())
                if tid is None and ubic:
                    # No está en la colección: se arma con lo único que hay, la ubicación.
                    tid = f"loc:{ubic}"
                    tracks.setdefault(tid, {
                        "rb_track_id": None, "titulo": Path(nombre_de_ruta(ubic)).stem,
                        "artista": "", "genero": "", "bpm": None, "camelot": None,
                        "tonalidad": None, "duracion": None, "location": ubic})
            else:
                tid = clave.strip() if clave.strip() in tracks else None
            if tid is None:
                inexistentes += 1
                continue
            entradas.append(tid)
        playlists.append({
            "id": len(playlists), "nombre": nombre or "Playlist",
            "carpeta": " / ".join(carpetas) or None,
            "ruta": " / ".join([*carpetas, nombre or "Playlist"]),
            "entradas": entradas, "inexistentes": inexistentes,
        })

    for n in nodos:
        recorrer(n, [])
    return Coleccion(nombre_xml=Path(nombre_xml or "rekordbox.xml").name[:200], tracks=tracks,
                     playlists=playlists)


def resolver_coleccion(col: Coleccion, raices: list[str]) -> None:
    """Ubica el archivo de cada tema que aparece en alguna playlist (una sola vez por tema).

    `confinar`: solo vale un archivo adentro de las raíces (el XML llega del navegador y su
    Location puede apuntar a cualquier lado). `por_cola`: la ruta del XML puede ser de otra PC."""
    from ground_truth.resolver import construir_indice, resolver

    usados = {tid for p in col.playlists for tid in p["entradas"]}
    indice = construir_indice(raices) if raices and usados else {}
    for tid in usados:
        ubic = col.tracks[tid]["location"]
        if ubic in col.resoluciones:
            continue
        if not raices:
            col.resoluciones[ubic] = ("no-encontrado", None, 0)
            continue
        r = resolver(ubic, raices, indice, None, por_cola=True, confinar=True)
        col.resoluciones[ubic] = (r.estado, r.ruta if r.estado == "ok" else None,
                                  len(r.candidatos))


def resumen_playlist(col: Coleccion, p: dict) -> dict:
    """Lo que la pantalla muestra de una playlist del XML, sin rutas."""
    estados = [col.resoluciones.get(col.tracks[t]["location"], ("no-encontrado", None, 0))[0]
               for t in p["entradas"]]
    return {"id": p["id"], "nombre": p["nombre"], "carpeta": p["carpeta"], "ruta": p["ruta"],
            "total": len(p["entradas"]), "encontrados": estados.count("ok"),
            "ambiguos": estados.count("ambiguo"), "faltan": estados.count("no-encontrado"),
            "inexistentes": p["inexistentes"]}


def temas_de_playlist(col: Coleccion, p: dict) -> list[dict]:
    """Los temas de la playlist como los guarda `db.crear_importada`, en el orden del XML."""
    temas = []
    for tid in p["entradas"]:
        t = col.tracks[tid]
        estado, ruta, homonimos = col.resoluciones.get(t["location"], ("no-encontrado", None, 0))
        temas.append({
            "rb_track_id": t["rb_track_id"], "titulo": t["titulo"], "artista": t["artista"],
            "genero": t["genero"], "bpm": t["bpm"], "camelot": t["camelot"],
            "duracion": t["duracion"], "ruta": ruta, "resolucion": estado,
            "homonimos": homonimos if estado == "ambiguo" else None,
            "archivo": nombre_de_ruta(t["location"]) if t["location"] else None,
        })
    return temas


class Sesiones:
    """Colecciones leídas, en memoria, por token. TTL corto y tope de entradas: nada en disco."""

    def __init__(self, ttl_s: float = SESION_TTL_S, maximo: int = SESION_MAX) -> None:
        self._ttl, self._max = ttl_s, maximo
        self._lock = threading.Lock()
        self._datos: dict[str, tuple[float, Coleccion]] = {}

    def _purgar(self, ahora: float) -> None:
        for k in [k for k, (t, _) in self._datos.items() if ahora - t > self._ttl]:
            del self._datos[k]

    def guardar(self, col: Coleccion) -> str:
        token = secrets.token_urlsafe(18)
        ahora = time.monotonic()
        with self._lock:
            self._purgar(ahora)
            while len(self._datos) >= self._max:
                viejo = min(self._datos, key=lambda k: self._datos[k][0])
                del self._datos[viejo]
            self._datos[token] = (ahora, col)
        return token

    def leer(self, token) -> Coleccion | None:
        if not isinstance(token, str) or not token:
            return None
        with self._lock:
            self._purgar(time.monotonic())
            par = self._datos.get(token)
            return par[1] if par else None


# =============================================================================================
#  Carpetas de la PC
# =============================================================================================

_RESERVADOS = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?$", re.I)


def raices_publicas(raices: list[str]) -> list[dict]:
    """Las raíces por su NOMBRE (nunca la ruta). Dos con el mismo nombre se distinguen con (2)."""
    out, vistos = [], {}
    for i, r in enumerate(raices):
        nombre = Path(r).name or r.rstrip("\\/") or f"Raíz {i + 1}"
        vistos[nombre] = vistos.get(nombre, 0) + 1
        out.append({"id": i, "nombre": nombre if vistos[nombre] == 1 else f"{nombre} ({vistos[nombre]})",
                    "disponible": os.path.isdir(r)})
    return out


def validar_relativa(ruta) -> list[str]:
    """Las partes de una ruta RELATIVA a una raíz, o `Rechazo` 400. No toca el disco.

    Se rechaza: absoluta, con unidad o ':' (también flujos alternativos de NTFS), UNC
    ('\\\\servidor'), '\\\\?\\', '..', nombres reservados de Windows (CON, NUL, COM1…),
    caracteres de control y lo que no es texto."""
    if ruta is None or ruta == "":
        return []
    if not isinstance(ruta, str) or len(ruta) > 1000:
        raise Rechazo(400, "La carpeta tiene que ser un texto corto.")
    if re.search(r"[\x00-\x1f]", ruta) or ":" in ruta or ruta.startswith(("/", "\\")):
        raise Rechazo(400, "La carpeta tiene que ser relativa a una de tus carpetas de música "
                           "(sin unidad, sin ruta absoluta).")
    partes = [p for p in re.split(r"[\\/]+", ruta) if p not in ("", ".")]
    for p in partes:
        if p == ".." or p.strip(" .") == "" and p:
            raise Rechazo(400, "La carpeta no puede subir de nivel (..).")
        if _RESERVADOS.match(p.rstrip(" .")):
            raise Rechazo(400, f"«{p[:40]}» es un nombre reservado de Windows.")
        if any(c in p for c in '<>"|?*'):
            raise Rechazo(400, f"«{p[:40]}» tiene caracteres que no van en un nombre de carpeta.")
    return partes


def carpeta_segura(raices: list[str], raiz_id, ruta) -> tuple[Path, Path, list[str]]:
    """(carpeta real, raíz real, partes) de una carpeta pedida por el cliente, o `Rechazo`.

    Se resuelve TODO (symlinks y junctions) y se exige que quede adentro de la raíz real: un
    link adentro de la raíz que apunta afuera no se sigue."""
    if not raices:
        raise Rechazo(409, "No hay carpetas de música configuradas. Poné en MUSIFLIX_LIBRARY_ROOTS "
                           "las carpetas donde tenés tus temas (separadas por ';' en Windows) y "
                           "reiniciá el servidor.")
    try:
        i = int(raiz_id)
    except (TypeError, ValueError):
        raise Rechazo(400, "Elegí una de tus carpetas de música.") from None
    if isinstance(raiz_id, bool) or not 0 <= i < len(raices):
        raise Rechazo(404, "Esa carpeta de música no existe.")
    partes = validar_relativa(ruta)
    try:
        base = Path(raices[i]).resolve(strict=True)
    except (OSError, RuntimeError):
        raise Rechazo(404, "Esa carpeta de música no está disponible (¿un disco desconectado?).") from None
    try:
        real = base.joinpath(*partes).resolve(strict=True)
    except (OSError, RuntimeError):
        raise Rechazo(404, "No encuentro esa carpeta.") from None
    if not real.is_relative_to(base):
        raise Rechazo(400, "Esa carpeta está fuera de tus carpetas de música.")
    if not real.is_dir():
        raise Rechazo(404, "No encuentro esa carpeta.")
    return real, base, partes


def _orden_natural(nombre: str) -> list:
    """'Tema 2' antes que 'Tema 10' (como el Explorador), sin distinguir mayúsculas."""
    return [(0, int(x), "") if x.isdigit() else (1, 0, x.casefold())
            for x in re.split(r"(\d+)", nombre) if x]


def _es_audio(nombre: str) -> bool:
    from calidad.tags import EXTS
    return Path(nombre).suffix.lower() in EXTS


def _adentro(p: Path, base: Path) -> bool:
    try:
        return p.resolve(strict=True).is_relative_to(base)
    except (OSError, RuntimeError):
        return False


def listar_carpeta(real: Path, base: Path) -> dict:
    """Subcarpetas inmediatas con cuántos audios tiene cada una (directos) y cuántas
    subcarpetas; y los audios de la carpeta misma. Lo que escapa de la raíz no aparece."""
    subcarpetas, audios = [], 0
    try:
        entradas = sorted(os.scandir(real), key=lambda e: _orden_natural(e.name))
    except OSError:
        raise Rechazo(409, "No pude leer esa carpeta (¿permisos?).") from None
    for e in entradas:
        try:
            if e.is_file() and _es_audio(e.name) and _adentro(Path(e.path), base):
                audios += 1
            elif e.is_dir() and _adentro(Path(e.path), base):
                n_aud = n_sub = 0
                try:
                    for h in os.scandir(e.path):
                        if h.is_file() and _es_audio(h.name):
                            n_aud += 1
                        elif h.is_dir():
                            n_sub += 1
                except OSError:
                    pass
                subcarpetas.append({"nombre": e.name, "audios": n_aud, "subcarpetas": n_sub})
        except OSError:
            continue
    return {"audios": audios, "subcarpetas": subcarpetas}


def audios_de_carpeta(real: Path, base: Path, recursivo: bool = False,
                      tope: int = CARPETA_MAX_ARCHIVOS) -> tuple[list[Path], bool]:
    """Los audios de la carpeta (y subcarpetas si `recursivo`) en orden natural por ruta
    relativa, hasta `tope`. Devuelve (archivos, se_pasó_del_tope)."""
    encontrados: list[tuple[list, Path]] = []
    pila = [real]
    while pila:
        d = pila.pop()
        try:
            entradas = list(os.scandir(d))
        except OSError:
            continue
        for e in entradas:
            try:
                p = Path(e.path)
                if e.is_file() and _es_audio(e.name) and _adentro(p, base):
                    rel = p.relative_to(real).parts
                    encontrados.append(([_orden_natural(x) for x in rel], p))
                elif recursivo and e.is_dir() and _adentro(p, base):
                    pila.append(p)
            except (OSError, ValueError):
                continue
    encontrados.sort(key=lambda x: x[0])
    archivos = [p for _, p in encontrados]
    return archivos[:tope], len(archivos) > tope


def tema_de_archivo(p: Path) -> dict:
    """Lo que se guarda de un audio de carpeta: título, artista y género de los TAGS (si no
    trae, el título es el nombre del archivo y el género queda vacío: no se inventa), la
    duración real y, si los tags no se pudieron leer, el motivo. BPM y key no: los mide el
    motor."""
    from calidad.tags import leer_tags

    tema = {"rb_track_id": None, "titulo": p.stem, "artista": "", "genero": "", "bpm": None,
            "camelot": None, "duracion": None, "ruta": str(p), "resolucion": "ok",
            "homonimos": None, "archivo": p.name, "import_motivo": None}
    try:
        tags = leer_tags(str(p))
    except Exception as e:  # noqa: BLE001 — un archivo raro no frena la carpeta
        tags = {"formato": "?", "_error": type(e).__name__}
    if tags.get("formato") == "?":
        tema["import_motivo"] = "No pude leer los tags del archivo (¿está dañado?)."
    tema["titulo"] = (tags.get("titulo") or "").strip() or p.stem
    tema["artista"] = (tags.get("artista") or "").strip()
    tema["genero"] = (tags.get("genero") or "").strip()
    try:
        from mutagen import File as MFile
        m = MFile(str(p))
        largo = getattr(getattr(m, "info", None), "length", None)
        if largo and largo > 0:
            tema["duracion"] = int(round(largo))
    except Exception:  # noqa: BLE001
        pass
    return tema
