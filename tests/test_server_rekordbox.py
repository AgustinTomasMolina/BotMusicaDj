"""Tests de la API de f56: exportar una playlist a Rekordbox (XML con cues) e importar los cues
que el DJ ya tiene en Rekordbox, con la ida y vuelta completa.

Datos sintéticos: WAVs de `tests/sinteticos.wav` y una base del motor escrita con `upsert` (el
BPM y la key se ESCRIBEN y se comprueba que viajen tal cual; no se mide nada). Los valores
esperados de los POSITION_MARK son los de la prueba verificada en Rekordbox 7.2.16
(`pipeline/PRUEBA_CUES.md` §5) y los colores del dueño en números, escritos acá.
"""
import importlib
import os
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
from sinteticos import location_rb, wav, xml_rekordbox_playlists  # noqa: E402

from motor.modelos import TrackFeatures  # noqa: E402
from motor.store import Store  # noqa: E402

ROJO = {"Red": "255", "Green": "77", "Blue": "90"}
BLANCO = {"Red": "242", "Green": "243", "Blue": "248"}
NARANJA = {"Red": "255", "Green": "154", "Blue": "46"}
XML = {"Content-Type": "application/xml", "X-Nombre-Archivo": "export.xml"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    datos = tmp_path_factory.mktemp("server-rekordbox")
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
def client(server):
    return TestClient(server.app)


def _emb(i):
    from motor.embeddings import DIM
    return np.random.default_rng(i).normal(size=DIM).astype(np.float32)


@pytest.fixture
def entorno(server, tmp_path, monkeypatch):
    """Carpeta «Set» con tres temas: «uno» (analizado, key segura), «dos» (analizado, key
    dudosa 2/3) y «tres» (sin analizar). Marcas en «uno» y «dos». La playlist se importa como
    carpeta (orden alfabético)."""
    import playlist_analisis
    with server.db.SessionLocal() as s:
        for modelo in (server.db.MiPlaylistItem, server.db.MiPlaylist):
            for fila in s.scalars(server.db.select(modelo)):
                s.delete(fila)
        s.commit()
    raiz = tmp_path / "musica"
    rutas = {n: raiz / "Set" / f"{n}.wav" for n in ("uno", "dos", "tres")}
    for i, r in enumerate(rutas.values()):
        wav(r, 220.0 + 110 * i, 0.25)
    base = tmp_path / "djradio" / "base.sqlite"
    with Store(base) as st:
        st.upsert(rutas["uno"], TrackFeatures(bpm=128.4, key="8A", energy_raw=0.3, embedding=_emb(1),
                                              key_acuerdo="3/3", key_tramos="8A|8A|8A"),
                  duration=240.0, artist="Artista A", title="Uno")
        st.upsert(rutas["dos"], TrackFeatures(bpm=130.0, key="9A", energy_raw=0.2, embedding=_emb(2),
                                              key_acuerdo="2/3", key_tramos="9A|8A|9A"),
                  duration=200.0, artist="Artista B", title="Dos")
        st.add_cue_mark(rutas["uno"], "cue", 1.5, num=0, name="Drop")
        st.add_cue_mark(rutas["uno"], "loop", 10.0, 14.0, num=2, name="Loop 4")
        st.add_cue_mark(rutas["uno"], "memory", 0.5, name="Intro")
        st.add_cue_mark(rutas["uno"], "loop", 20.0, 21.5)
        st.add_cue_mark(rutas["dos"], "cue", 30.25, num=7)
    monkeypatch.setattr(server, "_LIB_ROOTS", [str(raiz)])
    monkeypatch.setattr(server, "_LIB_XML", "")
    monkeypatch.setenv("DJRADIO_DB", str(base))
    monkeypatch.setattr(server, "_radio_audio", {})
    an = playlist_analisis.AnalizadorFondo(server._analizar_y_guardar, server._al_guardar)
    monkeypatch.setattr(server, "_analizador", an)
    yield {"raiz": raiz, "rutas": rutas, "db": base, "tmp": tmp_path}
    an.detener(30)


def _pid(client):
    r = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "Set"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _sin_rutas(texto, *rutas):
    for r in rutas:
        for forma in (str(r), Path(r).as_posix()):
            assert forma not in texto, f"la respuesta trae una ruta de la PC: {forma}"


def _exportar(client, pid, **cuerpo):
    r = client.post(f"/api/playlists/{pid}/rekordbox", json=cuerpo)
    assert r.status_code == 200, r.text
    return r


def _marks(xml: bytes) -> dict:
    """{nombre de archivo: [atributos de cada POSITION_MARK]} del XML exportado."""
    from playlist_import import nombre_de_ruta, ruta_de_location
    root = ET.fromstring(xml)
    return {nombre_de_ruta(ruta_de_location(t.get("Location"))): [p.attrib for p in t.findall("POSITION_MARK")]
            for t in root.find("COLLECTION").findall("TRACK")}


def _filas(db, ruta):
    with Store(db) as st:
        return [(m.kind, m.num, m.start_ms, m.end_ms, m.name) for m in st.list_cue_marks(ruta)]


MARCAS_UNO = [
    {"Name": "Drop", "Type": "0", "Start": "1.500", "Num": "0", **ROJO},
    {"Name": "Loop 4", "Type": "4", "Start": "10.000", "End": "14.000", "Num": "2", **NARANJA},
    {"Name": "Intro", "Type": "0", "Start": "0.500", "Num": "-1"},
    {"Name": "", "Type": "4", "Start": "20.000", "End": "21.500", "Num": "-1", **NARANJA},
]


# --- exportar --------------------------------------------------------------------------------------

def test_resumen_dice_que_va_y_que_no_sin_rutas(client, entorno):
    pid = _pid(client)
    r = client.get(f"/api/playlists/{pid}/rekordbox")
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["archivo"], d["total"], d["incluidos"], d["keys_omitidas"]) == ("MusiFlix - Set.xml", 3, 2, 1)
    assert d["marcas"] == {"hot_cues": 2, "memory": 1, "loops": 2}
    assert [(o["titulo"], o["motivo"].split(":")[0]) for o in d["omitidos"]] == \
        [("tres", "Todavía no lo analizó el motor")]
    assert [(t["titulo"], t["incluido"], t["bpm"], t["camelot"], t["key_omitida"]) for t in d["temas"]] == [
        ("dos", True, 130.0, None, True), ("tres", False, None, None, False), ("uno", True, 128.4, "8A", False)]
    assert "ruta completa" in d["aviso_rutas"] and "ESTIMADA" in d["aviso_grilla"]
    _sin_rutas(r.text, entorno["raiz"], entorno["tmp"])


