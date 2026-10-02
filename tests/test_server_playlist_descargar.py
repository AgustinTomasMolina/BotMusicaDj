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
        # El contenido dice de qué url salió: así se ve si una descarga pisó el archivo de otra.
        (self.server.DOWNLOADS_DIR / archivo).write_bytes(f"audio de {url}".encode())
        return {"ok": True, "archivo": archivo,
                "calidad": self.server._calidad_desde_ydl({"acodec": "opus", "abr": 160, "ext": "webm"})}


@pytest.fixture
def dobles(server, monkeypatch):
    # downloads/ vacío en cada test: el server es uno solo para el módulo y un archivo de otro
    # test haría que este baje con nombre «(2)» (C2: no se pisa un archivo ajeno).
    for f in server.DOWNLOADS_DIR.glob("*"):
        if f.is_file():
            f.unlink()
    d = {"bajar": Descargador(server), "tags": [], "youtube": [], "medida": None,
         "resultados_yt": [{"titulo": "Artista S - Tema S (Official Audio)", "artista": "Artista S",
                            "duracion": 0, "url": "https://www.youtube.com/watch?v=equivalente"}]}
    monkeypatch.setattr(server, "_descargar_sync", lambda *a: d["bajar"](*a))

    def _no_directo(*a):
        raise AssertionError("la descarga directa no tenía que usarse")
    monkeypatch.setattr(server, "_descargar_directo", _no_directo)
    monkeypatch.setattr(server, "_taggear_descarga",
                        lambda archivo, titulo, artista, payload, cal: d["tags"].append((archivo, dict(payload))))

    def _youtube(consulta, n):
        d["youtube"].append(consulta)
        return d["resultados_yt"]
    monkeypatch.setattr(server.search_agent, "buscar_en_youtube", _youtube)
    monkeypatch.setattr(server.jobs, "queue_disponible", lambda: False)
    # ffprobe sobre el archivo bajado: el doble escribe bytes que no son audio. `medida` = lo
    # que "mide" (None = no se pudo medir, como ffprobe con un archivo roto).
    monkeypatch.setattr(server, "_duracion_archivo", lambda ruta: d["medida"])
    # Sin DNS: los hosts de los tests resuelven a una IP pública (una de los rangos de
    # documentación, 203.0.113.0/24, NO sirve: no es global y el chequeo la bloquea).
    monkeypatch.setattr(server, "_resolver_host", lambda host: ["93.184.215.14"])
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
    reservas = set()
    monkeypatch.setattr(server.jobs, "reservar", lambda c, ttl: not (c in reservas or reservas.add(c)))
    monkeypatch.setattr(server.jobs, "liberar", lambda c: reservas.discard(c))
    r = _bajar(client, pid, a, {"formato": "flac", "url": "https://malo.example"})
    assert r.json() == {"encolado": True, "job_id": "job-123"}
    import tasks
    assert [(f.__name__, args) for f, args in encolados] == [("descargar_item_job", (pid, a, "flac"))]
    assert dobles["bajar"].llamadas == [], "con cola, el web no baja nada"
    # Lo que corre el worker: baja ESE item con el formato pedido.
    res = tasks.descargar_item_job(pid, a, "flac")
    assert (res["exito"], res["item"]["formato"]) == (True, "flac")
    assert dobles["bajar"].llamadas == [(TEMA_A["url"], "Tema A - Artista A", "flac")]


# =========================================================================================
#  Ronda 2 (auditoría): CSRF, SSRF, equivalente de YouTube, duración medida, Go+, archivo
#  borrado, Redis, nombres que chocan, url con espacios, artista vacío.
# =========================================================================================
URL_BAJAR = "/api/playlists/{pid}/items/{iid}/descargar"


