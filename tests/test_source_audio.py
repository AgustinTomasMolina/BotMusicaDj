"""Tests de /api/fuente/audio (f32): el audio de YouTube/SoundCloud servido a la barra.

Sin red: yt-dlp y la CDN están simulados. `FakeYDL` reemplaza a `yt_dlp.YoutubeDL` (anota con
qué URL y qué opciones lo llamaron) y `FakeCDN` reemplaza a `requests.get` (anota cada pedido
y contesta con un "archivo" de bytes conocidos, respetando Range). Así se prueba lo que hace
el endpoint con lo que devuelven ellos, incluido lo que NO tiene que hacer: pedir una URL
que no sea de la fuente.

Importar `server` tiene efectos al cargar (carpeta de descargas, base, Redis, .env): el
fixture los neutraliza como en test_server_biblioteca.py.
"""
import importlib
import sys
import time

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

YT_ID = "u31thuMehjM"
YT_URL = f"https://www.youtube.com/watch?v={YT_ID}"
CDN_YT = "https://rr7---sn-abc.googlevideo.com/videoplayback?expire=9999999999&id=1"
CDN_SC = "https://cf-media.sndcdn.com/abc.128.mp3?Policy=x&Expires=9999999999"
AUDIO = bytes(range(256)) * 40          # 10240 bytes conocidos: el "archivo" de la CDN


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    datos = tmp_path_factory.mktemp("server-datos")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MUSIFLIX_DATA_DIR", str(datos))
        mp.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
        mp.delenv("REDIS_URL", raising=False)
        mp.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        for mod in ("server", "jobs", "db", "source_audio"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
    return srv


@pytest.fixture
def sa(server):
    import source_audio
    source_audio.clear_cache()
    yield source_audio
    source_audio.clear_cache()


@pytest.fixture
def client(server):
    return TestClient(server.app)


def info_yt(**extra):
    base = {"id": YT_ID, "url": CDN_YT, "ext": "webm", "acodec": "opus", "abr": 133.2,
            "protocol": "https", "format_id": "251", "duration": 249, "filesize": len(AUDIO),
            "http_headers": {"User-Agent": "UA-de-yt-dlp", "Cookie": "no-se-reenvia"}}
    base.update(extra)
    return base


class FakeYDL:
    """yt_dlp.YoutubeDL simulado. `respuestas` es una lista: cada llamada consume la primera
    (un dict = info, una excepción = se lanza)."""
    llamadas: list = []
    respuestas: list = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        FakeYDL.llamadas.append({"url": url, "opts": self.opts, "download": download})
        r = FakeYDL.respuestas.pop(0) if len(FakeYDL.respuestas) > 1 else FakeYDL.respuestas[0]
        if isinstance(r, BaseException):
            raise r
        if callable(r):
            return r()
        return r


class FakeResp:
    def __init__(self, status, headers=None, body=b""):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.cerrada = False

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        self.cerrada = True


class FakeCDN:
    pedidos: list = []
    # url → función(headers) -> FakeResp; por defecto sirve AUDIO con Range
    rutas: dict = {}

    @staticmethod
    def get(url, headers=None, stream=False, timeout=None, allow_redirects=True):
        FakeCDN.pedidos.append({"url": url, "headers": dict(headers or {}), "allow_redirects": allow_redirects})
        if url in FakeCDN.rutas:
            return FakeCDN.rutas[url](headers or {})
        return servir(headers or {})


def servir(headers):
    rng = headers.get("Range")
    if not rng:
        return FakeResp(200, {"Content-Length": str(len(AUDIO))}, AUDIO)
    a, b = rng.split("=", 1)[1].split("-")
    ini = int(a) if a else len(AUDIO) - int(b)
    fin = int(b) if (a and b) else len(AUDIO) - 1
    parte = AUDIO[ini:fin + 1]
    return FakeResp(206, {"Content-Length": str(len(parte)),
                          "Content-Range": f"bytes {ini}-{fin}/{len(AUDIO)}"}, parte)


@pytest.fixture
def fakes(monkeypatch, sa):
    import requests
    import yt_dlp
    FakeYDL.llamadas = []
    FakeYDL.respuestas = [info_yt()]
    FakeCDN.pedidos = []
    FakeCDN.rutas = {}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(requests, "get", FakeCDN.get)
    return FakeYDL, FakeCDN


# --- validación: lo que no es un id de la fuente no llega a yt-dlp ni a la red ------------

@pytest.mark.parametrize("fuente, ref", [
    ("vimeo", "123456"),
    ("", YT_ID),
    ("file", "/etc/passwd"),
    ("youtube", "https://evil.example/x"),
    ("youtube", "http://127.0.0.1:8000/api/historial"),
    ("youtube", "file:///etc/passwd"),
    ("youtube", "localhost"),
    ("youtube", f"{YT_ID}&list=PL123"),
    ("youtube", "u31thuMehj"),            # 10 caracteres
    ("youtube", "u31thuMehjM1"),          # 12
    ("youtube", "--exec calc"),
    ("soundcloud", "../../etc/passwd"),
    ("soundcloud", "0123"),
    ("soundcloud", "charlottedewittemusic/sets"),
    ("soundcloud", "charlottedewittemusic/likes"),
    ("soundcloud", "a/b/c"),
    ("soundcloud", "user/track?secret_token=1"),
    ("soundcloud", "https://soundcloud.com/a/b"),
    ("soundcloud", "127.0.0.1/x@evil"),
])
def test_ref_invalido_400_sin_llamar_a_ytdlp_ni_a_la_red(client, fakes, fuente, ref):
    r = client.get("/api/fuente/audio", params={"fuente": fuente, "ref": ref})
    assert r.status_code == 400, r.text
    assert r.json()["error"], "el 400 tiene que decir por qué"
    assert FakeYDL.llamadas == [], "un ref inválido no puede llegar a yt-dlp"
    assert FakeCDN.pedidos == [], "un ref inválido no puede generar pedidos de red"


def test_ytdlp_recibe_la_url_armada_por_el_server_y_opciones_cerradas(client, fakes):
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    assert r.status_code == 200
    [ll] = FakeYDL.llamadas
    assert ll["url"] == YT_URL
    assert ll["download"] is False
    assert ll["opts"]["noplaylist"] is True
    assert ll["opts"]["allowed_extractors"] == ["youtube", "soundcloud"]
    assert ll["opts"]["socket_timeout"] > 0


@pytest.mark.parametrize("ref, esperada", [
    ("2402420514", "https://api.soundcloud.com/tracks/2402420514"),
    ("charlottedewittemusic/the-resistance-feat-theo-1",
     "https://soundcloud.com/charlottedewittemusic/the-resistance-feat-theo-1"),
])
def test_soundcloud_por_id_o_por_usuario_tema(client, fakes, ref, esperada):
    FakeYDL.respuestas = [info_yt(url=CDN_SC, ext="mp3", acodec="mp3", format_id="http_mp3_1_0", protocol="http")]
    r = client.get("/api/fuente/audio", params={"fuente": "soundcloud", "ref": ref})
    assert r.status_code == 200, r.text
    assert FakeYDL.llamadas[0]["url"] == esperada
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == AUDIO


# --- Range: el <audio> tiene que poder adelantar ----------------------------------------

def test_range_se_reenvia_y_devuelve_206_con_los_bytes_pedidos(client, fakes):
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID},
                   headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.content == AUDIO[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(AUDIO)}"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "audio/webm"
    [p] = FakeCDN.pedidos
    assert p["url"] == CDN_YT
    assert p["headers"]["Range"] == "bytes=100-199"
    assert p["headers"]["User-Agent"] == "UA-de-yt-dlp", "la CDN se pide con los headers de yt-dlp"
    assert "Cookie" not in p["headers"], "no se reenvían cookies ni headers fuera de la lista"
    assert p["allow_redirects"] is False, "las redirecciones se siguen a mano, validando el host"


