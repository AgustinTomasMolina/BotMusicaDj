"""Tests de la caché de análisis y la biblioteca (motor/store.py + motor/modelos.py).

Los embeddings son vectores armados a mano a propósito: para el store son datos opacos
(los guarda y los normaliza, no los interpreta), y hechos a mano se puede saber exactamente
qué tiene que salir. El BPM, la tonalidad y la energía NO: salen de `motor/sintetico.py` y
de las funciones que los miden (spec §5, "nunca inventar BPM ni tonalidad en un test").
"""
import os
import sqlite3
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from motor.embeddings import DIM, normalize_matrix  # noqa: E402
from motor.energia import energia_rms  # noqa: E402
from motor.modelos import Track, TrackFeatures  # noqa: E402
from motor.sintetico import click_track  # noqa: E402
from motor.store import EMB_DTYPE, Store  # noqa: E402
from motor.tonalidad import tono  # noqa: E402

LICENCIA = "CC-BY-4.0"
ORIGEN = "https://ejemplo.test/track"


def _vector(i: int) -> np.ndarray:
    """Embedding sintético i-ésimo: DIM float32 distintos entre sí y deterministas.
    Dos dimensiones prendidas alcanzan para que ninguna fila normalizada quede igual a otra
    (si quedaran iguales, un test de alineación no podría distinguirlas)."""
    v = np.zeros(DIM, dtype=EMB_DTYPE)
    v[i % DIM] = 1.0
    v[(i * 7 + 3) % DIM] = 0.5
    return v


@lru_cache(maxsize=1)
def _catalogo() -> tuple[dict, ...]:
    """Cinco tracks con BPM, tonalidad y energía de GROUND TRUTH, no inventados:

    - el BPM es el del generador (`click_track` lo construye exacto por definición);
    - la tonalidad se detecta con `tono` sobre ese mismo audio y se verifica contra la nota
      y el modo con los que se generó — si no coincidieran, el ground truth de estos tests
      no aplicaría y el assert lo dice;
    - la energía es el RMS MEDIDO de la señal escalada por una ganancia distinta en cada
      track, para que el orden de energía sea conocido y no coincida con el de las rutas.

    Se arma una sola vez por sesión: son cinco análisis de tonalidad reales.
    """
    specs = [(124.0, "C", "min", 0.2), (128.0, "A", "min", 1.0), (130.0, "D", "maj", 0.6),
             (134.0, "F#", "min", 0.4), (140.0, "G", "maj", 0.8)]
    catalogo = []
    for i, (bpm, nota, modo, ganancia) in enumerate(specs):
        y, sr = click_track(bpm, dur=10, nota=nota, modo=modo, seed=i)
        k = tono(y, sr)
        assert (k["nota"], k["modo"]) == (nota, modo), \
            f"el sintético se generó en {nota} {modo} y se detectó {k['nota']} {k['modo']}: " \
            f"el ground truth de tonalidad de estos tests no aplica"
        catalogo.append({
            "nombre": f"{i}_{bpm:.0f}.wav",
            "bpm": bpm,                       # ground truth del generador
            "key": k["camelot"],              # medido sobre el mismo audio
            "energy_raw": energia_rms(y * ganancia),
            "rms": energia_rms(y),   # el de la señal SIN escalar: otro valor medido, distinto
            "ganancia": ganancia,
            "embedding": _vector(i),
        })
    return tuple(catalogo)


def _features(c: dict) -> TrackFeatures:
    return TrackFeatures(bpm=c["bpm"], key=c["key"], energy_raw=c["energy_raw"],
                         embedding=c["embedding"])


def _poblar(store: Store, carpeta: Path, indices, orden=None) -> dict:
    """Crea los archivos del catálogo y los mete en el store. `orden` permite insertarlos
    en un orden distinto al de las rutas (para probar que el store no depende de eso).
    Devuelve {nombre: ruta}."""
    rutas = {}
    for i in indices:
        c = _catalogo()[i]
        ruta = carpeta / c["nombre"]
        ruta.write_bytes(b"no es audio de verdad: el store guarda el analisis, no el archivo")
        rutas[c["nombre"]] = ruta
    for i in (indices if orden is None else orden):
        c = _catalogo()[i]
        store.upsert(rutas[c["nombre"]], _features(c), duration=10.0,
                     license=LICENCIA, source_url=ORIGEN, artist="JPH", title=f"Track {i}")
    return rutas


# --- round-trip ----------------------------------------------------------------------

def test_round_trip_exacto(tmp_path):
    """Lo que entra a la caché es idénticamente lo que sale, embedding incluido (mismo
    dtype y mismos valores). Si el BLOB se leyera con otro dtype no habría excepción:
    saldrían números plausibles y equivocados."""
    c = _catalogo()[2]
    ruta = tmp_path / c["nombre"]
    ruta.write_bytes(b"x")
    # onset_rate / percussive_ratio son payload opaco para el store (no los calcula ni los
    # interpreta): lo que se verifica es que no se pierdan. `rms` es el RMS de la señal sin
    # escalar y `energy_raw` el de la escalada: distintos a propósito, porque con los dos
    # iguales un store que confundiera las dos columnas pasaría el test igual.
    feats = TrackFeatures(bpm=c["bpm"], key=c["key"], energy_raw=c["energy_raw"],
                          embedding=c["embedding"], rms=c["rms"],
                          onset_rate=0.125, percussive_ratio=0.75)

    store = Store(tmp_path / "db.sqlite")
    store.upsert(ruta, feats, duration=612.5, license=LICENCIA, source_url=ORIGEN,
                 artist="Fran Perrotta", title="Hipnótico")

    got = store.get_features(ruta)
    assert got.bpm == c["bpm"], f"bpm {got.bpm} != {c['bpm']}"
    assert got.key == c["key"], f"key {got.key!r} != {c['key']!r}"
    assert got.energy_raw == c["energy_raw"], f"energy_raw {got.energy_raw} != {c['energy_raw']}"
    assert got.rms == c["rms"], f"rms {got.rms} != {c['rms']} (¿se cruzó con energy_raw?)"
    assert got.onset_rate == 0.125, f"onset_rate {got.onset_rate} != 0.125"
    assert got.percussive_ratio == 0.75, f"percussive_ratio {got.percussive_ratio} != 0.75"
    assert got.embedding.dtype == EMB_DTYPE, f"el embedding volvió como {got.embedding.dtype}"
    assert np.array_equal(got.embedding, c["embedding"]), \
        f"el embedding volvió distinto: {got.embedding[:5]} vs {c['embedding'][:5]}"
    assert got == feats, "el TrackFeatures que salió no es el que entró"

    t = store.get(ruta)
    assert t.path == ruta, f"path {t.path} != {ruta}"
    assert t.duration == 612.5, f"duration {t.duration} != 612.5"
    assert (t.bpm, t.key) == (c["bpm"], c["key"]), f"bpm/key {(t.bpm, t.key)}"
    assert (t.artist, t.title) == ("Fran Perrotta", "Hipnótico"), f"tags {(t.artist, t.title)}"
    assert t.license == LICENCIA, f"license {t.license!r}"
    assert t.source_url == ORIGEN, f"source_url {t.source_url!r}"
    assert t.label == "Fran Perrotta — Hipnótico", f"label {t.label!r}"
    store.close()


# --- mtime ---------------------------------------------------------------------------

