"""f55: la radio desde una playlist de Mis playlists (`radio_playlist.py` + /api/radio/*).

Reglas del dueño que se prueban acá, comparando VALORES (CONTRIBUTING.md):

- la web arma SOLO desde una playlist (sin `playlist`, 400);
- solo temas del MISMO género que la semilla; si la semilla es de otro género, el pool es el
  de ese género;
- afuera, con su motivo y en el orden de la playlist: otro género, sin género, sin archivo,
  analizando, no es un track, duplicado (entra el primero dentro del género);
- BPM/key/energía y el orden los decide el motor: el set es EXACTAMENTE `build_set` sobre el
  pool (calculado acá por separado, desde la base y no desde la API);
- determinismo con randomness=0 (y con la misma semilla del azar);
- guardar el set como playlist nueva, en su orden, re-armando y sin rutas del cliente.

Los BPM/keys se ESCRIBEN en la base del motor (como en `test_server_radio.py`): lo que se
prueba es qué entra al pool y que el set sea el del motor, no la medición.
"""
import importlib
import re
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
from sinteticos import LICENCIA, ORIGEN, armar_base_radio, embedding, wav  # noqa: E402

from motor.modelos import TrackFeatures  # noqa: E402
from motor.radio import RadioConfig, build_set  # noqa: E402
from motor.store import Store  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
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


# Tracks que se suman al CATALOGO en la base del motor: dos de House (compatibles con «Uno»
# por BPM y key: si el género no filtrara, el motor los elegiría), una copia de «Uno» en otro
# archivo (el mismo tema) y uno que en la playlist no tiene género.
# (archivo, bpm, key, duración, artista, título, energía)
EXTRAS = [
    ("seis.wav", 128.0, "8A", 300.0, "Artista G", "Seis", 0.22),
    ("siete.wav", 129.0, "9A", 300.0, "Artista H", "Siete", 0.28),
    ("uno copia.wav", 128.4, "8A", 240.0, "Artista A", "Uno", 0.31),
    ("ocho.wav", 127.0, "8A", 260.0, "Artista I", "Ocho", 0.18),
]

# La playlist, en orden: (archivo o None, título, artista, género, fuente).
PLAYLIST = [
    ("uno.wav", "Uno", "Artista A", "Techno", "carpeta"),
    ("dos.wav", "Dos", "Artista B", "Techno", "carpeta"),
    ("seis.wav", "Seis", "Artista G", "House", "carpeta"),
    ("uno copia.wav", "Uno", "Artista A", "  techno ", "carpeta"),   # mismo género, escrito distinto
    ("tres.wav", "Tres", "Artista C", "Techno", "carpeta"),
    ("ocho.wav", "Ocho", "Artista I", None, "carpeta"),
    ("cuatro.wav", "Cuatro", "Artista D", "Techno", "carpeta"),
    ("loop.wav", "Loop", "Artista E", "Techno", "carpeta"),
    ("cinco.wav", "Cinco", "Artista F", "Techno", "carpeta"),
    ("siete.wav", "Siete", "Artista H", "House", "carpeta"),
    (None, "Nueve", "Artista J", "Techno", "youtube"),
]


def _crear_playlist(server, rutas, filas, nombre="Mezcla") -> int:
    pid = server.db.crear_playlist(nombre)["id"]
    for arch, titulo, artista, genero, fuente in filas:
        track = {"titulo": titulo, "artista": artista, "genero": genero, "fuente": fuente,
                 "url": "https://youtube.com/watch?v=x" if fuente == "youtube" else ""}
        r = server.db.agregar_item(pid, track, str(rutas[arch]) if arch else None)
        assert r and not r.get("dup"), (arch, r)
    return pid


@pytest.fixture
def escena(server, tmp_path, monkeypatch):
    db = tmp_path / "djradio" / "biblioteca.sqlite"
    raiz = tmp_path / "musica"
    _, rutas = armar_base_radio(raiz, db)
    with Store(db) as store:
        for i, (arch, bpm, key, dur, artista, titulo, e) in enumerate(EXTRAS, start=20):
            ruta = raiz / arch
            wav(ruta, 300.0 + 50 * i)
            rutas[arch] = ruta
            store.upsert(ruta, TrackFeatures(bpm=bpm, key=key, energy_raw=e, embedding=embedding(i),
                                             key_acuerdo="3/3", key_tramos=f"{key}|{key}|{key}"),
                         duration=dur, license=LICENCIA, source_url=ORIGEN, artist=artista,
                         title=titulo)
    monkeypatch.setenv("DJRADIO_DB", str(db))
    pid = _crear_playlist(server, rutas, PLAYLIST)
    items = server.db.get_playlist_mia(pid)["items"]
    return {"db": db, "rutas": rutas, "pid": pid, "items": items,
            "rid": {n: server._radio_id(r) for n, r in rutas.items()}}


