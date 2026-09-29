"""La radio desde una playlist de MusiFlix (f33): qué temas de la playlist entran al set.

El problema que lo originó: la pantalla de Radio DJ armaba con TODA la biblioteca del motor, y
el motor no sabe de géneros. Un set salió mezclando Hard Bounce, Industrial y Techno, y metió
dos veces el mismo tema (el mismo master en dos carpetas). Decisiones del dueño (2026-09-29),
que son el contrato de este módulo:

1. La radio arma SOLO desde una playlist que arma el DJ (`mi_playlists` de db.py).
2. Solo entra el MISMO GÉNERO que la semilla. El género es el que tiene guardado el item de
   la playlist, normalizado (`normalizar`). Vacío o "Sin género" (así lo rotula la home,
   server.py `_cargar_biblioteca`) = sin género: no entra, y se dice.
3. BPM, key y energía son los del MOTOR (su análisis en la base), nunca los del snapshot del
   item: esos vienen de Deezer o del XML y no son lo que mide la radio.
4. Sin archivo local no hay nada que analizar ni que tocar: no entra, y se dice.
5. Lo que el motor todavía no analizó se analiza en segundo plano (`AnalisisEnFondo`), con
   el mismo camino que `python -m motor scan` (`motor.cli.analizar_para_guardar`).
6. Licencia y origen (spec §5: obligatorios, no se inventan) salen de reglas fijas
   (`licencia_y_origen`). Sin origen no se analiza.
7. Duplicados (mismo artista + título normalizados, o el mismo archivo): entra el PRIMERO
   en el orden de la playlist; los demás quedan como "duplicado" y dicen de cuál.
8. El scoring NO se toca: esto solo FILTRA el pool antes de `motor.radio.build_set`, y lo
   filtra conservando el orden de la biblioteca (`pool_para`), así una playlist que tiene
   toda la biblioteca da exactamente el set de la CLI.

Lógica pura: no importa `server`, `db` ni `motor` (la imagen Docker puede no traer motor/;
ver `server._usar_store_motor`). Lo que toca el disco —si el archivo existe, el mtime para
recordar un fallo— entra por parámetro o está aislado en funciones chicas, para que los
tests controlen cada caso.
"""
from __future__ import annotations

import logging
import os
import threading
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("bot_web")

# --- estados de un item ------------------------------------------------------------------
# Uno por item, en el orden en que se deciden (`clasificar`). El orden de ESTADOS es el del
# resumen que ve la pantalla: primero lo que entra o va a entrar, después lo que falta arreglar.
LISTO = "listo"                        # analizado por el motor, con archivo y género: entra
POR_ANALIZAR = "por_analizar"          # con archivo y género, el motor todavía no lo midió
FALLO_ANALISIS = "fallo_analisis"      # se intentó analizar y no se pudo (motivo del motor)
SIN_ARCHIVO = "sin_archivo"            # el item no tiene archivo local (por bajar)
ARCHIVO_NO_EXISTE = "archivo_no_existe"  # tenía archivo y ya no está donde se guardó
SIN_GENERO = "sin_genero"              # vacío, None o "Sin género"
SIN_ORIGEN = "sin_origen"              # ni link ni fuente: no hay de dónde decir que salió
DUPLICADO = "duplicado"                # el mismo tema (o archivo) ya está antes en la playlist
ESTADOS = (LISTO, POR_ANALIZAR, FALLO_ANALISIS, SIN_ARCHIVO, ARCHIVO_NO_EXISTE, SIN_GENERO,
           SIN_ORIGEN, DUPLICADO)

# Solo en el recuento de un SET: un item de otro género que la semilla. No es un estado del
# item (en otra semilla ese mismo item sí entra).
OTRO_GENERO = "otro_genero"

# --- regla 6: licencia y origen ------------------------------------------------------------
# Los 197 temas que el dueño escaneó de su colección tienen exactamente estos dos textos: un
# tema agregado desde la home (su biblioteca de Rekordbox) es de esa misma colección.
FUENTE_BIBLIOTECA = "biblioteca"
LICENCIA_BIBLIOTECA = "biblioteca personal"
ORIGEN_BIBLIOTECA = "coleccion Rekordbox local"
# Un tema bajado del buscador: no sabemos con qué derecho, y se dice así en vez de inventar.
LICENCIA_DESCARGA = "descarga, licencia sin verificar"