def test_mtime_decide_si_se_reanaliza(tmp_path):
    """La decisión bajo prueba es la del re-scan: un archivo sin cambios NO se vuelve a
    analizar (y conserva lo analizado), uno con mtime distinto SÍ. Se verifica por lo que
    quedó guardado, no porque el método se haya llamado."""
    viejo, cambiado = _catalogo()[0], _catalogo()[2]
    store = Store(tmp_path / "db.sqlite")
    rutas = {}
    for c in (viejo, cambiado):
        rutas[c["nombre"]] = tmp_path / c["nombre"]
        rutas[c["nombre"]].write_bytes(b"v1")

    analizados = []

    def scan(features_por_nombre):
        for nombre, ruta in sorted(rutas.items()):
            if store.needs_analysis(ruta):
                analizados.append(nombre)
                store.upsert(ruta, features_por_nombre[nombre], duration=10.0,
                             license=LICENCIA, source_url=ORIGEN)

    v1 = {viejo["nombre"]: _features(viejo), cambiado["nombre"]: _features(cambiado)}
    scan(v1)
    assert analizados == [viejo["nombre"], cambiado["nombre"]], \
        f"la primera pasada tenía que analizar los dos, analizó {analizados}"

    # Segunda pasada, sin tocar nada: la caché está vigente para los dos.
    scan(v1)
    assert analizados == [viejo["nombre"], cambiado["nombre"]], \
        f"reanalizó archivos que no cambiaron: {analizados}"

    # Ahora uno cambia de verdad (otro contenido, otro mtime) y se reanaliza con OTRO BPM,
    # también de ground truth. El que no cambió tiene que conservar el suyo.
    otro = _catalogo()[4]
    ruta_cambiada = rutas[cambiado["nombre"]]
    ruta_cambiada.write_bytes(b"v2: otro contenido")
    os.utime(ruta_cambiada, (0, 0))
    scan({viejo["nombre"]: _features(viejo), cambiado["nombre"]: _features(otro)})
    assert analizados == [viejo["nombre"], cambiado["nombre"], cambiado["nombre"]], \
        f"la tercera pasada tenía que analizar solo el que cambió, analizó {analizados}"

    assert store.get_features(rutas[viejo["nombre"]]).bpm == viejo["bpm"], \
        "el track que no cambió perdió su análisis"
    assert store.get_features(ruta_cambiada).bpm == otro["bpm"], \
        "el track que cambió conservó el análisis viejo"
    assert store.count() == 2, f"un reanálisis duplicó filas: {store.count()}"
    store.close()


def test_needs_analysis_sin_archivo_y_sin_fila(tmp_path):
    """Lo que no está en la caché hay que analizarlo; y si el archivo desapareció, la
    caché no se puede dar por vigente (no hay mtime contra qué comparar)."""
    c = _catalogo()[3]
    ruta = tmp_path / c["nombre"]
    ruta.write_bytes(b"x")
    store = Store(tmp_path / "db.sqlite")
    assert store.needs_analysis(ruta) is True, "un track que no está en la caché está al día"

    store.upsert(ruta, _features(c), duration=10.0, license=LICENCIA, source_url=ORIGEN)
    assert store.needs_analysis(ruta) is False, "recién analizado y dice que hay que rehacerlo"

    ruta.unlink()
    assert store.needs_analysis(ruta) is True, \
        "el archivo ya no existe y el store dice que la caché sigue vigente"
    store.close()


# --- matrix --------------------------------------------------------------------------

def test_matrix_alineada_con_las_rutas(tmp_path):
    """El bug caro de esta capa: si la fila `i` no es la de `paths[i]`, el motor recomienda
    el track equivocado y no hay ningún síntoma. Los tracks se insertan en un orden y se
    esperan en otro (el de las rutas), y la matriz esperada se arma aparte con
    `normalize_matrix`, no con el store."""
    store = Store(tmp_path / "db.sqlite")
    _poblar(store, tmp_path, indices=[0, 1, 2, 3], orden=[2, 0, 3, 1])

    m, paths = store.matrix()

    esperado_nombres = sorted(_catalogo()[i]["nombre"] for i in range(4))
    assert [p.name for p in paths] == esperado_nombres, \
        f"el orden no es el de las rutas: {[p.name for p in paths]}"
    assert m.shape == (4, DIM), f"shape {m.shape}"

    por_nombre = {_catalogo()[i]["nombre"]: _catalogo()[i]["embedding"] for i in range(4)}
    cruda = np.vstack([por_nombre[n] for n in esperado_nombres]).astype(np.float64)
    esperada, _, _ = normalize_matrix(cruda)
    for i, nombre in enumerate(esperado_nombres):
        assert np.allclose(m[i], esperada[i], rtol=0, atol=1e-12), \
            f"la fila {i} no es la de {nombre}: {m[i][:4]} vs {esperada[i][:4]}"

    normas = np.linalg.norm(m, axis=1)
    assert np.allclose(normas, 1.0, rtol=0, atol=1e-12), \
        f"las filas no tienen norma 1, el producto punto no sería coseno: {normas}"

    # Y cada fila es la de SU track: `get` normaliza por otro camino (normalize_one con las
    # stats guardadas) y tiene que caer exactamente en la misma fila.
    for i, p in enumerate(paths):
        assert np.allclose(store.get(p).embedding, m[i], rtol=0, atol=1e-12), \
            f"get({p.name}) no coincide con la fila {i} de matrix()"
    store.close()


def test_matrix_es_determinista_entre_bases(tmp_path):
    """Spec §5: mismo contenido → mismo resultado. Dos bases con los mismos tracks
    insertados en orden distinto tienen que dar la MISMA matriz, bit a bit."""
    a = Store(tmp_path / "a.sqlite")
    b = Store(tmp_path / "b.sqlite")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _poblar(a, tmp_path / "a", indices=[0, 1, 2], orden=[0, 1, 2])
    _poblar(b, tmp_path / "b", indices=[0, 1, 2], orden=[2, 1, 0])

    ma, pa = a.matrix()
    mb, pb = b.matrix()
    assert [p.name for p in pa] == [p.name for p in pb], f"{pa} vs {pb}"
    assert np.array_equal(ma, mb), f"máxima diferencia {np.abs(ma - mb).max()}"
    a.close()
    b.close()


def test_la_base_guarda_el_embedding_crudo(tmp_path):
    """Decisión 2 del esquema: el BLOB es el vector SIN normalizar. Si se guardara
    normalizado, cada track nuevo cambiaría la media de la biblioteca e invalidaría las
    filas de todos los demás. Se lee la base directamente, no a través del store."""
    db = tmp_path / "db.sqlite"
    store = Store(db)
    rutas = _poblar(store, tmp_path, indices=[0, 1, 2])
    m, paths = store.matrix()
    store.close()

    con = sqlite3.connect(str(db))
    for i in range(3):
        c = _catalogo()[i]
        blob = con.execute("SELECT embedding FROM tracks WHERE path = ?",
                           (str(rutas[c["nombre"]]),)).fetchone()[0]
        guardado = np.frombuffer(blob, dtype=EMB_DTYPE)
        assert np.array_equal(guardado, c["embedding"]), \
            f"{c['nombre']}: el BLOB no es el embedding crudo ({guardado[:4]})"
    con.close()

    fila = m[[p.name for p in paths].index(_catalogo()[0]["nombre"])]
    assert not np.allclose(fila, _catalogo()[0]["embedding"], rtol=0, atol=1e-6), \
        "la fila normalizada es igual al vector crudo: o no se normaliza, o se guardó normalizado"


# --- energía -------------------------------------------------------------------------

def test_energia_se_guarda_cruda_y_sale_en_percentil(tmp_path):
    """La energía absoluta no dice nada; la relativa al crate sí (spec §7). Con cinco
    tracks de energía conocida, el percentil de cada uno es su posición: el más energético
    0.8 (el 80% de la biblioteca está por debajo) y el más tranquilo 0.0."""
    store = Store(tmp_path / "db.sqlite")
    rutas = _poblar(store, tmp_path, indices=[0, 1, 2, 3, 4])

    # El orden de energía lo fija la ganancia con la que se generó cada track.
    por_ganancia = sorted(range(5), key=lambda i: _catalogo()[i]["ganancia"])
    esperado = {_catalogo()[idx]["nombre"]: pos / 5.0 for pos, idx in enumerate(por_ganancia)}

    energias = {t.path.name: t.energy for t in store.load_library()}
    assert energias == esperado, f"percentiles {energias} != {esperado}"

    mas = _catalogo()[por_ganancia[-1]]
    menos = _catalogo()[por_ganancia[0]]
    assert energias[mas["nombre"]] == 0.8, f"el más energético dio {energias[mas['nombre']]}"
    assert energias[menos["nombre"]] == 0.0, f"el más tranquilo dio {energias[menos['nombre']]}"
    assert store.get(rutas[mas["nombre"]]).energy == 0.8, "get() no percentila igual que load_library()"

    # Y lo que quedó en la caché es el valor CRUDO, no el percentil: si se guardara el
    # percentil, agregar un track cambiaría la energía de todos los ya guardados.
    assert store.get_features(rutas[mas["nombre"]]).energy_raw == mas["energy_raw"], \
        "la energía guardada no es el RMS crudo"
    store.close()


# --- licencia y origen ---------------------------------------------------------------