def _motor(db) -> dict:
    with Store(db) as store:
        return {t.path.name: t for t in store.load_library()}


def _set(client, escena, desde="uno.wav", **params):
    r = client.get("/api/radio/set", params={"playlist": escena["pid"],
                                             "track": escena["rid"][desde], **params})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ qué entra y qué no

def test_entran_solo_los_del_genero_de_la_semilla_y_cada_afuera_dice_por_que(client, escena):
    d = _set(client, escena)
    pool = d["pool"]
    assert pool["playlist"]["id"] == escena["pid"] and pool["playlist"]["nombre"] == "Mezcla"
    assert (pool["genero"], pool["entran"], pool["total"]) == ("Techno", 5, 11)
    assert [(a["titulo"], a["motivo"]) for a in pool["afuera"]] == [
        ("Seis", "otro_genero"), ("Uno", "duplicado"), ("Ocho", "sin_genero"),
        ("Loop", "no_es_track"), ("Siete", "otro_genero"), ("Nueve", "sin_archivo")]
    assert pool["excluidos"] == {"otro_genero": 2, "sin_genero": 1, "sin_archivo": 1,
                                 "analizando": 0, "sin_analizar": 0, "fallo_analisis": 0,
                                 "no_es_track": 1, "duplicado": 1}
    assert pool["entran"] + sum(pool["excluidos"].values()) == pool["total"], "la partición no cierra"
    # El duplicado dice de cuál: del primer «Uno» de la playlist.
    dup = next(a for a in pool["afuera"] if a["motivo"] == "duplicado")
    assert dup["duplicado_de"] == escena["items"][0]["id"]
    assert "«Artista A — Uno» (#1 de la playlist)" in dup["texto"], dup["texto"]
    otro = next(a for a in pool["afuera"] if a["titulo"] == "Seis")
    assert "House" in otro["texto"] and "Techno" in otro["texto"], otro["texto"]
    # Ningún paso es de otro género, ni la copia, ni el que no tiene género.
    titulos = [p["track"]["titulo"] for p in d["pasos"]]
    assert not {"Seis", "Siete", "Ocho", "Loop"} & set(titulos), titulos
    ids = [p["track"]["id"] for p in d["pasos"]]
    assert escena["rid"]["uno copia.wav"] not in ids, "entró la copia del mismo tema"
    # Cada paso dice de qué item de la playlist salió.
    por_titulo = {it["titulo"]: it["id"] for it in escena["items"] if it["titulo"] != "Uno"}
    por_titulo["Uno"] = escena["items"][0]["id"]
    assert [p["item_id"] for p in d["pasos"]] == [por_titulo[t] for t in titulos]


def test_el_set_es_el_de_build_set_sobre_el_pool_y_sin_el_filtro_seria_otro(client, escena):
    """Con la MISMA config, el set es exactamente `build_set` sobre los 5 que entran. Y el
    filtro tiene dientes: con «Seis» (House, 128.0 8A) en el pool, el motor lo elegiría."""
    t = _motor(escena["db"])
    pool = [t[n] for n in ("uno.wav", "dos.wav", "tres.wav", "cuatro.wav", "cinco.wav")]
    config = RadioConfig(length=4)
    esperado = build_set(t["uno.wav"], pool, config)
    d = _set(client, escena, largo=4)
    assert [p["track"]["label"] for p in d["pasos"]] == [x.label for x in esperado.tracks]
    assert [p["motivo"] for p in d["pasos"]] == esperado.reasons()
    sin_filtro = build_set(t["uno.wav"], pool + [t["seis.wav"], t["uno copia.wav"]], config)
    assert {"seis.wav", "uno copia.wav"} & {x.path.name for x in sin_filtro.tracks}, \
        "el test no tiene dientes: sin filtro el motor tampoco elegiría los de afuera"
    # «Por qué» en la forma de §6 en cada salto.
    forma = re.compile(r"^[+-]\d+\.\d% BPM( \([a-z ]+\))? \| \w+ → \w+ \([a-z ]+\)$")
    assert all(forma.match(p["motivo"]) for p in d["pasos"][1:]), [p["motivo"] for p in d["pasos"]]


