"""Tests de las marcas del dueño (f48): hot cues, memory cues y loops en el store (esquema v6).

Biblioteca de `tests/sinteticos.py` (la misma que prueban la API y el E2E). Las duraciones
salen del CATALOGO (lo que declara la base), no de este archivo: "uno.wav" dura 240 s porque
así lo escribió `armar_base_radio`.

Lo central:
- la migración v4 → v5 es atómica y concurrente (como la 4), y la v5 → v6 (hot loops)
  reconstruye la tabla sin perder nada: ni filas, ni ids, ni fechas, ni el contador de ids;
- las validaciones rechazan ANTES de escribir y no dejan nada a medias;
- el pad es único por track entre hot cues y hot loops, también con pedidos a la vez;
- los colores salen de UNA tabla (`cue_marks.color_de_marca`): el loop, siempre naranja;
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
        with pytest.raises(InvalidCueMark, match="pad 2 ya lo usa un hot cue"):
            store.add_cue_mark(uno, "cue", 30.0, num=1)
        for _ in range(HOT_CUES - 4):
            store.add_cue_mark(uno, "cue", 40.0)
        assert sorted(m.num for m in store.list_cue_marks(uno)) == list(range(HOT_CUES))
        with pytest.raises(InvalidCueMark, match="ya tiene los 8 pads ocupados"):
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


# --- hot loops (v6): un loop puede vivir en un pad ---------------------------------------

def test_hot_loop_ocupa_su_pad_y_el_pad_es_unico(base):
    """Rekordbox guarda loops en los pads A-H (`Type=4`, `Num` 0..7: PRUEBA_CUES.md §5). Un
    loop con `num` ocupa ESE pad; sin `num` es un memory loop y no toma ninguno. El pad es UNO
    por track entre hot cues y hot loops, y el motivo dice quién lo usa."""
    db, rutas = base
    uno = rutas["uno.wav"]
    with Store(db) as store:
        hot = store.add_cue_mark(uno, "loop", 30.0, 34.0, num=2, name="Drop loop")
        assert (hot.kind, hot.num, hot.start_ms, hot.end_ms) == ("loop", 2, 30000, 34000)
        memoria = store.add_cue_mark(uno, "loop", 50.0, 52.0)
        assert memoria.num is None, "un loop sin pad pedido se mandó solo a un pad"
        with pytest.raises(InvalidCueMark, match="pad 3 ya lo usa un loop"):
            store.add_cue_mark(uno, "cue", 1.0, num=2)
        with pytest.raises(InvalidCueMark, match="pad 3 ya lo usa un loop"):
            store.add_cue_mark(uno, "loop", 60.0, 61.0, num=2)
        store.add_cue_mark(uno, "cue", 5.0, num=0)
        with pytest.raises(InvalidCueMark, match="pad 1 ya lo usa un hot cue"):
            store.add_cue_mark(uno, "loop", 60.0, 61.0, num=0)
        # El «primer pad libre» de un hot cue saltea el pad del hot loop.
        assert store.add_cue_mark(uno, "cue", 6.0).num == 1
        assert store.add_cue_mark(uno, "cue", 7.0).num == 3
        for n in (4, 5, 6):
            store.add_cue_mark(uno, "cue", 8.0 + n, num=n)
        store.add_cue_mark(uno, "loop", 70.0, 72.0, num=7)
        with pytest.raises(InvalidCueMark, match="ya tiene los 8 pads ocupados"):
            store.add_cue_mark(uno, "cue", 90.0)
        # El mismo pad en OTRO track sí.
        assert store.add_cue_mark(rutas["dos.wav"], "loop", 3.0, 4.0, num=2).num == 2
    with Store(db) as store:                      # otra conexión: es lo que quedó en la base
        marcas = [(m.kind, m.num, m.start_ms) for m in store.list_cue_marks(uno)]
    # Orden de la tabla: los 8 pads por número (cues y hot loops), después lo que no tiene pad.
    assert marcas == [("cue", 0, 5000), ("cue", 1, 6000), ("loop", 2, 30000), ("cue", 3, 7000),
                      ("cue", 4, 12000), ("cue", 5, 13000), ("cue", 6, 14000),
                      ("loop", 7, 70000), ("loop", None, 50000)], marcas


def test_un_loop_toma_y_suelta_un_pad(base):
    """PATCH de `num`: un memory loop pasa a un pad libre y vuelve a soltarlo (None); no puede
    ir a un pad ocupado. Una memory cue no toma pad y un hot cue no lo suelta."""
    db, rutas = base
    uno = rutas["uno.wav"]
    with Store(db) as store:
        loop = store.add_cue_mark(uno, "loop", 30.0, 34.0)
        cue = store.add_cue_mark(uno, "cue", 1.0, num=5)
        mem = store.add_cue_mark(uno, "memory", 2.0)
        m = store.update_cue_mark(uno, loop.id, num=4)
        assert (m.num, m.start_ms, m.end_ms, m.created_at) == (4, 30000, 34000, loop.created_at)
        with pytest.raises(InvalidCueMark, match="pad 5 ya lo usa un loop"):
            store.update_cue_mark(uno, cue.id, num=4)
        with pytest.raises(InvalidCueMark, match="pad 6 ya lo usa un hot cue"):
            store.update_cue_mark(uno, loop.id, num=5)
        with pytest.raises(InvalidCueMark, match="una memory cue no lleva pad"):
            store.update_cue_mark(uno, mem.id, num=1)
        with pytest.raises(InvalidCueMark, match="siempre tiene número"):
            store.update_cue_mark(uno, cue.id, num=None)
        assert store.update_cue_mark(uno, loop.id, num=None).num is None
        # El pad que soltó el loop quedó libre de verdad.
        assert store.update_cue_mark(uno, cue.id, num=4).num == 4
        final = [(x.kind, x.num, x.start_ms) for x in store.list_cue_marks(uno)]
    assert final == [("cue", 4, 1000), ("memory", None, 2000), ("loop", None, 30000)], final


def test_la_base_repite_las_reglas_del_pad(base):
    """Los CHECK de la v6, con filas escritas por fuera del store: un hot cue sin pad, una
    memory con pad o con salida y un loop sin salida no entran; un loop con pad sí, y su pad
    choca con el de un hot cue (índice único)."""
    db, rutas = base
    with Store(db) as store:
        store.add_cue_mark(rutas["uno.wav"], "cue", 1.0, num=2)
        clave = store._key(rutas["uno.wav"])
    con = sqlite3.connect(str(db))
    sql = ("INSERT INTO cue_marks (path_key, kind, num, start_ms, end_ms, created_at, "
           "updated_at) VALUES (?, ?, ?, 5000, ?, 'x', 'x')")
    try:
        for kind, num, fin in (("cue", None, None), ("memory", 1, None), ("memory", None, 6000),
                               ("loop", 3, None), ("loop", None, None), ("cue", 4, 6000)):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                con.execute(sql, (clave, kind, num, fin))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            con.execute(sql, (clave, "loop", 2, 6000))
        con.execute(sql, (clave, "loop", 3, 6000))       # hot loop en un pad libre: entra
        con.execute(sql, (clave, "loop", None, 6000))    # memory loop: entra
        assert con.execute("SELECT kind, num, end_ms FROM cue_marks WHERE path_key = ? "
                           "ORDER BY id", (clave,)).fetchall() == \
            [("cue", 2, None), ("loop", 3, 6000), ("loop", None, 6000)]
    finally:
        con.close()


# --- colores: UNA tabla para pantalla y export ---------------------------------------------

# Los colores que pidió el dueño (2026-10-09), escritos a mano: si la tabla del motor cambia,
# este test lo ve.
COLORES_PEDIDOS = ["#ff4d5a", "#34d17c", "#4fa3ff", "#ffd23f", "#b48cff", "#2fd4cf", "#ff7ab8",
                   "#f2f3f8"]


def test_colores_de_las_marcas():
    """Hot cues A..H con el color pedido; el loop SIEMPRE naranja, con o sin pad (no toma el
    color de su pad); la memory cue sin color propio al exportar y neutra en pantalla."""
    from motor.cue_marks import (
        COLOR_MEMORY_PANTALLA,
        color_de_marca,
        color_hex,
        color_hex_de_marca,
    )

    assert [color_hex_de_marca("cue", n) for n in range(HOT_CUES)] == COLORES_PEDIDOS
    assert color_de_marca("cue", 0) == (255, 77, 90)
    assert {color_hex_de_marca("loop", n) for n in (None, *range(HOT_CUES))} == {"#ff9a2e"}
    assert color_de_marca("loop") == (255, 154, 46)
    assert (color_de_marca("memory"), color_hex_de_marca("memory")) == (None, None)
    assert color_hex(COLOR_MEMORY_PANTALLA) == "#f2f3f8"
    for kind, num, pista in (("cue", None, "siempre tiene pad"), ("cue", 8, "de 0 a 7"),
                             ("memory", 0, "no lleva pad"), ("loop", -1, "de 0 a 7"),
                             ("loop", True, "de 0 a 7"), ("hot", 0, "tipo de marca")):
        with pytest.raises(InvalidCueMark, match=pista):
            color_de_marca(kind, num)


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
    ("memory", 1.0, None, 3, None, "una memory cue no lleva pad"),
    ("loop", 1.0, 2.0, 8, None, "de 0 a 7"),
    ("loop", 1.0, 2.0, True, None, "de 0 a 7"),
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
    ("cue", 1.0, None, None, "línea\u2028", r"U\+2028"),
    ("cue", 1.0, None, None, "al revés \u202e", r"U\+202E"),
    # Invisibles de formato (Cf): dos nombres que se ven iguales y no lo son.
    ("cue", 1.0, None, None, "a\u200bb", r"U\+200B"),
    ("cue", 1.0, None, None, "\ufeffintro", r"U\+FEFF"),
    ("cue", 1.0, None, None, "drop\u2060", r"U\+2060"),
    ("cue", 1.0, None, None, "pa\u00adlabra", r"U\+00AD"),
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
    nombres = ["Señor Coconut — «drop»", "👩\u200d🎤 vox", "東京 intro", "\"A\" & <B>",
               "می\u200cخواهم", "ü" * NAME_MAX]
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
        with pytest.raises(InvalidCueMark, match="pad 2 ya lo usa un hot cue"):
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
            store.update_cue_mark(uno, cue.id, name="x\u200b")
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
    assert (despues[0], VERSION_ESQUEMA) == (6, 6)
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
                                                       (5, paso_5_que_falla),
                                                       *modulo_store._MIGRACIONES[5:]))
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
                                                       (5, paso_5_y_romper_la_version),
                                                       *modulo_store._MIGRACIONES[5:]))
    with pytest.raises(RuntimeError, match="user_version"):
        Store(db)
    assert _foto(db) == antes, "la falla al escribir la versión dejó la base modificada"
    assert "cue_marks" not in _tablas(db)


def test_dos_aperturas_simultaneas_de_una_base_v4_migran_una_vez(tmp_path, monkeypatch):
    """A entra a la migración 5 y se frena; B abre la misma base mientras tanto. Los dos
    abren bien, la migración corre UNA vez y la base queda en la versión actual."""
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
                                                       (5, paso_5_que_se_frena_en_a),
                                                       *modulo_store._MIGRACIONES[5:]))
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
    assert _foto(db)[0] == 6


# --- migración v5 → v6: hot loops (la tabla se reconstruye) ---------------------------------

# El DDL de `cue_marks` de la v5 (2a0b586), tal cual: lo que tiene hoy la base del dueño.
_DDL_CUES_V5 = (
    """CREATE TABLE cue_marks (
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
    "CREATE INDEX idx_cue_marks_path ON cue_marks(path_key)",
    "CREATE UNIQUE INDEX idx_cue_marks_hot ON cue_marks(path_key, num) WHERE num IS NOT NULL",
)


