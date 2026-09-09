"""Tests de la resolución de rutas del ground truth.

Usan archivos temporales de verdad (no audio: al resolver solo importa la ruta).
"""
from ground_truth.resolver import (
    AMBIGUO,
    NO_ENCONTRADO,
    OK,
    construir_indice,
    resolver,
)


def _tocar(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_ruta_original_si_existe(tmp_path):
    f = _tocar(tmp_path / "crate" / "track.wav")
    r = resolver(str(f), [str(tmp_path)])
    assert r.estado == OK and r.ruta == str(f)


def test_reubica_por_ancla(tmp_path):
    """.../Escritorio/GROOOOVE/x.wav → <raíz>/GROOOOVE/x.wav"""
    destino = _tocar(tmp_path / "GROOOOVE" / "x.wav")
    vieja = "C:/Users/otro/OneDrive/Escritorio/GROOOOVE/x.wav"
    r = resolver(vieja, [str(tmp_path)])
    assert r.estado == OK and r.ruta == str(destino)


def test_basename_unico_resuelve(tmp_path):
    destino = _tocar(tmp_path / "sub" / "unico.wav")
    r = resolver("D:/viejo/unico.wav", [str(tmp_path)], construir_indice([str(tmp_path)]))
    assert r.estado == OK and r.ruta == str(destino)


def test_basename_homonimo_es_ambiguo(tmp_path):
    """Original vs edit con el mismo nombre: no se elige, se excluye."""
    _tocar(tmp_path / "originales" / "track.wav")
    _tocar(tmp_path / "edits" / "track.wav")
    r = resolver("D:/viejo/track.wav", [str(tmp_path)], construir_indice([str(tmp_path)]))
    assert r.estado == AMBIGUO
    assert r.ruta is None                 # no se resuelve a la suerte
    assert len(r.candidatos) == 2
    assert not r                          # __bool__ es False: no entra al cómputo


def test_sin_candidatos_es_no_encontrado(tmp_path):
    r = resolver("D:/viejo/no_esta.wav", [str(tmp_path)], construir_indice([str(tmp_path)]))
    assert r.estado == NO_ENCONTRADO and r.ruta is None


def test_location_vacio(tmp_path):
    assert resolver("", [str(tmp_path)]).estado == NO_ENCONTRADO


def test_el_ancla_gana_sobre_el_homonimo(tmp_path):
    """Si el ancla da una ruta concreta, no se cae al índice aunque haya homónimos."""
    exacto = _tocar(tmp_path / "GROOOOVE" / "track.wav")
    _tocar(tmp_path / "otra" / "track.wav")
    r = resolver("C:/x/Escritorio/GROOOOVE/track.wav", [str(tmp_path)],
                 construir_indice([str(tmp_path)]))
    assert r.estado == OK and r.ruta == str(exacto)