def test_range_abierto_y_sufijo(client, fakes):
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID}, headers={"Range": "bytes=10000-"})
    assert r.status_code == 206 and r.content == AUDIO[10000:]
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID}, headers={"Range": "bytes=-40"})
    assert r.status_code == 206 and r.content == AUDIO[-40:]


@pytest.mark.parametrize("rango", ["bytes=abc", "bytes=-", "items=0-10", "bytes=0-10,20-30"])
def test_range_invalido_416_sin_pedir_nada(client, fakes, rango):
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID}, headers={"Range": rango})
    assert r.status_code == 416, r.text
    assert FakeCDN.pedidos == []


def test_una_resolucion_para_varios_pedidos_del_mismo_tema(client, fakes):
    for rng in ("bytes=0-", "bytes=5000-", "bytes=9000-9999"):
        assert client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID},
                          headers={"Range": rng}).status_code == 206
    assert len(FakeYDL.llamadas) == 1, "adelantar no puede volver a resolver con yt-dlp"


def test_url_vencida_se_resuelve_de_nuevo_una_vez(client, fakes):
    nueva = "https://rr1---sn-xyz.googlevideo.com/videoplayback?expire=9999999999&id=2"
    FakeYDL.respuestas = [info_yt(), info_yt(url=nueva)]
    FakeCDN.rutas = {CDN_YT: lambda h: FakeResp(403)}
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID}, headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == AUDIO[:10]
    assert len(FakeYDL.llamadas) == 2
    assert [p["url"] for p in FakeCDN.pedidos] == [CDN_YT, nueva]


