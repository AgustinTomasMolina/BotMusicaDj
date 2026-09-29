"""Mide el umbral de TIEMPO de la spec §4 sobre una carpeta de audio.

Es el único umbral de §4 que no necesita ground truth: no compara contra nada, solo
cronometra lo que cuesta procesar un track. Sirve para decidir si hay que optimizar sin
depender del XML de Rekordbox.

Cronometra **el scan REAL del motor**, no un subconjunto: `carga` (`motor.analisis.cargar`)
+ `motor.analisis.analizar_senal`. Ese es el mismo camino que corre el pipeline y que llena
el store, e incluye —además de `bpm_refinado`, `tono` y `tono_consenso`— la energía
(`energia_rms`), el `embedding` y los `onsets`. Antes este módulo medía solo BPM y tonalidad
y §4 se aprobaba sobre un análisis más barato que el real: la energía, el embedding y los
onsets no se contaban. Ahora el número que se compara contra el umbral es el del scan real.

El TOTAL es autoritativo porque sale de una sola llamada a `analizar_senal`: si el scan
sumara un paso nuevo, el total lo cobraría igual. El desglose por paso se obtiene
ENVOLVIENDO con un cronómetro las funciones reales que `analizar_senal` ejecuta (no
reimplementando su secuencia), así el desglose nunca es una copia que se desincroniza; en el
peor caso, un paso nuevo no aparecería en el desglose, pero el total ya lo incluye.

Hace un WARM-UP antes de cronometrar: librosa/numba compilan JIT en la primera llamada y esa
compilación se le carga al primer track, inflándolo varios segundos. El warm-up calienta
TODO el camino de `analizar_senal` (BPM, tonalidad, MFCC/tonnetz/contraste del embedding y
onsets), no solo BPM y tonalidad. Sin eso el primer track miente y el máximo es un artefacto.

    python -m benchmark.tiempo_analisis --audio ~/Downloads

NO modifica los audios: solo los lee.
"""
import argparse
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from benchmark.umbrales import UMBRALES

# Formatos de audio: UNA sola lista para todo el repo (calidad.tags.EXTS). Una copia
# local hacía que el motor escaneara formatos que el benchmark nunca medía.
from calidad.tags import EXTS

# El scan real vive acá. Se importa el MÓDULO (no las funciones sueltas) porque el desglose
# reemplaza temporalmente sus globals por versiones cronometradas: `analizar_senal` llama a
# esos nombres como globals de `motor.analisis`, así que envolverlos ahí los cronometra tal
# como el scan los usa.
from motor import analisis as A

_UMBRAL_S = next(u.limite for u in UMBRALES if u.clave == "tiempo_analisis_s")

# Los pasos que `analizar_senal` ejecuta, por su nombre en el namespace de `motor.analisis`.
# `bpm_refinado`/`tono`/`tono_consenso` los llama `medir_bpm_y_tono`; `energia_rms`/`embed`/
# `onsets_por_segundo`, `analizar_senal`. Todos como globals del módulo → envolverlos ahí
# alcanza. `ventana_central` queda fuera del desglose (corre como argumento de `embed`); su
# costo igual está dentro del total.
_PASOS = ("bpm_refinado", "tono", "tono_consenso", "energia_rms", "embed", "onsets_por_segundo")


@dataclass
class Tiempo:
    archivo: str
    duracion_s: float
    carga_s: float
    analisis_s: float
    pasos: dict = field(default_factory=dict)  # {paso: segundos}, del scan real

    @property
    def total_s(self) -> float:
        return self.carga_s + self.analisis_s


class _Cronometrado:
    """Envuelve una función acumulando su tiempo en `acc[clave]`. Envolver la función REAL
    (no reimplementarla) mantiene un solo camino: se cronometra exactamente lo que corrió."""

    def __init__(self, fn, acc: dict, clave: str):
        self.fn, self.acc, self.clave = fn, acc, clave

    def __call__(self, *a, **k):
        t = time.perf_counter()
        try:
            return self.fn(*a, **k)
        finally:
            self.acc[self.clave] = self.acc.get(self.clave, 0.0) + (time.perf_counter() - t)


def calentar(sr: int = A.SR) -> float:
    """Compila el JIT sobre TODO el camino del scan (delega en `motor.analisis.calentar`).

    Recibe `sr` porque la etapa A (`benchmark.analizar.analizar`) lo llama con el suyo."""
    return A.calentar(sr)