def test_csrf_solo_acepta_json_de_la_propia_app(server, client, dobles):
    """Un <form method=POST> o un fetch no-cors con text/plain desde otra página no disparan
    preflight: el navegador los manda igual. Antes el endpoint leía el cuerpo en try/except,
    caía a WAV y BAJABA. Ahora: JSON obligatorio (415) y nada de Origin ajeno (403)."""
    pid, (a,) = _playlist(client, "CSRF", TEMA_A)
    url = URL_BAJAR.format(pid=pid, iid=a)
    casos = {
        "form": client.post(url, data={"formato": "wav"}),
        "text/plain": client.post(url, content=b'{"formato": "wav"}', headers={"Content-Type": "text/plain"}),
        "sin cuerpo": client.post(url),
        "json roto": client.post(url, content=b"{no", headers={"Content-Type": "application/json"}),
        "json que no es objeto": client.post(url, json=["wav"]),
        "Origin ajeno": client.post(url, json={"formato": "wav"}, headers={"Origin": "https://malo.example"}),
        "Origin null": client.post(url, json={"formato": "wav"}, headers={"Origin": "null"}),
        "otro puerto": client.post(url, json={"formato": "wav"}, headers={"Origin": "http://testserver:3000"}),
        "cross-site": client.post(url, json={"formato": "wav"}, headers={"Sec-Fetch-Site": "cross-site"}),
    }
    assert {k: r.status_code for k, r in casos.items()} == {
        "form": 415, "text/plain": 415, "sin cuerpo": 415, "json roto": 400,
        "json que no es objeto": 400, "Origin ajeno": 403, "Origin null": 403, "otro puerto": 403,
        "cross-site": 403}
    assert casos["Origin ajeno"].json()["mensaje"] == "Pedido rechazado: no viene de MusiFlix."
    assert dobles["bajar"].llamadas == [], "un pedido rechazado igual disparó la descarga"
    assert _items(client, pid)[a]["descargado"] is False
    # La propia app: mismo origen (lo que manda el navegador desde la pantalla).
    r = client.post(url, json={"formato": "mp3"},
                    headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert (r.status_code, r.json()["exito"]) == (200, True)
    assert dobles["bajar"].llamadas == [(TEMA_A["url"], "Tema A - Artista A", "mp3")]


def test_ssrf_links_internos_y_de_otro_sitio_no_se_bajan(server, client, dobles, monkeypatch):
    """La url sale del item guardado, pero el item lo pudo guardar cualquiera (POST /items no
    valida): el server no puede pedir por él la red local ni un servidor que no es el del sitio."""
    temas = {
        "loopback": {"fuente": "youtube", "url": "http://127.0.0.1:8000/api/historial"},
        "localhost": {"fuente": "soundcloud", "url": "http://localhost/x"},
        "ipv6": {"fuente": "youtube", "url": "http://[::1]/x"},
        "metadata": {"fuente": "youtube", "url": "http://169.254.169.254/latest/meta-data"},
        "privada": {"fuente": "youtube", "url": "https://10.0.0.5/x"},
        "decimal": {"fuente": "youtube", "url": "http://2130706433/x"},
        "corta": {"fuente": "youtube", "url": "http://127.1/x"},
        "ligaudio ajeno": {"fuente": "ligaudio", "url": "https://malo.example/x.mp3"},
        "hitplayer parecido": {"fuente": "hitplayer", "url": "https://hotplayer.ru.malo.example/x.mp3"},
        "ligaudio real": {"fuente": "ligaudio", "url": "https://storage6.lightaudio.ru/abc/x.mp3"},
        "hitplayer real": {"fuente": "hitplayer", "url": "https://d7.hotplayer.ru/x.mp3"},
        "nombre que resuelve adentro": {"fuente": "youtube", "url": "https://interno.example/x"},
    }
    pid, ids = _playlist(client, "SSRF", *({"titulo": k, "artista": "X", **v} for k, v in temas.items()))
    monkeypatch.setattr(server, "_resolver_host",
                        lambda h: ["127.0.0.1"] if h == "interno.example" else ["93.184.215.14"])
    motivos = {it["titulo"]: it["motivo_no_bajable"] for it in _items(client, pid).values()}
    ajeno = ("El link guardado no es de {f} (se esperaba un servidor de {d}, es {h}): no lo bajo.")
    assert motivos == {
        "loopback": server.LINK_INTERNO, "localhost": server.LINK_INTERNO, "ipv6": server.LINK_INTERNO,
        "metadata": server.LINK_INTERNO, "privada": server.LINK_INTERNO, "decimal": server.LINK_INTERNO,
        "corta": server.LINK_INTERNO,
        "ligaudio ajeno": ajeno.format(f="ligaudio", d="ligaudio.ru o lightaudio.ru", h="malo.example"),
        "hitplayer parecido": ajeno.format(f="hitplayer", d="hitplayer.ru o hotplayer.ru", h="hotplayer.ru.malo.example"),
        "ligaudio real": None, "hitplayer real": None,
        # La pantalla no resuelve DNS por cada item: esto lo ve recién el POST.
        "nombre que resuelve adentro": None,
    }
    por_titulo = dict(zip(temas, ids, strict=True))
    for t in ("loopback", "metadata", "ligaudio ajeno", "nombre que resuelve adentro"):
        r = _bajar(client, pid, por_titulo[t])
        assert (t, r.status_code, r.json()["mensaje"]) == (t, 400, motivos[t] or server.LINK_INTERNO)
    assert dobles["bajar"].llamadas == [], "un link interno o ajeno llegó al descargador"


def test_spotify_no_baja_un_equivalente_que_no_es_el_tema(server, client, dobles):
    """Caso real: para un tema de 420 s se bajó un video de 605,7 s y quedó "descargado"."""
    sp = {"titulo": "Tema S", "artista": "Artista S", "fuente": "spotify",
          "url": "https://open.spotify.com/track/xyz", "duracion": 420}
    pid, (s_id,) = _playlist(client, "Equivalente", sp)
    otro = {"titulo": "Otro Tema - Otro Artista", "artista": "Otro Artista", "duracion": 421,
            "url": "https://www.youtube.com/watch?v=otro"}
    largo = {"titulo": "Artista S - Tema S", "artista": "Artista S", "duracion": 605.7,
             "url": "https://www.youtube.com/watch?v=largo"}
    dobles["resultados_yt"] = [otro, largo]
    d = _bajar(client, pid, s_id).json()
    assert (d["exito"], d["mensaje"]) == (False, "No encontré en YouTube el mismo tema: «Tema S» de "
                                                 "Artista S dura 420 s y lo más parecido que apareció dura 605.7 s.")
    assert dobles["bajar"].llamadas == [], "bajó un video que no era el tema"
    assert _items(client, pid)[s_id]["descargado"] is False
    dobles["resultados_yt"] = [otro]
    assert _bajar(client, pid, s_id).json()["mensaje"] == \
        "No encontré en YouTube el mismo tema («Tema S» de Artista S): ningún resultado tenía ese título y artista."
    # El que cuadra (mismo tema, 428 s: +8 s de intro entra en ±max(10 s, 10 %)) es el que se baja,
    # aunque no sea el primero.
    bueno = {"titulo": "Tema S", "artista": "Artista S - Topic", "duracion": 428,
             "url": "https://www.youtube.com/watch?v=bueno"}
    dobles["resultados_yt"] = [otro, largo, bueno]
    assert _bajar(client, pid, s_id).json()["exito"] is True
    assert dobles["bajar"].llamadas == [("https://www.youtube.com/watch?v=bueno", "Tema S - Artista S", "wav")]


def test_lo_bajado_que_no_dura_lo_del_tema_no_queda_descargado(server, client, dobles):
    """B2-bis: la duración del ARCHIVO (ffprobe) contra la del item. 30 s de un tema de 250 s
    es el preview: no se guarda como el tema."""
    pid, (b,) = _playlist(client, "Duracion", TEMA_B)
    antes = len(client.get("/api/historial").json()["descargas"])
    dobles["medida"] = 30.0
    d = _bajar(client, pid, b).json()
    assert (d["exito"], d["mensaje"]) == (False, "Lo que se bajó no era el tema (30.0 s vs 250 s): no lo guardé.")
    assert d["item"]["descargado"] is False and _items(client, pid)[b]["descargado"] is False
    archivo = server.DOWNLOADS_DIR / f"{server._safe_name('Tema B - Artista B')}.wav"
    assert not archivo.exists(), "el archivo que no era el tema quedó en downloads/"
    assert len(client.get("/api/historial").json()["descargas"]) == antes, "fue al historial igual"
    assert dobles["tags"] == [], "se tagueó un archivo que no era el tema"
    # 255,3 s: cuadra (±max(10 s, 10 %)) → bajado, y la duración guardada no se pisa.
    dobles["medida"] = 255.3
    assert _bajar(client, pid, b).json()["exito"] is True
    assert _items(client, pid)[b]["duracion"] == 250


def test_duracion_archivo_mide_con_ffprobe_un_wav_sintetico(server, tmp_path):
    import math
    import shutil
    import struct
    import wave

    from analizar_calidad import FFPROBE
    if not (Path(FFPROBE).is_file() or shutil.which(FFPROBE)):
        pytest.skip("sin ffprobe")
    ruta = tmp_path / "seno.wav"
    sr, seg = 44100, 2.5
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / sr)))
                               for i in range(int(sr * seg))))
    assert server._duracion_archivo(ruta) == pytest.approx(2.5, abs=0.01)
    (tmp_path / "roto.wav").write_bytes(b"no es audio")
    assert server._duracion_archivo(tmp_path / "roto.wav") is None