# --- SSRF: lo que devuelve yt-dlp o la CDN tampoco puede llevar a cualquier lado ------------

@pytest.mark.parametrize("directa", [
    "http://rr7---sn-abc.googlevideo.com/videoplayback",      # sin TLS
    "https://127.0.0.1/videoplayback",
    "https://localhost/x",
    "https://169.254.169.254/latest/meta-data/",
    "https://googlevideo.com.evil.example/x",
    "https://evilgooglevideo.com/x",
    "https://user:pw@rr7.googlevideo.com/x",
    "https://rr7.googlevideo.com:8080/x",
    # Diferencia de parsers: urlparse ve un host terminado en .googlevideo.com; urllib3 (el
    # que conecta) corta en la barra y se conecta a 127.0.0.1.
    "https://127.0.0.1\\.googlevideo.com/x",
    "https://evil.example%00.googlevideo.com/x",
    "https://evil.example%40.googlevideo.com/x",
    "file:///etc/passwd",
    "",
])
def test_url_directa_fuera_de_la_cdn_no_se_pide(client, fakes, directa):
    FakeYDL.respuestas = [info_yt(url=directa)]
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    assert r.status_code == 502, r.text
    assert "error" in r.json()
    assert FakeCDN.pedidos == [], f"se pidió {directa!r}, que no es de la CDN de la fuente"


@pytest.mark.parametrize("destino", [
    "http://127.0.0.1:8000/api/historial",
    "https://127.0.0.1\\.googlevideo.com/x",   # pasa urlparse, urllib3 conecta a 127.0.0.1
])
def test_redireccion_a_otro_host_no_se_sigue(client, fakes, destino):
    FakeCDN.rutas = {CDN_YT: lambda h: FakeResp(302, {"Location": destino})}
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    assert r.status_code == 502, r.text
    assert [p["url"] for p in FakeCDN.pedidos] == [CDN_YT], f"se siguió el salto a {destino!r}"


def test_redireccion_dentro_de_la_cdn_si_se_sigue(client, fakes):
    otro = "https://rr3---sn-q.googlevideo.com/videoplayback?id=3"
    FakeCDN.rutas = {CDN_YT: lambda h: FakeResp(302, {"Location": otro})}
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID}, headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == AUDIO[:4]
    assert [p["url"] for p in FakeCDN.pedidos] == [CDN_YT, otro]


def test_una_lista_no_es_un_tema(client, fakes):
    FakeYDL.respuestas = [{"_type": "playlist", "entries": [info_yt()]}]
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    assert r.status_code == 400
    assert FakeCDN.pedidos == []


def test_hls_no_se_sirve(client, fakes):
    FakeYDL.respuestas = [info_yt(protocol="m3u8_native")]
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    assert r.status_code == 502 and FakeCDN.pedidos == []


def test_tema_demasiado_largo_413(client, fakes):
    FakeYDL.respuestas = [info_yt(duration=4 * 3600)]
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    assert r.status_code == 413 and FakeCDN.pedidos == []


# --- errores: con motivo, nunca 500 -------------------------------------------------------

