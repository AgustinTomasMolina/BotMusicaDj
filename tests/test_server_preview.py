"""Tests de la vista previa de la onda de las filas de Mis playlists (f62):
GET /api/radio/tracks/{id}/preview?puntos=<n>.

Misma base sintética que `test_server_onda3.py` (`tests/sinteticos.py`). Las bandas esperadas
salen de `motor.bandas.compute_bandas` (probado contra un oráculo en
`motor/tests/test_bandas.py`) reducidas acá a mano por MÁXIMO: la API no puede servir otra
cosa. Es la onda del tema ENTERO, sin grilla (no se estima: cuesta y la fila no la dibuja).
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
DUR = 30.0


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """El módulo `server` con sus efectos de carga neutralizados (receta de
    `tests/test_server_radio.py`)."""
    datos = tmp_path_factory.mktemp("server-preview-datos")
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
    monkeypatch.setattr(server.db, "DATA_DIR", tmp_path / "datos")
    # `uno.wav` pasa a durar 30 s: un tren de golpes graves y un siseo agudo en la 2.ª mitad,
    # para que las tres bandas no sean iguales.
    t = np.arange(int(DUR * SR)) / SR
    y = 0.4 * np.sin(2 * np.pi * 55 * t) * ((t % 0.5) < 0.1)
    y += 0.2 * np.sin(2 * np.pi * 6000 * t) * (t >= DUR / 2)
    sf.write(str(rutas["uno.wav"]), y.astype(np.float32), SR, subtype="PCM_16")
    ids = {n: server._radio_id(r) for n, r in rutas.items()}
    return {"rutas": rutas, "ids": ids, "datos": tmp_path / "datos"}


def _url(b, nombre="uno.wav"):
    return f"/api/radio/tracks/{b['ids'][nombre]}/preview"


def _esperado(ruta, puntos):
    """Los cuadros de 10 ms reducidos a `puntos` por máximo, con la misma partición que
    `motor.bandas.reducir_max` (bordes i * n // puntos)."""
    q = mb.compute_bandas(ruta).q
    n = q.shape[1]
    bordes = [i * n // puntos for i in range(puntos)] + [n]
    return [[round(int(q[k, a:b].max()) / 255, 3) for a, b in zip(bordes[:-1], bordes[1:], strict=True)]
            for k in range(3)]


def test_forma_exacta_y_bandas_del_tema_entero(client, biblioteca):
    r = client.get(_url(biblioteca), params={"puntos": 150})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"duracion_audio", "puntos", "bandas", "cache"}      # sin grilla
    assert (d["duracion_audio"], d["puntos"], d["cache"]) == (DUR, 150, False)
    assert list(d["bandas"]) == ["graves", "medios", "agudos"]
    esperado = _esperado(biblioteca["rutas"]["uno.wav"], 150)
    for k, nombre in enumerate(("graves", "medios", "agudos")):
        assert d["bandas"][nombre] == esperado[k], nombre
    # Los agudos son el siseo de la 2.ª mitad: la 1.ª mitad queda casi en cero.
    agudos = d["bandas"]["agudos"]
    assert max(agudos[:70]) < 0.2 < min(agudos[80:])


def test_por_defecto_160_puntos_y_la_segunda_vez_sale_de_la_cache(client, biblioteca):
    d = client.get(_url(biblioteca)).json()
    assert d["puntos"] == 160 and len(d["bandas"]["medios"]) == 160 and d["cache"] is False
    otra = client.get(_url(biblioteca)).json()
    assert otra["cache"] is True and otra["bandas"] == d["bandas"]
    # La misma caché que /onda3: después de la preview, /onda3 ya no decodifica.
    o3 = client.get(f"/api/radio/tracks/{biblioteca['ids']['uno.wav']}/onda3").json()
    assert o3["cache"] is True


def test_no_estima_la_grilla(client, biblioteca, monkeypatch):
    """La grilla cuesta ~0,1 s por tema: la vista previa nunca la pide."""
    def no(*a, **k):
        raise AssertionError("la vista previa no tiene que estimar la grilla")

    monkeypatch.setattr(mb, "grilla", no)
    monkeypatch.setattr(mb, "estimar_grilla", no)
    assert client.get(_url(biblioteca)).status_code == 200


def test_un_audio_mas_corto_que_los_puntos_no_inventa_resolucion(client, biblioteca):
    """`dos.wav` dura 0,25 s = 25 cuadros: con 160 puntos pedidos vienen 25."""
    d = client.get(_url(biblioteca, "dos.wav")).json()
    assert d["puntos"] == math.ceil(0.25 * 100) == len(d["bandas"]["graves"])


@pytest.mark.parametrize("puntos", ["0", "401", "1.5", "-3", "abc", "", "9" * 30, " 5"])
def test_puntos_invalidos_es_400_con_motivo(client, biblioteca, puntos):
    r = client.get(_url(biblioteca), params={"puntos": puntos})
    assert r.status_code == 400, (r.status_code, r.text)
    assert "de 1 a 400" in r.json()["error"]


def test_puntos_en_el_borde_entran(client, biblioteca):
    for p in ("1", "400"):
        d = client.get(_url(biblioteca), params={"puntos": p}).json()
        assert d["puntos"] == int(p), d


@pytest.mark.parametrize("track_id", ["0123456789abcdef", "0123456789abcdeg", "x" * 300, "1" * 17])
def test_track_que_no_existe_es_404(client, biblioteca, track_id):
    r = client.get(f"/api/radio/tracks/{track_id}/preview")
    assert r.status_code == 404 and "no encontrado" in r.json()["error"]


def test_sin_archivo_es_404_y_archivo_roto_es_422(client, biblioteca):
    biblioteca["rutas"]["dos.wav"].unlink()
    r = client.get(_url(biblioteca, "dos.wav"))
    assert r.status_code == 404 and "no está en esta máquina" in r.json()["error"]
    biblioteca["rutas"]["tres.wav"].write_bytes(b"RIFF\x00\x00\x00\x00WAVEbasura" * 20)
    r = client.get(_url(biblioteca, "tres.wav"))
    assert r.status_code == 422 and "no pude leer el audio" in r.json()["error"]


def test_un_bug_del_calculo_es_422_sin_culpar_al_archivo(client, biblioteca, monkeypatch):
    def roto(*a, **k):
        raise ValueError("bug del cálculo")

    monkeypatch.setattr(mb, "mezcla_mono", roto)
    r = client.get(_url(biblioteca))
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert "no necesariamente el archivo" in error and "ValueError" in error, error


def test_no_depende_de_la_base_ocupada(client, biblioteca, server, monkeypatch):
    """No necesita el BPM: con la base ocupada (un scan) la onda igual se dibuja."""
    url = _url(biblioteca)
    assert client.get(url).status_code == 200            # el índice id → ruta ya está cargado
    monkeypatch.setattr(server, "_usar_store_motor",
                        lambda accion: (None, "base-ocupada", "La biblioteca está ocupada."))
    assert client.get(url).status_code == 200


def test_como_mucho_dos_lecturas_de_bandas_a_la_vez(server, biblioteca, monkeypatch):
    """La primera lectura de un tema decodifica el archivo entero: entre /onda3 y /preview nunca
    hay más de 2 a la vez, aunque lleguen 6 pedidos juntos."""
    import asyncio
    import threading
    import time

    activos, pico, lock = [0], [0], threading.Lock()
    real = mb.cached_bandas

    def lenta(ruta, carpeta):
        with lock:
            activos[0] += 1
            pico[0] = max(pico[0], activos[0])
        try:
            time.sleep(0.15)
            return real(ruta, carpeta)
        finally:
            with lock:
                activos[0] -= 1

    monkeypatch.setattr(mb, "cached_bandas", lenta)
    ruta = str(biblioteca["rutas"]["uno.wav"])

    async def seis():
        return await asyncio.gather(*(server._onda3_bandas(ruta) for _ in range(6)))

    res = asyncio.run(seis())
    assert all(err is None and leidas is not None for leidas, err in res), res
    assert pico[0] == server._BANDAS_A_LA_VEZ == 2, f"hubo {pico[0]} lecturas a la vez"