def test_licencia_y_origen_son_obligatorios(tmp_path):
    """Spec §5: `licencia` y `origen` son obligatorios en cualquier modelo de track desde
    el primer día. No alcanza con un comentario: sin ellos no se construye el Track ni se
    persiste la fila."""
    base = dict(path=tmp_path / "x.wav", duration=10.0, bpm=128.0, key="8A", energy=0.5,
                embedding=np.zeros(DIM, dtype=EMB_DTYPE))

    with pytest.raises(TypeError) as e:
        Track(**base)
    assert "license" in str(e.value) and "source_url" in str(e.value), \
        f"faltan los dos y el error no los nombra: {e.value}"

    for campo in ("license", "source_url"):
        for vacio in (None, "", "   "):
            with pytest.raises(ValueError) as e:
                Track(**base, **{campo: vacio,
                                 "source_url" if campo == "license" else "license": "ok"})
            assert campo in str(e.value) and "obligatorio" in str(e.value), \
                f"{campo}={vacio!r} pasó o falló con otro motivo: {e.value}"

    # Y en la frontera de la caché, que es por donde entran los tracks de verdad.
    ruta = tmp_path / _catalogo()[0]["nombre"]
    ruta.write_bytes(b"x")
    store = Store(tmp_path / "db.sqlite")
    feats = _features(_catalogo()[0])
    with pytest.raises(TypeError):
        store.upsert(ruta, feats, duration=10.0)
    with pytest.raises(ValueError) as e:
        store.upsert(ruta, feats, duration=10.0, license="", source_url=ORIGEN)
    assert "license" in str(e.value), f"{e.value}"
    with pytest.raises(ValueError) as e:
        store.upsert(ruta, feats, duration=10.0, license=LICENCIA, source_url="  ")
    assert "source_url" in str(e.value), f"{e.value}"
    assert store.count() == 0, "quedó persistido un track sin licencia u origen"
    store.close()


def test_energy_fuera_de_0_1_no_se_acepta():
    """`Track.energy` es percentil 0..1 y `energia.percentil` devuelve 0..100: el error
    natural es olvidarse de dividir, y ahí el motor vería un track 80 veces más energético
    que el máximo posible."""
    base = dict(path=Path("x.wav"), duration=10.0, bpm=128.0, key="8A",
                embedding=np.zeros(DIM, dtype=EMB_DTYPE), license=LICENCIA, source_url=ORIGEN)
    for malo in (80.0, -0.1, 1.5):
        with pytest.raises(ValueError) as e:
            Track(**base, energy=malo)
        assert "percentil" in str(e.value), f"energy={malo} falló por otro motivo: {e.value}"
    assert Track(**base, energy=1.0).energy == 1.0, "el extremo válido 1.0 tiene que entrar"
    assert Track(**base, energy=0.0).energy == 0.0, "el extremo válido 0.0 tiene que entrar"


def test_bpm_no_finito_no_entra_ni_por_track_ni_por_la_cache(tmp_path):
    """H1 (tarea 1.2): con `bpm=inf` la compuerta daba mezclabilidad NaN y el track entraba
    al set con score NaN. `inf`/NaN se rechazan al construir el `Track` y en `upsert` (si
    entraran a la caché, `load_library` fallaría para TODA la biblioteca al leer esa fila).

    El 0.0 tiene que seguir entrando: es lo que el análisis real devuelve sobre silencio. El
    test no lo supone, lo MIDE (`bpm_refinado` sobre 30 s de ceros) y usa ese valor."""
    from motor.bpm import bpm_refinado

    base = dict(path=Path("x.wav"), duration=10.0, key="8A", energy=0.5,
                embedding=np.zeros(DIM, dtype=EMB_DTYPE), license=LICENCIA, source_url=ORIGEN)
    for malo in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError) as e:
            Track(**base, bpm=malo)
        assert "bpm" in str(e.value), f"bpm={malo} falló por otro motivo: {e.value}"

    bpm_silencio = bpm_refinado(np.zeros(22050 * 30, dtype=np.float32), 22050)
    assert bpm_silencio == 0.0, f"el análisis del silencio ya no da 0.0 sino {bpm_silencio!r}"
    assert Track(**base, bpm=bpm_silencio).bpm == 0.0, "un track mudo tiene que poder cargarse"

    store = Store(tmp_path / "db.sqlite")
    ruta = tmp_path / "a.wav"
    ruta.write_bytes(b"x")
    feats = _features(_catalogo()[0])
    feats.bpm = float("inf")
    with pytest.raises(ValueError, match="bpm"):
        store.upsert(ruta, feats, duration=10.0, license=LICENCIA, source_url=ORIGEN)
    assert store.count() == 0, "quedó persistido un track con BPM infinito"
    feats.bpm = bpm_silencio
    store.upsert(ruta, feats, duration=10.0, license=LICENCIA, source_url=ORIGEN)
    assert [t.bpm for t in store.load_library()] == [0.0]
    store.close()


# --- bordes --------------------------------------------------------------------------

def test_base_vacia(tmp_path):
    """Una biblioteca sin tracks no puede explotar: la matriz tiene 0 filas pero DIM
    columnas, así que `matriz @ v` sigue siendo una consulta válida que no devuelve nada."""
    store = Store(tmp_path / "db.sqlite")
    m, paths = store.matrix()
    assert m.shape == (0, DIM), f"shape {m.shape}"
    assert paths == [], f"paths {paths}"
    assert (m @ np.ones(DIM)).shape == (0,), "una consulta contra la biblioteca vacía no anda"
    assert store.load_library() == [], "hay tracks en una base vacía"
    assert store.count() == 0
    assert store.get(tmp_path / "no.wav") is None, "devolvió un track que no existe"
    assert store.get_features(tmp_path / "no.wav") is None
    assert store.delete(tmp_path / "no.wav") is False, "dijo haber borrado algo que no estaba"
    store.refresh_norm_stats()  # sin tracks no hay media que calcular: no puede tirar NaN
    assert store.matrix()[0].shape == (0, DIM)
    store.close()


def test_un_solo_track(tmp_path):
    """Con un track, cada dimensión es constante → desvío 0. `normalize_matrix` lo sanea a
    1.0 y la fila queda en ceros (no NaN): no hay 'lejos' ni 'cerca' con un solo track."""
    store = Store(tmp_path / "db.sqlite")
    rutas = _poblar(store, tmp_path, indices=[2])

    m, paths = store.matrix()
    assert m.shape == (1, DIM), f"shape {m.shape}"
    assert np.isfinite(m).all(), f"{int((~np.isfinite(m)).sum())} valores no finitos"
    assert np.array_equal(m[0], np.zeros(DIM)), f"la única fila tenía que quedar en 0: {m[0][:4]}"
    assert paths[0] == rutas[_catalogo()[2]["nombre"]]

    t = store.get(paths[0])
    assert np.array_equal(t.embedding, np.zeros(DIM)), f"get() dio {t.embedding[:4]}"
    assert t.energy == 0.0, f"con un solo track el percentil es 0.0, dio {t.energy}"
    assert t.bpm == _catalogo()[2]["bpm"], "se perdió el bpm"
    store.close()


def test_upsert_dos_veces_actualiza_y_no_duplica(tmp_path):
    """La ruta es la clave. Un reanálisis pisa el anterior; si insertara una fila nueva, la
    biblioteca tendría el mismo track dos veces y con dos análisis distintos."""
    viejo, nuevo = _catalogo()[0], _catalogo()[3]
    ruta = tmp_path / viejo["nombre"]
    ruta.write_bytes(b"x")
    store = Store(tmp_path / "db.sqlite")

    store.upsert(ruta, _features(viejo), duration=10.0, license=LICENCIA, source_url=ORIGEN,
                 title="antes")
    store.upsert(ruta, _features(nuevo), duration=20.0, license=LICENCIA, source_url=ORIGEN,
                 title="después")

    assert store.count() == 1, f"quedaron {store.count()} filas para la misma ruta"
    got = store.get_features(ruta)
    assert got.bpm == nuevo["bpm"], f"bpm {got.bpm}, quedó el análisis viejo"
    assert got.key == nuevo["key"], f"key {got.key}"
    assert np.array_equal(got.embedding, nuevo["embedding"]), "quedó el embedding viejo"
    t = store.get(ruta)
    assert (t.title, t.duration) == ("después", 20.0), f"metadatos viejos: {(t.title, t.duration)}"
    assert store.matrix()[0].shape == (1, DIM), "la matriz tiene el track repetido"
    store.close()


