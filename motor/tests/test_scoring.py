"""Tests del scoring multiplicativo (lógica pura, sin audio)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from motor.scoring import bpm_score, mezclabilidad, score  # noqa: E402


def test_bpm_es_compuerta():
    assert bpm_score(150, 150) == 1.0
    assert 0.0 < bpm_score(150, 153) < 1.0          # dentro de ±8%
    assert bpm_score(150, 200) == 0.0               # fuera de ±8% → compuerta dura
    assert bpm_score(150, 75) == 1.0                # half-time se considera mezclable


def test_bpm_score_es_simetrico():
    # 'Fuera de ±8%' no puede depender de cuál track va primero (Fable I3).
    assert bpm_score(150, 160) == bpm_score(160, 150)
    assert bpm_score(150, 138) == bpm_score(138, 150)
    # NaN / BPM inválido no evade la compuerta.
    assert bpm_score(float("nan"), 150) == 0.0
    assert bpm_score(0, 150) == 0.0


def test_mezclabilidad():
    assert mezclabilidad(150, 150, "8A", "8A") == 1.0        # perfecto
    assert mezclabilidad(150, 200, "8A", "8A") == 0.0        # BPM fuera → 0 aunque la key sea perfecta
    media = mezclabilidad(150, 150, "8A", "3B")             # BPM ok, key lejana
    assert 0.2 < media < 0.6                                 # baja pero NO se anula


def test_scoring_es_multiplicativo():
    # Un encaje altísimo NO compensa estar fuera de tempo (la clave de multiplicar vs sumar).
    assert score(0.95, 150, 200, "8A", "8A") == 0.0
    # Con todo alineado, el score es el encaje.
    assert abs(score(0.9, 150, 150, "8A", "8A") - 0.9) < 1e-9


if __name__ == "__main__":
    test_bpm_es_compuerta()
    test_bpm_score_es_simetrico()
    test_mezclabilidad()
    test_scoring_es_multiplicativo()
    print("OK — tests de scoring pasaron")
