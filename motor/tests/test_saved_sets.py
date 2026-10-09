"""Tests de los sets guardados y la calificación de transiciones (tarea 16).

La biblioteca es la de `tests/sinteticos.py` (la misma que prueban la API y el E2E): el BPM
y la key se ESCRIBEN en la base y lo que se prueba es que la foto guarde exactamente lo que
dio `build_set` y lo que se mostró, no una medición (esa se prueba contra ground truth en
`test_analisis.py`). Ver el docstring de `tests/sinteticos.py`.

El valor esperado sale siempre de otro lado que del código bajo prueba: del `RadioSet` que
devolvió `build_set`, de las funciones que imprimen la terminal (`cli._fila`) o del catálogo.
"""
import csv
import dataclasses
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "tests"))

import pytest  # noqa: E402
from sinteticos import CATALOGO, LICENCIA, ORIGEN, armar_base_radio, embedding  # noqa: E402

from motor.cli import _fila, main  # noqa: E402
from motor.modelos import TrackFeatures  # noqa: E402
from motor.radio import RadioConfig, build_set  # noqa: E402
from motor.saved_sets import (  # noqa: E402
    InvalidSavedSet,
    SavedSetNotFound,
    config_json,
    fingerprint,
    snapshot_row,
    snapshot_steps,
)
from motor.store import Store  # noqa: E402
from motor.tests.test_store import _ConexionQueFalla, _foto  # noqa: E402


@pytest.fixture
def base(tmp_path):
    """Base del motor con el catálogo sintético. Devuelve (db, rutas por nombre)."""
    db = tmp_path / "djradio" / "biblioteca.sqlite"
    _, rutas = armar_base_radio(tmp_path / "musica", db)
    return db, rutas


def _armar(store: Store, rutas: dict, largo: int = 4):
    """El set desde uno.wav, como lo arma la radio. Con largo 4 sale uno → cinco → dos → tres
    (con una transición a doble tiempo y dos keys dudosas): el set tiene de todo."""
    biblioteca = store.load_library()
    semilla = next(t for t in biblioteca if t.path == rutas["uno.wav"])
    config = RadioConfig(length=largo)
    return build_set(semilla, biblioteca, config), config


def _guardar(store: Store, rset, config, name=None) -> int:
    return store.save_set(snapshot_steps(rset), config=config_json(config),
                          requested=config.length, stop=rset.stop,
                          stop_detail=rset.stop_detail, fragments=rset.fragments, name=name)


# --- la foto --------------------------------------------------------------------------------

def test_la_foto_guardada_es_lo_que_devolvio_build_set(base):
    """Round-trip: cada campo de la foto leída de la base contra el `RadioSet` de
    `build_set` y contra lo que imprime la terminal. Si un valor se recalculara al leer, se
    redondeara al guardar o se cruzara de posición, deja de coincidir."""
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        set_id = _guardar(store, rset, config, name="prueba")
        guardado = store.get_saved_set(set_id)

    nombres = [p.snapshot.path for p in guardado.steps]
    assert nombres == [str(p.track.path) for p in rset.steps], nombres
    assert [Path(n).name for n in nombres] == ["uno.wav", "cinco.wav", "dos.wav", "tres.wav"], \
        "el set de referencia cambió: este test asume el de la docstring de `_armar`"
    for paso, leido in zip(rset.steps, guardado.steps, strict=True):
        t, tr, f = paso.track, paso.transition, leido.snapshot
        assert (f.bpm, f.bpm_shown, f.key, f.key_acuerdo) == \
            (t.bpm, f"{t.bpm:.1f}", t.key, t.key_acuerdo), f
        assert (f.energy, f.energy_pct) == (t.energy, round(t.energy * 100)), f
        assert (f.license, f.source_url, f.label, f.duration) == \
            (t.license, t.source_url, t.label, t.duration), f
        assert f.reason == tr.reason(), f"{f.position}: {f.reason!r} != {tr.reason()!r}"
        assert (f.mixability, f.musical_fit, f.score, f.key_compat) == \
            (tr.mixability, tr.musical_fit, tr.total, tr.key_compat), f
        # El renglón que muestra `sets ver` es, byte a byte, el que imprimió `radio`.
        assert snapshot_row(f) == _fila(t), (snapshot_row(f), _fila(t))
        assert leido.in_library, f"{f.path} está en la biblioteca y se leyó como faltante"
    # Y la foto entera, campo por campo, contra la que se armó en memoria.
    assert [p.snapshot for p in guardado.steps] == snapshot_steps(rset)

    assert guardado.name == "prueba"
    assert guardado.config == dataclasses.asdict(config), guardado.config
    assert (guardado.requested, guardado.stop, guardado.stop_detail, guardado.fragments) == \
        (4, rset.stop, rset.stop_detail, rset.fragments)
    assert guardado.fragments == 1, "loop.wav es un fragmento y el set lo tiene que contar"
    # El `?` se guardó como se mostró: dos.wav tiene acuerdo 2/3 y tres.wav no lo midió.
    assert [p.snapshot.key_doubtful for p in guardado.steps] == [False, False, True, True]


