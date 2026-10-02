"""Bajar desde la playlist (f41): POST /api/playlists/{pid}/items/{item_id}/descargar.

Lo que se protege:
- el archivo queda en ESE item (por id) y no en otra playlist activa, ni duplicado si la
  playlist es la activa (antes `_hist_descarga` lo mandaba a la activa por identidad aproximada);
- lo que se baja sale del item guardado: el cliente no puede elegir url, título ni ruta;
- un fallo queda como fallo, con el motivo real, y el item sigue "falta bajar";
- el .m3u8 de la playlist incluye lo bajado.

Nada sale a internet: el descargador (`_descargar_sync`), la búsqueda en YouTube y el
tagueo son dobles que anotan con qué los llamaron. La calidad que devuelve el doble es un
metadato de yt-dlp (Opus 160k) y la nota esperada sale de la tabla de `_grado`, no del doble.
"""
import importlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """El módulo `server` con datos y descargas en un temporal (receta de test_server_sets)."""
    datos = tmp_path_factory.mktemp("server-playlist-descargar")
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


class Descargador:
    """Doble de `_descargar_sync`: escribe un archivo en downloads/ y devuelve la calidad del
    metadato de yt-dlp, como el real. `falla` = la excepción que tira en vez de bajar."""

    def __init__(self, server, falla: Exception | None = None, durante=None):
        self.server, self.falla, self.durante, self.llamadas = server, falla, durante, []

    def __call__(self, url, titulo, formato):
        self.llamadas.append((url, titulo, formato))
        if self.durante:
            self.durante()
        if self.falla:
            raise self.falla
        archivo = f"{self.server._safe_name(titulo)}.{formato}"
        (self.server.DOWNLOADS_DIR / archivo).write_bytes(b"audio de prueba")
        return {"ok": True, "archivo": archivo,
                "calidad": self.server._calidad_desde_ydl({"acodec": "opus", "abr": 160, "ext": "webm"})}


@pytest.fixture
def dobles(server, monkeypatch):
    d = {"bajar": Descargador(server), "tags": [], "youtube": []}
    monkeypatch.setattr(server, "_descargar_sync", lambda *a: d["bajar"](*a))

    def _no_directo(*a):
        raise AssertionError("la descarga directa no tenía que usarse")
    monkeypatch.setattr(server, "_descargar_directo", _no_directo)
    monkeypatch.setattr(server, "_taggear_descarga",
                        lambda archivo, titulo, artista, payload, cal: d["tags"].append((archivo, dict(payload))))

    def _youtube(consulta, n):
        d["youtube"].append(consulta)
        return [{"url": "https://www.youtube.com/watch?v=equivalente"}]
    monkeypatch.setattr(server.search_agent, "buscar_en_youtube", _youtube)
    monkeypatch.setattr(server.jobs, "queue_disponible", lambda: False)
    return d


TEMA_A = {"titulo": "Tema A", "artista": "Artista A", "fuente": "youtube",
          "url": "https://www.youtube.com/watch?v=aaa", "bpm": 128.4, "camelot": "8A",
          "genero": "Techno", "duracion": 301, "thumbnail": None}
TEMA_B = {"titulo": "Tema B", "artista": "Artista B", "fuente": "soundcloud",
          "url": "https://soundcloud.com/x/tema-b", "duracion": 250}


def _playlist(client, nombre, *temas) -> tuple[int, list[int]]:
    pid = client.post("/api/playlists", json={"nombre": nombre}).json()["playlist"]["id"]
    ids = [client.post(f"/api/playlists/{pid}/items", json={"track": t}).json()["id"] for t in temas]
    return pid, ids


def _items(client, pid) -> dict:
    return {it["id"]: it for it in client.get(f"/api/playlists/{pid}").json()["data"]["items"]}


def _bajar(client, pid, iid, cuerpo=None):
    return client.post(f"/api/playlists/{pid}/items/{iid}/descargar", json=cuerpo or {"formato": "wav"})