def _base_v5(carpeta: Path) -> tuple[Path, dict]:
    """Una base v5 de verdad: la biblioteca, un set guardado con una calificación y marcas de
    las TRES clases (hot cues, memory cues y loops, con nombres y fechas distintas), una
    huérfana (de un archivo que no está en la biblioteca), ids con huecos (marcas que se
    borraron en el medio: una copia que renumerara se nota) y la última marca BORRADA: el
    contador de ids (9) queda por encima del id más alto que queda (8)."""
    db = _base_v4(carpeta)
    con = sqlite3.connect(str(db))
    for ddl in _DDL_CUES_V5:
        con.execute(ddl)
    uno, dos = (con.execute("SELECT path_key FROM tracks WHERE path LIKE ?", (f"%{n}",))
                .fetchone()[0] for n in ("uno.wav", "dos.wav"))
    huerfana = os.path.normcase(str(carpeta / "se_movio.wav"))
    filas = [
        (1, uno, "cue", 0, 12345, None, "Drop", "2026-10-01T10:00:00Z", "2026-10-02T11:30:00Z"),
        (2, uno, "memory", None, 3500, None, None, "2026-10-01T10:01:00Z", "2026-10-01T10:01:00Z"),
        (3, uno, "loop", None, 64000, 71500, "4 compases", "2026-10-01T10:02:00Z",
         "2026-10-03T09:00:00Z"),
        (5, dos, "cue", 7, 1000, None, None, "2026-10-04T08:00:00Z", "2026-10-04T08:00:00Z"),
        (8, huerfana, "memory", None, 2000, None, "vieja", "2026-09-30T23:59:59Z",
         "2026-09-30T23:59:59Z"),
        (9, uno, "memory", None, 9000, None, None, "2026-10-05T12:00:00Z", "2026-10-05T12:00:00Z"),
    ]
    con.executemany("INSERT INTO cue_marks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", filas)
    con.execute("DELETE FROM cue_marks WHERE id = 9")
    con.execute("PRAGMA user_version = 5")
    con.commit()
    con.close()
    return db, {"uno": uno, "dos": dos, "huerfana": huerfana}


