"""Distribución del desvío de la curva de energía sobre sets reales — calibra tarea 14.

El contrato §4 tiene el umbral `energia_desvio_curva` en None (a calibrar). Todavía nada
alimenta esa métrica: hay que decidir DOS cosas mirando datos reales —
  1) cómo se agrega (mediana / p90 / por-set), y
  2) el número del umbral.
Esta herramienta genera un set desde cada track de la biblioteca (build_set, determinista
con randomness=0), junta el `energy_curve_deviation` de cada uno y muestra la distribución.

    python -m benchmark.curva_energia_calibracion --db djradio.sqlite [--curva peak]

NO fija el umbral (lo elige una persona mirando la distribución, como los 90 s de la ventana).
"""
import argparse
import statistics as st
import sys
from pathlib import Path

import numpy as np

from motor.energia import CURVES, energy_curve_deviation
from motor.radio import RadioConfig, build_set
from motor.store import Store


def desvios(db: Path, curva: str) -> list[tuple[float, str]]:
    """(desvío, nombre de la semilla) de un set generado por cada track de la biblioteca."""
    lib = Store(db).load_library()
    cfg = RadioConfig(curve=curva)          # length 20, randomness 0 → determinista
    out = []
    for seed in lib:
        rset = build_set(seed, lib, cfg)
        if len(rset.energies) >= 2:         # un set de 0-1 tracks no tiene curva que medir
            out.append((energy_curve_deviation(rset.energies, cfg.curve, cfg.length),
                        seed.path.name))
    return out


def _histograma(vals: list[float], bins: int = 12, ancho: int = 50) -> None:
    lo, hi = min(vals), max(vals)
    paso = (hi - lo) / bins if hi > lo else 1e-9
    cuentas = [0] * bins
    for v in vals:
        cuentas[min(int((v - lo) / paso), bins - 1)] += 1
    top = max(cuentas) or 1
    for i, c in enumerate(cuentas):
        a = lo + i * paso
        print(f"  {a:6.3f}–{a + paso:6.3f} | {c:3}  {'#' * round(ancho * c / top)}")


def informe(db: Path, curva: str) -> None:
    pares = desvios(db, curva)
    if not pares:
        sys.exit("No se generaron sets con energías medibles (¿biblioteca vacía?).")
    vals = sorted(d for d, _ in pares)
    def p(q): return float(np.percentile(vals, q))  # noqa: E704
    print(f"biblioteca: {len(vals)} sets · curva {curva}")
    print(f"  min {vals[0]:.3f}  p25 {p(25):.3f}  MEDIANA {p(50):.3f}  p75 {p(75):.3f}  "
          f"p90 {p(90):.3f}  p95 {p(95):.3f}  max {vals[-1]:.3f}")
    print(f"  media {st.mean(vals):.3f}  σ {st.pstdev(vals):.3f}")
    print("\nhistograma:")
    _histograma(vals)
    print("\npeores 8 sets:")
    for d, nom in sorted(pares, reverse=True)[:8]:
        print(f"  {d:.3f}  {nom[:52]}")


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="benchmark.curva_energia_calibracion",
                                 description="Distribución del desvío de la curva de energía.")
    ap.add_argument("--db", required=True, type=Path, help="Store SQLite de la biblioteca (motor scan).")
    ap.add_argument("--curva", choices=CURVES, default="peak")
    args = ap.parse_args(argv)
    informe(args.db, args.curva)
    return 0


if __name__ == "__main__":
    sys.exit(main())
