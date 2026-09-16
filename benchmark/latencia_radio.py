"""Mide el umbral de LATENCIA de la radio de la spec §4: `build_set` con 10.000 tracks.

Como `tiempo_analisis`, no necesita ground truth: cronometra `motor.radio.build_set` y
`motor.radio.similar` sobre una biblioteca SINTÉTICA y DETERMINISTA (misma semilla → misma
biblioteca → mismo número, salvo el ruido de la máquina). No hay audio: los BPM, keys y
energías son datos de entrada del caso, no mediciones (spec §5).

Dos perfiles de biblioteca, porque el costo de `build_set` depende de cuántos candidatos
pasan la compuerta de ±8% en cada posición:

- `realista`: BPM repartidos en géneros (house, techno, dnb, halftime, downtempo), con
  pares cercanos a la octava (70 ↔ 140) y algún BPM sin medir (0.0); keys Camelot con
  un ~5% de `?`; ~800 artistas repetidos para que `artist_gap` actúe.
- `peor_caso`: todos los BPM entre 124 y 126 — TODOS mezclan con todos. Es la medición de
  la auditoría (653 ms antes de la tarea 1.2): cada posición puntúa la biblioteca entera.

Se reporta la MEDIANA de `--corridas` repeticiones después de un warm-up (la primera
corrida paga caches de numpy/BLAS y del intérprete, y no es lo que ve el usuario). El
veredicto usa el PEOR caso de `build_set` contra `latencia_radio_ms` de
`benchmark/umbrales.py` — se lee de ahí, no se copia el número.

    python -m benchmark.latencia_radio
    python -m benchmark.latencia_radio --tracks 10000 --corridas 9 --perfil peor_caso
    python -m benchmark.latencia_radio --perfilar      # cProfile de una corrida
    python -m benchmark.latencia_radio --largo 50      # explorar otro largo (no es §4)

El umbral de §4 se mide con sets de `LARGO_BENCHMARK` (30) tracks: el costo crece con el largo.
"""
import argparse
import contextlib
import cProfile
import io
import pstats
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from benchmark.umbrales import UMBRALES, evaluar
from motor.embeddings import DIM
from motor.modelos import Track
from motor.radio import RadioConfig, build_set, similar

_CLAVE = "latencia_radio_ms"
_UMBRAL = next(u for u in UMBRALES if u.clave == _CLAVE)

CAMELOT = [f"{n}{lado}" for lado in ("A", "B") for n in range(1, 13)]

# (centro, desvío, peso) de cada "género" del perfil realista. 70 y 87 son la mitad de
# 140 y 174: sin esos pares la lectura de octava de la compuerta no se ejercita nunca.
_GENEROS = (
    (124.0, 1.5, 0.30),   # house
    (132.0, 3.0, 0.30),   # techno
    (174.0, 1.0, 0.12),   # drum & bass
    (87.0, 0.8, 0.08),    # halftime (medio tiempo del dnb)
    (70.0, 1.5, 0.10),    # downtempo (medio tiempo del techno)
    (100.0, 6.0, 0.10),   # lo que no encaja en ningún lado
)

PERFILES = ("realista", "peor_caso")

# Largo del set con el que se mide el umbral de §4 (decisión del dueño, 2026-09-16): 30
# tracks, unas 3 horas con tracks de ~6 minutos. §4 decía "< 200 ms con 10k tracks" sin
# decir para qué largo, y el costo de `build_set` crece con el largo (cada posición recorre
# la biblioteca). 20 (el default de RadioConfig) no representaba un set largo real.
LARGO_BENCHMARK = 30


def configs(largo: int = LARGO_BENCHMARK) -> dict[str, RadioConfig]:
    """Las configs que se cronometran, todas con el mismo largo de set."""
    return {
        "peak r=0": RadioConfig(length=largo, curve="peak", randomness=0.0),
        "peak r=0.5": RadioConfig(length=largo, curve="peak", randomness=0.5, seed=7),
        "flat r=0 gap=0": RadioConfig(length=largo, curve="flat", randomness=0.0, artist_gap=0),
    }


CONFIGS: dict[str, RadioConfig] = configs()


