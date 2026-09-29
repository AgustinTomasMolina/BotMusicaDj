"""La radio desde una playlist de MusiFlix (f33): /api/radio/playlists* y el pool del set.

El caso real que lo originó: un set mezcló Hard Bounce, Industrial y Techno y metió dos veces
el mismo tema (el mismo master en dos carpetas). Cada test de acá arma su playlist y compara
el set contra lo que armaría `build_set` con el pool que corresponde — y verifica ANTES que,
sin el filtro, el tema que tiene que quedar afuera sí habría entrado (si no, el test no
prueba nada).

Los datos del motor de los tracks extra (BPM, key) se ESCRIBEN en la base, como el CATALOGO
de `tests/sinteticos.py` (ver su docstring): lo que se prueba es qué entra al pool, no la
medición. La única medición real es la del análisis en segundo plano, con audio de
`motor.sintetico` (BPM y tonalidad conocidos por construcción).
"""
import importlib
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
from sinteticos import (  # noqa: E402
    CATALOGO,
    LICENCIA,
    ORIGEN,
    armar_base_radio,
    crear_playlist_radio,
    embedding,
    item_de_catalogo,
    pistas_biblioteca,
    wav,
    xml_rekordbox,
)

from motor.modelos import TrackFeatures  # noqa: E402
from motor.radio import RadioConfig, build_set  # noqa: E402
from motor.store import Store  # noqa: E402