def test_una_semilla_de_otro_genero_cambia_el_pool_a_ese_genero(client, escena):
    d = _set(client, escena, desde="seis.wav")
    assert (d["pool"]["genero"], d["pool"]["entran"]) == ("House", 2)
    assert [p["track"]["titulo"] for p in d["pasos"]] == ["Seis", "Siete"]
    afuera = {a["titulo"]: a["motivo"] for a in d["pool"]["afuera"]}
    assert afuera == {"Uno": "otro_genero", "Dos": "otro_genero", "Tres": "otro_genero",
                      "Cuatro": "otro_genero", "Loop": "otro_genero", "Cinco": "otro_genero",
                      "Nueve": "otro_genero", "Ocho": "sin_genero"}
    assert d["pool"]["excluidos"]["otro_genero"] == 8      # las dos «Uno» cuentan


def test_lo_que_se_esta_analizando_no_entra_y_se_cuenta(server, client, escena, monkeypatch):
    ruta_dos = str(escena["rutas"]["dos.wav"])
    real = server._analizador.estado_de
    monkeypatch.setattr(server._analizador, "estado_de",
                        lambda r: "analizando" if Path(r) == Path(ruta_dos) else real(r))
    d = _set(client, escena)
    assert (d["pool"]["entran"], d["pool"]["analizando"]) == (4, 1)
    assert {a["titulo"]: a["motivo"] for a in d["pool"]["afuera"]}["Dos"] == "analizando"
    assert "Dos" not in [p["track"]["titulo"] for p in d["pasos"]]


def test_duplicado_entra_el_primero_de_la_playlist(server, client, escena):
    """Con la copia ANTES en la playlist, entra la copia y el original es el duplicado: el
    original ya no puede arrancar el set (400 con el motivo) y la copia sí."""
    ids = [it["id"] for it in escena["items"]]
    copia = escena["items"][3]["id"]
    assert server.db.reordenar(escena["pid"], [copia] + [i for i in ids if i != copia])
    r = client.get("/api/radio/set", params={"playlist": escena["pid"],
                                             "track": escena["rid"]["uno.wav"]})
    assert r.status_code == 400, r.text
    assert "es el mismo tema que «Artista A — Uno» (#1 de la playlist)" in r.json()["error"]
    d = _set(client, escena, desde="uno copia.wav")
    assert d["pasos"][0]["track"]["id"] == escena["rid"]["uno copia.wav"]
    primero = d["pool"]["afuera"][0]
    assert (primero["titulo"], primero["motivo"], primero["duplicado_de"]) == ("Uno", "duplicado", copia)
    assert d["pool"]["entran"] == 5


# ------------------------------------------------------------------ determinismo

def test_determinismo_randomness_0_y_misma_semilla(server, client, escena):
    clave = lambda d: [(p["track"]["id"], p["motivo"]) for p in d["pasos"]]  # noqa: E731
    a = _set(client, escena, randomness=0, semilla=1)
    b = _set(client, escena, randomness=0, semilla=99)
    assert clave(a) == clave(b), "con randomness=0 la semilla del azar cambió el set"
    # Reordenar la playlist (sin cambiar qué entra) no cambia el set.
    ids = [it["id"] for it in escena["items"]]
    dos = escena["items"][1]["id"]
    server.db.reordenar(escena["pid"], [i for i in ids if i != dos] + [dos])
    assert clave(_set(client, escena, randomness=0)) == clave(a), "el orden de la playlist movió el set"
    # Con azar, la misma semilla da lo mismo.
    x = _set(client, escena, randomness=0.6, semilla=7)
    y = _set(client, escena, randomness=0.6, semilla=7)
    assert clave(x) == clave(y)


# ------------------------------------------------------------------ pedidos inválidos

def test_sin_playlist_o_con_una_que_no_existe_no_hay_set(client, escena):
    rid = escena["rid"]["uno.wav"]
    for params, codigo, pista in (({"track": rid}, 400, "playlist"),
                                  ({"track": rid, "playlist": "abc"}, 400, "playlist"),
                                  ({"track": rid, "playlist": "999999"}, 404, "no existe")):
        r = client.get("/api/radio/set", params=params)
        assert r.status_code == codigo, (params, r.text)
        assert pista in r.json()["error"], r.json()