@pytest.mark.parametrize("mensaje, status, contiene", [
    ("ERROR: [youtube] u31thuMehjM: Private video. Sign in if you've been granted access", 404, "privado"),
    ("ERROR: [youtube] u31thuMehjM: Video unavailable", 404, "no está disponible"),
    ("ERROR: [youtube] u31thuMehjM: Sign in to confirm your age", 403, "iniciar sesión"),
    ("ERROR: [youtube] u31thuMehjM: Requested format is not available", 502, "no ofrece un audio"),
    ("ERROR: Unable to download API page: timed out", 504, "no contestó"),
    ("algo que nadie previó", 502, "No pude sacar el audio"),
])
def test_errores_de_ytdlp_con_motivo_y_sin_500(client, fakes, mensaje, status, contiene):
    import yt_dlp.utils
    FakeYDL.respuestas = [yt_dlp.utils.DownloadError(mensaje)]
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    assert r.status_code == status, r.text
    assert contiene in r.json()["error"]


def test_excepcion_cualquiera_de_ytdlp_no_es_500(client, fakes):
    FakeYDL.respuestas = [ValueError("boom")]
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    assert r.status_code == 502 and r.json()["error"]


def test_el_fallo_se_recuerda_y_el_motivo_no_vuelve_a_resolver(client, fakes):
    import yt_dlp.utils
    FakeYDL.respuestas = [yt_dlp.utils.DownloadError("Video unavailable")]
    a = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    b = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID}, headers={"Range": "bytes=0-0"})
    assert a.status_code == b.status_code == 404
    assert b.json() == a.json()
    assert len(FakeYDL.llamadas) == 1, "preguntar el motivo no puede repetir la resolución"


def test_ytdlp_que_cuelga_termina_en_504(server, fakes, sa, monkeypatch):
    monkeypatch.setattr(sa, "RESOLVE_TIMEOUT_S", 0.3)
    monkeypatch.setenv("MUSIFLIX_SIN_CALENTAR", "1")     # el arranque no calienta librosa acá
    FakeYDL.respuestas = [lambda: (time.sleep(1.5), info_yt())[1]]
    # Con `with`: el loop sigue vivo entre pedidos, como en uvicorn. Sin él, el TestClient
    # cierra el loop al final del pedido y espera al hilo de yt-dlp, y eso no es el endpoint.
    with TestClient(server.app) as c:
        t0 = time.perf_counter()
        r = c.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
        tardo = time.perf_counter() - t0
    assert r.status_code == 504, r.text
    assert tardo < 1.2, f"el endpoint esperó a yt-dlp ({tardo:.2f} s) en vez de cortar en el tope"


def test_cdn_sin_respuesta_504(client, fakes):
    import requests

    def cae(h):
        raise requests.ConnectionError("sin red")
    FakeCDN.rutas = {CDN_YT: cae}
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    assert r.status_code == 504 and r.json()["error"]


def test_cdn_con_error_502_con_el_codigo(client, fakes):
    FakeCDN.rutas = {CDN_YT: lambda h: FakeResp(500)}
    r = client.get("/api/fuente/audio", params={"fuente": "youtube", "ref": YT_ID})
    assert r.status_code == 502
    assert "HTTP 500" in r.json()["error"]


# --- /info: la barra no miente sobre lo que suena -----------------------------------------

def test_info_dice_si_soundcloud_da_solo_un_fragmento(client, fakes):
    FakeYDL.respuestas = [info_yt(url="https://cf-preview-media.sndcdn.com/p.128.mp3?Expires=9999999999",
                                  ext="mp3", acodec="mp3", abr=128, format_id="http_mp3_1_0_preview",
                                  protocol="http", duration=30.0)]
    r = client.get("/api/fuente/audio/info", params={"fuente": "soundcloud", "ref": "2389204287"})
    assert r.status_code == 200
    assert r.json() == {"fuente": "soundcloud", "preview": True, "duracion": 30.0, "ext": "mp3",
                        "codec": "mp3", "abr": 128}


def test_info_tema_entero_no_es_fragmento(client, fakes):
    r = client.get("/api/fuente/audio/info", params={"fuente": "youtube", "ref": YT_ID})
    assert r.json()["preview"] is False
    assert r.json()["duracion"] == 249.0


def test_info_valida_igual_que_el_audio(client, fakes):
    r = client.get("/api/fuente/audio/info", params={"fuente": "youtube", "ref": "http://127.0.0.1/"})
    assert r.status_code == 400 and FakeYDL.llamadas == []