TODOS_CERO = {"otro_genero": 0, "por_analizar": 0, "fallo_analisis": 0, "sin_archivo": 0,
              "archivo_no_existe": 0, "sin_genero": 0, "sin_origen": 0, "duplicado": 0}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Receta de `tests/test_server_radio.py`."""
    datos = tmp_path_factory.mktemp("server-radio-playlist-datos")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MUSIFLIX_DATA_DIR", str(datos))
        mp.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
        mp.delenv("REDIS_URL", raising=False)
        mp.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        for mod in ("server", "jobs", "db"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
        srv.db.init_db()
    return srv


@pytest.fixture
def client(server, monkeypatch):
    monkeypatch.setattr(server, "_radio_audio", {})
    return TestClient(server.app)


@pytest.fixture
def biblioteca(tmp_path, monkeypatch):
    raiz = tmp_path / "musica"
    db = tmp_path / "djradio" / "biblioteca.sqlite"
    _, rutas = armar_base_radio(raiz, db)
    monkeypatch.setenv("DJRADIO_DB", str(db))
    return {"raiz": raiz, "db": db, "rutas": rutas}


def _al_motor(biblioteca, ruta: Path, *, bpm, key, artista, titulo, i, dur=250.0, e=0.35):
    """Un track más en la base del motor (con su WAV), como los del CATALOGO."""
    wav(ruta, 220.0 + 37.0 * i)
    with Store(biblioteca["db"]) as store:
        store.upsert(ruta, TrackFeatures(bpm=bpm, key=key, energy_raw=e, embedding=embedding(i),
                                         key_acuerdo="3/3", key_tramos=f"{key}|{key}|{key}"),
                     duration=dur, license=LICENCIA, source_url=ORIGEN, artist=artista,
                     title=titulo)
    biblioteca["rutas"][ruta.name] = ruta
    return ruta


def _lib(biblioteca) -> list:
    with Store(biblioteca["db"]) as store:
        return store.load_library()


def _por_nombre(lib) -> dict:
    return {t.path.name: t for t in lib}


def _agregar(server, pid, item, ruta=None) -> int:
    return server.db.agregar_item(pid, item, None if ruta is None else str(ruta))["id"]


def _set(client, pid, track, **kw):
    return client.get("/api/radio/set", params={"playlist": pid, "track": track, **kw})


# ------------------------------------------------------------- (a) solo el mismo género

def test_ningun_tema_de_otro_genero_entra_y_el_recuento_es_exacto(server, client, biblioteca):
    seis = _al_motor(biblioteca, biblioteca["raiz"] / "seis.wav", bpm=129.0, key="8A",
                     artista="Artista G", titulo="Seis", i=10)
    lib = _lib(biblioteca)
    t = _por_nombre(lib)
    # Dientes: sin el filtro de género, "Seis" entra (129.0 está a +0.5% de la semilla).
    sin_filtro = build_set(t["uno.wav"], lib, RadioConfig(length=10))
    assert "Seis" in [x.title for x in sin_filtro.tracks], "Seis no entraba igual: el test no probaría nada"

    pid = crear_playlist_radio(server.db, biblioteca["rutas"], "Géneros",
                               nombres=["uno.wav", "tres.wav", "cuatro.wav", "loop.wav", "cinco.wav"])
    # El mismo género escrito distinto es el mismo género (NFKC + casefold + espacios).
    _agregar(server, pid, item_de_catalogo("dos.wav", "  TECHNO  "), biblioteca["rutas"]["dos.wav"])
    _agregar(server, pid, {"titulo": "Seis", "artista": "Artista G", "fuente": "biblioteca",
                           "genero": "Hard Bounce"}, seis)
    sin_genero = biblioteca["raiz"] / "suelto.wav"
    wav(sin_genero, 900.0)
    _agregar(server, pid, {"titulo": "Suelto", "artista": "X", "fuente": "biblioteca",
                           "genero": None}, sin_genero)
    _agregar(server, pid, {"titulo": "Por bajar", "artista": "Y", "fuente": "youtube",
                           "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "genero": "Techno"})

    d = _set(client, pid, server._radio_id(biblioteca["rutas"]["uno.wav"]), largo=10).json()
    assert "Seis" not in [p["track"]["titulo"] for p in d["pasos"]], "entró un tema de otro género"
    esperado = build_set(t["uno.wav"], [x for x in lib if x.path.name != "seis.wav"],
                         RadioConfig(length=10))
    assert [p["track"]["label"] for p in d["pasos"]] == [x.label for x in esperado.tracks]
    assert (d["playlist"], d["genero"], d["entran"], d["total_playlist"]) == \
        ({"id": pid, "nombre": "Géneros"}, "Techno", 6, 9)
    assert d["excluidos"] == {**TODOS_CERO, "otro_genero": 1, "sin_genero": 1, "sin_archivo": 1}

    # Desde la semilla de Hard Bounce el pool es ella sola: los 6 de Techno son "otro género",
    # y el Techno por bajar también (para ESTE set lo deja afuera el género, no el archivo).
    h = _set(client, pid, server._radio_id(seis)).json()
    assert [p["track"]["titulo"] for p in h["pasos"]] == ["Seis"]
    assert (h["genero"], h["entran"]) == ("Hard Bounce", 1)
    assert h["excluidos"] == {**TODOS_CERO, "otro_genero": 7, "sin_genero": 1}

    # Y la playlist lo dice ANTES de armar, por género.
    g = client.get(f"/api/radio/playlists/{pid}").json()["generos"]
    assert [(x["genero"], x["entran"], x["excluidos"]["otro_genero"]) for x in g] == \
        [("Techno", 6, 1), ("Hard Bounce", 1, 7)]


# ------------------------------------------------------------- (b) duplicados

def test_el_mismo_master_en_dos_carpetas_entra_una_sola_vez(server, client, biblioteca):
    """El caso STORM: dos archivos con el mismo artista y título en carpetas distintas.

    Los WAV no traen tags (como tantos masters): para el motor no tienen artista, así que
    `artist_gap` no los separa y la radio los pone a los dos. El artista y el título los
    tiene el ITEM de la playlist (vienen de la home), y con eso se reconocen."""
    a = _al_motor(biblioteca, biblioteca["raiz"] / "descargas" / "master.wav", bpm=128.0,
                  key="8A", artista=None, titulo=None, i=20)
    b = biblioteca["raiz"] / "rekordbox" / "master.wav"
    b.parent.mkdir(parents=True)
    shutil.copyfile(a, b)
    with Store(biblioteca["db"]) as store:
        store.upsert(b, store.get_features(a), duration=250.0, license=LICENCIA,
                     source_url=ORIGEN)
    lib = _lib(biblioteca)
    uno = _por_nombre(lib)["uno.wav"]
    sin_dedupe = build_set(uno, lib, RadioConfig(length=10))
    assert [str(x.path) for x in sin_dedupe.tracks].count(str(a)) + \
        [str(x.path) for x in sin_dedupe.tracks].count(str(b)) == 2, \
        "sin sacar duplicados el set no traía las dos copias: el test no probaría nada"

    pid = crear_playlist_radio(server.db, biblioteca["rutas"], "STORM", nombres=["uno.wav", "dos.wav"])
    storm = {"artista": "STORM", "fuente": "biblioteca", "genero": "Techno"}
    id_a = _agregar(server, pid, {**storm, "titulo": "Own This Block (MASTER)"}, a)
    # Otra grafía del mismo tema (mayúsculas, espacios): mismo tema normalizado. `db` no lo
    # deduplica al agregar (su identidad es exacta), la radio sí.
    id_b = _agregar(server, pid, {**storm, "titulo": "own this block  (master) "}, b)
    # Y el mismo ARCHIVO que otro item, con otro título: ni siquiera entra a la playlist (el
    # dedupe de `db.agregar_item` es por archivo); la radio lo cubre igual (test_radio_playlist).
    id_uno = next(it["id"] for it in server.db.get_playlist_radio(pid)["items"] if it["titulo"] == "Uno")
    assert server.db.agregar_item(pid, {**storm, "titulo": "Otro nombre"},
                                  str(biblioteca["rutas"]["uno.wav"]))["id"] == id_uno

    d = _set(client, pid, server._radio_id(biblioteca["rutas"]["uno.wav"]), largo=10).json()
    rutas = [p["track"]["id"] for p in d["pasos"]]
    assert rutas.count(server._radio_id(a)) == 1 and server._radio_id(b) not in rutas, d["pasos"]
    assert d["excluidos"] == {**TODOS_CERO, "duplicado": 1}

    items = {it["id"]: it for it in client.get(f"/api/radio/playlists/{pid}").json()["items"]}
    assert (items[id_a]["estado"], items[id_b]["estado"]) == ("listo", "duplicado")
    assert items[id_b]["duplicado_de"] == id_a
    assert items[id_b]["motivo"] == ("duplicado de «STORM — Own This Block (MASTER)» (#3 de la "
                                     "playlist): entra solo el primero")
    assert items[id_b]["track"] is None, "un duplicado no puede mostrarse como listo"

    # Pedir la radio DESDE el duplicado: 400 con su motivo, no un set.
    r = _set(client, pid, server._radio_id(b))
    assert r.status_code == 400 and "duplicado de «STORM" in r.json()["error"], r.text


# ------------------------------------------------------------- (c) sin género

def test_sin_genero_no_entra_y_se_avisa(server, client, biblioteca):
    pid = crear_playlist_radio(server.db, biblioteca["rutas"], "Sin género", nombres=["uno.wav"])
    ids = [_agregar(server, pid, item_de_catalogo(n, g), biblioteca["rutas"][n])
           for n, g in (("dos.wav", None), ("tres.wav", ""), ("cinco.wav", "  Sin género "),
                        ("cuatro.wav", "SIN GENERO"))]
    lib = _lib(biblioteca)
    uno = _por_nombre(lib)["uno.wav"]
    assert len(build_set(uno, lib, RadioConfig(length=5)).tracks) > 1, "sin la regla entraban"

    d = _set(client, pid, server._radio_id(biblioteca["rutas"]["uno.wav"]), largo=5).json()
    assert [p["track"]["titulo"] for p in d["pasos"]] == ["Uno"]
    assert d["excluidos"] == {**TODOS_CERO, "sin_genero": 4}
    items = {it["id"]: it for it in client.get(f"/api/radio/playlists/{pid}").json()["items"]}
    assert [(items[i]["estado"], items[i]["genero"]) for i in ids] == [("sin_genero", None)] * 4
    assert "no tiene género" in items[ids[0]]["motivo"]


# ------------------------------------------------------------- (d) sin archivo

def test_sin_archivo_o_archivo_que_ya_no_esta_no_entran(server, client, biblioteca):
    pid = crear_playlist_radio(server.db, biblioteca["rutas"], "Archivos",
                               nombres=["uno.wav", "dos.wav", "tres.wav"])
    sin = _agregar(server, pid, {"titulo": "Por bajar", "artista": "Y", "fuente": "youtube",
                                 "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
                                 "genero": "Techno"})
    lib = _lib(biblioteca)
    tres_en_set = build_set(_por_nombre(lib)["uno.wav"], lib, RadioConfig(length=10))
    assert "Tres" in [x.title for x in tres_en_set.tracks], "Tres no entraba igual"
    biblioteca["rutas"]["tres.wav"].unlink()     # sigue en la base del motor, sin archivo

    d = _set(client, pid, server._radio_id(biblioteca["rutas"]["uno.wav"]), largo=10).json()
    assert "Tres" not in [p["track"]["titulo"] for p in d["pasos"]]
    assert d["excluidos"] == {**TODOS_CERO, "sin_archivo": 1, "archivo_no_existe": 1}
    items = client.get(f"/api/radio/playlists/{pid}").json()["items"]
    assert [(it["titulo"], it["estado"]) for it in items] == [
        ("Uno", "listo"), ("Dos", "listo"), ("Tres", "archivo_no_existe"), ("Por bajar", "sin_archivo")]
    assert next(it for it in items if it["id"] == sin)["track"] is None
    # Nunca viaja la ruta del archivo al navegador.
    assert not any(k in it for it in items for k in ("ruta", "archivo"))


def test_un_archivo_que_cambio_desde_el_analisis_vuelve_a_por_analizar(server, client, biblioteca):
    """Misma condición que el scan (`Store.needs_analysis`): si el archivo cambió después de
    analizarlo, el análisis de la base describe OTRO audio y no puede decir "listo" (§6)."""
    pid = crear_playlist_radio(server.db, biblioteca["rutas"], "Cambió", nombres=["uno.wav", "dos.wav"])
    assert [it["estado"] for it in client.get(f"/api/radio/playlists/{pid}").json()["items"]] == \
        ["listo", "listo"]
    os.utime(biblioteca["rutas"]["dos.wav"], (1_000_000, 1_000_000))
    items = client.get(f"/api/radio/playlists/{pid}").json()["items"]
    assert [(it["estado"], it["track"]) for it in items][1] == ("por_analizar", None)


# ------------------------------------------------------------- (e) lib_id

def test_lib_id_resuelve_el_archivo_y_una_ruta_del_cliente_se_ignora(server, client, tmp_path,
                                                                    monkeypatch):
    raiz = tmp_path / "musica"
    _, pistas = pistas_biblioteca(raiz, tmp_path / "no-existe" / "fantasma.wav")
    xml = tmp_path / "rekordbox.xml"
    xml.write_text(xml_rekordbox(pistas), encoding="utf-8")
    monkeypatch.setattr(server, "_LIB_XML", str(xml))
    monkeypatch.setattr(server, "_LIB_ROOTS", [str(raiz)])
    monkeypatch.setattr(server, "_lib_audio", {})
    ajeno = str(Path(server.__file__).resolve())       # un archivo que existe y no es música
    pid = server.db.crear_playlist("lib_id")["id"]

    def agregar(track):
        r = client.post(f"/api/playlists/{pid}/items", json={"track": track})
        assert r.status_code == 200, r.text
        return r.json()

    r = agregar({"titulo": "Dos", "artista": "Artista B", "fuente": "biblioteca", "lib_id": "12",
                 "genero": "Techno", "ruta": ajeno, "archivo": "x", "formato": "exe"})
    assert r["con_archivo"] is True
    r2 = agregar({"titulo": "Otro", "artista": "Z", "fuente": "youtube",
                  "url": "https://www.youtube.com/watch?v=ccccccccccc", "ruta": ajeno})
    r3 = agregar({"titulo": "Nada", "artista": "Z", "fuente": "biblioteca", "lib_id": "no-existe"})
    assert (r2["con_archivo"], r3["con_archivo"]) == (False, False)

    items = {it["id"]: it for it in server.db.get_playlist_radio(pid)["items"]}
    assert os.path.normcase(items[r["id"]]["ruta"]) == \
        os.path.normcase(str(raiz / "techno" / "dos.wav")), "el lib_id no resolvió al archivo de ese tema"
    assert (items[r2["id"]]["ruta"], items[r3["id"]]["ruta"]) == (None, None), \
        "se guardó una ruta que mandó el cliente"
    mia = server.db.get_playlist_mia(pid)["items"]
    assert [(i["titulo"], i["formato"], i["descargado"]) for i in mia] == \
        [("Dos", "wav", True), ("Otro", None, False), ("Nada", None, False)]

    # Un item viejo (de antes de f33, sin archivo) se completa al agregarlo de nuevo.
    viejo = server.db.agregar_item(pid, {"titulo": "Uno", "artista": "Artista A",
                                         "fuente": "biblioteca", "genero": "Techno"})["id"]
    r4 = agregar({"titulo": "Uno", "artista": "Artista A", "fuente": "biblioteca", "lib_id": "11",
                  "genero": "Techno"})
    assert (r4["dup"], r4["id"], r4["archivo_completado"]) == (True, viejo, True)
    assert os.path.normcase(server.db.get_playlist_radio(pid)["items"][-1]["ruta"]) == \
        os.path.normcase(str(raiz / "techno" / "uno.wav"))
    # El mismo archivo otra vez es el mismo item; OTRO archivo con el mismo nombre (el master
    # en dos carpetas) entra como otro item: la radio lo marca duplicado y dice de cuál.
    assert agregar({"titulo": "Uno", "artista": "Artista A", "fuente": "biblioteca",
                    "lib_id": "11", "genero": "Techno"})["id"] == viejo
    otro = agregar({"titulo": "Uno", "artista": "Artista A", "fuente": "biblioteca",
                    "lib_id": "21", "genero": "Techno"})
    assert (otro.get("dup"), otro["id"] != viejo) == (None, True), otro


# ------------------------------------------------------------- (f) + (g) análisis en segundo plano

def _click(ruta: Path, bpm: float, nota: str, modo: str) -> Path:
    import soundfile as sf

    from motor.sintetico import click_track

    y, sr = click_track(bpm, dur=10.0, nota=nota, modo=modo)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(ruta), y.astype(np.float32), sr, subtype="FLOAT")
    return ruta


def test_el_analisis_en_segundo_plano_deja_listos_los_temas_con_licencia_y_origen(
        server, client, tmp_path, monkeypatch, capsys):
    """Sin base del motor: analizar la crea (como `scan`). Los BPM y keys salen del análisis
    real sobre audio sintético y se comparan con el generador y con lo que guarda `scan`."""
    from motor.cli import main

    db = tmp_path / "djradio" / "nueva.sqlite"
    monkeypatch.setenv("DJRADIO_DB", str(db))
    carpeta = tmp_path / "bajados"
    a = _click(carpeta / "a.wav", 126.0, "A", "min")
    b = _click(carpeta / "b.wav", 130.0, "C", "maj")
    c = _click(carpeta / "c.wav", 124.0, "G", "maj")
    d_ = _click(carpeta / "d.wav", 128.0, "E", "min")
    roto = carpeta / "roto.wav"
    roto.write_bytes(b"esto no es audio" * 8)
    antes = {p.name: p.read_bytes() for p in carpeta.iterdir()}

    pid = server.db.crear_playlist("Analizar")["id"]
    url_b = "https://www.youtube.com/watch?v=ddddddddddd"
    ids = {
        "a": _agregar(server, pid, {"titulo": "A", "artista": "S", "fuente": "biblioteca", "genero": "Techno"}, a),
        "b": _agregar(server, pid, {"titulo": "B", "artista": "S", "fuente": "youtube", "url": url_b, "genero": "Techno"}, b),
        "c": _agregar(server, pid, {"titulo": "C", "artista": "S", "fuente": "soundcloud", "genero": "Techno"}, c),
        "d": _agregar(server, pid, {"titulo": "D", "artista": "S", "fuente": "", "genero": "Techno"}, d_),
        "roto": _agregar(server, pid, {"titulo": "Roto", "artista": "S", "fuente": "biblioteca", "genero": "Techno"}, roto),
    }
    antes_api = client.get(f"/api/radio/playlists/{pid}").json()
    assert antes_api["estado"] == "sin-base" and not db.exists()
    assert [it["estado"] for it in antes_api["items"]] == \
        ["por_analizar"] * 3 + ["sin_origen", "por_analizar"]

    r = client.post(f"/api/radio/playlists/{pid}/analizar")
    assert r.status_code == 200 and r.json()["arrancado"] is True, r.text
    assert r.json()["analisis"]["total"] == 4, "tiene que analizar solo los por_analizar"
    assert server._analisis_radio.esperar(120), "el análisis no terminó en 2 minutos"

    d = client.get(f"/api/radio/playlists/{pid}").json()
    assert d["analisis"] == {"corriendo": False, "hechos": 4, "total": 4, "actual": None,
                             "fallidos": [{"item_id": ids["roto"], "titulo": "S — Roto",
                                           "motivo": "no se pudo decodificar o dura menos de 1 s"}],
                             "ocupado_por": None}
    items = {it["id"]: it for it in d["items"]}
    assert [items[ids[k]]["estado"] for k in ("a", "b", "c", "d", "roto")] == \
        ["listo", "listo", "listo", "sin_origen", "fallo_analisis"]
    assert items[ids["roto"]]["motivo"] == \
        "el análisis falló: no se pudo decodificar o dura menos de 1 s"
    assert d["resumen"]["listo"] == 3 and d["resumen"]["por_analizar"] == 0

    # (f) La licencia y el origen guardados en la base son EXACTAMENTE los de las reglas.
    with Store(db) as store:
        filas = {Path(f["path"]).name: (f["license"], f["source_url"])
                 for f in store._con.execute("SELECT path, license, source_url FROM tracks")}
    assert filas == {"a.wav": ("biblioteca personal", "coleccion Rekordbox local"),
                     "b.wav": ("descarga, licencia sin verificar", url_b),
                     "c.wav": ("descarga, licencia sin verificar", "soundcloud")}
    assert [(items[ids[k]]["licencia"], items[ids[k]]["origen"]) for k in ("a", "b", "c")] == \
        [filas["a.wav"], filas["b.wav"], filas["c.wav"]], "la pantalla no muestra la licencia de la base"

    # El BPM y la key son los del análisis: los del generador (ground truth) y los mismos que
    # guarda `python -m motor scan` sobre esos archivos (mismo camino, mismo número).
    otra = tmp_path / "scan.sqlite"
    assert main(["--db", str(otra), "scan", str(carpeta), "--licencia", "x", "--origen", "y"]) == 1
    capsys.readouterr()
    with Store(db) as s1, Store(otra) as s2:
        for ruta, bpm, key in ((a, 126.0, "8A"), (b, 130.0, "8B"), (c, 124.0, "9B")):
            f1, f2 = s1.get_features(ruta.resolve()), s2.get_features(ruta.resolve())
            assert (f1.bpm, f1.key) == (f2.bpm, f2.key), f"{ruta.name}: la web midió distinto que scan"
            assert abs(f1.bpm - bpm) <= 1.0 and f1.key == key, f"{ruta.name}: {f1.bpm} {f1.key}"
            assert items[ids[ruta.stem]]["track"]["bpm"] == round(f1.bpm, 1)
    assert {p.name: p.read_bytes() for p in carpeta.iterdir()} == antes, "se modificó un audio"

    # Otra vuelta: nada por analizar, el que falló no se reintenta (mismo archivo, mismo mtime).
    r = client.post(f"/api/radio/playlists/{pid}/analizar").json()
    assert (r["arrancado"], r["analisis"]["hechos"], r["analisis"]["total"]) == (False, 4, 4)


def test_analizar_es_uno_a_la_vez_y_no_rompe_si_la_playlist_no_existe(server, client, biblioteca,
                                                                      monkeypatch):
    import threading

    soltar = threading.Event()

    def lento(tarea):
        soltar.wait(10)
        return None

    monkeypatch.setattr(server, "_analizar_para_radio", lento)
    uno = server.db.crear_playlist("uno")["id"]
    otra = server.db.crear_playlist("otra")["id"]
    for pid, nombre in ((uno, "p1.wav"), (otra, "p2.wav")):
        wav(biblioteca["raiz"] / nombre, 300.0)
        _agregar(server, pid, {"titulo": nombre, "artista": "Q", "fuente": "biblioteca",
                               "genero": "Techno"}, biblioteca["raiz"] / nombre)
    try:
        assert client.post(f"/api/radio/playlists/{uno}/analizar").json()["arrancado"] is True
        again = client.post(f"/api/radio/playlists/{uno}/analizar")
        assert (again.status_code, again.json()["arrancado"]) == (200, False)
        assert again.json()["analisis"]["corriendo"] is True
        r = client.post(f"/api/radio/playlists/{otra}/analizar")
        assert r.status_code == 409, r.text
        assert r.json()["analisis"]["ocupado_por"] == {"id": uno, "nombre": "uno"}
        assert "«uno»" in r.json()["error"]
    finally:
        soltar.set()
        assert server._analisis_radio.esperar(10)
    assert client.post("/api/radio/playlists/987654/analizar").status_code == 404


# ------------------------------------------------------------- (h) pedidos inválidos

def test_playlist_faltante_o_inexistente_y_semilla_fuera_del_pool(server, client, biblioteca):
    id_tres = server._radio_id(biblioteca["rutas"]["tres.wav"])
    for ruta in ("/api/radio/set", "/api/radio/set.m3u8"):
        r = client.get(ruta, params={"track": id_tres})
        assert r.status_code == 400 and "Falta `playlist`" in r.json()["error"], r.text
        r = client.get(ruta, params={"playlist": 987654, "track": id_tres})
        assert r.status_code == 400 and "987654" in r.json()["error"], r.text
        assert "content-disposition" not in r.headers
    assert client.get("/api/radio/playlists/987654").status_code == 404
    r = client.post("/api/radio/sets", json={"track": id_tres, "esperado": ["a"], "huella": "h"})
    assert r.status_code == 400 and "playlist" in r.json()["error"]

    # Tres está analizado en la base pero NO en esta playlist: no puede ser la semilla.
    pid = crear_playlist_radio(server.db, biblioteca["rutas"], "Chica", nombres=["uno.wav", "dos.wav"])
    r = _set(client, pid, id_tres)
    assert r.status_code == 400, r.text
    assert "Ningún track" in r.json()["error"] and "«Chica»" in r.json()["error"], r.json()


# ------------------------------------------------------------- (i) determinismo

def test_misma_playlist_misma_config_mismo_set(server, client, biblioteca):
    pid = crear_playlist_radio(server.db, biblioteca["rutas"], "Determinismo")
    id_uno = server._radio_id(biblioteca["rutas"]["uno.wav"])
    for extra in ({"randomness": 0}, {"randomness": 0.5, "semilla": 7}):
        a = _set(client, pid, id_uno, largo=4, **extra).json()
        b = _set(client, pid, id_uno, largo=4, **extra).json()
        assert [p["track"]["id"] for p in a["pasos"]] == [p["track"]["id"] for p in b["pasos"]]
        assert a["huella"] == b["huella"]
    # El orden de la playlist no cambia el set (`build_set` ordena el pool por ruta antes de
    # desempatar, motor/radio.py `_pool`): reordenarla da el mismo set.
    antes = _set(client, pid, id_uno, largo=4).json()
    items = server.db.get_playlist_radio(pid)["items"]
    assert server.db.reordenar(pid, [it["id"] for it in reversed(items)])
    assert [it["titulo"] for it in server.db.get_playlist_radio(pid)["items"]][0] == CATALOGO[-1][7]
    despues = _set(client, pid, id_uno, largo=4).json()
    assert [p["track"]["id"] for p in despues["pasos"]] == [p["track"]["id"] for p in antes["pasos"]]
