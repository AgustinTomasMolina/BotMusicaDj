"""Tests de las versiones de la Station (f36): POST /api/versiones y sus piezas en server.py
(`_versiones_de`, `_version_valida`, `_sc_otros_uploads`, `_sc_gated`, `_buscar_mix_detalle`,
`_calidad_cacheada`).

Sin red. Los temas de la Station y de SoundCloud salen de las grabaciones reales de api-v2
(`fixtures/soundcloud_api_v2.json` y `fixtures/station_respuesta.json`). Lo que devuelven
YouTube, los MP3 directos y Spotify está armado a mano con la FORMA exacta de
`search_agent.buscar_en_*` y `scrapers.buscar_*` (título, canal/artista, duración en s), con
los casos que la regla de identidad tiene que separar: otro tema, un vivo, un remix, otra
edición más corta, otro artista, un video con intro. Lo esperado sale de esa regla (el
docstring de `track_identity.evidencia_misma_grabacion`), no del código bajo prueba.

`fixtures/versiones_respuestas.json` es lo que el endpoint REAL devuelve para cuatro temas con
estas fuentes simuladas; el E2E (frontend/e2e/pantalla.mjs) lo usa como API. Si cambia la
forma de la respuesta, este test falla antes que el E2E. Para regrabarlo a propósito:
MUSIFLIX_REGRABAR_VERSIONES=1.
"""
import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest
import soundcloud_station as sc

FIX = Path(__file__).parent / "fixtures"
FX = json.loads((FIX / "soundcloud_api_v2.json").read_text(encoding="utf-8"))
STATION = json.loads((FIX / "station_respuesta.json").read_text(encoding="utf-8"))
RESPUESTAS_E2E = FIX / "versiones_respuestas.json"
CID = "CID_SECRETO_0123456789abcdefABCD"


# --- dobles ----------------------------------------------------------------------------------

class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class FakeHttp:
    """`requests.get` de api-v2: las búsquedas grabadas por consulta (sin distinguir mayúsculas).
    `alias` manda otra consulta a una grabación; `guion`: respuestas que se consumen antes."""

    def __init__(self, guion=None, alias=None):
        self.guion = list(guion or [])
        self.alias = {k.lower(): v for k, v in (alias or {}).items()}
        self.pedidos = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.pedidos.append((url.removeprefix(sc.API), dict(params or {})))
        if self.guion:
            return self.guion.pop(0)
        if url.removeprefix(sc.API) != "/search/tracks":
            return Resp(404, {})
        busquedas = {k.lower(): v for k, v in FX["search"].items()}
        q = params["q"].lower()
        return Resp(200, {"collection": busquedas.get(self.alias.get(q, q).lower(), [])})


class Fuentes:
    """`search_agent` y los scrapers de juguete. `por_q[fuente][consulta en minúsculas]` es lo
    que contesta cada una; una Exception se lanza (como una fuente que se cae) y un Event se
    espera (una fuente que cuelga hasta que el test la suelta)."""

    def __init__(self, por_q=None):
        self.por_q = por_q or {}
        self.pedidos = []

    def _r(self, fuente, q, limit):
        self.pedidos.append((fuente, q))
        r = self.por_q.get(fuente, {})
        r = r.get(q.lower(), []) if isinstance(r, dict) else r
        if isinstance(r, threading.Event):
            r.wait(10)
            return []
        if isinstance(r, Exception):
            raise r
        return [dict(c) for c in r][:limit]

    def buscar_en_youtube(self, q, limit=10):
        return self._r("youtube", q, limit)

    def buscar_en_spotify(self, q, limit=10):
        return self._r("spotify", q, limit)

    def buscar_en_soundcloud(self, q, limit=10):
        return self._r("soundcloud", q, limit)

    def ligaudio(self, q, limit=10):
        return self._r("ligaudio", q, limit)

    def hitplayer(self, q, limit=10):
        return self._r("hitplayer", q, limit)

    def fuentes(self):
        return [f for f, _ in self.pedidos]


class Notas:
    """`_calidad_preview` de juguete: la nota por fuente (o por url) y cada pedido anotado."""

    def __init__(self, server, por_fuente=None, por_url=None):
        self.server = server
        self.por_fuente = por_fuente or {}
        self.por_url = por_url or {}
        self.pedidos = []

    def __call__(self, titulo, artista, fuente, url):
        self.pedidos.append((fuente, url))
        g = self.por_url.get(url, self.por_fuente.get(fuente))
        if isinstance(g, threading.Event):
            g.wait(10)
            return None
        if g is None:
            return None
        return {"badge": f"nota {g}", "calidad": f"calidad {g}", "metodo": "simulado",
                "grade": g, "color": self.server._GRADO_COLOR[g]}


