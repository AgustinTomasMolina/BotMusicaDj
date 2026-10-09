"""Tests de la API de f53: importar playlists (Rekordbox y carpeta), editar el género, el puente
con el motor (análisis en segundo plano y estado de cada tema) y el "+" de la home con archivo.

Datos sintéticos (`tests/sinteticos.py`): la colección de Rekordbox con la MISMA estructura que
Rekordbox 7.2.16 y audio del generador de `motor.sintetico` para el análisis: el BPM esperado
es el que se le pidió al generador, nunca uno escrito a mano. Ninguna ruta del dueño.
"""
import importlib
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("httpx")
import soundfile as sf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sinteticos import (  # noqa: E402
    biblioteca_importable,
    pistas_biblioteca,
    xml_rekordbox,
)

from motor.sintetico import click_track  # noqa: E402
from motor.store import Store  # noqa: E402

XML = {"Content-Type": "application/xml", "X-Nombre-Archivo": "mi%20coleccion.xml"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    datos = tmp_path_factory.mktemp("server-importar")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MUSIFLIX_DATA_DIR", str(datos))
        mp.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
        mp.delenv("REDIS_URL", raising=False)
        mp.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        for mod in ("server", "jobs", "db", "tasks"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
    srv.db.init_db()
    assert Path(srv.db.DB_PATH).parent == datos, "la base de los tests no puede ser la del dueño"
    return srv


@pytest.fixture
def entorno(server, tmp_path, monkeypatch):
    """Raíz de música con la colección importable, base del motor propia y una cola de análisis
    nueva. Las playlists de la base de MusiFlix se vacían: cada test arranca sin importadas."""
    import playlist_analisis

    with server.db.SessionLocal() as s:
        for modelo in (server.db.MiPlaylistItem, server.db.MiPlaylist):
            for fila in s.scalars(server.db.select(modelo)):
                s.delete(fila)
        s.commit()
    raiz = tmp_path / "musica"
    # 2 s: el análisis no mide nada de menos de 1 s (lo usa el test de "dos playlists").
    xml, rutas = biblioteca_importable(raiz, segundos=2.0)
    monkeypatch.setattr(server, "_LIB_ROOTS", [str(raiz)])
    monkeypatch.setattr(server, "_LIB_XML", "")
    monkeypatch.setenv("DJRADIO_DB", str(tmp_path / "djradio" / "base.sqlite"))
    monkeypatch.setattr(server, "_radio_audio", {})
    an = playlist_analisis.AnalizadorFondo(server._analizar_y_guardar, server._al_guardar)
    monkeypatch.setattr(server, "_analizador", an)
    yield {"raiz": raiz, "xml": xml, "rutas": rutas, "db": tmp_path / "djradio" / "base.sqlite",
           "an": an, "tmp": tmp_path}
    an.detener(30)


@pytest.fixture
def client(server):
    return TestClient(server.app)


def _leer(client, xml, **kw):
    return client.post("/api/importar/rekordbox/leer", content=xml.encode("utf-8"),
                       headers={**XML, **kw.pop("headers", {})}, **kw)


def _importar(client, xml, ids, actualizar=False):
    r = _leer(client, xml)
    assert r.status_code == 200, r.text
    return client.post("/api/importar/rekordbox", json={
        "token": r.json()["token"], "playlists": ids, "actualizar": actualizar})


def _playlist(client, pid):
    r = client.get(f"/api/playlists/{pid}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _sin_rutas(texto: str, *rutas):
    for r in rutas:
        for forma in (str(r), Path(r).as_posix()):
            assert forma not in texto, f"la respuesta trae una ruta de la PC: {forma}"


# --- leer el XML ---------------------------------------------------------------------------------

def test_leer_resume_las_playlists_sin_rutas(client, entorno):
    r = _leer(client, entorno["xml"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["archivo"], d["temas"], d["raices_configuradas"]) == ("mi coleccion.xml", 4, True)
    resumen = [(p["ruta"], p["total"], p["encontrados"], p["ambiguos"], p["faltan"],
                p["inexistentes"], p["ya_importada"]) for p in d["playlists"]]
    assert resumen == [
        ("Techno / Peak", 3, 2, 1, 0, 1, None),
        ("Techno / Sub / Cierre", 2, 1, 0, 1, 0, None),
        ("Warmup", 1, 0, 0, 1, 0, None),
    ]
    _sin_rutas(r.text, entorno["raiz"], "C:/Users/otra-pc")


def test_leer_sin_raices_lo_dice_y_no_falla(client, entorno, server, monkeypatch):
    monkeypatch.setattr(server, "_LIB_ROOTS", [])
    d = _leer(client, entorno["xml"]).json()
    assert d["raices_configuradas"] is False and "MUSIFLIX_LIBRARY_ROOTS" in d["motivo_raices"]
    assert [p["faltan"] for p in d["playlists"]] == [3, 2, 1]


@pytest.mark.parametrize(("cuerpo", "status"), [
    ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aa">]><DJ_PLAYLISTS/>', 400),
    ("<DJ_PLAYLISTS><COLLECTION>", 400),
    ("", 400),
])
def test_leer_xml_malo_es_400_con_motivo(client, entorno, cuerpo, status):
    r = client.post("/api/importar/rekordbox/leer", content=cuerpo.encode(), headers=XML)
    assert r.status_code == status and r.json()["mensaje"], r.text


def test_leer_xml_enorme_es_413(client, entorno, monkeypatch):
    import playlist_import
    monkeypatch.setattr(playlist_import, "XML_MAX_BYTES", 1000)
    r = _leer(client, entorno["xml"])
    assert r.status_code == 413 and "MB" in r.json()["mensaje"]


def test_leer_usar_configurado(client, entorno, server, monkeypatch):
    assert client.post("/api/importar/rekordbox/leer",
                       json={"usar_configurado": True}).status_code == 409
    f = entorno["tmp"] / "Rekordbox.xml"
    f.write_text(entorno["xml"], encoding="utf-8")
    monkeypatch.setattr(server, "_LIB_XML", str(f))
    d = client.post("/api/importar/rekordbox/leer", json={"usar_configurado": True}).json()
    assert d["archivo"] == "Rekordbox.xml" and len(d["playlists"]) == 3


@pytest.mark.parametrize(("headers", "status"), [
    ({"Content-Type": "text/plain"}, 415),
    ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
    ({"Content-Type": "application/xml", "Origin": "https://malo.example"}, 403),
    ({"Content-Type": "application/xml", "Sec-Fetch-Site": "cross-site"}, 403),
])
def test_leer_de_otra_pagina_o_con_otro_tipo_se_rechaza(client, entorno, headers, status):
    r = client.post("/api/importar/rekordbox/leer", content=entorno["xml"].encode(), headers=headers)
    assert r.status_code == status, r.text


def test_leer_xml_de_la_propia_app_con_origin_pasa(client, entorno):
    r = _leer(client, entorno["xml"], headers={"Origin": "http://testserver",
                                               "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200, r.text


# --- importar --------------------------------------------------------------------------------------

def test_importar_crea_la_playlist_en_el_orden_del_xml(client, entorno, server):
    r = _importar(client, entorno["xml"], [0])
    assert r.status_code == 200, r.text
    (res,) = r.json()["resultados"]
    assert (res["estado"], res["ruta"], res["temas"]) == ("creada", "Techno / Peak", 3)
    d = _playlist(client, res["id"])
    assert (d["nombre"], d["origen"], d["origen_ref"], d["origen_archivo"]) == \
        ("Peak", "rekordbox", "Techno / Peak", "mi coleccion.xml")
    filas = [(it["titulo"], it["archivo_estado"], it["local"], it["descargado"],
              it["nombre_archivo"], it["genero"]) for it in d["items"]]
    assert filas == [
        ("Reubicado", "ok", True, True, "reubicado.wav", "Hard Techno"),
        ("Ácido #1", "ok", True, True, "Ácido #1 (100%).wav", "Techno"),
        ("Repetido", "ambiguo", True, False, "repetido.wav", None),
    ]
    acido = d["items"][1]
    assert (acido["analisis"]["bpm"], acido["analisis"]["camelot"], acido["analisis"]["tonalidad"],
            acido["analisis"]["dato"], acido["analisis"]["estado"]) == \
        (128.4, "8A", "Am", "rekordbox", "pendiente")
    assert d["items"][0]["analisis"]["bpm"] is None, "AverageBpm 0.00 no es un BPM"
    rep = d["items"][2]["analisis"]
    assert rep["estado"] == "sin-archivo" and "2 archivos con ese nombre" in rep["motivo"]
    assert all("ruta" not in it for it in d["items"])
    _sin_rutas(client.get(f"/api/playlists/{res['id']}").text, entorno["raiz"])
    # Lo que se guardó es el archivo REAL (la ruta solo la ve el server).
    item = server.db.get_item(res["id"], acido["id"])
    assert os.path.samefile(item["ruta"], entorno["rutas"]["acido"])
    # Un tema que ya está en la PC no se "baja": 409 como uno descargado.
    r = client.post(f"/api/playlists/{res['id']}/items/{acido['id']}/descargar", json={})
    assert r.status_code == 409


def test_ya_importada_y_actualizar_sin_pisar_el_genero_editado(client, entorno):
    pid = _importar(client, entorno["xml"], [0]).json()["resultados"][0]["id"]
    otra = _importar(client, entorno["xml"], [0]).json()["resultados"][0]
    assert (otra["estado"], otra["id"]) == ("ya-importada", pid)
    assert _leer(client, entorno["xml"]).json()["playlists"][0]["ya_importada"]["id"] == pid

    items = _playlist(client, pid)["items"]
    acido = next(i for i in items if i["titulo"] == "Ácido #1")
    r = client.patch(f"/api/playlists/{pid}/items/{acido['id']}", json={"genero": "Acid"})
    assert r.status_code == 200 and r.json()["item"]["genero"] == "Acid"

    # En Rekordbox: cambia el género de los dos y se suma a la playlist el tema 4.
    xml2 = (entorno["xml"].replace('Genre="Hard Techno"', 'Genre="Industrial"')
            .replace('Name="Ácido #1" Artist="Artista Uno" Composer="" Album="" Grouping="" '
                     'Genre="Techno"', 'Name="Ácido #1" Artist="Artista Uno" Composer="" '
                     'Album="" Grouping="" Genre="Rave"')
            .replace('<TRACK Key="3"/>', '<TRACK Key="3"/>\n<TRACK Key="4"/>'))
    res = _importar(client, xml2, [0], actualizar=True).json()["resultados"][0]
    assert (res["estado"], res["id"], res["agregados"], res["actualizados"]) == \
        ("actualizada", pid, 1, 3)
    d = _playlist(client, pid)
    assert [(i["titulo"], i["genero"]) for i in d["items"]] == [
        ("Reubicado", "Industrial"), ("Ácido #1", "Acid"), ("Repetido", None),
        ("Fantasma", "Techno")], "el género editado a mano tiene que ganar; el nuevo va al final"


def test_un_tema_en_dos_playlists_aparece_en_las_dos(client, entorno):
    res = _importar(client, entorno["xml"], [0, 1]).json()["resultados"]
    assert [r["estado"] for r in res] == ["creada", "creada"]
    titulos = [[i["titulo"] for i in _playlist(client, r["id"])["items"]] for r in res]
    assert titulos == [["Reubicado", "Ácido #1", "Repetido"], ["Ácido #1", "Fantasma"]]


def test_importar_token_vencido_o_ids_malos(client, entorno):
    assert client.post("/api/importar/rekordbox", json={"token": "nada", "playlists": [0]}
                       ).status_code == 410
    tok = _leer(client, entorno["xml"]).json()["token"]
    for ids, status in (([], 400), (["0"], 400), ([True], 400), ([7], 404)):
        r = client.post("/api/importar/rekordbox", json={"token": tok, "playlists": ids})
        assert r.status_code == status, (ids, r.text)
    r = client.post("/api/importar/rekordbox", content=b"token=x",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


# --- editar el género ------------------------------------------------------------------------------

def test_patch_genero_valida_y_sanea(client, entorno):
    res = _importar(client, entorno["xml"], [0, 2]).json()["resultados"]
    pid, otra = res[0]["id"], res[1]["id"]
    it = _playlist(client, pid)["items"][2]
    url = f"/api/playlists/{pid}/items/{it['id']}"
    r = client.patch(url, json={"genero": "  Hard\x01   Techno  "})
    assert r.json()["item"]["genero"] == "Hard Techno"
    assert client.patch(url, json={"genero": "x", "ruta": "C:/"}).status_code == 400
    assert client.patch(url, json={"genero": 5}).status_code == 400
    assert client.patch(url, content=b"genero=x",
                        headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.patch(url, json={"genero": "x"},
                        headers={"Origin": "https://malo.example"}).status_code == 403
    assert client.patch(f"/api/playlists/{otra}/items/{it['id']}",
                        json={"genero": "x"}).status_code == 404, "el item es de otra playlist"
    assert client.patch(f"/api/playlists/{pid}/items/abc", json={"genero": "x"}).status_code == 404
    assert _playlist(client, pid)["items"][2]["genero"] == "Hard Techno", "un rechazo escribió"
    r = client.patch(url, json={"genero": "Sin género"})
    assert r.json()["item"]["genero"] is None


def test_genero_a_los_sin_genero(client, entorno):
    pid = _importar(client, entorno["xml"], [0]).json()["resultados"][0]["id"]
    r = client.post(f"/api/playlists/{pid}/genero", json={"genero": "Techno"})
    assert r.json()["cambiados"] == 1
    assert [i["genero"] for i in _playlist(client, pid)["items"]] == \
        ["Hard Techno", "Techno", "Techno"], "solo el que no tenía"
    assert client.post(f"/api/playlists/{pid}/genero", json={"genero": " "}).status_code == 400


# --- carpetas --------------------------------------------------------------------------------------

def test_carpetas_lista_raices_y_subcarpetas_sin_rutas(client, entorno):
    d = client.get("/api/importar/carpetas").json()
    assert d["raices"] == [{"id": 0, "nombre": "musica", "disponible": True}]
    r = client.get("/api/importar/carpetas", params={"raiz": 0, "ruta": "Techno"})
    d = r.json()
    assert (d["nombre"], d["ruta"], d["audios"], d["subcarpetas"]) == \
        ("Techno", "Techno", 1, [{"nombre": "Peak Time", "audios": 1, "subcarpetas": 0}])
    _sin_rutas(r.text, entorno["raiz"])
    for ruta in ("..", "../..", str(entorno["tmp"]), "C:/Windows", "\\\\srv\\x", "CON"):
        assert client.get("/api/importar/carpetas",
                          params={"raiz": 0, "ruta": ruta}).status_code == 400, ruta
    assert client.get("/api/importar/carpetas", params={"raiz": 3}).status_code == 404


def test_carpetas_sin_raices_configuradas(client, entorno, server, monkeypatch):
    monkeypatch.setattr(server, "_LIB_ROOTS", [])
    d = client.get("/api/importar/carpetas").json()
    assert d["configuradas"] is False and "MUSIFLIX_LIBRARY_ROOTS" in d["motivo"]
    r = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": ""})
    assert r.status_code == 409 and "MUSIFLIX_LIBRARY_ROOTS" in r.json()["mensaje"]


def test_importar_carpeta(client, entorno):
    roto = entorno["raiz"] / "Techno" / "roto.mp3"
    roto.write_bytes(b"no es audio" * 20)
    r = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "Techno", "recursivo": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["estado"], d["nombre"], d["ruta"], d["temas"], d["ilegibles"]) == \
        ("creada", "Techno", "musica/Techno", 3, 1)
    pl = _playlist(client, d["id"])
    assert pl["origen"] == "carpeta"
    assert [(i["titulo"], i["genero"], i["archivo_estado"], i["import_motivo"] is not None)
            for i in pl["items"]] == [
        ("Ácido #1 (100%)", None, "ok", False),    # Peak Time/… va primero (orden por ruta)
        ("reubicado", None, "ok", False),
        ("roto", None, "ok", True)]
    otra = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "Techno"}).json()
    assert (otra["estado"], otra["id"]) == ("ya-importada", d["id"])
    sin = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "Techno/Peak Time/.."})
    assert sin.status_code == 400
    assert client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "Techno"},
                       headers={"Origin": "https://malo.example"}).status_code == 403


# --- el puente con el motor ------------------------------------------------------------------------

def _click(ruta: Path, bpm: float, nota="A", modo="min", seed=0, dur=8.0):
    y, sr = click_track(bpm, dur=dur, nota=nota, modo=modo, seed=seed)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(ruta), y.astype(np.float32), sr, subtype="FLOAT")


