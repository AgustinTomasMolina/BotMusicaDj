"""Capa 2b — persistencia del análisis y biblioteca para la búsqueda vectorial.

SQLite (stdlib) + producto matricial numpy. Alcanza de sobra para una biblioteca personal
y no necesita levantar ningún servicio. Roadmap: Postgres + pgvector con índice HNSW
(gratis y sobra hasta ~1M tracks); Qdrant recién si escala más. La interfaz de esta clase
es el contrato que esa migración tiene que respetar.

Tres decisiones del esquema que NO son detalles:

1. **`mtime`** — la caché se invalida por fecha de modificación del archivo. Un re-scan
   no vuelve a analizar lo que no cambió (el §4 pide ≤10 s/track: reanalizar 10k tracks
   por gusto son horas).

2. **El embedding se guarda CRUDO (float32, sin normalizar)**. Normalizar es relativo a
   toda la biblioteca: si se guardara ya normalizado, cada track nuevo cambiaría la media
   y el desvío e invalidaría las filas de todos los demás. Guardando crudo, un track nuevo
   cuesta un INSERT.

3. **Las stats de normalización (media y desvío por dimensión) van en su propia tabla**,
   porque son de la BIBLIOTECA, no de ningún track. Están para que `normalize_one` pueda
   ubicar un vector suelto en la misma escala que la matriz sin recalcular todo.

Determinismo (spec §5): el orden de todo lo que devuelve el store lo fija Python ordenando
por la clave de la ruta (absoluta + `normcase`), no la collation de SQLite ni el orden de
inserción.
"""
import os
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from motor.embeddings import DIM, normalize_matrix, normalize_one
from motor.energia import percentil
from motor.modelos import (
    Track,
    TrackFeatures,
    require_acuerdo_key,
    require_finite_bpm,
    require_text,
)

# Versión del esquema, guardada en `PRAGMA user_version`. Subirla SIEMPRE que cambie la forma
# de una tabla, y agregar el paso a `_MIGRACIONES`. `CREATE TABLE IF NOT EXISTS` no altera
# una tabla que ya existe: sin versión, una base vieja se abre "bien" y revienta en el primer
# INSERT que el esquema viejo no acepta — que en el scan llega DESPUÉS de haber borrado filas.
#
#   0  sin versionar: cualquier base anterior a esta constante (commits 9440251 a df9819a)
#   1  rms / onset_rate / percussive_ratio aceptan NULL (9440251 los tenía NOT NULL)
#   2  columna path_key (ruta absoluta + normcase) con índice único
#   3  columnas key_acuerdo / key_tramos (la confianza de la key, tarea 17)
VERSION_ESQUEMA = 3

# Cuánto espera una apertura a que OTRO proceso suelte la base (por ejemplo, porque la está
# migrando) antes de rendirse con `sqlite3.OperationalError: database is locked`. La CLI
# convierte ese error en un mensaje de uso (`cli._abrir_store`). Hallazgo H2, tarea 1.2.
ESPERA_BLOQUEO_S = 30.0

_DDL_TRACKS = """
CREATE TABLE IF NOT EXISTS tracks (
    path            TEXT PRIMARY KEY,   -- la ruta con la grafía recibida (absoluta), para usarla
    path_key        TEXT,               -- la CLAVE: absoluta + normcase (índice único abajo)
    mtime           REAL NOT NULL,      -- para invalidar la caché si cambió el archivo
    duration        REAL NOT NULL,
    artist          TEXT,
    title           TEXT,
    bpm             REAL NOT NULL,
    key             TEXT NOT NULL,      -- Camelot
    energy_raw      REAL NOT NULL,      -- RMS absoluto; el percentil se calcula al leer
    embedding       BLOB NOT NULL,      -- float32 crudo, sin normalizar
    -- Detalle de energía (debug / reajuste de pesos). NULL = no se midió: un 0 inventado
    -- se leería como una medición (spec §6). `percussive_ratio` hoy es siempre NULL
    -- (necesita HPSS, inviable dentro del §4; ver motor/modelos.py).
    rms             REAL,
    onset_rate      REAL,
    percussive_ratio REAL,
    -- Confianza de la KEY: el acuerdo entre tramos de `tono_consenso` ("2/3") y qué votó
    -- cada tramo ("8A|3B|8A"). NULL en las dos = el consenso no se corrió (fila analizada
    -- por un código anterior a la tarea 17), y la CLI muestra `?`. "0/0" con tramos "" es
    -- distinto: el consenso SÍ corrió y el track no daba para comparar tramos.
    key_acuerdo     TEXT,
    key_tramos      TEXT,
    license         TEXT NOT NULL,      -- obligatorio (spec §5)
    source_url      TEXT NOT NULL,      -- obligatorio (spec §5)
    analyzed_at     TEXT NOT NULL
)"""

