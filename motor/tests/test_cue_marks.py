"""Tests de las marcas del dueño (f48): hot cues, memory cues y loops en el store (esquema v5).

Biblioteca de `tests/sinteticos.py` (la misma que prueban la API y el E2E). Las duraciones
salen del CATALOGO (lo que declara la base), no de este archivo: "uno.wav" dura 240 s porque
así lo escribió `armar_base_radio`.

Lo central:
- la migración v4 → v5 es atómica y concurrente (como la 4);
- las validaciones rechazan ANTES de escribir y no dejan nada a medias;
- el pad de un hot cue es único por track, también con pedidos a la vez;
- un re-escaneo (reanálisis, archivo que desaparece y vuelve, archivo movido) NO se lleva las
  marcas: se prueba con el `scan` real de la CLI sobre audio sintético.
"""
import os
import sqlite3
import sys
import threading
from pathlib import Path

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "tests"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import soundfile as sf  # noqa: E402
from sinteticos import CATALOGO, armar_base_radio, embedding  # noqa: E402

from motor.cli import main  # noqa: E402
from motor.cue_marks import (  # noqa: E402
    HOT_CUES,
    MAX_LOOPS,
    MAX_MEMORY,
    NAME_MAX,
    CueMarkNotFound,
    InvalidCueMark,
)
from motor.modelos import TrackFeatures  # noqa: E402
from motor.sintetico import click_track  # noqa: E402
from motor.store import Store  # noqa: E402
from motor.tests.test_store import _ConexionQueFalla, _foto  # noqa: E402


@pytest.fixture
def base(tmp_path):
    db = tmp_path / "djradio" / "biblioteca.sqlite"
    _, rutas = armar_base_radio(tmp_path / "musica", db)
    return db, rutas


def _tupla(m):
    return (m.id, m.kind, m.num, m.start_ms, m.end_ms, m.name, m.created_at, m.updated_at)


def _sembrar(store, ruta):
    """Una marca de cada tipo, con nombres. Devuelve las marcas como quedaron."""
    store.add_cue_mark(ruta, "cue", 12.345, name="Drop")
    store.add_cue_mark(ruta, "memory", 3.5)
    store.add_cue_mark(ruta, "loop", 64.0, 71.5, name="4 compases")
    store.add_cue_mark(ruta, "cue", 1.0, num=5)
    return store.list_cue_marks(ruta)


# --- crear, leer, ordenar ---------------------------------------------------------------

def test_las_marcas_vuelven_como_se_escribieron(base):
    db, rutas = base
    with Store(db) as store:
        _sembrar(store, rutas["uno.wav"])
    with Store(db) as store:                      # otra conexión: es lo que quedó en la base
        marcas = store.list_cue_marks(rutas["uno.wav"])
    # Orden de la tabla: hot cues por pad, después memory y loops por tiempo.
    assert [(m.kind, m.num, m.start_ms, m.end_ms, m.name) for m in marcas] == [
        ("cue", 0, 12345, None, "Drop"),
        ("cue", 5, 1000, None, None),
        ("memory", None, 3500, None, None),
        ("loop", None, 64000, 71500, "4 compases"),
    ]
    assert marcas[0].start_s == 12.345 and marcas[3].end_s == 71.5


def test_precision_de_milisegundo(base):
    """Los tiempos se guardan en ms enteros: 61.234 vuelve 61.234, y lo que viene con más
    decimales se redondea al ms más cercano (no se trunca)."""
    db, rutas = base
    with Store(db) as store:
        a = store.add_cue_mark(rutas["uno.wav"], "memory", 61.234)
        b = store.add_cue_mark(rutas["uno.wav"], "memory", 10.0006)
        c = store.add_cue_mark(rutas["uno.wav"], "memory", 10.0004)
    assert (a.start_ms, a.start_s) == (61234, 61.234)
    assert (b.start_ms, c.start_ms) == (10001, 10000)