def test_analizar_mide_con_el_motor_y_abre_el_editor(client, entorno, server):
    set_dir = entorno["raiz"] / "Set"
    _click(set_dir / "a.wav", 128.0, seed=1)
    _click(set_dir / "b.wav", 140.0, nota="E", modo="min", seed=2)
    pid = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "Set"}).json()["id"]
    assert not entorno["db"].exists()
    r = client.post(f"/api/playlists/{pid}/analizar", json={})
    assert r.status_code == 200 and r.json()["encolados"] == 2, r.text
    assert entorno["db"].exists(), "sin base, se crea como lo hace scan"
    assert entorno["an"].esperar(120)
    p = client.get(f"/api/playlists/{pid}/analisis").json()["progreso"]
    assert (p["corriendo"], p["hechos"], p["total"], p["fallidos"]) == (False, 2, 2, [])

    d = _playlist(client, pid)
    # La key del generador: A menor = 8A (Am), E menor = 9A (Em).
    for it, esperado, key in zip(d["items"], (128.0, 140.0), (("8A", "Am"), ("9A", "Em")),
                                 strict=True):
        a = it["analisis"]
        assert a["estado"] == "analizado" and a["dato"] == "motor"
        assert abs(a["bpm"] - esperado) <= 1.0, f"{it['titulo']}: {a['bpm']} y el generador hizo {esperado}"
        assert (a["camelot"], a["tonalidad"]) == key, f"{it['titulo']}: la key del generador"
        assert a["key_dudosa"] is True, "8 s no alcanzan para el acuerdo entre tramos: va con ?"
        ruta = set_dir / f"{it['titulo']}.wav"
        assert a["radio_id"] == server._radio_id(Path(os.path.abspath(ruta)))
        assert a["marcas"] == {"total": 0, "pads": [], "memory": 0, "loop": 0}
    with Store(entorno["db"]) as store:
        f = store.get_features(os.path.abspath(set_dir / "a.wav"))
        t = store.get(os.path.abspath(set_dir / "a.wav"))
        assert d["items"][0]["analisis"]["bpm"] == round(f.bpm, 1)
        assert (t.license, t.source_url) == ("no declarado", "no declarado")

    # El editor de cues abre el tema por su id, y una marca vuelve como punto de color.
    rid = d["items"][0]["analisis"]["radio_id"]
    assert client.get(f"/api/radio/tracks/{rid}/marcas").status_code == 200
    assert client.post(f"/api/radio/tracks/{rid}/marcas",
                       json={"tipo": "cue", "inicio": 1.0, "num": 2}).status_code == 201
    assert client.post(f"/api/radio/tracks/{rid}/marcas",
                       json={"tipo": "memory", "inicio": 2.0}).status_code == 201
    assert _playlist(client, pid)["items"][0]["analisis"]["marcas"] == \
        {"total": 2, "pads": [2], "memory": 1, "loop": 0}

    # Idempotente: lo vigente no se vuelve a encolar.
    assert client.post(f"/api/playlists/{pid}/analizar", json={}).json()["encolados"] == 0

    # Un archivo cambiado vuelve a "pendiente": lo medido describe OTRO audio y no se muestra.
    _click(set_dir / "b.wav", 124.0, seed=3)
    os.utime(set_dir / "b.wav", (1_000_000_000, 1_000_000_000))
    b = _playlist(client, pid)["items"][1]["analisis"]
    assert b["estado"] == "pendiente" and "cambió" in b["motivo"] and b["dato"] != "motor"
    assert client.post(f"/api/playlists/{pid}/analizar", json={}).json()["encolados"] == 1
    assert entorno["an"].esperar(120)
    b = _playlist(client, pid)["items"][1]["analisis"]
    assert b["estado"] == "analizado" and abs(b["bpm"] - 124.0) <= 1.0