def test_rutas_unicode(tmp_path):
    """Nombres con acentos, kanji y guiones largos: la clave primaria es la ruta y tiene que
    volver idéntica, no en mojibake."""
    c = _catalogo()[1]
    nombre = "電子 — ñandú café (Señor Coconut Édit).wav"
    ruta = tmp_path / nombre
    ruta.write_bytes(b"x")
    store = Store(tmp_path / "db.sqlite")
    store.upsert(ruta, _features(c), duration=10.0, license=LICENCIA, source_url=ORIGEN)

    assert store.get_features(ruta).bpm == c["bpm"], "no encontró el track por su propia ruta"
    t = store.get(ruta)
    assert t.path == ruta, f"la ruta volvió distinta: {t.path!r} vs {ruta!r}"
    assert t.label == "電子 — ñandú café (Señor Coconut Édit)", f"label {t.label!r}"
    _, paths = store.matrix()
    assert [p.name for p in paths] == [nombre], f"paths {paths}"
    assert store.needs_analysis(ruta) is False, "no reconoce el archivo unicode como analizado"
    store.close()


def test_delete_saca_el_track_de_la_biblioteca(tmp_path):
    """Para archivos que ya no están. La matriz tiene que quedar sin esa fila y alineada."""
    store = Store(tmp_path / "db.sqlite")
    rutas = _poblar(store, tmp_path, indices=[0, 1, 2])
    fuera = rutas[_catalogo()[1]["nombre"]]

    assert store.delete(fuera) is True, "no borró un track que estaba"
    assert store.delete(fuera) is False, "dijo haber borrado dos veces el mismo track"
    assert store.count() == 2, f"quedaron {store.count()} tracks"
    assert store.get(fuera) is None, "sigue devolviendo el track borrado"

    m, paths = store.matrix()
    esperados = sorted(_catalogo()[i]["nombre"] for i in (0, 2))
    assert [p.name for p in paths] == esperados, f"paths {[p.name for p in paths]}"
    assert m.shape == (2, DIM), f"shape {m.shape}"
    por_nombre = {_catalogo()[i]["nombre"]: _catalogo()[i]["embedding"] for i in (0, 2)}
    esperada, _, _ = normalize_matrix(
        np.vstack([por_nombre[n] for n in esperados]).astype(np.float64))
    assert np.allclose(m, esperada, rtol=0, atol=1e-12), \
        "después de borrar, la matriz no se renormalizó contra la biblioteca que quedó"
    store.close()


def test_abrir_dos_veces_la_misma_base_no_la_pisa(tmp_path):
    """El SCHEMA es idempotente: reabrir la base conserva lo analizado (si no, cada corrida
    reanalizaría la biblioteca entera)."""
    db = tmp_path / "db.sqlite"
    store = Store(db)
    rutas = _poblar(store, tmp_path, indices=[0, 4])
    store.close()

    otra = Store(db)
    assert otra.count() == 2, f"al reabrir quedaron {otra.count()} tracks"
    assert otra.get_features(rutas[_catalogo()[4]["nombre"]]).bpm == _catalogo()[4]["bpm"], \
        "al reabrir se perdió el análisis"
    assert otra.needs_analysis(rutas[_catalogo()[0]["nombre"]]) is False, \
        "al reabrir cree que hay que reanalizar todo"
    otra.close()


def test_lo_no_medido_vuelve_none_y_no_cero(tmp_path):
    """Spec §6: un dato que miente es peor que uno ausente. `TrackFeatures` sin rms,
    onset_rate ni percussive_ratio es "no se midió": en la base queda NULL y vuelve None,
    no 0.0 (que se leería como "sin percusión", "sin onsets")."""
    c = _catalogo()[1]
    ruta = tmp_path / c["nombre"]
    ruta.write_bytes(b"x")
    db = tmp_path / "db.sqlite"
    store = Store(db)
    store.upsert(ruta, _features(c), duration=10.0, license=LICENCIA, source_url=ORIGEN)
    got = store.get_features(ruta)
    store.close()

    assert (got.rms, got.onset_rate, got.percussive_ratio) == (None, None, None), \
        f"lo no medido volvió como {(got.rms, got.onset_rate, got.percussive_ratio)}"
    con = sqlite3.connect(str(db))
    crudo = con.execute("SELECT rms, onset_rate, percussive_ratio FROM tracks").fetchone()
    con.close()
    assert crudo == (None, None, None), f"en la base quedó {crudo} en vez de NULL"


# --- stats de normalización: invalidación y cinturón --------------------------------

def _fila_fresca(store: Store, ruta: Path) -> np.ndarray:
    """La fila de `ruta` en un `matrix()` recalculado AHORA, sobre la biblioteca actual."""
    m, paths = store.matrix()
    return m[paths.index(ruta)]


def test_alta_nueva_normaliza_con_las_stats_nuevas(tmp_path):
    """`matrix()` guarda stats; un `upsert` de un track nuevo mueve la media y el desvío de
    la biblioteca. `get(nuevo)` tiene que normalizar con las stats NUEVAS: con las viejas
    el vector cae en otra escala y las similitudes contra la matriz mienten."""
    store = Store(tmp_path / "db.sqlite")
    rutas = _poblar(store, tmp_path, indices=[0, 1, 2])
    store.matrix()                                     # guarda stats de 3 tracks
    nuevo = _poblar(store, tmp_path, indices=[3])[_catalogo()[3]["nombre"]]

    got = store.get(nuevo).embedding                   # ANTES de recalcular a mano
    esperada = _fila_fresca(store, nuevo)
    assert np.allclose(got, esperada, rtol=0, atol=1e-12), \
        f"get() normalizó con stats viejas: {got[:4]} vs {esperada[:4]}"
    viejo = rutas[_catalogo()[0]["nombre"]]
    assert np.allclose(store.get(viejo).embedding, _fila_fresca(store, viejo), rtol=0, atol=1e-12)
    store.close()


def test_reanalisis_de_la_misma_ruta_invalida_las_stats(tmp_path):
    """El caso que el cinturón de `count` NO ve: reanalizar una ruta que ya estaba cambia
    las stats sin cambiar la cantidad de tracks. Solo la invalidación en `upsert` lo cubre."""
    store = Store(tmp_path / "db.sqlite")
    rutas = _poblar(store, tmp_path, indices=[0, 1, 2])
    store.matrix()
    ruta = rutas[_catalogo()[1]["nombre"]]
    otro = _catalogo()[4]                              # embedding distinto, misma ruta
    store.upsert(ruta, _features(otro), duration=10.0, license=LICENCIA, source_url=ORIGEN)
    assert store.count() == 3, "el caso necesita que la cantidad NO cambie"

    got = store.get(ruta).embedding
    esperada = _fila_fresca(store, ruta)
    assert np.allclose(got, esperada, rtol=0, atol=1e-12), \
        f"get() normalizó con las stats de antes del reanálisis: {got[:4]} vs {esperada[:4]}"
    store.close()