def test_bajar_marca_ese_item_y_no_ensucia_otra_activa(server, client, dobles):
    pid, (a, b) = _playlist(client, "Techno", TEMA_A, TEMA_B)
    # Otra playlist ACTIVA con el mismo tema: el camino viejo (activa + identidad) lo marcaba ahí.
    otra, (a_otra,) = _playlist(client, "Activa", TEMA_A)
    client.patch(f"/api/playlists/{otra}", json={"activar": True})

    r = _bajar(client, pid, a)
    assert r.status_code == 200
    d = r.json()
    assert d["exito"] is True, d
    archivo = f"{server._safe_name('Tema A - Artista A')}.wav"
    assert dobles["bajar"].llamadas == [(TEMA_A["url"], "Tema A - Artista A", "wav")]

    items = _items(client, pid)
    esperado_grado = server._grado({"efectivo": 240})          # Opus 160k × 1.5
    assert (items[a]["descargado"], items[a]["formato"], items[a]["grade"], items[a]["color"]) == \
        (True, "wav", esperado_grado["grade"], esperado_grado["color"])
    assert items[a]["grade"] == "B", "Opus 160k (efectivo 240) es B en la tabla de _grado"
    assert items[b]["descargado"] is False, "bajar A no puede marcar B"
    assert d["item"]["id"] == a and d["item"]["descargado"] is True
    assert "ruta" not in d["item"], "la ruta en disco no viaja a la pantalla"
    # La ruta guardada es la del archivo bajado.
    with server.db.SessionLocal() as s:
        assert s.get(server.db.MiPlaylistItem, a).ruta == str(server.DOWNLOADS_DIR / archivo)

    otra_items = _items(client, otra)
    assert list(otra_items) == [a_otra], "la playlist activa ganó o perdió temas"
    assert otra_items[a_otra]["descargado"] is False, "la descarga desde Techno ensució la playlist activa"
    # El historial de descargas sí la registra (mismo flujo que el buscador).
    hist = client.get("/api/historial").json()["descargas"]
    assert [(h["titulo"], h["archivo"], h["formato"]) for h in hist[:1]] == [("Tema A", archivo, "wav")]
    # El tagueo recibe los datos del item guardado (BPM con decimal, key, género).
    assert [(t[0], t[1]["bpm"], t[1]["camelot"], t[1]["genero"]) for t in dobles["tags"]] == \
        [(archivo, 128.4, "8A", "Techno")]


def test_si_la_playlist_es_la_activa_no_se_duplica(server, client, dobles):
    """Un tema de Spotify se baja del equivalente de YouTube: la url cambia, y la búsqueda por
    identidad de la activa (fuente|url|título|artista) no lo encontraba y AGREGABA otra fila."""
    sp = {"titulo": "Tema S", "artista": "Artista S", "fuente": "spotify",
          "url": "https://open.spotify.com/track/xyz", "bpm": 131.0}
    pid, (a, s_id) = _playlist(client, "Activa y bajando", TEMA_A, sp)
    client.patch(f"/api/playlists/{pid}", json={"activar": True})
    assert _bajar(client, pid, s_id).json()["exito"] is True
    items = _items(client, pid)
    assert sorted(items) == sorted([a, s_id]), f"la playlist activa quedó con {len(items)} temas"
    assert (items[a]["descargado"], items[s_id]["descargado"]) == (False, True)
    assert items[s_id]["url"] == sp["url"], "el item conserva su link original"


def test_el_cliente_no_elige_que_se_baja(server, client, dobles):
    pid, (a,) = _playlist(client, "Segura", TEMA_A)
    r = _bajar(client, pid, a, {"formato": "mp3", "url": "https://malo.example/x.mp3",
                                "titulo": "Otro", "fuente": "ligaudio", "ruta": "C:/Windows/x"})
    assert r.json()["exito"] is True
    # La url y el título son los del item; del cuerpo solo cuenta el formato.
    assert dobles["bajar"].llamadas == [(TEMA_A["url"], "Tema A - Artista A", "mp3")]
    assert _items(client, pid)[a]["formato"] == "mp3"


def test_formato_fuera_de_la_lista_es_400(server, client, dobles):
    pid, (a,) = _playlist(client, "Formato", TEMA_A)
    r = _bajar(client, pid, a, {"formato": "exe"})
    assert (r.status_code, r.json()["mensaje"]) == (400, "Formato «exe» no válido: elegí WAV, FLAC, MP3.")
    assert dobles["bajar"].llamadas == []


def test_item_ajeno_o_playlist_inexistente_es_404(server, client, dobles):
    pid, _a = _playlist(client, "Una", TEMA_A)
    otra, (de_otra,) = _playlist(client, "Otra", TEMA_B)
    r = _bajar(client, pid, de_otra)
    assert (r.status_code, r.json()["mensaje"]) == (404, "Ese tema no está en esta playlist.")
    r = _bajar(client, 99999, de_otra)
    assert (r.status_code, r.json()["mensaje"]) == (404, "Playlist no encontrada.")
    assert dobles["bajar"].llamadas == [], "un item ajeno no se baja"
    assert _items(client, otra)[de_otra]["descargado"] is False


def test_item_sin_link_dice_el_motivo(server, client, dobles):
    # Lo que agrega la home (fromLibrary.paraPlaylist): título, artista, BPM, key; sin fuente ni url.
    local = {"titulo": "Mi edit", "artista": "Yo", "bpm": 140.2, "camelot": "5A"}
    raro = {"titulo": "Raro", "artista": "X", "fuente": "youtube", "url": "file:///C:/musica/x.wav"}
    pid, (lo, ra, _ta) = _playlist(client, "Mezcla", local, raro, TEMA_A)
    items = _items(client, pid)
    assert items[lo]["motivo_no_bajable"] == server.SIN_LINK
    assert items[ra]["motivo_no_bajable"] == "El link guardado no es una dirección web (http/https): no lo puedo bajar."
    assert [it["motivo_no_bajable"] for it in items.values() if it["titulo"] == "Tema A"] == [None]
    r = _bajar(client, pid, lo)
    assert (r.status_code, r.json()["mensaje"]) == (400, server.SIN_LINK)
    assert _bajar(client, pid, ra).status_code == 400
    assert dobles["bajar"].llamadas == [] and dobles["youtube"] == [], \
        "un item sin link no puede terminar buscándose en YouTube por título"


