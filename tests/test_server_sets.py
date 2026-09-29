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
from sinteticos import (  # noqa: E402
    CATALOGO,
    LICENCIA,
    ORIGEN,
    armar_base_radio,
    crear_playlist_radio,
    embedding,
)

from motor.modelos import TrackFeatures  # noqa: E402
from motor.radio import RadioConfig, build_set  # noqa: E402
from motor.saved_sets import fingerprint, shown_header, snapshot_steps  # noqa: E402
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
        srv.db.init_db()        # las tablas de MusiFlix (playlists): el lifespan no corre acá
    return srv


@pytest.fixture
def client(server, monkeypatch):
    monkeypatch.setattr(server, "_radio_audio", {})
    return TestClient(server.app)


@pytest.fixture
def biblioteca(server, tmp_path, monkeypatch):
    db = tmp_path / "djradio" / "biblioteca.sqlite"
    _, rutas = armar_base_radio(tmp_path / "musica", db)
    monkeypatch.setenv("DJRADIO_DB", str(db))
    # Desde f33 la radio arma solo desde una playlist: el CATALOGO entero, un solo género.
    return {"db": db, "rutas": rutas, "playlist": crear_playlist_radio(server.db, rutas)}


def _mostrado(server, client, biblioteca, largo=4) -> tuple[dict, dict]:
    """Lo que la pantalla muestra (/api/radio/set) y el cuerpo con que lo guardaría."""
    params = {"playlist": biblioteca["playlist"],
              "track": server._radio_id(biblioteca["rutas"]["uno.wav"]), "largo": largo}
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
    config = RadioConfig(length=4)
    rset = build_set(semilla, lib, config)
    assert mostrado["huella"] == fingerprint(snapshot_steps(rset), shown_header(rset, config))


def test_guardar_con_la_config_entera_en_json_como_la_manda_el_navegador(server, client, biblioteca):
    """La pantalla guarda mandando la `config` que devolvió /api/radio/set, en JSON. Un
    navegador serializa 0.0 como `0`: `randomness` y `mmr_lambda` pueden llegar como enteros.
    Tienen que dar la MISMA huella que el set que se mostró (que vino por la query, donde
    FastAPI los parsea como float); si no, guardar desde la pantalla daba 409 siempre."""
    mostrado, cuerpo = _mostrado(server, client, biblioteca)
    config = {k: (int(v) if isinstance(v, float) and v.is_integer() else v)
              for k, v in mostrado["config"].items() if v is not None}
    assert any(isinstance(v, int) and k in ("randomness", "mmr_lambda") for k, v in config.items()), \
        f"la config de fábrica no tiene un float entero: el test no probaría nada ({config})"
    r = client.post("/api/radio/sets", json={**cuerpo, **config})
    assert r.status_code == 201, r.text
    assert r.json()["set"]["config"] == mostrado["config"]


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


def test_guardar_se_niega_si_la_playlist_cambio_entre_mostrar_y_guardar(server, client,
                                                                        biblioteca):
    """Se saca de la playlist un tema que estaba en el set mostrado: el re-armado ya no lo
    tiene, los ids no coinciden y el guardado es 409 — no se guarda un set que no se vio."""
    mostrado, cuerpo = _mostrado(server, client, biblioteca)
    titulo = mostrado["pasos"][2]["track"]["titulo"]
    pl = server.db.get_playlist_radio(biblioteca["playlist"])
    item = next(it for it in pl["items"] if it["titulo"] == titulo)
    assert server.db.quitar_item(item["id"])
    r = client.post("/api/radio/sets", json=cuerpo)
    assert r.status_code == 409, r.text
    assert titulo not in [p["track"]["titulo"] for p in
                          client.get("/api/radio/set", params={
                              k: cuerpo[k] for k in ("playlist", "track", "largo")}).json()["pasos"]]
    assert r.json()["esperado"] == cuerpo["esperado"] and r.json()["armado"] != cuerpo["esperado"]
    assert client.get("/api/radio/sets").json()["sets"] == []


