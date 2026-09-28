"""Tests de /api/radio/sets* (sets guardados y calificación de transiciones, tarea 16).

Misma base sintética que `test_server_radio.py` (`tests/sinteticos.py`). Lo central: el
set que se guarda es EXACTAMENTE el que mostró /api/radio/set, y lo que se lee después es la
foto de ese momento, no un re-armado.
"""
import importlib
import sys

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
from sinteticos import CATALOGO, LICENCIA, ORIGEN, armar_base_radio, embedding  # noqa: E402

from motor.modelos import TrackFeatures  # noqa: E402
from motor.radio import RadioConfig, build_set  # noqa: E402
from motor.saved_sets import fingerprint, snapshot_steps  # noqa: E402
from motor.store import Store  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """El módulo `server` con sus efectos de carga neutralizados (receta de
    `tests/test_server_radio.py`)."""
    datos = tmp_path_factory.mktemp("server-sets-datos")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MUSIFLIX_DATA_DIR", str(datos))
        mp.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
        mp.delenv("REDIS_URL", raising=False)
        mp.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        for mod in ("server", "jobs", "db"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
    return srv


@pytest.fixture
def client(server, monkeypatch):
    monkeypatch.setattr(server, "_radio_audio", {})
    return TestClient(server.app)


@pytest.fixture
def biblioteca(tmp_path, monkeypatch):
    db = tmp_path / "djradio" / "biblioteca.sqlite"
    _, rutas = armar_base_radio(tmp_path / "musica", db)
    monkeypatch.setenv("DJRADIO_DB", str(db))
    return {"db": db, "rutas": rutas}


def _mostrado(server, client, biblioteca, largo=4) -> tuple[dict, dict]:
    """Lo que la pantalla muestra (/api/radio/set) y el cuerpo con que lo guardaría."""
    params = {"track": server._radio_id(biblioteca["rutas"]["uno.wav"]), "largo": largo}
    d = client.get("/api/radio/set", params=params).json()
    return d, {**params, "esperado": [p["track"]["id"] for p in d["pasos"]],
               "huella": d["huella"]}


# Los campos de un paso que muestran las dos respuestas: en el set guardado tienen que ser
# los MISMOS valores que mostró /api/radio/set.
CAMPOS_TRACK = ("id", "label", "titulo", "artista", "bpm", "camelot", "tonalidad",
                "key_dudosa", "energia", "energia_pct", "dur", "es_track")


def _como_se_mostro(paso: dict) -> tuple:
    return (paso["n"], paso["motivo"], paso["es_semilla"], paso["transicion"],
            tuple(paso["track"][c] for c in CAMPOS_TRACK))


def test_guardar_guarda_lo_que_se_mostro(server, client, biblioteca):
    mostrado, cuerpo = _mostrado(server, client, biblioteca)
    r = client.post("/api/radio/sets", json={**cuerpo, "nombre": "noche 1"})
    assert r.status_code == 201, r.text
    s = r.json()["set"]

    assert [_como_se_mostro(p) for p in s["pasos"]] == \
        [_como_se_mostro(p) for p in mostrado["pasos"]]
    assert (s["nombre"], s["total"], s["pedidos"], s["completo"], s["fragmentos"]) == \
        ("noche 1", 4, 4, True, 1)
    assert s["config"] == mostrado["config"], "la config guardada no es la que se usó"
    assert s["resumen"] == {"ok": 0, "regular": 0, "mala": 0, "sin_calificar": 3}
    assert [(t["n"], t["desde"], t["hasta"], t["calificacion"]) for t in s["transiciones"]] == \
        [(1, 1, 2, None), (2, 2, 3, None), (3, 3, 4, None)]
    # El porqué de la transición n es el del paso n + 1: el que se mostró llegando a ese track.
    assert [t["motivo_motor"] for t in s["transiciones"]] == \
        [p["motivo"] for p in mostrado["pasos"][1:]]
    assert all(p["track"]["en_biblioteca"] for p in s["pasos"])
    assert [p["track"]["licencia"] for p in s["pasos"]] == [LICENCIA] * 4

    # Leerlo después da lo mismo que devolvió el guardado.
    leido = client.get(f"/api/radio/sets/{s['id']}").json()
    assert (leido["estado"], leido["set"]) == ("ok", s)
    listado = client.get("/api/radio/sets").json()["sets"]
    assert [(x["id"], x["nombre"], x["semilla"], x["total"], x["curva"]) for x in listado] == \
        [(s["id"], "noche 1", "Artista A — Uno", 4, "peak")]


def test_la_huella_de_la_pantalla_es_la_de_la_foto_del_motor(server, client, biblioteca):
    """La `huella` que devuelve /api/radio/set es la de la foto que armaría el motor con ese
    set, calculada acá por separado."""
    mostrado, _ = _mostrado(server, client, biblioteca)
    with Store(biblioteca["db"]) as store:
        lib = store.load_library()
    semilla = next(t for t in lib if t.path.name == "uno.wav")
    assert mostrado["huella"] == fingerprint(snapshot_steps(build_set(semilla, lib,
                                                                      RadioConfig(length=4))))


def test_guardar_se_niega_si_el_set_ya_no_es_el_de_la_pantalla(server, client, biblioteca):
    _, cuerpo = _mostrado(server, client, biblioteca)
    ids = cuerpo["esperado"]
    otro = [ids[0], ids[2], ids[1], ids[3]]
    r = client.post("/api/radio/sets", json={**cuerpo, "esperado": otro})
    assert r.status_code == 409, r.text
    assert (r.json()["esperado"], r.json()["armado"]) == (otro, ids)
    assert client.get("/api/radio/sets").json()["sets"] == [], "guardó igual"


def test_guardar_se_niega_si_un_reescaneo_cambio_lo_que_se_mostro(server, client, biblioteca):
    """Mismos tracks, otro dato: entre que se mostró y se guardó, un re-escaneo cambió el
    acuerdo de la key de dos.wav (el `?` que se ve). Los ids coinciden; la huella no."""
    _, cuerpo = _mostrado(server, client, biblioteca)
    i = next(i for i, c in enumerate(CATALOGO) if c[0] == "dos.wav")
    _, bpm, key, _, _, dur, artista, titulo, e = CATALOGO[i]
    with Store(biblioteca["db"]) as store:
        store.upsert(biblioteca["rutas"]["dos.wav"],
                     TrackFeatures(bpm=bpm, key=key, energy_raw=e, embedding=embedding(i),
                                   key_acuerdo="3/3", key_tramos="9A|9A|9A"),
                     duration=dur, license=LICENCIA, source_url=ORIGEN, artist=artista,
                     title=titulo)
    r = client.post("/api/radio/sets", json=cuerpo)
    assert r.status_code == 409, r.text
    assert r.json()["huella_esperada"] == cuerpo["huella"]
    assert r.json()["huella_armada"] != cuerpo["huella"]
    assert client.get("/api/radio/sets").json()["sets"] == []


@pytest.mark.parametrize(("cambio", "pista"), [
    ({"esperado": None}, "esperado"),
    ({"esperado": []}, "esperado"),
    ({"esperado": [1, 2]}, "esperado"),
    ({"largo": "4"}, "largo"),
    ({"largo": True}, "largo"),
    ({"nombre": 5}, "nombre"),
    ({"curva": "inexistente"}, "curva"),
    ({"nombre": "x" * 201}, "nombre"),
])
def test_guardar_pedido_invalido_es_400_y_no_guarda(server, client, biblioteca, cambio, pista):
    _, cuerpo = _mostrado(server, client, biblioteca)
    r = client.post("/api/radio/sets", json={**cuerpo, **cambio})
    assert r.status_code == 400, r.text
    assert pista in r.json()["error"], r.json()
    assert client.get("/api/radio/sets").json()["sets"] == []


def test_calificar_cambiar_borrar_y_renombrar(server, client, biblioteca):
    _, cuerpo = _mostrado(server, client, biblioteca)
    sid = client.post("/api/radio/sets", json=cuerpo).json()["set"]["id"]
    base = f"/api/radio/sets/{sid}"

    r = client.put(f"{base}/transiciones/3", json={"calificacion": "mala"})
    assert r.status_code == 400 and "motivo" in r.json()["error"], r.text
    r = client.put(f"{base}/transiciones/3", json={"calificacion": "mala", "motivo": "choque"})
    assert r.status_code == 200, r.text
    t = r.json()["transicion"]
    assert (t["n"], t["desde"], t["hasta"], t["calificacion"], t["motivo"]) == \
        (3, 3, 4, "mala", "choque")
    assert r.json()["resumen"] == {"ok": 0, "regular": 0, "mala": 1, "sin_calificar": 2}
    client.put(f"{base}/transiciones/1", json={"calificacion": "ok"})
    client.put(f"{base}/transiciones/2", json={"calificacion": "regular", "motivo": "justo"})
    client.put(f"{base}/transiciones/1", json={"calificacion": "regular"})     # la cambia
    r = client.delete(f"{base}/transiciones/2")
    assert (r.json()["borrada"], r.json()["resumen"]) == \
        (True, {"ok": 0, "regular": 1, "mala": 1, "sin_calificar": 1})
    assert client.delete(f"{base}/transiciones/2").json()["borrada"] is False

    for n in (0, 4, -1):
        r = client.put(f"{base}/transiciones/{n}", json={"calificacion": "ok"})
        assert r.status_code == 400 and "de 1 a 3" in r.json()["error"], (n, r.text)

    r = client.patch(base, json={"nombre": "renombrado"})
    assert (r.status_code, r.json()["nombre"]) == (200, "renombrado")

    s = client.get(base).json()["set"]
    assert s["nombre"] == "renombrado"
    assert [(t["calificacion"], t["motivo"]) for t in s["transiciones"]] == \
        [("regular", None), (None, None), ("mala", "choque")]

    assert client.delete(base).json()["borrado"] == sid
    assert client.get(base).status_code == 404
    assert client.get("/api/radio/sets").json()["sets"] == []


def test_set_inexistente_es_404(client, biblioteca):
    assert client.get("/api/radio/sets/99").status_code == 404
    assert client.put("/api/radio/sets/99/transiciones/1",
                      json={"calificacion": "ok"}).status_code == 404
    assert client.delete("/api/radio/sets/99/transiciones/1").status_code == 404
    assert client.patch("/api/radio/sets/99", json={"nombre": "x"}).status_code == 404
    assert client.delete("/api/radio/sets/99").status_code == 404
    assert client.get("/api/radio/sets/no-es-un-id").status_code == 422


def test_un_track_que_ya_no_esta_se_muestra_entero_y_sin_audio(server, client, biblioteca):
    mostrado, cuerpo = _mostrado(server, client, biblioteca)
    sid = client.post("/api/radio/sets", json=cuerpo).json()["set"]["id"]
    with Store(biblioteca["db"]) as store:
        assert store.delete(biblioteca["rutas"]["dos.wav"])

    s = client.get(f"/api/radio/sets/{sid}").json()["set"]
    assert [_como_se_mostro(p) for p in s["pasos"]] == \
        [_como_se_mostro(p) for p in mostrado["pasos"]], "la foto cambió al faltar un track"
    assert [(p["track"]["label"], p["track"]["en_biblioteca"], p["track"]["audio"] is None)
            for p in s["pasos"]] == [
        ("Artista A — Uno", True, False), ("Artista F — Cinco", True, False),
        ("Artista B — Dos", False, True), ("Artista C — Tres", True, False)]
    assert s["faltan"] == 1
    assert client.get("/api/radio/sets").json()["sets"][0]["faltan"] == 1


def test_sin_base_degrada_sin_500_y_no_crea_la_base(client, tmp_path, monkeypatch):
    fantasma = tmp_path / "no-existe" / "biblioteca.sqlite"
    monkeypatch.setenv("DJRADIO_DB", str(fantasma))

    d = client.get("/api/radio/sets").json()
    assert (d["configurada"], d["estado"], d["sets"]) == (False, "sin-base", [])
    assert str(fantasma) in d["motivo"]
    d = client.get("/api/radio/sets/1").json()
    assert (d["estado"], d["set"]) == ("sin-base", None)
    for r in (client.post("/api/radio/sets", json={"track": "x", "esperado": ["a"]}),
              client.put("/api/radio/sets/1/transiciones/1", json={"calificacion": "ok"}),
              client.delete("/api/radio/sets/1/transiciones/1"),
              client.patch("/api/radio/sets/1", json={"nombre": "x"}),
              client.delete("/api/radio/sets/1")):
        assert r.status_code == 409, r.text
        assert (r.json()["estado"], r.json()["error"]) == ("sin-base", r.json()["motivo"])
    assert not fantasma.exists(), "un endpoint de sets creó la base"


def test_base_ocupada_es_409_rapido_y_no_un_500(server, client, biblioteca):
    import sqlite3

    _, cuerpo = _mostrado(server, client, biblioteca)
    sid = client.post("/api/radio/sets", json=cuerpo).json()["set"]["id"]
    otro = sqlite3.connect(str(biblioteca["db"]), timeout=0.1)
    otro.execute("BEGIN EXCLUSIVE")
    try:
        r = client.put(f"/api/radio/sets/{sid}/transiciones/1", json={"calificacion": "ok"})
    finally:
        otro.rollback()
        otro.close()
    assert r.status_code == 409, r.text
    assert r.json()["estado"] == "base-ocupada", r.json()
    assert client.get(f"/api/radio/sets/{sid}").json()["set"]["resumen"]["ok"] == 0