def test_hitplayer_sin_duracion_queda_con_la_medida(server, client, dobles, monkeypatch):
    """C6: hitplayer no da duración; después de bajar, el item guarda la del archivo (sirve
    para el #EXTINF del .m3u8)."""
    hp = {"titulo": "Tema H", "artista": "Artista H", "fuente": "hitplayer",
          "url": "https://d8.hotplayer.ru/abc/tema-h.mp3"}
    pid, (h,) = _playlist(client, "Hitplayer", hp)
    directos = []

    def _directo(url, nombre):
        directos.append((url, nombre))
        (server.DOWNLOADS_DIR / f"{nombre}.mp3").write_bytes(b"mp3")
        return {"ok": True, "archivo": f"{nombre}.mp3"}
    monkeypatch.setattr(server, "_descargar_directo", _directo)
    monkeypatch.setattr(server, "_calidad_espectral", lambda ruta: {"badge": "🟢", "calidad": "MP3 320k",
                                                                     "metodo": "doble", "cutoff_hz": 20000})
    dobles["medida"] = 187.4
    assert _bajar(client, pid, h).json()["exito"] is True
    assert directos == [(hp["url"], "Tema H - Artista H")]
    assert _items(client, pid)[h]["duracion"] == 187
    lineas = Path(client.post(f"/api/playlists/{pid}/export").json()["ruta"]).read_text(encoding="utf-8").splitlines()
    assert lineas[1] == "#EXTINF:187,Artista H - Tema H"


