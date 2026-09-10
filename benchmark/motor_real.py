"""Atajo: corre las dos etapas del benchmark seguidas, desde el XML de Rekordbox.

El trabajo real vive en dos módulos desacoplados:

  `benchmark.analizar`  (etapa A) — recorre audio y escribe un CSV por track. Es la parte
                        cara y la que tiene que estar donde están los archivos.
  `benchmark.evaluar`   (etapa B) — cruza ese CSV contra el ground truth y saca las
                        métricas de §4. Solo lee CSVs, corre en cualquier máquina.

Este módulo es la comodidad de tenerlas juntas cuando el audio y el XML están en la misma
máquina. Si están separados (el caso normal: audio en un pendrive, ground truth en otra
compu), se corren las etapas por separado:

    python -m benchmark.analizar --audio D:\\ --consenso
    python -m benchmark.evaluar --analisis benchmark/out/analisis_....csv \\
                                --ground-truth ground_truth/out/rekordbox_tracks.csv

Lo que agrega este atajo sobre `analizar --audio`: usa el XML para saber QUÉ tracks buscar
y `ground_truth.resolver` para reubicarlos si las rutas del XML quedaron viejas.

    python -m benchmark.motor_real --xml Rekordbox.xml --roots D:\\ --limit 45 [--consenso]

NO modifica los audios: solo los lee (regla del proyecto).
"""
import argparse
import random
import sys
from dataclasses import asdict
from pathlib import Path

from benchmark.analizar import SEMILLA, analizar
from benchmark.analizar import escribir_csv as escribir_analisis
from benchmark.evaluar import cruzar, informe
from benchmark.evaluar import escribir_csv as escribir_evaluacion
from ground_truth.rekordbox import escribir_csv as escribir_gt
from ground_truth.rekordbox import parsear
from ground_truth.resolver import AMBIGUO, NO_ENCONTRADO, construir_indice, resolver


def resolver_rutas(tracks: list[dict], raices: list[str]) -> dict:
    """Ubica el audio de cada track del XML. Los ambiguos NO entran: resolver un homónimo
    a la suerte haría que el motor analice otro archivo y el fallo se vería igual que un
    error de algoritmo."""
    indice = construir_indice(raices)
    rutas: list[str] = []
    ambiguos: list[tuple[str, tuple[str, ...]]] = []
    no_encontrados = 0
    for t in tracks:
        if t["bpm"] <= 0 and not t["camelot"]:
            continue                      # sin nada contra qué comparar
        r = resolver(t["location"], raices, indice)
        if r.estado == AMBIGUO:
            ambiguos.append((Path(t["location"]).name, r.candidatos))
        elif r.estado == NO_ENCONTRADO:
            no_encontrados += 1
        else:
            rutas.append(r.ruta)
    return {"rutas": rutas, "ambiguos": ambiguos, "no_encontrados": no_encontrados}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="benchmark.motor_real",
        description="Atajo: analiza (etapa A) y evalúa (etapa B) contra el GT de Rekordbox.")
    ap.add_argument("--xml", required=True, type=Path)
    ap.add_argument("--roots", nargs="+", required=True,
                    help="Carpetas donde buscar los audios (ej. D:\\).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Analizar solo N tracks, como muestra aleatoria (no los primeros).")
    ap.add_argument("--seed", type=int, default=SEMILLA,
                    help=f"Semilla del muestreo (default {SEMILLA}, para que sea reproducible).")
    ap.add_argument("--consenso", action="store_true",
                    help="Usar tono_consenso() en vez de tono(). Corré la misma semilla con "
                         "y sin el flag para el antes/después que pide la spec §5.")
    ap.add_argument("--out", type=Path, default=Path("benchmark/out"))
    ap.add_argument("--gt-out", type=Path, default=Path("ground_truth/out"),
                    help="Dónde dejar el CSV del ground truth derivado del XML.")
    args = ap.parse_args(argv)

    # --- ground truth ---
    tracks = parsear(args.xml)
    gt_csv, _ = escribir_gt(tracks, args.gt_out)
    print(f"Ground truth: {len(tracks)} tracks desde {args.xml.name} → {gt_csv}")

    # --- resolver dónde está cada audio ---
    res = resolver_rutas(tracks, args.roots)
    rutas = res["rutas"]
    print(f"Audio resuelto: {len(rutas)}  ·  excluidos: {len(res['ambiguos'])} ambiguos "
          f"(nombre repetido) · {res['no_encontrados']} sin archivo")
    for nombre, cands in res["ambiguos"][:10]:
        print(f"  ambiguo: {nombre[:44]:44} {len(cands)} candidatos")
    if not rutas:
        print("Ningún audio resuelto: revisá --roots.")
        return 1

    if args.limit and args.limit < len(rutas):
        # Muestra aleatoria con semilla fija — el orden del XML es el de la colección y
        # tomar la cabeza mediría ese orden, no la biblioteca.
        rutas = sorted(random.Random(args.seed).sample(rutas, args.limit))
        print(f"Muestra aleatoria de {len(rutas)} (semilla {args.seed})")

    # --- etapa A ---
    print(f"\nEtapa A — analizando {len(rutas)} tracks…")
    filas, fallos = analizar(rutas, consenso=args.consenso)
    if not filas:
        print("Ningún track se pudo analizar.")
        return 1
    sufijo = "_consenso" if args.consenso else ""
    a_csv = escribir_analisis(filas, args.out, sufijo=sufijo)
    print(f"→ {a_csv}" + (f"  ({len(fallos)} sin poder cargar)" if fallos else ""))

    # --- etapa B ---
    cruce = cruzar([asdict(f) for f in filas], tracks)
    informe(cruce)
    if cruce["cruces"]:
        e_csv = escribir_evaluacion(cruce["cruces"], args.out, sufijo=sufijo)
        print(f"Análisis por track   → {a_csv}")
        print(f"Evaluación por track → {e_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