# Cómo rotula la home un tema sin género (server.py, `_cargar_biblioteca`). Con y sin tilde:
# alguien que lo escriba a mano en otra herramienta no tiene por qué acertarle al acento.
_SIN_GENERO = {"sin género", "sin genero"}


def normalizar(texto) -> str:
    """Texto comparable: NFKC, casefold, sin espacios en las puntas y los de adentro
    colapsados a uno. "  Hard   Bounce" y "hard bounce" son el mismo género; "ＴＥＣＨＮＯ"
    (ancho completo, típico de un copiar-pegar) y "techno" también."""
    if texto is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", str(texto)).casefold().split())


def mostrar(texto) -> str | None:
    """El texto como lo escribió el DJ, sin espacios de sobra. `None` si queda vacío."""
    t = " ".join(str(texto or "").split())
    return t or None


def clave_genero(genero) -> str | None:
    """La clave con la que se comparan géneros, o `None` = sin género (regla 2)."""
    n = normalizar(genero)
    return None if not n or n in _SIN_GENERO else n


def licencia_y_origen(item: Mapping) -> tuple[str, str] | None:
    """La licencia y el origen con que se GUARDA en la base del motor el análisis de este
    item (regla 6). `None` si no hay origen: no se analiza (spec §5, no se inventa)."""
    fuente = (item.get("fuente") or "").strip()
    if fuente.casefold() == FUENTE_BIBLIOTECA:
        return LICENCIA_BIBLIOTECA, ORIGEN_BIBLIOTECA
    origen = (item.get("url") or "").strip() or fuente
    return (LICENCIA_DESCARGA, origen) if origen else None


def claves_de_ruta(ruta: str) -> tuple[str, ...]:
    r"""Las formas con que una ruta puede estar guardada en la base del motor, normalizadas
    como la clave del store (`os.path.normcase`): la absoluta (`Store._ruta`) y la resuelta
    (`cli._clave`, con la que guarda el scan). Casi siempre son la misma; difieren con un
    symlink o una unidad `subst`, y ahí buscar solo una haría que un tema ya escaneado se
    vuelva a analizar como si fuera nuevo. La ÚLTIMA es la identidad del archivo."""
    absoluta = os.path.normcase(os.path.abspath(ruta))
    try:
        resuelta = os.path.normcase(str(Path(ruta).resolve()))
    except OSError:
        resuelta = absoluta
    return (absoluta,) if absoluta == resuelta else (absoluta, resuelta)


def clave_de_track(track) -> str:
    """La clave de un `Track` del motor, comparable con `claves_de_ruta`."""
    return os.path.normcase(str(track.path))


def etiqueta(item: Mapping) -> str:
    """"Artista — Título" del item (lo que el DJ puso en la playlist), o solo el título."""
    titulo = mostrar(item.get("titulo")) or "(sin título)"
    artista = mostrar(item.get("artista"))
    return f"{artista} — {titulo}" if artista else titulo


# --- clasificación ---------------------------------------------------------------------------

@dataclass
class ItemRadio:
    """Un item de la playlist con lo que la radio decidió de él."""
    item: Mapping                 # la fila de `db.get_playlist_radio` (ruta incluida)
    posicion: int                 # 1-based, en el orden de la playlist
    estado: str
    motivo: str | None            # en texto, para la pantalla (None = listo, no hay nada que decir)
    genero: str | None            # como lo escribió el DJ (sin espacios de sobra)
    clave_genero: str | None      # normalizado; None = sin género
    licencia: str | None
    origen: str | None
    # El Track del motor si el archivo del item está analizado Y AL DÍA (mismo mtime). Puede
    # estar también en un item que no entra (un duplicado cuyo archivo se escaneó): sirve para
    # reconocer la semilla que pide el cliente y decirle por qué no puede serlo.
    track: object = None
    duplicado_de: int | None = None   # id del item que quedó

    @property
    def etiqueta(self) -> str:
        return etiqueta(self.item)