def test_un_tema_en_dos_playlists_se_analiza_una_vez(client, entorno):
    res = _importar(client, entorno["xml"], [0, 1]).json()["resultados"]
    for r in res:
        assert client.post(f"/api/playlists/{r['id']}/analizar", json={}).status_code == 200
    assert entorno["an"].esperar(120)
    with Store(entorno["db"]) as store:
        assert sorted(p.name for p in store.paths()) == ["reubicado.wav", "Ácido #1 (100%).wav"]


def test_un_archivo_que_no_se_puede_analizar_queda_con_su_motivo(client, entorno):
    (entorno["raiz"] / "Mal").mkdir()
    (entorno["raiz"] / "Mal" / "roto.wav").write_bytes(b"RIFF....WAVEbasura" * 5)
    pid = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "Mal"}).json()["id"]
    assert client.post(f"/api/playlists/{pid}/analizar", json={}).json()["encolados"] == 1
    assert entorno["an"].esperar(60)
    p = client.get(f"/api/playlists/{pid}/analisis").json()["progreso"]
    assert p["corriendo"] is False and len(p["fallidos"]) == 1
    a = _playlist(client, pid)["items"][0]["analisis"]
    assert a["estado"] == "fallo" and a["motivo"]
    _sin_rutas(client.get(f"/api/playlists/{pid}/analisis").text, entorno["raiz"])
    assert client.post(f"/api/playlists/{pid}/analizar", json={}).json()["encolados"] == 0, \
        "el mismo archivo sin cambios no se reintenta en cada pedido"