def test_go_plus_de_soundcloud_no_se_ofrece_para_bajar(server, client, dobles):
    """B3: el `solo_preview` de la Station se guarda con el item y da el motivo."""
    gp = {**TEMA_B, "titulo": "Tema Go", "solo_preview": True}
    pid, (g, b) = _playlist(client, "Go+", gp, TEMA_B)
    items = _items(client, pid)
    assert (items[g]["solo_preview"], items[g]["motivo_no_bajable"]) == (True, server.SOLO_PREVIEW)
    assert (items[b]["solo_preview"], items[b]["motivo_no_bajable"]) == (False, None)
    r = _bajar(client, pid, g)
    assert (r.status_code, r.json()["mensaje"]) == (400, server.SOLO_PREVIEW)
    assert dobles["bajar"].llamadas == []


def test_migracion_agrega_solo_preview_a_una_base_vieja(server, tmp_path):
    """create_all no agrega columnas a una tabla que ya existe: la base del dueño tiene
    mi_playlist_items sin solo_preview y, sin migración, todo SELECT del item fallaría."""
    import sqlite3

    from sqlalchemy import create_engine
    vieja = tmp_path / "vieja.db"
    with sqlite3.connect(vieja) as con:
        con.execute("CREATE TABLE mi_playlist_items (id INTEGER PRIMARY KEY, playlist_id INTEGER, titulo TEXT)")
        con.execute("INSERT INTO mi_playlist_items (id, playlist_id, titulo) VALUES (1, 1, 'Viejo')")
    eng = create_engine(f"sqlite:///{vieja}")
    assert server.db._migrar(eng) == ["mi_playlist_items.solo_preview"]
    assert server.db._migrar(eng) == [], "la migración no es idempotente"
    with sqlite3.connect(vieja) as con:
        assert con.execute("SELECT titulo, solo_preview FROM mi_playlist_items").fetchall() == [("Viejo", None)]
    eng.dispose()