def test_semilla_sin_genero_o_fuera_de_la_playlist_es_400(server, client, escena):
    r = client.get("/api/radio/set", params={"playlist": escena["pid"], "track": escena["rid"]["ocho.wav"]})
    assert r.status_code == 400 and "no tiene género" in r.json()["error"], r.text
    # Un tema del motor que NO está en esta playlist no puede ser semilla.
    otra = _crear_playlist(server, escena["rutas"], PLAYLIST[:2], nombre="Chica")
    r = client.get("/api/radio/set", params={"playlist": otra, "track": escena["rid"]["seis.wav"]})
    assert r.status_code == 400 and "Chica" in r.json()["error"], r.text


# ------------------------------------------------------------------ /api/radio/playlists/{id}

def test_la_playlist_como_la_ve_la_radio(client, escena):
    d = client.get(f"/api/radio/playlists/{escena['pid']}").json()
    assert (d["estado"], d["puede_armar"], d["motivo_no_puede"]) == ("ok", True, None)
    assert [(s["titulo"], s["genero"]) for s in d["semillas"]] == [
        ("Uno", "Techno"), ("Dos", "Techno"), ("Seis", "House"), ("Tres", "Techno"),
        ("Cuatro", "Techno"), ("Cinco", "Techno"), ("Siete", "House")]
    assert d["semillas"][0]["bpm"] == 128.4 and d["semillas"][0]["camelot"] == "8A"
    assert [(g["genero"], g["entran"], g["total"]) for g in d["generos"]] == \
        [("Techno", 5, 11), ("House", 2, 11)]
    # Lo mismo que dice el set armado desde ese género.
    techno = d["generos"][0]
    s = _set(client, escena)
    assert (techno["excluidos"], techno["afuera"]) == (s["pool"]["excluidos"], s["pool"]["afuera"])
    assert {t["titulo"]: t["motivo"] for t in d["temas"]}["Nueve"] == "sin_archivo"
    assert d["motor_total"] == 10


def test_sin_dos_temas_del_mismo_genero_no_se_puede_armar(server, client, escena):
    pid = _crear_playlist(server, escena["rutas"], [PLAYLIST[0], PLAYLIST[2], PLAYLIST[5]], "Suelta")
    d = client.get(f"/api/radio/playlists/{pid}").json()
    assert d["puede_armar"] is False
    assert d["motivo_no_puede"] == ("Hace falta al menos 2 temas analizados del mismo género "
                                    "(el que más tiene, 1). 1 sin género."), d["motivo_no_puede"]
    assert client.get("/api/radio/playlists/999999").status_code == 404
    assert client.get("/api/radio/playlists/abc").status_code == 404


def test_un_analisis_nuevo_se_ve_en_el_proximo_set_aunque_haya_cache(client, escena):
    """f55: la biblioteca normalizada se cachea entre pedidos (latencia §4), pero un re-análisis
    (otra conexión a la base, como el análisis en segundo plano o un scan) se ve en el set
    siguiente: el BPM, la energía y el porqué son los de la base de AHORA."""
    _set(client, escena)                       # llena la caché
    with Store(escena["db"]) as store:
        store.upsert(escena["rutas"]["dos.wav"],
                     TrackFeatures(bpm=129.1, key="9A", energy_raw=0.9, embedding=embedding(1),
                                   key_acuerdo="3/3", key_tramos="9A|9A|9A"),
                     duration=300.0, license=LICENCIA, source_url=ORIGEN, artist="Artista B", title="Dos")
    d = _set(client, escena, largo=4)
    t = _motor(escena["db"])
    pool = [t[n] for n in ("uno.wav", "dos.wav", "tres.wav", "cuatro.wav", "cinco.wav")]
    esperado = build_set(t["uno.wav"], pool, RadioConfig(length=4))
    assert [(p["track"]["label"], p["track"]["bpm"], p["track"]["energia"], p["motivo"]) for p in d["pasos"]] == \
        [(x.label, round(x.bpm, 1), round(x.energy, 3), m) for x, m in zip(esperado.tracks, esperado.reasons(), strict=True)]
    dos = next(p for p in d["pasos"] if p["track"]["titulo"] == "Dos")
    assert dos["track"]["bpm"] == 129.1, "el set siguió con el BPM de antes del re-análisis"


def test_latencia_de_la_radio_desde_playlist(client, escena):
    """Spec §4: < 200 ms. Acá con la base chica (con 10k tracks se midió aparte: ver el
    informe de f55); lo que protege es que el endpoint no haga trabajo por tema de más."""
    _set(client, escena)
    t0 = time.perf_counter()
    for _ in range(5):
        _set(client, escena)
    assert (time.perf_counter() - t0) / 5 < 0.2