def test_base_ocupada_es_409_y_no_encola(client, entorno, server, monkeypatch):
    monkeypatch.setattr(server, "_RADIO_ESPERA_S", 0.2)
    pid = _importar(client, entorno["xml"], [0]).json()["resultados"][0]["id"]
    Store(entorno["db"]).close()
    con = sqlite3.connect(str(entorno["db"]))
    con.execute("BEGIN EXCLUSIVE")
    try:
        r = client.post(f"/api/playlists/{pid}/analizar", json={})
    finally:
        con.rollback()
        con.close()
    assert r.status_code == 409 and "ocupada" in r.json()["mensaje"], r.text
    assert client.get(f"/api/playlists/{pid}/analisis").json()["progreso"]["total"] == 0


def test_analizar_rechaza_otra_pagina_y_ids_malos(client, entorno):
    pid = _importar(client, entorno["xml"], [0]).json()["resultados"][0]["id"]
    assert client.post(f"/api/playlists/{pid}/analizar", json={},
                       headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post(f"/api/playlists/{pid}/analizar", content=b"",
                       headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.get(f"/api/playlists/{pid}/analisis").json()["progreso"]["total"] == 0
    assert client.post("/api/playlists/99999/analizar", json={}).status_code == 404
    assert client.post("/api/playlists/x/analizar", json={}).status_code == 404
    assert client.get("/api/playlists/x/analisis").status_code == 404


# --- el "+" de la home -------------------------------------------------------------------------------

def test_home_lib_id_resuelve_el_archivo_en_el_server(client, entorno, server, monkeypatch, tmp_path):
    raiz = tmp_path / "home"
    _, pistas = pistas_biblioteca(raiz, tmp_path / "no" / "fantasma.wav")
    xml = tmp_path / "home.xml"
    xml.write_text(xml_rekordbox(pistas), encoding="utf-8")
    monkeypatch.setattr(server, "_LIB_XML", str(xml))
    monkeypatch.setattr(server, "_LIB_ROOTS", [str(raiz)])
    monkeypatch.setattr(server, "_lib_audio", {})
    pid = client.post("/api/playlists", json={"nombre": "Home"}).json()["playlist"]["id"]

    # Un item viejo (la home lo guardaba sin archivo).
    viejo = client.post(f"/api/playlists/{pid}/items", json={"track": {
        "titulo": "Uno", "artista": "Artista A", "fuente": "biblioteca"}}).json()
    assert _playlist(client, pid)["items"][0]["archivo_estado"] == "sin-archivo"

    secreto = tmp_path / "secreto.wav"
    secreto.write_bytes(b"x")
    r = client.post(f"/api/playlists/{pid}/items", json={"track": {
        "titulo": "Uno", "artista": "Artista A", "fuente": "biblioteca", "lib_id": "11",
        "ruta": str(secreto), "archivo": "secreto.wav", "formato": "exe"}}).json()
    assert (r["dup"], r["id"], r["archivo_completado"], r["con_archivo"]) == \
        (True, viejo["id"], True, True), "el item viejo se completa con el archivo"
    it = server.db.get_item(pid, viejo["id"])
    assert os.path.samefile(it["ruta"], raiz / "techno" / "uno.wav"), "la ruta del cliente se ignora"
    assert it["formato"] == "wav"
    publico = _playlist(client, pid)["items"][0]
    assert (publico["archivo_estado"], publico["local"], publico["nombre_archivo"]) == \
        ("ok", True, "uno.wav")

    otra = client.post(f"/api/playlists/{pid}/items", json={"track": {
        "titulo": "Uno", "artista": "Artista A", "fuente": "biblioteca", "lib_id": "11"}}).json()
    assert otra["dup"] is True and otra["archivo_completado"] is False
    assert len(_playlist(client, pid)["items"]) == 1

    # Un lib_id que no existe: sin archivo (y se dice), nunca la ruta del cliente.
    r = client.post(f"/api/playlists/{pid}/items", json={"track": {
        "titulo": "X", "artista": "Y", "fuente": "biblioteca", "lib_id": "nada",
        "ruta": str(secreto)}}).json()
    assert r["con_archivo"] is False
    assert server.db.get_item(pid, r["id"])["ruta"] is None


# --- auditoría f53 --------------------------------------------------------------------------------

def test_xml_sin_content_length_igual_tiene_tope(client, entorno, monkeypatch):
    """El tope se corta leyendo por trozos: un cuerpo chunked (sin Content-Length) no pasa."""
    import playlist_import
    monkeypatch.setattr(playlist_import, "XML_MAX_BYTES", 1000)
    cuerpo = entorno["xml"].encode("utf-8")
    assert len(cuerpo) > 1000

    def trozos():
        for i in range(0, len(cuerpo), 200):
            yield cuerpo[i:i + 200]
    r = client.post("/api/importar/rekordbox/leer", content=trozos(), headers=XML)
    assert "content-length" not in {k.lower() for k in r.request.headers}, "el pedido tenía Content-Length"
    assert r.status_code == 413 and "MB" in r.json()["mensaje"], r.text


def test_leer_cuerpo_xml_corta_en_cuanto_se_pasa(server, monkeypatch):
    """Sin Content-Length, `_leer_cuerpo_xml` deja de leer apenas pasa el tope (no junta un
    cuerpo enorme en memoria para rechazarlo después): de 100 trozos de 300 bytes con tope
    1000, lee 4 y corta con 413."""
    import asyncio

    import playlist_import
    monkeypatch.setattr(playlist_import, "XML_MAX_BYTES", 1000)
    leidos = []

    class Pedido:
        headers = {}

        async def stream(self):
            for i in range(100):
                leidos.append(i)
                yield b"x" * 300

    with pytest.raises(playlist_import.Rechazo) as e:
        asyncio.run(server._leer_cuerpo_xml(Pedido()))
    assert e.value.status == 413
    assert len(leidos) == 4, f"leyó {len(leidos)} trozos: no cortó al pasarse del tope"


def test_dos_importaciones_a_la_vez_no_duplican(client, entorno, server, monkeypatch):
    """Buscar-y-crear es atómico: con los dos pedidos parados adentro de `buscar_importada`
    (sin lock los dos ven "no está" y crean), queda UNA playlist."""
    import threading
    barrera = threading.Barrier(2)
    real = server.db.buscar_importada

    def lenta(*a, **k):
        try:
            barrera.wait(timeout=1.5)    # con lock el segundo nunca llega: se rompe y sigue
        except threading.BrokenBarrierError:
            pass
        return real(*a, **k)
    monkeypatch.setattr(server.db, "buscar_importada", lenta)
    res = [None, None]

    def importar(i):
        res[i] = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "Techno"}).json()
    hilos = [threading.Thread(target=importar, args=(i,)) for i in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)
    assert sorted(r["estado"] for r in res) == ["creada", "ya-importada"], res
    assert res[0]["id"] == res[1]["id"]
    assert sum(1 for p in server.db.listar_playlists() if p["origen"] == "carpeta") == 1


