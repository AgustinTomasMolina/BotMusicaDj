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
    antes de las grabaciones (un 401, un 500, una excepción). `tracks` (f43, opcional): más
    cuerpos de GET /tracks/{id} además de los grabados; un id sin cuerpo contesta 404.
    `pool` (opcional): la colección que contesta CUALQUIER búsqueda (para medir la regla
    sobre un pool grabado con otra consulta)."""

    def __init__(self, guion=None, tracks=None, pool=None):
        self.guion = list(guion or [])
        self.pedidos = []
        self.tracks = {**FX["tracks"], **(tracks or {})}
        self.pool = pool

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.pedidos.append((url, dict(params or {}), timeout))
        if self.guion:
            r = self.guion.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        ruta = url.removeprefix(sc.API)
        if ruta == "/search/tracks":
            if self.pool is not None:
                return Resp(200, {"collection": self.pool})
            busquedas = {k.lower(): v for k, v in FX["search"].items()}
            return Resp(200, {"collection": busquedas[params["q"].lower()]})
        if ruta.startswith("/tracks/"):
            body = self.tracks.get(ruta.removeprefix("/tracks/"))
            return Resp(200, body) if body is not None else Resp(404, {})
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


def test_go_plus_alcanza_con_una_de_las_dos_marcas():
    # SoundCloud marca un Go+ con policy SNIP Y con transcodings `snipped`; alcanza con una
    # (un cambio de la API puede sacar cualquiera de las dos). Los dos casos salen de la
    # grabación del Go+ de Daft Punk, sacándole una marca por vez.
    snip = _de_busqueda("Daft Punk One More Time", 2366118086)
    solo_policy = dict(snip, media={"transcodings": [dict(x, snipped=False) for x in snip["media"]["transcodings"]]})
    solo_snipped = dict(snip, policy="ALLOW")
    assert sc.map_track(solo_policy)["solo_preview"] is True, "policy SNIP sin `snipped` sigue siendo un preview"
    assert sc.map_track(solo_snipped)["solo_preview"] is True, "`snipped` con policy ALLOW sigue siendo un preview"


def test_policy_block_no_es_reproducible_aunque_traiga_progressive():
    completo = _de_busqueda("Daft Punk One More Time", 199428706)
    assert sc.map_track(completo)["reproducible"] is True
    bloqueado = sc.map_track(dict(completo, policy="BLOCK"))
    assert (bloqueado["reproducible"], bloqueado["solo_preview"]) == (False, False)


def test_semilla_con_titulo_al_reves_usa_la_otra_lectura():
    # «From The Top - IMMINENT» subido por un tercero: la lectura normal busca al artista «From
    # The Top» con el tema «IMMINENT» y no hay tal cosa; la otra lectura de "A - B" encuentra el
    # upload de RICOCHET «IMMINENT - From The Top» (247,2 s). SoundCloud contesta a esa búsqueda
    # con lo mismo que a «IMMINENT From The Top» (grabado).
    consultas = []

    class Http(FakeHttp):
        def __call__(self, url, params=None, headers=None, timeout=None):
            if params and "q" in params:
                consultas.append(params["q"])
                if params["q"].lower() == "from the top imminent":
                    params = dict(params, q="IMMINENT From The Top")
            return super().__call__(url, params, headers, timeout)

    http = Http()
    r = sc.build_station("youtube", "aaaaaaaaaaa", "From The Top - IMMINENT", "Subidas de un fan", 247.0, api(http))
    assert [q.lower() for q in consultas] == ["from the top imminent"], "una sola búsqueda: las dos lecturas usan el mismo pool"
    assert r["exito"] is True and r["semilla"]["video_id"] == IMMINENT
    assert r["semilla"]["evidencia"] == "texto+duracion"


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
    assert r == {"exito": False, "motivo": "no_esta_en_soundcloud", "codigo": "NOT_FOUND",
                 "mensaje": "No encontré este tema en SoundCloud."}
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
    assert r == {"exito": False, "motivo": "soundcloud_no_disponible", "codigo": "SOUNDCLOUD_ERROR",
                 "mensaje": sc.CODE_MESSAGES["SOUNDCLOUD_ERROR"]}
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


@pytest.mark.parametrize("guion, motivo, codigo", [
    ([__import__("requests").exceptions.ReadTimeout(f"HTTPSConnectionPool: /x?client_id={CID} (read timeout=10)")],
     "soundcloud_no_disponible", "SOUNDCLOUD_TIMEOUT"),
    ([__import__("requests").exceptions.ConnectTimeout("conectando")], "soundcloud_no_disponible", "SOUNDCLOUD_TIMEOUT"),
    ([__import__("requests").exceptions.ConnectionError("conexión rechazada")], "soundcloud_no_disponible",
     "SOUNDCLOUD_ERROR"),
    ([Resp(500, {})], "soundcloud_no_disponible", "SOUNDCLOUD_ERROR"),
    ([Resp(429, {})], "soundcloud_no_disponible", "RATE_LIMITED"),
    ([Resp(200, None, no_json=True)], "respuesta_inesperada", "SOUNDCLOUD_ERROR"),
    ([Resp(200, {"tracks": []})], "respuesta_inesperada", "SOUNDCLOUD_ERROR"),
    ([Resp(200, {"collection": {"id": 1}})], "respuesta_inesperada", "SOUNDCLOUD_ERROR"),
    ([Resp(200, ["no", "es", "un", "objeto"])], "respuesta_inesperada", "SOUNDCLOUD_ERROR"),
    ([Resp(200, {"collection": [{"x": 1}, "basura", None]})], "respuesta_inesperada", "SOUNDCLOUD_ERROR"),
    ([Resp(200, {"collection": []})], "station_vacia", "STATION_UNAVAILABLE"),
    ([Resp(200, {"collection": [FX["station"][ELVITO][0]]})], "station_vacia", "STATION_UNAVAILABLE"),   # solo la semilla
])
def test_fallas_de_soundcloud_dan_exito_false_con_motivo_y_sin_filtrar_el_client_id(guion, motivo, codigo, caplog):
    # f43: `motivo` es el de siempre (contrato del front); `codigo` separa timeout, 429, error
    # y Station vacía, cada uno con su mensaje.
    caplog.set_level("INFO")
    r = sc.build_station("soundcloud", ELVITO, "GIB MIR BOUNCE!", "ELVITO", None, api(FakeHttp(guion), Proveedor(CID)))
    assert r == {"exito": False, "motivo": motivo, "codigo": codigo, "mensaje": sc.CODE_MESSAGES[codigo]}
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
    assert (r.status_code, r.json()) == (200, {"exito": False, "motivo": "no_esta_en_soundcloud", "codigo": "NOT_FOUND",
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
    # f43: `sc_ref` con el mismo formato que fuente_id de SoundCloud y el origen de una lista cerrada.
    {"fuente": "youtube", "titulo": "x", "sc_ref": "abc", "sc_ref_origen": "busqueda"},
    {"fuente": "youtube", "titulo": "x", "sc_ref": "0", "sc_ref_origen": "busqueda"},
    {"fuente": "youtube", "titulo": "x", "sc_ref": "1" * 21, "sc_ref_origen": "busqueda"},
    {"fuente": "youtube", "titulo": "x", "sc_ref": "١٢٣", "sc_ref_origen": "busqueda"},
    {"fuente": "youtube", "titulo": "x", "sc_ref": "123/../456", "sc_ref_origen": "busqueda"},
    {"fuente": "youtube", "titulo": "x", "sc_ref": "123&client_id=x", "sc_ref_origen": "station"},
    {"fuente": "youtube", "titulo": "x", "sc_ref": "2041950136", "sc_ref_origen": "usuario"},
    {"fuente": "youtube", "titulo": "x", "sc_ref": "2041950136"},
    {"fuente": "youtube", "titulo": "x", "sc_ref_origen": "station"},
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


# --- f43: la referencia de SoundCloud de la fila (caso Kashpitzky) ----------------------------------
# El caso real (auditoría del 2026-10-01, grabado en el fixture): buscando "Kashpitzky For The
# Vision", la fila tiene YouTube (elegido) «Kashpitzky — For The Vision [BAO095]» del canal HATE
# (334 s) y SoundCloud 2041950136 «GTG Premiere | Kashpitzky - For The Vision  [BAOX095]» de
# Grab The Groove (333,3 s). La antena decía "No encontré este tema en SoundCloud".

KASH = "2041950136"
FOR_HER = 1635796287          # «PREMIERE: Shlomi Aber & Kashpitzky - For Her [BAO090]»: otro tema, mismo artista
YT = ("youtube", "5HuHGbZno1Y", "Kashpitzky — For The Vision [BAO095]", "HATE", 334.0)
RESPUESTA_E2E_KASH = Path(__file__).parent / "fixtures" / "station_respuesta_kashpitzky.json"


def _kash(tid):
    return next(t for t in FX["search"]["Kashpitzky for the vision"] if t["id"] == tid)


def _remix_de_kash():
    # Derivado de la grabación: el MISMO upload con «(Matador Remix)» en el título (el remix
    # existe; el agrupador del buscador lo metió en el grupo, s6 de la auditoría). Mismo
    # artista, misma duración: lo único que cambia es la versión. Id que no es de nadie.
    t = FX["tracks"][KASH]
    return dict(t, id=999000111, title="GTG Premiere | Kashpitzky - For The Vision (Matador Remix) [BAOX095]")


def test_parse_entry_ruido_pipe_artista_tema_descarta_el_prefijo():
    i = ti.parse_entry("GTG Premiere | Kashpitzky - For The Vision  [BAOX095]", "Grab The Groove")
    assert (i.artists, i.base_title, i.version) == (frozenset({"kashpitzky"}), "for the vision", "original")


@pytest.mark.parametrize("titulo, uploader, artistas, base, version", [
    # "Artista | Tema" subido por otro: sigue siendo artista | tema.
    ("Adele | Hello", "Lionel Richie", {"adele"}, "hello", "original"),
    # "Artista | Tema" del propio artista.
    ("BICEP | GLUE", "BICEP", {"bicep"}, "glue", "original"),
    # " | evento": es otra grabación (un vivo) y la versión queda desconocida.
    ("Coldplay - Fix You | Glastonbury 2024", "BBC Music", {"coldplay"}, "fix you", "desconocida:glastonbury 2024"),
    # Ruido a la izquierda, pero la derecha es un evento: no entra en la regla nueva.
    ("GTG Premiere | Kashpitzky - For The Vision Live 2024", "Grab The Groove", {"grab the groove"}, "gtg premiere",
     "desconocida:kashpitzky for the vision live 2024"),
    # Auditoría f43 (H2): la izquierda SIN ruido de `_NOISE` no entra en la regla nueva aunque la
    # derecha traiga "Artista - Tema" (un evento o un canal, no un prefijo de upload).
    ("Boiler Room | Kashpitzky - For The Vision", "Grab The Groove", {"boiler room"}, "kashpitzky for the vision",
     "original"),
    ("HÖR | Kashpitzky - For The Vision", "HÖR BERLIN", {"hor"}, "kashpitzky for the vision", "original"),
    # Auditoría f43 (H1): la izquierda ES el uploader (el artista) aunque tenga una palabra de
    # `_NOISE` (audio, video, from, official, lyrics, premiere): "Artista | Tema - Versión".
    ("Audio Bullys | We Don't Care - Radio Edit", "Audio Bullys", {"audio bullys"}, "we dont care", "radio"),
    ("Kashpitzky Official | For The Vision - Original Mix", "Kashpitzky", {"kashpitzky"}, "for the vision", "original"),
    ("Video Age | Pop Therapy - Radio Edit", "Video Age", {"video age"}, "pop therapy", "radio"),
    ("From First To Last | Note To Self - Acoustic", "From First To Last", {"from first to last"}, "note to self",
     "acoustic"),
    ("Lyrics Born | Callin' Out - Instrumental", "Lyrics Born", {"lyrics born"}, "callin out", "instrumental"),
    ("Premiere Class | Song - Edit", "Premiere Class", {"premiere class"}, "song", "edit"),
])
def test_parse_entry_pipe_sin_cambios(titulo, uploader, artistas, base, version):
    # Valores de main (2516929) antes del cambio, medidos con el código viejo.
    i = ti.parse_entry(titulo, uploader)
    assert (set(i.artists), i.base_title, i.version) == (artistas, base, version)


def test_regresion_kashpitzky_station_desde_sc_ref_sin_buscar():
    http = FakeHttp()
    r = sc.build_station(*YT, api(http), sc_ref=KASH, sc_ref_origen="busqueda")
    assert http.rutas() == [f"/tracks/{KASH}", f"/stations/soundcloud:track-stations:{KASH}/tracks"], \
        "con la referencia validada no se busca: un /tracks reemplaza al /search"
    assert sum(ruta == "/search/tracks" for ruta in http.rutas()) == 0
    assert r["exito"] is True and r["semilla"]["video_id"] == KASH
    assert r["semilla"]["evidencia"] == "referencia+texto+duracion"
    assert [i["video_id"] for i in r["items"]] == ids_grabados(KASH)[1:], "el orden de SoundCloud no se toca"
    assert r["total"] == 49


def test_regresion_kashpitzky_sin_sc_ref_la_busqueda_tambien_lo_encuentra():
    # Paso 1 solo (sin la referencia): la búsqueda de siempre, con la lectura corregida.
    http = FakeHttp()
    r = sc.build_station(*YT, api(http))
    assert http.rutas() == ["/search/tracks", f"/stations/soundcloud:track-stations:{KASH}/tracks"]
    assert http.pedidos[0][1]["q"] == "Kashpitzky for the vision"
    assert (r["exito"], r["semilla"]["video_id"], r["semilla"]["evidencia"]) == (True, KASH, "texto+duracion")


@pytest.mark.parametrize("titulo, artista, dur, semilla", [
    ("Kashpitzky — For The Vision [BAO095]", "HATE", 334.0, (KASH, "texto+duracion")),
    ("For The Vision", "Kashpitzky", 333.0, (KASH, "texto+duracion")),
    ("For The Vision (Original Mix)", "Kashpitzky", None, (KASH, "texto")),
    # Sin artista en el título, el artista es el canal (HATE): no hay evidencia. Correcto.
    ("For The Vision [BAO095]", "HATE", 334.0, None),
    # Falso negativo conocido (la lectura toma "For The Vision" como artista); fuera de este PR.
    ("For The Vision [BAO095] - Original Mix", "Kashpitzky", None, None),
])
def test_variantes_del_caso_sobre_el_pool_grabado(titulo, artista, dur, semilla):
    # El pool es el grabado para "Kashpitzky for the vision" sea cual sea la consulta: se mide
    # la REGLA de identidad, no lo que SoundCloud contestaría a otra consulta.
    http = FakeHttp(pool=FX["search"]["Kashpitzky for the vision"])
    r = sc.build_station("youtube", "5HuHGbZno1Y", titulo, artista, dur, api(http))
    if semilla:
        assert (r["exito"], r["semilla"]["video_id"], r["semilla"]["evidencia"]) == (True, *semilla)
    else:
        assert (r["exito"], r["codigo"]) == (False, "NOT_FOUND")
        assert http.rutas() == ["/search/tracks"]


@pytest.mark.parametrize("ref, body", [
    (str(FOR_HER), _kash(FOR_HER)),          # otro tema del mismo artista
    ("999000111", _remix_de_kash()),         # un remix del mismo tema
])
def test_sc_ref_de_otro_tema_o_remix_se_rechaza_y_se_busca(ref, body, caplog):
    caplog.set_level("INFO")
    http = FakeHttp(tracks={ref: body})
    r = sc.build_station(*YT, api(http), sc_ref=ref, sc_ref_origen="busqueda")
    assert http.rutas() == [f"/tracks/{ref}", "/search/tracks", f"/stations/soundcloud:track-stations:{KASH}/tracks"]
    assert not any(ref in ruta for ruta in http.rutas()[1:]), "la referencia inválida nunca es la semilla"
    assert (r["exito"], r["semilla"]["video_id"], r["semilla"]["evidencia"]) == (True, KASH, "texto+duracion")
    evt = _eventos(caplog)
    assert len(evt) == 1 and evt[0]["ref"]["resultado"] == "INVALID_MATCH" and evt[0]["metodo"] == "SEARCH"
    assert evt[0]["candidato"] == KASH and evt[0]["codigo"] == "OK"


def test_sc_ref_invalido_y_la_busqueda_tampoco_encuentra_dice_invalid_match():
    # «IMMINENT - Ascend»: la búsqueda grabada no tiene el tema; la fila traía «FINIVOID — ASCENT»
    # (de la cuenta 𝗜𝗠𝗠𝗜𝗡𝗘𝗡𝗧), otra canción.
    finivoid = FX["search"]["IMMINENT Ascend"][0]
    ref = str(finivoid["id"])
    http = FakeHttp(tracks={ref: finivoid})
    r = sc.build_station("youtube", "aaaaaaaaaaa", "IMMINENT - Ascend", "IMMINENT", 190.0, api(http),
                         sc_ref=ref, sc_ref_origen="busqueda")
    # El mensaje no afirma "no es la misma": dice lo que se sabe (auditoría H3).
    assert r == {"exito": False, "motivo": "no_esta_en_soundcloud", "codigo": "INVALID_MATCH",
                 "mensaje": "No pude confirmar que la versión de SoundCloud de esta fila sea la misma grabación, "
                            "y la búsqueda tampoco lo encontró."}
    assert http.rutas() == [f"/tracks/{ref}", "/search/tracks"], "ninguna Station: ni la de la referencia ni otra"


# Auditoría f43 H3: cada mensaje dice solo lo que se sabe. «IMMINENT - Ascend» no está en la
# búsqueda grabada; la entrada sin artista ni canal no deja armar la búsqueda (`query: null`).
ASCEND = ("youtube", "aaaaaaaaaaa", "IMMINENT - Ascend", "IMMINENT", 190.0)


@pytest.mark.parametrize("guion, ref, rutas_ref, mensaje", [
    ([], "123456789", ["/tracks/123456789"],
     "La versión de SoundCloud de esta fila ya no existe en SoundCloud, y la búsqueda tampoco lo encontró."),
    ([Resp(403, {}), Resp(403, {})], KASH, [f"/tracks/{KASH}"] * 2,
     "SoundCloud no me dejó ver la versión de SoundCloud de esta fila, y la búsqueda tampoco lo encontró."),
])
def test_invalid_match_dice_que_paso_con_la_referencia(guion, ref, rutas_ref, mensaje, caplog):
    caplog.set_level("INFO")
    http = FakeHttp(guion)
    r = sc.build_station(*ASCEND, api(http), sc_ref=ref, sc_ref_origen="busqueda")
    assert r == {"exito": False, "motivo": "no_esta_en_soundcloud", "codigo": "INVALID_MATCH", "mensaje": mensaje}
    assert http.rutas() == rutas_ref + ["/search/tracks"]
    assert _eventos(caplog)[0]["query"] == "IMMINENT ascend"


def test_sin_artista_ni_canal_no_dice_no_encontre_dice_que_no_hay_con_que_buscar(caplog):
    caplog.set_level("INFO")
    http = FakeHttp()
    r = sc.build_station("youtube", "5HuHGbZno1Y", "For The Vision", "", 334.0, api(http))
    assert r == {"exito": False, "motivo": "no_esta_en_soundcloud", "codigo": "NOT_FOUND",
                 "mensaje": "No tengo datos suficientes para buscar este tema en SoundCloud (falta el artista o el "
                            "título)."}
    assert http.rutas() == [], "no se buscó"
    assert [(e["metodo"], e["query"]) for e in _eventos(caplog)] == [("SEARCH", None)]


def test_referencia_que_no_valida_y_sin_con_que_buscar():
    # La referencia ES el tema, pero la entrada no tiene artista: no se puede confirmar y no se busca.
    http = FakeHttp()
    r = sc.build_station("youtube", "5HuHGbZno1Y", "For The Vision", "", 334.0, api(http),
                         sc_ref=KASH, sc_ref_origen="busqueda")
    assert r == {"exito": False, "motivo": "no_esta_en_soundcloud", "codigo": "INVALID_MATCH",
                 "mensaje": "No pude confirmar que la versión de SoundCloud de esta fila sea la misma grabación, "
                            "y no tengo datos suficientes para buscarlo (falta el artista o el título)."}
    assert http.rutas() == [f"/tracks/{KASH}"]


def test_sc_ref_sin_duracion_no_alcanza_y_se_busca():
    # Identidad sí, pero sin la duración de la entrada no hay "+duracion": no se usa a ciegas.
    http = FakeHttp()
    r = sc.build_station("youtube", "5HuHGbZno1Y", YT[2], YT[3], None, api(http), sc_ref=KASH, sc_ref_origen="busqueda")
    assert http.rutas() == [f"/tracks/{KASH}", "/search/tracks", f"/stations/soundcloud:track-stations:{KASH}/tracks"]
    assert r["semilla"]["evidencia"] == "texto", "la semilla vino de la búsqueda, no de la referencia"


def test_sc_ref_con_duracion_que_no_cuadra_no_valida():
    # Un video con 2 minutos de intro (454 s contra 333,3 s): identidad sí, duración no.
    http = FakeHttp()
    sc.build_station("youtube", "5HuHGbZno1Y", YT[2], YT[3], 454.0, api(http), sc_ref=KASH, sc_ref_origen="busqueda")
    assert http.rutas()[:2] == [f"/tracks/{KASH}", "/search/tracks"]


def test_sc_ref_que_no_existe_se_busca():
    http = FakeHttp()
    r = sc.build_station(*YT, api(http), sc_ref="123456789", sc_ref_origen="busqueda")
    assert http.rutas() == ["/tracks/123456789", "/search/tracks", f"/stations/soundcloud:track-stations:{KASH}/tracks"]
    assert r["semilla"]["video_id"] == KASH


@pytest.mark.parametrize("falla, codigo", [
    (Resp(429, {}), "RATE_LIMITED"),
    (__import__("requests").exceptions.ReadTimeout("read timeout"), "SOUNDCLOUD_TIMEOUT"),
    (Resp(503, {}), "SOUNDCLOUD_ERROR"),
])
def test_si_el_tracks_de_la_referencia_falla_no_se_busca(falla, codigo, caplog):
    # Buscar después de un 429 o un timeout chocaría con lo mismo: se dice qué pasó.
    caplog.set_level("INFO")
    http = FakeHttp([falla])
    r = sc.build_station(*YT, api(http), sc_ref=KASH, sc_ref_origen="busqueda")
    assert (r["exito"], r["codigo"], r["mensaje"]) == (False, codigo, sc.CODE_MESSAGES[codigo])
    assert http.rutas() == [f"/tracks/{KASH}"]
    # Auditoría H4: el evento dice qué se estaba intentando (antes salía `metodo: null`).
    assert [(e["metodo"], e["codigo"]) for e in _eventos(caplog)] == [("VALIDATED_REFERENCE", codigo)]


@pytest.mark.parametrize("status", [401, 403])
def test_tracks_sin_acceso_renueva_una_vez_y_despues_busca(status, caplog):
    # Auditoría H4: un 401/403 que sigue después de renovar el client_id (track privado o
    # bloqueado) no corta: es una referencia que no se pudo confirmar, y se busca como con un 404.
    caplog.set_level("INFO")
    prov = Proveedor("CID_A", "CID_B")
    http = FakeHttp([Resp(status, {}), Resp(status, {})])
    r = sc.build_station(*YT, api(http, prov), sc_ref=KASH, sc_ref_origen="busqueda")
    assert http.rutas() == [f"/tracks/{KASH}", f"/tracks/{KASH}", "/search/tracks",
                            f"/stations/soundcloud:track-stations:{KASH}/tracks"]
    assert prov.llamadas == [False, True], "una sola renovación del client_id"
    assert [p[1]["client_id"] for p in http.pedidos] == ["CID_A", "CID_B", "CID_B", "CID_B"]
    assert (r["exito"], r["semilla"]["video_id"], r["semilla"]["evidencia"]) == (True, KASH, "texto+duracion")
    evt = _eventos(caplog)
    assert len(evt) == 1 and evt[0]["metodo"] == "SEARCH"
    assert evt[0]["ref"] == {"origen": "busqueda", "sc_id": KASH, "resultado": "INVALID_MATCH", "rechazo": "sin_acceso"}


def test_tracks_que_devuelve_otro_id_no_se_usa():
    # Auditoría H5: /tracks/{ref} contesta un cuerpo de OTRO id (el de Kashpitzky, que sí pasaría
    # la identidad y la duración). No es la referencia pedida: se rechaza por forma y se busca.
    ref = "999000111"
    http = FakeHttp(tracks={ref: FX["tracks"][KASH]})
    r = sc.build_station(*YT, api(http), sc_ref=ref, sc_ref_origen="busqueda")
    assert http.rutas() == [f"/tracks/{ref}", "/search/tracks", f"/stations/soundcloud:track-stations:{KASH}/tracks"]
    assert r["semilla"]["evidencia"] == "texto+duracion", "la semilla vino de la búsqueda, no de la referencia"
    trace: dict = {}
    assert sc.validate_reference(api(FakeHttp(tracks={ref: FX["tracks"][KASH]})), ref, *YT[2:], trace) is None
    assert trace["rechazo"] == "forma"


@pytest.mark.parametrize("busqueda, codigo", [
    ("ambigua", "AMBIGUOUS"),
    ("503", "SOUNDCLOUD_ERROR"),
])
def test_referencia_invalida_no_tapa_otro_codigo_de_la_busqueda(busqueda, codigo):
    # Auditoría H5: INVALID_MATCH reemplaza SOLO a NOT_FOUND. Si la búsqueda dice ambiguo o
    # falla, ese es el código (la referencia inválida no explica por qué no hay Station).
    for_her = str(FOR_HER)
    if busqueda == "ambigua":
        otro = dict(FX["tracks"][KASH], id=999000222, title="Shlomi Aber - For The Vision [BAOX095]")
        http = FakeHttp(tracks={for_her: _kash(FOR_HER)}, pool=[FX["tracks"][KASH], otro])
        entrada = ("youtube", "5HuHGbZno1Y", "Shlomi Aber & Kashpitzky - For The Vision", "HATE", 334.0)
    else:
        http = FakeHttp([Resp(200, _kash(FOR_HER)), Resp(503, {})])
        entrada = YT
    r = sc.build_station(*entrada, api(http), sc_ref=for_her, sc_ref_origen="busqueda")
    assert (r["exito"], r["codigo"], r["mensaje"]) == (False, codigo, sc.CODE_MESSAGES[codigo])
    assert http.rutas() == [f"/tracks/{for_her}", "/search/tracks"]


def test_origen_station_va_directo_sin_buscar_ni_validar():
    # Fila de la Station con otra versión elegida (YouTube): `g.base` ES el tema de SoundCloud.
    http = FakeHttp()
    r = sc.build_station("youtube", "zzzzzzzzzzz", "cualquier título de un video", "otro canal", 999.0, api(http),
                         sc_ref=KASH, sc_ref_origen="station")
    assert http.rutas() == [f"/stations/soundcloud:track-stations:{KASH}/tracks"]
    assert (r["semilla"]["video_id"], r["semilla"]["evidencia"]) == (KASH, "id")
    assert [i["video_id"] for i in r["items"]] == ids_grabados(KASH)[1:]


def test_ambiguo_tiene_su_codigo():
    # Pool grabado + una copia del upload de Kashpitzky con OTRO artista (Shlomi Aber, que en la
    # entrada va junto a Kashpitzky): los dos pasan la identidad y no tienen artista en común.
    otro = dict(FX["tracks"][KASH], id=999000222, title="Shlomi Aber - For The Vision [BAOX095]")
    http = FakeHttp(pool=[FX["tracks"][KASH], otro])
    r = sc.build_station("youtube", "5HuHGbZno1Y", "Shlomi Aber & Kashpitzky - For The Vision", "HATE", 334.0, api(http))
    assert r == {"exito": False, "motivo": "no_esta_en_soundcloud", "codigo": "AMBIGUOUS",
                 "mensaje": "En SoundCloud hay más de un tema que podría ser este; no elijo uno a ciegas."}
    assert http.rutas() == ["/search/tracks"]


def test_station_vacia_desde_la_referencia_dice_station_unavailable():
    class SinStation(FakeHttp):
        def __call__(self, url, params=None, headers=None, timeout=None):
            if "/stations/" in url:
                self.pedidos.append((url, dict(params or {}), timeout))
                return Resp(200, {"collection": []})
            return super().__call__(url, params, headers, timeout)

    http = SinStation()
    r = sc.build_station(*YT, api(http), sc_ref=KASH, sc_ref_origen="busqueda")
    assert http.rutas() == [f"/tracks/{KASH}", f"/stations/soundcloud:track-stations:{KASH}/tracks"]
    assert r == {"exito": False, "motivo": "station_vacia", "codigo": "STATION_UNAVAILABLE",
                 "mensaje": "Encontré el tema en SoundCloud, pero SoundCloud no tiene una Station para él."}


def test_los_siete_codigos_tienen_mensajes_distintos():
    codigos = ["NOT_FOUND", "AMBIGUOUS", "INVALID_MATCH", "SOUNDCLOUD_TIMEOUT", "RATE_LIMITED",
               "SOUNDCLOUD_ERROR", "STATION_UNAVAILABLE"]
    assert sorted(sc.CODE_MESSAGES) == sorted(codigos)
    assert len(set(sc.CODE_MESSAGES.values())) == 7


def _eventos(caplog):
    return [json.loads(r.getMessage().removeprefix("station_resolve "))
            for r in caplog.records if r.getMessage().startswith("station_resolve ")]


def test_un_evento_de_log_por_resolucion_sin_client_id_ni_urls(caplog):
    import requests
    caplog.set_level("INFO")
    sc.build_station(*YT, api(FakeHttp(), Proveedor(CID)), sc_ref=KASH, sc_ref_origen="busqueda")
    evt = _eventos(caplog)
    assert evt == [{"entrada": {"fuente": "youtube", "titulo": YT[2], "artista": "HATE"},
                    "ref": {"origen": "busqueda", "sc_id": KASH, "resultado": "OK"},
                    "metodo": "VALIDATED_REFERENCE", "query": None, "candidato": KASH,
                    "evidencia": "referencia+texto+duracion", "codigo": "OK"}]
    caplog.clear()
    sc.build_station(*YT, api(FakeHttp([requests.exceptions.ReadTimeout(f"/x?client_id={CID}")]), Proveedor(CID)))
    evt = _eventos(caplog)
    assert [(e["metodo"], e["query"], e["codigo"]) for e in evt] == [("SEARCH", "Kashpitzky for the vision",
                                                                       "SOUNDCLOUD_TIMEOUT")]
    assert CID not in caplog.text and "api-v2" not in caplog.text and "client_id" not in caplog.text


def test_endpoint_kashpitzky_con_sc_ref_es_la_respuesta_que_usa_el_e2e(client):
    r = client.get("/api/station", params={"fuente": "youtube", "fuente_id": "5HuHGbZno1Y",
                                           "titulo": "Kashpitzky — For The Vision [BAO095]", "artista": "HATE",
                                           "duracion": "334", "sc_ref": KASH, "sc_ref_origen": "busqueda"})
    assert r.status_code == 200
    assert r.json() == json.loads(RESPUESTA_E2E_KASH.read_text(encoding="utf-8")), "el E2E depende de esta forma exacta"
    assert client.http.rutas() == [f"/tracks/{KASH}", f"/stations/soundcloud:track-stations:{KASH}/tracks"]
    assert [i["video_id"] for i in r.json()["items"]] == ids_grabados(KASH)[1:]


def test_endpoint_pasa_sc_ref_y_origen(client, monkeypatch):
    recibido = []
    monkeypatch.setattr(sc, "build_station", lambda *a, **k: recibido.append(k) or sc.failure(sc.EMPTY_STATION))
    client.get("/api/station", params={"fuente": "youtube", "titulo": "x", "sc_ref": " 2041950136 ",
                                       "sc_ref_origen": "Station"})
    assert recibido == [{"sc_ref": KASH, "sc_ref_origen": "station"}]
