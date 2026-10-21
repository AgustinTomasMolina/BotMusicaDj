"""Licencia y origen en el historial de descargas (f66).

La tabla `descargas_hist` no tenía ni licencia ni origen. Ahora los guarda, con el mismo
criterio que el resto (CLAUDE.md, 2026-10-09): lo que la fuente DECLARA (hoy solo Jamendo:
`license_ccurl` y la página del tema) y, si no declara nada, el literal «no declarado», nunca
uno inventado ni uno que mande el cliente.

La base del dueño ya tiene la tabla con el esquema viejo: `_migrar` le agrega las dos columnas
con `ALTER TABLE ... ADD COLUMN`, sin tocar las filas que ya estaban (quedan en NULL y se leen
como «no declarado»). Los valores esperados están escritos a mano o salen del fixture de
Jamendo (`tests/fixtures/fuentes_externas.json`), nunca del código bajo prueba.
"""
import copy
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db

FX = json.loads((Path(__file__).parent / "fixtures" / "fuentes_externas.json").read_text(encoding="utf-8"))

# El CREATE TABLE de `descargas_hist` como lo dejaba el código de antes de f66 (main 28eb87c):
# las mismas columnas, sin licencia ni origen.
DDL_VIEJO = """CREATE TABLE descargas_hist (
    id INTEGER NOT NULL, user_id INTEGER, titulo VARCHAR(400) NOT NULL, artista VARCHAR(400) NOT NULL,
    fuente VARCHAR(40) NOT NULL, url TEXT NOT NULL, formato VARCHAR(10) NOT NULL,
    archivo VARCHAR(500) NOT NULL, ruta TEXT NOT NULL, grade VARCHAR(4), color VARCHAR(16),
    calidad_txt VARCHAR(200), bpm INTEGER, camelot VARCHAR(8), genero VARCHAR(100), thumbnail TEXT,
    creado_en DATETIME NOT NULL, PRIMARY KEY (id))"""

FILAS_VIEJAS = [
    (1, None, "Viejo Uno", "Artista A", "youtube", "https://www.youtube.com/watch?v=aaaaaaaaaaa", "wav",
     "Viejo Uno - Artista A.wav", "C:/m/Viejo Uno - Artista A.wav", "B", "#00ff00", "320 kbps", 128,
     "8A", "Techno", None, "2026-09-01 10:00:00.000000"),
    (2, None, "Viejo Dos", "Artista B", "hitplayer", "https://example.invalid/2.mp3", "mp3",
     "Viejo Dos - Artista B.mp3", "C:/m/Viejo Dos - Artista B.mp3", "D", None, None, None,
     None, None, None, "2026-09-02 11:30:00.000000"),
]
COLS_VIEJAS = ("id, user_id, titulo, artista, fuente, url, formato, archivo, ruta, grade, color, "
               "calidad_txt, bpm, camelot, genero, thumbnail, creado_en")


@pytest.fixture
def base_vieja(tmp_path, monkeypatch):
    """Una base con `descargas_hist` como la tiene hoy el dueño (sin las columnas de f66)."""
    ruta = tmp_path / "vieja.db"
    with sqlite3.connect(ruta) as con:
        con.execute(DDL_VIEJO)
        con.executemany(f"INSERT INTO descargas_hist ({COLS_VIEJAS}) VALUES ({', '.join('?' * 17)})", FILAS_VIEJAS)
    eng = create_engine(f"sqlite:///{ruta}")
    db.Busqueda.__table__.create(eng)       # las otras dos del historial (`listar_historial` las lee)
    db.Playlist.__table__.create(eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, expire_on_commit=False))
    yield ruta, eng
    eng.dispose()


def _columnas(ruta) -> list[str]:
    with sqlite3.connect(ruta) as con:
        return [r[1] for r in con.execute("PRAGMA table_info(descargas_hist)")]


def _filas(ruta, cols=COLS_VIEJAS) -> list[tuple]:
    with sqlite3.connect(ruta) as con:
        return con.execute(f"SELECT {cols} FROM descargas_hist ORDER BY id").fetchall()


def test_migracion_agrega_licencia_y_origen_sin_perder_filas(base_vieja):
    ruta, eng = base_vieja
    assert "licencia" not in _columnas(ruta), "el caso no prueba nada: la base vieja ya tenía la columna"
    assert db._migrar(eng) == ["descargas_hist.licencia", "descargas_hist.origen"]
    assert _columnas(ruta)[-2:] == ["licencia", "origen"]
    assert _filas(ruta) == FILAS_VIEJAS, "la migración tocó las filas que ya estaban"
    assert _filas(ruta, "id, licencia, origen") == [(1, None, None), (2, None, None)], \
        "las filas viejas tienen que quedar en NULL (no se reescribe nada)"