def test_un_reescaneo_no_cambia_la_foto(base):
    """Se guarda el set, después se "re-escanea" dos.wav con otro BPM y otro acuerdo. La
    biblioteca cambia (se verifica) y la foto NO: la calificación tiene que seguir hablando
    de lo que sonó."""
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        set_id = _guardar(store, rset, config)
        antes = store.get_saved_set(set_id)

        i = next(i for i, c in enumerate(CATALOGO) if c[0] == "dos.wav")
        store.upsert(rutas["dos.wav"],
                     TrackFeatures(bpm=133.3, key="10A", energy_raw=0.9, embedding=embedding(i),
                                   key_acuerdo="3/3", key_tramos="10A|10A|10A"),
                     duration=300.0, license=LICENCIA, source_url=ORIGEN,
                     artist="Otro Artista", title="Otro Título")
        hoy = store.get(rutas["dos.wav"])
        assert (hoy.bpm, hoy.key, hoy.label) == (133.3, "10A", "Otro Artista — Otro Título"), \
            "el re-escaneo no cambió la biblioteca: el test no probaría nada"
        despues = store.get_saved_set(set_id)

    assert despues == antes, "la foto cambió con un re-escaneo"
    dos = next(p.snapshot for p in despues.steps if p.snapshot.path.endswith("dos.wav"))
    assert (dos.bpm_shown, dos.key, dos.key_doubtful, dos.label) == \
        ("130.0", "9A", True, "Artista B — Dos"), dos


def test_un_track_que_ya_no_esta_se_lee_entero_y_se_dice(base):
    """dos.wav sale de la biblioteca (el scan lo borra porque ya no está en disco). El set
    guardado se sigue leyendo entero desde la foto, y dice que ESE archivo ya no está."""
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        set_id = _guardar(store, rset, config)
        antes = store.get_saved_set(set_id)
        assert store.delete(rutas["dos.wav"]), "dos.wav no estaba en la base"
        despues = store.get_saved_set(set_id)
        listado = store.list_saved_sets()

    assert [p.snapshot for p in despues.steps] == [p.snapshot for p in antes.steps]
    assert [(Path(p.snapshot.path).name, p.in_library) for p in despues.steps] == [
        ("uno.wav", True), ("cinco.wav", True), ("dos.wav", False), ("tres.wav", True)]
    assert (despues.missing, listado[0].missing) == (1, 1)


# --- calificaciones -------------------------------------------------------------------------

def test_calificar_cambiar_y_borrar_queda_en_la_base(base):
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        set_id = _guardar(store, rset, config)
        store.rate_transition(set_id, 1, "ok")
        store.rate_transition(set_id, 2, "regular", "   ")      # motivo vacío = sin motivo
        store.rate_transition(set_id, 3, "mala", "choque de bajos")
        store.rate_transition(set_id, 1, "mala", "se pisan los kicks")   # la cambia
        assert store.delete_rating(set_id, 2) is True
        assert store.delete_rating(set_id, 2) is False, "borrar dos veces dijo que borró"

    # Reabierta: lo que vale es lo que quedó persistido, no lo que tiene la conexión.
    with Store(db) as store:
        s = store.get_saved_set(set_id)
        listado = store.list_saved_sets()
    assert [(r.transition, r.rating, r.reason) for r in s.ratings] == [
        (1, "mala", "se pisan los kicks"), (3, "mala", "choque de bajos")]
    assert s.summary() == {"ok": 0, "regular": 0, "mala": 2, "sin_calificar": 1}
    assert listado[0].summary == s.summary()


@pytest.mark.parametrize(("calificacion", "motivo", "pista"), [
    ("bien", None, "calificación"),
    ("Mala", "x", "calificación"),          # no se interpreta: "Mala" no es un nivel
    (None, None, "calificación"),
    ("mala", None, "motivo"),
    ("mala", "   ", "motivo"),
    ("ok", 5, "motivo"),
])
def test_calificacion_invalida_no_se_escribe(base, calificacion, motivo, pista):
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        set_id = _guardar(store, rset, config)
        with pytest.raises(InvalidSavedSet, match=pista):
            store.rate_transition(set_id, 1, calificacion, motivo)
        assert store.get_saved_set(set_id).ratings == ()


@pytest.mark.parametrize("transicion", [0, 4, -1, True, "1"])
def test_transicion_que_el_set_no_tiene_no_se_califica(base, transicion):
    """Un set de 4 tracks tiene las transiciones 1, 2 y 3. `True` no es 1 y "1" no es 1."""
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        set_id = _guardar(store, rset, config)
        with pytest.raises(InvalidSavedSet, match="de 1 a 3"):
            store.rate_transition(set_id, transicion, "ok")
        assert store.get_saved_set(set_id).ratings == ()


