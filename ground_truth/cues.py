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
# Qué es cada POSITION_MARK. Lectura VERIFICADA contra Rekordbox 7.2.16 (prueba 5.68,
# pipeline/PRUEBA_CUES.md, 2026-10-08): importó un XML con hot cues, una memory cue y un loop
# escritos así y los reexportó idénticos; y en los dos exports reales de la biblioteca todas
# las marcas Type=4 traen End (07/09: 154; 08/10: 243, de 0,75 a 6,96 s). Es la
# especificación pública:
#   Type 0 = cue, 1 = fade-in, 2 = fade-out, 3 = load, 4 = loop (lleva End)
#   Num -1 = memory, 0..7 = hot cue A..H
# Antes este módulo leía "Type=4, Num=0, sin nombre" como memory cue anónimo: era un loop en
# el hot cue A.
_TIPO = {"0": "cue", "1": "fade-in", "2": "fade-out", "3": "load", "4": "loop"}
_SLOTS = "ABCDEFGH"


def _zona(rel: float) -> str:
    """En qué parte del track cae una posición relativa (0..1)."""
    if rel < 0.15:
        return "inicio"
    if rel > 0.85:
        return "final"
    return "medio"


def clase(cue: dict) -> str:
    """Qué es la marca: 'memory cue', 'hot cue A', 'hot loop G', 'memory loop', 'fade-in'…"""
    tipo = _TIPO.get(cue["type"], f"Type {cue['type']}")
    num = cue["num"]
    if num == "-1":
        return f"memory {tipo}"
    if num.isdigit() and int(num) < len(_SLOTS):
        return f"hot {tipo} {_SLOTS[int(num)]}"
    return f"{tipo} (Num {num})"


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
    # En la biblioteca real casi todas las marcas en G/H son LOOPS (Type=4), no cues: la
    # hipótesis es sobre dónde el DJ guarda sus loops, no dónde marca la entrada/salida.
    print(f"\n{'-'*70}\nHipótesis 'Num=6/7 = entrada/salida de mezcla' (ver sus clases abajo):")
    for num in ("6", "7"):
        rels = por_num.get(num, [])
        if rels:
            z = Counter(_zona(r) for r in rels)
            dom = z.most_common(1)[0]
            print(f"  Num={num}: {len(rels)} cues · zona dominante '{dom[0]}' ({dom[1]}/{len(rels)}) "
                  f"· mediana rel {sorted(rels)[len(rels)//2]:.2f}")

    # --- Qué es cada marca (lectura verificada, ver `clase`) ---
    print(f"\n{'-'*70}\nClases de marca (cues humanos):")
    for k, n in sorted(Counter(clase(c) for _, c in humanos).items()):
        print(f"  {k:<16} {n}")
    loops = [c for _, c in humanos if c["type"] == "4"]
    con_end = sorted(c["end"] - c["start"] for c in loops if c["end"] is not None)
    print(f"\nloops: {len(loops)} · con End {len(con_end)} · sin End {len(loops) - len(con_end)}")
    if con_end:
        print(f"  duración: min {con_end[0]:.2f} s · mediana {con_end[len(con_end)//2]:.2f} s "
              f"· max {con_end[-1]:.2f} s")


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