def test_exportar_baja_el_xml_con_los_cues_verificados(client, entorno, server):
    pid = _pid(client)
    antes = {n: (r.read_bytes(), r.stat().st_mtime_ns) for n, r in entorno["rutas"].items()}
    r = _exportar(client, pid)
    assert r.headers["content-type"].startswith("application/xml")
    assert r.headers["content-disposition"] == \
        "attachment; filename=\"MusiFlix - Set.xml\"; filename*=UTF-8''MusiFlix%20-%20Set.xml"
    assert (r.headers["x-musiflix-temas"], r.headers["x-musiflix-omitidos"],
            r.headers["x-musiflix-marcas"], r.headers["x-musiflix-grillas"]) == ("2", "1", "5", "0")
    import playlist_import
    playlist_import.validar_xml(r.content)            # sin DOCTYPE/ENTITY, UTF-8
    root = ET.fromstring(r.content)
    tracks = root.find("COLLECTION").findall("TRACK")
    assert root.find("COLLECTION").get("Entries") == "2"
    por = {t.get("Name"): t for t in tracks}
    assert sorted(por) == ["dos", "uno"]
    uno, dos = por["uno"], por["dos"]
    assert os.path.samefile(playlist_import.ruta_de_location(uno.get("Location")), entorno["rutas"]["uno"]), \
        "la Location no es el archivo original"
    assert (uno.get("AverageBpm"), uno.get("Tonality"), uno.get("TotalTime")) == ("128.4", "Am", "240")
    assert (dos.get("AverageBpm"), dos.get("Tonality")) == ("130.0", None), "la key dudosa no se escribe"
    assert [p.attrib for p in uno.findall("POSITION_MARK")] == MARCAS_UNO
    assert [p.attrib for p in dos.findall("POSITION_MARK")] == \
        [{"Name": "", "Type": "0", "Start": "30.250", "Num": "7", **BLANCO}]
    assert root.find(".//TEMPO") is None, "la grilla estimada no va por defecto"
    nodo = root.find("PLAYLISTS/NODE/NODE")
    ids = {t.get("TrackID"): t.get("Name") for t in tracks}
    assert (nodo.get("Name"), nodo.get("KeyType")) == ("Set", "0")
    assert [ids[t.get("Key")] for t in nodo.findall("TRACK")] == ["dos", "uno"], "el orden de la playlist"
    for n, r0 in entorno["rutas"].items():
        assert (r0.read_bytes(), r0.stat().st_mtime_ns) == antes[n], f"se tocó el audio {n}"