def test_baja_normaliza_con_las_stats_nuevas(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    rutas = _poblar(store, tmp_path, indices=[0, 1, 2, 3])
    store.matrix()
    store.delete(rutas[_catalogo()[2]["nombre"]])
    queda = rutas[_catalogo()[0]["nombre"]]

    got = store.get(queda).embedding
    esperada = _fila_fresca(store, queda)
    assert np.allclose(got, esperada, rtol=0, atol=1e-12), \
        f"después de un delete, get() normalizó con stats viejas: {got[:4]} vs {esperada[:4]}"
    store.close()


def test_stats_con_count_desfasado_se_recalculan(tmp_path):
    """Cinturón: una escritura que no pasó por `upsert`/`delete` (otra versión del código,
    un DELETE a mano) deja stats guardadas con un `count` que ya no es el de la tabla.
    `get` tiene que notarlo por `count` y recalcular."""
    db = tmp_path / "db.sqlite"
    store = Store(db)
    rutas = _poblar(store, tmp_path, indices=[0, 1, 2, 3])
    store.matrix()
    store.close()

    con = sqlite3.connect(str(db))                     # por fuera del store: no invalida
    con.execute("DELETE FROM tracks WHERE path = ?", (str(rutas[_catalogo()[3]["nombre"]]),))
    con.commit()
    assert con.execute("SELECT count FROM norm_stats").fetchone()[0] == 4, \
        "el caso necesita stats guardadas de 4 tracks"
    con.close()

    store = Store(db)
    queda = rutas[_catalogo()[1]["nombre"]]
    got = store.get(queda).embedding
    esperada = _fila_fresca(store, queda)
    assert np.allclose(got, esperada, rtol=0, atol=1e-12), \
        f"stats de 4 tracks usadas sobre una biblioteca de 3: {got[:4]} vs {esperada[:4]}"
    store.close()


# --- la clave de la ruta ------------------------------------------------------------

_DISTINGUE_MAYUSCULAS = os.path.normcase("A") == "A"


@pytest.mark.skipif(_DISTINGUE_MAYUSCULAS,
                    reason="en este sistema normcase no iguala mayúsculas: son dos archivos")
def test_la_clave_no_distingue_mayusculas_en_windows(tmp_path):
    r"""`C:\Musica\a.wav` y `c:\musica\a.wav` son el mismo archivo en Windows: una sola
    fila, encontrable con cualquiera de las dos grafías. Antes eran dos filas y el mismo
    track entraba dos veces a la biblioteca."""
    c, otro = _catalogo()[0], _catalogo()[3]
    ruta = tmp_path / "Carpeta" / "Tema Uno.wav"
    ruta.parent.mkdir()
    ruta.write_bytes(b"x")
    grito = Path(str(ruta).upper())
    chico = Path(str(ruta).lower())
    store = Store(tmp_path / "db.sqlite")

    store.upsert(ruta, _features(c), duration=10.0, license=LICENCIA, source_url=ORIGEN)
    store.upsert(grito, _features(otro), duration=10.0, license=LICENCIA, source_url=ORIGEN)
    assert store.count() == 1, f"quedaron {store.count()} filas para el mismo archivo"
    assert store.get_features(chico).bpm == otro["bpm"], "no encontró el track por otra grafía"
    assert store.needs_analysis(chico) is False, "otra grafía de la ruta se ve como sin analizar"
    # La ruta devuelta es USABLE: la última grafía recibida, no la clave en minúsculas.
    assert [str(p) for p in store.paths()] == [str(grito)], f"paths {store.paths()}"
    assert store.load_library()[0].path.exists()
    assert store.delete(chico) is True and store.count() == 0, "delete por otra grafía no borró"
    store.close()


def test_la_clave_es_la_ruta_absoluta(tmp_path, monkeypatch):
    """Una ruta relativa y la absoluta del mismo archivo son la misma fila, y lo que se
    devuelve es la absoluta (una relativa deja de servir apenas cambia el cwd)."""
    c = _catalogo()[2]
    (tmp_path / "rel.wav").write_bytes(b"x")
    store = Store(tmp_path / "db.sqlite")
    monkeypatch.chdir(tmp_path)
    store.upsert("rel.wav", _features(c), duration=10.0, license=LICENCIA, source_url=ORIGEN)
    monkeypatch.chdir(tmp_path.parent)
    assert store.get_features(tmp_path / "rel.wav").bpm == c["bpm"], "la relativa no es la absoluta"
    assert [str(p) for p in store.paths()] == [str(tmp_path / "rel.wav")], f"{store.paths()}"
    store.close()


def test_el_orden_no_depende_de_las_mayusculas(tmp_path):
    """Determinismo (§5): mismo contenido, mismo orden. Ordenando por la ruta cruda, 'B.wav'
    va antes que 'a.wav' (las mayúsculas ordenan primero) y escribir 'b.wav' lo da vuelta."""
    ordenes = []
    for n, nombres in enumerate((["a.wav", "B.wav"], ["A.wav", "b.wav"])):
        carpeta = tmp_path / f"base{n}"
        carpeta.mkdir()
        store = Store(carpeta / "db.sqlite")
        for i, nombre in enumerate(nombres):
            store.upsert(carpeta / nombre, _features(_catalogo()[i]), duration=10.0,
                         license=LICENCIA, source_url=ORIGEN, mtime=1.0)
        ordenes.append([p.name.lower() for p in store.paths()])
        store.close()
    assert ordenes[0] == ordenes[1] == ["a.wav", "b.wav"], f"órdenes {ordenes}"


@pytest.mark.skipif(_DISTINGUE_MAYUSCULAS,
                    reason="en este sistema normcase no iguala mayúsculas: no hay colisión")
def test_base_vieja_se_migra_y_colapsa_duplicados(tmp_path):
    """Una base de antes de `path_key` puede tener el mismo archivo dos veces con distinta
    grafía. Al abrirla queda UNA fila — la del análisis más reciente — con la clave llena."""
    db = tmp_path / "vieja.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("""CREATE TABLE tracks (path TEXT PRIMARY KEY, mtime REAL NOT NULL,
        duration REAL NOT NULL, artist TEXT, title TEXT, bpm REAL NOT NULL, key TEXT NOT NULL,
        energy_raw REAL NOT NULL, embedding BLOB NOT NULL, rms REAL, onset_rate REAL,
        percussive_ratio REAL, license TEXT NOT NULL, source_url TEXT NOT NULL,
        analyzed_at TEXT NOT NULL)""")
    ruta = tmp_path / "Tema.wav"
    for grafia, c, cuando in ((str(ruta), _catalogo()[0], "2026-01-01T00:00:00"),
                              (str(ruta).lower(), _catalogo()[1], "2026-02-01T00:00:00")):
        con.execute("INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (grafia, 1.0, 10.0, None, None, c["bpm"], c["key"], c["energy_raw"],
                     c["embedding"].tobytes(), None, None, None, LICENCIA, ORIGEN, cuando))
    con.commit()
    con.close()

    store = Store(db)
    assert store.count() == 1, f"la migración dejó {store.count()} filas del mismo archivo"
    assert store.get_features(ruta).bpm == _catalogo()[1]["bpm"], "no quedó el análisis más nuevo"
    store.close()
    Store(db).close()                                  # idempotente: reabrir no rompe


# --- versión del esquema (PRAGMA user_version) --------------------------------------------

# El SCHEMA de `motor/store.py` en el commit 9440251 (`git show 9440251:motor/store.py`),
# copiado tal cual: rms, onset_rate y percussive_ratio NOT NULL, sin path_key, sin versión.
SCHEMA_9440251 = """
CREATE TABLE IF NOT EXISTS tracks (
    path            TEXT PRIMARY KEY,
    mtime           REAL NOT NULL,      -- para invalidar la caché si cambió el archivo
    duration        REAL NOT NULL,
    artist          TEXT,
    title           TEXT,
    bpm             REAL NOT NULL,
    key             TEXT NOT NULL,      -- Camelot
    energy_raw      REAL NOT NULL,      -- RMS absoluto; el percentil se calcula al leer
    embedding       BLOB NOT NULL,      -- float32 crudo, sin normalizar
    rms             REAL NOT NULL,      -- detalle de energía (debug / reajuste de pesos)
    onset_rate      REAL NOT NULL,
    percussive_ratio REAL NOT NULL,
    license         TEXT NOT NULL,      -- obligatorio (spec §5)
    source_url      TEXT NOT NULL,      -- obligatorio (spec §5)
    analyzed_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm);
CREATE INDEX IF NOT EXISTS idx_tracks_key ON tracks(key);

-- Estadísticas de normalización de la biblioteca (media y desvío por dimensión).
-- Se invalidan con cada alta/baja y se recalculan cuando alguien las necesita.
CREATE TABLE IF NOT EXISTS norm_stats (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    mean    BLOB NOT NULL,
    std     BLOB NOT NULL,
    count   INTEGER NOT NULL
);
"""


def test_base_del_esquema_9440251_se_migra_y_conserva_sus_datos(tmp_path):
    """Una base de 9440251 abierta con el store actual: sus filas vuelven idénticas, la
    versión queda en la actual y — el bug — un upsert con rms/onset_rate/percussive_ratio en
    None ya no tira IntegrityError."""
    from motor.store import VERSION_ESQUEMA

    db = tmp_path / "vieja.sqlite"
    rutas = {}
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA_9440251)
    for i in (0, 1):
        c = _catalogo()[i]
        ruta = tmp_path / c["nombre"]
        ruta.write_bytes(b"x")
        rutas[i] = ruta
        con.execute(
            "INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(ruta), 111.0 + i, 10.0 + i, f"Artista {i}", f"Título {i}", c["bpm"], c["key"],
             c["energy_raw"], c["embedding"].tobytes(), c["rms"], 2.5 + i, 0.25 * i,
             LICENCIA, ORIGEN, f"2026-01-0{i + 1}T00:00:00"))
    con.commit()
    con.close()

    store = Store(db)
    for i, ruta in rutas.items():
        c = _catalogo()[i]
        got = store.get_features(ruta)
        assert (got.bpm, got.key, got.energy_raw) == (c["bpm"], c["key"], c["energy_raw"]), got
        assert np.array_equal(got.embedding, c["embedding"]), f"{ruta.name}: embedding perdido"
        assert (got.rms, got.onset_rate, got.percussive_ratio) == (c["rms"], 2.5 + i, 0.25 * i)
        t = store.get(ruta)
        assert (t.artist, t.title, t.duration) == (f"Artista {i}", f"Título {i}", 10.0 + i)

    nuevo = tmp_path / _catalogo()[2]["nombre"]
    nuevo.write_bytes(b"x")
    store.upsert(nuevo, _features(_catalogo()[2]), duration=10.0, license=LICENCIA,
                 source_url=ORIGEN)                     # rms & cía en None: el INSERT del bug
    assert store.count() == 3
    store.close()

    con = sqlite3.connect(str(db))
    version = con.execute("PRAGMA user_version").fetchone()[0]
    estrictas = {f[1] for f in con.execute("PRAGMA table_info(tracks)") if f[3]}
    filas_mtime = dict(con.execute("SELECT path, mtime FROM tracks").fetchall())
    con.close()
    assert version == VERSION_ESQUEMA, f"la base quedó en versión {version}"
    assert not estrictas & {"rms", "onset_rate", "percussive_ratio"}, estrictas
    assert filas_mtime[str(rutas[0])] == 111.0 and filas_mtime[str(rutas[1])] == 112.0


def test_base_con_version_futura_se_rechaza_sin_modificarla(tmp_path):
    from motor.store import EsquemaIncompatible

    db = tmp_path / "futura.sqlite"
    store = Store(db)
    _poblar(store, tmp_path, indices=[0])
    store.close()
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA user_version = 7")
    con.commit()
    con.close()
    antes = db.read_bytes()

    with pytest.raises(EsquemaIncompatible, match="versión 7"):
        Store(db)
    assert db.read_bytes() == antes, "la base de versión desconocida se modificó al abrirla"


def test_base_nueva_nace_con_la_version_actual(tmp_path):
    from motor.store import VERSION_ESQUEMA

    db = tmp_path / "nueva.sqlite"
    Store(db).close()
    con = sqlite3.connect(str(db))
    assert con.execute("PRAGMA user_version").fetchone()[0] == VERSION_ESQUEMA
    con.close()


# --- migración: atomicidad y concurrencia (hallazgos H2 y H3, tarea 1.2) --------------------

def _base_9440251(carpeta: Path) -> Path:
    """Base con el esquema de 9440251 (versión 0) y dos filas del catálogo."""
    db = carpeta / "v0.sqlite"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA_9440251)
    for i in (0, 1):
        c = _catalogo()[i]
        ruta = carpeta / c["nombre"]
        ruta.write_bytes(b"x")
        con.execute(
            "INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(ruta), 111.0 + i, 10.0 + i, f"Artista {i}", f"Título {i}", c["bpm"], c["key"],
             c["energy_raw"], c["embedding"].tobytes(), c["rms"], 2.5 + i, 0.25 * i,
             LICENCIA, ORIGEN, f"2026-01-0{i + 1}T00:00:00"))
    con.commit()
    con.close()
    return db


def _foto(db: Path) -> tuple:
    """Todo lo que una migración puede tocar: versión, objetos del esquema con su SQL,
    columnas de `tracks` (nombre, tipo, NOT NULL) y las filas completas."""
    con = sqlite3.connect(str(db))
    try:
        return (
            con.execute("PRAGMA user_version").fetchone()[0],
            sorted(con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall(),
                   key=repr),
            [tuple(f)[1:4] for f in con.execute("PRAGMA table_info(tracks)")],
            sorted(con.execute("SELECT * FROM tracks").fetchall(), key=repr),
        )
    finally:
        con.close()


class _ConexionQueFalla:
    """Envuelve la conexión del store y hace fallar UNA sentencia elegida. `sqlite3.Connection`
    es un tipo de C y no se le puede pisar `execute`, por eso el envoltorio."""

    def __init__(self, con: sqlite3.Connection, prefijo: str) -> None:
        self._real, self._prefijo = con, prefijo

    def execute(self, sql, *args):
        if sql.strip().startswith(self._prefijo):
            raise RuntimeError(f"falla inyectada en {sql.strip()!r}")
        return self._real.execute(sql, *args)

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)


def _reabrir_y_verificar(db: Path, carpeta: Path) -> None:
    """Después del fallo, una apertura normal migra bien y conserva los datos."""
    from motor.store import VERSION_ESQUEMA

    store = Store(db)
    try:
        assert store.count() == 2
        for i in (0, 1):
            c = _catalogo()[i]
            got = store.get_features(carpeta / c["nombre"])
            assert (got.bpm, got.key, got.rms) == (c["bpm"], c["key"], c["rms"]), got
            assert np.array_equal(got.embedding, c["embedding"])
    finally:
        store.close()
    con = sqlite3.connect(str(db))
    assert con.execute("PRAGMA user_version").fetchone()[0] == VERSION_ESQUEMA
    con.close()


def test_migracion_que_falla_en_el_paso_2_deja_la_base_como_estaba(tmp_path, monkeypatch):
    """H3: los pasos de `_MIGRACIONES` corren en UNA transacción. Si el paso 2 falla DESPUÉS
    de hacer su trabajo (y después de que el paso 1 recreó la tabla), la base tiene que
    quedar con el esquema, la versión y las filas de antes, no a medio migrar."""
    import motor.store as modulo_store

    db = _base_9440251(tmp_path)
    antes = _foto(db)

    def paso_2_que_falla(self):
        Store._migrar_a_2_path_key(self)
        raise RuntimeError("falla inyectada al final de la migración 2")

    monkeypatch.setattr(modulo_store, "_MIGRACIONES",
                        ((1, Store._migrar_a_1_nulos), (2, paso_2_que_falla)))
    with pytest.raises(RuntimeError, match="migración 2"):
        Store(db)
    assert _foto(db) == antes, "la migración fallida dejó la base modificada"

    monkeypatch.undo()
    _reabrir_y_verificar(db, tmp_path)


def test_migracion_que_falla_al_escribir_la_version_deja_la_base_como_estaba(tmp_path, monkeypatch):
    """H3: el `PRAGMA user_version` final es parte de la MISMA transacción. Si falla ahí —
    con las dos migraciones ya hechas — tampoco puede quedar una base migrada con versión
    vieja (la próxima apertura re-migraría algo que ya cambió de forma)."""
    import motor.store as modulo_store

    db = _base_9440251(tmp_path)
    antes = _foto(db)

    def paso_2_y_romper_la_version(self):
        Store._migrar_a_2_path_key(self)
        self._con = _ConexionQueFalla(self._con, "PRAGMA user_version =")

    monkeypatch.setattr(modulo_store, "_MIGRACIONES",
                        ((1, Store._migrar_a_1_nulos), (2, paso_2_y_romper_la_version)))
    with pytest.raises(RuntimeError, match="user_version"):
        Store(db)
    assert _foto(db) == antes, "la falla al escribir la versión dejó la base modificada"

    monkeypatch.undo()
    _reabrir_y_verificar(db, tmp_path)


def test_dos_aperturas_simultaneas_de_una_base_vieja_no_se_pisan(tmp_path, monkeypatch):
    """H2: dos procesos abriendo a la vez una base de esquema viejo. Antes el segundo moría
    con `database is locked` AL INSTANTE (sin esperar el timeout): el primero leía dentro de
    un `BEGIN` diferido, el segundo migraba y pedía el lock para confirmar, y cuando el
    primero pedía el de escritura SQLite detectaba el abrazo mutuo y lo tiraba.

    Se reproduce de forma determinista con dos hilos: A entra a la migración, lee y se
    frena; B abre la misma base mientras tanto; después A sigue. Los dos tienen que abrir
    bien y la base tiene que quedar migrada con sus datos."""
    import threading

    import motor.store as modulo_store

    db = _base_9440251(tmp_path)
    a_adentro, seguir_a = threading.Event(), threading.Event()
    migraron: list[str] = []

    def paso_1_que_se_frena_en_a(self):
        migraron.append(threading.current_thread().name)
        if threading.current_thread().name == "A":
            self._con.execute("SELECT COUNT(*) FROM tracks").fetchone()   # lee dentro de la tx
            a_adentro.set()
            seguir_a.wait(10)
        Store._migrar_a_1_nulos(self)

    monkeypatch.setattr(modulo_store, "_MIGRACIONES",
                        ((1, paso_1_que_se_frena_en_a), (2, Store._migrar_a_2_path_key)))
    resultados: dict[str, object] = {}

    def abrir(nombre):
        try:
            store = Store(db)
            resultados[nombre] = store.count()
            store.close()
        except Exception as e:  # noqa: BLE001 — el resultado ES la excepción
            resultados[nombre] = e

    hilo_a = threading.Thread(target=abrir, args=("A",), name="A")
    hilo_a.start()
    assert a_adentro.wait(10), "A nunca llegó a la migración"
    hilo_b = threading.Thread(target=abrir, args=("B",), name="B")
    hilo_b.start()
    hilo_b.join(0.5)                 # B llega a la base mientras A la tiene a medio migrar
    seguir_a.set()
    hilo_a.join(60)
    hilo_b.join(60)

    assert resultados == {"A": 2, "B": 2}, f"aperturas simultáneas: {resultados}"
    # B tiene que releer la versión con el lock tomado y ver que A ya migró: los pasos no
    # pueden correr dos veces (hoy son idempotentes, pero nada obliga a que el próximo lo sea).
    assert migraron == ["A"], f"la migración corrió en {migraron}"
    monkeypatch.undo()
    _reabrir_y_verificar(db, tmp_path)


# --- confianza de la key: acuerdo entre tramos (tarea 17) ---------------------------------

# El esquema de la versión 2 (`git show 6036031:motor/store.py`): igual al actual pero sin
# `key_acuerdo` ni `key_tramos`. Es la base que tiene hoy cualquiera que ya escaneó su
# biblioteca, y la que la migración 3 tiene que poder abrir.
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS tracks (
    path            TEXT PRIMARY KEY,
    path_key        TEXT,
    mtime           REAL NOT NULL,
    duration        REAL NOT NULL,
    artist          TEXT,
    title           TEXT,
    bpm             REAL NOT NULL,
    key             TEXT NOT NULL,
    energy_raw      REAL NOT NULL,
    embedding       BLOB NOT NULL,
    rms             REAL,
    onset_rate      REAL,
    percussive_ratio REAL,
    license         TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    analyzed_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm);
CREATE INDEX IF NOT EXISTS idx_tracks_key ON tracks(key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_path_key ON tracks(path_key);

CREATE TABLE IF NOT EXISTS norm_stats (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    mean    BLOB NOT NULL,
    std     BLOB NOT NULL,
    count   INTEGER NOT NULL
);

PRAGMA user_version = 2;
"""


def _base_v2(carpeta: Path) -> Path:
    """Base del esquema versión 2 (sin las columnas del acuerdo) con dos filas del catálogo."""
    db = carpeta / "v2.sqlite"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA_V2)
    for i in (0, 1):
        c = _catalogo()[i]
        ruta = carpeta / c["nombre"]
        ruta.write_bytes(b"x")
        con.execute(
            "INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(ruta), os.path.normcase(str(ruta)), 111.0 + i, 10.0 + i, f"Artista {i}",
             f"Título {i}", c["bpm"], c["key"], c["energy_raw"], c["embedding"].tobytes(),
             c["rms"], 2.5 + i, None, LICENCIA, ORIGEN, f"2026-01-0{i + 1}T00:00:00"))
    con.commit()
    con.close()
    return db