def test_migracion_es_idempotente(base_vieja):
    ruta, eng = base_vieja
    db._migrar(eng)
    db.registrar_descarga(titulo="Nuevo", fuente="jamendo", licencia="http://creativecommons.org/licenses/by/4.0/",
                          origen="https://www.jamendo.com/track/9")
    antes = _filas(ruta, "*")
    assert db._migrar(eng) == [], "la segunda vez no tenía que agregar nada"
    assert db._migrar(eng) == []
    assert _filas(ruta, "*") == antes, "correr la migración otra vez cambió datos"
    assert _columnas(ruta).count("licencia") == 1


def test_filas_viejas_y_descargas_sin_dato_dicen_no_declarado(base_vieja):
    _, eng = base_vieja
    db._migrar(eng)
    db.registrar_descarga(titulo="Sin nada", fuente="youtube")
    db.registrar_descarga(titulo="Vacios", fuente="youtube", licencia="  ", origen=None)
    db.registrar_descarga(titulo="Con datos", fuente="jamendo", licencia="http://creativecommons.org/licenses/by-nc/3.0/",
                          origen="https://www.jamendo.com/track/77")
    hist = {d["titulo"]: (d["licencia"], d["origen"]) for d in db.listar_historial()["descargas"]}
    assert hist == {
        "Viejo Uno": ("no declarado", "no declarado"),
        "Viejo Dos": ("no declarado", "no declarado"),
        "Sin nada": ("no declarado", "no declarado"),
        "Vacios": ("no declarado", "no declarado"),
        "Con datos": ("http://creativecommons.org/licenses/by-nc/3.0/", "https://www.jamendo.com/track/77"),
    }


def test_el_default_de_registrar_descarga_se_guarda_como_literal(base_vieja):
    """Lo nuevo se GUARDA con el literal (no NULL): es lo que el modelo del motor también guarda."""
    ruta, eng = base_vieja
    db._migrar(eng)
    db.registrar_descarga(titulo="Nuevo", fuente="youtube")
    assert _filas(ruta, "titulo, licencia, origen")[-1] == ("Nuevo", "no declarado", "no declarado")
    from fuentes.base import NO_DECLARADO
    assert db.NO_DECLARADO == NO_DECLARADO == "no declarado"


# --- server: una descarga de Jamendo guarda la licencia que declara Jamendo ---------------------

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from fuentes import registro  # noqa: E402
from fuentes.jamendo import JamendoProvider  # noqa: E402

ORIGEN_1 = "https://www.jamendo.com/track/1500001/campo-abierto"   # la página que da la API


class _Resp:
    def __init__(self, body, status=200):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class _Jamendo:
    """El HTTP de la API de Jamendo, de juguete: contesta del fixture por id y anota cada pedido.

    `licencias`: la licencia de 1500001 en el 1.º, 2.º… pedido (para ver de CUÁL respuesta sale
    la que se guarda). `falla`: la respuesta HTTP con la que contesta en vez del JSON."""

    def __init__(self, licencias=None, falla=None):
        self.body = copy.deepcopy(FX["jamendo_busqueda"])
        # La página del tema distinta de la url canónica: así se ve que el origen sale de la API.
        self.body["results"][0]["shareurl"] = ORIGEN_1
        self.licencias, self.falla = licencias or [], falla
        self.pedidos = []

    def __call__(self, url, params=None, timeout=None):
        self.pedidos.append((url, dict(params or {})))
        if self.falla is not None:
            return self.falla
        body = copy.deepcopy(self.body)
        if len(self.pedidos) <= len(self.licencias):
            body["results"][0]["license_ccurl"] = self.licencias[len(self.pedidos) - 1]
        res = [r for r in body["results"] if r["id"] == (params or {}).get("id")]
        return _Resp({"headers": {"status": "success", "code": 0}, "results": res})