def test_archivo_borrado_ya_no_cuenta_como_descargado(server, client, dobles):
    """B4: con la ruta guardada pero el archivo borrado, el item decía "descargado", el POST
    daba 409 y el .m3u8 no lo contaba ni como incluido ni como excluido."""
    pid, (a, b) = _playlist(client, "Borrado", TEMA_A, TEMA_B)
    assert _bajar(client, pid, a).json()["exito"] is True
    assert _bajar(client, pid, b).json()["exito"] is True
    (server.DOWNLOADS_DIR / f"{server._safe_name('Tema A - Artista A')}.wav").unlink()
    data = client.get(f"/api/playlists/{pid}").json()["data"]
    items = {it["id"]: it for it in data["items"]}
    assert (items[a]["descargado"], items[a]["motivo_no_bajable"], items[b]["descargado"]) == (False, None, True)
    assert data["metrics"]["descargados"] == 1
    exp = client.post(f"/api/playlists/{pid}/export").json()
    assert (exp["incluidos"], exp["excluidos"]) == (1, 1), "la cuenta del .m3u8 no cierra"
    # Se puede bajar de nuevo; un tema bajado CON archivo sigue en 409.
    assert _bajar(client, pid, a).json()["exito"] is True
    assert _items(client, pid)[a]["descargado"] is True
    assert _bajar(client, pid, b).status_code == 409


def test_ffmpeg_solo_se_culpa_si_falta(server, client, dobles, monkeypatch):
    """B6: cualquier error que nombrara ffmpeg se mostraba como "Falta ffmpeg"."""
    pid, (a,) = _playlist(client, "ffmpeg", TEMA_A)
    error = "ERROR: Postprocessing: audio conversion failed: ffmpeg exited with code 1"
    dobles["bajar"] = Descargador(server, falla=Exception(error))
    monkeypatch.setattr(server, "_tiene_ffmpeg", lambda: True)
    assert _bajar(client, pid, a).json()["mensaje"] == error
    monkeypatch.setattr(server, "_tiene_ffmpeg", lambda: False)
    assert _bajar(client, pid, a).json()["mensaje"] == \
        "Falta ffmpeg para convertir el audio. Instalá ffmpeg (ver iniciar_web.bat)."


def test_con_redis_no_se_encola_dos_veces_ni_se_baja_lo_ya_bajado(server, client, dobles, monkeypatch):
    """C1: el registro en memoria del web no ve los jobs del worker. La reserva va a Redis y la
    suelta el job; el job vuelve a mirar si el item ya está bajado."""
    pid, (a, b) = _playlist(client, "Cola doble", TEMA_A, TEMA_B)
    encolados, reservas = [], {}

    class Job:
        id = "job-1"
    monkeypatch.setattr(server.jobs, "queue_disponible", lambda: True)
    monkeypatch.setattr(server.jobs, "encolar", lambda f, *args, **k: (encolados.append(args), Job())[1])
    monkeypatch.setattr(server.jobs, "reservar", lambda c, ttl: c not in reservas and not reservas.update({c: ttl}))
    monkeypatch.setattr(server.jobs, "liberar", lambda c: reservas.pop(c, None))
    assert _bajar(client, pid, a).json() == {"encolado": True, "job_id": "job-1"}
    r = _bajar(client, pid, a)
    assert (r.status_code, r.json()["mensaje"]) == (409, "Este tema ya se está bajando.")
    assert encolados == [(pid, a, "wav")], "el mismo item se encoló dos veces"
    assert reservas == {server._clave_item(a): server.RESERVA_ITEM_S}
    import tasks
    assert tasks.descargar_item_job(pid, a, "wav")["exito"] is True
    assert reservas == {}, "el job no soltó la reserva"
    # Ya bajado (con archivo): el endpoint dice 409 y un job viejo que corre igual no baja nada.
    assert _bajar(client, pid, a).status_code == 409
    llamadas = len(dobles["bajar"].llamadas)
    assert tasks.descargar_item_job(pid, a, "wav")["mensaje"] == "Este tema ya está descargado."
    assert len(dobles["bajar"].llamadas) == llamadas
    # Si no se pudo encolar, la reserva se suelta (si no, el tema quedaría trabado ~16 min).
    monkeypatch.setattr(server.jobs, "encolar", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("redis caído")))
    assert _bajar(client, pid, b).status_code == 503
    assert reservas == {}


