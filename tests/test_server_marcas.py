"""Tests de la API del editor de cues (f48): /api/radio/tracks/{id}/marcas, /onda y
/api/radio/marcas/conteo.

Misma base sintética que `test_server_radio.py` (`tests/sinteticos.py`). Lo esperado sale del
store (lo que quedó en la base) o del catálogo, no de la API: si la API mintiera un tiempo o un
BPM, la comparación lo ve. Todo pedido inválido es 400 / 403 / 404 / 415 con el motivo, nunca
500.
"""
import importlib
import sys

import numpy as np
import pytest

pytest.importorskip("httpx")
import soundfile as sf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sinteticos import CATALOGO, armar_base_radio  # noqa: E402

from motor.store import Store  # noqa: E402
from motor.tonalidad import camelot_a_clasica  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """El módulo `server` con sus efectos de carga neutralizados (receta de
    `tests/test_server_radio.py`)."""
    datos = tmp_path_factory.mktemp("server-marcas-datos")
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
def biblioteca(tmp_path, monkeypatch, server):
    db = tmp_path / "djradio" / "biblioteca.sqlite"
    _, rutas = armar_base_radio(tmp_path / "musica", db)
    monkeypatch.setenv("DJRADIO_DB", str(db))
    # La caché de la onda de ESTE test, para que un test no lea la de otro.
    monkeypatch.setattr(server.db, "DATA_DIR", tmp_path / "datos")
    ids = {n: server._radio_id(r) for n, r in rutas.items()}
    return {"db": db, "rutas": rutas, "ids": ids}


def _url(b, nombre="uno.wav", marca=None):
    base = f"/api/radio/tracks/{b['ids'][nombre]}/marcas"
    return base if marca is None else f"{base}/{marca}"


def _en_base(b, nombre="uno.wav"):
    with Store(b["db"]) as store:
        return [(m.id, m.kind, m.num, m.start_ms, m.end_ms, m.name)
                for m in store.list_cue_marks(b["rutas"][nombre])]


def _como_api(filas):
    """Las filas del store como las tendría que mostrar la API (ms → s)."""
    return [(i, k, n, s / 1000, None if e is None else e / 1000, nom)
            for i, k, n, s, e, nom in filas]


def _de_api(marcas):
    return [(m["id"], m["tipo"], m["num"], m["inicio"], m["fin"], m["nombre"]) for m in marcas]


# --- leer -------------------------------------------------------------------------------

def test_el_track_trae_lo_medido_por_el_motor(client, biblioteca):
    r = client.get(_url(biblioteca))
    assert r.status_code == 200, r.text
    d = r.json()
    nombre, bpm, key, acuerdo, _tramos, dur, artista, titulo, _e = CATALOGO[0]
    t = d["track"]
    assert (t["id"], t["titulo"], t["artista"], t["bpm"], t["camelot"], t["tonalidad"],
            t["key_acuerdo"], t["dur"]) == \
        (biblioteca["ids"][nombre], titulo, artista, bpm, key, camelot_a_clasica(key), acuerdo, dur)
    assert (d["estado"], d["marcas"]) == ("ok", [])
    assert d["limites"] == {"hot_cues": 8, "memory": 32, "loops": 32, "nombre_max": 64}


def test_sin_bpm_medido_es_null_y_no_cero(client, biblioteca):
    """`cuatro.wav` tiene BPM 0.0 en la base (lo que da el análisis sobre silencio): no es una
    medición, la API dice null y la pantalla dibuja «?»."""
    t = client.get(_url(biblioteca, "cuatro.wav")).json()["track"]
    assert t["bpm"] is None and t["camelot"] is None


# --- crear, cambiar, borrar -------------------------------------------------------------

