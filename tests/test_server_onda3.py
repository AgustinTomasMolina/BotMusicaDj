"""Tests de la API de la onda de 3 bandas (f52): GET /api/radio/tracks/{id}/onda3.

Misma base sintética que `test_server_marcas.py` (`tests/sinteticos.py`). Para la grilla, el
WAV de `uno.wav` (128.4 BPM en la base) se reemplaza por un tren de kicks a 128.4 BPM con un
desfase CONOCIDO: lo esperado sale de esa construcción. Las bandas esperadas salen de
`motor.bandas` (probado contra un oráculo en `motor/tests/test_bandas.py`) reducidas acá a
mano; la API no puede servir otra cosa. Todo pedido inválido es 400 / 404 / 409 / 422 con el
motivo, nunca 500.
"""
import importlib
import math
import sys

import numpy as np
import pytest

pytest.importorskip("httpx")
import soundfile as sf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sinteticos import armar_base_radio  # noqa: E402

import motor.bandas as mb  # noqa: E402

SR = 22050
BPM_UNO = 128.4          # el de `uno.wav` en el CATALOGO
DESFASE = 0.0789
DUR = 30.0


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """El módulo `server` con sus efectos de carga neutralizados (receta de
    `tests/test_server_radio.py`)."""
    datos = tmp_path_factory.mktemp("server-onda3-datos")
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


def _kicks(ruta, bpm, desfase, dur):
    """Un kick (seno de 55 Hz con caída exponencial) en cada beat desde `desfase`. Devuelve
    los instantes REALES (redondeados a la muestra)."""
    t = np.arange(int(0.12 * SR)) / SR
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 28)
    y = np.zeros(int(dur * SR))
    instantes, j = [], 0
    while (i := int(round((desfase + j * 60 / bpm) * SR))) + len(kick) <= len(y):
        y[i:i + len(kick)] += kick
        instantes.append(i / SR)
        j += 1
    sf.write(str(ruta), (0.5 * y).astype(np.float32), SR, subtype="PCM_16")
    return instantes


@pytest.fixture
def biblioteca(tmp_path, monkeypatch, server):
    db = tmp_path / "djradio" / "biblioteca.sqlite"
    _, rutas = armar_base_radio(tmp_path / "musica", db)
    monkeypatch.setenv("DJRADIO_DB", str(db))
    # La caché de ESTE test, para que un test no lea la de otro.
    monkeypatch.setattr(server.db, "DATA_DIR", tmp_path / "datos")
    kicks = _kicks(rutas["uno.wav"], BPM_UNO, DESFASE, DUR)
    ids = {n: server._radio_id(r) for n, r in rutas.items()}
    return {"db": db, "rutas": rutas, "ids": ids, "kicks": kicks, "datos": tmp_path / "datos"}


def _url(b, nombre="uno.wav"):
    return f"/api/radio/tracks/{b['ids'][nombre]}/onda3"


# --- la respuesta -------------------------------------------------------------------------

def test_forma_exacta_y_las_bandas_son_las_del_archivo(client, biblioteca):
    r = client.get(_url(biblioteca), params={"puntos": 1000})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"desde", "hasta", "duracion_audio", "puntos", "tasa_hz", "normalizacion",
                      "bandas", "grilla", "cache"}
    assert (d["desde"], d["hasta"], d["duracion_audio"], d["puntos"], d["tasa_hz"], d["cache"]) \
        == (0.0, DUR, DUR, 1000, 100, False)
    assert list(d["bandas"]) == ["graves", "medios", "agudos"]
    # 3000 cuadros de 10 ms → 1000 puntos: cada punto es el MÁXIMO de 3 cuadros.
    b = mb.compute_bandas(biblioteca["rutas"]["uno.wav"])
    esperado = b.q.reshape(3, 1000, 3).max(axis=2)
    for i, nombre in enumerate(("graves", "medios", "agudos")):
        assert d["bandas"][nombre] == [round(int(v) / 255, 3) for v in esperado[i]], nombre
    n = d["normalizacion"]
    assert n["relativa_al_track"] is True and "no volumen absoluto" in n["criterio"]
    assert (n["percentil"], n["cortes_hz"]) == (99.0, {"graves_hasta": 150.0, "agudos_desde": 2500.0})
    assert n["referencias"] == {k: round(v, 6) for k, v in zip(("graves", "medios", "agudos"),
                                                               b.referencias, strict=True)}


