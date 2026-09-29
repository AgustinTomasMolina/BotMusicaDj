"""Parecidas más rápidas sin cambiar QUÉ devuelven (f32).

Medido con red real: /api/parecidas_lista tardaba 148-193 s. Se fue el tiempo en (1) los
pedidos a Deezer en serie, (2) la búsqueda de YouTube que abría cada video, y (3) un tope
por fuente que no cortaba (se descartaba YouTube por "timeout" y además se lo esperaba). Estos
tests fijan que los arreglos no cambian el resultado y que el tope corta de verdad. Sin red:
Deezer, yt-dlp y las fuentes están simulados.
"""
import datetime
import importlib
import random
import sys
import threading
import time
from urllib.parse import quote

import pytest
import search_agent
import similares

HOY = datetime.date.today().isoformat()


# --- construir_playlist: en paralelo, mismo resultado que en serie ------------------------

def catalogo():
    """Deezer de juguete: semilla del artista 1, 4 related, 2 álbumes recientes por artista y
    3 tracks por álbum. Hay temas repetidos entre álbumes/artistas (mismo artista + título):
    el dedup se queda con el PRIMERO en orden artista → álbum → track, y eso es lo que un
    armado en paralelo mal hecho (por orden de llegada) rompe."""
    D = similares.DEEZER
    r = {}
    seed = {"id": 1000, "title": "Semilla", "artist": {"id": 1, "name": "A1"}, "album": {"id": 9000},
            "preview": "https://cdnt-preview.dzcdn.net/semilla.mp3?hdnea=x"}
    # La semilla se busca con la consulta estricta (f33) y se verifica: mismo artista y título.
    q_seed = quote('artist:"A1" track:"semilla"')
    r[f"{D}/search?q={q_seed}&limit=10"] = {"data": [seed]}
    r[f"{D}/album/9000"] = {"genres": {"data": [{"name": "Techno"}]}}
    r[f"{D}/track/1000"] = {"bpm": 130}
    artistas = [1, 2, 3, 4, 5]          # 5 × 5 temas únicos + 5 hits = 30 (el tope de candidatos)
    r[f"{D}/artist/1/related?limit=12"] = {"data": [{"id": a} for a in artistas[1:]]}
    tid = 1
    for a in artistas:
        albs = []
        for k in range(2):
            alb = a * 100 + k
            albs.append({"id": alb, "release_date": HOY, "cover_medium": f"c{alb}"})
            tracks = []
            for j in range(3):
                # El título se repite entre los dos álbumes del mismo artista (j == 0): dedup.
                titulo = f"Tema {a}-{j}" if j == 0 else f"Tema {a}-{k}-{j}"
                nombre = f"A{a}"
                # Una colaboración que sale en un álbum del artista 2 Y en uno del 4, acreditada
                # a A2 en los dos: el dedup entre ARTISTAS tiene que quedarse con la del 2.
                if a in (2, 4) and k == 1 and j == 2:
                    titulo, nombre = "Colab", "A2"
                tracks.append({"id": tid, "title": titulo, "artist": {"name": nombre},
                               "preview": f"https://cdnt-preview.dzcdn.net/{tid}.mp3?hdnea=y", "duration": 200 + tid})
                tid += 1
            r[f"{D}/album/{alb}"] = {"genres": {"data": [{"name": "Techno"}]}, "tracks": {"data": tracks}}
        r[f"{D}/artist/{a}/albums?limit=25"] = {"data": albs}
        r[f"{D}/artist/{a}/top?limit=3"] = {"data": [
            {"id": 5000 + a, "title": f"Hit {a}", "artist": {"name": f"A{a}"},
             "preview": f"https://cdnt-preview.dzcdn.net/h{a}.mp3", "album": {"cover_medium": "h"}}]}
    return r


def armar(monkeypatch, workers, demora):
    cat = catalogo()
    rnd = random.Random(7)
    lock = threading.Lock()

    def fake_get(url):
        with lock:
            espera = rnd.random() * demora
        time.sleep(espera)                      # contestan en cualquier orden
        return cat.get(url, {})

    def fake_preview(url, dur=30):
        # Determinista por track: BPM y key salen del id (no se inventan para un test real:
        # acá solo importa que el puntaje dependa del track, no del orden de llegada).
        n = int(url.rsplit("/", 1)[1].split(".")[0].lstrip("h") or 0)
        return {"bpm": 120 + n % 15, "tono": "A menor", "camelot": ["8A", "9A", "3B"][n % 3]}

    monkeypatch.setattr(similares, "_get", fake_get)
    monkeypatch.setattr(similares, "_analizar_preview", fake_preview)
    monkeypatch.setattr(similares, "_DEEZER_WORKERS", workers)
    return similares.construir_playlist("Semilla", "A1", total=40, analizar_tono=False)