@pytest.fixture
def server(tmp_path, monkeypatch):
    datos = tmp_path / "datos"
    monkeypatch.setenv("MUSIFLIX_DATA_DIR", str(datos))
    monkeypatch.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
    monkeypatch.setenv("MUSIFLIX_SIN_CALENTAR", "1")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MUSIC_WATCH_FOLDER", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for mod in ("server", "jobs", "db", "tasks"):
        sys.modules.pop(mod, None)
    srv = importlib.import_module("server")
    bajados = tmp_path / "bajados"
    bajados.mkdir()
    monkeypatch.setattr(srv, "DOWNLOADS_DIR", bajados)
    # Sin red, sin ffmpeg y sin tocar tags: lo que se prueba es qué va al historial.
    monkeypatch.setattr(srv, "_calidad_espectral", lambda ruta: {"badge": "?", "calidad": "sin medir", "metodo": "test"})
    monkeypatch.setattr(srv, "_taggear_descarga", lambda *a, **k: None)
    con_jamendo(srv, monkeypatch, _Jamendo())
    return srv


def _audio(url):
    return iter([b"ID3-audio-de-prueba" * 50])


def con_jamendo(srv, monkeypatch, http: _Jamendo, stream=_audio) -> _Jamendo:
    """El registro del server con un Jamendo que contesta con `http` y baja con `stream` (sin red)."""
    reg = registro.Registry([JamendoProvider(client_id="clave-de-prueba", http_get=http, http_stream=stream)])
    monkeypatch.setattr(srv, "_fuentes_externas", lambda: reg)
    return http


JAMENDO_1 = {"titulo": "Campo Abierto", "artista": "Coro de Niebla", "fuente": "jamendo",
             "url": "https://www.jamendo.com/track/1500001"}


def test_descarga_de_jamendo_guarda_su_licencia_y_la_api_la_devuelve(server):
    esperado = FX["jamendo_busqueda"]["results"][0]["license_ccurl"]
    assert esperado == "http://creativecommons.org/licenses/by-sa/3.0/"
    with TestClient(server.app) as c:
        r = server.procesar_descarga({
            "titulo": "Campo Abierto", "artista": "Coro de Niebla", "fuente": "jamendo", "formato": "mp3",
            "url": "https://www.jamendo.com/track/1500001",
            # Lo que mande el cliente no cuenta: la licencia es la que declara Jamendo.
            "licencia": "CC0 (inventada por el cliente)", "origen": "https://evil.example/"})
        assert r["exito"] is True, r
        d = c.get("/api/historial").json()["descargas"]
    assert [(x["titulo"], x["fuente"], x["licencia"], x["origen"]) for x in d] == [
        ("Campo Abierto", "jamendo", esperado, ORIGEN_1)]


def test_descarga_de_otra_fuente_dice_no_declarado_aunque_el_cliente_mande_una(server, monkeypatch):
    def bajar(url, nombre):
        (server.DOWNLOADS_DIR / f"{nombre}.mp3").write_bytes(b"ID3-mp3" * 50)
        return {"ok": True, "archivo": f"{nombre}.mp3"}

    monkeypatch.setattr(server, "_descargar_directo", bajar)
    with TestClient(server.app) as c:
        r = server.procesar_descarga({
            "titulo": "Tema MP3", "artista": "Artista M", "fuente": "hitplayer", "formato": "mp3",
            "url": "https://example.invalid/tema.mp3",
            "licencia": "http://creativecommons.org/licenses/by/4.0/", "origen": "https://example.invalid/"})
        assert r["exito"] is True, r
        d = c.get("/api/historial").json()["descargas"]
    assert [(x["titulo"], x["licencia"], x["origen"]) for x in d] == [("Tema MP3", "no declarado", "no declarado")]


def test_jamendo_un_solo_pedido_y_la_licencia_es_la_de_ese_pedido(server, monkeypatch):
    # Si Jamendo contestara distinto en un segundo pedido, se vería: la 1.ª respuesta trae la
    # licencia del fixture y cualquier otra, la de 1500002 (también del fixture).
    primera = FX["jamendo_busqueda"]["results"][0]["license_ccurl"]
    otra = FX["jamendo_busqueda"]["results"][1]["license_ccurl"]
    assert (primera, otra) == ("http://creativecommons.org/licenses/by-sa/3.0/",
                               "http://creativecommons.org/licenses/by-nc-nd/3.0/")
    http = con_jamendo(server, monkeypatch, _Jamendo(licencias=[primera, otra, otra]))
    with TestClient(server.app) as c:
        r = server.procesar_descarga(dict(JAMENDO_1, formato="mp3"))
        assert r["exito"] is True, r
        d = c.get("/api/historial").json()["descargas"]
    assert http.pedidos == [("https://api.jamendo.com/v3.0/tracks/",
                             {"client_id": "clave-de-prueba", "format": "json", "id": "1500001"})], \
        "el permiso y la licencia tienen que salir de UNA respuesta"
    assert [(x["licencia"], x["origen"]) for x in d] == [(primera, ORIGEN_1)]


