"""Tests de tagger._titulo_sin_artista (#5.35).

Módulo de raíz (capa de adquisición), igual que test_bot.py. OJO: la raíz NO está en
`testpaths` de pyproject, así que `pytest -q` (que usa testpaths) NO lo colecta; hay que
correrlo apuntándolo: `pytest test_tagger.py`.
"""
from tagger import _titulo_sin_artista


def test_recorta_artista_duplicado_exacto():
    # Caso real: el título arranca con el mismo TPE1 → se recorta (no duplicar en iTunes).
    assert _titulo_sin_artista(
        "BabaBass3000, Pueblo Gelb - Loose my Mind KMA", "BabaBass3000, Pueblo Gelb"
    ) == "Loose my Mind KMA"
    assert _titulo_sin_artista("Boltcore - Track X", "Boltcore") == "Track X"


def test_preserva_cuando_el_titulo_aporta_algo():
    # Otro artista en el prefijo → no matchea → se respeta.
    assert _titulo_sin_artista("JØR - SWITCH (master)", "Otro") == "JØR - SWITCH (master)"
    # Separador sin espacios ('kylian-dictador') → no es '<artista> - ' → intacto.
    assert (_titulo_sin_artista("kylian-dictador (Video Request #012)", "kylian")
            == "kylian-dictador (Video Request #012)")


def test_nunca_deja_el_titulo_vacio():
    # Si tras recortar no queda nada, se deja el título original.
    assert _titulo_sin_artista("Artista - ", "Artista") == "Artista - "


def test_entradas_vacias_no_rompen():
    assert _titulo_sin_artista(None, "X") is None
    assert _titulo_sin_artista("Solo Titulo", None) == "Solo Titulo"
    assert _titulo_sin_artista("Solo Titulo", "") == "Solo Titulo"