def _marcas_crudas(db: Path) -> tuple[list, int | None, list]:
    """Las filas de `cue_marks` enteras, el contador de AUTOINCREMENT y los índices."""
    con = sqlite3.connect(str(db))
    try:
        filas = con.execute("SELECT * FROM cue_marks ORDER BY id").fetchall()
        seq = con.execute("SELECT seq FROM sqlite_sequence WHERE name = 'cue_marks'").fetchone()
        indices = sorted(con.execute("SELECT name FROM sqlite_master WHERE type = 'index' "
                                     "AND tbl_name = 'cue_marks'").fetchall())
        return filas, None if seq is None else seq[0], indices
    finally:
        con.close()


def _ruta_de(store, nombre: str) -> Path:
    return next(p for p in store.paths() if p.name == nombre)


def test_base_v5_se_migra_a_v6_sin_perder_marcas(tmp_path):
    """v5 → v6: la tabla se reconstruye (SQLite no altera un CHECK) y queda TODO igual —cada
    fila con su id, nombre y fechas de creación y modificación, el contador de ids y los
    índices—, la biblioteca y los sets ni se tocan, y recién ahí un loop puede tener pad. El
    id de la marca borrada (9) NO se reusa."""
    from motor.store import VERSION_ESQUEMA

    db, claves = _base_v5(tmp_path)
    foto, sets = _foto(db), _filas_sets(db)
    filas, seq, indices = _marcas_crudas(db)
    assert (foto[0], seq, [f[0] for f in filas]) == (5, 9, [1, 2, 3, 5, 8]), (foto[0], seq, filas)
    con = sqlite3.connect(str(db))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):   # la v5 no acepta hot loops
            con.execute("INSERT INTO cue_marks (path_key, kind, num, start_ms, end_ms, "
                        "created_at, updated_at) VALUES ('x', 'loop', 3, 1, 2, 'x', 'x')")
    finally:
        con.close()

    with Store(db) as store:
        nueva = store.add_cue_mark(_ruta_de(store, "uno.wav"), "loop", 100.0, 104.0, num=3)
        assert (nueva.id, nueva.num) == (10, 3), "se reusó el id de una marca borrada"
        assert store.orphan_cue_marks() == [(claves["huerfana"], 1)]

    despues = _foto(db)
    assert (despues[0], VERSION_ESQUEMA) == (6, 6)
    assert despues[2:] == foto[2:], "la migración 6 tocó `tracks`"
    assert _filas_sets(db) == sets, "la migración 6 tocó los sets guardados"
    filas_despues, seq_despues, indices_despues = _marcas_crudas(db)
    assert filas_despues[:-1] == filas, "la migración 6 cambió alguna marca"
    assert filas_despues[-1][:6] == (10, claves["uno"], "loop", 3, 100000, 104000)
    assert (seq_despues, indices_despues) == (10, indices), (seq_despues, indices_despues)