def clasificar(items: Sequence[Mapping], analizados: Mapping[str, object], *,
               existe: Callable[[str], bool] = os.path.isfile,
               fallo: Callable[[str], str | None] = lambda ruta: None) -> list[ItemRadio]:
    """El estado de cada item de la playlist, en su orden.

    `analizados`: clave de ruta (`claves_de_ruta`) → Track del motor, SOLO de los archivos
    cuyo análisis está al día (el server lo arma con `Store.needs_analysis`, la misma
    condición del scan). `existe` y `fallo` (el motivo de un análisis que ya falló para ese
    archivo, `AnalisisEnFondo.motivo_fallo`) son lo único que mira el disco.

    El orden de las preguntas es el que sirve para arreglarlo: sin archivo no hay nada más
    que decir; con archivo pero sin género, el DJ lo arregla en la playlist; y recién entre
    los que podrían entrar se buscan duplicados. Un duplicado se decide ANTES que el estado
    del análisis a propósito: si el primero está por analizar, el segundo no se analiza
    (no va a entrar igual) — y un análisis que FALLÓ no se queda con el lugar: si el primero
    no se puede analizar, el siguiente igual puede entrar.
    """
    out: list[ItemRadio] = []
    vistos: dict[tuple, ItemRadio] = {}     # identidad (tema o archivo) → el item que quedó
    for i, it in enumerate(items, 1):
        genero = mostrar(it.get("genero"))
        cg = clave_genero(genero)
        lo = licencia_y_origen(it)
        licencia, origen = lo if lo else (None, None)
        c = ItemRadio(it, i, LISTO, None, genero if cg else None, cg, licencia, origen)
        out.append(c)
        ruta = it.get("ruta")
        if not ruta:
            c.estado, c.motivo = SIN_ARCHIVO, (
                "no tiene archivo en esta PC (está por bajar): bajalo desde el buscador, o "
                "agregalo desde tu biblioteca en la home")
            continue
        if not existe(ruta):
            c.estado, c.motivo = ARCHIVO_NO_EXISTE, (
                "el archivo ya no está donde se guardó (¿se movió o se borró?): volvé a "
                "agregarlo a la playlist")
            continue
        claves = claves_de_ruta(ruta)
        c.track = next((analizados[k] for k in claves if k in analizados), None)
        if cg is None:
            c.estado, c.motivo = SIN_GENERO, (
                "no tiene género: la radio arma solo con temas del mismo género que la "
                "semilla, y sin género no hay con qué compararlo")
            continue
        if lo is None:
            c.estado, c.motivo = SIN_ORIGEN, (
                "no se sabe de dónde salió (no tiene link ni fuente): sin origen no se "
                "analiza (la licencia y el origen son obligatorios)")
            continue
        motivo_fallo = None if c.track is not None else fallo(ruta)
        if motivo_fallo:
            c.estado, c.motivo = FALLO_ANALISIS, f"el análisis falló: {motivo_fallo}"
            continue
        identidades = [("archivo", claves[-1])]
        titulo, artista = normalizar(it.get("titulo")), normalizar(it.get("artista"))
        if titulo:   # sin título no hay tema que comparar: "" == "" no es el mismo tema
            identidades.append(("tema", artista, titulo))
        primero = next((vistos[k] for k in identidades if k in vistos), None)
        if primero is not None:
            c.estado, c.duplicado_de = DUPLICADO, primero.item.get("id")
            c.motivo = (f"duplicado de «{primero.etiqueta}» (#{primero.posicion} de la "
                        f"playlist): entra solo el primero")
            continue
        for k in identidades:
            vistos[k] = c
        if c.track is None:
            c.estado, c.motivo = POR_ANALIZAR, "el motor todavía no lo analizó"
            continue
        # Listo. La licencia y el origen que se MUESTRAN son los que tiene la base del motor
        # para ese archivo: si lo escaneó la CLI con otros, esos son los verdaderos (§6).
        c.licencia, c.origen = c.track.license, c.track.source_url
    return out


def resumen(clasificados: Sequence[ItemRadio]) -> dict[str, int]:
    """Cuántos items hay en cada estado (todos los estados, también en cero)."""
    cuenta = dict.fromkeys(ESTADOS, 0)
    for c in clasificados:
        cuenta[c.estado] += 1
    return cuenta