def test_exportar_no_lleva_marcas_fuera_del_audio(client, entorno):
    pid = _pid(client)
    with Store(entorno["db"]) as st:      # el archivo «cambió»: la base ahora dice 12 s
        con = st._con
        con.execute("UPDATE tracks SET duration = 12.0 WHERE path_key = ?", (Store._key(entorno["rutas"]["uno"]),))
        con.commit()
    d = client.get(f"/api/playlists/{pid}/rekordbox").json()
    assert (d["marcas"], d["marcas_fuera"]) == ({"hot_cues": 2, "memory": 1, "loops": 0}, 2)
    assert [m["Start"] for m in _marks(_exportar(client, pid).content)["uno.wav"]] == ["1.500", "0.500"]


def test_un_tema_que_cambio_desde_el_analisis_queda_afuera(client, entorno):
    """Lo medido describe OTRO audio: no se exporta (ni su BPM ni sus marcas) y se dice."""
    pid = _pid(client)
    os.utime(entorno["rutas"]["uno"], (1_000_000_000, 1_000_000_000))
    d = client.get(f"/api/playlists/{pid}/rekordbox").json()
    assert [(o["titulo"], o["motivo"]) for o in d["omitidos"]] == [
        ("tres", d["omitidos"][0]["motivo"]),
        ("uno", "El archivo cambió desde que se analizó: volvé a analizarlo.")]
    assert d["omitidos"][0]["motivo"].startswith("Todavía no lo analizó el motor")
    assert (d["incluidos"], d["marcas"]) == (1, {"hot_cues": 1, "memory": 0, "loops": 0})
    root = ET.fromstring(_exportar(client, pid).content)
    assert [t.get("Name") for t in root.find("COLLECTION").findall("TRACK")] == ["dos"]


def test_temas_de_dos_carpetas_con_el_mismo_nombre_cada_uno_a_su_archivo(client, entorno):
    """Dos «uno.wav» en carpetas distintas son DOS temas: cada Location es su archivo original
    (y no uno por nombre, ni la carpeta del otro)."""
    otro = entorno["raiz"] / "Otra" / "uno.wav"
    wav(otro, 990.0, 0.25)
    with Store(entorno["db"]) as st:
        st.upsert(otro, TrackFeatures(bpm=140.2, key="5A", energy_raw=0.1, embedding=_emb(3),
                                      key_acuerdo="3/3", key_tramos="5A|5A|5A"), duration=180.0)
        st.add_cue_mark(otro, "cue", 2.0, num=3, name="Otra")
    r = client.post("/api/importar/carpeta", json={"raiz": 0, "ruta": "", "recursivo": True})
    assert r.status_code == 200, r.text
    root = ET.fromstring(_exportar(client, r.json()["id"]).content)
    import playlist_import
    tracks = root.find("COLLECTION").findall("TRACK")
    por_bpm = {t.get("AverageBpm"): t for t in tracks}
    assert sorted(por_bpm) == ["128.4", "130.0", "140.2"]
    assert os.path.samefile(playlist_import.ruta_de_location(por_bpm["140.2"].get("Location")), otro)
    assert os.path.samefile(playlist_import.ruta_de_location(por_bpm["128.4"].get("Location")),
                            entorno["rutas"]["uno"])
    assert [p.get("Name") for p in por_bpm["140.2"].findall("POSITION_MARK")] == ["Otra"]
    assert [p.get("Name") for p in por_bpm["128.4"].findall("POSITION_MARK")][0] == "Drop"