def test_el_acuerdo_de_la_key_vuelve_igual_de_la_base(tmp_path):
    """El acuerdo entre tramos y los votos viajan enteros hasta la base y vuelven: es la
    confianza con la que la CLI decide si la key va con `?` (§6). Dos tracks con acuerdos
    DISTINTOS, así que guardar el de uno para los dos se ve."""
    ruta_a, ruta_b = tmp_path / "unanime.wav", tmp_path / "dudosa.wav"
    for r in (ruta_a, ruta_b):
        r.write_bytes(b"x")
    ca, cb = _catalogo()[0], _catalogo()[1]
    fa = TrackFeatures(bpm=ca["bpm"], key=ca["key"], energy_raw=ca["energy_raw"],
                       embedding=ca["embedding"], key_acuerdo="3/3",
                       key_tramos=f"{ca['key']}|{ca['key']}|{ca['key']}")
    # Tramos ASIMÉTRICOS: con un palíndromo ("8A|5A|8A") invertir el orden de los votos al
    # guardarlos o al leerlos daría la misma cadena y el round-trip no lo vería.
    fb = TrackFeatures(bpm=cb["bpm"], key=cb["key"], energy_raw=cb["energy_raw"],
                       embedding=cb["embedding"], key_acuerdo="2/3",
                       key_tramos=f"{cb['key']}|{cb['key']}|{ca['key']}")

    with Store(tmp_path / "db.sqlite") as store:
        store.upsert(ruta_a, fa, duration=10.0, license=LICENCIA, source_url=ORIGEN)
        store.upsert(ruta_b, fb, duration=10.0, license=LICENCIA, source_url=ORIGEN)

        vuelta_a, vuelta_b = store.get_features(ruta_a), store.get_features(ruta_b)
        assert (vuelta_a.key_acuerdo, vuelta_a.key_tramos) == (fa.key_acuerdo, fa.key_tramos), \
            f"unanime.wav volvió con {vuelta_a.key_acuerdo!r} / {vuelta_a.key_tramos!r}"
        assert (vuelta_b.key_acuerdo, vuelta_b.key_tramos) == (fb.key_acuerdo, fb.key_tramos), \
            f"dudosa.wav volvió con {vuelta_b.key_acuerdo!r} / {vuelta_b.key_tramos!r}"

        # El `Track` del motor también lo lleva: es el que imprimen `list`, `similar` y `radio`.
        por_ruta = {t.path.name: t.key_acuerdo for t in store.load_library()}
        assert por_ruta == {"unanime.wav": "3/3", "dudosa.wav": "2/3"}, por_ruta
        assert store.get(ruta_b).key_acuerdo == "2/3", store.get(ruta_b).key_acuerdo