def excluidos_para(clasificados: Sequence[ItemRadio], clave: str) -> tuple[int, dict[str, int]]:
    """`(entran, excluidos)` de un set cuya semilla es del género `clave`.

    Es una PARTICIÓN de la playlist: `entran + sum(excluidos) == len(playlist)`, para que la
    pantalla pueda decir "entran 18 de 40 (12 de otro género, 3 sin archivo…)" sin que los
    números dejen de cerrar. Un item con género distinto cuenta como "otro género" aunque
    además esté por analizar o duplicado: para ESTE set, lo que lo deja afuera es el género.
    """
    excluidos = dict.fromkeys((OTRO_GENERO, *ESTADOS[1:]), 0)
    entran = 0
    for c in clasificados:
        if c.clave_genero is not None and c.clave_genero != clave:
            excluidos[OTRO_GENERO] += 1
        elif c.estado == LISTO:
            entran += 1
        else:
            excluidos[c.estado] += 1
    return entran, excluidos


@dataclass(frozen=True)
class Pool:
    """Con qué arma el motor un set desde una semilla de la playlist."""
    tracks: list                  # Tracks del motor, en el orden de la biblioteca
    genero: str                   # el de la semilla, como se muestra
    entran: int
    total: int                    # items de la playlist
    excluidos: dict = field(default_factory=dict)


def pool_para(clasificados: Sequence[ItemRadio], semilla: ItemRadio,
              biblioteca: Sequence) -> Pool:
    """El pool del set: los items LISTOS del género de la semilla (semilla incluida), como
    Tracks del motor y en el orden de `biblioteca` (`Store.load_library`, por ruta).

    El orden de la biblioteca y no el de la playlist: hoy `build_set` ordena el pool por ruta
    antes de desempatar (motor/radio.py, `_pool`), así que no cambiaría nada, pero entregarle
    lo mismo que le entrega la CLI no depende de ese detalle: una playlist con toda la
    biblioteca arma EXACTAMENTE el set de `python -m motor radio`.
    Los duplicados ya quedaron afuera en `clasificar`, así que no hay un archivo dos veces.
    """
    if semilla.estado != LISTO or semilla.clave_genero is None:
        raise ValueError(f"la semilla tiene que estar lista y con género (está {semilla.estado})")
    claves = {clave_de_track(c.track) for c in clasificados
              if c.estado == LISTO and c.clave_genero == semilla.clave_genero}
    tracks = [t for t in biblioteca if clave_de_track(t) in claves]
    entran, excluidos = excluidos_para(clasificados, semilla.clave_genero)
    return Pool(tracks, semilla.genero, entran, len(clasificados), excluidos)


def generos(clasificados: Sequence[ItemRadio]) -> list[dict]:
    """Por cada género que tiene algún item listo: cuántos entrarían a un set de ese género
    y qué queda afuera. La pantalla lo muestra al elegir la semilla, ANTES de armar (el set
    trae lo mismo, calculado con `pool_para`). El nombre es el del primer item de ese género."""
    vistos: dict[str, str] = {}
    for c in clasificados:
        if c.estado == LISTO and c.clave_genero not in vistos:
            vistos[c.clave_genero] = c.genero
    out = []
    for clave, nombre in vistos.items():
        entran, excluidos = excluidos_para(clasificados, clave)
        out.append({"genero": nombre, "clave": clave, "entran": entran,
                    "total": len(clasificados), "excluidos": excluidos})
    return out


# --- regla 5: análisis en segundo plano -----------------------------------------------------

@dataclass(frozen=True)
class TareaAnalisis:
    item_id: int
    etiqueta: str
    ruta: str
    licencia: str
    origen: str


def _mtime(ruta: str) -> float | None:
    try:
        return os.stat(ruta).st_mtime
    except OSError:
        return None


