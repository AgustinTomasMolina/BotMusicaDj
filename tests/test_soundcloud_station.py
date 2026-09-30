"""Tests de la Station de SoundCloud (f34): soundcloud_station.py y GET /api/station.

Sin red. `tests/fixtures/soundcloud_api_v2.json` tiene respuestas REALES de api-v2 grabadas
el 2026-09-30 (búsquedas y las Stations de ELVITO e IMMINENT), recortadas y sin credenciales.
`FakeHttp` hace de `requests.get`: contesta con esas grabaciones según la ruta y anota cada
pedido. Lo esperado sale de las grabaciones (el orden de SoundCloud, sus ids, su policy), no
del código bajo prueba. El orden de la Station de ELVITO se comparó a mano contra la web de
SoundCloud cuando se grabó (SPÆCE - B WITH U, NARCX - Take that, …).
"""
import importlib
import json
import sys
from pathlib import Path

import pytest
import soundcloud_station as sc
import track_identity as ti

FX_PATH = Path(__file__).parent / "fixtures" / "soundcloud_api_v2.json"
FX = json.loads(FX_PATH.read_text(encoding="utf-8"))
RESPUESTA_E2E = Path(__file__).parent / "fixtures" / "station_respuesta.json"
ELVITO, IMMINENT = "2169048723", "2092052082"
CID = "CID_SECRETO_0123456789abcdefABCD"


class Resp:
    def __init__(self, status=200, body=None, no_json=False):
        self.status_code = status
        self._body = body
        self._no_json = no_json

    def json(self):
        if self._no_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


class FakeHttp:
    """`requests.get` con las grabaciones. `guion` (opcional): respuestas que se consumen
    antes de las grabaciones (un 401, un 500, una excepción)."""

    def __init__(self, guion=None):
        self.guion = list(guion or [])
        self.pedidos = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.pedidos.append((url, dict(params or {}), timeout))
        if self.guion:
            r = self.guion.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        ruta = url.removeprefix(sc.API)
        if ruta == "/search/tracks":
            busquedas = {k.lower(): v for k, v in FX["search"].items()}
            return Resp(200, {"collection": busquedas[params["q"].lower()]})
        pre, suf = "/stations/soundcloud:track-stations:", "/tracks"
        if ruta.startswith(pre) and ruta.endswith(suf):
            tid = ruta[len(pre):-len(suf)]
            return Resp(200, {"collection": FX["station"].get(tid, [])})
        return Resp(404, {})

    def rutas(self):
        return [u.removeprefix(sc.API) for u, _, _ in self.pedidos]


class Proveedor:
    """client_id: anota cada pedido (refresh o no) y devuelve el siguiente de la lista."""

    def __init__(self, *cids):
        self.cids = list(cids) or [CID]
        self.llamadas = []

    def __call__(self, refresh):
        self.llamadas.append(refresh)
        return self.cids[min(len(self.llamadas) - 1, len(self.cids) - 1)]


@pytest.fixture(autouse=True)
def _cache_limpia():
    sc.clear_cache()
    yield
    sc.clear_cache()


def api(http=None, prov=None):
    return sc.SoundCloudApi(http_get=http or FakeHttp(), client_id_provider=prov or Proveedor())


def ids_grabados(tid):
    return [str(t["id"]) for t in FX["station"][tid]]


# --- mapeo de la Station --------------------------------------------------------------------

def test_station_de_elvito_conserva_el_orden_y_saca_la_semilla():
    http = FakeHttp()
    r = sc.build_station("soundcloud", ELVITO, "GIB MIR BOUNCE!", "ELVITO", None, api(http))
    grabado = ids_grabados(ELVITO)
    assert grabado[0] == ELVITO, "la grabación trae la semilla primero (así contesta SoundCloud)"
    assert [i["video_id"] for i in r["items"]] == grabado[1:], "el orden de SoundCloud no se toca"
    assert r["total"] == 49 and ELVITO not in [i["video_id"] for i in r["items"]]
    # Lo que muestra la web de SoundCloud en la Station de ELVITO, en ese orden.
    assert [(i["artista"], i["titulo"]) for i in r["items"][:2]] == [("SPÆCE", "B WITH U"), ("NARCX", "Take that")]
    assert http.rutas() == [f"/stations/soundcloud:track-stations:{ELVITO}/tracks"]
    assert http.pedidos[0][1]["limit"] == 50