def test_fallo_de_descarga_queda_con_su_motivo_y_se_puede_reintentar(server, client, dobles):
    pid, (a,) = _playlist(client, "Falla", TEMA_A)
    antes = len(client.get("/api/historial").json()["descargas"])
    dobles["bajar"] = Descargador(server, falla=Exception("ERROR: [youtube] aaa: Video unavailable"))
    r = _bajar(client, pid, a)
    d = r.json()
    assert (r.status_code, d["exito"], d["mensaje"]) == (200, False, "ERROR: [youtube] aaa: Video unavailable")
    assert d["item"]["descargado"] is False
    assert _items(client, pid)[a]["descargado"] is False, "un fallo no puede marcar el item como bajado"
    assert len(client.get("/api/historial").json()["descargas"]) == antes, "un fallo no va al historial"
    # Reintento: ahora baja.
    dobles["bajar"] = Descargador(server)
    assert _bajar(client, pid, a).json()["exito"] is True
    assert _items(client, pid)[a]["descargado"] is True
    # Ya bajado: no se vuelve a bajar.
    r = _bajar(client, pid, a)
    assert (r.status_code, r.json()["mensaje"]) == (409, "Este tema ya está descargado.")


def test_bajado_pero_no_anotado_no_dice_exito(server, client, dobles):
    """Si el item desaparece mientras baja (lo quitaron), el archivo existe pero la playlist no
    lo tiene: decir "listo" sería mentir."""
    pid, (a,) = _playlist(client, "Se va", TEMA_A)
    dobles["bajar"] = Descargador(server, durante=lambda: server.db.quitar_item(a))
    d = _bajar(client, pid, a).json()
    archivo = f"{server._safe_name('Tema A - Artista A')}.wav"
    assert (d["exito"], d["mensaje"], d["item"]) == (
        False, f"Se bajó {archivo} pero no quedó anotado en la playlist (¿quitaste el tema mientras bajaba?).", None)


def test_spotify_busca_el_equivalente_como_el_buscador(server, client, dobles):
    sp = {"titulo": "Tema S", "artista": "Artista S", "fuente": "spotify",
          "url": "https://open.spotify.com/track/xyz"}
    pid, (s_id,) = _playlist(client, "Spotify", sp)
    assert _bajar(client, pid, s_id).json()["exito"] is True
    assert dobles["youtube"] == ["Tema S Artista S"]
    assert dobles["bajar"].llamadas == [("https://www.youtube.com/watch?v=equivalente", "Tema S - Artista S", "wav")]


def test_el_m3u8_incluye_lo_bajado(server, client, dobles):
    pid, (a, b) = _playlist(client, "Para Rekordbox", TEMA_A, TEMA_B)
    assert _bajar(client, pid, b).json()["exito"] is True
    r = client.post(f"/api/playlists/{pid}/export").json()
    assert (r["incluidos"], r["excluidos"]) == (1, 1)
    lineas = Path(r["ruta"]).read_text(encoding="utf-8").splitlines()
    ruta_b = (server.DOWNLOADS_DIR / f"{server._safe_name('Tema B - Artista B')}.wav").resolve()
    assert lineas == ["#EXTM3U", "#EXTINF:250,Artista B - Tema B", str(ruta_b)]


def test_con_redis_encola_el_item_y_el_job_lo_baja(server, client, dobles, monkeypatch):
    pid, (a,) = _playlist(client, "Cola", TEMA_A)
    encolados = []

    class Job:
        id = "job-123"
    monkeypatch.setattr(server.jobs, "queue_disponible", lambda: True)
    monkeypatch.setattr(server.jobs, "encolar", lambda f, *args, **k: (encolados.append((f, args)), Job())[1])
    r = _bajar(client, pid, a, {"formato": "flac", "url": "https://malo.example"})
    assert r.json() == {"encolado": True, "job_id": "job-123"}
    import tasks
    assert [(f.__name__, args) for f, args in encolados] == [("descargar_item_job", (pid, a, "flac"))]
    assert dobles["bajar"].llamadas == [], "con cola, el web no baja nada"
    # Lo que corre el worker: baja ESE item con el formato pedido.
    res = tasks.descargar_item_job(pid, a, "flac")
    assert (res["exito"], res["item"]["formato"]) == (True, "flac")
    assert dobles["bajar"].llamadas == [(TEMA_A["url"], "Tema A - Artista A", "flac")]
