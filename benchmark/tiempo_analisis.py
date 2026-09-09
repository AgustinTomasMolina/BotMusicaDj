"""Mide el umbral de TIEMPO de la spec §4 sobre una carpeta de audio.

Es el único umbral de §4 que no necesita ground truth: no compara contra nada, solo
cronometra lo que cuesta procesar un track. Sirve para decidir si hay que optimizar sin
depender del XML de Rekordbox.

Mide lo mismo que la corrida del benchmark: **carga + análisis** (`librosa.load` +
`bpm_refinado` + `tono`). La carga cuenta porque decodificar es parte del costo real de
procesar un track.

Hace un WARM-UP antes de cronometrar: librosa/numba compilan JIT en la primera llamada y
esa compilación se le carga al primer track, inflándolo varios segundos. Sin warm-up el
primer track miente y el máximo del reporte es un artefacto.

    python -m benchmark.tiempo_analisis --audio ~/Downloads

NO modifica los audios: solo los lee.
"""
import argparse
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from benchmark.umbrales import UMBRALES
from motor.bpm import bpm_refinado
from motor.sintetico import click_track
from motor.tonalidad import tono

EXTS = (".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg")

_UMBRAL_S = next(u.limite for u in UMBRALES if u.clave == "tiempo_analisis_s")


@dataclass
class Tiempo:
    archivo: str
    duracion_s: float
    carga_s: float
    bpm_s: float
    tono_s: float

    @property
    def analisis_s(self) -> float:
        return self.bpm_s + self.tono_s

    @property
    def total_s(self) -> float:
        return self.carga_s + self.analisis_s


def calentar(sr: int = 22050) -> float:
    """Fuerza la compilación JIT con una señal sintética corta. Devuelve lo que tardó."""
    t0 = time.perf_counter()
    y, _ = click_track(128.0, dur=10.0, sr=sr, nota="A")
    bpm_refinado(y, sr)
    tono(y, sr)
    return time.perf_counter() - t0


def medir(ruta: str, sr: int = 22050) -> Tiempo | None:
    import librosa

    t0 = time.perf_counter()
    try:
        y, _ = librosa.load(ruta, sr=sr, mono=True)
    except Exception:  # noqa: BLE001
        return None
    carga = time.perf_counter() - t0
    if y.size < sr:
        return None

    t1 = time.perf_counter()
    bpm_refinado(y, sr)
    t_bpm = time.perf_counter() - t1

    t2 = time.perf_counter()
    tono(y, sr)
    t_tono = time.perf_counter() - t2

    return Tiempo(Path(ruta).name, round(y.size / sr, 1), carga, t_bpm, t_tono)


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
    print(f"{'=' * 78}")
    print(f"  TOTAL (carga+análisis): medio {statistics.mean(tot):6.2f}  "
          f"mediana {statistics.median(tot):6.2f}  p95 {p95:6.2f}  max {max(tot):6.2f}")
    print(f"    · carga             : medio {statistics.mean(car):6.2f}  "
          f"max {max(car):6.2f}")
    print(f"    · análisis          : medio {statistics.mean(ana):6.2f}  "
          f"max {max(ana):6.2f}")
    print(f"        - bpm_refinado  : medio {statistics.mean([t.bpm_s for t in ts]):6.2f}  "
          f"max {max(t.bpm_s for t in ts):6.2f}")
    print(f"        - tono          : medio {statistics.mean([t.tono_s for t in ts]):6.2f}  "
          f"max {max(t.tono_s for t in ts):6.2f}")

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
            print(f"  {t.archivo[:40]:40} dur {t.duracion_s:6.0f}s  "
                  f"total {t.total_s:6.2f}  (carga {t.carga_s:5.2f} + "
                  f"bpm {t.bpm_s:5.2f} + tono {t.tono_s:5.2f})")
    return 0 if p95 <= _UMBRAL_S else 1


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="benchmark.tiempo_analisis")
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=None)
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
        print(f"warm-up (compilación JIT): {calentar():.2f} s — no se cuenta en las métricas")

    ts = []
    for i, r in enumerate(rutas, 1):
        t = medir(r)
        if t is None:
            continue
        ts.append(t)
        print(f"  [{i}/{len(rutas)}] {t.archivo[:40]:40} dur {t.duracion_s:6.0f}s  "
              f"total {t.total_s:6.2f}s (carga {t.carga_s:5.2f} bpm {t.bpm_s:5.2f} "
              f"tono {t.tono_s:5.2f})", flush=True)

    return informe(ts)


if __name__ == "__main__":
    sys.exit(main())