def test_station_de_imminent_es_la_grabada_sin_la_semilla():
    r = sc.build_station("soundcloud", IMMINENT, "From The Top", "RICOCHET", None, api())
    assert [i["video_id"] for i in r["items"]] == ids_grabados(IMMINENT)[1:]
    assert (r["items"][0]["artista"], r["items"][0]["titulo"]) == ("ADES", "QUENTE")


def test_mapeo_de_un_tema_completo_con_los_datos_de_la_grabacion():
    t = FX["station"][ELVITO][1]                       # SPÆCE - B WITH U
    assert sc.map_track(t) == {
        "titulo": "B WITH U", "artista": "SPÆCE", "duracion": round(t["full_duration"] / 1000, 1),
        "url": t["permalink_url"], "fuente": "soundcloud", "video_id": "2134484646",
        "thumbnail": t["artwork_url"], "permalink": "https://soundcloud.com/spaecesound/b-with-u",
        "reproducible": True, "solo_preview": False,
    }


def _de_busqueda(q, tid):
    return next(t for t in FX["search"][q] if t["id"] == tid)


def test_un_tema_go_plus_se_marca_como_preview_con_la_duracion_del_tema_entero():
    # Daft Punk «One More Time» 2366118086: policy SNIP, duration 30000, full_duration 320 s.
    snip = _de_busqueda("Daft Punk One More Time", 2366118086)
    assert snip["policy"] == "SNIP" and snip["duration"] == 30000
    m = sc.map_track(snip)
    assert m["solo_preview"] is True, "un Go+ suena 30 s: no puede mostrarse como tema completo"
    assert m["duracion"] == round(snip["full_duration"] / 1000, 1) != 30.0
    completo = sc.map_track(_de_busqueda("Daft Punk One More Time", 199428706))
    assert completo["solo_preview"] is False


def test_un_tema_solo_con_hls_cifrado_no_es_reproducible_en_la_barra():
    # «Cheek to Cheek» 326709745: cbc/ctr-encrypted-hls y hls, sin progressive.
    t = _de_busqueda("IMMINENT From The Top", 326709745)
    assert "progressive" not in {x["format"]["protocol"] for x in t["media"]["transcodings"]}
    assert sc.map_track(t)["reproducible"] is False


@pytest.mark.parametrize("raro", [
    None, "x", {"kind": "playlist", "id": 1, "title": "t"}, {"id": "123", "title": "t"},
    {"id": True, "title": "t"}, {"id": 0, "title": "t"}, {"id": 5, "title": "  "}, {"id": 5},
])
def test_un_tema_con_forma_rara_se_descarta(raro):
    assert sc.map_track(raro) is None


def test_una_url_que_no_es_de_soundcloud_no_pasa_como_permalink_ni_caratula():
    t = dict(FX["station"][ELVITO][1], permalink_url="https://evil.example/x/y",
             artwork_url="javascript:alert(1)", user={"username": "SPÆCE", "avatar_url": "http://i1.sndcdn.com/a.jpg"})
    m = sc.map_track(t)
    assert (m["permalink"], m["thumbnail"], m["url"]) == (None, None, "https://api.soundcloud.com/tracks/2134484646")


# --- la semilla ------------------------------------------------------------------------------

def test_semilla_de_soundcloud_va_por_su_id_sin_buscar():
    http = FakeHttp()
    r = sc.build_station("soundcloud", IMMINENT, "IMMINENT - From The Top", "RICOCHET", 247.0, api(http))
    assert not any(ruta == "/search/tracks" for ruta in http.rutas())
    assert r["semilla"] == {"titulo": "IMMINENT - From The Top", "artista": "RICOCHET", "video_id": IMMINENT,
                            "permalink": "https://soundcloud.com/ricochetmovement/imminent-from-the-top",
                            "thumbnail": FX["station"][IMMINENT][0]["artwork_url"], "evidencia": "id"}


def test_desde_youtube_con_identidad_valida_usa_el_tema_de_soundcloud():
    # El video de YouTube «From The Top» del canal «IMMINENT - Topic» (247 s): en SoundCloud
    # es el upload de RICOCHET «IMMINENT - From The Top» (247,2 s).
    http = FakeHttp()
    r = sc.build_station("youtube", "aaaaaaaaaaa", "From The Top", "IMMINENT - Topic", 247.0, api(http))
    assert http.rutas() == ["/search/tracks", f"/stations/soundcloud:track-stations:{IMMINENT}/tracks"]
    assert http.pedidos[0][1]["q"].lower() == "imminent from the top"
    assert r["exito"] is True and r["semilla"]["video_id"] == IMMINENT
    assert r["semilla"]["evidencia"] == "texto+duracion"
    assert [i["video_id"] for i in r["items"]] == ids_grabados(IMMINENT)[1:]