def test_pad_libre_y_pad_ocupado(base):
    db, rutas = base
    uno = rutas["uno.wav"]
    with Store(db) as store:
        for n in (0, 1, 3):
            store.add_cue_mark(uno, "cue", 10.0 + n, num=n)
        assert store.add_cue_mark(uno, "cue", 20.0).num == 2, "no tomó el primer pad libre"
        with pytest.raises(InvalidCueMark, match="hot cue 2 ya está puesto"):
            store.add_cue_mark(uno, "cue", 30.0, num=1)
        for _ in range(HOT_CUES - 4):
            store.add_cue_mark(uno, "cue", 40.0)
        assert sorted(m.num for m in store.list_cue_marks(uno)) == list(range(HOT_CUES))
        with pytest.raises(InvalidCueMark, match="ya tiene los 8 hot cues"):
            store.add_cue_mark(uno, "cue", 50.0)
        # El mismo pad en OTRO track sí: la unicidad es por track.
        assert store.add_cue_mark(rutas["dos.wav"], "cue", 5.0, num=1).num == 1


def test_la_base_tampoco_acepta_dos_hot_cues_en_el_mismo_pad(base):
    """El índice único repite la regla en la base: una fila escrita por fuera tampoco puede
    duplicar un pad."""
    db, rutas = base
    with Store(db) as store:
        store.add_cue_mark(rutas["uno.wav"], "cue", 1.0, num=2)
        clave = store._key(rutas["uno.wav"])
    con = sqlite3.connect(str(db))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            con.execute("INSERT INTO cue_marks (path_key, kind, num, start_ms, created_at, "
                        "updated_at) VALUES (?, 'cue', 2, 5000, 'x', 'x')", (clave,))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            con.execute("INSERT INTO cue_marks (path_key, kind, num, start_ms, end_ms, "
                        "created_at, updated_at) VALUES (?, 'loop', NULL, 5000, 4000, 'x', 'x')",
                        (clave,))
    finally:
        con.close()


