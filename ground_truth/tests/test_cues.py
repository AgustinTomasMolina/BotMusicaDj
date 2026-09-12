"""Tests de la lógica de análisis de cues (#5.4 prep).

Inputs construidos a mano para probar la lógica; no se inventa ground truth (las posiciones
son sintéticas para ejercitar los límites, no valores reales de la biblioteca)."""
from ground_truth.cues import _coincide_con_otro, _es_memory_sin_nombre, _zona


def _cue(start=0.0, type="4", num="0", name=""):
    return {"start": start, "type": type, "num": num, "name": name}


def test_zona_limites():
    assert _zona(0.10) == "inicio"
    assert _zona(0.14) == "inicio"
    assert _zona(0.15) == "medio"       # 0.15 ya NO es inicio (umbral estricto <0.15)
    assert _zona(0.50) == "medio"
    assert _zona(0.85) == "medio"       # 0.85 todavía es medio (final es >0.85)
    assert _zona(0.86) == "final"
    assert _zona(0.99) == "final"


def test_memory_sin_nombre():
    assert _es_memory_sin_nombre(_cue(type="4", num="0", name="")) is True
    assert _es_memory_sin_nombre(_cue(type="0", num="0", name="")) is False   # hot cue
    assert _es_memory_sin_nombre(_cue(type="4", num="6", name="")) is False   # slot con Num
    assert _es_memory_sin_nombre(_cue(type="4", num="0", name="1.1Bars")) is False  # con nombre


def test_coincide_con_otro():
    m = _cue(start=10.0)
    assert _coincide_con_otro(m, [_cue(start=10.5)]) is True     # dentro de ±1s
    assert _coincide_con_otro(m, [_cue(start=12.0)]) is False    # a 2s, no coincide
    assert _coincide_con_otro(m, []) is False                    # no hay otros
    assert _coincide_con_otro(m, [_cue(start=11.0)]) is True     # justo en el borde (±1s)
