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

Migraciones (ver `VERSION_ESQUEMA`): son de ida. Una base que abrió este código no la lee un
djradio más viejo. Volver de la v5 (marcas del dueño, f48) a la v4 es a mano y borra las
marcas: `DROP TABLE cue_marks; PRAGMA user_version = 4;` sobre una copia de la base.

Determinismo (spec §5): el orden de todo lo que devuelve el store lo fija Python ordenando
por la clave de la ruta (absoluta + `normcase`), no la collation de SQLite ni el orden de
inserción.
"""
import contextlib
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
#   4  tablas saved_sets / saved_set_steps / saved_set_ratings (sets guardados, tarea 16)
#   5  tabla cue_marks (hot cues, memory cues y loops del dueño, f48)
#
# OJO, la migración es de ida: una base que abrió este código queda en la versión nueva y un
# djradio más viejo la rechaza con `EsquemaIncompatible` (sin tocarla). Para volver a usar
# una base v5 con un código v4 hay que bajarla a mano, y eso BORRA las marcas del dueño
# (copiá la base antes):
#     sqlite3 biblioteca.sqlite "DROP TABLE cue_marks; PRAGMA user_version = 4;"
# Ninguna otra tabla cambia de la 4 a la 5, así que el resto queda como estaba.
VERSION_ESQUEMA = 5

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

# Sets guardados (tarea 16, `motor/saved_sets.py`). Tres tablas y no una columna JSON con todo
# adentro: las calificaciones se cambian y se borran de a una, y el CSV de la tarea 14 las
# cruza con los pasos.
#
# `saved_set_steps` es la FOTO de lo que se escuchó: una fila por posición con los datos tal
# como se mostraron (ver `saved_sets.StepSnapshot`). NO referencia a `tracks`: un re-escaneo
# que cambia un BPM, o un archivo que se borra de la biblioteca, no pueden cambiar ni llevarse
# lo que se calificó. `path_key` está para poder DECIR que el archivo ya no está, no para
# leer sus datos de hoy.
#
# `AUTOINCREMENT` en `saved_sets`: sin él SQLite reusa el id del último set borrado, y un CSV
# ya exportado con "set 7" pasaría a hablar de otro set.
_DDL_SETS = (
    """CREATE TABLE IF NOT EXISTS saved_sets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,          -- ISO 8601 UTC
    name        TEXT,                   -- NULL = sin nombre
    config      TEXT NOT NULL,          -- el RadioConfig usado, en JSON (saved_sets.config_json)
    requested   INTEGER NOT NULL,       -- largo pedido
    stop        TEXT,                   -- RadioSet.stop de ese armado; NULL = llegó al largo
    stop_detail TEXT NOT NULL,
    fragments   INTEGER NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS saved_set_steps (
    set_id        INTEGER NOT NULL REFERENCES saved_sets(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL CHECK (position >= 1),
    path          TEXT NOT NULL,
    path_key      TEXT NOT NULL,
    label         TEXT NOT NULL,
    artist        TEXT,
    title         TEXT,
    title_shown   TEXT NOT NULL,
    duration      REAL NOT NULL,
    is_track      INTEGER NOT NULL,
    license       TEXT NOT NULL,        -- obligatorio (spec §5)
    source_url    TEXT NOT NULL,        -- obligatorio (spec §5)
    bpm           REAL NOT NULL,
    bpm_shown     TEXT NOT NULL,        -- "128.4", como se mostró
    key           TEXT NOT NULL,
    key_classic   TEXT,
    key_acuerdo   TEXT,
    key_doubtful  INTEGER NOT NULL,     -- el `?` que se mostró
    energy        REAL NOT NULL,        -- percentil 0..1 dentro de la biblioteca de ESE momento
    energy_pct    INTEGER NOT NULL,
    reason        TEXT NOT NULL,        -- Transition.reason() tal cual
    is_seed       INTEGER NOT NULL,
    from_bpm      REAL,
    bpm_delta_pct REAL,
    bpm_octave    TEXT NOT NULL,
    from_key      TEXT,
    key_relation  TEXT NOT NULL,
    key_compat    REAL,
    mixability    REAL,
    musical_fit   REAL,
    score         REAL,
    energy_goal   REAL,
    PRIMARY KEY (set_id, position)
)""",
    # `transition` = n, de la posición n a la n + 1. El CHECK repite la validación de
    # `saved_sets.require_rating` en la base: una fila escrita por fuera tampoco puede
    # inventar un nivel ni una `mala` sin motivo.
    """CREATE TABLE IF NOT EXISTS saved_set_ratings (
    set_id      INTEGER NOT NULL REFERENCES saved_sets(id) ON DELETE CASCADE,
    transition  INTEGER NOT NULL CHECK (transition >= 1),
    rating      TEXT NOT NULL CHECK (rating IN ('ok', 'regular', 'mala')),
    reason      TEXT,
    rated_at    TEXT NOT NULL,          -- ISO 8601 UTC, la última vez que se cambió
    PRIMARY KEY (set_id, transition),
    CHECK (rating <> 'mala' OR (reason IS NOT NULL AND trim(reason) <> ''))
)""",
)

# Marcas del dueño (f48, `motor/cue_marks.py`). Como `saved_set_steps`, NO referencia a
# `tracks`: el scan hace INSERT OR REPLACE de la fila del track al reanalizarlo y la BORRA si
# hoy no está en disco, y ninguna de las dos cosas puede llevarse lo que el dueño marcó a mano.
# La clave es `path_key` (la del store): sin huella del contenido, es lo único estable.
#
# Los CHECK repiten en la base las reglas de `cue_marks.py` que no dependen del track (la
# duración sí, y esa se valida al escribir): una fila escrita por fuera tampoco puede tener un
# hot cue sin número, un loop sin salida o una salida antes de la entrada.
# `AUTOINCREMENT`: sin él SQLite reusa el id de la última marca borrada, y una pantalla vieja
# que manda "borrá la 7" borraría otra.
_DDL_CUES = (
    """CREATE TABLE IF NOT EXISTS cue_marks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path_key    TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('cue', 'memory', 'loop')),
    num         INTEGER CHECK (num IS NULL OR (num >= 0 AND num <= 7)),
    start_ms    INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms      INTEGER,
    name        TEXT,
    created_at  TEXT NOT NULL,          -- ISO 8601 UTC
    updated_at  TEXT NOT NULL,
    CHECK ((kind = 'cue') = (num IS NOT NULL)),
    CHECK ((kind = 'loop') = (end_ms IS NOT NULL)),
    CHECK (end_ms IS NULL OR end_ms > start_ms)
)""",
    "CREATE INDEX IF NOT EXISTS idx_cue_marks_path ON cue_marks(path_key)",
    # Un solo hot cue por número en cada track (el pad 3 es UNO).
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cue_marks_hot ON cue_marks(path_key, num) "
    "WHERE num IS NOT NULL",
)


def _ahora() -> str:
    """ISO 8601 UTC con `T` y `Z`, sin microsegundos: así lo ve el CSV de `sets exportar`, y
    Excel no lo convierte en una fecha suya (que perdería los segundos)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        # Las FK de los sets guardados (ON DELETE CASCADE) solo se aplican con esto, y es por
        # conexión: sin él, borrar un set dejaría sus pasos y calificaciones huérfanos.
        self._con.execute("PRAGMA foreign_keys = ON")
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
                self._migrar_a_4_sets()
                self._migrar_a_5_cues()
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

    def _migrar_a_4_sets(self) -> None:
        """Crea las tablas de sets guardados (tarea 16). No toca `tracks`: una biblioteca
        v3 sigue igual y arranca sin sets. `IF NOT EXISTS` porque también la usa una base
        nueva, y porque una base que otro código ya llevó a 4 no puede fallar acá."""
        for ddl in _DDL_SETS:
            self._con.execute(ddl)

    def _migrar_a_5_cues(self) -> None:
        """Crea la tabla de marcas del dueño (f48). No toca `tracks` ni los sets: una base v4
        sigue igual y arranca sin marcas. `IF NOT EXISTS` por lo mismo que el paso 4."""
        for ddl in _DDL_CUES:
            self._con.execute(ddl)

    # -- sets guardados (tarea 16, motor/saved_sets.py) -----------------------

    @contextlib.contextmanager
    def _escritura(self):
        """Una escritura de varias sentencias, entera o nada. `BEGIN IMMEDIATE` por lo mismo
        que en `_preparar_esquema`: tomar el lock de escritura de entrada hace que otro
        proceso escribiendo a la vez ESPERE en vez de morir con "database is locked"."""
        self._con.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._con.execute("COMMIT")
        except BaseException:
            self._con.execute("ROLLBACK")
            raise

    def save_set(self, steps, *, config: str, requested: int, stop: str | None,
                 stop_detail: str, fragments: int, name: str | None = None) -> int:
        """Guarda la foto de un set (`saved_sets.snapshot_steps`) y devuelve su id.

        Todo en una transacción: un set a medio guardar (la cabecera sin sus pasos) sería
        un set que "se escuchó" sin tracks. Se valida ANTES de escribir, como `upsert`: las
        posiciones son 1..n sin huecos, cada track trae licencia y origen (§5) y un BPM
        finito, y solo la posición 1 es la semilla.
        """
        from motor.saved_sets import STEP_FIELDS, InvalidSavedSet, clean_name

        steps = list(steps)
        if not steps:
            raise InvalidSavedSet("un set guardado tiene al menos un track (la semilla)")
        posiciones = [s.position for s in steps]
        if posiciones != list(range(1, len(steps) + 1)):
            raise InvalidSavedSet(f"las posiciones tienen que ser 1..{len(steps)} en orden, "
                                  f"recibí {posiciones}")
        for s in steps:
            require_text(s.license, "license")
            require_text(s.source_url, "source_url")
            require_finite_bpm(s.bpm)
            if s.is_seed != (s.position == 1):
                raise InvalidSavedSet(f"la posición {s.position} dice is_seed={s.is_seed}: "
                                      f"la semilla es la posición 1 y solo ella")
        nombre = clean_name(name)
        if not isinstance(stop_detail, str):
            raise InvalidSavedSet(f"stop_detail es un texto, recibí {stop_detail!r}")

        marcas = ", ".join("?" * (len(STEP_FIELDS) + 1))
        with self._escritura():
            cur = self._con.execute(
                "INSERT INTO saved_sets (created_at, name, config, requested, stop, "
                "stop_detail, fragments) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_ahora(), nombre, config, int(requested), stop, stop_detail, int(fragments)))
            set_id = int(cur.lastrowid)
            self._con.executemany(
                f"INSERT INTO saved_set_steps (set_id, {', '.join(STEP_FIELDS)}) "
                f"VALUES ({marcas})",
                [(set_id, *(getattr(s, f) for f in STEP_FIELDS)) for s in steps])
        return set_id

    def list_saved_sets(self) -> list:
        """Todos los sets guardados, del más nuevo al más viejo (por id: la fecha puede
        repetirse dentro del mismo segundo), con el resumen de calificaciones."""
        from motor.saved_sets import SavedSetInfo, summarize

        filas = self._con.execute("""
            SELECT s.id, s.created_at, s.name, s.config, s.requested,
                   (SELECT label FROM saved_set_steps p
                     WHERE p.set_id = s.id AND p.position = 1) AS seed_label,
                   (SELECT COUNT(*) FROM saved_set_steps p WHERE p.set_id = s.id) AS tracks,
                   (SELECT COUNT(*) FROM saved_set_steps p WHERE p.set_id = s.id
                      AND NOT EXISTS (SELECT 1 FROM tracks t
                                       WHERE t.path_key = p.path_key)) AS missing
              FROM saved_sets s ORDER BY s.id DESC""").fetchall()
        calif: dict[int, list[str]] = {}
        for f in self._con.execute("SELECT set_id, rating FROM saved_set_ratings"):
            calif.setdefault(int(f["set_id"]), []).append(f["rating"])
        return [SavedSetInfo(
            id=int(f["id"]), created_at=f["created_at"], name=f["name"],
            seed_label=f["seed_label"], tracks=int(f["tracks"]), requested=int(f["requested"]),
            curve=_curva(f["config"]),
            summary=summarize(max(int(f["tracks"]) - 1, 0), calif.get(int(f["id"]), [])),
            missing=int(f["missing"])) for f in filas]

    def get_saved_set(self, set_id: int):
        """El set guardado entero: su foto (tal cual se guardó, sin recalcular nada), si
        cada archivo sigue en la biblioteca y sus calificaciones. `SavedSetNotFound` si no
        existe."""
        import json

        from motor.saved_sets import (
            STEP_FIELDS,
            Rating,
            SavedSet,
            SavedStep,
            StepSnapshot,
        )

        cab = self._cabecera_set(set_id)
        pasos = self._con.execute(f"""
            SELECT {', '.join('p.' + c for c in STEP_FIELDS)},
                   EXISTS (SELECT 1 FROM tracks t WHERE t.path_key = p.path_key) AS in_library
              FROM saved_set_steps p WHERE p.set_id = ? ORDER BY p.position""",
                                  (int(set_id),)).fetchall()
        _bools = ("is_track", "key_doubtful", "is_seed")
        steps = tuple(SavedStep(
            StepSnapshot(**{c: (bool(f[c]) if c in _bools else f[c]) for c in STEP_FIELDS}),
            bool(f["in_library"])) for f in pasos)
        ratings = tuple(Rating(int(f["transition"]), f["rating"], f["reason"], f["rated_at"])
                        for f in self._con.execute(
                            "SELECT transition, rating, reason, rated_at FROM saved_set_ratings "
                            "WHERE set_id = ? ORDER BY transition", (int(set_id),)))
        return SavedSet(id=int(cab["id"]), created_at=cab["created_at"], name=cab["name"],
                        config=json.loads(cab["config"]), requested=int(cab["requested"]),
                        stop=cab["stop"], stop_detail=cab["stop_detail"],
                        fragments=int(cab["fragments"]), steps=steps, ratings=ratings)

    def rate_transition(self, set_id: int, transition: int, rating: str,
                        reason: str | None = None):
        """Califica la transición `transition` (de la posición n a la n + 1) del set. Si ya
        tenía calificación, la reemplaza (nivel, motivo y fecha). Valida el nivel, el motivo
        (obligatorio en `mala`) y que la transición exista en ESE set."""
        from motor.saved_sets import Rating, require_rating

        texto = require_rating(rating, reason)
        cuando = _ahora()
        # El set y el rango se validan ADENTRO de la transacción: validados afuera, otro
        # proceso podía borrar el set en el medio y el INSERT fallaba por la FK, que la API
        # reportaba como "base ilegible" en vez de "no existe".
        with self._escritura():
            n = self._transicion_valida(set_id, transition)
            self._con.execute(
                "INSERT INTO saved_set_ratings (set_id, transition, rating, reason, rated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (set_id, transition) DO UPDATE SET "
                "rating = excluded.rating, reason = excluded.reason, "
                "rated_at = excluded.rated_at",
                (int(set_id), n, rating, texto, cuando))
        return Rating(n, rating, texto, cuando)

    def delete_rating(self, set_id: int, transition: int) -> bool:
        """Deja la transición sin calificar. True si tenía calificación."""
        with self._escritura():
            n = self._transicion_valida(set_id, transition)
            cur = self._con.execute(
                "DELETE FROM saved_set_ratings WHERE set_id = ? AND transition = ?",
                (int(set_id), n))
        return cur.rowcount > 0

    def rename_saved_set(self, set_id: int, name: str | None) -> str | None:
        """Cambia el nombre (vacío o None = sin nombre). Devuelve el nombre que quedó."""
        from motor.saved_sets import clean_name

        nombre = clean_name(name)
        with self._escritura():
            self._cabecera_set(set_id)
            self._con.execute("UPDATE saved_sets SET name = ? WHERE id = ?",
                              (nombre, int(set_id)))
        return nombre

    def delete_saved_set(self, set_id: int) -> None:
        """Borra el set con su foto y sus calificaciones. `SavedSetNotFound` si no existe.
        Los hijos se borran explícitamente además del CASCADE: una conexión abierta por
        otro código sin `PRAGMA foreign_keys` no puede dejar huérfanos por esta vía."""
        with self._escritura():
            self._cabecera_set(set_id)
            for tabla in ("saved_set_ratings", "saved_set_steps"):
                self._con.execute(f"DELETE FROM {tabla} WHERE set_id = ?", (int(set_id),))
            self._con.execute("DELETE FROM saved_sets WHERE id = ?", (int(set_id),))

    def _cabecera_set(self, set_id) -> sqlite3.Row:
        from motor.saved_sets import SavedSetNotFound

        # Fuera del rango de INTEGER de SQLite (64 bits con signo) no puede haber un set, y
        # pasarle ese número a SQLite es `OverflowError`, que ninguna capa de arriba espera
        # (un id de 26 dígitos en la URL daba 500). Se contesta lo que es: no existe.
        if isinstance(set_id, bool) or not isinstance(set_id, int) \
                or not -(2 ** 63) <= set_id < 2 ** 63:
            raise SavedSetNotFound(f"no hay un set guardado con id {set_id!r}")
        fila = self._con.execute("SELECT * FROM saved_sets WHERE id = ?", (set_id,)).fetchone()
        if fila is None:
            raise SavedSetNotFound(f"no hay un set guardado con id {set_id}")
        return fila

    def _transicion_valida(self, set_id, transition) -> int:
        """La transición como int, si el set existe y la tiene (1..largo-1)."""
        from motor.saved_sets import InvalidSavedSet

        self._cabecera_set(set_id)
        largo = int(self._con.execute("SELECT COUNT(*) FROM saved_set_steps WHERE set_id = ?",
                                      (set_id,)).fetchone()[0])
        if isinstance(transition, bool) or not isinstance(transition, int) \
                or not 1 <= transition <= largo - 1:
            rango = (f"de 1 a {largo - 1}" if largo > 1
                     else "ninguna: el set tiene un solo track")
            raise InvalidSavedSet(f"el set {set_id} tiene {largo} tracks, así que sus "
                                  f"transiciones van {rango}; recibí {transition!r}")
        return transition

    # -- marcas del dueño: hot cues, memory cues y loops (f48, motor/cue_marks.py) ----------
    #
    # Todo se valida ADENTRO de la transacción de escritura (como `rate_transition`): el track,
    # su duración, el número libre y los topes se leen con el lock tomado, así dos pedidos a la
    # vez no pueden dejar dos hot cues 3 ni pasar el tope entre los dos.

    def list_cue_marks(self, path: Path | str) -> list:
        """Las marcas de un track, en el orden de la tabla de la pantalla: hot cues por número,
        después memory cues y loops por tiempo. Lista vacía si no tiene (esté o no en la
        biblioteca: las marcas de un archivo que hoy no está siguen siendo suyas)."""
        filas = self._con.execute("SELECT * FROM cue_marks WHERE path_key = ?",
                                  (self._key(path),)).fetchall()
        return _ordenar_marcas([_marca(f) for f in filas])

    def cue_mark_counts(self, paths: Iterable[Path | str]) -> dict[str, int]:
        """Cuántas marcas tiene cada track, por clave del store (`_key`). Los que no tienen
        no aparecen."""
        claves = sorted({self._key(p) for p in paths})
        conteos: dict[str, int] = {}
        # De a tandas: SQLite tiene un tope de parámetros por sentencia.
        for i in range(0, len(claves), 500):
            tanda = claves[i:i + 500]
            marcas = ", ".join("?" * len(tanda))
            for f in self._con.execute(
                    f"SELECT path_key, COUNT(*) AS n FROM cue_marks WHERE path_key IN ({marcas}) "
                    f"GROUP BY path_key", tanda):
                conteos[f["path_key"]] = int(f["n"])
        return conteos

    def orphan_cue_marks(self) -> list[tuple[str, int]]:
        """Marcas cuyo archivo ya no está en la biblioteca: `[(path_key, cantidad)]`, ordenado.

        Pasa cuando el archivo se movió o se renombró y se re-escaneó (la marca se ata a la
        ruta, ver `motor/cue_marks.py`), o cuando hoy no está en disco. NO se borran: se
        cuentan para que la pantalla lo diga."""
        filas = self._con.execute("""
            SELECT m.path_key, COUNT(*) AS n FROM cue_marks m
             WHERE NOT EXISTS (SELECT 1 FROM tracks t WHERE t.path_key = m.path_key)
             GROUP BY m.path_key ORDER BY m.path_key""").fetchall()
        return [(f["path_key"], int(f["n"])) for f in filas]

    def add_cue_mark(self, path: Path | str, kind: str, start_s: float,
                     end_s: float | None = None, *, num: int | None = None,
                     name: str | None = None):
        """Crea una marca en un track de la biblioteca y la devuelve.

        - `cue` (hot cue): `num` 0..7; sin `num` toma el primer pad libre. Ocupado o sin pads
          libres → `InvalidCueMark` (no se pisa un hot cue en silencio).
        - `memory`: sin número, hasta `MAX_MEMORY` por track.
        - `loop`: `end_s` obligatorio y después de `start_s`, hasta `MAX_LOOPS`.

        Track que no está en la biblioteca → `CueMarkNotFound`: sin su duración no hay con qué
        validar que la marca caiga adentro."""
        from motor.cue_marks import (
            KIND_CUE,
            KIND_LOOP,
            InvalidCueMark,
            clean_mark_name,
            require_kind,
            require_num,
            seconds_to_ms,
            validate_times,
        )

        kind = require_kind(kind)
        start_ms = seconds_to_ms(start_s, "inicio")
        end_ms = None if end_s is None else seconds_to_ms(end_s, "fin")
        nombre = clean_mark_name(name)
        if kind != KIND_CUE and num is not None:
            raise InvalidCueMark("solo un hot cue lleva número")
        if num is not None:
            num = require_num(num)
        clave = self._key(path)
        cuando = _ahora()
        with self._escritura():
            validate_times(kind, start_ms, end_ms, self._duracion_marcable(clave))
            if kind == KIND_CUE:
                num = self._num_hot_cue(clave, num)
            else:
                self._dentro_del_tope(clave, kind)
            cur = self._con.execute(
                "INSERT INTO cue_marks (path_key, kind, num, start_ms, end_ms, name, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (clave, kind, num, start_ms, end_ms if kind == KIND_LOOP else None, nombre,
                 cuando, cuando))
            mark_id = int(cur.lastrowid)
        return self._marca_por_id(clave, mark_id)

    _SIN_CAMBIO = object()

    def update_cue_mark(self, path: Path | str, mark_id: int, *, start_s=_SIN_CAMBIO,
                        end_s=_SIN_CAMBIO, name=_SIN_CAMBIO, num=_SIN_CAMBIO):
        """Mueve, renombra o cambia de pad una marca del track. Lo que no se pasa queda como
        está. El tipo no cambia (un hot cue no se vuelve loop: se borra uno y se crea el otro).
        La marca resultante se valida entera, como si fuera nueva."""
        from motor.cue_marks import (
            KIND_CUE,
            KIND_LOOP,
            InvalidCueMark,
            clean_mark_name,
            require_num,
            seconds_to_ms,
            validate_times,
        )

        cambios: dict[str, object] = {}
        if start_s is not self._SIN_CAMBIO:
            cambios["start_ms"] = seconds_to_ms(start_s, "inicio")
        if end_s is not self._SIN_CAMBIO:
            cambios["end_ms"] = None if end_s is None else seconds_to_ms(end_s, "fin")
        if name is not self._SIN_CAMBIO:
            cambios["name"] = clean_mark_name(name)
        if num is not self._SIN_CAMBIO:
            cambios["num"] = None if num is None else require_num(num)
        if not cambios:
            raise InvalidCueMark("no hay nada que cambiar: mandá `inicio`, `fin`, `nombre` o `num`")
        clave = self._key(path)
        with self._escritura():
            actual = self._marca_por_id(clave, mark_id)
            kind = actual.kind
            if "num" in cambios and kind != KIND_CUE:
                raise InvalidCueMark("solo un hot cue lleva número")
            if "num" in cambios and cambios["num"] is None:
                raise InvalidCueMark("un hot cue siempre tiene número (0 a 7)")
            if "end_ms" in cambios and kind != KIND_LOOP:
                raise InvalidCueMark("solo un loop tiene salida (`fin`)")
            start_ms = cambios.get("start_ms", actual.start_ms)
            end_ms = cambios.get("end_ms", actual.end_ms)
            validate_times(kind, start_ms, end_ms, self._duracion_marcable(clave))
            if "num" in cambios and cambios["num"] != actual.num:
                self._num_hot_cue(clave, cambios["num"])     # ocupado → InvalidCueMark
            asignaciones = ", ".join(f"{c} = ?" for c in cambios)
            self._con.execute(
                f"UPDATE cue_marks SET {asignaciones}, updated_at = ? WHERE id = ?",
                (*cambios.values(), _ahora(), actual.id))
        return self._marca_por_id(clave, actual.id)

    def delete_cue_mark(self, path: Path | str, mark_id: int) -> None:
        """Borra UNA marca del track. `CueMarkNotFound` si ese track no la tiene. Funciona
        también si el track ya no está en la biblioteca: borrar una marca huérfana es una
        decisión del dueño, no algo que dependa de dónde quedó el archivo."""
        clave = self._key(path)
        with self._escritura():
            marca = self._marca_por_id(clave, mark_id)
            self._con.execute("DELETE FROM cue_marks WHERE id = ?", (marca.id,))

    def _marca_por_id(self, clave: str, mark_id) -> object:
        """La marca `mark_id` de ESE track. Un id de otro track, inexistente, que no es un
        entero o fuera del rango de SQLite es lo mismo: no existe (`CueMarkNotFound`)."""
        from motor.cue_marks import CueMarkNotFound

        if isinstance(mark_id, bool) or not isinstance(mark_id, int) \
                or not -(2 ** 63) <= mark_id < 2 ** 63:
            raise CueMarkNotFound(f"no hay una marca con id {mark_id!r}")
        fila = self._con.execute("SELECT * FROM cue_marks WHERE id = ? AND path_key = ?",
                                 (mark_id, clave)).fetchone()
        if fila is None:
            raise CueMarkNotFound(f"este track no tiene una marca con id {mark_id}")
        return _marca(fila)

    def _duracion_marcable(self, clave: str) -> float:
        from motor.cue_marks import CueMarkNotFound

        fila = self._con.execute("SELECT duration FROM tracks WHERE path_key = ?",
                                 (clave,)).fetchone()
        if fila is None:
            raise CueMarkNotFound("el track no está en la biblioteca del motor: sin su duración "
                                  "no se puede validar una marca")
        return float(fila["duration"])

    def _num_hot_cue(self, clave: str, num: int | None) -> int:
        """`num` si está libre, o el primer pad libre si `num` es None."""
        from motor.cue_marks import HOT_CUES, InvalidCueMark

        usados = {int(f[0]) for f in self._con.execute(
            "SELECT num FROM cue_marks WHERE path_key = ? AND num IS NOT NULL", (clave,))}
        if num is None:
            libres = [n for n in range(HOT_CUES) if n not in usados]
            if not libres:
                raise InvalidCueMark(f"el track ya tiene los {HOT_CUES} hot cues: borrá uno "
                                     f"o movelo")
            return libres[0]
        if num in usados:
            raise InvalidCueMark(f"el hot cue {num + 1} ya está puesto en este track: borralo o "
                                 f"movelo antes de poner otro en el mismo pad")
        return num

    def _dentro_del_tope(self, clave: str, kind: str) -> None:
        from motor.cue_marks import KIND_LOOP, MAX_LOOPS, MAX_MEMORY, InvalidCueMark

        tope = MAX_LOOPS if kind == KIND_LOOP else MAX_MEMORY
        n = int(self._con.execute("SELECT COUNT(*) FROM cue_marks WHERE path_key = ? AND kind = ?",
                                  (clave, kind)).fetchone()[0])
        if n >= tope:
            que = "loops" if kind == KIND_LOOP else "memory cues"
            raise InvalidCueMark(f"el track ya tiene {n} {que}; el tope es {tope} por track")

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
    (4, Store._migrar_a_4_sets),
    (5, Store._migrar_a_5_cues),
)


def _marca(fila: sqlite3.Row):
    from motor.cue_marks import CueMark

    return CueMark(id=int(fila["id"]), path_key=fila["path_key"], kind=fila["kind"],
                   num=None if fila["num"] is None else int(fila["num"]),
                   start_ms=int(fila["start_ms"]),
                   end_ms=None if fila["end_ms"] is None else int(fila["end_ms"]),
                   name=fila["name"], created_at=fila["created_at"],
                   updated_at=fila["updated_at"])


def _ordenar_marcas(marcas: list) -> list:
    """Hot cues por número; después memory cues y loops mezclados por tiempo (como se leen
    en la onda). El id desempata: el orden no depende de cómo devuelva SQLite las filas."""
    return sorted(marcas, key=lambda m: (0, m.num, 0, m.id) if m.kind == "cue"
                  else (1, 0, m.start_ms, m.id))


def _curva(config: str) -> str | None:
    """La curva del `RadioConfig` guardado, para el listado. None si el JSON no la trae (o
    no se puede leer): el listado no puede caerse por un set guardado por otro código."""
    import json

    try:
        valor = json.loads(config).get("curve")
    except (ValueError, AttributeError):
        return None
    return valor if isinstance(valor, str) else None