def test_acuerdo_no_medido_se_guarda_null_y_no_un_acuerdo_inventado(tmp_path):
    """Un análisis sin consenso deja NULL en las dos columnas. Un "3/3" por defecto diría
    que la key es confiable sin que nadie la haya medido (§6)."""
    db = tmp_path / "db.sqlite"
    ruta = tmp_path / _catalogo()[0]["nombre"]
    ruta.write_bytes(b"x")
    with Store(db) as store:
        store.upsert(ruta, _features(_catalogo()[0]), duration=10.0,
                     license=LICENCIA, source_url=ORIGEN)
        vuelta = store.get_features(ruta)
    assert (vuelta.key_acuerdo, vuelta.key_tramos) == (None, None), \
        f"lo no medido volvió como {vuelta.key_acuerdo!r} / {vuelta.key_tramos!r}"

    con = sqlite3.connect(str(db))
    fila = con.execute("SELECT key_acuerdo, key_tramos FROM tracks").fetchone()
    con.close()
    assert fila == (None, None), f"en la base quedó {fila}, y NULL es lo único honesto"


def test_base_v2_se_migra_con_acuerdo_null_y_conserva_sus_datos(tmp_path):
    """Una biblioteca ya escaneada (versión 2) abierta con este código: queda en la versión
    actual, con las columnas nuevas en NULL y TODO lo demás intacto. La migración no puede
    inventar el acuerdo: esas filas se analizaron sin correr `tono_consenso`."""
    from motor.store import VERSION_ESQUEMA

    db = _base_v2(tmp_path)

    with Store(db) as store:
        assert store.count() == 2, f"la migración dejó {store.count()} filas"
        for i in (0, 1):
            c = _catalogo()[i]
            got = store.get_features(tmp_path / c["nombre"])
            assert (got.bpm, got.key, got.energy_raw) == (c["bpm"], c["key"], c["energy_raw"]), got
            assert (got.rms, got.onset_rate) == (c["rms"], 2.5 + i), got
            assert np.array_equal(got.embedding, c["embedding"]), f"{c['nombre']}: embedding perdido"
            assert (got.key_acuerdo, got.key_tramos) == (None, None), \
                f"{c['nombre']}: la migración inventó acuerdo {got.key_acuerdo!r}"
        assert [t.key_acuerdo for t in store.load_library()] == [None, None], \
            "los Track de una base vieja tienen que venir sin acuerdo"

    con = sqlite3.connect(str(db))
    version = con.execute("PRAGMA user_version").fetchone()[0]
    columnas = [f[1] for f in con.execute("PRAGMA table_info(tracks)")]
    con.close()
    assert version == VERSION_ESQUEMA, f"la base quedó en versión {version}"
    assert "key_acuerdo" in columnas and "key_tramos" in columnas, columnas


