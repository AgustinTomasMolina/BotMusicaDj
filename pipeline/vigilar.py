"""Carpeta vigilada (f65): lo nuevo que aparece en una carpeta entra solo al pipeline.

    python -m pipeline.vigilar                 # un ciclo ahora, sobre MUSIC_WATCH_FOLDER
    python -m pipeline.vigilar --carpeta <dir> # un ciclo sobre otra carpeta

En el server corre sola: un hilo mira la carpeta cada MUSIC_WATCH_INTERVAL_S segundos
(default 60) y `POST /api/vigilancia/escanear` adelanta el próximo ciclo.

NO es un pipeline aparte. Es una ENTRADA delgada sobre `pipeline.revisar.procesar`, el mismo
paso 1 que corre `python -m pipeline.revisar --carpeta`: calidad (corte espectral),
duplicados, BPM/tonalidad del motor, tags normalizados en la copia y el reporte HTML. Lo que
cambia es solo QUÉ archivos le llegan: los nuevos y terminados de la carpeta vigilada. Cada
tanda deja sus copias en una subcarpeta propia del staging (`<staging>/tanda_<fecha>_<n>/`) y
su reporte al lado del staging (`revision_vigilada_<fecha>.html`); de ahí en más es el flujo
de siempre: revisar a mano, bajar decisiones.json y `python -m pipeline.aplicar` (iTunes + XML
de Rekordbox). Nada llega a iTunes ni a Rekordbox sin pasar por los ojos del dueño.

Decisiones:
- Los originales de la carpeta vigilada SOLO se leen (stat, listado y la lectura de
  `procesar`, que copia con copy2 y taggea la copia). Nunca se mueven, renombran ni borran.
- "Nuevo" = (ruta, tamaño, mtime) que no está en el registro. El pipeline no tiene un registro
  de lo ya procesado (`revisar` procesa la carpeta entera cada vez), así que el vigilante lleva
  uno chico en JSON (`carpeta_vigilada.json`, en MUSIFLIX_DATA_DIR). Un archivo que cambia se
  vuelve a procesar.
- Fallos: un error del PIPELINE (el audio no se puede analizar) queda fallido hasta que el
  archivo cambie. Un tope de tiempo o un error de E/S (red que se corta, archivo tomado) es
  pasajero: se reintenta hasta 3 veces en total, con espera creciente entre ciclos.
- Un archivo a medio escribir no entra: se ignoran `.part`, `.tmp`, `.crdownload` (también
  como `tema.temp.mp3` / `tema.part.mp3`), ocultos y de sistema, y los que cambian de tamaño o
  mtime entre dos miradas separadas unos segundos.
- El recorrido NO entra en junctions ni enlaces simbólicos de carpeta (en Python 3.12
  `os.walk` sí entra en las junctions de Windows: una junction a un ancestro repetía la
  carpeta entera, staging incluido). Sí entra en las carpetas de OneDrive, que también son
  puntos de reparse pero de otro tipo. El staging y la carpeta de iTunes se excluyen por
  IDENTIDAD (dispositivo + inodo), no por texto: un nombre corto 8.3, una unidad mapeada o un
  UNC que apuntan a la misma carpeta se excluyen igual.
- Un ciclo por vez en TODA la PC: un cerrojo de archivo (`carpeta_vigilada.json.lock`) cubre
  el ciclo entero, así la CLI y el server no procesan a la vez ni se pisan el registro. Si
  está tomado, el ciclo se saltea (el server lo anota; la CLI sale con error y lo dice). El
  registro se relee bajo el cerrojo antes de cada ciclo.
- La carpeta puede ser de red (\\\\NAS\\musica): si no está, se anota, se reintenta en el
  próximo ciclo y el server sigue. Nunca levanta hacia el hilo.
- De a UN proceso de análisis por vez, en un proceso APARTE con tope de tiempo: un hilo de
  Python no se puede matar, un proceso sí. La tanda va junta (los duplicados se buscan entre
  los archivos de la tanda, con la lógica de siempre); si la tanda se pasa del tope o se cae,
  sus archivos se reintentan de a uno (y eso se guarda en el registro: reiniciar no los vuelve
  a juntar). Tanda acotada (`max_por_ciclo`): lo que sobra espera al ciclo siguiente.
- Dos archivos con el MISMO nombre (sub1/tema.mp3 y sub2/tema.mp3) nunca van en la misma
  tanda: `revisar` nombra la copia con el nombre del archivo y uno pisaría al otro. Se reparten
  en tandas distintas (cada una con su subcarpeta), no se marcan fallidos: ninguno de los dos
  tiene nada malo y el dueño no tendría cómo destrabarlo. El costo: esos dos no se comparan
  como posibles duplicados entre sí (igual que dos archivos de tandas distintas).
- Licencia y origen: el pipeline no los pide; el registro guarda `no declarado` (CLAUDE.md,
  2026-10-09). BPM y key salen del motor tal cual; el reporte ya marca con `?` la key dudosa.
- Sin dependencias nuevas: polling. `watchdog` evitaría el sondeo, pero sobre SMB los eventos
  no son confiables y el sondeo hace falta igual.
- Las respuestas del estado no llevan rutas absolutas de la PC: nombres de archivo.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import logging
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from calidad.tags import EXTS
from pipeline import config

logger = logging.getLogger("bot_web")

BASE_DIR = config.BASE_DIR
ENV_FILE = BASE_DIR / ".env"
NOMBRE_REGISTRO = "carpeta_vigilada.json"

# Lo que deja un navegador o un gestor de descargas mientras baja: nunca es un archivo listo.
# Los sufijos solos ya no son audio (no pasarían por EXTS); los INFIJOS sí importan: yt-dlp
# convierte a "tema.temp.mp3" y algunos gestores escriben "tema.part.mp3", con extensión de
# audio y a medio escribir.
SUFIJOS_EN_CURSO = (".part", ".tmp", ".crdownload")
INFIJOS_EN_CURSO = (".part.", ".tmp.", ".temp.", ".crdownload.")
ESPERA_ESTABLE_S = 5.0
ARRANQUE_S = 180.0          # importar librosa y compilar numba en el proceso nuevo (en frío)
MAX_POR_CICLO = 8           # PC con poca memoria: una tanda chica por ciclo

PROCESADO = "procesado"
FALLIDO = "fallido"

# Tipos de fallo: los dos primeros son pasajeros y se reintentan.
TIMEOUT, IO, PIPELINE = "timeout", "io", "pipeline"
REINTENTABLES = (TIMEOUT, IO)
MAX_INTENTOS = 3            # intentos en total, contando el primero
REINTENTO_BASE_S = 120.0    # espera antes del 2do intento; se duplica en cada uno

OK, FALLO, USO = 0, 1, 2

_RE_FALLO_IO = re.compile(r"^(FileNotFoundError|PermissionError|OSError|IsADirectoryError|"
                          r"NotADirectoryError|TimeoutError|ConnectionError|BlockingIOError|"
                          r"InterruptedError|BrokenPipeError)\b|\[(Errno|WinError) \d+\]")


class CarpetaInalcanzable(OSError):
    """La carpeta vigilada no está (disco desenchufado, NAS apagado, red caída)."""


def clave(ruta: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(str(ruta)))


def _etiqueta(carpeta: Path) -> str:
    """Cómo se nombra la carpeta en logs y respuestas: el último tramo, sin la ruta entera."""
    partes = [p for p in str(carpeta).replace("\\", "/").split("/") if p]
    return partes[-1] if partes else str(carpeta)


def _sin_rutas(texto: str, rutas: Iterable[str]) -> str:
    """Un motivo de fallo sin rutas de la PC: cada ruta se reemplaza por su nombre.

    Incluye la forma `repr` (barras duplicadas: `'C:\\\\Users\\\\...'`), que es como sale la
    ruta en `[Errno 2] No such file or directory: '...'`."""
    for r in sorted({str(r) for r in rutas}, key=len, reverse=True):
        nombre = Path(r).name or r
        formas = set()
        for base in (r, os.path.abspath(r)):
            formas |= {base, base.replace("\\", "/"), base.replace("\\", "\\\\")}
        for forma in sorted(formas, key=len, reverse=True):
            texto = texto.replace(forma, nombre)
    return texto[:300]


def _tipo_de_fallo(motivo: str) -> str:
    """io si el mensaje es de E/S (red, archivo tomado); pipeline si no."""
    return IO if _RE_FALLO_IO.search(motivo or "") else PIPELINE


# --- descubrir ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Foto:
    """Un archivo tal como se lo vio: con esto se decide si es nuevo y si sigue creciendo."""
    ruta: str
    tamano: int
    mtime_ns: int

    @property
    def clave(self) -> str:
        return clave(self.ruta)


_ATRIBUTOS_OCULTOS = (getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
                      | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4))
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
# Solo estas dos son "otra carpeta": junction (mount point) y enlace simbólico. OneDrive y
# otros proveedores de nube marcan sus carpetas como punto de reparse con OTRAS etiquetas, y
# esas sí se recorren (la música del dueño puede vivir en OneDrive).
_ETIQUETAS_ENLACE = {getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
                     getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C)}


def _en_curso(nombre: str) -> bool:
    n = nombre.lower()
    return n.endswith(SUFIJOS_EN_CURSO) or any(i in n for i in INFIJOS_EN_CURSO)


def _nombre_ignorado(nombre: str) -> bool:
    return nombre.lower().startswith((".", "~$")) or _en_curso(nombre)


def _oculto(st: os.stat_result) -> bool:
    """Oculto o de sistema para Windows. En Linux/mac alcanza con el punto del nombre."""
    return bool(getattr(st, "st_file_attributes", 0) & _ATRIBUTOS_OCULTOS)


def _es_enlace(lst: os.stat_result) -> bool:
    """Junction o enlace simbólico (sobre el resultado de `os.lstat`)."""
    if stat.S_ISLNK(lst.st_mode):
        return True
    if getattr(lst, "st_file_attributes", 0) & _REPARSE_POINT:
        return getattr(lst, "st_reparse_tag", 0) in _ETIQUETAS_ENLACE
    return False


def _identidad(st: os.stat_result) -> tuple[int, int] | None:
    return (st.st_dev, st.st_ino) if st.st_ino else None


class _Exclusiones:
    """Carpetas que no se recorren, comparadas por identidad y, de respaldo, por texto."""

    def __init__(self, dirs: Iterable[Path]) -> None:
        dirs = list(dirs)
        self._claves = {clave(d) for d in dirs}
        self._ids: set[tuple[int, int]] = set()
        for d in dirs:
            try:
                i = _identidad(os.stat(d))
            except OSError:
                continue
            if i is not None:
                self._ids.add(i)

    def contiene(self, ruta: str, st: os.stat_result) -> bool:
        i = _identidad(st)
        return clave(ruta) in self._claves or (i is not None and i in self._ids)


def descubrir(carpeta: Path, excluir: Iterable[Path] = (),
              ignorados: list[str] | None = None) -> list[Foto]:
    """Los audios de la carpeta (recursivo), sin los en curso, ocultos ni de sistema.

    `excluir`: carpetas que NO se recorren aunque estén adentro (el staging, la de iTunes):
    si el staging quedara dentro de la carpeta vigilada, las copias procesadas volverían a
    entrar como nuevas en cada ciclo. Se comparan por identidad (ver el docstring del módulo).
    No entra en junctions ni enlaces simbólicos de carpeta: uno que apunte a un ancestro
    repetiría todo. `ignorados` (si se pasa) recibe el nombre de cada archivo salteado por
    estar a medio bajar. Levanta `CarpetaInalcanzable` si la raíz no se puede leer.
    """
    raiz = str(carpeta)
    if not os.path.isdir(raiz):
        raise CarpetaInalcanzable(
            f"no puedo leer la carpeta vigilada «{_etiqueta(carpeta)}» (¿está montada?)")
    excluidas = _Exclusiones(excluir)
    errores: list[OSError] = []
    fotos: list[Foto] = []
    for actual, dirs, archivos in os.walk(raiz, onerror=errores.append):
        quedan = []
        for d in sorted(dirs):
            p = os.path.join(actual, d)
            if _nombre_ignorado(d):
                continue
            try:
                if _es_enlace(os.lstat(p)):
                    continue
                st = os.stat(p)
            except OSError:
                continue
            if _oculto(st) or excluidas.contiene(p, st):
                continue
            quedan.append(d)
        dirs[:] = quedan
        for nombre in sorted(archivos):
            if _nombre_ignorado(nombre):
                if ignorados is not None and _en_curso(nombre):
                    ignorados.append(nombre)
                continue
            if not nombre.lower().endswith(EXTS):
                continue
            p = os.path.join(actual, nombre)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or _oculto(st):
                continue
            fotos.append(Foto(p, st.st_size, st.st_mtime_ns))
    # El listado de la RAÍZ falló (la red se cayó entre el isdir y el listado): no es "carpeta
    # vacía", es carpeta inalcanzable. Un error en una subcarpeta solo se saltea esa.
    if any(clave(getattr(e, "filename", "") or "") == clave(raiz) for e in errores):
        raise CarpetaInalcanzable(
            f"no puedo listar la carpeta vigilada «{_etiqueta(carpeta)}» (¿se cortó la red?)")
    return fotos


def estables(fotos: list[Foto], espera_s: float = ESPERA_ESTABLE_S,
             dormir: Callable[[float], None] = time.sleep) -> tuple[list[Foto], list[Foto]]:
    """(terminados, todavía cambiando). Se vuelve a mirar cada archivo después de `espera_s`:
    si cambió el tamaño o el mtime, alguien lo sigue escribiendo y queda para otro ciclo.
    Un archivo vacío tampoco está listo (el que lo crea todavía no escribió nada)."""
    if not fotos:
        return [], []
    dormir(espera_s)
    listos, cambiando = [], []
    for f in fotos:
        try:
            st = os.stat(f.ruta)
        except OSError:
            cambiando.append(f)
            continue
        if f.tamano > 0 and st.st_size == f.tamano and st.st_mtime_ns == f.mtime_ns:
            listos.append(f)
        else:
            cambiando.append(f)
    return listos, cambiando


def partir_por_nombre(fotos: list[Foto]) -> list[list[Foto]]:
    """Reparte en tandas sin dos archivos del mismo nombre (sin distinguir mayúsculas en
    Windows): la copia en el staging se llama como el archivo y uno pisaría al otro."""
    tandas: list[list[Foto]] = []
    nombres: list[set[str]] = []
    for f in fotos:
        n = os.path.normcase(Path(f.ruta).name)
        for t, ns in zip(tandas, nombres, strict=True):
            if n not in ns:
                t.append(f)
                ns.add(n)
                break
        else:
            tandas.append([f])
            nombres.append({n})
    return tandas


# --- registro de lo ya procesado ---------------------------------------------------------


def ruta_registro() -> Path:
    """Al lado de la base de MusiFlix (MUSIFLIX_DATA_DIR, mismo default que `db.py`)."""
    return Path(os.getenv("MUSIFLIX_DATA_DIR", str(BASE_DIR))) / NOMBRE_REGISTRO


class Registro:
    """Lo que el vigilante ya procesó (o falló), por (ruta, tamaño, mtime). En JSON.

    También guarda qué archivos se reintentan de a uno (`solos`), así un reinicio no los
    vuelve a juntar con la tanda que se cayó."""

    def __init__(self, ruta: Path) -> None:
        self.ruta = Path(ruta)
        self._lock = threading.Lock()
        try:
            self._archivos, self._solos = self._cargar()
        except OSError as e:
            # Tomado por otro proceso en este instante: se arranca vacío en memoria, pero el
            # ciclo relee bajo el cerrojo (`recargar`) antes de escribir nada.
            logger.warning(f"⚠️ Carpeta vigilada: no pude leer el registro ahora ({e}).")
            self._archivos, self._solos = {}, set()

    def _leer(self) -> str:
        """El texto del registro. Un PermissionError pasajero (otro proceso lo está
        reemplazando) se reintenta; uno que dura LEVANTA: no es un registro ilegible."""
        for intento in range(40):
            try:
                return self.ruta.read_text(encoding="utf-8")
            except PermissionError:
                if intento == 39:
                    raise
                time.sleep(0.025)
        raise AssertionError("inalcanzable")

    def _cargar(self) -> tuple[dict[str, dict], set[str]]:
        try:
            texto = self._leer()
        except FileNotFoundError:
            return {}, set()
        try:
            doc = json.loads(texto)
            archivos = doc.get("archivos") if isinstance(doc, dict) else None
            if not isinstance(archivos, dict):
                raise ValueError("sin 'archivos'")
            solos = doc.get("solos") or []
            return ({k: v for k, v in archivos.items() if isinstance(v, dict)},
                    {s for s in solos if isinstance(s, str)})
        except ValueError as e:
            # Contenido roto (no un archivo tomado). No se pisa: se aparta y se arranca de cero (reprocesar es caro pero no rompe
            # nada; pisarlo perdería la evidencia de qué pasó).
            apartado = self.ruta.with_name(
                f"{self.ruta.name}.ilegible-{datetime.datetime.now():%Y%m%d%H%M%S}")
            with contextlib.suppress(OSError):
                os.replace(self.ruta, apartado)
            logger.warning(f"⚠️ Carpeta vigilada: el registro estaba ilegible ({e}); "
                           f"se apartó como {apartado.name} y se arranca de cero.")
            return {}, set()

    def recargar(self) -> None:
        """Lo que está en disco reemplaza lo de memoria (otro proceso pudo haber escrito).
        Se llama con el cerrojo del ciclo tomado."""
        archivos, solos = self._cargar()
        with self._lock:
            self._archivos, self._solos = archivos, solos

    def debe_procesar(self, f: Foto, ahora: float | None = None) -> bool:
        """Nuevo, cambiado, o fallido pasajero cuyo próximo intento ya llegó."""
        with self._lock:
            e = self._archivos.get(f.clave)
        if e is None or e.get("tamano") != f.tamano or e.get("mtime_ns") != f.mtime_ns:
            return True
        proximo = e.get("proximo_intento")
        if e.get("estado") == FALLIDO and isinstance(proximo, (int, float)):
            return (time.time() if ahora is None else ahora) >= proximo
        return False

    # Compatibilidad con el nombre de la primera versión.
    es_nuevo = debe_procesar

    def marcar(self, f: Foto, estado: str, **extra) -> None:
        from motor.modelos import NO_DECLARADO

        with self._lock:
            self._archivos[f.clave] = {
                "archivo": Path(f.ruta).name, "tamano": f.tamano, "mtime_ns": f.mtime_ns,
                "estado": estado, "cuando": datetime.datetime.now().isoformat(timespec="seconds"),
                "licencia": NO_DECLARADO, "origen": NO_DECLARADO, **extra}
            self._solos.discard(f.clave)

    def marcar_fallido(self, f: Foto, motivo: str, tipo: str, ahora: float) -> None:
        """Fallido con su tipo y la cuenta de intentos sobre ESTE contenido del archivo."""
        with self._lock:
            prev = self._archivos.get(f.clave) or {}
        mismo = (prev.get("estado") == FALLIDO and prev.get("tamano") == f.tamano
                 and prev.get("mtime_ns") == f.mtime_ns)
        intentos = int(prev.get("intentos") or 1) + 1 if mismo else 1
        proximo = None
        if tipo in REINTENTABLES and intentos < MAX_INTENTOS:
            proximo = ahora + REINTENTO_BASE_S * 2 ** (intentos - 1)
        self.marcar(f, FALLIDO, motivo=motivo, motivo_tipo=tipo, intentos=intentos,
                    proximo_intento=proximo)

    def es_solo(self, k: str) -> bool:
        with self._lock:
            return k in self._solos

    def agregar_solos(self, claves: Iterable[str]) -> None:
        with self._lock:
            self._solos.update(claves)

    def guardar(self) -> None:
        with self._lock:
            texto = json.dumps({"version": 2, "archivos": self._archivos,
                                "solos": sorted(self._solos)}, ensure_ascii=False, indent=1)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        # Temporal propio de este proceso e hilo: dos escritores con el mismo `.tmp` chocan
        # en Windows (PermissionError) aunque el cerrojo del ciclo lo evite en la práctica.
        tmp = self.ruta.with_name(f"{self.ruta.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(texto, encoding="utf-8")
        # Atómico: un corte a mitad no deja medio JSON. En Windows el reemplazo falla con
        # PermissionError si en ese instante otro proceso tiene abierto el destino (leyéndolo
        # o reemplazándolo): se reintenta un rato corto antes de rendirse.
        for intento in range(40):
            try:
                os.replace(tmp, self.ruta)
                return
            except PermissionError:
                if intento == 39:
                    with contextlib.suppress(OSError):
                        tmp.unlink()
                    raise
                time.sleep(0.025)

    def contar(self) -> dict[str, int]:
        with self._lock:
            n = {PROCESADO: 0, FALLIDO: 0}
            for e in self._archivos.values():
                n[e.get("estado", "")] = n.get(e.get("estado", ""), 0) + 1
            return n

    def ultimos_fallidos(self, cuantos: int = 5) -> list[str]:
        """Los nombres (sin rutas) de los últimos fallidos, del más nuevo al más viejo."""
        with self._lock:
            fallidos = [e for e in self._archivos.values() if e.get("estado") == FALLIDO]
        fallidos.sort(key=lambda e: str(e.get("cuando", "")), reverse=True)
        return [str(e.get("archivo", "")) for e in fallidos[:cuantos]]

    def entrada(self, ruta: str) -> dict | None:
        with self._lock:
            e = self._archivos.get(clave(ruta))
            return dict(e) if e else None


class CerrojoArchivo:
    """Cerrojo entre procesos sobre un archivo `.lock` (msvcrt en Windows, flock en el resto).
    No bloquea: `tomar()` devuelve False si otro lo tiene."""

    def __init__(self, ruta: Path) -> None:
        self.ruta = Path(ruta)
        self._f = None

    def tomar(self) -> bool:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.ruta, "a+b")  # noqa: SIM115 — queda abierto mientras dura el cerrojo
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._f = f
        return True

    def soltar(self) -> None:
        f, self._f = self._f, None
        if f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                with contextlib.suppress(OSError):
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


# --- procesar una tanda en un proceso aparte ---------------------------------------------


@dataclass
class ResultadoLote:
    ok: bool
    filas: list = field(default_factory=list)
    reporte: str | None = None
    motivo: str = ""
    cancelado: bool = False
    tipo: str = PIPELINE        # si no anduvo: timeout | io | pipeline


def _opciones_proceso() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def _matar(proc: subprocess.Popen) -> None:
    """Mata el proceso Y sus hijos (el decodificador puede haber lanzado un ffmpeg)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                           check=False, **_opciones_proceso())
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    with contextlib.suppress(OSError):
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning(f"⚠️ Carpeta vigilada: el proceso {proc.pid} no terminó después de matarlo.")


