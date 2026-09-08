"""Tests de tonalidad: compat_camelot (lógica) + detección sobre audio sintético."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from motor.sintetico import click_track  # noqa: E402
from motor.tonalidad import tono, compat_camelot  # noqa: E402


def test_compat_camelot():
    assert compat_camelot("8A", "8A") == 1.0        # misma
    assert compat_camelot("8A", "9A") == 0.8        # vecina, mismo lado
    assert compat_camelot("8A", "8B") == 0.8        # relativo mayor/menor
    assert compat_camelot("8A", "10A") == 0.5       # ±2 mismo lado
    assert compat_camelot("8A", "3B") == 0.2        # lejana → piso (se tapa con EQ)
    assert compat_camelot("8A", "?") == 0.5         # desconocida → neutro


def test_compat_camelot_consistente_por_distancia():
    # Antes: 8A→10B (cruce, dist 2) valía 0.5 y 8A→9B (diagonal) 0.2, aunque 10B
    # está MÁS lejos. Ahora ±2 solo cuenta en el mismo lado (Fable I1).
    assert compat_camelot("8A", "10B") == 0.2
    assert compat_camelot("8A", "9B") == 0.2
    assert compat_camelot("8A", "10B") <= compat_camelot("8A", "10A")


def test_compat_camelot_ignora_basura():
    assert compat_camelot("8A", "8C") == 0.5        # lado inválido → tratado como '?'
    assert compat_camelot("8A", "13A") == 0.5       # número fuera de 1..12


def test_deteccion_nota_sintetica():
    """Sobre un tono sintético en una nota conocida, detecta esa pitch class."""
    for nota in ["C", "A", "E"]:
        y, sr = click_track(140.0, dur=20, nota=nota)
        det = tono(y, sr)
        assert det["nota"] == nota, f"generé {nota}, detecté {det['nota']}"


def test_silencio_no_inventa_key():
    """Sin señal armónica, NO devolver un Camelot concreto (un dato que miente
    es peor que uno ausente, spec §6 / Fable I4)."""
    import numpy as np
    det = tono(np.zeros(44100, dtype="float32"), 22050)
    assert det["camelot"] == "?"
    assert det["nota"] is None
    assert det["confianza"] == 0.0


if __name__ == "__main__":
    test_compat_camelot()
    test_compat_camelot_consistente_por_distancia()
    test_compat_camelot_ignora_basura()
    test_deteccion_nota_sintetica()
    test_silencio_no_inventa_key()
    print("OK — tests de tonalidad pasaron")