@pytest.mark.parametrize("dur", [190.0, None])
def test_desde_youtube_si_soundcloud_devuelve_otra_cancion_no_hay_station(dur):
    # «IMMINENT - Ascend»: SoundCloud devuelve «FINIVOID — ASCENT» (de la cuenta 𝗜𝗠𝗠𝗜𝗡𝗘𝗡𝗧) e
    # «Imminent Ascent» de Dominic Schumerth. Ninguno es el tema: nunca otra canción.
    http = FakeHttp()
    r = sc.build_station("youtube", "aaaaaaaaaaa", "IMMINENT - Ascend", "IMMINENT", dur, api(http))
    assert [t["title"] for t in FX["search"]["IMMINENT Ascend"]] == ["FINIVOID — ASCENT (ft. moonkit)", "Imminent Ascent"]
    assert r == {"exito": False, "motivo": "no_esta_en_soundcloud", "mensaje": "No encontré este tema en SoundCloud."}
    assert http.rutas() == ["/search/tracks"], "sin semilla no se pide ninguna Station"


def test_desde_youtube_entre_dos_lanzamientos_elige_el_de_duracion_cercana():
    # Dos «One More Time» de Daft Punk: 2366118086 (Go+, 320 s) y 199428706 (323 s).
    http = FakeHttp()
    sc.build_station("youtube", "aaaaaaaaaaa", "Daft Punk - One More Time", "Daft Punk", 323.0, api(http))
    assert http.rutas()[-1] == "/stations/soundcloud:track-stations:199428706/tracks"


# --- robustez ----------------------------------------------------------------------------------

def test_un_401_renueva_el_client_id_una_vez_y_reintenta():
    http = FakeHttp([Resp(401, {})])
    prov = Proveedor("VIEJO_client_id", "NUEVO_client_id")
    r = sc.build_station("soundcloud", ELVITO, "", "", None, api(http, prov))
    assert r["exito"] is True and r["total"] == 49
    assert prov.llamadas == [False, True]
    assert [p["client_id"] for _, p, _ in http.pedidos] == ["VIEJO_client_id", "NUEVO_client_id"]


def test_dos_403_seguidos_no_insisten_y_dicen_que_soundcloud_no_contesto():
    http = FakeHttp([Resp(403, {}), Resp(403, {}), Resp(200, {"collection": FX["station"][ELVITO]})])
    prov = Proveedor()
    r = sc.build_station("soundcloud", ELVITO, "", "", None, api(http, prov))
    assert r == {"exito": False, "motivo": "soundcloud_no_disponible", "mensaje": sc.MESSAGES["soundcloud_no_disponible"]}
    assert len(http.pedidos) == 2 and prov.llamadas == [False, True]


def test_el_client_id_se_reusa_entre_pedidos():
    prov = Proveedor()
    a = api(FakeHttp(), prov)
    sc.build_station("soundcloud", ELVITO, "", "", None, a)
    sc.build_station("soundcloud", IMMINENT, "", "", None, a)
    assert prov.llamadas == [False], "sin 401 no se vuelve a pedir el client_id"


def test_sin_client_id_no_sale_a_la_red():
    http = FakeHttp()
    r = sc.build_station("soundcloud", ELVITO, "", "", None, api(http, Proveedor(None)))
    assert r["motivo"] == "soundcloud_no_disponible" and http.pedidos == []


@pytest.mark.parametrize("guion, motivo", [
    ([__import__("requests").exceptions.ReadTimeout(f"HTTPSConnectionPool: /x?client_id={CID} (read timeout=10)")],
     "soundcloud_no_disponible"),
    ([__import__("requests").exceptions.ConnectionError("conexión rechazada")], "soundcloud_no_disponible"),
    ([Resp(500, {})], "soundcloud_no_disponible"),
    ([Resp(429, {})], "soundcloud_no_disponible"),
    ([Resp(200, None, no_json=True)], "respuesta_inesperada"),
    ([Resp(200, {"tracks": []})], "respuesta_inesperada"),
    ([Resp(200, {"collection": {"id": 1}})], "respuesta_inesperada"),
    ([Resp(200, ["no", "es", "un", "objeto"])], "respuesta_inesperada"),
    ([Resp(200, {"collection": [{"x": 1}, "basura", None]})], "respuesta_inesperada"),
    ([Resp(200, {"collection": []})], "station_vacia"),
    ([Resp(200, {"collection": [FX["station"][ELVITO][0]]})], "station_vacia"),   # solo la semilla
])
def test_fallas_de_soundcloud_dan_exito_false_con_motivo_y_sin_filtrar_el_client_id(guion, motivo, caplog):
    caplog.set_level("INFO")
    r = sc.build_station("soundcloud", ELVITO, "GIB MIR BOUNCE!", "ELVITO", None, api(FakeHttp(guion), Proveedor(CID)))
    assert r == {"exito": False, "motivo": motivo, "mensaje": sc.MESSAGES[motivo]}
    assert CID not in caplog.text