def test_grilla_que_revienta_no_es_500(client, entorno, server, monkeypatch):
    pid = _pid(client)
    monkeypatch.setattr(server, "_grilla_export", lambda ruta, bpm: {"primer_beat_s": 0.1, "compas_ref": "x", "bpm": 128.0})
    r = _exportar(client, pid, incluir_grilla=True)
    assert r.headers["x-musiflix-grillas"] == "0" and b"<TEMPO" not in r.content


def test_grilla_solo_si_se_pide_y_solo_con_compas(client, entorno, server, monkeypatch):
    pid = _pid(client)
    pedidas = []

    def falsa(ruta, bpm):
        pedidas.append(Path(ruta).name)
        if Path(ruta).name == "uno.wav":
            return {"primer_beat_s": 0.1, "compas_ref": 0.569, "bpm": 128.4012}
        return {"primer_beat_s": 0.1, "compas_ref": None, "bpm": 130.0,
                "compas_motivo": "no se puede saber cuál beat es el 1"}
    monkeypatch.setattr(server, "_grilla_export", falsa)
    _exportar(client, pid)
    assert pedidas == [], "sin pedirla no se estima ni se escribe la grilla"
    r = _exportar(client, pid, incluir_grilla=True)
    root = ET.fromstring(r.content)
    tempos = {t.get("Name"): (t.find("TEMPO").attrib if t.find("TEMPO") is not None else None)
              for t in root.find("COLLECTION").findall("TRACK")}
    assert tempos == {"uno": {"Inizio": "0.569", "Bpm": "128.40", "Metro": "4/4", "Battito": "1"},
                      "dos": None}
    assert r.headers["x-musiflix-grillas"] == "1"


