"""Tests de energía (RMS + percentil)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402
from motor.energia import energia_rms, percentil  # noqa: E402


def test_rms_fuerte_mayor_que_suave():
    fuerte = np.sin(np.linspace(0, 100, 4410)).astype(np.float32)
    suave = 0.1 * fuerte
    assert energia_rms(fuerte) > energia_rms(suave)
    assert energia_rms(np.zeros(1000)) == 0.0


def test_percentil():
    biblioteca = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert percentil(0.35, biblioteca) == 60.0     # 3 de 5 por debajo
    assert percentil(0.05, biblioteca) == 0.0      # el más tranquilo
    assert percentil(0.9, biblioteca) == 100.0     # el más energético


if __name__ == "__main__":
    test_rms_fuerte_mayor_que_suave()
    test_percentil()
    print("OK — tests de energía pasaron")
