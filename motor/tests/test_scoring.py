"""Tests del scoring multiplicativo (lógica pura, sin audio)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402

from motor.scoring import _dist_bpm_relativa, bpm_score, mezclabilidad, score  # noqa: E402


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


def test_distancia_simetrica_tambien_en_octava():
    """`dist(a, b) == dist(b, a)` para pares normales Y de medio/doble tiempo. La versión
    con denominador `max(a, b)` sin transformar era simétrica solo en los normales: en
    octava 100→220 medía 4.55% y 220→100 medía 9.09%."""
    pares = [(150, 160), (150, 138), (128, 131.5), (100, 220), (80, 174), (75, 150),
             (140, 72), (174, 90), (60, 128), (95.3, 187.1)]
    for a, b in pares:
        assert _dist_bpm_relativa(a, b) == pytest.approx(_dist_bpm_relativa(b, a), abs=1e-15), \
            f"{a}→{b} mide {_dist_bpm_relativa(a, b):.5f} y {b}→{a} {_dist_bpm_relativa(b, a):.5f}"


def test_octava_fuera_de_tolerancia_se_corta_en_los_dos_sentidos():
    """Los tres casos de la auditoría. El pitch real sale a mano: 100 contra 220 se mezcla
    como 100 contra 110 (medio tiempo), y 110/100 es un 10% más rápido → 1 - 100/110 = 9.09%.
    80 contra 174 es 80 contra 87 → 1 - 80/87 = 8.05%. Los dos, fuera de ±8%."""
    casos = [(100.0, 220.0, 1 - 100 / 110), (80.0, 174.0, 1 - 80 / 87)]
    for a, b, real in casos:
        for x, y in ((a, b), (b, a)):
            assert _dist_bpm_relativa(x, y) == pytest.approx(real, abs=1e-12), \
                f"{x}→{y}: la distancia no es el pitch real {real:.4%}"
            assert bpm_score(x, y) == 0.0, f"{x}→{y} tiene pitch real {real:.2%} y entró"
    # Y lo que sí mezcla en octava sigue entrando: 75 contra 152 es 76 contra 75 (1.3%).
    assert bpm_score(75, 152) > 0.0 and bpm_score(152, 75) > 0.0


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
    test_distancia_simetrica_tambien_en_octava()
    test_octava_fuera_de_tolerancia_se_corta_en_los_dos_sentidos()
    test_mezclabilidad()
    test_scoring_es_multiplicativo()
    print("OK — tests de scoring pasaron")
