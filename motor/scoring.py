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


# Lecturas de octava de `b` que se consideran al medir contra `a`, en orden de preferencia:
# mismo tiempo, doble tiempo, medio tiempo. `radio.bpm_delta_pct` usa este mismo orden para
# nombrar la lectura elegida, así que cambiarlo acá lo cambia allá.
FACTORES_OCTAVA = (1.0, 2.0, 0.5)


def _rel(x: float, y: float) -> float:
    """Distancia relativa entre dos tempos: `|x - y| / max(x, y)`. Simétrica."""
    return abs(x - y) / max(x, y)


def _distancias_por_lectura(a: float, b: float) -> tuple[float, ...]:
    """La distancia relativa de `a` contra cada lectura de `b` (ver `FACTORES_OCTAVA`),
    cada una medida contra SU propio par."""
    return tuple(_rel(a, f * b) for f in FACTORES_OCTAVA)


def _dist_bpm_relativa(a: float, b: float) -> float:
    """Distancia de BPM relativa, tolerante a medio/doble tiempo (75 ↔ 150).

    `min(rel(a, b), rel(a, 2b), rel(a, b/2))` con `rel(x, y) = |x - y| / max(x, y)`: cada
    lectura se mide contra SU propio par, y gana la más cercana. Es simétrica —
    `dist(a, b) == dist(b, a)`, porque `rel(a, 2b) == rel(b, a/2)` — y es el pitch que hay
    que aplicar de verdad para mezclar en esa lectura.

    Historia, porque ya se rompió dos veces:

    1. La primera versión dividía por `a`, y 'fuera de ±8%' dependía de qué track iba
       primero. La auditoría Fable I3 lo arregló dividiendo por `max(a, b)`.
    2. Ese arreglo dejaba el numerador medido contra el BPM TRANSFORMADO (`|a - 2b|`,
       `|a - b/2|`) pero el denominador contra el `max` SIN transformar. En el caso de
       octava el denominador quedaba el doble de grande y la distancia a la mitad:
       100 → 220 daba 4.55% y entraba (pitch real 9.09%), 220 → 100 daba 9.09% y se cortaba,
       80 → 174 daba 4.02% (real 8.05%). La compuerta de §4 era asimétrica justo donde se
       había arreglado la simetría, y el motivo de §6 decía "+4.5%" sobre un salto de 9%.
    """
    if not (a > 0 and b > 0):
        return 1.0
    return min(_distancias_por_lectura(a, b))


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