@pytest.mark.skipif(os.name != "nt", reason="variantes de nombre que solo Windows abre como la misma carpeta")
def test_carpeta_con_otra_forma_del_nombre_es_la_misma(client, entorno, server):
    """El `origen_ref` sale del nombre EN DISCO: techno, TECHNO, 'Techno ' y 'Techno.' son Techno."""
    d = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "Techno"}).json()
    assert (d["estado"], d["ruta"]) == ("creada", "musica/Techno")
    for variante in ("techno", "TECHNO", "Techno ", "Techno."):
        r = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": variante})
        assert r.status_code == 200, r.text
        assert (r.json()["estado"], r.json()["id"]) == ("ya-importada", d["id"]), variante
    assert sum(1 for p in server.db.listar_playlists() if p["origen"] == "carpeta") == 1
    lista = client.get("/api/importar/carpetas", params={"raiz": 0, "ruta": "techno"}).json()
    assert (lista["ruta"], lista["nombre"]) == ("Techno", "Techno")


_ESCRITURAS_PROPIAS = [
    ("POST", "/api/playlists", {"nombre": "de otra página"}),
    ("PATCH", "/api/playlists/{pid}", {"nombre": "pisado"}),
    ("DELETE", "/api/playlists/{pid}", None),
    ("POST", "/api/playlists/{pid}/items", {"track": {"titulo": "Intruso", "artista": "X"}}),
    ("DELETE", "/api/playlists/{pid}/items/{iid}", None),
    ("POST", "/api/playlists/{pid}/orden", {"orden": []}),
    ("POST", "/api/playlists/{pid}/export", None),
]


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo"), _ESCRITURAS_PROPIAS)
@pytest.mark.parametrize("ajeno", [{"Origin": "https://malo.example"}, {"Sec-Fetch-Site": "cross-site"}])
def test_mis_playlists_rechazan_otra_pagina(client, entorno, server, metodo, ruta, cuerpo, ajeno):
    pid = client.post("/api/playlists", json={"nombre": "Mía"}).json()["playlist"]["id"]
    iid = client.post(f"/api/playlists/{pid}/items",
                      json={"track": {"titulo": "Uno", "artista": "A"}}).json()["id"]
    antes = server.db.get_playlist_mia(pid)
    n_antes = len(server.db.listar_playlists())
    r = client.request(metodo, ruta.format(pid=pid, iid=iid), json=cuerpo, headers=ajeno)
    assert r.status_code == 403 and "MusiFlix" in r.json()["mensaje"], r.text
    assert server.db.get_playlist_mia(pid) == antes, "la playlist cambió con un pedido de otra página"
    assert len(server.db.listar_playlists()) == n_antes
    # Y desde la propia app sí anda (la guarda no rechaza todo).
    propio = client.request(metodo, ruta.format(pid=pid, iid=iid), json=cuerpo,
                            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert propio.status_code != 403, propio.text


def test_db_agregar_item_ignora_la_ruta_del_track(entorno, server, tmp_path):
    """A nivel `db` (no solo el endpoint): `track["ruta"]` nunca llega al item."""
    secreto = tmp_path / "secreto.wav"
    secreto.write_bytes(b"x")
    pid = server.db.crear_playlist("Directo")["id"]
    r = server.db.agregar_item(pid, {"titulo": "T", "artista": "A", "ruta": str(secreto),
                                     "archivo": "secreto.wav", "formato": "exe"})
    it = server.db.get_playlist_mia(pid, True)["items"][0]
    assert (it["ruta"], it.get("nombre_archivo"), it.get("formato"), it["archivo_estado"]) ==         (None, None, None, "sin-archivo"), it


def test_db_item_viejo_sin_fuente_se_completa_y_no_se_duplica(entorno, server, tmp_path):
    """La home vieja guardaba el tema con fuente "": agregarlo de nuevo como "biblioteca"
    con su archivo completa ESE item."""
    audio = tmp_path / "uno.wav"
    audio.write_bytes(b"x")
    pid = server.db.crear_playlist("Home vieja")["id"]
    viejo = server.db.agregar_item(pid, {"titulo": "Uno", "artista": "Artista A"})
    r = server.db.agregar_item(pid, {"titulo": "Uno", "artista": "Artista A", "fuente": "biblioteca"},
                               str(audio))
    assert (r["dup"], r["id"], r["archivo_completado"]) == (True, viejo["id"], True), r
    items = server.db.get_playlist_mia(pid, True)["items"]
    assert len(items) == 1 and os.path.samefile(items[0]["ruta"], audio)
    assert items[0]["fuente"] == "biblioteca"


def test_bpm_con_decimal_no_se_redondea_a_entero(client, entorno, server):
    """El BPM del motor va con UN decimal: con un clic generado a 127.5 BPM (no entero) un
    `round(bpm)` daría un entero y se notaría."""
    _click(entorno["raiz"] / "Medio" / "medio.wav", 127.5, seed=4)
    pid = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "Medio"}).json()["id"]
    assert client.post(f"/api/playlists/{pid}/analizar", json={}).status_code == 200
    assert entorno["an"].esperar(120)
    a = _playlist(client, pid)["items"][0]["analisis"]
    with Store(entorno["db"]) as store:
        medido = store.get_features(os.path.abspath(entorno["raiz"] / "Medio" / "medio.wav")).bpm
    assert abs(medido - 127.5) <= 1.0, f"el motor midió {medido} y el generador hizo 127.5"
    assert abs(round(medido, 1) - round(medido)) >= 0.1, f"{medido} queda pegado a un entero: el test no distingue"
    assert a["bpm"] == round(medido, 1), f"la API dice {a['bpm']} y el motor midió {medido}"


