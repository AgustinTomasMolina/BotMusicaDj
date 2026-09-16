"""Antes/después del arreglo de la compuerta de ±8% en medio/doble tiempo (tarea 1.1).

Hasta `df9819a` la distancia de BPM era

    min(|a - b|, |a - 2b|, |a - b/2|) / max(a, b)

con el numerador medido contra el BPM TRANSFORMADO y el denominador contra el `max` SIN
transformar. En octava el denominador quedaba el doble de grande y la distancia a la mitad:
100 → 220 medía 4.55% y entraba al set, con un pitch real de 9.09%. El arreglo mide cada
lectura contra su propio par (`motor/scoring.py::_dist_bpm_relativa`).

El número de aquel commit ("56 de 3800 transiciones con pitch real >= 8% → 0") salió de un
script que no quedó en el repo (hallazgo H5, tarea 1.2). Este lo reemplaza: arma radios
sobre bibliotecas sintéticas con muchos pares cerca de la octava, una vez con la fórmula
VIEJA y otra con la actual, y cuenta las transiciones cuyo pitch real es >= 8%.

- La fórmula vieja está copiada ACÁ ADENTRO como REFERENCIA HISTÓRICA (`_dist_vieja`). No
  se usa en el motor ni se arregla: es lo que había, para poder medir contra eso.
- Las radios son las de `motor.radio.build_set` tal cual; para el "antes" se reemplaza solo
  la distancia de `motor.scoring` mientras dura la corrida (`_con_compuerta_vieja`).
- El pitch real se mide con una cuenta independiente del motor (`pitch_real`: `1 - lento /
  rápido` en la mejor de las tres lecturas), no con la función que se está evaluando.

El generador de bibliotecas no es el de aquel script (no quedó), así que los números no
tienen por qué dar 56/3800 exacto: lo que se reproduce es el fenómeno — con la fórmula vieja
hay transiciones fuera de ±8% y con la actual hay 0.

Cuántas depende de qué tan poblada está la biblioteca: con pocos tracks el greedy se queda
sin candidatos del mismo tempo y tiene que cruzar la octava, que es donde la fórmula vieja
fallaba. Medido (10 bibliotecas × 20 radios de 20, 2026-09-16):

    tracks por biblioteca    40            60 (default)   100           200
    ANTES                    110 de 3729   57 de 3798     14 de 3800    10 de 3800
    DESPUÉS                  0 de 3578     0 de 3787      0 de 3800     0 de 3800

Con 60 da 57 de 3798 (1.50%), del orden de los 56 de 3800 (1.47%) de aquel commit, sin
ser el mismo número. DESPUÉS da menos transiciones porque algunas radios se CORTAN: son
las que antes seguían gracias a una transición fuera de ±8% (spec §4, decisión 1 de
`motor/radio.py`).

    python -m benchmark.compuerta_octava
    python -m benchmark.compuerta_octava --bibliotecas 10 --radios 20 --tracks 200
"""
import argparse
import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from motor import scoring
from motor.modelos import Track
from motor.radio import RadioConfig, build_set

TOL = scoring.TOLERANCIA_BPM
CAMELOT = [f"{n}{lado}" for lado in ("A", "B") for n in range(1, 13)]
DIM = 8


# --- REFERENCIA HISTÓRICA: la compuerta anterior a df9819a (no usar en el motor) ------------

def _dist_vieja(a: float, b: float) -> float:
    """`git show df9819a^:motor/scoring.py`, textual."""
    if not (a > 0 and b > 0):
        return 1.0
    return min(abs(a - b), abs(a - 2 * b), abs(a - b / 2)) / max(a, b)


def _dist_vieja_vector(a: float, bs: np.ndarray) -> np.ndarray:
    """La misma cuenta que `_dist_vieja`, para el camino vectorizado de `build_set`."""
    bs = np.asarray(bs, dtype=np.float64)
    if not a > 0:
        return np.ones_like(bs)
    validos = bs > 0
    seguros = np.where(validos, bs, 1.0)
    num = np.minimum(np.minimum(np.abs(a - seguros), np.abs(a - 2 * seguros)),
                     np.abs(a - seguros / 2))
    return np.where(validos, num / np.maximum(a, seguros), 1.0)


