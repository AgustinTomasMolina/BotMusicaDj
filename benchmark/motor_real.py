"""Benchmark del motor contra el ground truth REAL de Rekordbox (tareas F0 #4, #6, #9).

Corre el motor (BPM + tonalidad) sobre los audios de verdad, resueltos con
`ground_truth.resolver` (los archivos viven en un pendrive / otra carpeta), y compara
contra el BPM/Camelot que puso Rekordbox. Calcula las métricas de la spec §4 que dependen
del análisis por track (las que dependen de la RADIO —transiciones, choques, energía,
latencia— quedan sin medir hasta que exista el motor de secuencia).

    python -m benchmark.motor_real --xml Rekordbox.xml --roots D:\\ --limit 40

NO modifica los audios: solo los lee (regla del proyecto).
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

from benchmark.umbrales import evaluar
from ground_truth.rekordbox import parsear
from ground_truth.resolver import construir_indice, resolver
from motor.bpm import bpm_refinado
from motor.tonalidad import compat_camelot, tono


def _error_bpm(est: float, gt: float) -> float:
    """Error absoluto tolerando ambigüedad de octava (mitad/doble), como pide §4."""
    return min(abs(est - gt), abs(est - 2 * gt), abs(est - gt / 2))


def medir(xml: Path, raices: list[str], limite: int | None, sr: int = 22050) -> dict:
    import librosa  # import perezoso: el parser/umbrales no necesitan audio

    tracks = parsear(xml)
    indice = construir_indice(raices)

    # Solo tracks con audio resoluble y con al menos BPM o Camelot para comparar.
    candidatos = []
    for t in tracks:
        if t["bpm"] <= 0 and not t["camelot"]:
            continue
        real = resolver(t["location"], raices, indice)
        if real:
            candidatos.append((t, real))

    total_resueltos = len(candidatos)
    if limite:
        candidatos = candidatos[:limite]

    err_bpm, exactas, compatibles, tiempos = [], [], [], []
    n_key = n_bpm = 0
    fallos = []

    for i, (t, real) in enumerate(candidatos, 1):
        try:
            y, _ = librosa.load(real, sr=sr, mono=True)
        except Exception as e:  # noqa: BLE001
            fallos.append((Path(real).name, f"load: {e}"))
            continue
        if y.size < sr:  # < 1 s de audio: no sirve
            fallos.append((Path(real).name, "audio muy corto"))
            continue

        t0 = time.time()
        est_bpm = bpm_refinado(y, sr)
        det = tono(y, sr)
        tiempos.append(time.time() - t0)

        if t["bpm"] > 0:
            n_bpm += 1
            err_bpm.append(_error_bpm(est_bpm, t["bpm"]))
        if t["camelot"]:
            n_key += 1
            est_cam = det["camelot"]
            exactas.append(1.0 if est_cam == t["camelot"] else 0.0)
            # 'compatible' = exacta, relativo o vecino (compat >= 0.8 en la escala del motor).
            compatibles.append(1.0 if compat_camelot(est_cam, t["camelot"]) >= 0.8 else 0.0)

        print(f"  [{i}/{len(candidatos)}] {Path(real).name[:48]:48}  "
              f"BPM {est_bpm:6.1f} (gt {t['bpm']:5.1f})  "
              f"key {det['camelot'] or '?':>3} (gt {t['camelot'] or '?':>3})")

    metricas: dict[str, float] = {}
    if err_bpm:
        metricas["bpm_error_p95"] = float(np.percentile(err_bpm, 95))
    if exactas:
        metricas["tonalidad_exacta"] = 100.0 * float(np.mean(exactas))
        metricas["tonalidad_compatible"] = 100.0 * float(np.mean(compatibles))
    if tiempos:
        metricas["tiempo_analisis_s"] = float(np.percentile(tiempos, 95))

    return {
        "metricas": metricas,
        "n_tracks_analizados": len(tiempos),
        "n_bpm": n_bpm, "n_key": n_key,
        "total_resueltos": total_resueltos, "total_xml": len(tracks),
        "tiempo_medio_s": float(np.mean(tiempos)) if tiempos else 0.0,
        "tiempo_max_s": float(np.max(tiempos)) if tiempos else 0.0,
        "bpm_error_medio": float(np.mean(err_bpm)) if err_bpm else None,
        "bpm_error_max": float(np.max(err_bpm)) if err_bpm else None,
        "fallos": fallos,
    }


def _imprimir(res: dict) -> None:
    m = res["metricas"]
    print(f"\n{'='*64}")
    print(f"Tracks en XML: {res['total_xml']}  ·  con audio resuelto: {res['total_resueltos']}"
          f"  ·  analizados: {res['n_tracks_analizados']}")
    print(f"BPM comparados: {res['n_bpm']}  ·  Key comparadas: {res['n_key']}")
    print(f"Tiempo/track: medio {res['tiempo_medio_s']:.1f}s · máx {res['tiempo_max_s']:.1f}s")
    if res["bpm_error_medio"] is not None:
        print(f"Error BPM: medio {res['bpm_error_medio']:.2f} · máx {res['bpm_error_max']:.2f}")
    print(f"{'-'*64}")
    print("Contra los umbrales de la spec §4:")
    for f in evaluar(m):
        if f.valor is None:
            estado, val = "· sin medir (necesita la radio)", "—"
        else:
            estado = "OK ✓" if f.ok else "ROTO ✗"
            val = f"{f.valor:.2f}{f.umbral.unidad}"
        print(f"  {f.umbral.nombre:34} {val:>12}  (límite {f.umbral.op} "
              f"{f.umbral.limite:g}{f.umbral.unidad})  {estado}")
    if res["fallos"]:
        print(f"{'-'*64}\n{len(res['fallos'])} fallo(s) de carga:")
        for nombre, err in res["fallos"][:10]:
            print(f"  - {nombre}: {err}")
    print(f"{'='*64}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="benchmark.motor_real",
                                 description="Benchmark del motor contra el ground truth de Rekordbox.")
    ap.add_argument("--xml", required=True, type=Path)
    ap.add_argument("--roots", nargs="+", required=True,
                    help="Carpetas donde buscar los audios (ej. D:\\).")
    ap.add_argument("--limit", type=int, default=None, help="Analizar solo los primeros N.")
    args = ap.parse_args(argv)

    res = medir(args.xml, args.roots, args.limit)
    _imprimir(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