def test_migracion_6_es_idempotente(tmp_path):
    """Correr el paso 6 otra vez sobre una tabla que ya es v6 la deja igual: mismas filas,
    mismo contador, mismos índices, y el pad sigue siendo único."""
    db, claves = _base_v5(tmp_path)
    with Store(db) as store:
        uno = _ruta_de(store, "uno.wav")
        store.add_cue_mark(uno, "loop", 1.0, 2.0, num=6)
        antes = _marcas_crudas(db)
        for _ in range(2):
            with store._escritura():
                store._migrar_a_6_hot_loops()
        assert _marcas_crudas(db) == antes
        with pytest.raises(InvalidCueMark, match="pad 7 ya lo usa un loop"):
            store.add_cue_mark(uno, "cue", 3.0, num=6)
    con = sqlite3.connect(str(db))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            con.execute("INSERT INTO cue_marks (path_key, kind, num, start_ms, created_at, "
                        "updated_at) VALUES (?, 'cue', 0, 5, 'x', 'x')", (claves["uno"],))
    finally:
        con.close()


def test_migracion_6_que_falla_deja_la_base_v5_como_estaba(tmp_path, monkeypatch):
    """Si el paso 6 falla DESPUÉS de reconstruir la tabla, el ROLLBACK deja la v5 entera: el
    DDL viejo (con sus CHECK: `_foto` compara el SQL de cada objeto), las filas, el contador
    y la versión."""
    import motor.store as modulo_store

    db, _ = _base_v5(tmp_path)
    antes, marcas = _foto(db), _marcas_crudas(db)

    def paso_6_que_falla(self):
        Store._migrar_a_6_hot_loops(self)
        raise RuntimeError("falla inyectada al final de la migración 6")

    monkeypatch.setattr(modulo_store, "_MIGRACIONES", (*modulo_store._MIGRACIONES[:5],
                                                       (6, paso_6_que_falla)))
    with pytest.raises(RuntimeError, match="migración 6"):
        Store(db)
    assert _foto(db) == antes, "la migración 6 fallida dejó la base modificada"
    assert _marcas_crudas(db) == marcas

    monkeypatch.undo()
    with Store(db) as store:
        assert [m.id for m in store.list_cue_marks(_ruta_de(store, "uno.wav"))] == [1, 2, 3]
    assert _foto(db)[0] == 6