def test_set_inexistente(base):
    db, _ = base
    with Store(db) as store:
        for accion in (lambda: store.get_saved_set(99), lambda: store.rate_transition(99, 1, "ok"),
                       lambda: store.delete_rating(99, 1), lambda: store.rename_saved_set(99, "x"),
                       lambda: store.delete_saved_set(99)):
            with pytest.raises(SavedSetNotFound, match="99"):
                accion()


def test_la_base_tampoco_acepta_una_mala_sin_motivo(base):
    """El CHECK de la tabla repite la validación: una fila escrita por fuera del store tampoco
    puede dejar una `mala` sin motivo ni un nivel inventado."""
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        set_id = _guardar(store, rset, config)
    con = sqlite3.connect(str(db))
    try:
        for fila in ((set_id, 1, "mala", None, "x"), (set_id, 1, "mala", "  ", "x"),
                     (set_id, 1, "genial", None, "x")):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute("INSERT INTO saved_set_ratings VALUES (?, ?, ?, ?, ?)", fila)
    finally:
        con.close()


def test_guardar_valida_la_foto(base):
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        fotos = snapshot_steps(rset)
        kw = dict(config=config_json(config), requested=4, stop=None, stop_detail="",
                  fragments=0)
        casos = [
            ([], "al menos un track"),
            ([fotos[1], fotos[0], *fotos[2:]], "posiciones"),
            # Una licencia que no es texto no es una declaración (no se guarda un "123").
            ([dataclasses.replace(fotos[0], license=123), *fotos[1:]], "license"),
            ([dataclasses.replace(fotos[0], source_url=["x"]), *fotos[1:]], "source_url"),
            ([fotos[0], dataclasses.replace(fotos[1], is_seed=True), *fotos[2:]], "semilla"),
        ]
        for pasos, pista in casos:
            with pytest.raises(ValueError, match=pista):
                store.save_set(pasos, **kw)
        with pytest.raises(InvalidSavedSet, match="nombre"):
            store.save_set(fotos, **kw, name=123)
        assert store.list_saved_sets() == [], "una validación fallida dejó un set guardado"


def test_la_foto_guarda_lo_no_declarado_como_el_literal(base):
    """Licencia y origen opcionales (CLAUDE.md, 2026-10-09): una foto con licencia en blanco
    y sin origen se guarda con el literal "no declarado" —nunca un vacío— y los pasos que sí
    los declaran quedan TAL CUAL (los del catálogo). Se mira la fila cruda de la base."""
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        fotos = snapshot_steps(rset)
        fotos[0] = dataclasses.replace(fotos[0], license="  ", source_url=None)
        set_id = store.save_set(fotos, config=config_json(config), requested=4, stop=None,
                                stop_detail="", fragments=0)
    con = sqlite3.connect(str(db))
    filas = con.execute("SELECT position, license, source_url FROM saved_set_steps "
                        "WHERE set_id = ? ORDER BY position", (set_id,)).fetchall()
    con.close()
    assert filas == [(1, "no declarado", "no declarado"),
                     *((n, LICENCIA, ORIGEN) for n in range(2, len(fotos) + 1))], filas


def test_guardar_es_todo_o_nada(base):
    """Si falla el INSERT de los pasos, la cabecera tampoco queda: un set "escuchado" sin
    tracks no existe."""
    class _FallaEnLosPasos(_ConexionQueFalla):
        def executemany(self, sql, *args):
            if sql.startswith(self._prefijo):
                raise RuntimeError(f"falla inyectada en {sql[:30]!r}")
            return self._real.executemany(sql, *args)

    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        real = store._con
        store._con = _FallaEnLosPasos(real, "INSERT INTO saved_set_steps")
        with pytest.raises(RuntimeError, match="falla inyectada"):
            _guardar(store, rset, config)
        store._con = real
        # Se mira con la MISMA conexión: una cabecera sin confirmar también se vería acá.
        assert store.list_saved_sets() == [], "quedó la cabecera de un set sin sus pasos"
    with Store(db) as store:
        assert store.list_saved_sets() == []


def test_renombrar_y_borrar(base):
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        a = _guardar(store, rset, config, name="viejo")
        b = _guardar(store, rset, config, name="se borra")
        assert store.rename_saved_set(a, "nuevo") == "nuevo"
        assert store.rename_saved_set(b, "   ") is None
        store.rate_transition(b, 1, "ok")
        # Se borra el ÚLTIMO: es el caso en que SQLite, sin AUTOINCREMENT, reusaría su id.
        store.delete_saved_set(b)
        c = _guardar(store, rset, config)
        nombres = [(s.id, s.name) for s in store.list_saved_sets()]
    assert nombres == [(c, None), (a, "nuevo")], nombres
    assert c > b, f"el id {b} de un set borrado se reusó ({c}): un CSV viejo hablaría de otro set"
    con = sqlite3.connect(str(db))
    huerfanos = [con.execute(f"SELECT COUNT(*) FROM {t} WHERE set_id = ?", (b,)).fetchone()[0]
                 for t in ("saved_set_steps", "saved_set_ratings")]
    con.close()
    assert huerfanos == [0, 0], f"borrar el set dejó pasos/calificaciones: {huerfanos}"