def test_exportar_rechaza_otra_pagina_opciones_malas_y_nada_que_exportar(client, entorno, server):
    pid = _pid(client)
    url = f"/api/playlists/{pid}/rekordbox"
    assert client.post(url, json={}, headers={"Origin": "https://malo.example"}).status_code == 403
    assert client.post(url, json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post(url, content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post(url, json={"incluir_grilla": "si"}).status_code == 400
    assert client.post(url, json={"ruta": "C:/"}).status_code == 400
    assert client.post("/api/playlists/99999/rekordbox", json={}).status_code == 404
    assert client.post("/api/playlists/abc/rekordbox", json={}).status_code == 404
    assert client.get("/api/playlists/abc/rekordbox").status_code == 404
    vacia = client.post("/api/playlists", json={"nombre": "Vacía"}).json()["playlist"]["id"]
    r = client.post(f"/api/playlists/{vacia}/rekordbox", json={})
    assert r.status_code == 409 and r.json()["mensaje"]
    # Base del motor ocupada: 409 con el motivo, sin la ruta de la base.
    con = sqlite3.connect(str(entorno["db"]))
    con.execute("BEGIN EXCLUSIVE")
    try:
        server._RADIO_ESPERA_S, viejo = 0.2, server._RADIO_ESPERA_S
        r = client.post(url, json={})
    finally:
        server._RADIO_ESPERA_S = viejo
        con.rollback()
        con.close()
    assert r.status_code == 409 and "ocupada" in r.json()["mensaje"], r.text
    _sin_rutas(r.text, entorno["db"])


def test_titulo_con_caracter_de_control_sale_como_xml_valido(client, entorno, server):
    pid = _pid(client)
    with server.db.SessionLocal() as s:
        it = s.scalars(server.db.select(server.db.MiPlaylistItem).where(
            server.db.MiPlaylistItem.titulo == "uno")).one()
        it.titulo = "uno\x01\x0b"
        s.commit()
    root = ET.fromstring(_exportar(client, pid).content)
    assert sorted(t.get("Name") for t in root.find("COLLECTION").findall("TRACK")) == ["dos", "uno"]


# --- ida y vuelta ------------------------------------------------------------------------------------

def _importar_xml(client, xml: bytes, **extra):
    r = client.post("/api/importar/rekordbox/leer", content=xml, headers=XML)
    assert r.status_code == 200, r.text
    d = r.json()
    r = client.post("/api/importar/rekordbox", json={"token": d["token"], "playlists": [0], **extra})
    assert r.status_code == 200, r.text
    return d, r.json()["resultados"][0], r.text


def test_ida_y_vuelta_exporta_reimporta_y_los_cues_son_los_mismos(client, entorno):
    pid = _pid(client)
    xml1 = _exportar(client, pid).content
    antes = {n: _filas(entorno["db"], r) for n, r in entorno["rutas"].items()}
    assert len(antes["uno"]) == 4 and len(antes["dos"]) == 1
    # Se borran todas las marcas de la página y se reimporta lo exportado.
    with Store(entorno["db"]) as st:
        for r in entorno["rutas"].values():
            for m in st.list_cue_marks(r):
                st.delete_cue_mark(r, m.id)
    lectura, res, texto = _importar_xml(client, xml1)
    assert (lectura["playlists"][0]["cues"], lectura["playlists"][0]["loops"]) == (3, 2)
    assert res["estado"] == "creada"
    c = res["cues"]
    assert (c["temas"], c["agregadas"], c["ya_estaban"], c["conservadas"], c["color_distinto"],
            c["ignoradas"], c["error"]) == (2, 5, 0, 0, 0, {}, None)
    despues = {n: _filas(entorno["db"], r) for n, r in entorno["rutas"].items()}
    assert despues == antes, "los cues que volvieron no son los que se exportaron"
    # Y exportar de nuevo da las MISMAS marcas, con los mismos colores.
    assert _marks(_exportar(client, pid).content) == _marks(xml1)
    _sin_rutas(texto, entorno["raiz"], entorno["tmp"])

    # Importar otra vez lo mismo (actualizar): no duplica.
    _, res2, _ = _importar_xml(client, xml1, actualizar=True)
    assert (res2["estado"], res2["cues"]["agregadas"], res2["cues"]["ya_estaban"]) == ("actualizada", 0, 5)
    assert {n: _filas(entorno["db"], r) for n, r in entorno["rutas"].items()} == antes


def test_actualizar_desde_rekordbox_la_pagina_gana_salvo_pisar(client, entorno):
    pid = _pid(client)
    xml = _exportar(client, pid).content          # pad A de «uno» en 1.500
    _importar_xml(client, xml)                    # ya importada (todo ya_estaban)
    uno = entorno["rutas"]["uno"]
    with Store(entorno["db"]) as st:              # en la página, el dueño mueve el pad A
        a = next(m for m in st.list_cue_marks(uno) if m.num == 0)
        st.update_cue_mark(uno, a.id, start_s=3.0)
    _, res, _ = _importar_xml(client, xml, actualizar=True)
    assert (res["cues"]["conservadas"], res["cues"]["reemplazadas"]) == (1, 0)
    assert [f for f in _filas(entorno["db"], uno) if f[1] == 0] == [("cue", 0, 3000, None, "Drop")], \
        "la página tiene que ganar por defecto"
    _, res, _ = _importar_xml(client, xml, actualizar=True, pisar_cues=True)
    assert (res["cues"]["conservadas"], res["cues"]["reemplazadas"], res["cues"]["pisar"]) == (0, 1, True)
    assert [f for f in _filas(entorno["db"], uno) if f[1] == 0] == [("cue", 0, 1500, None, "Drop")]
    assert len(_filas(entorno["db"], uno)) == 4, "pisar no borra las demás marcas"


# --- importar cues de un XML de Rekordbox ------------------------------------------------------------

def _xml_dj(raiz: Path, marcas: dict) -> bytes:
    """Una colección como la exporta Rekordbox, con POSITION_MARK propios en cada tema."""
    tracks = [dict(id=str(i), name=n, artist="DJ", genre="Techno", bpm="0.00", ton="", dur="240",
                   loc=location_rb(raiz / "Set" / f"{n}.wav")) for i, n in enumerate(marcas, 1)]
    xml = xml_rekordbox_playlists(tracks, [("playlist", "Del DJ", "0", [t["id"] for t in tracks])])
    for t in tracks:      # cambia la memory de 0.025 que trae el sintético por las del caso
        viejo = f'<TRACK TrackID="{t["id"]}"'
        cabeza, cola = xml.split(viejo, 1)
        bloque, resto = cola.split("</TRACK>", 1)
        bloque = bloque.replace('      <POSITION_MARK Name="" Type="0" Start="0.025" Num="-1"/>\n',
                                marcas[t["name"]])
        xml = cabeza + viejo + bloque + "</TRACK>" + resto
    return xml.encode("utf-8")


def test_importar_cues_del_dj_aun_sin_analizar(client, entorno, server):
    tres = entorno["rutas"]["tres"]
    xml = _xml_dj(entorno["raiz"], {
        "tres": ('<POSITION_MARK Name="Entrada" Type="0" Start="0.100" Num="1" Red="40" Green="226" Blue="20"/>'
                 '<POSITION_MARK Name="" Type="4" Start="0.050" End="0.200" Num="3" Red="255" Green="140" Blue="0"/>'
                 '<POSITION_MARK Name="" Type="0" Start="0.150" Num="-1"/>'
                 '<POSITION_MARK Name="fade" Type="1" Start="0.010" Num="-1"/>'
                 '<POSITION_MARK Name="" Type="0" Start="9.000" Num="4"/>'),   # el WAV dura 0.25 s
    })
    _, res, texto = _importar_xml(client, xml)
    c = res["cues"]
    assert (c["temas"], c["agregadas"], c["fuera_del_tema"], c["color_distinto"], c["ignoradas"], c["error"]) == \
        (1, 3, 1, 2, {"tipo": 1}, None)
    assert _filas(entorno["db"], tres) == [("cue", 1, 100, None, "Entrada"), ("loop", 3, 50, 200, None),
                                           ("memory", None, 150, None, None)]
    _sin_rutas(texto, entorno["raiz"])
    # No son huérfanas (el archivo está; falta analizarlo) y la playlist ya muestra los puntos.
    d = client.get("/api/radio/marcas/conteo").json()
    assert d["huerfanas"] == {"tracks": 0, "marcas": 0, "archivos": []}
    it = client.get(f"/api/playlists/{res['id']}").json()["data"]["items"][0]
    assert (it["analisis"]["estado"], it["analisis"]["marcas"]) == \
        ("pendiente", {"total": 3, "pads": [1], "memory": 1, "loop": 1})


def test_cues_de_un_tema_ambiguo_se_cuentan_aparte(client, entorno):
    raiz = entorno["raiz"]
    wav(raiz / "A" / "rep.wav", 300.0, 0.25)
    wav(raiz / "B" / "rep.wav", 400.0, 0.25)
    tracks = [dict(id="1", name="Rep", artist="DJ", genre="", bpm="0.00", ton="", dur="1",
                   loc=location_rb("C:/Users/otra-pc/Viejo/rep.wav"))]
    xml = xml_rekordbox_playlists(tracks, [("playlist", "Amb", "0", ["1"])]).replace(
        '<POSITION_MARK Name="" Type="0" Start="0.025" Num="-1"/>',
        '<POSITION_MARK Name="" Type="0" Start="0.025" Num="-1"/><POSITION_MARK Name="" Type="0" Start="0.1" Num="0"/>')
    _, res, _ = _importar_xml(client, xml.encode("utf-8"))
    assert (res["cues"]["ambiguas"], res["cues"]["sin_archivo"], res["cues"]["agregadas"]) == (2, 0, 0), \
        "las marcas de un tema con el nombre repetido no son «sin archivo»"


def test_cues_sin_archivo_se_cuentan_y_base_ocupada_no_frena_la_importacion(client, entorno, server, monkeypatch):
    raiz = entorno["raiz"]
    xml = _xml_dj(raiz, {"tres": '<POSITION_MARK Name="" Type="0" Start="0.100" Num="0"/>',
                         "fantasma": '<POSITION_MARK Name="" Type="0" Start="1.0" Num="0"/>'
                                     '<POSITION_MARK Name="" Type="0" Start="2.0" Num="-1"/>'})
    monkeypatch.setattr(server, "_RADIO_ESPERA_S", 0.2)
    con = sqlite3.connect(str(entorno["db"]))
    con.execute("BEGIN EXCLUSIVE")
    try:
        _, res, texto = _importar_xml(client, xml)
    finally:
        con.rollback()
        con.close()
    assert res["estado"] == "creada", "la playlist se importa aunque los cues no puedan guardarse"
    assert res["cues"]["sin_archivo"] == 2 and "ocupada" in res["cues"]["error"]
    assert "volvé a importar la playlist tildando «Actualizar»" in res["cues"]["error"], \
        "el error no dice qué hacer para traer los cues"
    _sin_rutas(texto, entorno["db"])
    assert _filas(entorno["db"], entorno["rutas"]["tres"]) == []