def test_en_paralelo_devuelve_lo_mismo_que_en_serie(monkeypatch):
    serie = armar(monkeypatch, workers=1, demora=0)
    paralelo = armar(monkeypatch, workers=10, demora=0.02)
    assert serie["exito"] and paralelo["exito"]
    titulos = [c["titulo"] for c in serie["canciones"]]
    assert "Hit 1" in titulos, "el catálogo tiene que ejercitar también el relleno con los hits"
    assert len(titulos) == len(set(zip(titulos, [c["artista"] for c in serie["canciones"]], strict=True))), "hay repetidos"
    assert [(c["titulo"], c["artista"], c["duracion"], c["thumbnail"]) for c in paralelo["canciones"]] == \
           [(c["titulo"], c["artista"], c["duracion"], c["thumbnail"]) for c in serie["canciones"]]


def test_el_dedup_se_queda_con_el_primer_album(monkeypatch):
    # "Tema 3-0" está en los álbumes 300 y 301: tiene que quedar el de 300 (primero en orden).
    res = armar(monkeypatch, workers=10, demora=0.02)
    [c] = [c for c in res["canciones"] if c["titulo"] == "Tema 3-0"]
    assert c["thumbnail"] == "c300"
    # La colaboración del artista 2 y del 4: gana la del 2 (va antes en la lista de artistas).
    [c] = [c for c in res["canciones"] if c["titulo"] == "Colab"]
    assert c["thumbnail"] == "c201", "el dedup entre artistas no respetó el orden artista → álbum"


# --- Deezer: cuota y cache -----------------------------------------------------------------

class Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_cuota_de_deezer_se_reintenta_y_no_se_cachea_el_error(monkeypatch):
    similares._GET_CACHE.clear()
    llamadas = []
    respuestas = [{"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}}, {"data": [1, 2]}]

    def fake_requests_get(url, headers=None, timeout=None):
        llamadas.append(url)
        return Resp(respuestas.pop(0))

    monkeypatch.setattr(similares.requests, "get", fake_requests_get)
    monkeypatch.setattr(similares.time, "sleep", lambda s: None)
    assert similares._get("https://api.deezer.com/x") == {"data": [1, 2]}
    assert len(llamadas) == 2, "con la cuota agotada tiene que reintentar"
    assert similares._get("https://api.deezer.com/x") == {"data": [1, 2]}
    assert len(llamadas) == 2, "la respuesta buena se cachea"


def test_la_cache_de_deezer_devuelve_copias(monkeypatch):
    similares._GET_CACHE.clear()
    monkeypatch.setattr(similares.requests, "get", lambda url, headers=None, timeout=None: Resp({"data": [{"id": 1}]}))
    for _ in range(3):                     # la 1ª va a la red; la 2ª y la 3ª salen de la cache
        a = similares._get("https://api.deezer.com/album/1")
        assert a == {"data": [{"id": 1}]}, "un pedido no puede ver lo que otro le escribió al cacheado"
        a["data"][0]["_score"] = 99        # construir_playlist escribe en los tracks


def test_analisis_de_preview_se_cachea_por_archivo_no_por_token(monkeypatch):
    similares._ANALYSIS_CACHE.clear()
    n = []
    monkeypatch.setattr(similares, "_analizar_preview_sin_cache", lambda url, dur: n.append(url) or {"bpm": 128})
    assert similares._analizar_preview("https://cdnt-preview.dzcdn.net/x.mp3?hdnea=uno", 20) == {"bpm": 128}
    assert similares._analizar_preview("https://cdnt-preview.dzcdn.net/x.mp3?hdnea=dos", 20) == {"bpm": 128}
    assert len(n) == 1, "el mismo preview con otro token no se vuelve a bajar"


# --- YouTube: búsqueda plana ----------------------------------------------------------------

def test_busqueda_de_youtube_es_plana_y_arma_la_url_del_id(monkeypatch):
    if not search_agent.YTDLP_AVAILABLE:
        pytest.skip("sin yt-dlp")
    visto = {}

    class FakeYDL:
        def __init__(self, opts):
            visto["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            # Forma real de una entrada plana (medida con yt-dlp 2026.08.19): sin webpage_url
            # ni thumbnail; uploader y channel con el canal.
            return {"entries": [
                {"id": "u31thuMehjM", "title": "FISHER - Losing It (Official Audio)", "uploader": "FISHER",
                 "channel": "FISHER", "duration": 249, "url": "https://www.youtube.com/watch?v=u31thuMehjM"},
                {"id": "abcdefghijk", "title": "Otro", "uploader": None, "channel": "Canal", "duration": None,
                 "url": "https://www.youtube.com/shorts/abcdefghijk"},
                None,
            ]}

    monkeypatch.setattr(search_agent.yt_dlp, "YoutubeDL", FakeYDL)
    agente = search_agent.SearchAgent.__new__(search_agent.SearchAgent)
    a, b = agente.buscar_en_youtube("fisher losing it", 4)
    assert visto["opts"]["extract_flat"] == "in_playlist", "sin búsqueda plana cada video se abre entero"
    assert a == {"titulo": "FISHER - Losing It (Official Audio)", "artista": "FISHER", "duracion": 249,
                 "url": "https://www.youtube.com/watch?v=u31thuMehjM", "fuente": "youtube",
                 "video_id": "u31thuMehjM", "thumbnail": "https://i.ytimg.com/vi/u31thuMehjM/hqdefault.jpg"}
    assert b["url"] == "https://www.youtube.com/watch?v=abcdefghijk", "un /shorts/ queda como watch?v="
    assert (b["artista"], b["duracion"]) == ("Canal", 0)


# --- _buscar_mix: el tope corta de verdad y lo recortado no se cachea -----------------------

@pytest.fixture(scope="module")
def server(tmp_path_factory):
    datos = tmp_path_factory.mktemp("server-datos")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MUSIFLIX_DATA_DIR", str(datos))
        mp.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
        mp.delenv("REDIS_URL", raising=False)
        mp.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        for mod in ("server", "jobs", "db"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
    return srv


def fuentes(server, monkeypatch, yt_demora):
    llamadas = []

    def yt(q, limit=10):
        llamadas.append("youtube")
        time.sleep(yt_demora)
        return [{"titulo": q, "fuente": "youtube", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}]

    def mk(nombre):
        def f(q, limit=10):
            llamadas.append(nombre)
            return [{"titulo": q, "fuente": nombre, "url": f"https://{nombre}/x.mp3"}]
        return f

    monkeypatch.setattr(server.search_agent, "buscar_en_youtube", yt)
    monkeypatch.setattr(server.search_agent, "buscar_en_spotify", lambda q, limit=10: [])
    monkeypatch.setattr(server.search_agent, "buscar_en_soundcloud", mk("soundcloud"))
    monkeypatch.setattr(server, "FUENTES_SCRAPER", [("ligaudio", mk("ligaudio")), ("hitplayer", mk("hitplayer"))])
    server._MIX_CACHE.clear()
    return llamadas


def test_una_fuente_que_cuelga_no_se_espera(server, monkeypatch):
    fuentes(server, monkeypatch, yt_demora=1.5)
    monkeypatch.setattr(server, "_SOURCE_DEADLINE_S", 0.3)
    t0 = time.perf_counter()
    mezcla = server._buscar_mix("tema x", 12)
    tardo = time.perf_counter() - t0
    assert tardo < 1.0, f"se esperó a la fuente colgada ({tardo:.2f} s) pese al tope de 0,3 s"
    assert [c["fuente"] for c in mezcla] == ["ligaudio", "hitplayer", "soundcloud"]
    assert server._MIX_CACHE == {}, "un resultado sin YouTube (por timeout) no se cachea"


def test_con_todas_las_fuentes_se_cachea_y_no_se_repite(server, monkeypatch):
    llamadas = fuentes(server, monkeypatch, yt_demora=0)
    a = server._buscar_mix("tema y", 12)
    assert [c["fuente"] for c in a] == ["youtube", "ligaudio", "hitplayer", "soundcloud"]
    n = len(llamadas)
    b = server._buscar_mix("tema y", 12)
    assert b == a and len(llamadas) == n, "la misma búsqueda no vuelve a pedir a las fuentes"
    b[0]["titulo"] = "pisado"
    assert server._buscar_mix("tema y", 12)[0]["titulo"] == "tema y", "la cache devuelve copias"