def procesar_aparte(rutas: list[str], staging: Path, reporte: Path, timeout_s: float,
                    cancelar: threading.Event | None = None,
                    comando: list[str] | None = None) -> ResultadoLote:
    """Corre `pipeline.revisar.procesar` + el reporte sobre `rutas` en un proceso nuevo, con
    tope de `timeout_s` (pasado el tope se mata el proceso y su árbol). `cancelar` corta antes
    (apagado del server). `comando` reemplaza el intérprete y el módulo (para los tests)."""
    with tempfile.TemporaryDirectory(prefix="musiflix-vigilar-",
                                     ignore_cleanup_errors=True) as tmp:
        entrada, salida = Path(tmp) / "lote.json", Path(tmp) / "resultado.json"
        errlog = Path(tmp) / "stderr.txt"
        entrada.write_text(json.dumps({"rutas": rutas, "staging": str(staging),
                                       "reporte": str(reporte)}, ensure_ascii=False),
                           encoding="utf-8")
        cmd = (comando or [sys.executable, "-m", "pipeline.vigilar"]) + [
            "--lote", str(entrada), "--salida", str(salida)]
        limite = time.monotonic() + timeout_s
        with open(errlog, "wb") as err:
            proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=err, **_opciones_proceso())
            while True:
                try:
                    rc = proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if cancelar is not None and cancelar.is_set():
                    _matar(proc)
                    return ResultadoLote(False, motivo="cancelado (se apagó el server)",
                                         cancelado=True)
                if time.monotonic() > limite:
                    _matar(proc)
                    return ResultadoLote(False, motivo=f"superó el tope de {timeout_s:.0f} s",
                                         tipo=TIMEOUT)
        if rc != 0:
            cola = errlog.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            ultima = cola[-1] if cola else f"código {rc}"
            return ResultadoLote(False, motivo=_sin_rutas(f"el proceso falló: {ultima}", rutas),
                                 tipo=_tipo_de_fallo(ultima))
        try:
            doc = json.loads(salida.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return ResultadoLote(False, motivo=f"el proceso no dejó resultado legible ({e})")
        if not isinstance(doc, dict) or not isinstance(doc.get("filas") or [], list):
            return ResultadoLote(False, motivo="el proceso dejó un resultado con otra forma")
        reporte_hijo = doc.get("reporte")
        return ResultadoLote(True, filas=list(doc.get("filas") or []),
                             reporte=reporte_hijo if isinstance(reporte_hijo, str) else None)


def _correr_lote(entrada: Path, salida: Path) -> int:
    """El lado del proceso hijo: el paso 1 del pipeline sobre la tanda, tal cual. `staging`
    es la subcarpeta de ESTA tanda (la elige el vigilante)."""
    from pipeline.reporte import generar
    from pipeline.revisar import procesar

    doc = json.loads(entrada.read_text(encoding="utf-8"))
    staging = Path(doc["staging"])
    staging.mkdir(parents=True, exist_ok=True)
    filas = procesar(list(doc["rutas"]), staging, progreso=False)
    reporte = generar(filas, staging, Path(doc["reporte"]))
    salida.write_text(json.dumps({"filas": [asdict(f) for f in filas], "reporte": str(reporte)},
                                 ensure_ascii=False), encoding="utf-8")
    return OK


def _bpm_de(fila: dict) -> float | None:
    """El BPM de una fila del hijo, o None si no se midió. Levanta si no es un número."""
    bruto = fila.get("bpm")
    if bruto is None:
        return None
    if isinstance(bruto, bool) or not isinstance(bruto, (int, float, str)):
        raise ValueError(f"bpm con otra forma: {type(bruto).__name__}")
    bpm = float(bruto)
    if not math.isfinite(bpm):
        raise ValueError("bpm no finito")
    return round(bpm, 1) if bpm > 0 else None


# --- el vigilante ------------------------------------------------------------------------


Procesador = Callable[..., ResultadoLote]


class Vigilante:
    def __init__(self, carpeta: Path, *, staging: Path, registro: Registro,
                 intervalo_s: float = config.WATCH_INTERVALO_DEFAULT_S,
                 timeout_por_archivo_s: float = config.WATCH_TIMEOUT_DEFAULT_S,
                 arranque_s: float = ARRANQUE_S, espera_estable_s: float = ESPERA_ESTABLE_S,
                 max_por_ciclo: int = MAX_POR_CICLO, excluir: Iterable[Path] = (),
                 procesar: Procesador = procesar_aparte,
                 dormir: Callable[[float], None] = time.sleep,
                 reloj: Callable[[], float] = time.time) -> None:
        self.carpeta = Path(carpeta)
        self.staging = Path(staging)
        self.registro = registro
        self.intervalo_s = intervalo_s
        self.timeout_por_archivo_s = timeout_por_archivo_s
        self.arranque_s = arranque_s
        self.espera_estable_s = espera_estable_s
        self.max_por_ciclo = max(1, int(max_por_ciclo))
        self.excluir = [Path(e) for e in excluir] + [self.staging]
        self._procesar = procesar
        self._dormir = dormir
        self._reloj = reloj
        self._ciclo_lock = threading.Lock()       # un ciclo por vez en este proceso
        self._cerrojo = CerrojoArchivo(self.registro.ruta.with_name(
            self.registro.ruta.name + ".lock"))   # ... y en toda la PC
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._despertar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._alcanzable: bool | None = None
        self._ultimo_error: str | None = None
        self._ultimo_ciclo: str | None = None
        self._ultimo_reporte: str | None = None
        self._actual: list[str] = []
        self._pendientes = 0
        self._creciendo = 0
        self._ignorados = 0
        self._tanda = 0

    # -- un ciclo -----------------------------------------------------------------------

    def escanear_ahora(self) -> dict:
        """Un ciclo completo, ya. Si hay otro corriendo (acá o en otro proceso) no se lanza un
        segundo: vuelve con `ocupado` en True."""
        if not self._ciclo_lock.acquire(blocking=False):
            return {**self.estado(), "ocupado": True}
        try:
            if not self._cerrojo.tomar():
                logger.info("📂 Carpeta vigilada: otro proceso la está procesando; "
                            "salteo este ciclo.")
                return {**self.estado(), "ocupado": True}
            try:
                try:
                    self.registro.recargar()
                except OSError as e:
                    # Sin el registro al día no se procesa ni se escribe: se pisaría lo de otro.
                    logger.warning(f"⚠️ Carpeta vigilada: no pude leer el registro ({e}); "
                                   f"salteo este ciclo.")
                    with self._lock:
                        self._ultimo_error = "no pude leer el registro de lo ya procesado"
                    return {**self.estado(), "ocupado": False}
                self._ciclo()
            finally:
                self._cerrojo.soltar()
        finally:
            self._ciclo_lock.release()
        return {**self.estado(), "ocupado": False}

    def _ciclo(self) -> None:
        ahora = datetime.datetime.now().isoformat(timespec="seconds")
        ignorados: list[str] = []
        try:
            fotos = descubrir(self.carpeta, self.excluir, ignorados)
        except (CarpetaInalcanzable, OSError) as e:
            motivo = (str(e) if isinstance(e, CarpetaInalcanzable) else
                      _sin_rutas(f"no puedo leer la carpeta vigilada ({e})", [str(self.carpeta)]))
            if self._alcanzable is not False:     # una línea por caída, no una por ciclo
                logger.warning(f"⚠️ Carpeta vigilada: {motivo}. Reintento en el próximo ciclo.")
            with self._lock:
                self._alcanzable, self._ultimo_error, self._ultimo_ciclo = False, motivo, ahora
            return
        if self._alcanzable is False:
            logger.info(f"📂 Carpeta vigilada: «{_etiqueta(self.carpeta)}» volvió a estar.")
        with self._lock:
            self._alcanzable, self._ultimo_ciclo = True, ahora
            self._ignorados = len(ignorados)

        t = self._reloj()
        nuevos = [f for f in fotos if self.registro.debe_procesar(f, t)]
        listos, cambiando = estables(nuevos, self.espera_estable_s, self._dormir)
        solos = [f for f in listos if self.registro.es_solo(f.clave)][: self.max_por_ciclo]
        resto = [f for f in listos if not self.registro.es_solo(f.clave)]
        resto = resto[: self.max_por_ciclo - len(solos)]
        lotes = [[f] for f in solos] + partir_por_nombre(resto)
        with self._lock:
            self._creciendo = len(cambiando)
            self._pendientes = len(listos)
            self._ultimo_error = None

        for lote in lotes:
            if self._parar.is_set():
                return
            self._procesar_lote(lote)
            with self._lock:
                self._pendientes -= len(lote)

    def _sello(self) -> str:
        self._tanda += 1
        return f"{datetime.datetime.now():%Y-%m-%d_%H%M%S}_{self._tanda}"

    def _destinos(self) -> tuple[Path, Path]:
        """(subcarpeta del staging, reporte) de una tanda nueva. Nunca reutiliza una que ya
        existe: otra tanda (o un `revisar` anterior) no puede quedar pisada."""
        while True:
            sello = self._sello()
            sub = self.staging / f"tanda_{sello}"
            reporte = self.staging.parent / f"revision_vigilada_{sello}.html"
            if not sub.exists() and not reporte.exists():
                return sub, reporte

    def _procesar_lote(self, lote: list[Foto]) -> None:
        staging, reporte = self._destinos()
        timeout = self.arranque_s + self.timeout_por_archivo_s * len(lote)
        with self._lock:
            self._actual = [Path(f.ruta).name for f in lote]
        logger.info(f"📂 Carpeta vigilada: procesando {len(lote)} archivo(s) nuevo(s).")
        try:
            res = self._procesar([f.ruta for f in lote], staging, reporte, timeout, self._parar)
        except Exception as e:  # noqa: BLE001 — el hilo no se cae por un archivo
            res = ResultadoLote(False, motivo=f"{type(e).__name__}: {e}",
                                tipo=IO if isinstance(e, OSError) else PIPELINE)
        finally:
            with self._lock:
                self._actual = []
        if res.cancelado:
            return
        try:
            self._anotar(lote, res, reporte)
        finally:
            try:
                self.registro.guardar()
            except OSError as e:
                logger.warning(f"⚠️ Carpeta vigilada: no pude guardar el registro ({e}).")

    def _anotar(self, lote: list[Foto], res: ResultadoLote, reporte: Path) -> None:
        ahora = self._reloj()
        motivo = _sin_rutas(res.motivo, [f.ruta for f in lote])
        if res.ok:
            por_ruta = {clave(f["ruta_original"]): f for f in res.filas
                        if isinstance(f, dict) and isinstance(f.get("ruta_original"), str)}
            nombre_reporte = Path(res.reporte or reporte).name
            for foto in lote:
                fila = por_ruta.get(foto.clave)
                if fila is None:
                    self.registro.marcar_fallido(
                        foto, "el pipeline no devolvió una fila legible para este archivo",
                        PIPELINE, ahora)
                    continue
                try:
                    bpm = _bpm_de(fila)
                except (TypeError, ValueError) as e:
                    self.registro.marcar_fallido(
                        foto, f"el pipeline devolvió una fila ilegible ({e})", PIPELINE, ahora)
                    continue
                self.registro.marcar(
                    foto, PROCESADO, reporte=nombre_reporte, revision=str(fila.get("estado", "")),
                    bandera=str(fila.get("bandera", "")), bpm=bpm,
                    camelot=str(fila.get("camelot") or "?"))
            with self._lock:
                self._ultimo_reporte = nombre_reporte
        elif len(lote) > 1:
            # La tanda entera no anduvo: no se sabe cuál fue. Se reintentan de a uno.
            logger.warning(f"⚠️ Carpeta vigilada: la tanda de {len(lote)} falló ({motivo}); "
                           f"se reintenta de a un archivo.")
            self.registro.agregar_solos(f.clave for f in lote)
        else:
            foto = lote[0]
            logger.warning(f"⚠️ Carpeta vigilada: «{Path(foto.ruta).name}» falló: {motivo}")
            self.registro.marcar_fallido(foto, motivo, res.tipo, ahora)

    # -- estado -------------------------------------------------------------------------

    def estado(self) -> dict:
        n = self.registro.contar()
        ultimos = self.registro.ultimos_fallidos()
        with self._lock:
            return {
                "carpeta": _etiqueta(self.carpeta), "alcanzable": self._alcanzable,
                "corriendo": self._hilo is not None and self._hilo.is_alive(),
                "procesando": list(self._actual), "pendientes": self._pendientes,
                "creciendo": self._creciendo, "ignorados": self._ignorados,
                "procesados": n.get(PROCESADO, 0), "fallidos": n.get(FALLIDO, 0),
                "ultimos_fallidos": ultimos, "ultimo_ciclo": self._ultimo_ciclo,
                "ultimo_error": self._ultimo_error, "ultimo_reporte": self._ultimo_reporte,
                "intervalo_s": self.intervalo_s,
            }

    # -- hilo ---------------------------------------------------------------------------

    def iniciar(self) -> None:
        with self._lock:
            if self._hilo is not None and self._hilo.is_alive():
                return
            self._parar.clear()
            self._hilo = threading.Thread(target=self._bucle, name="carpeta-vigilada",
                                          daemon=True)
            self._hilo.start()
        logger.info(f"📂 Carpeta vigilada: «{_etiqueta(self.carpeta)}», cada "
                    f"{self.intervalo_s:.0f} s.")

    def _bucle(self) -> None:
        while not self._parar.is_set():
            try:
                self.escanear_ahora()
            except Exception as e:  # noqa: BLE001 — nunca tira abajo el server
                logger.warning(f"⚠️ Carpeta vigilada: el ciclo falló ({type(e).__name__}: {e}).")
            self._despertar.wait(self.intervalo_s)
            self._despertar.clear()

    def pedir_escaneo(self) -> bool:
        """Adelanta el próximo ciclo del hilo. False si el hilo no está corriendo."""
        self._despertar.set()
        return self._hilo is not None and self._hilo.is_alive()

    def detener(self, timeout: float | None = 15.0) -> None:
        """Corta el hilo; el proceso de la tanda en curso se mata (no se espera a que termine)."""
        self._parar.set()
        self._despertar.set()
        hilo = self._hilo
        if hilo is not None and hilo is not threading.current_thread():
            hilo.join(timeout)


def desde_entorno() -> Vigilante | None:
    """El vigilante configurado por el entorno, o None (y UNA línea de log) si está apagado."""
    carpeta = config.watch_folder()
    if carpeta is None:
        logger.info(f"📂 Carpeta vigilada: apagada ({config.VAR_WATCH} vacía o sin definir).")
        return None
    staging = config.staging_dir()
    excluir = [staging]
    with contextlib.suppress(config.DestinoNoConfigurado):
        excluir.append(config.itunes_dir())   # lo que `aplicar` copia ahí no vuelve a entrar
    return Vigilante(carpeta, staging=staging, registro=Registro(ruta_registro()),
                     intervalo_s=config.watch_interval_s(),
                     timeout_por_archivo_s=config.watch_timeout_s(), excluir=excluir)


# --- CLI ---------------------------------------------------------------------------------


def _cargar_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE, override=False)