# --- candidatos (forma de search_agent / scrapers) ---------------------------------------------

def yt(titulo, canal, dur, vid):
    return {"titulo": titulo, "artista": canal, "duracion": dur, "url": f"https://www.youtube.com/watch?v={vid}",
            "fuente": "youtube", "video_id": vid, "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}


def spotify(titulo, artista, dur, tid):
    return {"titulo": titulo, "artista": artista, "duracion": dur, "popularidad": 40,
            "url": f"https://open.spotify.com/track/{tid}", "fuente": "spotify", "preview_url": None,
            "id": tid, "thumbnail": None}


def mp3(fuente, titulo, artista, dur, n):
    url = f"https://example.invalid/{fuente}/{n}.mp3"
    return {"titulo": titulo, "artista": artista, "duracion": dur, "url": url, "stream_url": url,
            "fuente": fuente, "thumbnail": None}


# «B WITH U» de SPÆCE (244,8 s), el primer tema de la Station de ELVITO.
B_WITH_U = STATION["items"][0]
TAKE_THAT = STATION["items"][1]
Q_B = "spæce b with u"
YT_B = yt("SPÆCE - B WITH U", "SPÆCE", 245, "bwithu00001")
YT_B_INTRO = yt("SPÆCE - B WITH U (Official Video)", "SPÆCE", 291, "bwithu00002")
YT_OTRO_TEMA = yt("SPÆCE - LOST IN YOU", "SPÆCE", 245, "bwithu00003")
YT_VIVO = yt("SPÆCE - B WITH U (Live at Nature One 2025)", "Nature One", 250, "bwithu00004")
YT_REMIX = yt("SPÆCE - B WITH U (KHROME Remix)", "KHROME", 240, "bwithu00005")
YT_OTRO_ARTISTA = yt("NARCX - B WITH U", "NARCX", 245, "bwithu00006")
YT_MAS_CORTA = yt("SPÆCE - B WITH U", "SPÆCE", 180, "bwithu00007")
LIG_B = mp3("ligaudio", "B WITH U", "SPÆCE", 244, 1)
HIT_B = mp3("hitplayer", "B WITH U", "SPÆCE", 0, 2)
SP_B = spotify("B WITH U", "SPÆCE", 245, "0bwithu0spotify0000000")


def sc_track(tid, q="Daft Punk One More Time"):
    return sc.map_track(next(t for t in FX["search"][q] if t["id"] == tid))


# «One More Time» de Daft Punk en SoundCloud: 2366118086 es Go+ (30 s de 320 s) y 199428706 es
# el upload completo de la misma cuenta (322,6 s), los dos grabados.
DAFT_GO = sc_track(2366118086)
DAFT_COMPLETO_ID = "199428706"
Q_DAFT = "daft punk one more time"
# «From The Top» de FblManny: Go+ (30 s de 142,3 s) y nadie más lo tiene.
FBL_GO = sc_track(2374863902, "IMMINENT From The Top")


# --- server aislado ------------------------------------------------------------------------------

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


class Entorno:
    def __init__(self, server, monkeypatch):
        self.server = server
        self.mp = monkeypatch
        self.fuentes = Fuentes()
        self.notas = Notas(server)
        self.http = FakeHttp()
        monkeypatch.setattr(server, "search_agent", self.fuentes)
        monkeypatch.setattr(server, "FUENTES_SCRAPER", [("ligaudio", self.fuentes.ligaudio),
                                                        ("hitplayer", self.fuentes.hitplayer)])
        monkeypatch.setattr(server, "_calidad_preview", self.notas)
        monkeypatch.setattr(server.jobs, "queue_disponible", lambda: False)
        monkeypatch.setattr(server, "_sc_pause_until", 0.0)
        monkeypatch.setattr(sc, "_default_api", None)
        self.usar_http(self.http)
        server._MIX_CACHE.clear()
        server._CALIDAD_CACHE.clear()
        sc.clear_cache()

    def usar_http(self, http):
        self.http = http
        self.mp.setattr(sc, "_default_api", sc.SoundCloudApi(http_get=http, client_id_provider=lambda r: CID))

    def resultados(self, **por_fuente):
        self.fuentes.por_q = por_fuente

    def versiones(self, tema, formato="wav"):
        return self.server._versiones_de(dict(tema), formato)


@pytest.fixture
def env(server, monkeypatch):
    e = Entorno(server, monkeypatch)
    yield e
    server._MIX_CACHE.clear()
    server._CALIDAD_CACHE.clear()


def opciones(r):
    """(fuente, url) de cada opción, en orden."""
    return [(o["fuente"], o["url"]) for o in r["opciones"]]


BASE_B = ("soundcloud", B_WITH_U["url"])


# --- la regla de identidad: solo el mismo tema ------------------------------------------------------

@pytest.mark.parametrize("cand, por_que", [
    (YT_OTRO_TEMA, "otro tema del mismo artista"),
    (YT_VIVO, "un vivo no es la grabación de estudio"),
    (YT_REMIX, "un remix es otro tema para un DJ"),
    (YT_OTRO_ARTISTA, "mismo título, otro artista"),
    (YT_MAS_CORTA, "180 s de un tema de 244,8 s es otra edición (audio_exacto)"),
])
def test_version_valida_descarta_lo_que_no_es_el_tema(env, cand, por_que):
    env.resultados(youtube={Q_B: [cand]})
    r = env.versiones(B_WITH_U)
    assert opciones(r) == [BASE_B], por_que


def test_una_edicion_mas_corta_no_pasa_cuando_la_station_es_extended(env):
    # La Station trae la Extended (305 s); YouTube, la «misma» Extended de 180 s: por texto
    # coincide, pero el audio de la Station ES el tema y una más corta que no cuadra es otra
    # edición. Sin audio_exacto pasaría como "texto".
    tema = dict(B_WITH_U, titulo="B WITH U (Extended Mix)", duracion=305.0)
    corta = yt("SPÆCE - B WITH U (Extended Mix)", "SPÆCE", 180, "bwithu00008")
    env.resultados(youtube={"spæce b with u extended mix": [corta]})
    r = env.versiones(tema)
    assert opciones(r) == [("soundcloud", tema["url"])]
    assert r["motivo"] == "No lo encontré en otras plataformas (o lo que apareció no era este tema)"


def test_acepta_un_video_de_youtube_con_intro_mas_largo(env):
    # 291 s contra 244,8 s: no cuadra por duración, pero un video con intro es MÁS largo; lo que
    # delata otra edición es que sea más corto.
    env.resultados(youtube={Q_B: [YT_B_INTRO]})
    r = env.versiones(B_WITH_U)
    assert opciones(r) == [("youtube", YT_B_INTRO["url"]), BASE_B]
    assert r["opciones"][0]["evidencia"] == "texto"
    assert r["motivo"] is None


def test_una_por_plataforma_en_orden_de_fuente_y_con_nota(env):
    env.resultados(
        youtube={Q_B: [YT_OTRO_TEMA, YT_B, YT_B_INTRO]},     # dos de YouTube válidas: la primera
        ligaudio={Q_B: [LIG_B]}, hitplayer={Q_B: [HIT_B]}, spotify={Q_B: [SP_B]})
    env.notas.por_fuente = {"youtube": "B", "soundcloud": "C", "ligaudio": "A", "hitplayer": "D"}
    r = env.versiones(B_WITH_U)
    # Orden de fuente para WAV (_rank_calidad): YouTube, SoundCloud, Spotify, MP3 directo.
    assert opciones(r) == [("youtube", YT_B["url"]), BASE_B, ("spotify", SP_B["url"]),
                           ("ligaudio", LIG_B["url"]), ("hitplayer", HIT_B["url"])]
    assert [o.get("estacion", False) for o in r["opciones"]] == [False, True, False, False, False]
    assert [o["evidencia"] for o in r["opciones"] if not o.get("estacion")] == \
        ["texto+duracion", "texto+duracion", "texto+duracion", "texto"]
    assert [(o.get("calidad") or {}).get("grade") for o in r["opciones"]] == ["B", "C", None, "A", "D"]
    assert (r["motivo"], r["fallidas"]) == (None, [])


def test_tope_de_cinco_opciones(env):
    # Go+ resuelto en todas partes: YouTube, la Station, otro upload de SC, Spotify, Ligaudio y
    # HitPlayer = 6. Queda afuera la última en orden de fuente (HitPlayer), nunca la Station.
    env.resultados(
        youtube={Q_DAFT: [yt("Daft Punk - One More Time (Official Video)", "Daft Punk", 320, "daftpunk001")]},
        spotify={Q_DAFT: [spotify("One More Time", "Daft Punk", 320, "0daftpunkspotify000000")]},
        ligaudio={Q_DAFT: [mp3("ligaudio", "One More Time", "Daft Punk", 320, 3)]},
        hitplayer={Q_DAFT: [mp3("hitplayer", "One More Time", "Daft Punk", 0, 4)]})
    r = env.versiones(DAFT_GO)
    assert [(o["fuente"], o.get("video_id")) for o in r["opciones"]] == [
        ("youtube", "daftpunk001"), ("soundcloud", DAFT_GO["video_id"]), ("soundcloud", DAFT_COMPLETO_ID),
        ("spotify", None), ("ligaudio", None)]
    assert r["opciones"][1]["estacion"] is True


def test_sin_nota_para_un_go_plus_ni_para_spotify(env):
    env.resultados(youtube={Q_DAFT: [yt("Daft Punk - One More Time (Official Video)", "Daft Punk", 320, "daftpunk001")]},
                   spotify={Q_DAFT: [spotify("One More Time", "Daft Punk", 320, "0daftpunkspotify000000")]})
    env.notas.por_fuente = {"youtube": "B", "soundcloud": "A", "spotify": "A"}
    r = env.versiones(DAFT_GO)
    notas = {(o["fuente"], o.get("video_id")): o.get("calidad", "SIN_CAMPO") for o in r["opciones"]}
    assert notas[("soundcloud", DAFT_GO["video_id"])] == "SIN_CAMPO", "un preview de 30 s no es el tema: sin nota"
    assert notas[("spotify", None)] == "SIN_CAMPO", "Spotify se baja buscando en YouTube: la nota sería de otro audio"
    assert notas[("soundcloud", DAFT_COMPLETO_ID)]["grade"] == "A"
    assert sorted(f for f, _ in env.notas.pedidos) == ["soundcloud", "youtube"]


# --- motivos ---------------------------------------------------------------------------------------

def test_motivo_cuando_una_plataforma_se_cae(env):
    env.resultados(youtube=RuntimeError("yt-dlp roto"), spotify={Q_B: [SP_B]})
    r = env.versiones(B_WITH_U)
    assert r["fallidas"] == ["youtube"]
    assert r["motivo"] == "No contestó a tiempo: YouTube"
    assert opciones(r) == [BASE_B, ("spotify", SP_B["url"])]


def test_motivo_cuando_una_plataforma_no_contesta_a_tiempo(env, monkeypatch):
    colgada = threading.Event()
    monkeypatch.setattr(env.server, "_SOURCE_DEADLINE_S", 0.3)
    env.resultados(ligaudio=colgada, hitplayer=colgada)
    t0 = time.monotonic()
    try:
        r = env.versiones(B_WITH_U)
    finally:
        colgada.set()
    assert time.monotonic() - t0 < 5, "el tope de la búsqueda no cortó"
    assert sorted(r["fallidas"]) == ["hitplayer", "ligaudio"]
    # f38: el usuario ve solo "MP3", y dos MP3 caídos lo dicen una sola vez.
    assert r["motivo"] == "No contestó a tiempo: MP3"


def test_motivo_no_lo_encontre(env):
    env.resultados(youtube={"narcx take that": [yt("NARCX - Take Me Higher", "NARCX", 227, "narcx000001")]},
                   spotify={"narcx take that": [spotify("Patience", "Take That", 202, "0takethatspotify000000")]})
    r = env.versiones(TAKE_THAT)
    assert opciones(r) == [("soundcloud", TAKE_THAT["url"])]
    assert r["motivo"] == "No lo encontré en otras plataformas (o lo que apareció no era este tema)"


def test_motivo_texto_ilegible_y_no_busca(env):
    r = env.versiones(dict(B_WITH_U, titulo="???", artista=""))
    assert r["motivo"] == "No pude leer artista y título de este tema para buscarlo en otras plataformas"
    assert [o["fuente"] for o in r["opciones"]] == ["soundcloud"]
    assert env.fuentes.pedidos == [] and env.http.pedidos == []


# --- tope de tiempo de las notas ---------------------------------------------------------------------

def test_las_notas_tienen_tope_y_la_que_cuelga_queda_sin_nota(env, monkeypatch):
    colgada = threading.Event()
    monkeypatch.setattr(env.server, "_VERSION_CAL_DEADLINE_S", 0.3)
    env.resultados(youtube={Q_B: [YT_B]}, ligaudio={Q_B: [LIG_B]})
    env.notas.por_fuente = {"youtube": "A", "soundcloud": "B", "ligaudio": colgada}
    t0 = time.monotonic()
    try:
        r = env.versiones(B_WITH_U)
    finally:
        colgada.set()
    assert time.monotonic() - t0 < 5, "una nota colgada frenó la fila"
    assert [(o["fuente"], (o["calidad"] or {}).get("grade")) for o in r["opciones"]] == \
        [("youtube", "A"), ("soundcloud", "B"), ("ligaudio", None)]


# --- SoundCloud: solo para Go+ o sin audio, con cupo y pausa -------------------------------------------

def test_a_soundcloud_no_se_le_pregunta_por_un_tema_completo(env):
    r = env.versiones(B_WITH_U)
    assert env.http.pedidos == [], "el tema ya es de SoundCloud y se puede bajar entero"
    assert "soundcloud" not in env.fuentes.fuentes(), "la búsqueda general no le pregunta a SoundCloud"
    assert r["opciones"][0]["estacion"] is True


@pytest.mark.parametrize("cambio", [{"solo_preview": True}, {"reproducible": False}])
def test_a_soundcloud_se_le_pregunta_por_un_go_plus_o_sin_audio(env, cambio):
    tema = {**DAFT_GO, "solo_preview": False, "reproducible": True, **cambio}
    r = env.versiones(tema)
    assert env.http.pedidos == [("/search/tracks", {"q": "Daft Punk one more time", "limit": 10, "client_id": CID})]
    assert DAFT_COMPLETO_ID in [o.get("video_id") for o in r["opciones"]]
    assert "soundcloud" not in env.fuentes.fuentes()


def test_otros_uploads_descarta_previews_y_el_mismo_id(env):
    # Como si la Station hubiera traído 199428706 sin audio: se busca otro upload, pero ni él
    # mismo ni los Go+ (2366118086, 254112146) sirven.
    otros, motivo = env.server._sc_otros_uploads("Daft Punk One More Time", DAFT_COMPLETO_ID)
    grabados = FX["search"]["Daft Punk One More Time"]
    esperado = [str(t["id"]) for t in grabados if t["policy"] != "SNIP" and str(t["id"]) != DAFT_COMPLETO_ID]
    assert motivo is None
    assert [o["video_id"] for o in otros] == esperado
    assert "2366118086" not in esperado and "254112146" not in esperado and len(esperado) == len(grabados) - 3


def test_un_go_plus_se_resuelve_con_otro_upload_completo(env):
    env.notas.por_fuente = {"soundcloud": "A"}
    r = env.versiones(DAFT_GO)
    assert [(o["video_id"], o.get("solo_preview"), o.get("estacion", False)) for o in r["opciones"]] == \
        [(DAFT_GO["video_id"], True, True), (DAFT_COMPLETO_ID, False, False)]
    assert r["opciones"][1]["calidad"]["grade"] == "A"


def test_un_429_pausa_a_soundcloud_y_la_fila_lo_dice(env):
    env.usar_http(FakeHttp(guion=[Resp(429, {})]))
    r = env.versiones(DAFT_GO)
    assert r["motivo"] == "SoundCloud frenó los pedidos (demasiados seguidos): no busqué otro upload completo"
    restante = env.server._sc_pause_until - time.monotonic()
    assert 55 < restante <= 60, f"la pausa tiene que ser de 60 s (quedan {restante:.1f})"
    # La fila siguiente ni pregunta mientras dura la pausa, y lo dice.
    r2 = env.versiones(FBL_GO)
    assert len(env.http.pedidos) == 1
    assert r2["motivo"] == "SoundCloud está en pausa por demasiados pedidos: no busqué otro upload completo"


def test_otra_falla_de_soundcloud_no_pausa(env):
    env.usar_http(FakeHttp(guion=[Resp(500, {})]))
    r = env.versiones(DAFT_GO)
    assert r["motivo"] == "SoundCloud no contestó la búsqueda de otro upload completo"
    assert env.server._sc_pause_until == 0.0


def test_con_el_cupo_de_soundcloud_lleno_la_fila_lo_dice(env, monkeypatch):
    monkeypatch.setattr(env.server, "_SC_GATE_WAIT_S", 0.05)
    gate = env.server._SC_GATE
    tomados = [gate.acquire(timeout=1), gate.acquire(timeout=1)]
    try:
        assert tomados == [True, True], "el cupo de SoundCloud tiene que ser 2"
        assert gate.acquire(timeout=0.01) is False
        r = env.versiones(DAFT_GO)
    finally:
        gate.release()
        gate.release()
    assert r["motivo"] == "SoundCloud estaba ocupado: no busqué otro upload completo"
    assert env.http.pedidos == []


def test_la_nota_de_soundcloud_tambien_pasa_por_el_cupo(env, monkeypatch):
    monkeypatch.setattr(env.server, "_SC_GATE_WAIT_S", 0.05)
    env.notas.por_fuente = {"soundcloud": "A", "youtube": "B"}
    env.resultados(youtube={Q_B: [YT_B]})
    gate = env.server._SC_GATE
    gate.acquire()
    gate.acquire()
    try:
        r = env.versiones(B_WITH_U)
    finally:
        gate.release()
        gate.release()
    assert [(o["fuente"], (o["calidad"] or {}).get("grade")) for o in r["opciones"]] == [("youtube", "B"), ("soundcloud", None)]
    assert [f for f, _ in env.notas.pedidos] == ["youtube"]


# --- _buscar_mix_detalle ---------------------------------------------------------------------------

def test_mix_detalle_con_fuentes_no_llama_a_soundcloud(env):
    env.resultados(youtube=[YT_B], soundcloud=[dict(YT_B, fuente="soundcloud")])
    mezcla, fallidas = env.server._buscar_mix_detalle("x", 12, ("youtube", "spotify"))
    assert sorted(set(env.fuentes.fuentes())) == ["spotify", "youtube"]
    assert [c["fuente"] for c in mezcla] == ["youtube"] and fallidas == []


def test_mix_detalle_la_cache_con_fuentes_es_otra_clave(env):
    env.resultados(youtube=[YT_B], soundcloud=[dict(YT_B, fuente="soundcloud", url="https://soundcloud.com/a/b")])
    env.server._buscar_mix_detalle("x", 12, ("youtube",))
    env.fuentes.pedidos.clear()
    completa = env.server._buscar_mix("x", 12)
    assert "soundcloud" in env.fuentes.fuentes(), "la búsqueda completa no puede salir de la caché recortada"
    assert sorted(c["fuente"] for c in completa) == ["soundcloud", "youtube"]
    # Y al revés: con la completa en caché, la recortada no trae lo de SoundCloud.
    env.server._MIX_CACHE.clear()
    env.server._buscar_mix("y", 12)
    recortada, _ = env.server._buscar_mix_detalle("y", 12, ("youtube",))
    assert [c["fuente"] for c in recortada] == ["youtube"]
    # La misma pregunta dos veces sí sale de la caché.
    env.fuentes.pedidos.clear()
    env.server._buscar_mix_detalle("y", 12, ("youtube",))
    assert env.fuentes.pedidos == []


def test_mix_detalle_devuelve_las_que_fallaron_y_no_cachea(env):
    env.resultados(youtube=[YT_B], spotify=RuntimeError("spotify caído"))
    mezcla, fallidas = env.server._buscar_mix_detalle("x", 12, ("youtube", "spotify"))
    assert fallidas == ["spotify"] and [c["fuente"] for c in mezcla] == ["youtube"]
    env.fuentes.pedidos.clear()
    env.server._buscar_mix_detalle("x", 12, ("youtube", "spotify"))
    assert sorted(env.fuentes.fuentes()) == ["spotify", "youtube"], "un resultado con una fuente caída no se cachea"


# --- _calidad_cacheada: la misma caché que /api/calidad ---------------------------------------------------

@pytest.fixture
def client(server, env):
    from fastapi.testclient import TestClient
    return TestClient(server.app)


def test_la_nota_de_versiones_la_reusa_api_calidad(env, client):
    env.resultados(youtube={Q_B: [YT_B]})
    env.notas.por_fuente = {"youtube": "A", "soundcloud": "B"}
    r = env.versiones(B_WITH_U)
    antes = list(env.notas.pedidos)
    q = {"titulo": YT_B["titulo"], "artista": YT_B["artista"], "fuente": "youtube", "url": YT_B["url"]}
    c = client.get("/api/calidad", params=q).json()
    assert env.notas.pedidos == antes, "/api/calidad volvió a calcular una nota que versiones ya tenía"
    assert c == r["opciones"][0]["calidad"] and c["grade"] == "A"


def test_api_calidad_llena_la_cache_que_usa_versiones(env, client):
    env.resultados(youtube={Q_B: [YT_B]})
    env.notas.por_fuente = {"youtube": "C", "soundcloud": "B"}
    client.get("/api/calidad", params={"titulo": YT_B["titulo"], "artista": YT_B["artista"],
                                       "fuente": "youtube", "url": YT_B["url"]})
    env.notas.por_fuente["youtube"] = "A"          # si se recalculara, cambiaría
    r = env.versiones(B_WITH_U)
    assert r["opciones"][0]["calidad"]["grade"] == "C"
    assert [f for f, _ in env.notas.pedidos].count("youtube") == 1


# --- POST /api/versiones ----------------------------------------------------------------------------

def cuerpo(**cambios):
    tema = {k: B_WITH_U[k] for k in ("titulo", "artista", "duracion", "video_id", "url", "permalink",
                                     "thumbnail", "reproducible", "solo_preview")}
    tema.update(cambios)
    return {"tema": tema, "formato": "wav"}


@pytest.mark.parametrize("pedido", [
    {},
    {"tema": "B WITH U"},
    {"tema": None},
    cuerpo(titulo=""), cuerpo(titulo="   "), cuerpo(titulo=None), cuerpo(titulo=123), cuerpo(artista=None),
    cuerpo(artista=["SPÆCE"]), cuerpo(titulo="x" * 301), cuerpo(artista="x" * 301),
    cuerpo(video_id=""), cuerpo(video_id="abc"), cuerpo(video_id="0"), cuerpo(video_id="1" * 21),
    cuerpo(video_id="123/../456"), cuerpo(video_id="١٢٣"),
    cuerpo(url=None), cuerpo(url="http://soundcloud.com/spaecesound/b-with-u"),
    cuerpo(url="https://soundcloud.com.evil.example/a/b"), cuerpo(url="https://soundcloud.com/spaecesound"),
    cuerpo(url="https://soundcloud.com/a/b?secret_token=s-123"), cuerpo(url="https://www.youtube.com/watch?v=x"),
    cuerpo(url="https://api.soundcloud.com/tracks/abc"),
])
def test_endpoint_pedido_invalido_da_400_sin_salir_a_la_red(env, client, pedido):
    r = client.post("/api/versiones", json=pedido)
    assert r.status_code == 400, r.text
    assert r.json()["exito"] is False and r.json()["motivo"] == "pedido_invalido"
    assert env.fuentes.pedidos == [] and env.http.pedidos == []


@pytest.mark.parametrize("pedido", [["no", "es", "un", "objeto"], "texto", 5])
def test_endpoint_cuerpo_que_no_es_objeto_no_es_un_500(env, client, pedido):
    r = client.post("/api/versiones", json=pedido)
    assert 400 <= r.status_code < 500
    assert env.fuentes.pedidos == []


@pytest.mark.parametrize("dur", ["abc", float("inf"), -1, {"x": 1}, [1], None, 1e400])
def test_endpoint_duracion_rara_no_es_un_500(env, client, dur):
    pedido = cuerpo()
    pedido["tema"]["duracion"] = dur
    r = client.post("/api/versiones", content=json.dumps(pedido, allow_nan=True),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["opciones"][0]["duracion"] is None


def test_endpoint_con_un_bug_adentro_no_es_un_500(env, client, monkeypatch):
    monkeypatch.setattr(env.server, "_versiones_de", lambda *a: 1 / 0)
    r = client.post("/api/versiones", json=cuerpo())
    assert r.status_code == 200
    d = r.json()
    assert (d["exito"], d["fallidas"], d["motivo"]) == (True, [], "No pude buscar versiones de este tema (error del servidor)")
    assert [(o["fuente"], o["video_id"], o["estacion"]) for o in d["opciones"]] == [("soundcloud", B_WITH_U["video_id"], True)]


def test_endpoint_no_devuelve_campos_de_mas(env, client):
    pedido = cuerpo(fuente="youtube", secreto="no-vuelve", estacion=False, calidad={"grade": "A", "ok": True},
                    evidencia="isrc", permalink="https://evil.example/a/b", titulo="  B WITH U  ")
    pedido["extra"] = "tampoco"
    d = client.post("/api/versiones", json=pedido).json()
    assert set(d) == {"exito", "opciones", "motivo", "fallidas"}
    base = d["opciones"][0]
    assert set(base) == {"titulo", "artista", "duracion", "url", "fuente", "video_id", "thumbnail", "permalink",
                         "reproducible", "solo_preview", "estacion", "calidad"}
    assert (base["fuente"], base["titulo"], base["permalink"], base["estacion"]) == ("soundcloud", "B WITH U", None, True)
    assert (base["calidad"]["ok"], base["calidad"]["grade"]) == (False, "?"), "la nota la calcula el server, no la manda el cliente"


def test_endpoint_booleanos_estrictos(env, client):
    # Solo `false` literal apaga reproducible y solo `true` literal marca un preview.
    d = client.post("/api/versiones", json=cuerpo(reproducible="false", solo_preview="true")).json()
    assert (d["opciones"][0]["reproducible"], d["opciones"][0]["solo_preview"]) == (True, False)


# --- las respuestas que usa el E2E -------------------------------------------------------------------

def _escenario_e2e(env):
    env.usar_http(FakeHttp(alias={"fblmanny from the top": "IMMINENT From The Top"}))
    env.resultados(
        youtube={Q_B: [YT_OTRO_TEMA, YT_B, YT_REMIX],
                 "narcx take that": [yt("NARCX - Take Me Higher", "NARCX", 227, "narcx000001")],
                 Q_DAFT: [yt("Daft Punk - One More Time (Official Video)", "Daft Punk", 320, "daftpunk001"),
                          yt("Daft Punk - One More Time (Matroda Remix)", "MATRODA", 184, "daftpunk002")],
                 "fblmanny from the top": [yt("Crappy Banjos - From The Top", "Crappy Banjos", 147, "banjos00001")]},
        ligaudio={Q_B: [LIG_B]}, hitplayer={Q_B: [HIT_B]},
        spotify={Q_B: [SP_B], Q_DAFT: [spotify("One More Time", "Daft Punk", 320, "0daftpunkspotify000000")],
                 "narcx take that": [spotify("Patience", "Take That", 202, "0takethatspotify000000")]})
    # Notas distintas por opción: la mejor NO es la primera de la fila (así el E2E ve que la
    # elegida por defecto sale de la nota y no de la posición).
    env.notas.por_url = {YT_B["url"]: "B", B_WITH_U["url"]: "C", LIG_B["url"]: "A", HIT_B["url"]: "B",
                         TAKE_THAT["url"]: "B", "https://www.youtube.com/watch?v=daftpunk001": "B",
                         sc_track(199428706)["url"]: "A"}
    temas = [B_WITH_U, TAKE_THAT, DAFT_GO, FBL_GO]
    return {t["video_id"]: t for t in temas}


def test_las_respuestas_que_usa_el_e2e_son_las_del_endpoint(env, client):
    temas = _escenario_e2e(env)
    reales = {}
    for vid, t in temas.items():
        pedido = {"tema": {k: t.get(k) for k in ("titulo", "artista", "duracion", "video_id", "url", "permalink",
                                                   "thumbnail", "reproducible", "solo_preview")}, "formato": "wav"}
        r = client.post("/api/versiones", json=pedido)
        assert r.status_code == 200
        reales[vid] = {"tema": pedido["tema"], "respuesta": r.json()}
    if os.environ.get("MUSIFLIX_REGRABAR_VERSIONES") == "1":
        RESPUESTAS_E2E.write_text(json.dumps(reales, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    esperado = json.loads(RESPUESTAS_E2E.read_text(encoding="utf-8"))
    assert reales == esperado, "el front (y el E2E) dependen de esta forma exacta"
    # Lo que el E2E da por sentado de cada tema, dicho acá en palabras:
    b = esperado[B_WITH_U["video_id"]]["respuesta"]
    assert [o["fuente"] for o in b["opciones"]] == ["youtube", "soundcloud", "spotify", "ligaudio", "hitplayer"]
    assert [o["calidad"]["grade"] for o in b["opciones"] if o["fuente"] != "spotify"] == ["B", "C", "A", "B"]
    t = esperado[TAKE_THAT["video_id"]]["respuesta"]
    assert len(t["opciones"]) == 1 and t["motivo"].startswith("No lo encontré")
    d = esperado[DAFT_GO["video_id"]]["respuesta"]
    assert [(o["fuente"], o.get("solo_preview")) for o in d["opciones"]] == \
        [("youtube", None), ("soundcloud", True), ("soundcloud", False), ("spotify", None)]
    f = esperado[FBL_GO["video_id"]]["respuesta"]
    assert [o.get("solo_preview") for o in f["opciones"]] == [True] and f["motivo"].startswith("No lo encontré")
    assert CID not in RESPUESTAS_E2E.read_text(encoding="utf-8")