def test_la_grilla_sale_de_los_kicks_y_del_bpm_de_la_base(client, biblioteca):
    g = client.get(_url(biblioteca)).json()["grilla"]
    assert set(g) == {"estimada", "bpm", "bpm_base", "periodo_s", "primer_beat_s", "confianza",
                      "motivo", "beats_por_compas", "compas_ref", "compas_confianza",
                      "compas_motivo"}
    assert (g["estimada"], g["bpm_base"], g["beats_por_compas"]) == (True, BPM_UNO, 4)
    assert g["confianza"] >= mb.CONFIANZA_MIN and g["motivo"] is None
    p = 60 / BPM_UNO
    err = (g["primer_beat_s"] - biblioteca["kicks"][0] + p / 2) % p - p / 2
    assert abs(err) <= 0.010, f"la grilla está {err * 1000:+.1f} ms corrida"
    # El último kick también tiene su línea: la grilla no se corre a lo largo del tema.
    ultimo = biblioteca["kicks"][-1]
    linea = g["primer_beat_s"] + g["periodo_s"] * round((ultimo - g["primer_beat_s"]) / g["periodo_s"])
    assert abs(linea - ultimo) <= 0.010
    # Un loop de kicks iguales no dice cuál beat es el 1: no se inventa.
    assert g["compas_ref"] is None and g["compas_motivo"]


def test_sin_bpm_medido_no_hay_grilla(client, biblioteca):
    """`cuatro.wav` tiene BPM 0.0 en la base: no es una medición, no hay grilla que estimar."""
    _kicks(biblioteca["rutas"]["cuatro.wav"], 128.0, 0.1, 10.0)
    d = client.get(_url(biblioteca, "cuatro.wav")).json()
    assert d["grilla"] is None and d["bandas"]["graves"], d.get("error")