def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="pipeline.vigilar",
        description="Un ciclo de la carpeta vigilada: procesa con el pipeline lo nuevo.")
    ap.add_argument("--carpeta", type=Path, default=None,
                    help=f"Carpeta a mirar (default: {config.VAR_WATCH}).")
    ap.add_argument("--lote", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--salida", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.lote is not None:
        if args.salida is None:
            ap.error("--lote necesita --salida")
        return _correr_lote(args.lote, args.salida)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _cargar_env()
    if args.carpeta is not None:
        os.environ[config.VAR_WATCH] = str(args.carpeta)
    vig = desde_entorno()
    if vig is None:
        print(f"La carpeta vigilada está apagada: definí {config.VAR_WATCH} en el .env o "
              f"pasá --carpeta.")
        return USO
    e = vig.escanear_ahora()
    if e["ocupado"]:
        print("Otro proceso (el server u otra terminal) está procesando la carpeta vigilada "
              "ahora mismo. No hice nada: probá de nuevo cuando termine.")
        return FALLO
    if e["alcanzable"] is False:
        print(f"No pude leer la carpeta: {e['ultimo_error']}")
        return FALLO
    print(f"«{e['carpeta']}» · todavía escribiéndose {e['creciendo']} · a medio bajar "
          f"{e['ignorados']} · quedan para el próximo ciclo {e['pendientes']} · en el registro: "
          f"procesados {e['procesados']}, fallidos {e['fallidos']}")
    if e["ultimo_reporte"]:
        print(f"Reporte de esta tanda: {vig.staging.parent / e['ultimo_reporte']}")
        print("Revisalo, bajá decisiones.json y después: "
              "python -m pipeline.aplicar --decisiones decisiones.json")
    print("No se tocó ningún original.")
    return OK


if __name__ == "__main__":
    sys.exit(main())