def _otro_valor(valor):
    """Un valor distinto del mismo tipo, para cambiar UN campo por vez."""
    if isinstance(valor, bool):
        return not valor
    if isinstance(valor, int):
        return valor + 1
    if isinstance(valor, float):
        return valor + 0.5
    if isinstance(valor, str):
        return valor + "x"
    return "x"          # None → un valor presente


def test_la_huella_cambia_con_cada_dato_mostrado_por_separado(base):
    """TODOS los campos de la foto y de la cabecera entran en la huella: se cambia cada uno
    por separado (en el paso 2, que no es la semilla y tiene todo medido) y la huella tiene
    que cambiar. Un campo que la huella ignorara podría cambiar entre mostrar y guardar sin
    que el 409 salte."""
    from motor.saved_sets import StepSnapshot, shown_header

    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
    fotos, cab = snapshot_steps(rset), shown_header(rset, config)
    base_huella = fingerprint(fotos, cab)
    assert base_huella == fingerprint(snapshot_steps(rset), shown_header(rset, config)), \
        "la huella no es estable"

    sin_cambio = []
    for campo in (f.name for f in dataclasses.fields(StepSnapshot)):
        otra = [*fotos[:1],
                dataclasses.replace(fotos[1], **{campo: _otro_valor(getattr(fotos[1], campo))}),
                *fotos[2:]]
        if fingerprint(otra, cab) == base_huella:
            sin_cambio.append(campo)
    for clave in cab:
        if fingerprint(fotos, {**cab, clave: _otro_valor(cab[clave])}) == base_huella:
            sin_cambio.append(f"cabecera.{clave}")
    assert sin_cambio == [], f"la huella no ve estos campos: {sin_cambio}"
    assert set(cab) == {"requested", "curve", "seed", "randomness", "stop", "stop_detail",
                        "fragments"}, cab


# --- migración v3 → v4: atomicidad y concurrencia ---------------------------------------

# El esquema de la versión 3 (`git show 649a221:motor/store.py`): la biblioteca que tiene hoy
# cualquiera que escaneó, sin las tablas de sets guardados.
SCHEMA_V3 = """
CREATE TABLE tracks (
    path TEXT PRIMARY KEY, path_key TEXT, mtime REAL NOT NULL, duration REAL NOT NULL,
    artist TEXT, title TEXT, bpm REAL NOT NULL, key TEXT NOT NULL, energy_raw REAL NOT NULL,
    embedding BLOB NOT NULL, rms REAL, onset_rate REAL, percussive_ratio REAL,
    key_acuerdo TEXT, key_tramos TEXT, license TEXT NOT NULL, source_url TEXT NOT NULL,
    analyzed_at TEXT NOT NULL
);
CREATE INDEX idx_tracks_bpm ON tracks(bpm);
CREATE INDEX idx_tracks_key ON tracks(key);
CREATE UNIQUE INDEX idx_tracks_path_key ON tracks(path_key);
CREATE TABLE norm_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1), mean BLOB NOT NULL, std BLOB NOT NULL,
    count INTEGER NOT NULL
);
PRAGMA user_version = 3;
"""


def _base_v3(carpeta: Path) -> Path:
    db = carpeta / "v3.sqlite"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA_V3)
    for i, (nombre, bpm, key, ac, tr, dur, artista, titulo, e) in enumerate(CATALOGO[:3]):
        ruta = carpeta / nombre
        con.execute("INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(ruta), os.path.normcase(str(ruta)), 100.0 + i, dur, artista, titulo,
                     bpm, key, e, embedding(i).tobytes(), None, None, None, ac, tr, LICENCIA,
                     ORIGEN, f"2026-09-0{i + 1}T00:00:00"))
    con.commit()
    con.close()
    return db


