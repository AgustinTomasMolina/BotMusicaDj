"""Scoring de transiciones — MULTIPLICATIVO, no aditivo (spec §7).

    score = encaje_musical × mezclabilidad
    mezclabilidad = bpm_score^1.0 × key_score^0.6

Multiplicando, la mezclabilidad es COMPUERTA: un track parecidísimo (encaje alto) pero
fuera de tempo NO entra (sumando, compensaría y entraría igual). El BPM pesa más que la
key porque un choque de tonalidad se tapa con EQ, pero un tema fuera de tempo no se mezcla.
La spec exige 0% de transiciones fuera de ±8% de BPM → bpm_score es 0 fuera de esa banda.

Hay DOS formas de pedir la mezclabilidad, y miden con el MISMO código:

- `mezclabilidad(bpm_a, bpm_b, key_a, key_b)` — escalar, un par. Es la fuente de verdad y
  la que usa el "por qué" de la radio (`radio._transition`).
- `mezclabilidad_vector(bpm_a, bpms_b, key_a, keys_b)` — el anterior contra TODOS los
  candidatos de una vez, para `radio.build_set` (tarea 1.2: con 10.000 tracks el loop
  escalar por candidato costaba ~650 ms contra los < 200 ms de §4).

La vectorizada no tiene una copia de la fórmula: llama a las mismas piezas que la escalar
(`_bpm_valido`, `_distancias_por_lectura` → `_rel`, `_bpm_score_de_distancia`,
`_factor_key`, `_combinar`, y `tonalidad.compat_camelot` para la key), que están escritas
para aceptar un float o un array de numpy. Lo único propio de la vectorizada es cómo junta
las tres lecturas (`np.fmin`, que da lo mismo que el `min` de Python sobre estos valores) y
cómo reparte la key por grupos; `motor/tests/test_scoring.py` compara las dos elemento a
elemento sobre una grilla densa de BPM y las 24×24 keys + `?` + inválidas.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from functools import reduce

import numpy as np

from motor.tonalidad import compat_camelot

TOLERANCIA_BPM = 0.08   # ±8% (spec §4: 0% de transiciones fuera de esta banda)


# Lecturas de octava de `b` que se consideran al medir contra `a`, en orden de preferencia:
# mismo tiempo, doble tiempo, medio tiempo. `radio.bpm_delta_pct` usa este mismo orden para
# nombrar la lectura elegida, así que cambiarlo acá lo cambia allá.
FACTORES_OCTAVA = (1.0, 2.0, 0.5)

# Exponentes de `mezclabilidad = bpm_score^EXPONENTE_BPM × key_score^EXPONENTE_KEY`.
EXPONENTE_BPM = 1.0
EXPONENTE_KEY = 0.6


def _bpm_valido(x):
    """¿La compuerta puede medir este BPM? Finito y > 0. Acepta un float o un array.

    0 o negativo es "sin BPM" (el análisis devuelve 0.0 sobre silencio: `bpm.bpm_refinado`
    devuelve el `tempo0 <= 0` de beat_track tal cual) y NaN no se compara. `inf` también
    queda afuera (hallazgo H1 de la auditoría): antes pasaba el `> 0`, todas las distancias
    daban `inf/inf = NaN`, `NaN >= tol` es False y la compuerta dejaba entrar al track con
    mezclabilidad NaN.
    """
    return np.logical_and(np.isfinite(x), np.greater(x, 0))


def _rel(x, y):
    """Distancia relativa entre dos tempos: `|x - y| / max(x, y)`. Simétrica.

    Elemento a elemento: sirve igual para dos floats (la compuerta escalar) que para un
    float contra un array (la vectorizada). Por eso es `np.maximum` y no `max`.
    """
    with np.errstate(over="ignore", invalid="ignore"):
        return np.abs(x - y) / np.maximum(x, y)


def _distancias_por_lectura(a, b) -> tuple:
    """La distancia relativa de `a` contra cada lectura de `b` (ver `FACTORES_OCTAVA`),
    cada una medida contra SU propio par. `b` puede ser un array (una distancia por
    elemento)."""
    with np.errstate(over="ignore"):
        return tuple(_rel(a, f * b) for f in FACTORES_OCTAVA)


def _dist_bpm_relativa(a: float, b: float) -> float:
    """Distancia de BPM relativa, tolerante a medio/doble tiempo (75 ↔ 150).

    `min(rel(a, b), rel(a, 2b), rel(a, b/2))` con `rel(x, y) = |x - y| / max(x, y)`: cada
    lectura se mide contra SU propio par, y gana la más cercana. Es simétrica —
    `dist(a, b) == dist(b, a)`, porque `rel(a, 2b) == rel(b, a/2)` — y es el pitch que hay
    que aplicar de verdad para mezclar en esa lectura.

    BPM no medible (`_bpm_valido`: 0, negativo, NaN, inf) → 1.0, que está fuera de
    cualquier tolerancia.

    Historia, porque ya se rompió dos veces:

    1. La primera versión dividía por `a`, y 'fuera de ±8%' dependía de qué track iba
       primero. La auditoría Fable I3 lo arregló dividiendo por `max(a, b)`.
    2. Ese arreglo dejaba el numerador medido contra el BPM TRANSFORMADO (`|a - 2b|`,
       `|a - b/2|`) pero el denominador contra el `max` SIN transformar. En el caso de
       octava el denominador quedaba el doble de grande y la distancia a la mitad:
       100 → 220 daba 4.55% y entraba (pitch real 9.09%), 220 → 100 daba 9.09% y se cortaba,
       80 → 174 daba 4.02% (real 8.05%). La compuerta de §4 era asimétrica justo donde se
       había arreglado la simetría, y el motivo de §6 decía "+4.5%" sobre un salto de 9%.
       (El antes/después de ese arreglo lo reproduce `benchmark/compuerta_octava.py`.)
    """
    if not (_bpm_valido(a) and _bpm_valido(b)):
        return 1.0
    return float(min(_distancias_por_lectura(a, b)))


def _dist_bpm_relativa_vector(a: float, bs: np.ndarray) -> np.ndarray:
    """`_dist_bpm_relativa(a, b)` para cada `b` de `bs`, sin loop de Python.

    Las lecturas salen de `_distancias_por_lectura` (la misma que usa la escalar). Se
    juntan con `np.fmin` y no con `np.minimum`: `fmin` ignora un NaN igual que el `min` de
    Python cuando el NaN no está primero, y la única lectura que puede dar NaN con BPMs
    válidos es la de doble tiempo (`2b` desborda a inf con b ~ 1e308); la de mismo tiempo,
    que es la primera, nunca. Los BPM no medibles se reemplazan por 1.0 ANTES de medir —
    para no dividir por cero — y se devuelven como 1.0, lo mismo que la escalar.
    """
    bs = np.asarray(bs, dtype=np.float64)
    if not _bpm_valido(a):
        return np.ones_like(bs)
    validos = _bpm_valido(bs)
    medibles = np.where(validos, bs, 1.0)
    d = reduce(np.fmin, _distancias_por_lectura(a, medibles))
    return np.where(validos, d, 1.0)


def _bpm_score_de_distancia(d, tol: float):
    """1.0 a distancia 0, cae linealmente hasta 0 en `tol`, y 0 desde ahí (compuerta dura).
    Elemento a elemento; la escalar lo convierte a float."""
    return np.where(d >= tol, 0.0, 1.0 - d / tol)


def bpm_score(a: float, b: float, tol: float = TOLERANCIA_BPM) -> float:
    """1.0 si mismo BPM, cae linealmente hasta 0 en ±tol, y 0 fuera (compuerta dura)."""
    return float(_bpm_score_de_distancia(_dist_bpm_relativa(a, b), tol))


def _factor_key(key_score: float) -> float:
    """`key_score ** EXPONENTE_KEY`. Siempre sobre un float de Python (también desde la
    vectorizada, que lo llama una vez por key distinta): así la potencia es la misma
    función de C en los dos caminos y no la de numpy, que no está garantizado que redondee
    igual en el último bit."""
    return float(key_score) ** EXPONENTE_KEY


def _combinar(bs, factor_key):
    """`bpm_score^EXPONENTE_BPM × factor_key`. Elemento a elemento en `bs`."""
    return (bs ** EXPONENTE_BPM) * factor_key


def mezclabilidad(bpm_a: float, bpm_b: float, camelot_a: str, camelot_b: str) -> float:
    """bpm_score^1.0 × key_score^0.6. Es compuerta: si el BPM no mezcla, da 0."""
    bs = bpm_score(bpm_a, bpm_b)
    if bs == 0.0:
        return 0.0
    return float(_combinar(bs, _factor_key(compat_camelot(camelot_a, camelot_b))))


@dataclass(frozen=True, slots=True)
class KeysIndexadas:
    """Las keys de una lista de tracks, agrupadas por texto para no re-parsearlas.

    `unicas[codigos[i]] == keys[i]`. Se agrupa por el TEXTO tal cual y no por la key
    parseada: `compat_camelot` decide qué significa cada texto ("8a", " 8A", "?", "13A"), y
    agrupar por otra cosa sería re-derivar esa decisión acá.
    """

    unicas: tuple[str, ...]
    codigos: np.ndarray

    @classmethod
    def de(cls, keys: Sequence[str]) -> "KeysIndexadas":
        posicion: dict[str, int] = {}
        codigos = np.fromiter((posicion.setdefault(k, len(posicion)) for k in keys),
                              dtype=np.intp, count=len(keys))
        return cls(tuple(posicion), codigos)

    def __len__(self) -> int:
        return len(self.codigos)


def mezclabilidad_vector(bpm_a: float, bpms_b: np.ndarray, camelot_a: str,
                         keys_b: KeysIndexadas) -> np.ndarray:
    """`mezclabilidad(bpm_a, bpms_b[i], camelot_a, keys_b[i])` para todo `i`, en un array.

    Da EXACTAMENTE lo mismo que la escalar (bit a bit, lo verifica el test de
    equivalencia), porque está hecha de sus mismas piezas: la distancia sale de
    `_distancias_por_lectura`, el puntaje de `_bpm_score_de_distancia`, y la key de
    `compat_camelot` llamada una vez por cada texto de key distinto (nunca una tabla escrita
    a mano) y elevada con `_factor_key` sobre un float de Python.
    """
    bpms_b = np.asarray(bpms_b, dtype=np.float64)
    if bpms_b.shape != (len(keys_b),):
        raise ValueError(f"{bpms_b.shape[0] if bpms_b.ndim else 0} BPM contra "
                         f"{len(keys_b)} keys: tienen que ser uno por candidato")
    bs = _bpm_score_de_distancia(_dist_bpm_relativa_vector(bpm_a, bpms_b), TOLERANCIA_BPM)
    factores = np.array([_factor_key(compat_camelot(camelot_a, k)) for k in keys_b.unicas],
                        dtype=np.float64)
    mezcla = _combinar(bs, factores[keys_b.codigos])
    # Mismo corto que la escalar (`if bs == 0.0: return 0.0`): un 0 exacto, no 0 × factor.
    return np.where(bs == 0.0, 0.0, mezcla)


def score(encaje_musical: float, bpm_a: float, bpm_b: float,
          camelot_a: str, camelot_b: str) -> float:
    """score = encaje_musical × mezclabilidad. `encaje_musical` (0..1) sale del
    embedding (similitud de 'cómo suena'); hasta que exista, se pasa como parámetro."""
    return float(encaje_musical) * mezclabilidad(bpm_a, bpm_b, camelot_a, camelot_b)