def test_metricas_de_la_playlist_usan_lo_medido_por_el_motor(client, entorno):
    """Con un tema medido por el motor, las métricas salen SOLO de lo medido (con un decimal);
    el BPM de Rekordbox del otro tema queda afuera y se cuenta. Antes de medir: el de Rekordbox."""
    r = _importar(client, entorno["xml"], [0]).json()["resultados"][0]   # Peak
    pid = r["id"]
    antes = _playlist(client, pid)
    m = antes["metrics"]
    rb = [i["bpm"] for i in antes["items"] if i.get("bpm")]
    assert (m["bpm_fuente"], m["bpm_n"]) == ("rekordbox", len(rb)), m
    # Ácido (128.40 en el XML) se mide de verdad: un clic de 127.5 BPM en su lugar.
    acido = entorno["rutas"]["acido"]
    _click(acido, 127.5, seed=5)
    assert client.post(f"/api/playlists/{pid}/analizar", json={}).status_code == 200
    assert entorno["an"].esperar(120)
    d = _playlist(client, pid)
    medidos = [i["analisis"]["bpm"] for i in d["items"] if i["analisis"]["dato"] == "motor" and i["analisis"]["bpm"]]
    assert medidos, "el motor no midió ningún tema de Peak"
    m = d["metrics"]
    assert (m["bpm_fuente"], m["bpm_n"]) == ("motor", len(medidos)), m
    assert m["bpm_prom"] == round(sum(medidos) / len(medidos), 1)
    assert (m["bpm_min"], m["bpm_max"]) == (min(medidos), max(medidos))
    con_bpm = sum(1 for i in d["items"] if i["analisis"].get("bpm"))
    assert m["bpm_afuera"] == con_bpm - len(medidos)