def test_la_falla_de_red_se_loguea_por_su_tipo():
    import requests
    http = FakeHttp([requests.exceptions.ReadTimeout(f"/x?client_id={CID}")])
    try:
        api(http).get_json("/search/tracks", {"q": "x"})
    except sc.SoundCloudError as e:
        assert (e.reason, e.detail) == ("soundcloud_no_disponible", "ReadTimeout")
        assert e.__cause__ is None and e.__suppress_context__, "la excepción original (con la URL) no viaja"
    else:
        pytest.fail("un timeout tiene que ser SoundCloudError")


def test_los_pedidos_llevan_timeout():
    http = FakeHttp()
    sc.build_station("soundcloud", ELVITO, "", "", None, api(http))
    assert http.pedidos[0][2] == sc.TIMEOUT and all(x > 0 for x in sc.TIMEOUT)


# --- caché ---------------------------------------------------------------------------------------

def test_la_station_se_cachea_por_id():
    http = FakeHttp()
    a = api(http)
    r1 = sc.build_station("soundcloud", ELVITO, "", "", None, a)
    r2 = sc.build_station("soundcloud", ELVITO, "", "", None, a)
    assert r1 == r2 and len(http.pedidos) == 1


def test_la_cache_vence_a_los_10_minutos(monkeypatch):
    reloj = [1000.0]
    monkeypatch.setattr(sc.time, "monotonic", lambda: reloj[0])
    http = FakeHttp()
    a = api(http)
    sc.build_station("soundcloud", ELVITO, "", "", None, a)
    reloj[0] += sc.CACHE_TTL_S - 1
    sc.build_station("soundcloud", ELVITO, "", "", None, a)
    assert len(http.pedidos) == 1
    reloj[0] += 2
    sc.build_station("soundcloud", ELVITO, "", "", None, a)
    assert len(http.pedidos) == 2 and sc.CACHE_TTL_S == 600


@pytest.mark.parametrize("falla", [Resp(500, {}), Resp(200, {"collection": []}), Resp(200, None, no_json=True)])
def test_la_cache_no_guarda_fallos(falla):
    http = FakeHttp([falla])
    a = api(http)
    assert sc.build_station("soundcloud", ELVITO, "", "", None, a)["exito"] is False
    r = sc.build_station("soundcloud", ELVITO, "", "", None, a)
    assert r["exito"] is True and r["total"] == 49 and len(http.pedidos) == 2


# --- client_id compartido con track_identity ----------------------------------------------------

def test_renovar_sin_track_borra_el_cacheado_y_lo_saca_de_nuevo(monkeypatch):
    import yt_dlp
    hechos, cache = [], {"client_id": "VIEJO"}

    class Cache:
        def load(self, sec, key):
            return cache.get(key)

        def store(self, sec, key, v):
            hechos.append(("store", key, v))
            cache[key] = v

    class IE:
        def initialize(self):
            hechos.append(("initialize",))
            cache["client_id"] = cache["client_id"] or "NUEVO"

    class YDL:
        def __init__(self, *a, **k):
            self.cache = Cache()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_info_extractor(self, nombre):
            hechos.append(("ie", nombre))
            return IE()

        def extract_info(self, url, **k):
            hechos.append(("extract", url))

    monkeypatch.setattr(yt_dlp, "YoutubeDL", YDL)
    assert ti.soundcloud_client_id() == "VIEJO" and hechos == []
    assert ti.soundcloud_client_id(refresh=True) == "NUEVO"
    assert hechos == [("store", "client_id", None), ("ie", "Soundcloud"), ("initialize",)]