class AnalisisEnFondo:
    """Analiza los temas de UNA playlist en un hilo, uno por uno, y cuenta cómo va.

    Uno a la vez por proceso: analizar es CPU pura (~3 s por tema) y dos análisis en
    paralelo solo harían que los dos tarden el doble mientras la pantalla se arrastra.
    `arrancar` con uno ya corriendo no arranca otro: devuelve el progreso del que corre
    (idempotente si es la misma playlist; `ocupado_por` si es otra).

    El hilo no toca el event loop de FastAPI: los endpoints solo leen el progreso (con el
    lock) y la radio sigue leyendo la base mientras el hilo escribe, con la misma espera
    corta de siempre (`server._usar_store_motor`, estado "base-ocupada").

    Los fallos se recuerdan por (archivo, mtime): el mismo archivo sin cambios no se vuelve
    a intentar en cada vuelta (su motivo se muestra); si el archivo cambia, sí. Viven en
    memoria: reiniciar el server los vuelve a intentar, que es lo que se quiere después de
    arreglar lo que fallaba.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hilo: threading.Thread | None = None
        self._corriendo = False
        self._pid: int | None = None
        self._nombre: str | None = None
        self._hechos = 0
        self._total = 0
        self._actual: str | None = None
        self._fallidos: list[dict] = []
        self._fallos: dict[tuple[str, float], str] = {}

    def motivo_fallo(self, ruta: str) -> str | None:
        """Por qué falló el análisis de este archivo TAL COMO ESTÁ HOY, o None."""
        mtime = _mtime(ruta)
        if mtime is None:
            return None
        clave = claves_de_ruta(ruta)[-1]
        with self._lock:
            return self._fallos.get((clave, mtime))

    def _progreso(self, pid: int | None) -> dict:
        propio = pid is not None and pid == self._pid
        return {
            "corriendo": self._corriendo and propio,
            "hechos": self._hechos if propio else 0,
            "total": self._total if propio else 0,
            "actual": self._actual if propio and self._corriendo else None,
            "fallidos": list(self._fallidos) if propio else [],
            # Otra playlist se está analizando: esta espera (un análisis a la vez).
            "ocupado_por": ({"id": self._pid, "nombre": self._nombre}
                            if self._corriendo and not propio else None),
        }

    def progreso(self, pid: int | None) -> dict:
        with self._lock:
            return self._progreso(pid)

    def arrancar(self, pid: int, nombre: str, tareas: Sequence[TareaAnalisis],
                 analizar: Callable[[TareaAnalisis], str | None]) -> tuple[bool, dict]:
        """Arranca el análisis de `tareas` si no hay otro corriendo. `(arrancó, progreso)`.

        `analizar(tarea)` devuelve None si anduvo o el MOTIVO si no; si levanta, el motivo
        es la excepción. Sin tareas no arranca nada (y el progreso de la última corrida de
        esa playlist queda, para que la pantalla pueda decir cómo terminó)."""
        with self._lock:
            if self._corriendo or not tareas:
                return False, self._progreso(pid)
            self._corriendo = True
            self._pid, self._nombre = pid, nombre
            self._hechos, self._total = 0, len(tareas)
            self._actual, self._fallidos = None, []
            self._hilo = threading.Thread(target=self._correr, args=(list(tareas), analizar),
                                          name=f"radio-analisis-{pid}", daemon=True)
            self._hilo.start()
            return True, self._progreso(pid)

    def _correr(self, tareas: list[TareaAnalisis], analizar) -> None:
        try:
            for t in tareas:
                with self._lock:
                    self._actual = t.etiqueta
                try:
                    motivo = analizar(t)
                except Exception as e:  # noqa: BLE001 — un archivo raro no frena la playlist
                    motivo = f"{type(e).__name__}: {e}"
                mtime, clave = _mtime(t.ruta), claves_de_ruta(t.ruta)[-1]
                with self._lock:
                    if motivo:
                        logger.warning(f"⚠️ Radio: no pude analizar «{t.etiqueta}»: {motivo}")
                        self._fallidos.append({"item_id": t.item_id, "titulo": t.etiqueta,
                                               "motivo": motivo})
                        if mtime is not None:
                            self._fallos[(clave, mtime)] = motivo
                    else:
                        self._fallos = {k: v for k, v in self._fallos.items() if k[0] != clave}
                    self._hechos += 1
        finally:
            with self._lock:
                self._corriendo = False
                self._actual = None

    def esperar(self, timeout: float | None = None) -> bool:
        """Espera a que termine el análisis en curso (para los tests). True si terminó."""
        hilo = self._hilo
        if hilo is not None:
            hilo.join(timeout)
        with self._lock:
            return not self._corriendo
