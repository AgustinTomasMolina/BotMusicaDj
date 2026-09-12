"""Análisis de los cue points del XML de Rekordbox — preparación de #5.4.

104 de 220 tracks tienen cues marcados A MANO: son ground truth de ESTRUCTURA (dónde
entra/sale una mezcla, dónde está el drop). Este módulo SOLO describe esos cues con
números; NO detecta cues automáticamente (eso es #5.4, otra tarea).

    python -m ground_truth.cues --xml Rekordbox.xml

El XML no está versionado (.gitignore excluye *.xml): la ruta va por parámetro.
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

from ground_truth.rekordbox import parsear

# Cues que Rekordbox autogenera (grilla de compases): NO son decisiones humanas.
_AUTOGENERADO = "1.1Bars"
# Tolerancia para considerar que dos cues del mismo track están "en la misma posición".
_TOL_COINCIDE_S = 1.0


def _zona(rel: float) -> str:
    """En qué parte del track cae una posición relativa (0..1)."""
    if rel < 0.15:
        return "inicio"
    if rel > 0.85:
        return "final"
    return "medio"


def analizar(tracks: list[dict]) -> None:
    con_cues = [t for t in tracks if t["cues"]]
    todos = [(t, c) for t in con_cues for c in t["cues"]]
    print(f"{'='*70}\nCues del ground truth — {len(con_cues)} tracks, {len(todos)} cues\n{'='*70}")

    print("\ncues por track:", dict(sorted(Counter(len(t['cues']) for t in con_cues).items())))
    print("por Type      :", dict(sorted(Counter(c['type'] for _, c in todos).items())))
    print("por Num       :", dict(sorted(Counter(c['num'] for _, c in todos).items(),
                                         key=lambda kv: int(kv[0]))))

    # --- Separar los autogenerados (no son decisión humana) ---
    auto = [(t, c) for t, c in todos if c["name"] == _AUTOGENERADO]
    humanos = [(t, c) for t, c in todos if c["name"] != _AUTOGENERADO]
    print(f"\nautogenerados Name='{_AUTOGENERADO}': {len(auto)}  → se excluyen del análisis humano")
    print(f"cues 'humanos' (sin los autogenerados): {len(humanos)}")

    # --- Posición relativa por slot (Num) ---
    print(f"\n{'-'*70}\nPosición RELATIVA (cue / TotalTime) por slot Num — solo cues humanos:")
    por_num = defaultdict(list)
    for t, c in humanos:
        if t["duration_s"] > 0:
            por_num[c["num"]].append(c["start"] / t["duration_s"])
    for num in sorted(por_num, key=int):
        rels = sorted(por_num[num])
        n = len(rels)
        med = rels[n // 2]
        zonas = Counter(_zona(r) for r in rels)
        print(f"  Num={num:>3}  n={n:3}  rel min {rels[0]:.2f} · mediana {med:.2f} · max {rels[-1]:.2f}"
              f"  zonas {dict(zonas)}")

    # --- Hipótesis: Num=6 y Num=7 = entrada/salida de mezcla (convención propia) ---
    print(f"\n{'-'*70}\nHipótesis 'Num=6/7 = entrada/salida de mezcla':")
    for num in ("6", "7"):
        rels = por_num.get(num, [])
        if rels:
            z = Counter(_zona(r) for r in rels)
            dom = z.most_common(1)[0]
            print(f"  Num={num}: {len(rels)} cues · zona dominante '{dom[0]}' ({dom[1]}/{len(rels)}) "
                  f"· mediana rel {sorted(rels)[len(rels)//2]:.2f}")

    # --- Memory cues sin nombre (Type=4, Num=0): ¿coinciden con otro cue del track? ---
    print(f"\n{'-'*70}\nMemory cues sin nombre (Type=4, Num=0):")
    coinciden = solos = 0
    total_mem = 0
    for t in con_cues:
        mem = [c for c in t["cues"] if c["type"] == "4" and c["num"] == "0" and not c["name"]]
        otros = [c for c in t["cues"] if not (c["type"] == "4" and c["num"] == "0" and not c["name"])]
        for m in mem:
            total_mem += 1
            if any(abs(m["start"] - o["start"]) <= _TOL_COINCIDE_S for o in otros):
                coinciden += 1
            else:
                solos += 1
    print(f"  total: {total_mem}")
    print(f"  coinciden (±{_TOL_COINCIDE_S:g}s) con OTRO cue del mismo track: {coinciden}")
    print(f"  solos (no coinciden con ningún otro cue): {solos}")
    print("  → si casi todos están 'solos', son marcadores independientes, no duplican hot cues")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="ground_truth.cues",
                                 description="Describe los cue points del XML de Rekordbox (solo números).")
    ap.add_argument("--xml", required=True, type=Path, help="XML exportado de Rekordbox.")
    args = ap.parse_args(argv)
    analizar(parsear(args.xml))
    return 0


if __name__ == "__main__":
    sys.exit(main())