@contextlib.contextmanager
def _con_compuerta_vieja() -> Iterator[None]:
    """Mientras dura, `motor.scoring` mide con la fórmula vieja (escalar y vectorizada)."""
    originales = scoring._dist_bpm_relativa, scoring._dist_bpm_relativa_vector
    scoring._dist_bpm_relativa, scoring._dist_bpm_relativa_vector = _dist_vieja, _dist_vieja_vector
    try:
        yield
    finally:
        scoring._dist_bpm_relativa, scoring._dist_bpm_relativa_vector = originales


# --- medición ---------------------------------------------------------------------------

def pitch_real(a: float, b: float) -> float:
    """Cuánto hay que estirar el tempo para mezclar `a` con `b` en la MEJOR lectura (mismo,
    doble o medio tiempo): `1 - lento / rápido`. Independiente de `motor.scoring`."""
    mejor = float("inf")
    for lectura in (b, b * 2, b / 2):
        lento, rapido = sorted((a, lectura))
        mejor = min(mejor, 1.0 - lento / rapido)
    return mejor


def biblioteca(seed: int, n: int) -> list[Track]:
    """Mitad de los tracks entre 118 y 182 BPM y mitad entre 59 y 91: casi todo par de
    "rápido" contra "lento" cae cerca de la octava, que es donde la fórmula vieja medía la
    mitad de la distancia real."""
    rng = np.random.default_rng(seed)
    rapidos = rng.random(n) < 0.5
    bpms = np.where(rapidos, rng.uniform(118.0, 182.0, n), rng.uniform(59.0, 91.0, n))
    emb = rng.standard_normal((n, DIM))
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    return [
        Track(path=Path("/compuerta") / f"{seed:02d}-{i:04d}.wav", duration=300.0,
              bpm=float(round(bpms[i], 1)), key=str(rng.choice(CAMELOT)),
              energy=float(rng.random()), embedding=emb[i], license="CC0",
              source_url="benchmark/compuerta_octava.py", artist=None, title=f"t{i}")
        for i in range(n)
    ]


def medir(bibliotecas: int, radios: int, tracks: int, length: int) -> tuple[int, int, int]:
    """(transiciones, fuera de ±8% por pitch real, radios que se cortaron)."""
    total = fuera = cortadas = 0
    for s in range(bibliotecas):
        bib = biblioteca(s, tracks)
        for semilla in bib[:radios]:
            rset = build_set(semilla, bib, RadioConfig(length=length))
            cortadas += rset.stop is not None
            for prev, sig in zip(rset.tracks, rset.tracks[1:], strict=False):
                total += 1
                fuera += pitch_real(prev.bpm, sig.bpm) >= TOL
    return total, fuera, cortadas


def main(argv=None) -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="benchmark.compuerta_octava",
                                 description="Antes/después del arreglo de la compuerta de octava.")
    ap.add_argument("--bibliotecas", type=int, default=10)
    ap.add_argument("--radios", type=int, default=20, help="Radios por biblioteca.")
    ap.add_argument("--tracks", type=int, default=60, help="Tracks por biblioteca.")
    ap.add_argument("--length", type=int, default=20)
    args = ap.parse_args(argv)

    print(f"{args.bibliotecas} bibliotecas × {args.radios} radios de {args.length} "
          f"({args.tracks} tracks c/u), transiciones con pitch real >= {TOL:.0%}:")
    with _con_compuerta_vieja():
        antes = medir(args.bibliotecas, args.radios, args.tracks, args.length)
    despues = medir(args.bibliotecas, args.radios, args.tracks, args.length)
    for nombre, (total, fuera, cortadas) in (("ANTES  (fórmula vieja)", antes),
                                             ("DESPUÉS (fórmula actual)", despues)):
        pct = 100.0 * fuera / total if total else 0.0
        print(f"  {nombre:<25} {fuera:4d} de {total} ({pct:.2f}%)  — radios cortadas: {cortadas}")
    ok = despues[1] == 0
    print(f"\n  §4 exige 0% fuera de ±{TOL:.0%}: {'OK ✓' if ok else 'ROTO ✗'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
