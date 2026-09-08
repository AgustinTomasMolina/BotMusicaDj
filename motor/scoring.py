"""Scoring de transiciones — MULTIPLICATIVO, no aditivo (spec §7).

    score = encaje_musical × mezclabilidad
    mezclabilidad = bpm_score^1.0 × key_score^0.6

Multiplicando, la mezclabilidad es COMPUERTA: un track parecidísimo (encaje alto) pero
fuera de tempo NO entra (sumando, compensaría y entraría igual). El BPM pesa más que la
key porque un choque de tonalidad se tapa con EQ, pero un tema fuera de tempo no se mezcla.
La spec exige 0% de transiciones fuera de ±8% de BPM → bpm_score es 0 fuera de esa banda.
"""
from motor.tonalidad import compat_camelot

TOLERANCIA_BPM = 0.08   # ±8% (spec §4: 0% de transiciones fuera de esta banda)


def _dist_bpm_relativa(a: float, b: float) -> float:
    """Distancia de BPM relativa, tolerante a medio/doble tiempo (75 ↔ 150)."""
    if not a or not b:
        return 1.0
    return min(abs(a - b), abs(a - 2 * b), abs(a - b / 2)) / a


def bpm_score(a: float, b: float, tol: float = TOLERANCIA_BPM) -> float:
    """1.0 si mismo BPM, cae linealmente hasta 0 en ±tol, y 0 fuera (compuerta dura)."""
    d = _dist_bpm_relativa(a, b)
    if d >= tol:
        return 0.0
    return 1.0 - d / tol


def mezclabilidad(bpm_a: float, bpm_b: float, camelot_a: str, camelot_b: str) -> float:
    """bpm_score^1.0 × key_score^0.6. Es compuerta: si el BPM no mezcla, da 0."""
    bs = bpm_score(bpm_a, bpm_b)
    if bs == 0.0:
        return 0.0
    ks = compat_camelot(camelot_a, camelot_b)
    return (bs ** 1.0) * (ks ** 0.6)


def score(encaje_musical: float, bpm_a: float, bpm_b: float,
          camelot_a: str, camelot_b: str) -> float:
    """score = encaje_musical × mezclabilidad. `encaje_musical` (0..1) sale del
    embedding (similitud de 'cómo suena'); hasta que exista, se pasa como parámetro."""
    return float(encaje_musical) * mezclabilidad(bpm_a, bpm_b, camelot_a, camelot_b)