def test_una_base_v2_migrada_acepta_el_acuerdo_del_proximo_scan(tmp_path):
    """El otro lado de la migración: sobre la base ya migrada, reanalizar un track SÍ guarda
    su acuerdo, y NO se lo pone al que no se tocó."""
    db = _base_v2(tmp_path)
    c = _catalogo()[0]
    ruta = tmp_path / c["nombre"]
    tramos = "|".join([c["key"]] * 3)

    with Store(db) as store:
        store.upsert(ruta, TrackFeatures(bpm=c["bpm"], key=c["key"], energy_raw=c["energy_raw"],
                                         embedding=c["embedding"], key_acuerdo="3/3",
                                         key_tramos=tramos),
                     duration=10.0, license=LICENCIA, source_url=ORIGEN)
        got = store.get_features(ruta)
        otro = store.get_features(tmp_path / _catalogo()[1]["nombre"])

    assert (got.key_acuerdo, got.key_tramos) == ("3/3", tramos), \
        f"el reanálisis sobre la base migrada guardó {got.key_acuerdo!r} / {got.key_tramos!r}"
    assert otro.key_acuerdo is None, \
        f"reanalizar un track le puso acuerdo {otro.key_acuerdo!r} a OTRO que no se tocó"


def test_migracion_3_que_falla_deja_la_base_v2_como_estaba(tmp_path, monkeypatch):
    """La migración 3 va en la MISMA transacción que las anteriores: si falla, la base
    vuelve a la versión 2 sin columnas nuevas. Una base a medio migrar (columnas puestas y
    versión vieja) se re-migraría en la próxima apertura sobre una forma que ya cambió."""
    import motor.store as modulo_store

    db = _base_v2(tmp_path)
    antes = _foto(db)
    assert antes[0] == 2, f"la base de partida tiene que ser versión 2, es {antes[0]}"

    def paso_3_que_falla(self):
        Store._migrar_a_3_acuerdo_key(self)
        raise RuntimeError("falla inyectada al final de la migración 3")

    monkeypatch.setattr(modulo_store, "_MIGRACIONES",
                        ((1, Store._migrar_a_1_nulos), (2, Store._migrar_a_2_path_key),
                         (3, paso_3_que_falla)))
    with pytest.raises(RuntimeError, match="migración 3"):
        Store(db)
    assert _foto(db) == antes, "la migración 3 fallida dejó la base modificada"

    monkeypatch.undo()
    with Store(db) as store:                     # después del fallo, una apertura normal migra
        assert store.count() == 2
        assert store.get_features(tmp_path / _catalogo()[0]["nombre"]).key_acuerdo is None


# --- la compuerta del acuerdo: qué NO se puede persistir --------------------------------

# (acuerdo, tramos, qué tiene de malo). Todos son datos inventados de distinta forma: el
# scan real no produce ninguno, y guardarlos haría que `info` narrara una confianza que
# nadie midió (spec §6).
ACUERDOS_INVALIDOS = [
    ("abc", "8A|3B|9A", "no tiene la forma ganados/total"),
    ("2-3", "8A|3B|9A", "el separador no es /"),
    ("4/3", "8A|3B|9A", "más tramos de acuerdo que tramos"),
    ("3/3", "", "dice 3 tramos coincidiendo y no guarda ninguno"),
    ("3/3", "8A|8A", "dice 3 tramos y guarda 2"),
    ("0/0", "8A", "dice 0 tramos y guarda 1"),
    ("", "", "vacío en vez de NULL: nadie midió, y eso se guarda como NULL"),
    ("2/3", None, "acuerdo sin votos"),
    (None, "8A|3B|9A", "votos sin acuerdo"),
]


@pytest.mark.parametrize(("acuerdo", "tramos", "motivo"), ACUERDOS_INVALIDOS,
                         ids=[f"{a!r}-{t!r}" for a, t, _ in ACUERDOS_INVALIDOS])
def test_la_cache_no_acepta_un_acuerdo_inventado(tmp_path, acuerdo, tramos, motivo):
    """`Store.upsert` rechaza la confianza de la key mal formada, igual que ya rechaza una
    licencia vacía o un BPM infinito. La fila NO entra: después del intento la base sigue
    sin ese track."""
    db = tmp_path / "db.sqlite"
    c = _catalogo()[0]
    ruta = tmp_path / c["nombre"]
    ruta.write_bytes(b"x")
    features = TrackFeatures(bpm=c["bpm"], key=c["key"], energy_raw=c["energy_raw"],
                             embedding=c["embedding"], key_acuerdo=acuerdo, key_tramos=tramos)

    with Store(db) as store, pytest.raises(ValueError, match="key_acuerdo|key_tramos"):
        store.upsert(ruta, features, duration=10.0, license=LICENCIA, source_url=ORIGEN)

    with Store(db) as store:
        assert store.count() == 0, \
            f"entró a la caché un acuerdo {acuerdo!r} con tramos {tramos!r} ({motivo})"


@pytest.mark.parametrize(("acuerdo", "tramos"), [
    (None, None),            # no se midió
    ("0/0", ""),             # se midió y el track no dio para tramos
    ("1/3", "9A|3B|8A"),     # los tres tramos votaron distinto
    ("2/3", "8A|8A|3B"),
    ("3/3", "8A|8A|8A"),
    ("5/5", "8A|8A|8A|8A|8A"),   # n_tramos es configurable: no se compara contra 3
    ("0/3", "?|?|?"),        # ningún tramo pudo votar
])
def test_la_cache_acepta_los_acuerdos_que_produce_el_consenso(tmp_path, acuerdo, tramos):
    """El otro lado de la compuerta: todo lo que `tono_consenso` puede devolver entra y
    vuelve igual. Una validación que rechazara alguno de estos rompería el scan."""
    db = tmp_path / "db.sqlite"
    c = _catalogo()[0]
    ruta = tmp_path / c["nombre"]
    ruta.write_bytes(b"x")

    with Store(db) as store:
        store.upsert(ruta, TrackFeatures(bpm=c["bpm"], key=c["key"], energy_raw=c["energy_raw"],
                                         embedding=c["embedding"], key_acuerdo=acuerdo,
                                         key_tramos=tramos),
                     duration=10.0, license=LICENCIA, source_url=ORIGEN)
        got = store.get_features(ruta)

    assert (got.key_acuerdo, got.key_tramos) == (acuerdo, tramos), \
        f"entró ({acuerdo!r}, {tramos!r}) y volvió ({got.key_acuerdo!r}, {got.key_tramos!r})"


def test_leer_una_fila_con_acuerdo_corrupto_no_rompe_la_biblioteca(tmp_path):
    """La compuerta es de ESCRITURA. Una base tocada por fuera puede tener "abc", y leerla
    tiene que devolver ese texto tal cual —para que la CLI lo muestre con `?`— en vez de
    tirar `load_library` para los otros tracks."""
    db = tmp_path / "db.sqlite"
    rutas = _poblar(Store(db), tmp_path, indices=[0, 1])
    store = Store(db)
    store.close()
    con = sqlite3.connect(str(db))
    con.execute("UPDATE tracks SET key_acuerdo = 'abc', key_tramos = 'x' WHERE path LIKE ?",
                (f"%{_catalogo()[0]['nombre']}",))
    con.commit()
    con.close()

    with Store(db) as store:
        biblio = store.load_library()
        corrupto = store.get_features(rutas[_catalogo()[0]["nombre"]])

    assert len(biblio) == 2, f"una fila corrupta se llevó puesta la biblioteca: {len(biblio)}"
    assert corrupto.key_acuerdo == "abc", f"el acuerdo corrupto volvió como {corrupto.key_acuerdo!r}"
    assert {t.path.name: t.key_acuerdo for t in biblio} == \
        {_catalogo()[0]["nombre"]: "abc", _catalogo()[1]["nombre"]: None}, \
        {t.path.name: t.key_acuerdo for t in biblio}
