"""Tests de la API de la carpeta vigilada (f65): `/api/vigilancia` y `/api/vigilancia/escanear`.

Carpetas temporales y sin audio: lo que se prueba es el cableado del server (apagada sin la
variable, prendida con ella, el hilo que arranca y para con el server, las respuestas sin
rutas de la PC). Qué procesa el vigilante lo prueban `pipeline/tests/test_vigilar.py`.
"""
import importlib
import sys

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def server(tmp_path, monkeypatch):
    datos = tmp_path / "datos"
    monkeypatch.setenv("MUSIFLIX_DATA_DIR", str(datos))
    monkeypatch.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
    monkeypatch.setenv("MUSIFLIX_STAGING", str(tmp_path / "salida" / "stg"))
    monkeypatch.setenv("MUSIFLIX_SIN_CALENTAR", "1")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MUSIFLIX_ITUNES", raising=False)
    monkeypatch.delenv("MUSIC_WATCH_FOLDER", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for mod in ("server", "jobs", "db", "tasks"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


def test_apagada_sin_la_variable(server):
    with TestClient(server.app) as c:
        r = c.get("/api/vigilancia")
        assert r.status_code == 200
        assert r.json()["activa"] is False
        assert "MUSIC_WATCH_FOLDER" in r.json()["mensaje"]
        r = c.post("/api/vigilancia/escanear", json={})
        assert r.status_code == 409 and r.json()["exito"] is False


def test_prendida_arranca_el_hilo_y_para_con_el_server(server, tmp_path, monkeypatch):
    entrada = tmp_path / "Mis Descargas"
    entrada.mkdir()
    monkeypatch.setenv("MUSIC_WATCH_FOLDER", str(entrada))
    with TestClient(server.app) as c:
        r = c.get("/api/vigilancia")
        assert r.json()["activa"] is True
        e = r.json()["estado"]
        assert e["carpeta"] == "Mis Descargas", "solo el nombre, nunca la ruta"
        assert e["corriendo"] is True
        assert str(tmp_path) not in r.text, "la respuesta lleva una ruta de la PC"
        vig = server._vigilante
        r = c.post("/api/vigilancia/escanear", json={})
        assert r.status_code == 200 and r.json()["encolado"] is True
    assert vig.estado()["corriendo"] is False, "el hilo siguió vivo después de apagar el server"


def test_carpeta_inalcanzable_no_impide_arrancar(server, tmp_path, monkeypatch):
    monkeypatch.setenv("MUSIC_WATCH_FOLDER", str(tmp_path / "nas-apagado"))
    with TestClient(server.app) as c:
        vig = server._vigilante
        vig.escanear_ahora()
        r = c.get("/api/vigilancia")
        assert r.status_code == 200
        assert r.json()["estado"]["alcanzable"] is False
        assert str(tmp_path) not in r.text
        assert c.get("/api/playlists").status_code == 200, "el resto del server dejó de andar"


def test_escanear_rechaza_un_pedido_de_otra_pagina(server, tmp_path, monkeypatch):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    monkeypatch.setenv("MUSIC_WATCH_FOLDER", str(entrada))
    with TestClient(server.app) as c:
        r = c.post("/api/vigilancia/escanear", json={},
                   headers={"Origin": "https://otra.example", "Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403
        r = c.post("/api/vigilancia/escanear", content="x", headers={"Content-Type": "text/plain"})
        assert r.status_code == 415