def _audio_cortado(url):
    yield b"medio archivo"
    raise TimeoutError("se cortó")


@pytest.mark.parametrize("http_kw, stream, mensaje", [
    ({"falla": _Resp({}, status=503)}, _audio, "Jamendo bloqueó (HTTP 503)."),
    ({}, _audio_cortado, "Jamendo no contestó."),
], ids=["api-503", "audio-cortado"])
def test_jamendo_que_falla_al_bajar_no_deja_historial_y_dice_por_que(server, monkeypatch, http_kw, stream, mensaje):
    con_jamendo(server, monkeypatch, _Jamendo(**http_kw), stream)
    with TestClient(server.app) as c:
        r = server.procesar_descarga(dict(JAMENDO_1, formato="mp3"))
        d = c.get("/api/historial").json()["descargas"]
    assert r == {"exito": False, "mensaje": mensaje}
    assert d == [], "una descarga que falló quedó en el historial"
    assert list(server.DOWNLOADS_DIR.iterdir()) == []


def test_jamendo_con_formato_wav_guarda_mp3_que_es_lo_que_se_bajo(server):
    # Jamendo da un MP3 y acá no se convierte: el historial no puede decir «wav».
    with TestClient(server.app) as c:
        r = server.procesar_descarga(dict(JAMENDO_1, formato="wav"))
        assert r["exito"] is True, r
        d = c.get("/api/historial").json()["descargas"]
    assert [(x["archivo"], x["formato"]) for x in d] == [("Campo Abierto - Coro de Niebla.mp3", "mp3")]


def test_jamendo_a_un_item_de_playlist_con_formato_wav_lo_anota_como_mp3(server, monkeypatch):
    # El mismo caso bajando desde la playlist (f41): lo que se anota en ESE item
    # (`db.marcar_item_descargado`) también es el formato del archivo que quedó.
    monkeypatch.setattr(server.jobs, "queue_disponible", lambda: False)
    monkeypatch.setattr(server, "_duracion_archivo", lambda ruta: None)          # sin ffprobe
    monkeypatch.setattr(server, "_resolver_host", lambda host: ["93.184.215.14"])  # sin DNS
    with TestClient(server.app) as c:
        pid = c.post("/api/playlists", json={"nombre": "Con Jamendo"}).json()["playlist"]["id"]
        iid = c.post(f"/api/playlists/{pid}/items", json={"track": JAMENDO_1}).json()["id"]
        r = c.post(f"/api/playlists/{pid}/items/{iid}/descargar", json={"formato": "wav"})
        assert r.status_code == 200 and r.json()["exito"] is True, r.json()
        items = c.get(f"/api/playlists/{pid}").json()["data"]["items"]
        d = c.get("/api/historial").json()["descargas"]
    assert [(it["id"], it["nombre_archivo"], it["formato"], it["descargado"]) for it in items] == [
        (iid, "Campo Abierto - Coro de Niebla.mp3", "mp3", True)]
    assert r.json()["item"]["formato"] == "mp3"
    assert [(x["archivo"], x["formato"]) for x in d] == [("Campo Abierto - Coro de Niebla.mp3", "mp3")]


def test_descarga_directa_sin_extension_guarda_formato_vacio_no_el_elegido(server, monkeypatch):
    # Un archivo sin extensión: no se sabe qué formato es, y el elegido (wav) sería mentira.
    def bajar(url, nombre):
        (server.DOWNLOADS_DIR / nombre).write_bytes(b"ID3-mp3" * 50)
        return {"ok": True, "archivo": nombre}

    monkeypatch.setattr(server, "_descargar_directo", bajar)
    with TestClient(server.app) as c:
        r = server.procesar_descarga({"titulo": "Sin Ext", "artista": "Artista X", "fuente": "hitplayer",
                                      "formato": "wav", "url": "https://example.invalid/sin-ext"})
        assert r["exito"] is True, r
        d = c.get("/api/historial").json()["descargas"]
    assert [(x["archivo"], x["formato"]) for x in d] == [("Sin Ext - Artista X", "")]
