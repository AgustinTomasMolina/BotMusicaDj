"""Carpeta vigilada (f65): lo nuevo que aparece en una carpeta entra solo al pipeline.

    python -m pipeline.vigilar                 # un ciclo ahora, sobre MUSIC_WATCH_FOLDER
    python -m pipeline.vigilar --carpeta <dir> # un ciclo sobre otra carpeta

En el server corre sola: un hilo mira la carpeta cada MUSIC_WATCH_INTERVAL_S segundos
(default 60) y `POST /api/vigilancia/escanear` adelanta el próximo ciclo.

NO es un pipeline aparte. Es una ENTRADA delgada sobre `pipeline.revisar.procesar`, el mismo
paso 1 que corre `python -m pipeline.revisar --carpeta`: calidad (corte espectral),
duplicados, BPM/tonalidad del motor, tags normalizados en la copia y el reporte HTML. Lo que
cambia es solo QUÉ archivos le llegan: los nuevos y terminados de la carpeta vigilada. Las
copias van al staging de siempre y el reporte de cada tanda queda al lado
(`revision_vigilada_<fecha>.html`); de ahí en más es el flujo de siempre: revisar a mano,
bajar decisiones.json y `python -m pipeline.aplicar` (iTunes + XML de Rekordbox). Nada llega a
iTunes ni a Rekordbox sin pasar por los ojos del dueño, igual que antes.

Decisiones:
- Los originales de la carpeta vigilada SOLO se leen (stat, listado y la lectura de
  `procesar`, que copia con copy2 y taggea la copia). Nunca se mueven, renombran ni borran.
- "Nuevo" = (ruta, tamaño, mtime) que no está en el registro. El pipeline no tiene un registro
  de lo ya procesado (`revisar` procesa la carpeta entera cada vez), así que el vigilante lleva
  uno chico en JSON (`carpeta_vigilada.json`, en MUSIFLIX_DATA_DIR). Un archivo que cambia se
  vuelve a procesar; uno que falló no se reintenta hasta que cambie.
- Un archivo a medio escribir no entra: se ignoran `.part`, `.tmp`, `.crdownload` (también
  como `tema.temp.mp3` / `tema.part.mp3`), ocultos y de sistema, y los que cambian de tamaño o mtime entre dos miradas separadas unos segundos.
- La carpeta puede ser de red (\\\\NAS\\musica): si no está, se anota, se reintenta en el
  próximo ciclo y el server sigue. Nunca levanta hacia el hilo.
- De a UN proceso de análisis por vez, en un proceso APARTE con tope de tiempo: un hilo de
  Python no se puede matar, un proceso sí. La tanda va junta (los duplicados se buscan entre
  los archivos de la tanda, con la lógica de siempre); si la tanda se pasa del tope o se cae,
  sus archivos se reintentan de a uno, y el que vuelve a fallar solo queda como fallido.
  Tanda acotada (`max_por_ciclo`): lo que sobra espera al ciclo siguiente.
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
import os
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
MAX_POR_CICLO = 20

PROCESADO = "procesado"
FALLIDO = "fallido"

OK, FALLO, USO = 0, 1, 2


class CarpetaInalcanzable(OSError):
    """La carpeta vigilada no está (disco desenchufado, NAS apagado, red caída)."""


def clave(ruta: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(str(ruta)))


def _etiqueta(carpeta: Path) -> str:
    """Cómo se nombra la carpeta en logs y respuestas: el último tramo, sin la ruta entera."""
    partes = [p for p in str(carpeta).replace("\\", "/").split("/") if p]
    return partes[-1] if partes else str(carpeta)


def _sin_rutas(texto: str, rutas: Iterable[str]) -> str:
    """Un motivo de fallo sin rutas de la PC: cada ruta se reemplaza por su nombre."""
    for r in sorted({str(r) for r in rutas}, key=len, reverse=True):
        nombre = Path(r).name or r
        for forma in {r, os.path.abspath(r), r.replace("\\", "/"),
                      os.path.abspath(r).replace("\\", "/")}:
            texto = texto.replace(forma, nombre)
    return texto[:300]


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


def _nombre_ignorado(nombre: str) -> bool:
    n = nombre.lower()
    return (n.startswith((".", "~$")) or n.endswith(SUFIJOS_EN_CURSO)
            or any(i in n for i in INFIJOS_EN_CURSO))


def _oculto(st: os.stat_result) -> bool:
    """Oculto o de sistema para Windows. En Linux/mac alcanza con el punto del nombre."""
    return bool(getattr(st, "st_file_attributes", 0) & _ATRIBUTOS_OCULTOS)


def descubrir(carpeta: Path, excluir: Iterable[Path] = ()) -> list[Foto]:
    """Los audios de la carpeta (recursivo), sin los en curso, ocultos ni de sistema.

    `excluir`: carpetas que NO se recorren aunque estén adentro (el staging, la de iTunes):
    si el staging quedara dentro de la carpeta vigilada, las copias procesadas volverían a
    entrar como nuevas en cada ciclo. No sigue enlaces simbólicos (un enlace a un ancestro
    sería un recorrido infinito). Levanta `CarpetaInalcanzable` si la raíz no se puede leer.
    """
    raiz = str(carpeta)
    if not os.path.isdir(raiz):
        raise CarpetaInalcanzable(
            f"no puedo leer la carpeta vigilada «{_etiqueta(carpeta)}» (¿está montada?)")
    excluidas = {clave(e) for e in excluir}
    errores: list[OSError] = []
    fotos: list[Foto] = []
    for actual, dirs, archivos in os.walk(raiz, onerror=errores.append):
        quedan = []
        for d in sorted(dirs):
            p = os.path.join(actual, d)
            if _nombre_ignorado(d) or clave(p) in excluidas:
                continue
            try:
                if _oculto(os.stat(p)):
                    continue
            except OSError:
                continue
            quedan.append(d)
        dirs[:] = quedan
        for nombre in sorted(archivos):
            if _nombre_ignorado(nombre) or not nombre.lower().endswith(EXTS):
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


# --- registro de lo ya procesado ---------------------------------------------------------


def ruta_registro() -> Path:
    """Al lado de la base de MusiFlix (MUSIFLIX_DATA_DIR, mismo default que `db.py`)."""
    return Path(os.getenv("MUSIFLIX_DATA_DIR", str(BASE_DIR))) / NOMBRE_REGISTRO


class Registro:
    """Lo que el vigilante ya procesó (o falló), por (ruta, tamaño, mtime). En JSON."""

    def __init__(self, ruta: Path) -> None:
        self.ruta = Path(ruta)
        self._lock = threading.Lock()
        self._archivos: dict[str, dict] = self._cargar()

    def _cargar(self) -> dict[str, dict]:
        try:
            doc = json.loads(self.ruta.read_text(encoding="utf-8"))
            archivos = doc.get("archivos") if isinstance(doc, dict) else None
            if not isinstance(archivos, dict):
                raise ValueError("sin 'archivos'")
            return {k: v for k, v in archivos.items() if isinstance(v, dict)}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            # No se pisa: se aparta y se arranca de cero (reprocesar es caro pero no rompe
            # nada; pisarlo perdería la evidencia de qué pasó).
            apartado = self.ruta.with_name(
                f"{self.ruta.name}.ilegible-{datetime.datetime.now():%Y%m%d%H%M%S}")
            with contextlib.suppress(OSError):
                os.replace(self.ruta, apartado)
            logger.warning(f"⚠️ Carpeta vigilada: el registro estaba ilegible ({e}); "
                           f"se apartó como {apartado.name} y se arranca de cero.")
            return {}

    def es_nuevo(self, f: Foto) -> bool:
        with self._lock:
            e = self._archivos.get(f.clave)
        return e is None or e.get("tamano") != f.tamano or e.get("mtime_ns") != f.mtime_ns

    def marcar(self, f: Foto, estado: str, **extra) -> None:
        from motor.modelos import NO_DECLARADO

        with self._lock:
            self._archivos[f.clave] = {
                "archivo": Path(f.ruta).name, "tamano": f.tamano, "mtime_ns": f.mtime_ns,
                "estado": estado, "cuando": datetime.datetime.now().isoformat(timespec="seconds"),
                "licencia": NO_DECLARADO, "origen": NO_DECLARADO, **extra}

    def guardar(self) -> None:
        with self._lock:
            texto = json.dumps({"version": 1, "archivos": self._archivos},
                               ensure_ascii=False, indent=1)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta.with_name(self.ruta.name + ".tmp")
        tmp.write_text(texto, encoding="utf-8")
        os.replace(tmp, self.ruta)          # atómico: un corte a mitad no deja medio JSON

    def contar(self) -> dict[str, int]:
        with self._lock:
            n = {PROCESADO: 0, FALLIDO: 0}
            for e in self._archivos.values():
                n[e.get("estado", "")] = n.get(e.get("estado", ""), 0) + 1
            return n

    def entrada(self, ruta: str) -> dict | None:
        with self._lock:
            e = self._archivos.get(clave(ruta))
            return dict(e) if e else None


# --- procesar una tanda en un proceso aparte ---------------------------------------------


@dataclass
class ResultadoLote:
    ok: bool
    filas: list[dict] = field(default_factory=list)
    reporte: str | None = None
    motivo: str = ""
    cancelado: bool = False


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
                    return ResultadoLote(False, motivo=f"superó el tope de {timeout_s:.0f} s")
        if rc != 0:
            cola = errlog.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            ultima = cola[-1] if cola else f"código {rc}"
            return ResultadoLote(False, motivo=_sin_rutas(f"el proceso falló: {ultima}", rutas))
        try:
            doc = json.loads(salida.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return ResultadoLote(False, motivo=f"el proceso no dejó resultado legible ({e})")
        return ResultadoLote(True, filas=list(doc.get("filas") or []), reporte=doc.get("reporte"))


def _correr_lote(entrada: Path, salida: Path) -> int:
    """El lado del proceso hijo: el paso 1 del pipeline sobre la tanda, tal cual."""
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


# --- el vigilante ------------------------------------------------------------------------


Procesador = Callable[..., ResultadoLote]


class Vigilante:
    def __init__(self, carpeta: Path, *, staging: Path, registro: Registro,
                 intervalo_s: float = config.WATCH_INTERVALO_DEFAULT_S,
                 timeout_por_archivo_s: float = config.WATCH_TIMEOUT_DEFAULT_S,
                 arranque_s: float = ARRANQUE_S, espera_estable_s: float = ESPERA_ESTABLE_S,
                 max_por_ciclo: int = MAX_POR_CICLO, excluir: Iterable[Path] = (),
                 procesar: Procesador = procesar_aparte,
                 dormir: Callable[[float], None] = time.sleep) -> None:
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
        self._ciclo_lock = threading.Lock()       # un ciclo por vez: hilo y "escanear ya"
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._despertar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._solos: set[str] = set()             # claves que se reintentan de a una
        self._alcanzable: bool | None = None
        self._ultimo_error: str | None = None
        self._ultimo_ciclo: str | None = None
        self._ultimo_reporte: str | None = None
        self._actual: list[str] = []
        self._pendientes = 0
        self._creciendo = 0

    # -- un ciclo -----------------------------------------------------------------------

    def escanear_ahora(self) -> dict:
        """Un ciclo completo, ya. Si hay otro corriendo no se lanza un segundo."""
        if not self._ciclo_lock.acquire(blocking=False):
            return {**self.estado(), "ocupado": True}
        try:
            self._ciclo()
        finally:
            self._ciclo_lock.release()
        return {**self.estado(), "ocupado": False}

    def _ciclo(self) -> None:
        ahora = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            fotos = descubrir(self.carpeta, self.excluir)
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

        nuevos = [f for f in fotos if self.registro.es_nuevo(f)]
        listos, cambiando = estables(nuevos, self.espera_estable_s, self._dormir)
        solos = [f for f in listos if f.clave in self._solos][: self.max_por_ciclo]
        resto = [f for f in listos if f.clave not in self._solos]
        resto = resto[: self.max_por_ciclo - len(solos)]
        lotes = [[f] for f in solos] + ([resto] if resto else [])
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

    def _ruta_reporte(self) -> Path:
        carpeta = self.staging.parent
        sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        ruta = carpeta / f"revision_vigilada_{sello}.html"
        n = 2
        while ruta.exists():
            ruta = carpeta / f"revision_vigilada_{sello}_{n}.html"
            n += 1
        return ruta

    def _procesar_lote(self, lote: list[Foto]) -> None:
        reporte = self._ruta_reporte()
        timeout = self.arranque_s + self.timeout_por_archivo_s * len(lote)
        with self._lock:
            self._actual = [Path(f.ruta).name for f in lote]
        logger.info(f"📂 Carpeta vigilada: procesando {len(lote)} archivo(s) nuevo(s).")
        try:
            res = self._procesar([f.ruta for f in lote], self.staging, reporte, timeout,
                                 self._parar)
        except Exception as e:  # noqa: BLE001 — el hilo no se cae por un archivo
            res = ResultadoLote(False, motivo=f"{type(e).__name__}: {e}")
        finally:
            with self._lock:
                self._actual = []
        if res.cancelado:
            return
        motivo = _sin_rutas(res.motivo, [f.ruta for f in lote])
        if res.ok:
            por_ruta = {clave(f.get("ruta_original", "")): f for f in res.filas}
            for foto in lote:
                fila = por_ruta.get(foto.clave)
                if fila is None:
                    self.registro.marcar(foto, FALLIDO, motivo="el pipeline no devolvió resultado")
                else:
                    bpm = fila.get("bpm") or 0.0
                    self.registro.marcar(
                        foto, PROCESADO, reporte=Path(res.reporte or reporte).name,
                        revision=fila.get("estado", ""), bandera=fila.get("bandera", ""),
                        bpm=round(float(bpm), 1) if bpm > 0 else None,
                        camelot=fila.get("camelot") or "?")
                self._solos.discard(foto.clave)
            with self._lock:
                self._ultimo_reporte = Path(res.reporte or reporte).name
        elif len(lote) > 1:
            # La tanda entera no anduvo: no se sabe cuál fue. Se reintentan de a uno.
            logger.warning(f"⚠️ Carpeta vigilada: la tanda de {len(lote)} falló ({motivo}); "
                           f"se reintenta de a un archivo.")
            self._solos.update(f.clave for f in lote)
        else:
            foto = lote[0]
            logger.warning(f"⚠️ Carpeta vigilada: «{Path(foto.ruta).name}» falló: {motivo}")
            self.registro.marcar(foto, FALLIDO, motivo=motivo)
            self._solos.discard(foto.clave)
        try:
            self.registro.guardar()
        except OSError as e:
            logger.warning(f"⚠️ Carpeta vigilada: no pude guardar el registro ({e}).")

    # -- estado -------------------------------------------------------------------------

    def estado(self) -> dict:
        n = self.registro.contar()
        with self._lock:
            return {
                "carpeta": _etiqueta(self.carpeta), "alcanzable": self._alcanzable,
                "corriendo": self._hilo is not None and self._hilo.is_alive(),
                "procesando": list(self._actual), "pendientes": self._pendientes,
                "creciendo": self._creciendo, "procesados": n.get(PROCESADO, 0),
                "fallidos": n.get(FALLIDO, 0), "ultimo_ciclo": self._ultimo_ciclo,
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
    if e["alcanzable"] is False:
        print(f"No pude leer la carpeta: {e['ultimo_error']}")
        return FALLO
    print(f"«{e['carpeta']}» · todavía escribiéndose {e['creciendo']} · quedan para el "
          f"próximo ciclo {e['pendientes']} · en el registro: procesados {e['procesados']}, "
          f"fallidos {e['fallidos']}")
    if e["ultimo_reporte"]:
        print(f"Reporte de esta tanda: {vig.staging.parent / e['ultimo_reporte']}")
        print("Revisalo, bajá decisiones.json y después: "
              "python -m pipeline.aplicar --decisiones decisiones.json")
    print("No se tocó ningún original.")
    return OK


if __name__ == "__main__":
    sys.exit(main())