# --- endpoint ------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def server(tmp_path_factory):
    datos = tmp_path_factory.mktemp("server-datos")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MUSIFLIX_DATA_DIR", str(datos))
        mp.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
        mp.setenv("MUSIFLIX_SIN_CALENTAR", "1")
        mp.delenv("REDIS_URL", raising=False)
        mp.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        for mod in ("server", "jobs", "db"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
    return srv


@pytest.fixture
def client(server, monkeypatch):
    from fastapi.testclient import TestClient
    http = FakeHttp()
    monkeypatch.setattr(sc, "_default_api", api(http))
    c = TestClient(server.app)
    c.http = http
    return c


def test_endpoint_completo_devuelve_la_station_que_usa_el_e2e(client):
    r = client.get("/api/station", params={"fuente": "SoundCloud", "fuente_id": ELVITO,
                                           "titulo": "ELVITO x W CHOPPA - GIB MIR BOUNCE! // FREE DOWNLOAD",
                                           "artista": "ELVITO", "duracion": "291"})
    assert r.status_code == 200
    esperado = json.loads(RESPUESTA_E2E.read_text(encoding="utf-8"))
    assert r.json() == esperado, "el front (y el E2E) dependen de esta forma exacta"
    assert esperado["origen"] == "soundcloud_station" and esperado["total"] == len(esperado["items"]) == 49
    assert [i["video_id"] for i in esperado["items"]] == ids_grabados(ELVITO)[1:]


def test_endpoint_desde_youtube_sin_el_tema_en_soundcloud(client):
    r = client.get("/api/station", params={"fuente": "youtube", "fuente_id": "aaaaaaaaaaa",
                                           "titulo": "IMMINENT - Ascend", "artista": "IMMINENT", "duracion": "190"})
    assert (r.status_code, r.json()) == (200, {"exito": False, "motivo": "no_esta_en_soundcloud",
                                               "mensaje": "No encontré este tema en SoundCloud."})


def test_endpoint_con_soundcloud_caido_no_es_un_500(client):
    client.http.guion = [Resp(502, {})]
    r = client.get("/api/station", params={"fuente": "soundcloud", "fuente_id": IMMINENT})
    assert (r.status_code, r.json()["motivo"]) == (200, "soundcloud_no_disponible")


def test_endpoint_con_un_bug_adentro_no_es_un_500(client, monkeypatch):
    monkeypatch.setattr(sc, "station_for", lambda *a: 1 / 0)
    r = client.get("/api/station", params={"fuente": "soundcloud", "fuente_id": IMMINENT})
    assert (r.status_code, r.json()["motivo"]) == (200, "respuesta_inesperada")


@pytest.mark.parametrize("params", [
    {"fuente": "soundcloud", "fuente_id": ""},
    {"fuente": "soundcloud", "fuente_id": "abc"},
    {"fuente": "soundcloud", "fuente_id": "0"},
    {"fuente": "soundcloud", "fuente_id": "1" * 21},
    {"fuente": "soundcloud", "fuente_id": "١٢٣"},
    {"fuente": "soundcloud", "fuente_id": "123/../456"},
    {"fuente": "soundcloud", "fuente_id": "123&client_id=x"},
    {"fuente": "ftp", "fuente_id": "123", "titulo": "x"},
    {"fuente": "", "titulo": "x"},
    {"fuente": "youtube", "fuente_id": "aaaaaaaaaaa", "titulo": ""},
    {"fuente": "youtube", "titulo": "x" * 301},
    {"fuente": "youtube", "titulo": "x", "duracion": "9" * 40},
])
def test_endpoint_con_pedido_invalido_da_400_sin_salir_a_la_red(client, params):
    r = client.get("/api/station", params=params)
    assert r.status_code == 400 and r.json()["exito"] is False and r.json()["motivo"] == "pedido_invalido"
    assert client.http.pedidos == []


@pytest.mark.parametrize("dur, llega", [("inf", None), ("nan", None), ("-1", None), ("abc", None), ("247", 247.0)])
def test_endpoint_limpia_la_duracion(client, monkeypatch, dur, llega):
    recibido = []
    monkeypatch.setattr(sc, "build_station", lambda *a, **k: recibido.append(a) or sc.failure(sc.EMPTY_STATION))
    client.get("/api/station", params={"fuente": "youtube", "titulo": "From The Top", "artista": "IMMINENT - Topic",
                                       "duracion": dur})
    assert recibido == [("youtube", "", "From The Top", "IMMINENT - Topic", llega)]