@pytest.mark.parametrize(("cambio", "pista"), [
    ({"esperado": None}, "esperado"),
    ({"esperado": []}, "esperado"),
    ({"esperado": [1, 2]}, "esperado"),
    ({"huella": None}, "huella"),
    ({"huella": ""}, "huella"),
    ({"huella": 5}, "huella"),
    ({"largo": "4"}, "largo"),
    ({"largo": True}, "largo"),
    ({"nombre": 5}, "nombre"),
    ({"curva": "inexistente"}, "curva"),
    ({"nombre": "x" * 201}, "nombre"),
    # Desde f33 el set sale de una playlist: sin ella (o con una que no existe) no se guarda.
    ({"playlist": None}, "playlist"),
    ({"playlist": "1"}, "playlist"),
    ({"playlist": 987654}, "987654"),
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


def test_sin_base_degrada_sin_500_y_no_crea_la_base(server, client, tmp_path, monkeypatch):
    fantasma = tmp_path / "no-existe" / "biblioteca.sqlite"
    monkeypatch.setenv("DJRADIO_DB", str(fantasma))
    pid = server.db.crear_playlist("sin base")["id"]

    d = client.get("/api/radio/sets").json()
    assert (d["configurada"], d["estado"], d["sets"]) == (False, "sin-base", [])
    assert str(fantasma) in d["motivo"]
    d = client.get("/api/radio/sets/1").json()
    assert (d["estado"], d["set"]) == ("sin-base", None)
    for r in (client.post("/api/radio/sets", json={"playlist": pid, "track": "x",
                                                     "esperado": ["a"], "huella": "h"}),
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


def test_sin_huella_no_guarda_aunque_los_ids_coincidan(server, client, biblioteca):
    """El caso que encontró la auditoría: sin huella, tras un re-escaneo de dos.wav se
    guardaba 130.4 BPM y 9A sin `?` cuando la pantalla mostraba 130.0 BPM y 9A?. Ahora la
    huella es obligatoria: sin ella, 400, y no se guarda nada."""
    _, cuerpo = _mostrado(server, client, biblioteca)
    sin = {k: v for k, v in cuerpo.items() if k != "huella"}
    r = client.post("/api/radio/sets", json=sin)
    assert r.status_code == 400 and "huella" in r.json()["error"], r.text
    assert client.get("/api/radio/sets").json()["sets"] == []


def test_un_set_cortado_se_guarda_con_el_largo_pedido_y_su_corte(server, client, biblioteca):
    mostrado, cuerpo = _mostrado(server, client, biblioteca, largo=10)
    assert mostrado["completo"] is False and mostrado["total"] < 10, \
        "el set de 10 no se cortó: el test no probaría nada"
    r = client.post("/api/radio/sets", json=cuerpo)
    assert r.status_code == 201, r.text
    for s in (r.json()["set"], client.get(f"/api/radio/sets/{r.json()['set']['id']}").json()["set"]):
        assert (s["total"], s["pedidos"], s["completo"], s["corte"]) == \
            (mostrado["total"], 10, False, mostrado["corte"]), s
    listado = client.get("/api/radio/sets").json()["sets"][0]
    assert (listado["total"], listado["pedidos"]) == (mostrado["total"], 10)


@pytest.mark.parametrize("set_id", [str(2 ** 63), "9" * 26])
def test_un_id_enorme_es_404_y_no_un_500(client, biblioteca, set_id):
    base = f"/api/radio/sets/{set_id}"
    for r in (client.get(base), client.patch(base, json={"nombre": "x"}), client.delete(base),
              client.put(f"{base}/transiciones/1", json={"calificacion": "ok"}),
              client.delete(f"{base}/transiciones/1")):
        assert r.status_code == 404, (r.request.method, r.text)


def test_una_transicion_enorme_es_400(server, client, biblioteca):
    _, cuerpo = _mostrado(server, client, biblioteca)
    sid = client.post("/api/radio/sets", json=cuerpo).json()["set"]["id"]
    r = client.put(f"/api/radio/sets/{sid}/transiciones/{'9' * 26}", json={"calificacion": "ok"})
    assert r.status_code == 400 and "de 1 a 3" in r.json()["error"], r.text


def test_un_set_borrado_en_el_medio_es_404_y_no_base_ilegible(server, client, biblioteca,
                                                            monkeypatch):
    """Otro proceso borra el set entre el pedido y la escritura: tiene que decir que el set no
    existe, no que la base está rota."""
    import sqlite3

    _, cuerpo = _mostrado(server, client, biblioteca)
    sid = client.post("/api/radio/sets", json=cuerpo).json()["set"]["id"]
    original = Store._escritura

    def escritura(self):
        otro = sqlite3.connect(str(biblioteca["db"]))
        for sql in ("DELETE FROM saved_set_steps WHERE set_id = ?",
                    "DELETE FROM saved_sets WHERE id = ?"):
            otro.execute(sql, (sid,))
        otro.commit()
        otro.close()
        return original(self)

    monkeypatch.setattr(Store, "_escritura", escritura)
    r = client.put(f"/api/radio/sets/{sid}/transiciones/1", json={"calificacion": "ok"})
    assert r.status_code == 404, r.text
    assert str(sid) in r.json()["error"], r.json()


# --- .m3u8 de un set guardado: la FOTO, no un re-armado --------------------------------------

def _guardar_uno(server, client, biblioteca) -> tuple[dict, bytes, str]:
    """Guarda el set de uno.wav y devuelve (set guardado, bytes y Content-Disposition del
    .m3u8 del set ARMADO en ese mismo momento)."""
    _, cuerpo = _mostrado(server, client, biblioteca)
    vivo = client.get("/api/radio/set.m3u8",
                      params={"playlist": cuerpo["playlist"], "track": cuerpo["track"],
                              "largo": cuerpo["largo"]})
    assert vivo.status_code == 200, vivo.text
    s = client.post("/api/radio/sets", json=cuerpo).json()["set"]
    return s, vivo.content, vivo.headers["content-disposition"]


def test_el_m3u8_del_set_guardado_es_byte_a_byte_el_del_set_que_se_guardo(server, client,
                                                                           biblioteca):
    s, vivo, disp = _guardar_uno(server, client, biblioteca)
    r = client.get(f"/api/radio/sets/{s['id']}/m3u8")
    assert r.status_code == 200, r.text
    assert r.content == vivo, "el .m3u8 de la foto no es el del set armado que se guardó"
    assert r.headers["content-disposition"] == disp
    assert (r.headers["x-content-type-options"], r.headers["cache-control"],
            r.headers["x-djradio-faltan"]) == ("nosniff", "no-store", "0")
    # En el orden de la foto, con las rutas de la foto.
    rutas = [ln for ln in r.content.decode("utf-8").split("\r\n")
             if ln and not ln.startswith("#")]
    assert rutas == [str(biblioteca["rutas"][n])
                     for n in ("uno.wav", "cinco.wav", "dos.wav", "tres.wav")]


def test_el_m3u8_del_set_guardado_no_cambia_si_se_re_escanea_un_track(server, client,
                                                                     biblioteca):
    """Un re-escaneo cambia el BPM de dos.wav: el set armado de hoy lo exporta con el BPM
    nuevo, el guardado sigue diciendo el de la foto."""
    import sqlite3

    s, vivo, _ = _guardar_uno(server, client, biblioteca)
    con = sqlite3.connect(str(biblioteca["db"]))
    con.execute("UPDATE tracks SET bpm = 131.3 WHERE path_key = ?",
                (Store._key(biblioteca["rutas"]["dos.wav"]),))
    con.commit()
    con.close()
    hoy = client.get("/api/radio/set.m3u8",
                     params={"playlist": biblioteca["playlist"],
                             "track": s["pasos"][0]["track"]["id"], "largo": 4})
    assert b"bpm=131.3" in hoy.content and hoy.content != vivo, \
        "el re-escaneo no cambió el set de hoy: el test no probaría nada"
    r = client.get(f"/api/radio/sets/{s['id']}/m3u8")
    assert r.content == vivo
    assert b"bpm=130.0" in r.content and b"bpm=131.3" not in r.content


def test_el_m3u8_del_set_guardado_trae_la_ruta_que_ya_no_esta_y_dice_cuantas(server, client,
                                                                             biblioteca):
    s, vivo, _ = _guardar_uno(server, client, biblioteca)
    with Store(biblioteca["db"]) as store:
        assert store.delete(biblioteca["rutas"]["cinco.wav"])
    r = client.get(f"/api/radio/sets/{s['id']}/m3u8")
    assert r.status_code == 200, r.text
    assert r.content == vivo, "la ruta que ya no está no puede desaparecer del .m3u8 de la foto"
    assert str(biblioteca["rutas"]["cinco.wav"]).encode("utf-8") in r.content
    assert r.headers["x-djradio-faltan"] == "1"


def test_el_m3u8_de_un_set_que_no_existe_es_404(server, client, biblioteca):
    r = client.get("/api/radio/sets/999/m3u8")
    assert r.status_code == 404 and "999" in r.json()["error"], r.text


def test_un_float_gigante_es_400_y_no_500(server, client, biblioteca):
    """`float()` de un entero de cientos de dígitos tira OverflowError: tiene que ser el 400
    de un pedido inválido, no un 500."""
    _, cuerpo = _mostrado(server, client, biblioteca)
    for campo in ("randomness", "mmr_lambda"):
        r = client.post("/api/radio/sets", json={**cuerpo, campo: 10 ** 400})
        assert r.status_code == 400, r.text
        assert campo in r.json()["error"] and "fuera de rango" in r.json()["error"]
    assert client.get("/api/radio/sets").json()["sets"] == []