def _tablas(db: Path) -> set[str]:
    con = sqlite3.connect(str(db))
    try:
        return {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def test_base_v3_se_migra_y_conserva_la_biblioteca(tmp_path):
    from motor.store import VERSION_ESQUEMA

    db = _base_v3(tmp_path)
    filas_antes = _foto(db)[3]
    assert "saved_sets" not in _tablas(db)

    with Store(db) as store:
        assert store.list_saved_sets() == []
        assert [t.path.name for t in store.load_library()] == ["dos.wav", "tres.wav", "uno.wav"]

    version, _, _, filas = _foto(db)
    # Una base v3 abierta hoy pasa por la 4 (sets), la 5 (marcas, f48) y la 6 (hot loops, f51)
    # en la misma apertura.
    assert (version, VERSION_ESQUEMA) == (6, 6)
    assert filas == filas_antes, "la migración 4 tocó las filas de `tracks`"
    assert {"saved_sets", "saved_set_steps", "saved_set_ratings"} <= _tablas(db)


def test_migracion_4_que_falla_deja_la_base_v3_como_estaba(tmp_path, monkeypatch):
    import motor.store as modulo_store

    db = _base_v3(tmp_path)
    antes = _foto(db)

    def paso_4_que_falla(self):
        Store._migrar_a_4_sets(self)
        raise RuntimeError("falla inyectada al final de la migración 4")

    monkeypatch.setattr(modulo_store, "_MIGRACIONES", (*modulo_store._MIGRACIONES[:3],
                                                       (4, paso_4_que_falla),
                                                       *modulo_store._MIGRACIONES[4:]))
    with pytest.raises(RuntimeError, match="migración 4"):
        Store(db)
    assert _foto(db) == antes, "la migración 4 fallida dejó la base modificada"
    assert "saved_sets" not in _tablas(db), "quedaron tablas de una migración que falló"

    monkeypatch.undo()
    with Store(db) as store:
        assert store.list_saved_sets() == [] and store.count() == 3


def test_migracion_4_que_falla_al_escribir_la_version_deja_la_base_v3(tmp_path, monkeypatch):
    """Las tablas nuevas y el `user_version` van en la MISMA transacción: tablas creadas con
    la versión vieja harían que la próxima apertura migre sobre una forma que ya cambió."""
    import motor.store as modulo_store

    db = _base_v3(tmp_path)
    antes = _foto(db)

    def paso_4_y_romper_la_version(self):
        Store._migrar_a_4_sets(self)
        self._con = _ConexionQueFalla(self._con, "PRAGMA user_version =")

    monkeypatch.setattr(modulo_store, "_MIGRACIONES", (*modulo_store._MIGRACIONES[:3],
                                                       (4, paso_4_y_romper_la_version),
                                                       *modulo_store._MIGRACIONES[4:]))
    with pytest.raises(RuntimeError, match="user_version"):
        Store(db)
    assert _foto(db) == antes, "la falla al escribir la versión dejó la base modificada"
    assert "saved_sets" not in _tablas(db)


def test_dos_aperturas_simultaneas_de_una_base_v3_migran_una_vez(tmp_path, monkeypatch):
    """Mismo escenario que el H2 de la migración 1 (`test_store.py`): A entra a la migración
    4 y se frena, B abre la misma base mientras tanto. Los dos abren bien, la migración corre
    UNA vez y la base queda en v4 con sus datos."""
    import motor.store as modulo_store

    db = _base_v3(tmp_path)
    a_adentro, seguir_a = threading.Event(), threading.Event()
    migraron: list[str] = []

    def paso_4_que_se_frena_en_a(self):
        migraron.append(threading.current_thread().name)
        if threading.current_thread().name == "A":
            self._con.execute("SELECT COUNT(*) FROM tracks").fetchone()
            a_adentro.set()
            seguir_a.wait(10)
        Store._migrar_a_4_sets(self)

    monkeypatch.setattr(modulo_store, "_MIGRACIONES", (*modulo_store._MIGRACIONES[:3],
                                                       (4, paso_4_que_se_frena_en_a),
                                                       *modulo_store._MIGRACIONES[4:]))
    resultados: dict[str, object] = {}

    def abrir(nombre):
        try:
            with Store(db) as store:
                resultados[nombre] = (store.count(), store.list_saved_sets())
        except Exception as e:  # noqa: BLE001 — el resultado ES la excepción
            resultados[nombre] = e

    hilo_a = threading.Thread(target=abrir, args=("A",), name="A")
    hilo_a.start()
    assert a_adentro.wait(10), "A nunca llegó a la migración"
    hilo_b = threading.Thread(target=abrir, args=("B",), name="B")
    hilo_b.start()
    hilo_b.join(0.5)
    seguir_a.set()
    hilo_a.join(60)
    hilo_b.join(60)

    assert resultados == {"A": (3, []), "B": (3, [])}, f"aperturas simultáneas: {resultados}"
    assert migraron == ["A"], f"la migración corrió en {migraron}"
    assert _foto(db)[0] == 6


def test_dos_procesos_guardando_a_la_vez_no_se_pisan(base):
    """Otro proceso tiene la base tomada para escribir (un scan): el guardado ESPERA y
    después guarda, en vez de morir con "database is locked"."""
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
    otro = sqlite3.connect(str(db), timeout=0.1)
    otro.execute("BEGIN IMMEDIATE")
    resultado: dict[str, object] = {}

    def guardar():
        try:
            with Store(db, espera_bloqueo_s=10) as store:
                resultado["id"] = _guardar(store, rset, config, name="en espera")
        except Exception as e:  # noqa: BLE001
            resultado["id"] = e

    hilo = threading.Thread(target=guardar)
    hilo.start()
    hilo.join(0.5)
    assert hilo.is_alive(), f"no esperó al otro proceso: {resultado}"
    otro.rollback()
    otro.close()
    hilo.join(30)
    assert isinstance(resultado["id"], int), resultado
    with Store(db) as store:
        assert [(s.id, s.name) for s in store.list_saved_sets()] == [(resultado["id"], "en espera")]


# --- CLI --------------------------------------------------------------------------------

def _correr(capsys, *argv) -> tuple[int, str, str]:
    codigo = main([str(a) for a in argv])
    salida = capsys.readouterr()
    return codigo, salida.out, salida.err


def _renglones_del_set(out: str) -> list[str]:
    """Los renglones de tracks y porqués (`  N. ...` y `└ ...` del motor), sin las
    calificaciones, que `radio` no tiene."""
    return [r for r in out.splitlines()
            if (r[:5].strip().rstrip(".").isdigit() and r.startswith("  ")
                or r.strip().startswith("└ ") and not r.strip().startswith("└ ["))]


def test_cli_radio_guardar_ver_calificar_y_exportar(base, capsys, tmp_path):
    db, rutas = base
    codigo, radio, err = _correr(capsys, "--db", db, "radio", rutas["uno.wav"], "--largo", 4,
                                 "--guardar", "mi primer set")
    assert codigo == 0, err
    assert "Guardado como set #1." in radio, radio

    codigo, ver, err = _correr(capsys, "--db", db, "sets", "ver", 1)
    assert codigo == 0, err
    assert _renglones_del_set(ver) == _renglones_del_set(radio), \
        f"`sets ver` no muestra lo mismo que imprimió `radio`:\n{ver}\n---\n{radio}"
    assert len(_renglones_del_set(ver)) == 8
    assert radio.splitlines()[0] in ver.splitlines(), "el encabezado del set no es el mismo"

    codigo, _, err = _correr(capsys, "--db", db, "sets", "calificar", 1, 3, "mala")
    assert codigo == 2 and "motivo" in err, err
    for n, nivel, extra in ((1, "ok", []), (2, "regular", ["--motivo", "entra justo"]),
                            (3, "mala", ["--motivo", "=choque armónico"])):
        codigo, out, err = _correr(capsys, "--db", db, "sets", "calificar", 1, n, nivel, *extra)
        assert codigo == 0, err
    assert "Set #1: ok 1 · regular 1 · mala 1 · sin calificar 0" in out, out

    codigo, ver, _ = _correr(capsys, "--db", db, "sets", "ver", 1)
    assert [r.strip() for r in ver.splitlines() if r.strip().startswith("└ [")] == [
        "└ [1 → 2] ok", "└ [2 → 3] regular — entra justo", "└ [3 → 4] mala — =choque armónico"]

    codigo, listado, _ = _correr(capsys, "--db", db, "sets", "listar")
    fila = next(r for r in listado.splitlines() if r.strip().startswith("1 "))
    assert fila.split()[4:8] == ["1", "1", "1", "0"], fila
    assert "\"mi primer set\" · Artista A — Uno" in fila, fila

    csv_path = tmp_path / "planilla.csv"
    codigo, out, err = _correr(capsys, "--db", db, "sets", "exportar", csv_path)
    assert codigo == 0, err
    assert "Separado por \";\"" in out, out
    crudo = csv_path.read_bytes()
    assert crudo.startswith(b"\xef\xbb\xbfset;nombre_set;guardado;"), \
        f"sin BOM Excel no lee los acentos, y el separador por defecto es ';': {crudo[:40]!r}"
    filas = list(csv.DictReader(crudo.decode("utf-8-sig").splitlines(), delimiter=";"))
    # Con `--separador ,` es el mismo contenido, separado por coma.
    csv_coma = tmp_path / "planilla_coma.csv"
    codigo, out_coma, err = _correr(capsys, "--db", db, "sets", "exportar", csv_coma,
                                    "--separador", ",")
    assert codigo == 0 and "Separado por \",\"" in out_coma, err
    assert list(csv.DictReader(csv_coma.read_bytes().decode("utf-8-sig").splitlines())) == filas
    with Store(db) as store:
        rset, _ = _armar(store, rutas)
    esperado = [(f"{n} → {n + 1}", f"{rset[n - 1].track.bpm:.1f} BPM",
                 f"{rset[n].track.bpm:.1f} BPM", "'" + rset[n].transition.reason())
                for n in (1, 2, 3)]
    assert [(f["transicion"], f["bpm_desde"], f["bpm_hasta"], f["porque"]) for f in filas] == \
        esperado
    assert [(f["calificacion"], f["motivo"]) for f in filas] == [
        ("ok", ""), ("regular", "entra justo"), ("mala", "'=choque armónico")]
    assert [f["acuerdo_key_desde"] for f in filas] == ["3 de 3", "3 de 3", "2 de 3"]
    assert [f["key_hasta"] for f in filas] == ["8A", "9A?", "8B?"]
    assert [float(f["score"]) for f in filas] == [rset[n].transition.total for n in (1, 2, 3)]


def test_cli_sets_ver_dice_que_un_track_ya_no_esta(base, capsys, tmp_path):
    db, rutas = base
    assert _correr(capsys, "--db", db, "radio", rutas["uno.wav"], "--largo", 4,
                   "--guardar")[0] == 0
    with Store(db) as store:
        store.delete(rutas["dos.wav"])
    codigo, ver, err = _correr(capsys, "--db", db, "sets", "ver", 1)
    assert codigo == 0, err
    renglon = next(r for r in ver.splitlines() if r.startswith("   3."))
    assert renglon.endswith("Artista B — Dos   [YA NO ESTÁ EN LA BIBLIOTECA]"), renglon
    assert f"   3. {rutas['dos.wav']}" in ver.splitlines(), ver
    assert sum("YA NO ESTÁ" in r for r in ver.splitlines()) == 1, ver

    # El CSV también lo dice, en las dos transiciones que tocan a dos.wav (la 2 llega, la 3 sale).
    destino = tmp_path / "faltan.csv"
    assert _correr(capsys, "--db", db, "sets", "exportar", destino)[0] == 0
    filas = list(csv.DictReader(destino.read_bytes().decode("utf-8-sig").splitlines(),
                                delimiter=";"))
    assert [(f["desde_en_biblioteca"], f["hasta_en_biblioteca"]) for f in filas] == [
        ("sí", "sí"), ("sí", "no"), ("no", "sí")]


# --- un set cortado (el motor no llegó al largo pedido) -----------------------------------

def test_un_set_cortado_se_guarda_con_su_largo_pedido_y_su_corte(base, capsys):
    """Pedido de 10 sobre una biblioteca que no da para 10: el set se corta. Se guarda con el
    largo PEDIDO, el código y el detalle del corte, y `sets ver` lo dice igual que `radio`."""
    db, rutas = base
    with Store(db) as store:
        rset, _ = _armar(store, rutas, largo=10)
    assert rset.stop is not None and len(rset) < 10, \
        f"el set de 10 no se cortó ({len(rset)}, {rset.stop}): el test no probaría nada"

    codigo, radio, err = _correr(capsys, "--db", db, "radio", rutas["uno.wav"], "--largo", 10,
                                 "--guardar")
    assert codigo == 0, err
    with Store(db) as store:
        s = store.get_saved_set(1)
    assert (len(s.steps), s.requested, s.stop, s.stop_detail) == \
        (len(rset), 10, rset.stop, rset.stop_detail)
    assert s.summary()["sin_calificar"] == len(rset) - 1

    codigo, ver, err = _correr(capsys, "--db", db, "sets", "ver", 1)
    assert codigo == 0, err
    corte_radio = [r for r in radio.splitlines() if r.startswith("NO ") or "El set quedó" in r]
    corte_ver = [r for r in ver.splitlines() if r.startswith("NO ") or "El set quedó" in r]
    assert len(corte_radio) == 2 and corte_ver == corte_radio, (corte_ver, corte_radio)
    assert f"{len(rset)} de 10 tracks pedidos" in ver, ver


# --- validaciones de texto --------------------------------------------------------------

@pytest.mark.parametrize(("crudo", "limpio"), [
    ("noche\nde prueba", "noche de prueba"),
    ("noche\r\nde prueba", "noche de prueba"),
    ("a\x00b\tc\x7fd", "a b c d"),
])
def test_nombres_y_motivos_quedan_en_una_linea(base, crudo, limpio):
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        sid = _guardar(store, rset, config, name=crudo)
        assert store.rename_saved_set(sid, crudo) == limpio
        store.rate_transition(sid, 1, "mala", crudo)
        s = store.get_saved_set(sid)
        assert (s.name, s.ratings[0].reason) == (limpio, limpio)
        with pytest.raises(InvalidSavedSet, match="motivo"):
            store.rate_transition(sid, 2, "mala", "\r\n\t\x00")


def test_el_motivo_se_guarda_recortado(base):
    """Espacios (y saltos, que pasan a espacio) al principio o al final no son parte del
    motivo: se guarda recortado. Los de adentro quedan."""
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        sid = _guardar(store, rset, config)
        assert store.rate_transition(sid, 1, "mala", "  choque  de bajos \n").reason == "choque  de bajos"
        assert store.rate_transition(sid, 2, "ok", "\t bien ").reason == "bien"
        s = store.get_saved_set(sid)
        assert [r.reason for r in s.ratings] == ["choque  de bajos", "bien"]


def test_sets_listar_no_se_parte_con_un_nombre_de_varias_lineas(base, capsys):
    db, rutas = base
    assert _correr(capsys, "--db", db, "radio", rutas["uno.wav"], "--largo", 4,
                   "--guardar", "linea uno\nlinea dos")[0] == 0
    _, listado, _ = _correr(capsys, "--db", db, "sets", "listar")
    # Las filas de sets van alineadas a la derecha ("   1  2026-…"); el pie no tiene sangría.
    filas = [r for r in listado.splitlines() if r.startswith("   ") and r.strip()[:1].isdigit()]
    assert len(filas) == 1 and "\"linea uno linea dos\" · Artista A — Uno" in filas[0], listado


def test_motivo_y_nombre_con_tope(base):
    from motor.saved_sets import NAME_MAX, REASON_MAX

    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        sid = _guardar(store, rset, config, name="n" * NAME_MAX)
        assert store.rate_transition(sid, 1, "ok", "m" * REASON_MAX).reason == "m" * REASON_MAX
        with pytest.raises(InvalidSavedSet, match=f"como mucho {REASON_MAX}"):
            store.rate_transition(sid, 1, "mala", "m" * (REASON_MAX + 1))
        with pytest.raises(InvalidSavedSet, match=f"como mucho {NAME_MAX}"):
            store.rename_saved_set(sid, "n" * (NAME_MAX + 1))
        s = store.get_saved_set(sid)
    assert (s.name, s.ratings[0].rating) == ("n" * NAME_MAX, "ok"), "una falla escribió igual"


@pytest.mark.parametrize("inicio", ["=", "+", "-", "@", "\t", "\r"])
def test_el_csv_neutraliza_todo_lo_que_excel_toma_como_formula(inicio):
    from motor.saved_sets import _celda

    assert _celda(f"{inicio}1+1") == f"'{inicio}1+1"
    assert _celda("1+1") == "1+1", "a lo que no empieza con fórmula no se le agrega nada"


@pytest.mark.parametrize("set_id", [2 ** 63, 10 ** 25, -(2 ** 63) - 1])
def test_un_id_fuera_del_rango_de_sqlite_es_set_inexistente(base, set_id):
    """SQLite guarda enteros de 64 bits: un id más grande no puede existir, y pasárselo
    tira `OverflowError` (que por la API era un 500)."""
    db, _ = base
    with Store(db) as store:
        for accion in (lambda: store.get_saved_set(set_id),
                       lambda: store.rate_transition(set_id, 1, "ok"),
                       lambda: store.delete_rating(set_id, 1),
                       lambda: store.rename_saved_set(set_id, "x"),
                       lambda: store.delete_saved_set(set_id)):
            with pytest.raises(SavedSetNotFound, match=str(set_id)):
                accion()


def _borrar_el_set_antes_de_escribir(monkeypatch, db, set_id):
    """Otro proceso borra el set justo entre que se pidió la escritura y que se toma el lock:
    el caso en que validar AFUERA de la transacción no alcanza."""
    original = Store._escritura

    def escritura(self):
        otro = sqlite3.connect(str(db))
        for sql in ("DELETE FROM saved_set_ratings WHERE set_id = ?",
                    "DELETE FROM saved_set_steps WHERE set_id = ?",
                    "DELETE FROM saved_sets WHERE id = ?"):
            otro.execute(sql, (set_id,))
        otro.commit()
        otro.close()
        return original(self)

    monkeypatch.setattr(Store, "_escritura", escritura)


@pytest.mark.parametrize("accion", ["calificar", "descalificar", "renombrar", "borrar"])
def test_un_set_borrado_en_el_medio_es_inexistente_y_no_una_base_rota(base, monkeypatch,
                                                                       accion):
    db, rutas = base
    with Store(db) as store:
        rset, config = _armar(store, rutas)
        sid = _guardar(store, rset, config)
        store.rate_transition(sid, 1, "ok")
        _borrar_el_set_antes_de_escribir(monkeypatch, db, sid)
        hacer = {"calificar": lambda: store.rate_transition(sid, 2, "ok"),
                 "descalificar": lambda: store.delete_rating(sid, 1),
                 "renombrar": lambda: store.rename_saved_set(sid, "x"),
                 "borrar": lambda: store.delete_saved_set(sid)}[accion]
        with pytest.raises(SavedSetNotFound, match=str(sid)):
            hacer()


def test_cli_sets_errores_de_uso(base, capsys, tmp_path):
    db, _ = base
    codigo, _, err = _correr(capsys, "--db", db, "sets", "ver", 7)
    assert codigo == 2 and "No hay un set guardado con id 7" in err, err
    codigo, _, err = _correr(capsys, "--db", tmp_path / "no.sqlite", "sets", "listar")
    assert codigo == 2 and "No existe la base" in err, err
    assert not (tmp_path / "no.sqlite").exists(), "`sets listar` creó una base que no existía"


def test_config_json_es_el_radioconfig_entero():
    c = RadioConfig(length=7, curve="warmup", seed=3, randomness=0.25, w_energy=0.5)
    assert json.loads(config_json(c)) == {
        "length": 7, "curve": "warmup", "artist_gap": 4, "mmr_lambda": 0.3, "w_seed": 0.5,
        "w_prev": 0.5, "w_energy": 0.5, "seed": 3, "randomness": 0.25, "top_k": 5}
