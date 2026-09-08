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
    assert compat_camelot("8A", "3B") == 0.2        # lejana → piso (se tapa con EQ)
    assert compat_camelot("8A", "?") == 0.5         # desconocida → neutro


def test_deteccion_nota_sintetica():
    """Sobre un tono sintético en una nota conocida, detecta esa pitch class."""
    for nota in ["C", "A", "E"]:
        y, sr = click_track(140.0, dur=20, nota=nota)
        det = tono(y, sr)
        assert det["nota"] == nota, f"generé {nota}, detecté {det['nota']}"


if __name__ == "__main__":
    test_compat_camelot()
    test_deteccion_nota_sintetica()
    print("OK — tests de tonalidad pasaron")