def medir(ruta: str, sr: int = A.SR, consenso: bool = False) -> Tiempo | None:
    t0 = time.perf_counter()
    y = A.cargar(ruta, sr)
    carga = time.perf_counter() - t0
    if y is None:  # no decodificó o dura < 1 s
        return None

    pasos: dict = {}
    originales = {p: getattr(A, p) for p in _PASOS}
    for p in _PASOS:
        setattr(A, p, _Cronometrado(originales[p], pasos, p))
    try:
        t1 = time.perf_counter()
        A.analizar_senal(y, sr, consenso=consenso)  # EL scan real
        analisis = time.perf_counter() - t1
    except Exception as e:  # noqa: BLE001 — un archivo raro no puede tumbar la corrida entera
        print(f"  (saltado, analizar_senal falló: {Path(ruta).name[:50]} — {e})", flush=True)
        return None
    finally:
        for p, fn in originales.items():
            setattr(A, p, fn)

    return Tiempo(Path(ruta).name, round(y.size / sr, 1), carga, analisis, pasos)


def informe(ts: list[Tiempo]) -> int:
    if not ts:
        print("Sin archivos medibles.")
        return 1
    tot = [t.total_s for t in ts]
    ana = [t.analisis_s for t in ts]
    car = [t.carga_s for t in ts]
    p95 = float(np.percentile(tot, 95))

    print(f"\n{'=' * 78}")
    print(f"Tiempo por track — {len(ts)} archivos  (umbral spec §4: <= {_UMBRAL_S:g} s/track)")
    print("Cronometra el scan real: carga + motor.analisis.analizar_senal")
    print(f"{'=' * 78}")
    print(f"  TOTAL (carga+análisis): medio {statistics.mean(tot):6.2f}  "
          f"mediana {statistics.median(tot):6.2f}  p95 {p95:6.2f}  max {max(tot):6.2f}")
    print(f"    · carga             : medio {statistics.mean(car):6.2f}  "
          f"max {max(car):6.2f}")
    print(f"    · análisis          : medio {statistics.mean(ana):6.2f}  "
          f"max {max(ana):6.2f}")
    for paso in _PASOS:
        vals = [t.pasos.get(paso, 0.0) for t in ts]
        print(f"        - {paso:15s} : medio {statistics.mean(vals):6.2f}  max {max(vals):6.2f}")

    sobre = [t for t in ts if t.total_s > _UMBRAL_S]
    veredicto = "OK ✓" if p95 <= _UMBRAL_S else "ROTO ✗"
    print(f"\n  p95 {p95:.2f} s vs umbral {_UMBRAL_S:g} s  ->  {veredicto}")
    print(f"  tracks por encima del umbral: {len(sobre)}/{len(ts)}")

    dur = [t.duracion_s for t in ts]
    if len(set(dur)) > 1:
        print(f"  correlación duración ↔ tiempo total: {np.corrcoef(dur, tot)[0, 1]:+.2f}")

    if sobre:
        print(f"\n{'-' * 78}\nLos 10 más lentos:")
        for t in sorted(ts, key=lambda t: -t.total_s)[:10]:
            desg = " ".join(f"{p.split('_')[0]} {t.pasos.get(p, 0.0):4.1f}" for p in _PASOS)
            print(f"  {t.archivo[:36]:36} dur {t.duracion_s:6.0f}s  "
                  f"total {t.total_s:6.2f}  (carga {t.carga_s:4.1f} + {desg})")
    return 0 if p95 <= _UMBRAL_S else 1


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="benchmark.tiempo_analisis")
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--consenso", action="store_true",
                    help="Correr el scan con consenso=True (la key sale del voto). El scan del "
                         "store usa el default (consenso=False), que igual cronometra el "
                         "tono_consenso por el '?' de confianza.")
    ap.add_argument("--sin-warmup", action="store_true",
                    help="No calentar el JIT (para ver cuánto contamina el primer track).")
    args = ap.parse_args(argv)

    rutas = sorted(str(p) for p in args.audio.rglob("*") if p.suffix.lower() in EXTS)
    if args.limit:
        rutas = rutas[:args.limit]
    if not rutas:
        print(f"No encontré audio en {args.audio}")
        return 1

    if not args.sin_warmup:
        print(f"warm-up (compilación JIT, camino completo): {calentar():.2f} s — no cuenta")

    ts = []
    for i, r in enumerate(rutas, 1):
        t = medir(r, consenso=args.consenso)
        if t is None:
            continue
        ts.append(t)
        desg = " ".join(f"{p.split('_')[0]} {t.pasos.get(p, 0.0):4.1f}" for p in _PASOS)
        print(f"  [{i}/{len(rutas)}] {t.archivo[:34]:34} dur {t.duracion_s:6.0f}s  "
              f"total {t.total_s:6.2f}s (carga {t.carga_s:4.1f} + {desg})", flush=True)

    return informe(ts)


if __name__ == "__main__":
    sys.exit(main())
