"""Energía percibida de un track y su percentil dentro de la biblioteca.

La spec §7 es clara: la energía se usa como **percentil de la biblioteca**, no como
valor absoluto (un -6 dB no significa lo mismo en una colección de ambient que en una
de hard techno). Baja de prioridad con rotación de 1:30, pero entra en la curva del set.
"""
import numpy as np


def energia_rms(y: np.ndarray) -> float:
    """Energía percibida ≈ RMS de la señal (0..1 para audio normalizado)."""
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(y ** 2)))


def percentil(valor: float, valores) -> float:
    """Percentil (0..100) de `valor` dentro de `valores` (la biblioteca).
    100 = el más energético de la biblioteca; 0 = el más tranquilo."""
    a = np.asarray(list(valores), dtype=np.float64)
    if a.size == 0:
        return 0.0
    return float((a < valor).mean() * 100.0)