def biblioteca_sintetica(n: int = 10_000, seed: int = 20260916,
                         perfil: str = "realista") -> list[Track]:
    """`n` tracks deterministas. Misma `(n, seed, perfil)` → exactamente la misma lista."""
    if perfil not in PERFILES:
        raise ValueError(f"perfil desconocido {perfil!r}; los soportados son {PERFILES}")
    rng = np.random.default_rng(seed)

    emb = rng.standard_normal((n, DIM))
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)

    if perfil == "peor_caso":
        bpms = rng.uniform(124.0, 126.0, n)
    else:
        centros = np.array([g[0] for g in _GENEROS])
        desvios = np.array([g[1] for g in _GENEROS])
        pesos = np.array([g[2] for g in _GENEROS])
        genero = rng.choice(len(_GENEROS), size=n, p=pesos / pesos.sum())
        bpms = rng.normal(centros[genero], desvios[genero])
        bpms[rng.random(n) < 0.005] = 0.0          # sin BPM medido (silencio): no mezcla
    bpms = np.round(bpms, 2)

    keys = rng.choice(CAMELOT, size=n)
    keys[rng.random(n) < 0.05] = "?"
    energias = rng.random(n)
    n_artistas = max(1, n // 12)
    artistas = rng.integers(0, n_artistas, size=n)
    sin_artista = rng.random(n) < 0.03

    return [
        Track(
            path=Path("/sintetica") / f"{i:06d}.mp3",
            duration=360.0,
            bpm=float(bpms[i]),
            key=str(keys[i]),
            energy=float(energias[i]),
            embedding=emb[i],
            license="CC-BY",
            source_url=f"https://example.org/sintetica/{i}",
            artist=None if sin_artista[i] else f"Artista {int(artistas[i])}",
            title=f"Track {i}",
        )
        for i in range(n)
    ]


def _semilla(biblioteca: list[Track], perfil: str) -> Track:
    """Una semilla de techno para el perfil realista (el género más poblado), la primera
    para el peor caso. Fija: cambiar de semilla cambia cuánto trabajo hace el set."""
    if perfil == "peor_caso":
        return biblioteca[0]
    return next(t for t in biblioteca if 130.0 <= t.bpm <= 134.0)


def cronometrar(fn: Callable[[], object], corridas: int, warmup: int = 1) -> list[float]:
    """Tiempos en ms de `corridas` llamadas a `fn`, después de `warmup` sin contar."""
    for _ in range(warmup):
        fn()
    tiempos = []
    for _ in range(corridas):
        t0 = time.perf_counter()
        fn()
        tiempos.append((time.perf_counter() - t0) * 1000.0)
    return tiempos


def perfilar(biblioteca: list[Track], semilla: Track, config: RadioConfig, top: int = 25) -> str:
    """cProfile de UNA corrida de `build_set` (ya calentada), ordenado por tiempo acumulado."""
    build_set(semilla, biblioteca, config)
    prof = cProfile.Profile()
    prof.enable()
    build_set(semilla, biblioteca, config)
    prof.disable()
    salida = io.StringIO()
    pstats.Stats(prof, stream=salida).sort_stats("tottime").print_stats(top)
    return salida.getvalue()


def main(argv=None) -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="benchmark.latencia_radio",
                                 description="Latencia de build_set/similar contra la spec §4.")
    ap.add_argument("--tracks", type=int, default=10_000)
    ap.add_argument("--corridas", type=int, default=7, help="Repeticiones medidas (se reporta la mediana).")
    ap.add_argument("--seed", type=int, default=20260916, help="Semilla de la biblioteca sintética.")
    ap.add_argument("--perfil", choices=PERFILES, action="append",
                    help="Perfil de biblioteca (repetible). Default: los dos.")
    ap.add_argument("--perfilar", action="store_true",
                    help="Imprimir además un cProfile de build_set (config 'peak r=0').")
    ap.add_argument("--largo", type=int, default=LARGO_BENCHMARK,
                    help=f"Largo del set (default {LARGO_BENCHMARK}: el del umbral de §4). "
                         "Otro largo sirve para explorar, pero el veredicto de §4 es con el default.")
    args = ap.parse_args(argv)
    if args.tracks < 2 or args.corridas < 1 or args.largo < 1:
        ap.error("--tracks >= 2, --corridas >= 1 y --largo >= 1")

    perfiles = args.perfil or list(PERFILES)
    a_medir = configs(args.largo)
    print(f"Latencia de la radio — {args.tracks} tracks, sets de {args.largo}, mediana de "
          f"{args.corridas} corridas (umbral spec §4: {_UMBRAL.objetivo()} con sets de "
          f"{LARGO_BENCHMARK})")

    peor_build = 0.0
    for perfil in perfiles:
        biblioteca = biblioteca_sintetica(args.tracks, args.seed, perfil)
        semilla = _semilla(biblioteca, perfil)
        print(f"\n[{perfil}] semilla {semilla.path.name} ({semilla.bpm:.1f} BPM, {semilla.key})")

        for nombre, config in a_medir.items():
            ultimo = {}

            def correr(config=config, ultimo=ultimo, semilla=semilla, biblioteca=biblioteca):
                ultimo["set"] = build_set(semilla, biblioteca, config)

            ts = cronometrar(correr, args.corridas)
            med = statistics.median(ts)
            peor_build = max(peor_build, med)
            rset = ultimo["set"]
            corte = rset.stop or "completo"
            print(f"  build_set {nombre:<16} mediana {med:8.1f} ms  (min {min(ts):7.1f}  "
                  f"max {max(ts):7.1f})  largo {len(rset)}/{config.length} [{corte}]")

        ts = cronometrar(lambda s=semilla, b=biblioteca: similar(s, b, 10), args.corridas)
        print(f"  similar   {'n=10':<16} mediana {statistics.median(ts):8.1f} ms  "
              f"(min {min(ts):7.1f}  max {max(ts):7.1f})")

        if args.perfilar:
            print(f"\n  cProfile build_set 'peak r=0' [{perfil}]:")
            print(perfilar(biblioteca, semilla, a_medir["peak r=0"]))

    fila = next(f for f in evaluar({_CLAVE: round(peor_build, 1)}) if f.umbral.clave == _CLAVE)
    veredicto = "OK ✓" if fila.ok else "ROTO ✗"
    print(f"\n  peor mediana de build_set {peor_build:.1f} ms vs umbral {_UMBRAL.objetivo()}"
          f"  ->  {veredicto}")
    return 0 if fila.ok else 1


if __name__ == "__main__":
    sys.exit(main())
