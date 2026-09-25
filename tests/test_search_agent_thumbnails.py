"""Carátulas de SoundCloud en la búsqueda.

Con `extract_flat` yt-dlp deja `thumbnail` en None y la carátula viene solo en la lista
`thumbnails`. La búsqueda leía solo `thumbnail`, así que TODOS los resultados de SoundCloud
llegaban al front sin imagen (medido contra yt-dlp 2026.08.19 con `scsearch`). Las entradas
de acá copian la forma real que devuelve yt-dlp (ids y tamaños de SoundCloud).
"""
import pytest
import search_agent
from search_agent import SearchAgent, _thumbnail_soundcloud

BASE = "https://i1.sndcdn.com/artworks-000373455918-gmn9w4-"
THUMBS = [{"id": i, "url": f"{BASE}{i}.jpg", "width": w, "height": w} for i, w in
          (("mini", 16), ("tiny", 20), ("small", 32), ("badge", 47), ("t67x67", 67),
           ("large", 100), ("t300x300", 300), ("crop", 400), ("t500x500", 500))] + \
         [{"id": "original", "url": f"{BASE}original.jpg"}]


def test_prefiere_t500_de_la_lista_cuando_thumbnail_es_none():
    assert _thumbnail_soundcloud({"thumbnail": None, "thumbnails": THUMBS}) == f"{BASE}t500x500.jpg"


def test_sin_t500_usa_t300_y_sin_ninguno_el_mas_ancho():
    sin_500 = [t for t in THUMBS if t["id"] != "t500x500"]
    assert _thumbnail_soundcloud({"thumbnails": sin_500}) == f"{BASE}t300x300.jpg"
    raros = [{"id": "a", "url": "https://x/a.jpg", "width": 80},
             {"id": "b", "url": "https://x/b.jpg", "width": 120},
             {"id": "c", "url": "https://x/c.jpg"}]
    assert _thumbnail_soundcloud({"thumbnails": raros}) == "https://x/b.jpg"


def test_respeta_thumbnail_si_viene_y_sin_nada_es_none():
    assert _thumbnail_soundcloud({"thumbnail": "https://x/t.jpg", "thumbnails": THUMBS}) == "https://x/t.jpg"
    assert _thumbnail_soundcloud({"thumbnail": None}) is None
    assert _thumbnail_soundcloud({"thumbnails": [{"id": "x"}]}) is None, "una entrada sin url no es carátula"


def test_buscar_en_soundcloud_manda_la_caratula(monkeypatch):
    if not search_agent.YTDLP_AVAILABLE:
        pytest.skip("sin yt-dlp")
    entrada = {"id": "471251166", "title": "Losing It", "uploader": "FISHER", "duration": 245,
               "url": "https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A471251166",
               "thumbnail": None, "thumbnails": THUMBS}

    class FakeYDL:
        def __init__(self, opts):
            assert opts.get("extract_flat") == "in_playlist"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"entries": [entrada]}

    monkeypatch.setattr(search_agent.yt_dlp, "YoutubeDL", FakeYDL)
    agente = SearchAgent.__new__(SearchAgent)   # sin conectar a Spotify
    [c] = agente.buscar_en_soundcloud("fisher losing it", 1)
    assert c["thumbnail"] == f"{BASE}t500x500.jpg"
    assert (c["video_id"], c["fuente"]) == ("471251166", "soundcloud")