def test_jobs_reservar_usa_set_nx_con_vencimiento(server, monkeypatch):
    class Redis:
        def __init__(self):
            self.claves, self.llamadas = {}, []

        def set(self, k, v, nx=False, ex=None):
            self.llamadas.append((k, nx, ex))
            if nx and k in self.claves:
                return None
            self.claves[k] = v
            return True

        def delete(self, k):
            self.claves.pop(k, None)
    r = Redis()
    monkeypatch.setattr(server.jobs, "_redis", r)
    assert [server.jobs.reservar("k", 960), server.jobs.reservar("k", 960)] == [True, False]
    server.jobs.liberar("k")
    assert server.jobs.reservar("k", 960) is True
    assert r.llamadas == [("k", True, 960)] * 3


def test_dos_items_con_el_mismo_nombre_no_se_pisan(server, client, dobles):
    """C2: el mismo tema en dos playlists bajaba al MISMO archivo; el segundo pisaba al primero
    y los dos .m3u8 apuntaban a la misma ruta."""
    otro_link = {**TEMA_A, "url": "https://www.youtube.com/watch?v=otro-upload"}
    p1, (a1,) = _playlist(client, "Uno", TEMA_A)
    p2, (a2,) = _playlist(client, "Dos", otro_link)
    assert _bajar(client, p1, a1).json()["exito"] is True
    assert _bajar(client, p2, a2).json()["exito"] is True
    assert [ll[1] for ll in dobles["bajar"].llamadas] == ["Tema A - Artista A", "Tema A - Artista A (2)"]
    r1 = Path(client.post(f"/api/playlists/{p1}/export").json()["ruta"]).read_text(encoding="utf-8").splitlines()[2]
    r2 = Path(client.post(f"/api/playlists/{p2}/export").json()["ruta"]).read_text(encoding="utf-8").splitlines()[2]
    assert (Path(r1).read_bytes(), Path(r2).read_bytes()) == \
        (f"audio de {TEMA_A['url']}".encode(), f"audio de {otro_link['url']}".encode()), "una descarga pisó a la otra"


def test_el_buscador_sigue_bajando_con_el_nombre_de_siempre(server, client, dobles):
    """El nombre único es solo para la playlist: /api/descargar no cambia (re-bajar desde el
    buscador reemplaza el archivo como siempre)."""
    for _ in range(2):
        assert client.post("/api/descargar", json={**TEMA_A, "formato": "wav"}).json()["exito"] is True
    assert [ll[1] for ll in dobles["bajar"].llamadas] == ["Tema A - Artista A"] * 2


def test_url_con_espacios_y_artista_vacio(server, client, dobles):
    """C3: el link guardado con espacios se baja limpio. C8: sin artista no queda «Test -»."""
    pid, (a, t) = _playlist(client, "Bordes", {**TEMA_A, "url": "  https://www.youtube.com/watch?v=aaa \n"},
                            {"titulo": "Test", "artista": "", "fuente": "youtube",
                             "url": "https://www.youtube.com/watch?v=ttt"})
    assert _bajar(client, pid, a).json()["exito"] is True
    d = _bajar(client, pid, t).json()
    assert (d["exito"], d["archivo"]) == (True, "Test.wav")
    assert dobles["bajar"].llamadas == [("https://www.youtube.com/watch?v=aaa", "Tema A - Artista A", "wav"),
                                        ("https://www.youtube.com/watch?v=ttt", "Test", "wav")]


def test_item_quitado_responde_quitado(server, client, dobles):
    """C5: la pantalla necesita saber que la fila ya no existe para no contarlo como un error
    "con el motivo en el tema"."""
    pid, (a, b) = _playlist(client, "Quitado", TEMA_A, TEMA_B)
    client.delete(f"/api/playlists/{pid}/items/{a}")
    r = _bajar(client, pid, a)
    assert (r.status_code, r.json()["quitado"]) == (404, True)
    import tasks
    assert tasks.descargar_item_job(pid, a, "wav")["quitado"] is True
    assert _bajar(client, 99999, b).json()["quitado"] is False, "una playlist inexistente no es un tema quitado"