def test_el_tramo_pedido_y_la_cache(client, biblioteca):
    """[10.004, 12.5] con 100 puntos: los cuadros 1000..1249 (el primero contiene a 10.004)
    reducidos por máximo; y la segunda vez sale de la caché con los mismos valores."""
    params = {"desde": "10.004", "hasta": "12.5", "puntos": "100"}
    d = client.get(_url(biblioteca), params=params).json()
    assert (d["desde"], d["hasta"], d["puntos"], d["cache"]) == (10.0, 12.5, 100, False)
    q = mb.compute_bandas(biblioteca["rutas"]["uno.wav"]).q[:, 1000:1250]
    inicios = [i * 250 // 100 for i in range(100)] + [250]
    esperado = [max(q[0, a:b]) for a, b in zip(inicios[:-1], inicios[1:], strict=True)]
    assert d["bandas"]["graves"] == [round(int(v) / 255, 3) for v in esperado]
    otra = client.get(_url(biblioteca), params=params).json()
    assert otra["cache"] is True and otra["bandas"] == d["bandas"] and otra["grilla"] == d["grilla"]


def test_un_tramo_mas_chico_que_los_puntos_no_inventa_resolucion(client, biblioteca):
    d = client.get(_url(biblioteca), params={"desde": 5, "hasta": 5.5, "puntos": 4000}).json()
    assert (d["desde"], d["hasta"], d["puntos"]) == (5.0, 5.5, 50)
    assert len(d["bandas"]["agudos"]) == 50


def test_hasta_redondeado_a_ms_entra(client, biblioteca, server):
    """La duración que muestra /onda está redondeada a ms: pedir hasta ahí no es un 400."""
    dur = client.get(f"/api/radio/tracks/{biblioteca['ids']['uno.wav']}/onda").json()["duracion_audio"]
    r = client.get(_url(biblioteca), params={"desde": 0, "hasta": dur + 0.0004})
    assert r.status_code == 200, r.text
    assert r.json()["hasta"] == DUR


def test_la_cache_comparte_carpeta_con_los_picos_y_se_invalida(client, biblioteca):
    client.get(f"/api/radio/tracks/{biblioteca['ids']['uno.wav']}/onda")
    primero = client.get(_url(biblioteca)).json()
    peaks = biblioteca["datos"] / "peaks"
    assert sorted(p.suffix for p in peaks.iterdir()) == [".bandas3", ".json"]
    # El archivo cambia (otro desfase): se recalcula, la grilla se mueve con los kicks.
    nuevos = _kicks(biblioteca["rutas"]["uno.wav"], BPM_UNO, 0.3, DUR)
    d = client.get(_url(biblioteca)).json()
    assert d["cache"] is False and d["bandas"] != primero["bandas"]
    p = 60 / BPM_UNO
    assert abs((d["grilla"]["primer_beat_s"] - nuevos[0] + p / 2) % p - p / 2) <= 0.010


# --- pedidos inválidos: 400 con el motivo -------------------------------------------------

@pytest.mark.parametrize(("params", "pista"), [
    ({"desde": "-1"}, "negativo"),
    ({"desde": "10", "hasta": "5"}, "menor que `hasta`"),
    ({"desde": "5", "hasta": "5"}, "menor que `hasta`"),
    ({"hasta": "0"}, "mayor que 0"),
    ({"hasta": "31"}, "después del final del audio (30.000 s)"),
    ({"desde": "30"}, "en o después del final"),
    ({"desde": "29.999", "hasta": "40"}, "después del final"),
    ({"desde": "abc"}, "número de segundos"),
    ({"hasta": ""}, "número de segundos"),
    ({"desde": "nan"}, "finito"),
    ({"hasta": "inf"}, "finito"),
    ({"desde": "1e400"}, "finito"),
    ({"puntos": "0"}, "de 1 a 4000"),
    ({"puntos": "4001"}, "de 1 a 4000"),
    ({"puntos": "1.5"}, "de 1 a 4000"),
    ({"puntos": "-3"}, "de 1 a 4000"),
    ({"puntos": "abc"}, "de 1 a 4000"),
    ({"puntos": "9" * 30}, "de 1 a 4000"),
])
def test_rango_o_puntos_invalidos_es_400_con_motivo(client, biblioteca, params, pista):
    r = client.get(_url(biblioteca), params=params)
    assert r.status_code == 400, (r.status_code, r.text)
    assert pista in r.json()["error"], r.json()


# --- 404 / 422 / 409: como /onda, nunca 500 -----------------------------------------------

@pytest.mark.parametrize("track_id", ["0123456789abcdef", "0123456789abcdeg", "ABCDEF0123456789",
                                      "x" * 300, "1" * 17])
def test_track_que_no_existe_es_404(client, biblioteca, track_id):
    r = client.get(f"/api/radio/tracks/{track_id}/onda3")
    assert r.status_code == 404, (r.status_code, r.text)
    assert "no encontrado" in r.json()["error"]


def test_sin_archivo_es_404_y_archivo_roto_es_422(client, biblioteca):
    biblioteca["rutas"]["dos.wav"].unlink()
    r = client.get(_url(biblioteca, "dos.wav"))
    assert r.status_code == 404 and "no está en esta máquina" in r.json()["error"]
    biblioteca["rutas"]["tres.wav"].write_bytes(b"RIFF\x00\x00\x00\x00WAVEbasura" * 20)
    r = client.get(_url(biblioteca, "tres.wav"))
    assert r.status_code == 422 and "no pude leer el audio" in r.json()["error"]


def test_flac_que_se_corta_a_mitad_es_422(client, biblioteca):
    ruta = biblioteca["rutas"]["tres.wav"]
    y = (0.5 * np.sin(np.arange(SR * 3) / 20)).astype(np.float32)
    sf.write(str(ruta), np.stack([y, y], 1), SR, format="FLAC")
    b = ruta.read_bytes()
    ruta.write_bytes(b[:len(b) // 2])
    r = client.get(_url(biblioteca, "tres.wav"))
    assert r.status_code == 422 and "se corta o está dañado" in r.json()["error"], r.text


def test_sin_base_utilizable_es_409(client, biblioteca, server, monkeypatch):
    """El BPM de la grilla sale de la base: si está ocupada (un scan) es 409 con el motivo,
    no un 200 sin grilla que la pantalla leería como «este tema no tiene grilla»."""
    url = _url(biblioteca)
    assert client.get(url).status_code == 200            # el índice id → ruta ya está cargado
    monkeypatch.setattr(server, "_usar_store_motor",
                        lambda accion: (None, "base-ocupada", "La biblioteca está ocupada."))
    r = client.get(url)
    assert r.status_code == 409, r.text
    assert (r.json()["estado"], r.json()["error"]) == ("base-ocupada", "La biblioteca está ocupada.")


def test_un_track_que_ya_no_esta_en_la_base_es_404(client, biblioteca, server, monkeypatch):
    url = _url(biblioteca)
    assert client.get(url).status_code == 200
    monkeypatch.setattr(server, "_usar_store_motor", lambda accion: (None, "ok", None))
    r = client.get(url)
    assert r.status_code == 404 and "no encontrado" in r.json()["error"]


def test_audio_corto_del_catalogo_no_tiene_grilla_y_no_es_500(client, biblioteca):
    """`dos.wav` es un seno de 0,25 s con 130 BPM en la base: la grilla dice por qué no hay
    (muy corto) en vez de inventar una."""
    d = client.get(_url(biblioteca, "dos.wav")).json()
    assert d["puntos"] == math.ceil(0.25 * 100) == len(d["bandas"]["graves"])
    assert d["grilla"]["primer_beat_s"] is None and "muy corto" in d["grilla"]["motivo"]