def test_pedidos_a_la_vez_no_repiten_pad(base):
    """Diez procesos piden «el primer pad libre» a la vez: salen los 8 pads, sin repetir, y
    los dos que sobran reciben el motivo (no un IntegrityError de la base)."""
    db, rutas = base
    uno = rutas["uno.wav"]
    barrera = threading.Barrier(10)
    resultados: list[object] = []
    candado = threading.Lock()

    def poner(i):
        with Store(db, espera_bloqueo_s=30) as store:
            barrera.wait(10)
            try:
                r = store.add_cue_mark(uno, "cue", 10.0 + i).num
            except Exception as e:  # noqa: BLE001 — el resultado ES la excepción
                r = e
        with candado:
            resultados.append(r)

    hilos = [threading.Thread(target=poner, args=(i,)) for i in range(10)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(60)
    pads = sorted(r for r in resultados if isinstance(r, int))
    errores = [r for r in resultados if not isinstance(r, int)]
    assert pads == list(range(HOT_CUES)), resultados
    assert len(errores) == 2 and all(isinstance(e, InvalidCueMark) for e in errores), errores


# --- validaciones -----------------------------------------------------------------------

@pytest.mark.parametrize(("kind", "start", "end", "num", "name", "pista"), [
    ("hot", 1.0, None, None, None, "tipo de marca"),
    (None, 1.0, None, None, None, "tipo de marca"),
    ("cue", 240.0, None, None, None, "dura 240.000"),       # justo al final: afuera
    ("cue", 999.0, None, None, None, "dura 240.000"),
    ("cue", -0.5, None, None, None, "negativo"),
    ("cue", float("nan"), None, None, None, "finito"),
    ("cue", float("inf"), None, None, None, "finito"),
    ("cue", 10 ** 400, None, None, None, "fuera de rango"),
    # Finitos pero enormes: 1e306 × 1000 es infinito y `round` revienta (era un 500).
    ("cue", 1e306, None, None, None, "fuera de rango"),
    ("cue", 1.7e308, None, None, None, "fuera de rango"),
    ("cue", 86400.001, None, None, None, "fuera de rango"),
    ("loop", 1.0, 1e306, None, None, "fuera de rango"),
    ("cue", True, None, None, None, "número"),
    ("cue", "12.5", None, None, None, "número"),
    ("cue", 1.0, None, 8, None, "de 0 a 7"),
    ("cue", 1.0, None, -1, None, "de 0 a 7"),
    ("cue", 1.0, None, True, None, "de 0 a 7"),
    ("cue", 1.0, None, 1.0, None, "de 0 a 7"),
    ("memory", 1.0, None, 3, None, "solo un hot cue lleva número"),
    ("memory", 1.0, 2.0, None, None, "solo un loop tiene salida"),
    ("loop", 10.0, None, None, None, "necesita la salida"),
    ("loop", 10.0, 10.0, None, None, "después de la entrada"),
    ("loop", 10.0, 9.0, None, None, "después de la entrada"),
    ("loop", 230.0, 240.5, None, None, "después del final"),
    ("cue", 1.0, None, None, "dos\nlíneas", r"U\+000A"),
    ("cue", 1.0, None, None, "retorno\r", r"U\+000D"),
    ("cue", 1.0, None, None, "nul\x00", r"U\+0000"),
    ("cue", 1.0, None, None, "tab\tx", r"U\+0009"),
    ("cue", 1.0, None, None, "nel\x85", r"U\+0085"),
    ("cue", 1.0, None, None, "línea ", r"U\+2028"),
    ("cue", 1.0, None, None, "al revés ‮", r"U\+202E"),
    # Invisibles de formato (Cf): dos nombres que se ven iguales y no lo son.
    ("cue", 1.0, None, None, "a​b", r"U\+200B"),
    ("cue", 1.0, None, None, "﻿intro", r"U\+FEFF"),
    ("cue", 1.0, None, None, "drop⁠", r"U\+2060"),
    ("cue", 1.0, None, None, "pa­labra", r"U\+00AD"),
    ("cue", 1.0, None, None, "x" * (NAME_MAX + 1), "como mucho"),
    ("cue", 1.0, None, None, 42, "es un texto"),
])
def test_lo_invalido_no_se_escribe(base, kind, start, end, num, name, pista):
    db, rutas = base
    uno = rutas["uno.wav"]
    with Store(db) as store:
        antes = [_tupla(m) for m in store.list_cue_marks(uno)]
        with pytest.raises(InvalidCueMark, match=pista):
            store.add_cue_mark(uno, kind, start, end, num=num, name=name)
        assert [_tupla(m) for m in store.list_cue_marks(uno)] == antes, "escribió algo inválido"


def test_nombres_con_caracteres_raros_vuelven_iguales(base):
    """Lo que sí es un nombre (acentos, emoji con ZWJ, CJK, comillas, < > &) vuelve tal cual;
    solo se sacan los espacios de los bordes, y vacío es sin nombre."""
    db, rutas = base
    # El emoji con ZWJ (U+200D) y el persa con ZWNJ (U+200C) son Cf pero son texto real.
    nombres = ["Señor Coconut — «drop»", "👩‍🎤 vox", "東京 intro", "\"A\" & <B>",
               "می‌خواهم", "ü" * NAME_MAX]
    with Store(db) as store:
        for i, n in enumerate(nombres):
            store.add_cue_mark(rutas["uno.wav"], "memory", 10.0 + i, name=n)
        store.add_cue_mark(rutas["uno.wav"], "memory", 30.0, name="   ")
        store.add_cue_mark(rutas["uno.wav"], "memory", 31.0, name="  bordes  ")
    with Store(db) as store:
        leidos = [m.name for m in store.list_cue_marks(rutas["uno.wav"])]
    assert leidos == [*nombres, None, "bordes"]


def test_topes_de_memory_y_loops(base):
    db, rutas = base
    uno = rutas["uno.wav"]
    with Store(db) as store:
        for i in range(MAX_MEMORY):
            store.add_cue_mark(uno, "memory", float(i))
        with pytest.raises(InvalidCueMark, match=f"el tope es {MAX_MEMORY}"):
            store.add_cue_mark(uno, "memory", 100.0)
        for i in range(MAX_LOOPS):
            store.add_cue_mark(uno, "loop", float(i), i + 0.5)
        with pytest.raises(InvalidCueMark, match=f"el tope es {MAX_LOOPS}"):
            store.add_cue_mark(uno, "loop", 100.0, 101.0)
        # Los hot cues no cuentan para el tope de memory (son otro tipo).
        assert store.add_cue_mark(uno, "cue", 1.0).num == 0


def test_track_que_no_esta_en_la_biblioteca(base, tmp_path):
    db, _ = base
    with Store(db) as store, pytest.raises(CueMarkNotFound, match="no está en la biblioteca"):
        store.add_cue_mark(tmp_path / "otro.wav", "cue", 1.0)


# --- cambiar y borrar -------------------------------------------------------------------

def test_mover_renombrar_y_cambiar_de_pad(base):
    db, rutas = base
    uno = rutas["uno.wav"]
    with Store(db) as store:
        cue = store.add_cue_mark(uno, "cue", 10.0)
        otro = store.add_cue_mark(uno, "cue", 20.0)
        loop = store.add_cue_mark(uno, "loop", 30.0, 32.0)

        m = store.update_cue_mark(uno, cue.id, start_s=11.111, name="Intro")
        assert (m.start_ms, m.name, m.num, m.created_at) == (11111, "Intro", 0, cue.created_at)
        assert store.update_cue_mark(uno, cue.id, name="").name is None
        assert store.update_cue_mark(uno, cue.id, num=6).num == 6
        with pytest.raises(InvalidCueMark, match="hot cue 2 ya está puesto"):
            store.update_cue_mark(uno, cue.id, num=otro.num)
        m = store.update_cue_mark(uno, loop.id, end_s=36.5)
        assert (m.start_ms, m.end_ms) == (30000, 36500)
        # La marca resultante se valida entera: mover la entrada después de la salida no.
        with pytest.raises(InvalidCueMark, match="después de la entrada"):
            store.update_cue_mark(uno, loop.id, start_s=40.0)
        with pytest.raises(InvalidCueMark, match="solo un loop tiene salida"):
            store.update_cue_mark(uno, cue.id, end_s=50.0)
        with pytest.raises(InvalidCueMark, match="siempre tiene número"):
            store.update_cue_mark(uno, cue.id, num=None)
        with pytest.raises(InvalidCueMark, match="nada que cambiar"):
            store.update_cue_mark(uno, cue.id)
        for enorme in ({"start_s": 1e306}, {"end_s": 1e306}):
            with pytest.raises(InvalidCueMark, match="fuera de rango"):
                store.update_cue_mark(uno, loop.id, **enorme)
        with pytest.raises(InvalidCueMark, match=r"U\+200B"):
            store.update_cue_mark(uno, cue.id, name="x​")
        final = [(x.kind, x.num, x.start_ms, x.end_ms, x.name) for x in store.list_cue_marks(uno)]
    assert final == [("cue", 1, 20000, None, None), ("cue", 6, 11111, None, None),
                     ("loop", None, 30000, 36500, None)]


@pytest.mark.parametrize("mark_id", [10 ** 30, 2 ** 63, -(2 ** 63) - 1, 999_999, True, "1", 1.0])
def test_id_imposible_es_marca_inexistente(base, mark_id):
    db, rutas = base
    with Store(db) as store:
        store.add_cue_mark(rutas["uno.wav"], "cue", 1.0)
        with pytest.raises(CueMarkNotFound):
            store.update_cue_mark(rutas["uno.wav"], mark_id, start_s=2.0)
        with pytest.raises(CueMarkNotFound):
            store.delete_cue_mark(rutas["uno.wav"], mark_id)
        assert len(store.list_cue_marks(rutas["uno.wav"])) == 1


def test_borrar_solo_borra_esa_y_solo_de_ese_track(base):
    db, rutas = base
    with Store(db) as store:
        a = store.add_cue_mark(rutas["uno.wav"], "cue", 1.0)
        b = store.add_cue_mark(rutas["uno.wav"], "memory", 2.0)
        c = store.add_cue_mark(rutas["dos.wav"], "memory", 3.0)
        # El id de una marca de OTRO track no se borra desde este.
        with pytest.raises(CueMarkNotFound):
            store.delete_cue_mark(rutas["uno.wav"], c.id)
        store.delete_cue_mark(rutas["uno.wav"], a.id)
        with pytest.raises(CueMarkNotFound):
            store.delete_cue_mark(rutas["uno.wav"], a.id)
        assert [m.id for m in store.list_cue_marks(rutas["uno.wav"])] == [b.id]
        assert [m.id for m in store.list_cue_marks(rutas["dos.wav"])] == [c.id]
        # AUTOINCREMENT: el id de la borrada no se reusa.
        assert store.add_cue_mark(rutas["uno.wav"], "cue", 4.0).id > c.id


def test_conteos(base):
    db, rutas = base
    with Store(db) as store:
        _sembrar(store, rutas["uno.wav"])
        store.add_cue_mark(rutas["dos.wav"], "memory", 1.0)
        conteos = store.cue_mark_counts([rutas["uno.wav"], rutas["dos.wav"], rutas["tres.wav"]])
        assert conteos == {store._key(rutas["uno.wav"]): 4, store._key(rutas["dos.wav"]): 1}


# --- re-escaneo: las marcas no se pierden -------------------------------------------------

def test_reanalisis_y_baja_de_la_fila_no_tocan_las_marcas(base):
    """Lo que hace el scan con la fila del track: INSERT OR REPLACE al reanalizar (otro BPM,
    otra duración) y DELETE si hoy no está en disco. Ninguna de las dos se lleva una marca."""
    db, rutas = base
    uno = rutas["uno.wav"]
    with Store(db) as store:
        antes = [_tupla(m) for m in _sembrar(store, uno)]
        store.upsert(uno, TrackFeatures(bpm=131.7, key="9A", energy_raw=0.5,
                                        embedding=embedding(99)),
                     duration=250.0, license="x", source_url="y")
        assert store.get(uno).bpm == 131.7, "el reanálisis simulado no cambió la fila"
        assert [_tupla(m) for m in store.list_cue_marks(uno)] == antes
        assert store.orphan_cue_marks() == []

        assert store.delete(uno)
        assert [_tupla(m) for m in store.list_cue_marks(uno)] == antes
        assert store.orphan_cue_marks() == [(store._key(uno), 4)]
        # Sin la fila no hay duración contra la cual validar: no se marca, pero se puede
        # borrar una huérfana (es decisión del dueño).
        with pytest.raises(CueMarkNotFound):
            store.add_cue_mark(uno, "memory", 1.0)

        store.upsert(uno, TrackFeatures(bpm=128.4, key="8A", energy_raw=0.3,
                                        embedding=embedding(0)),
                     duration=240.0, license="x", source_url="y")
        assert [_tupla(m) for m in store.list_cue_marks(uno)] == antes
        assert store.orphan_cue_marks() == []


def _scan(db, carpeta):
    return main([str(a) for a in ("--db", db, "scan", carpeta, "--licencia", "CC0-1.0",
                                  "--origen", "motor/sintetico.py")])


def _escribir(ruta: Path, bpm: float, seed: int = 0) -> None:
    y, sr = click_track(bpm, dur=6.0, nota="A", modo="min", seed=seed)
    sf.write(str(ruta), y.astype(np.float32), sr, subtype="FLOAT")


def test_el_scan_real_no_se_lleva_las_marcas(tmp_path, capsys):
    """Con el `scan` de la CLI sobre audio sintético: el archivo cambia y se reanaliza, se
    borra y vuelve, se renombra. Las marcas siguen en la base en todos los casos; al moverlo
    quedan huérfanas (atadas a la ruta vieja) y el track nuevo arranca sin marcas: esa es la
    limitación documentada en `motor/cue_marks.py`, y este test la fija."""
    carpeta = tmp_path / "crate"
    carpeta.mkdir()
    ruta = carpeta / "tema.wav"
    _escribir(ruta, 124.0)
    db = tmp_path / "db.sqlite"
    assert _scan(db, carpeta) == 0
    ruta = ruta.resolve()
    with Store(db) as store:
        bpm_antes = store.get(ruta).bpm
        store.add_cue_mark(ruta, "cue", 1.5, name="entrada")
        store.add_cue_mark(ruta, "loop", 2.0, 3.0)
        antes = [_tupla(m) for m in store.list_cue_marks(ruta)]

    # 1. Otro audio en la misma ruta (otro BPM y mtime): el scan lo reanaliza.
    _escribir(ruta, 140.0, seed=3)
    os.utime(ruta, (1_000_000_000, 1_000_000_000))
    assert _scan(db, carpeta) == 0
    with Store(db) as store:
        assert abs(store.get(ruta).bpm - bpm_antes) > 5, "el scan no reanalizó el archivo"
        assert [_tupla(m) for m in store.list_cue_marks(ruta)] == antes

    # 2. El archivo desaparece: el scan saca la fila; las marcas quedan (huérfanas).
    guardado = ruta.read_bytes()
    ruta.unlink()
    assert _scan(db, carpeta) == 0
    with Store(db) as store:
        assert store.get(ruta) is None, "el scan no sacó el track que ya no está"
        assert [_tupla(m) for m in store.list_cue_marks(ruta)] == antes
        assert store.orphan_cue_marks() == [(store._key(ruta), 2)]

    # 3. Vuelve a su lugar: el track vuelve y con él sus marcas.
    ruta.write_bytes(guardado)
    assert _scan(db, carpeta) == 0
    with Store(db) as store:
        assert store.get(ruta) is not None
        assert [_tupla(m) for m in store.list_cue_marks(ruta)] == antes
        assert store.orphan_cue_marks() == []

    # 4. Renombrado: el track nuevo no tiene marcas y las viejas quedan huérfanas, no borradas.
    nueva = ruta.with_name("tema (renombrado).wav")
    ruta.rename(nueva)
    assert _scan(db, carpeta) == 0
    with Store(db) as store:
        assert store.get(nueva) is not None and store.list_cue_marks(nueva) == []
        assert [_tupla(m) for m in store.list_cue_marks(ruta)] == antes
        assert store.orphan_cue_marks() == [(store._key(ruta), 2)]
    capsys.readouterr()


# --- migración v4 → v5: atomicidad y concurrencia ---------------------------------------

def _base_v4(carpeta: Path) -> Path:
    """Una base v4 de verdad: la biblioteca, un set guardado con una calificación, y sin la
    tabla de marcas (el DDL de las tablas de la v4 es el mismo que hoy)."""
    from motor.radio import RadioConfig, build_set
    from motor.saved_sets import config_json, snapshot_steps

    db = carpeta / "v4.sqlite"
    _, rutas = armar_base_radio(carpeta / "musica", db)
    with Store(db) as store:
        lib = store.load_library()
        semilla = next(t for t in lib if t.path == rutas["uno.wav"])
        config = RadioConfig(length=3)
        rset = build_set(semilla, lib, config)
        set_id = store.save_set(snapshot_steps(rset), config=config_json(config), requested=3,
                                stop=rset.stop, stop_detail=rset.stop_detail,
                                fragments=rset.fragments, name="v4")
        store.rate_transition(set_id, 1, "ok")
    con = sqlite3.connect(str(db))
    con.execute("DROP TABLE cue_marks")
    con.execute("PRAGMA user_version = 4")
    con.commit()
    con.close()
    return db


def _tablas(db: Path) -> set[str]:
    con = sqlite3.connect(str(db))
    try:
        return {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def _filas_sets(db: Path) -> list:
    con = sqlite3.connect(str(db))
    try:
        return [sorted(con.execute(f"SELECT * FROM {t}").fetchall(), key=repr)
                for t in ("saved_sets", "saved_set_steps", "saved_set_ratings")]
    finally:
        con.close()


def test_base_v4_se_migra_y_conserva_biblioteca_y_sets(tmp_path):
    from motor.store import VERSION_ESQUEMA

    db = _base_v4(tmp_path)
    foto, sets = _foto(db), _filas_sets(db)
    assert foto[0] == 4 and "cue_marks" not in _tablas(db)
    with Store(db) as store:
        assert [s.name for s in store.list_saved_sets()] == ["v4"]
        assert store.add_cue_mark(store.paths()[0], "cue", 1.0).num == 0
    despues = _foto(db)
    assert (despues[0], VERSION_ESQUEMA) == (5, 5)
    assert despues[2:] == foto[2:], "la migración 5 tocó `tracks`"
    assert _filas_sets(db) == sets, "la migración 5 tocó los sets guardados"
    assert "cue_marks" in _tablas(db)


def test_migracion_5_que_falla_deja_la_base_v4_como_estaba(tmp_path, monkeypatch):
    import motor.store as modulo_store

    db = _base_v4(tmp_path)
    antes, sets = _foto(db), _filas_sets(db)

    def paso_5_que_falla(self):
        Store._migrar_a_5_cues(self)
        raise RuntimeError("falla inyectada al final de la migración 5")

    monkeypatch.setattr(modulo_store, "_MIGRACIONES", (*modulo_store._MIGRACIONES[:4],
                                                       (5, paso_5_que_falla)))
    with pytest.raises(RuntimeError, match="migración 5"):
        Store(db)
    assert _foto(db) == antes, "la migración 5 fallida dejó la base modificada"
    assert _filas_sets(db) == sets
    assert "cue_marks" not in _tablas(db), "quedó la tabla de una migración que falló"

    monkeypatch.undo()
    with Store(db) as store:
        assert store.list_cue_marks(store.paths()[0]) == [] and store.count() == len(CATALOGO)


def test_migracion_5_que_falla_al_escribir_la_version_deja_la_base_v4(tmp_path, monkeypatch):
    import motor.store as modulo_store

    db = _base_v4(tmp_path)
    antes = _foto(db)

    def paso_5_y_romper_la_version(self):
        Store._migrar_a_5_cues(self)
        self._con = _ConexionQueFalla(self._con, "PRAGMA user_version =")

    monkeypatch.setattr(modulo_store, "_MIGRACIONES", (*modulo_store._MIGRACIONES[:4],
                                                       (5, paso_5_y_romper_la_version)))
    with pytest.raises(RuntimeError, match="user_version"):
        Store(db)
    assert _foto(db) == antes, "la falla al escribir la versión dejó la base modificada"
    assert "cue_marks" not in _tablas(db)


def test_dos_aperturas_simultaneas_de_una_base_v4_migran_una_vez(tmp_path, monkeypatch):
    """A entra a la migración 5 y se frena; B abre la misma base mientras tanto. Los dos
    abren bien, la migración corre UNA vez y la base queda en v5."""
    import motor.store as modulo_store

    db = _base_v4(tmp_path)
    a_adentro, seguir_a = threading.Event(), threading.Event()
    migraron: list[str] = []

    def paso_5_que_se_frena_en_a(self):
        migraron.append(threading.current_thread().name)
        if threading.current_thread().name == "A":
            a_adentro.set()
            seguir_a.wait(10)
        Store._migrar_a_5_cues(self)

    monkeypatch.setattr(modulo_store, "_MIGRACIONES", (*modulo_store._MIGRACIONES[:4],
                                                       (5, paso_5_que_se_frena_en_a)))
    resultados: dict[str, object] = {}

    def abrir(nombre):
        try:
            with Store(db) as store:
                resultados[nombre] = (store.count(), store.orphan_cue_marks())
        except Exception as e:  # noqa: BLE001 — el resultado ES la excepción
            resultados[nombre] = e

    hilo_a = threading.Thread(target=abrir, args=("A",), name="A")
    hilo_a.start()
    assert a_adentro.wait(10), "A nunca llegó a la migración"
    hilo_b = threading.Thread(target=abrir, args=("B",), name="B")
    hilo_b.start()
    hilo_b.join(0.5)
    assert hilo_b.is_alive(), f"B no esperó a que A terminara de migrar: {resultados}"
    seguir_a.set()
    hilo_a.join(60)
    hilo_b.join(60)

    n = len(CATALOGO)
    assert resultados == {"A": (n, []), "B": (n, [])}, f"aperturas simultáneas: {resultados}"
    assert migraron == ["A"], f"la migración corrió en {migraron}"
    assert _foto(db)[0] == 5