# ------------------------------------------------------------------ guardar como playlist

def _guardar(client, escena, d, **extra):
    return client.post(f"/api/radio/playlists/{escena['pid']}/guardar",
                       json={"track": d["semilla"]["id"], **d["config"],
                             "esperado": [p["track"]["id"] for p in d["pasos"]], **extra})


def test_guardar_el_set_como_playlist_nueva_en_su_orden(server, client, escena):
    d = _set(client, escena, largo=4)
    r = _guardar(client, escena, d, nombre="Noche")
    assert r.status_code == 201, r.text
    nueva = r.json()["playlist"]
    assert (nueva["nombre"], nueva["total"]) == ("Noche", 4)
    leida = server.db.get_playlist_mia(nueva["id"], True)
    assert [it["titulo"] for it in leida["items"]] == [p["track"]["titulo"] for p in d["pasos"]]
    ruta_de = {escena["rid"][n]: str(r) for n, r in escena["rutas"].items()}
    assert [it["ruta"] for it in leida["items"]] == [ruta_de[p["track"]["id"]] for p in d["pasos"]], \
        "la playlist nueva no tiene los archivos del set en su orden"
    assert leida["items"][0]["ruta"] == str(escena["rutas"]["uno.wav"])
    assert {it["genero"] for it in leida["items"]} == {"Techno"}
    # La de origen no cambió.
    assert len(server.db.get_playlist_mia(escena["pid"])["items"]) == len(PLAYLIST)
    # Sin nombre, uno que dice de dónde sale.
    r2 = _guardar(client, escena, d)
    assert r2.json()["playlist"]["nombre"] == "Mezcla · set desde Uno (peak)"


def test_guardar_se_niega_si_el_set_no_es_el_que_se_ve(server, client, escena):
    d = _set(client, escena, largo=4)
    antes = len(server.db.listar_playlists())
    ids = [p["track"]["id"] for p in d["pasos"]]
    r = client.post(f"/api/radio/playlists/{escena['pid']}/guardar",
                    json={"track": d["semilla"]["id"], **d["config"], "esperado": ids[::-1]})
    assert r.status_code == 409 and r.json()["armado"] == ids, r.text
    r = client.post(f"/api/radio/playlists/{escena['pid']}/guardar",
                    json={"track": d["semilla"]["id"], **d["config"]})
    assert r.status_code == 400 and "esperado" in r.json()["error"], r.text
    assert len(server.db.listar_playlists()) == antes, "un pedido rechazado creó una playlist"


def test_guardar_rechaza_otras_paginas_y_lo_que_no_es_json(server, client, escena):
    d = _set(client, escena, largo=2)
    antes = len(server.db.listar_playlists())
    r = client.post(
        f"/api/radio/playlists/{escena['pid']}/guardar", headers={"Origin": "https://malo.example"},
        json={"track": d["semilla"]["id"], "esperado": [p["track"]["id"] for p in d["pasos"]]})
    assert r.status_code == 403, r.text
    r = client.post(f"/api/radio/playlists/{escena['pid']}/guardar", content=b"{}",
                    headers={"Content-Type": "text/plain"})
    assert r.status_code == 415, r.text
    # Y el guardado de sets del motor también se cuida de otras páginas.
    r = client.post("/api/radio/sets", headers={"Sec-Fetch-Site": "cross-site"}, json={})
    assert r.status_code == 403, r.text
    assert len(server.db.listar_playlists()) == antes


# ------------------------------------------------------------------ puntos de color (cues)

def test_el_conteo_de_marcas_dice_de_que_tipo_es_cada_una(client, escena):
    with Store(escena["db"]) as store:
        store.add_cue_mark(escena["rutas"]["uno.wav"], "cue", 1.0, num=2)
        store.add_cue_mark(escena["rutas"]["uno.wav"], "memory", 3.0)
        store.add_cue_mark(escena["rutas"]["uno.wav"], "loop", 5.0, 9.0)
    d = _set(client, escena)          # deja los ids en el índice de la radio
    ids = [p["track"]["id"] for p in d["pasos"]]
    c = client.get("/api/radio/marcas/conteo", params={"ids": ",".join(ids)}).json()
    uno = escena["rid"]["uno.wav"]
    assert c["conteos"][uno] == 3
    assert c["marcas"] == {uno: {"total": 3, "pads": [2], "memory": 1, "loop": 1}}