def test_crear_cambiar_y_borrar_queda_en_la_base(client, biblioteca):
    url = _url(biblioteca)
    r = client.post(url, json={"tipo": "cue", "inicio": 12.3456, "nombre": "Drop"})
    assert r.status_code == 201, r.text
    cue = r.json()["marca"]
    assert (cue["tipo"], cue["num"], cue["inicio"], cue["nombre"]) == ("cue", 0, 12.346, "Drop")
    assert client.post(url, json={"tipo": "memory", "inicio": 3.5}).status_code == 201
    r = client.post(url, json={"tipo": "loop", "inicio": 64, "fin": 71.5})
    assert r.status_code == 201, r.text
    loop = r.json()["marca"]
    # La lista que devuelve la escritura es la de la base, entera.
    assert _de_api(r.json()["marcas"]) == _como_api(_en_base(biblioteca))
    assert _de_api(client.get(url).json()["marcas"]) == _como_api(_en_base(biblioteca))

    r = client.patch(f"{url}/{cue['id']}", json={"inicio": 13.0, "nombre": "", "num": 4})
    assert r.status_code == 200, r.text
    assert (r.json()["marca"]["inicio"], r.json()["marca"]["nombre"], r.json()["marca"]["num"]) == \
        (13.0, None, 4)
    r = client.patch(f"{url}/{loop['id']}", json={"fin": 80.25})
    assert r.status_code == 200 and r.json()["marca"]["fin"] == 80.25
    assert _de_api(r.json()["marcas"]) == _como_api(_en_base(biblioteca))

    r = client.delete(f"{url}/{cue['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["borrada"] == cue["id"]
    assert cue["id"] not in [f[0] for f in _en_base(biblioteca)]
    assert _de_api(r.json()["marcas"]) == _como_api(_en_base(biblioteca))
    assert client.delete(f"{url}/{cue['id']}").status_code == 404


# --- pedidos inválidos: 400 con el motivo y la base sin tocar -------------------------------

@pytest.mark.parametrize(("cuerpo", "pista"), [
    ({"tipo": "hot", "inicio": 1}, "tipo de marca"),
    ({"inicio": 1}, "tipo de marca"),
    ({"tipo": "cue", "inicio": "12.5"}, "número"),
    ({"tipo": "cue"}, "número"),
    ({"tipo": "cue", "inicio": -1}, "negativo"),
    ({"tipo": "cue", "inicio": 240}, "dura 240.000"),
    ({"tipo": "cue", "inicio": 10 ** 400}, "fuera de rango"),
    # Finitos pero enormes (H1 de la auditoría: eran un 500 por OverflowError en `round`).
    ({"tipo": "cue", "inicio": 1e306}, "fuera de rango"),
    ({"tipo": "cue", "inicio": 1.7976931348623157e308}, "fuera de rango"),
    ({"tipo": "loop", "inicio": 1, "fin": 1e306}, "fuera de rango"),
    ({"tipo": "memory", "inicio": 1, "nombre": "a\u200bb"}, "invisibles"),
    ({"tipo": "memory", "inicio": 1, "nombre": "\ufeffintro"}, "invisibles"),
    ({"tipo": "cue", "inicio": 1, "num": 8}, "de 0 a 7"),
    ({"tipo": "cue", "inicio": 1, "num": "1"}, "de 0 a 7"),
    ({"tipo": "memory", "inicio": 1, "num": 2}, "solo un hot cue"),
    ({"tipo": "memory", "inicio": 1, "fin": 2}, "solo un loop"),
    ({"tipo": "loop", "inicio": 10}, "necesita la salida"),
    ({"tipo": "loop", "inicio": 10, "fin": 9.999}, "después de la entrada"),
    ({"tipo": "loop", "inicio": 230, "fin": 241}, "después del final"),
    ({"tipo": "cue", "inicio": 1, "nombre": "a\nb"}, "una sola línea"),
    ({"tipo": "cue", "inicio": 1, "nombre": "x" * 65}, "como mucho 64"),
    ({"tipo": "cue", "inicio": 1, "nombre": ["x"]}, "es un texto"),
    ({"tipo": "cue", "inicio": 1, "ruta": "C:/x.wav"}, "campos desconocidos: ruta"),
])
def test_crear_invalido_es_400(client, biblioteca, cuerpo, pista):
    r = client.post(_url(biblioteca), json=cuerpo)
    assert r.status_code == 400, r.text
    assert pista in r.json()["error"], r.json()
    assert _en_base(biblioteca) == []


def test_el_noveno_hot_cue_es_400(client, biblioteca):
    for i in range(8):
        assert client.post(_url(biblioteca), json={"tipo": "cue", "inicio": i}).status_code == 201
    r = client.post(_url(biblioteca), json={"tipo": "cue", "inicio": 9})
    assert r.status_code == 400 and "los 8 hot cues" in r.json()["error"]
    assert sorted(f[2] for f in _en_base(biblioteca)) == list(range(8))


@pytest.mark.parametrize(("cuerpo", "pista"), [
    ({}, "nada que cambiar"),
    ({"tipo": "memory"}, "campos desconocidos: tipo"),
    ({"inicio": 300}, "dura 240.000"),
    ({"fin": 5}, "solo un loop"),
    ({"num": None}, "siempre tiene número"),
    ({"num": 9}, "de 0 a 7"),
    ({"nombre": "\u202eal revés"}, "una sola línea"),
    ({"inicio": 1e306}, "fuera de rango"),
    ({"nombre": "x\u2060"}, "invisibles"),
])
def test_cambiar_invalido_es_400(client, biblioteca, cuerpo, pista):
    m = client.post(_url(biblioteca), json={"tipo": "cue", "inicio": 1}).json()["marca"]
    antes = _en_base(biblioteca)
    r = client.patch(_url(biblioteca, marca=m["id"]), json=cuerpo)
    assert r.status_code == 400, r.text
    assert pista in r.json()["error"], r.json()
    assert _en_base(biblioteca) == antes


@pytest.mark.parametrize(("contenido", "tipo"), [
    (b"no es json", "application/json"),
    (b"[1, 2]", "application/json"),
    (b'"texto"', "application/json"),
])
def test_cuerpo_que_no_es_un_objeto_json_es_400(client, biblioteca, contenido, tipo):
    r = client.post(_url(biblioteca), content=contenido, headers={"Content-Type": tipo})
    assert r.status_code == 400 and r.json()["error"], r.text
    assert _en_base(biblioteca) == []


# --- CSRF: misma regla que las otras escrituras -----------------------------------------

def test_pedido_de_otra_pagina_es_403_y_no_escribe(client, biblioteca):
    m = client.post(_url(biblioteca), json={"tipo": "cue", "inicio": 1}).json()["marca"]
    antes = _en_base(biblioteca)
    ajenos = [{"Origin": "https://malo.example"}, {"Sec-Fetch-Site": "cross-site"}]
    for h in ajenos:
        assert client.post(_url(biblioteca), json={"tipo": "memory", "inicio": 2},
                           headers=h).status_code == 403
        assert client.patch(_url(biblioteca, marca=m["id"]), json={"inicio": 2},
                            headers=h).status_code == 403
        assert client.delete(_url(biblioteca, marca=m["id"]), headers=h).status_code == 403
    assert _en_base(biblioteca) == antes
    # El mismo origen sí (como lo manda el navegador desde la app).
    r = client.post(_url(biblioteca), json={"tipo": "memory", "inicio": 2},
                    headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 201, r.text


def test_un_form_o_texto_plano_es_415(client, biblioteca):
    r = client.post(_url(biblioteca), content=b'{"tipo": "cue", "inicio": 1}',
                    headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    r = client.post(_url(biblioteca), data={"tipo": "cue", "inicio": "1"})
    assert r.status_code == 415
    assert _en_base(biblioteca) == []


# --- 404: track o marca que no existe, nunca 500 ------------------------------------------

@pytest.mark.parametrize("track_id", ["0123456789abcdef", "0123456789abcdeg", "ABCDEF0123456789",
                                      "x" * 300, "1" * 17])
def test_track_que_no_existe_es_404(client, biblioteca, track_id):
    for metodo, sufijo, cuerpo in (("GET", "/marcas", None),
                                   ("POST", "/marcas", {"tipo": "cue", "inicio": 1}),
                                   ("PATCH", "/marcas/1", {"inicio": 1}),
                                   ("DELETE", "/marcas/1", None),
                                   ("GET", "/onda", None)):
        r = client.request(metodo, f"/api/radio/tracks/{track_id}{sufijo}", json=cuerpo)
        assert r.status_code == 404, (metodo, sufijo, r.status_code, r.text)


@pytest.mark.parametrize("marca", ["9" * 30, "9223372036854775808", "999999", "abc", "-1",
                                   "1.5", "0"])
def test_marca_que_no_existe_es_404(client, biblioteca, marca):
    client.post(_url(biblioteca), json={"tipo": "cue", "inicio": 1})
    antes = _en_base(biblioteca)
    assert client.patch(_url(biblioteca, marca=marca), json={"inicio": 2}).status_code == 404
    assert client.delete(_url(biblioteca, marca=marca)).status_code == 404
    assert _en_base(biblioteca) == antes


def test_la_marca_de_otro_track_es_404(client, biblioteca):
    m = client.post(_url(biblioteca, "dos.wav"), json={"tipo": "cue", "inicio": 1}).json()["marca"]
    assert client.patch(_url(biblioteca, "uno.wav", m["id"]), json={"inicio": 2}).status_code == 404
    assert client.delete(_url(biblioteca, "uno.wav", m["id"])).status_code == 404
    assert [f[0] for f in _en_base(biblioteca, "dos.wav")] == [m["id"]]


# --- conteo y huérfanas -------------------------------------------------------------------

def test_conteo_y_huerfanas(client, biblioteca):
    ids = biblioteca["ids"]
    for t in (1, 2, 3):
        client.post(_url(biblioteca), json={"tipo": "memory", "inicio": t})
    client.post(_url(biblioteca, "dos.wav"), json={"tipo": "memory", "inicio": 1})
    r = client.get("/api/radio/marcas/conteo",
                   params={"ids": f"{ids['uno.wav']},{ids['dos.wav']},{ids['tres.wav']},nada"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["conteos"] == {ids["uno.wav"]: 3, ids["dos.wav"]: 1, ids["tres.wav"]: 0}
    assert d["huerfanas"] == {"tracks": 0, "marcas": 0, "archivos": []}

    # El archivo sale de la base (un scan que ya no lo encuentra): las marcas quedan y se
    # cuentan como huérfanas, con el nombre del archivo y no la ruta.
    with Store(biblioteca["db"]) as store:
        store.delete(biblioteca["rutas"]["uno.wav"])
    d = client.get("/api/radio/marcas/conteo", params={"ids": ids["dos.wav"]}).json()
    assert d["huerfanas"] == {"tracks": 1, "marcas": 3, "archivos": ["uno.wav"]}


def test_conteo_con_demasiados_ids_es_400(client, biblioteca):
    r = client.get("/api/radio/marcas/conteo", params={"ids": ",".join(["a"] * 501)})
    assert r.status_code == 400 and "500" in r.json()["error"]


# --- la onda -----------------------------------------------------------------------------

def test_la_onda_es_la_del_archivo_y_se_cachea(client, biblioteca):
    r = client.get(f"/api/radio/tracks/{biblioteca['ids']['uno.wav']}/onda")
    assert r.status_code == 200, r.text
    d = r.json()
    y, sr = sf.read(str(biblioteca["rutas"]["uno.wav"]), dtype="float32", always_2d=True)
    assert (d["bins"], d["duracion_audio"], d["sample_rate"], d["canales"], d["cache"]) == \
        (1000, round(len(y) / sr, 3), sr, 1, False)
    # Oráculo: el máximo de |muestra| de cada tramo, con el archivo leído entero. El tramo k
    # va del frame ceil(k·N/1000) al ceil((k+1)·N/1000): 5512 frames no dividen 1000 bins.
    amp = np.abs(y).max(axis=1)
    bordes = [-((-k * len(amp)) // 1000) for k in range(1001)]
    esperado = [round(float(amp[a:b].max()), 5) for a, b in zip(bordes[:-1], bordes[1:], strict=True)]
    assert d["picos"] == esperado
    otra = client.get(f"/api/radio/tracks/{biblioteca['ids']['uno.wav']}/onda").json()
    assert otra["cache"] is True and otra["picos"] == d["picos"]


def test_onda_sin_archivo_es_404_y_archivo_roto_es_422(client, biblioteca):
    biblioteca["rutas"]["dos.wav"].unlink()
    r = client.get(f"/api/radio/tracks/{biblioteca['ids']['dos.wav']}/onda")
    assert r.status_code == 404 and "no está en esta máquina" in r.json()["error"]
    biblioteca["rutas"]["tres.wav"].write_bytes(b"RIFF\x00\x00\x00\x00WAVEbasura" * 20)
    r = client.get(f"/api/radio/tracks/{biblioteca['ids']['tres.wav']}/onda")
    assert r.status_code == 422 and "no pude leer el audio" in r.json()["error"]


def _flac_danado(ruta, como):
    """Un FLAC de 3 s que libsndfile ABRE bien y que falla recién al decodificar el medio."""
    y = (0.5 * np.sin(np.arange(44100 * 3) / 20)).astype(np.float32)
    sf.write(str(ruta), np.stack([y, y], 1), 44100, format="FLAC")
    b = ruta.read_bytes()
    mitad = len(b) // 2
    ruta.write_bytes(b[:mitad] if como == "truncado"
                     else b[:mitad] + b"\x00" * 5000 + b[mitad + 5000:])


@pytest.mark.parametrize("como", ["truncado", "basura en el medio"])
def test_onda_de_un_flac_que_falla_a_mitad_es_422_y_no_500(client, biblioteca, como):
    """H2 de la auditoría: soundfile abre el archivo y revienta en el medio del stream; antes
    solo se capturaba al abrir y la API devolvía 500."""
    _flac_danado(biblioteca["rutas"]["tres.wav"], como)
    r = client.get(f"/api/radio/tracks/{biblioteca['ids']['tres.wav']}/onda")
    assert r.status_code == 422, r.text
    assert "se corta o está dañado" in r.json()["error"], r.json()


_VENENOS = {"PEAKS_NULL": ("peaks", [None] * 1000), "PEAKS_NAN": ("peaks", [float("nan")] * 1000),
            "PEAKS_ANIDADOS": ("peaks", [[1]] * 1000), "PEAKS_TEXTO": ("peaks", ["x"] * 1000),
            "DUR_TEXTO": ("duration_s", "abc"), "SR_CERO": ("sample_rate", 0)}


@pytest.mark.parametrize("veneno", ["null", "[]", '"x"', *_VENENOS])
def test_una_cache_envenenada_se_rehace_y_no_es_500(client, server, biblioteca, veneno):
    """H3: un JSON que parsea pero no es lo que se escribió se recalcula: ni 500 ni una onda
    falsa. Lo esperado es la primera respuesta, calculada del archivo."""
    import json

    url = f"/api/radio/tracks/{biblioteca['ids']['uno.wav']}/onda"
    bueno = client.get(url).json()
    (archivo,) = list((server.db.DATA_DIR / "peaks").glob("*.json"))
    if veneno in _VENENOS:
        j = json.loads(archivo.read_text(encoding="utf-8"))
        clave, valor = _VENENOS[veneno]
        j[clave] = valor
        archivo.write_text(json.dumps(j), encoding="utf-8")
    else:
        archivo.write_text(veneno, encoding="utf-8")
    r = client.get(url)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["cache"], d["picos"], d["duracion_audio"]) == \
        (False, bueno["picos"], bueno["duracion_audio"])
