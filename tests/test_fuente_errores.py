"""Una fuente que no pudo buscar lo SEÑALA (f40): `search_agent.buscar_en_*` y
`scrapers.buscar_*` lanzan `FuenteCaida` / `FuenteNoConfigurada` en vez de devolver [].

Antes se tragaban la excepción y devolvían [], lo mismo que "no lo tiene": la Station decía
"No lo encontré en otras plataformas" con la plataforma caída o sin configurar (auditoría f40).
Sin red: yt-dlp, spotipy y requests son dobles con la forma de lo que devuelven de verdad."""
import pytest
import requests
import scrapers
import search_agent
from fuente_errores import FuenteCaida, FuenteError, FuenteNoConfigurada
from search_agent import SearchAgent


def agente(spotify_client=None):
    a = SearchAgent.__new__(SearchAgent)     # sin conectar a Spotify
    a.spotify_client = spotify_client
    return a


def ydl_que(resultado=None, error=None):
    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            if error:
                raise error
            return resultado
    return FakeYDL


# --- Spotify ------------------------------------------------------------------------------------

def test_spotify_sin_credenciales_es_no_configurada_no_vacio():
    with pytest.raises(FuenteNoConfigurada, match="Spotify sin credenciales"):
        agente().buscar_en_spotify("x")


def test_spotify_que_falla_es_caida():
    class Cliente:
        def search(self, **kw):
            raise requests.ConnectionError("sin red")
    with pytest.raises(FuenteCaida, match="Spotify: ConnectionError"):
        agente(Cliente()).buscar_en_spotify("x")


def test_spotify_que_contesta_sin_resultados_es_vacio():
    class Cliente:
        def search(self, **kw):
            return {"tracks": {"items": []}}
    assert agente(Cliente()).buscar_en_spotify("x") == []


# --- YouTube y SoundCloud (yt-dlp) ---------------------------------------------------------------

@pytest.fixture
def con_ytdlp():
    if not search_agent.YTDLP_AVAILABLE:
        pytest.skip("sin yt-dlp")


def test_youtube_con_ignoreerrors_que_devuelve_none_es_caida(con_ytdlp, monkeypatch):
    # Con `ignoreerrors` una búsqueda que falla entera no lanza: yt-dlp devuelve None.
    monkeypatch.setattr(search_agent.yt_dlp, "YoutubeDL", ydl_que(resultado=None))
    with pytest.raises(FuenteCaida, match="YouTube no devolvió la página de resultados"):
        agente().buscar_en_youtube("x")


def test_youtube_que_lanza_es_caida(con_ytdlp, monkeypatch):
    monkeypatch.setattr(search_agent.yt_dlp, "YoutubeDL", ydl_que(error=OSError("sin red")))
    with pytest.raises(FuenteCaida, match="YouTube: OSError"):
        agente().buscar_en_youtube("x")


def test_youtube_sin_resultados_es_vacio_no_caida(con_ytdlp, monkeypatch):
    monkeypatch.setattr(search_agent.yt_dlp, "YoutubeDL", ydl_que(resultado={"entries": []}))
    assert agente().buscar_en_youtube("x") == []


def test_soundcloud_que_lanza_es_caida(con_ytdlp, monkeypatch):
    monkeypatch.setattr(search_agent.yt_dlp, "YoutubeDL", ydl_que(error=OSError("sin red")))
    with pytest.raises(FuenteCaida, match="SoundCloud: OSError"):
        agente().buscar_en_soundcloud("x")


def test_sin_ytdlp_es_no_configurada(monkeypatch):
    monkeypatch.setattr(search_agent, "YTDLP_AVAILABLE", False)
    with pytest.raises(FuenteNoConfigurada):
        agente().buscar_en_youtube("x")
    with pytest.raises(FuenteNoConfigurada):
        agente().buscar_en_soundcloud("x")


def test_el_fallback_sigue_con_las_que_contestan(con_ytdlp, monkeypatch):
    # `buscar_con_fallback` es "lo que haya": Spotify sin configurar y SoundCloud caído no cortan
    # la búsqueda; YouTube contesta y eso vuelve.
    entrada = {"id": "abc123def45", "title": "Daft Punk - One More Time", "uploader": "Daft Punk", "duration": 320}

    class YDL(ydl_que()):
        def extract_info(self, url, download=False):
            if url.startswith("scsearch"):
                raise OSError("sin red")
            return {"entries": [entrada]}
    monkeypatch.setattr(search_agent.yt_dlp, "YoutubeDL", YDL)
    r = agente().buscar_con_fallback("daft punk one more time", limit=3)
    assert [(c["fuente"], c["video_id"]) for c in r] == [("youtube", "abc123def45")]


# --- MP3 directos (scrapers) --------------------------------------------------------------------

@pytest.mark.parametrize("buscar, nombre", [(scrapers.buscar_ligaudio, "ligaudio"), (scrapers.buscar_hitplayer, "hitplayer")])
def test_scraper_sin_red_es_caida(monkeypatch, buscar, nombre):
    def sin_red(*a, **k):
        raise requests.ConnectionError("sin red")
    monkeypatch.setattr(scrapers.requests, "get", sin_red)
    with pytest.raises(FuenteCaida, match=f"{nombre}: ConnectionError"):
        buscar("x")


@pytest.mark.parametrize("buscar", [scrapers.buscar_ligaudio, scrapers.buscar_hitplayer])
def test_scraper_con_pagina_sin_resultados_es_vacio(monkeypatch, buscar):
    # Medido (2026-09-30): los dos sitios contestan 200 con una página sin items para una
    # búsqueda sin resultados; eso es "no lo tiene", no una caída.
    class Resp:
        text = "<html><body>nada</body></html>"

        def raise_for_status(self):
            pass
    monkeypatch.setattr(scrapers.requests, "get", lambda *a, **k: Resp())
    assert buscar("x") == []


def test_las_dos_son_fuente_error():
    # El que solo necesita "no pudo buscar" (descargar, Spek) atrapa FuenteError y cubre las dos.
    assert issubclass(FuenteCaida, FuenteError) and issubclass(FuenteNoConfigurada, FuenteError)
    assert not issubclass(FuenteNoConfigurada, FuenteCaida), "sin configurar no es una caída"