# Sentencias sueltas y no un `executescript`: `executescript` hace COMMIT antes de correr, y
# las migraciones tienen que ir enteras dentro de UNA transacción (o todo o nada).
_DDL_INDICES = (
    "CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm)",
    "CREATE INDEX IF NOT EXISTS idx_tracks_key ON tracks(key)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_path_key ON tracks(path_key)",
)

# Estadísticas de normalización de la biblioteca (media y desvío por dimensión).
# Se invalidan con cada alta/baja y se recalculan cuando alguien las necesita.
_DDL_NORM_STATS = """
CREATE TABLE IF NOT EXISTS norm_stats (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    mean    BLOB NOT NULL,
    std     BLOB NOT NULL,
    count   INTEGER NOT NULL
)"""


class EsquemaIncompatible(Exception):
    """La base tiene una versión de esquema que este código no sabe leer. Se lanza al abrir,
    ANTES de modificar nada."""


# dtype del BLOB de embeddings: se ESCRIBE y se LEE con este. Leerlo con otro dtype no
# falla, devuelve basura silenciosa (90 float32 se leen como 45 float64 plausibles).
EMB_DTYPE = np.float32

# Las stats van en float64 a propósito: `normalize_one` tiene que caer EXACTAMENTE en la
# fila que produjo `normalize_matrix` (que trabaja en float64). Con stats en float32 las
# dos escalas difieren en el último bit y la caché de la biblioteca empieza a mentir.
STATS_DTYPE = np.float64

_COLUMNAS = ("path", "path_key", "mtime", "duration", "artist", "title", "bpm", "key",
             "energy_raw", "embedding", "rms", "onset_rate", "percussive_ratio",
             "key_acuerdo", "key_tramos", "license", "source_url", "analyzed_at")


def _opcional(valor: object) -> float | None:
    """float, o None si no se midió. `float(None)` revienta y `valor or 0.0` inventaría un
    cero: los dos caminos obvios están mal para un campo que puede no haberse medido."""
    return None if valor is None else float(valor)


class Store:
    """Caché de análisis + biblioteca en memoria para la búsqueda vectorial."""

    def __init__(self, db_path: Path | str = "djradio.sqlite", *,
                 espera_bloqueo_s: float | None = None) -> None:
        """`espera_bloqueo_s`: cuánto esperar a que otro proceso suelte la base antes de
        rendirse. Sin pasarlo vale `ESPERA_BLOQUEO_S`, que es lo que quiere la CLI (esperar
        30 s a que termine un scan es lo correcto en la terminal). Quien atiende pedidos
        interactivos lo baja: una pantalla congelada medio minuto no es "paciente", es una
        pantalla colgada (`server.py`, la radio).

        El default es `None` y no `ESPERA_BLOQUEO_S` a propósito: un default en la firma se
        evalúa al DEFINIR la función, así que `monkeypatch.setattr(store, "ESPERA_BLOQUEO_S",
        0.2)` dejaría de tener efecto — y eso es lo que usa
        `test_base_tomada_por_otra_instancia_es_error_de_uso` para no tardar 30 s. Leerlo
        acá adentro lo resuelve en cada llamada.
        """
        self.db_path = Path(db_path)
        if str(self.db_path.parent) not in ("", "."):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        espera = ESPERA_BLOQUEO_S if espera_bloqueo_s is None else espera_bloqueo_s
        self._con = sqlite3.connect(str(self.db_path), timeout=espera)
        self._con.row_factory = sqlite3.Row
        try:
            self._preparar_esquema()
        except BaseException:
            self._con.close()
            raise

    # -- ciclo de vida ------------------------------------------------------

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- escritura ----------------------------------------------------------

    def upsert(self, path: Path | str, features: TrackFeatures, *, duration: float,
               license: str, source_url: str, artist: str | None = None,
               title: str | None = None, mtime: float | None = None) -> None:
        """Guarda o actualiza el análisis de un track. La ruta es la clave: un segundo
        upsert de la misma ruta ACTUALIZA, no duplica.

        `license` y `source_url` son keyword y obligatorios — la misma regla que impone
        `Track`, acá en la frontera de la caché, para que no se pueda persistir un track
        sin procedencia (spec §5). El boceto viejo los pasaba adentro de un dict `tags`
        suelto, donde faltar era indistinguible de venir vacío.

        `mtime`: si no se pasa, sale de `stat()` del archivo. Se guarda tal cual para que
        `needs_analysis` pueda comparar por igualdad exacta.

        El embedding va como BLOB float32 CRUDO (ver decisión 2 del módulo).
        """
        vec = np.asarray(features.embedding)
        if vec.ndim != 1 or vec.size != DIM:
            raise ValueError(
                f"el embedding tiene que ser 1-D de largo DIM={DIM}, recibí shape {vec.shape}; "
                f"mezclar largos rompe la matriz de la biblioteca"
            )
        require_text(license, "license")
        require_text(source_url, "source_url")
        require_finite_bpm(features.bpm)
        # La confianza de la key se valida al ESCRIBIR y no al leer: acá el dato lo produce
        # el análisis y tiene que ser coherente; al leer, una fila corrupta se degrada a `?`
        # en vez de tirar la biblioteca entera (ver `require_acuerdo_key`).
        require_acuerdo_key(features.key_acuerdo, features.key_tramos)

        key = self._key(path)
        mt = float(mtime) if mtime is not None else Path(path).stat().st_mtime
        # INSERT OR REPLACE choca por `path_key` (índice único) aunque la ruta llegue escrita
        # con otras mayúsculas: la fila vieja se reemplaza y queda la grafía nueva.
        valores = (self._ruta(path), key, mt, float(duration), artist, title,
                   float(features.bpm), features.key, float(features.energy_raw), vec.astype(EMB_DTYPE).tobytes(),
                   _opcional(features.rms), _opcional(features.onset_rate),
                   _opcional(features.percussive_ratio),
                   features.key_acuerdo, features.key_tramos, license, source_url,
                   datetime.now(UTC).isoformat())
        marcas = ", ".join("?" * len(_COLUMNAS))
        self._con.execute(
            f"INSERT OR REPLACE INTO tracks ({', '.join(_COLUMNAS)}) VALUES ({marcas})", valores)
        self._invalidate_norm_stats()
        self._con.commit()

    def delete(self, path: Path | str) -> bool:
        """Saca un track de la caché (para archivos que ya no están). True si había algo."""
        cur = self._con.execute("DELETE FROM tracks WHERE path_key = ?", (self._key(path),))
        self._invalidate_norm_stats()
        self._con.commit()
        return cur.rowcount > 0

    def refresh_norm_stats(self) -> None:
        """Recalcula y guarda media y desvío por dimensión sobre toda la biblioteca.

        Correr después de un scan. No hace falta llamarla a mano: las altas y bajas
        invalidan las stats y cualquier lectura que las necesite las rehace.

        Con pocos tracks son ruidosas: por debajo de ~50 los percentiles de energía y las
        distancias no significan mucho todavía.
        """
        self.matrix()

    # -- lectura ------------------------------------------------------------

    def count(self) -> int:
        """Cuántos tracks hay analizados."""
        return int(self._con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])

    def paths(self) -> list[Path]:
        """Las rutas de todos los tracks guardados, ordenadas. Sin normalizar nada: es lo
        que necesita un scan para saber qué hay en la base y qué ya no está en disco."""
        return [Path(f["path"]) for f in self._rows()]

    def needs_analysis(self, path: Path | str) -> bool:
        """True si el track no está en la caché o el archivo cambió desde que se analizó.

        Si el archivo no existe devuelve True: no hay con qué verificar que la caché siga
        vigente, y afirmar que está al día sería inventar. Para esos casos el scan llama a
        `delete`.
        """
        fila = self._con.execute("SELECT mtime FROM tracks WHERE path_key = ?",
                                 (self._key(path),)).fetchone()
        if fila is None:
            return True
        try:
            actual = Path(path).stat().st_mtime
        except OSError:
            return True
        return float(fila["mtime"]) != float(actual)

    def get_features(self, path: Path | str) -> TrackFeatures | None:
        """Las features CRUDAS tal cual se guardaron: energía absoluta y embedding float32
        sin normalizar. `None` si el track no está analizado.

        Es la vista de la caché (qué se midió); `get` es la vista del motor (dónde queda
        ese track dentro de la biblioteca).
        """
        fila = self._con.execute("SELECT * FROM tracks WHERE path_key = ?",
                                 (self._key(path),)).fetchone()
        return None if fila is None else self._features(fila)

    def get(self, path: Path | str) -> Track | None:
        """Un track puntual con embedding normalizado y energía en percentil.
        `None` si no está analizado.

        El embedding sale por `normalize_one` con las stats guardadas, así que cae
        exactamente en la misma fila que devuelve `matrix()` para esa ruta.
        """
        fila = self._con.execute("SELECT * FROM tracks WHERE path_key = ?",
                                 (self._key(path),)).fetchone()
        if fila is None:
            return None
        media, desvio = self._norm_stats()
        return self._track(fila, normalize_one(self._embedding(fila), media, desvio),
                           self._energias())

    def load_library(self) -> list[Track]:
        """Todos los tracks, con embeddings normalizados y energía en percentil, ordenados
        por ruta.

        Acá `energy_raw` se convierte en percentil: es el único lugar donde hay biblioteca
        contra la cual comparar. La energía absoluta no dice nada; la relativa a tu crate sí.
        """
        filas = self._rows()
        if not filas:
            return []
        norm, _, _, _ = self._normalized(filas)
        energias = [float(f["energy_raw"]) for f in filas]
        return [self._track(f, norm[i], energias) for i, f in enumerate(filas)]

    def matrix(self) -> tuple[np.ndarray, list[Path]]:
        """La matriz de embeddings normalizados de TODA la biblioteca y el orden de las
        rutas: `(matriz (n, DIM), paths)`, con `paths[i]` describiendo `matriz[i]`.

        Es lo que consume la búsqueda de similares: como cada fila tiene norma 1,
        `matriz @ v` es directamente el vector de similitudes coseno contra `v`.

        Si la fila `i` y `paths[i]` se desalinean, el motor recomienda el track equivocado
        sin ningún síntoma: por eso las dos cosas salen de la MISMA lista de filas ya
        ordenada, no de dos consultas distintas.

        Efecto de borde a propósito: guarda las stats con las que se armó la matriz, para
        que `get`/`normalize_one` no puedan quedar en otra escala que ella.

        Biblioteca vacía → matriz `(0, DIM)` y lista vacía: `matriz @ v` sigue andando y
        devuelve 0 similitudes, en vez de explotar.
        """
        filas = self._rows()
        if not filas:
            return np.zeros((0, DIM), dtype=np.float64), []
        norm, media, desvio, paths = self._normalized(filas)
        self._save_norm_stats(media, desvio, len(filas))
        return norm, paths

    # -- internos -----------------------------------------------------------

    @staticmethod
    def _ruta(path: Path | str) -> str:
        r"""La ruta que se GUARDA y se devuelve: absoluta y con separadores normalizados
        (en Windows 'a/b' y 'a\b' son la misma), con la grafía que se recibió — mayúsculas
        y unicode incluidos. Es la que usan `paths()`, `load_library()` y los labels."""
        return os.path.abspath(os.fspath(path))

    @staticmethod
    def _key(path: Path | str) -> str:
        r"""Ruta → clave. Absoluta + `os.path.normcase`: en Windows `C:\Musica\a.wav` y
        `c:\musica\a.wav` son el mismo archivo y tienen que ser la misma fila; antes eran
        dos, con dos análisis del mismo audio. En Linux/macOS `normcase` no toca nada.

        La clave NO se usa para devolver rutas: pasada por `normcase` queda en minúsculas,
        y el label de un track sin título diría "señor coconut" en vez de "Señor Coconut"."""
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    def _preparar_esquema(self) -> None:
        """Crea el esquema en una base nueva o migra una vieja, según `PRAGMA user_version`.

        - Versión fuera de 0..`VERSION_ESQUEMA` → `EsquemaIncompatible`, sin tocar nada: la
          escribió otro código (más nuevo) y leerla con este sería adivinar su forma.
        - Base sin tabla `tracks` → esquema actual, versión actual.
        - Versión menor → los pasos de `_MIGRACIONES` que falten, EN ORDEN y en una sola
          transacción: si uno falla no queda una base a medio migrar.

        Los pasos miran la forma real de la tabla antes de actuar, porque la versión 0 cubre
        bases de más de un commit (con y sin NOT NULL, con y sin path_key).
        """
        version = self._version_compatible()
        existe = self._existe_tracks()
        if existe and version == VERSION_ESQUEMA:
            return

        # `BEGIN IMMEDIATE` y no `BEGIN` (hallazgo H2, tarea 1.2). Con `BEGIN` a secas la
        # transacción arranca leyendo (lock compartido) y recién pide el de escritura en el
        # primer ALTER. Si otro proceso está migrando la misma base y ya pidió el lock para
        # confirmar, SQLite ve el abrazo mutuo y le devuelve "database is locked" AL INSTANTE
        # a uno de los dos, sin pasar por el `timeout` — medido: 4 procesos abriendo una base
        # vieja a la vez, de 1 a 3 morían con OperationalError. Tomando el lock de escritura
        # de entrada, el segundo proceso ESPERA (hasta `ESPERA_BLOQUEO_S`) en vez de morir.
        self._con.execute("BEGIN IMMEDIATE")
        try:
            # Releer con el lock tomado: mientras este proceso esperaba, otro pudo haber
            # migrado la base. Sin esto se migraría dos veces (o se rechazaría una versión
            # que ya no es la que se leyó arriba).
            version = self._version_compatible()
            existe = self._existe_tracks()
            if existe and version == VERSION_ESQUEMA:
                self._con.execute("COMMIT")
                return
            if existe:
                for destino, paso in _MIGRACIONES:
                    if version < destino:
                        paso(self)
            else:
                self._con.execute(_DDL_TRACKS)
            for ddl in _DDL_INDICES:
                self._con.execute(ddl)
            self._con.execute(_DDL_NORM_STATS)
            self._con.execute(f"PRAGMA user_version = {VERSION_ESQUEMA}")
            self._con.execute("COMMIT")
        except BaseException:
            self._con.execute("ROLLBACK")
            raise

    def _version_compatible(self) -> int:
        """`PRAGMA user_version`, o `EsquemaIncompatible` si este código no la sabe leer."""
        version = int(self._con.execute("PRAGMA user_version").fetchone()[0])
        if not 0 <= version <= VERSION_ESQUEMA:
            raise EsquemaIncompatible(
                f"La base {self.db_path} tiene esquema versión {version} y este código entiende "
                f"hasta la {VERSION_ESQUEMA}: la escribió otra versión de djradio. "
                f"No se tocó la base; actualizá el código antes de usarla.")
        return version

    def _existe_tracks(self) -> bool:
        return self._con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tracks'"
        ).fetchone() is not None

    def _migrar_a_1_nulos(self) -> None:
        """rms / onset_rate / percussive_ratio pasan a aceptar NULL.

        SQLite no tiene `ALTER COLUMN`: se recrea la tabla con el DDL actual y se copian las
        filas, con las columnas que existan en la vieja. Los valores se copian tal cual: un
        0.0 que la base vieja haya guardado no se convierte en NULL, porque desde acá no hay
        forma de saber si fue medido o inventado."""
        info = list(self._con.execute("PRAGMA table_info(tracks)"))
        estrictas = {f["name"] for f in info if f["notnull"]}
        if not estrictas & {"rms", "onset_rate", "percussive_ratio"}:
            return
        viejas = {f["name"] for f in info}
        comunes = ", ".join(c for c in _COLUMNAS if c in viejas)
        self._con.execute("ALTER TABLE tracks RENAME TO tracks_v0")
        self._con.execute(_DDL_TRACKS)
        self._con.execute(f"INSERT INTO tracks ({comunes}) SELECT {comunes} FROM tracks_v0")
        self._con.execute("DROP TABLE tracks_v0")   # se lleva sus índices; se recrean después

    def _migrar_a_2_path_key(self) -> None:
        r"""Agrega `path_key` (si falta) y la llena.

        Si dos filas viejas caen en la misma clave (`C:\a.wav` y `c:\a.wav`), queda la del
        análisis más reciente y las otras se borran: son dos análisis del mismo archivo, y
        dejar los dos sería tener el track repetido en la biblioteca. El índice único lo crea
        `_preparar_esquema` al final."""
        columnas = {f["name"] for f in self._con.execute("PRAGMA table_info(tracks)")}
        if "path_key" not in columnas:
            self._con.execute("ALTER TABLE tracks ADD COLUMN path_key TEXT")
        filas = self._con.execute(
            "SELECT path, analyzed_at FROM tracks WHERE path_key IS NULL").fetchall()
        por_clave: dict[str, list[str]] = {}
        for f in sorted(filas, key=lambda f: (f["analyzed_at"], f["path"])):
            por_clave.setdefault(self._key(f["path"]), []).append(f["path"])
        for clave, rutas in sorted(por_clave.items()):
            *viejas, vigente = rutas
            for ruta in viejas:
                self._con.execute("DELETE FROM tracks WHERE path = ?", (ruta,))
            self._con.execute("UPDATE tracks SET path_key = ? WHERE path = ?", (clave, vigente))
        if filas:
            self._con.execute("DROP TABLE IF EXISTS norm_stats")   # se recrea vacía después

    def _migrar_a_3_acuerdo_key(self) -> None:
        """Agrega `key_acuerdo` y `key_tramos` (si faltan). Las filas viejas quedan en NULL.

        NULL y NO un acuerdo inventado: esas filas se analizaron con `con_acuerdo=False`, o
        sea que `tono_consenso` nunca corrió sobre ese audio. Desde la base no hay con qué
        llenarlas — rellenarlas con "3/3" diría que la key es confiable sin que nadie la
        haya medido, que es exactamente el dato que miente del §6. `list`/`info` las
        muestran con `?` hasta que el archivo se vuelva a analizar.

        Se mira la forma real de la tabla y no la versión, porque la migración a 1 recrea
        `tracks` con el DDL ACTUAL: viniendo de una base 9440251 las columnas ya existen
        cuando este paso corre, y un `ALTER TABLE ADD COLUMN` repetido falla.
        """
        columnas = {f["name"] for f in self._con.execute("PRAGMA table_info(tracks)")}
        for col in ("key_acuerdo", "key_tramos"):
            if col not in columnas:
                self._con.execute(f"ALTER TABLE tracks ADD COLUMN {col} TEXT")

    def _rows(self) -> list[sqlite3.Row]:
        """Todas las filas, ORDENADAS POR CLAVE EN PYTHON — no por la collation de SQLite,
        que depende de cómo se compiló. Determinismo: mismo contenido, mismo orden. Se
        ordena por la clave y no por la ruta guardada para que el orden no dependa de con
        qué mayúsculas se escribió cada ruta."""
        filas = self._con.execute("SELECT * FROM tracks").fetchall()
        return sorted(filas, key=lambda f: (f["path_key"], f["path"]))

    @staticmethod
    def _embedding(fila: sqlite3.Row) -> np.ndarray:
        """BLOB → vector. Se lee con el MISMO dtype con que se escribió (`EMB_DTYPE`)."""
        vec = np.frombuffer(fila["embedding"], dtype=EMB_DTYPE)
        if vec.size != DIM:
            raise ValueError(
                f"'{fila['path']}' tiene un embedding de {vec.size} dimensiones y DIM={DIM}: "
                f"la caché se armó con otra versión del embedding, hay que reanalizar"
            )
        return vec.copy()  # `frombuffer` devuelve una vista de solo lectura sobre el BLOB

    def _features(self, fila: sqlite3.Row) -> TrackFeatures:
        return TrackFeatures(
            bpm=float(fila["bpm"]), key=fila["key"], energy_raw=float(fila["energy_raw"]),
            embedding=self._embedding(fila), rms=_opcional(fila["rms"]),
            onset_rate=_opcional(fila["onset_rate"]),
            percussive_ratio=_opcional(fila["percussive_ratio"]),
            key_acuerdo=fila["key_acuerdo"], key_tramos=fila["key_tramos"])

    @staticmethod
    def _track(fila: sqlite3.Row, embedding: np.ndarray, energias: Iterable[float]) -> Track:
        # `energia.percentil` devuelve 0..100; `Track.energy` es 0..1 (ver su docstring).
        pct = percentil(float(fila["energy_raw"]), energias) / 100.0
        return Track(path=Path(fila["path"]), duration=float(fila["duration"]),
                     bpm=float(fila["bpm"]), key=fila["key"], energy=pct, embedding=embedding,
                     license=fila["license"], source_url=fila["source_url"],
                     artist=fila["artist"], title=fila["title"],
                     key_acuerdo=fila["key_acuerdo"])

    def _energias(self) -> list[float]:
        """Las energías crudas de toda la biblioteca: el universo contra el que se percentila."""
        return [float(f[0]) for f in self._con.execute("SELECT energy_raw FROM tracks")]

    def _normalized(self, filas: list[sqlite3.Row]):
        """(matriz normalizada, media, desvío, paths) a partir de filas YA ordenadas."""
        cruda = np.vstack([self._embedding(f) for f in filas]).astype(np.float64)
        norm, media, desvio = normalize_matrix(cruda)
        return norm, media, desvio, [Path(f["path"]) for f in filas]

    def _save_norm_stats(self, media: np.ndarray, desvio: np.ndarray, n: int) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO norm_stats (id, mean, std, count) VALUES (1, ?, ?, ?)",
            (media.astype(STATS_DTYPE).tobytes(), desvio.astype(STATS_DTYPE).tobytes(), n))
        self._con.commit()

    def _invalidate_norm_stats(self) -> None:
        """Un alta o una baja mueve la media y el desvío de la biblioteca entera, así que
        las stats guardadas dejan de valer. Se borran en vez de recalcularse: recalcular en
        cada `upsert` sería O(n) por track durante un scan."""
        self._con.execute("DELETE FROM norm_stats")

    def _norm_stats(self) -> tuple[np.ndarray, np.ndarray]:
        """Media y desvío vigentes, recalculándolos si un alta o una baja los invalidó.

        Dos señales de que las stats guardadas no valen: que no haya fila (las borró
        `_invalidate_norm_stats`) o que `count` no coincida con los tracks que hay. La
        segunda es un cinturón: cubre una escritura que no pasó por `upsert`/`delete` (otra
        versión del código, un INSERT a mano). No reemplaza a la invalidación — un reanálisis
        de una ruta que ya estaba cambia las stats sin cambiar la cantidad.
        """
        consulta = "SELECT mean, std, count FROM norm_stats WHERE id = 1"
        fila = self._con.execute(consulta).fetchone()
        if fila is None or int(fila["count"]) != self.count():
            self.matrix()  # las recalcula y las guarda
            fila = self._con.execute(consulta).fetchone()
        return (np.frombuffer(fila["mean"], dtype=STATS_DTYPE).copy(),
                np.frombuffer(fila["std"], dtype=STATS_DTYPE).copy())


# (versión a la que lleva, paso), en orden. Ver `VERSION_ESQUEMA`.
_MIGRACIONES = (
    (1, Store._migrar_a_1_nulos),
    (2, Store._migrar_a_2_path_key),
    (3, Store._migrar_a_3_acuerdo_key),
)
