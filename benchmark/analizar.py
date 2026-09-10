"""Etapa A del benchmark: analizar audio y escribir un CSV por track.

Esta etapa NO compara contra nada y NO necesita el XML de Rekordbox: recorre una carpeta
de audios, corre el motor sobre cada uno y anota lo que midió. Es la mitad cara (lee y
decodifica audio) y la que necesita estar donde están los archivos.

La comparación contra el ground truth vive en `benchmark.evaluar`, que solo lee CSVs.
Están separadas a propósito: el audio suele estar en un pendrive o en otra máquina, y el
ground truth en otra. Sin la separación no se puede medir nada hasta tener las dos cosas
juntas.

    python -m benchmark.analizar --audio D:\\ --consenso

NO modifica los audios: solo los lee (regla del proyecto).
"""
import argparse
import csv
import datetime
import random
import sys
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from motor.bpm import bpm_refinado
from motor.tonalidad import tono, tono_consenso

EXTS = (".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg")

# Semilla por defecto del muestreo de --limit. Fija a propósito: dos corridas con la misma
# carpeta y el mismo --limit tienen que analizar EXACTAMENTE los mismos tracks, o comparar
# un "antes y después" de un cambio de motor no significa nada.
SEMILLA = 20260908

METODO_SIMPLE = "tono"
METODO_CONSENSO = "tono_consenso"


@dataclass
class FilaAnalisis:
    """Una fila del CSV de la etapa A: solo lo que el motor midió, sin juicio."""

    archivo: str          # basename — es la clave con la que la etapa B cruza contra el GT
    ruta: str
    duracion_s: float
    bpm_est: float
    key_est: str
    confianza: float
    acuerdo: str          # "2/3" con --consenso; "" con tono() simple
    tramos: str           # "6A|6A|1A" con --consenso; "" con tono() simple
    t_carga_s: float
    t_analisis_s: float
    t_total_s: float
    metodo: str           # tono | tono_consenso — queda registrado qué produjo la fila


COLUMNAS = [f.name for f in fields(FilaAnalisis)]


def rutas_de(carpeta: Path) -> list[str]:
    """Audios de la carpeta, recursivo y en orden estable."""
    return sorted(str(p) for p in carpeta.rglob("*") if p.suffix.lower() in EXTS)


def muestrear(rutas: list[str], limite: int | None, semilla: int = SEMILLA) -> list[str]:
    """Muestra ALEATORIA con semilla fija, no los primeros N: el orden de una carpeta es
    alfabético y tomar la cabeza mide ese orden, no la biblioteca."""
    if not limite or limite >= len(rutas):
        return rutas
    elegidas = random.Random(semilla).sample(rutas, limite)
    return sorted(elegidas)   # salida estable


def analizar_uno(ruta: str, sr: int = 22050, consenso: bool = False) -> FilaAnalisis | None:
    """Corre el motor sobre un archivo. `None` si no se pudo cargar o es muy corto."""
    import librosa

    t0 = time.perf_counter()
    try:
        y, _ = librosa.load(ruta, sr=sr, mono=True)
    except Exception:  # noqa: BLE001
        return None
    t_carga = time.perf_counter() - t0
    if y.size < sr:  # < 1 s de audio: no sirve
        return None

    t1 = time.perf_counter()
    bpm = bpm_refinado(y, sr)
    det = tono_consenso(y, sr) if consenso else tono(y, sr)
    t_analisis = time.perf_counter() - t1

    ganados, total = det.get("acuerdo", (0, 0))
    return FilaAnalisis(
        archivo=Path(ruta).name, ruta=ruta, duracion_s=round(y.size / sr, 1),
        bpm_est=round(float(bpm), 2), key_est=det["camelot"],
        confianza=float(det["confianza"]),
        acuerdo=f"{ganados}/{total}" if total else "",
        tramos="|".join(det.get("tramos", [])),
        t_carga_s=round(t_carga, 3), t_analisis_s=round(t_analisis, 3),
        t_total_s=round(t_carga + t_analisis, 3),
        metodo=METODO_CONSENSO if consenso else METODO_SIMPLE,
    )


def analizar(rutas: list[str], sr: int = 22050, consenso: bool = False,
             progreso: bool = True, warmup: bool = True) -> tuple[list[FilaAnalisis], list[str]]:
    """Analiza cada ruta. `warmup` fuerza la compilación JIT ANTES de cronometrar.

    Sin eso, librosa/numba compilan durante el primer track y le cargan varios segundos
    (96 s medidos en una máquina fría): el tiempo del track 1 sale inventado y arrastra el
    p95 del umbral §4.
    """
    if warmup:
        from benchmark.tiempo_analisis import calentar
        t = calentar(sr)
        if progreso:
            print(f"  warm-up (compilación JIT): {t:.1f} s — no se cuenta", flush=True)

    filas, fallos = [], []
    for i, r in enumerate(rutas, 1):
        fila = analizar_uno(r, sr=sr, consenso=consenso)
        if fila is None:
            fallos.append(Path(r).name)
            continue
        filas.append(fila)
        if progreso:
            extra = f" {fila.acuerdo}" if fila.acuerdo else ""
            print(f"  [{i}/{len(rutas)}] {fila.archivo[:44]:44} "
                  f"BPM {fila.bpm_est:6.1f}  key {fila.key_est:>3}{extra}  "
                  f"{fila.t_total_s:5.2f}s", flush=True)
    return filas, fallos


def escribir_csv(filas: list[FilaAnalisis], out_dir: Path, sufijo: str = "") -> Path:
    """Un CSV por corrida, con fecha en el nombre para no pisar corridas anteriores."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = out_dir / f"analisis_{sello}{sufijo}.csv"
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS)
        w.writeheader()
        for f in filas:
            w.writerow(asdict(f))
    return destino


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="benchmark.analizar",
        description="Etapa A: corre el motor sobre una carpeta de audio y escribe un CSV.")
    ap.add_argument("--audio", required=True, type=Path, help="Carpeta con los audios.")
    ap.add_argument("--consenso", action="store_true",
                    help="Usar tono_consenso() (voto entre 3 tramos) en vez de tono().")
    ap.add_argument("--limit", type=int, default=None,
                    help="Analizar solo N tracks, como muestra aleatoria (no los primeros).")
    ap.add_argument("--seed", type=int, default=SEMILLA,
                    help=f"Semilla del muestreo (default {SEMILLA}, para que sea reproducible).")
    ap.add_argument("--out", type=Path, default=Path("benchmark/out"))
    args = ap.parse_args(argv)

    rutas = muestrear(rutas_de(args.audio), args.limit, args.seed)
    if not rutas:
        print(f"No encontré audio en {args.audio}")
        return 1

    print(f"Analizando {len(rutas)} tracks con "
          f"{METODO_CONSENSO if args.consenso else METODO_SIMPLE}…")
    filas, fallos = analizar(rutas, consenso=args.consenso)
    if not filas:
        print("Ningún track se pudo analizar.")
        return 1

    destino = escribir_csv(filas, args.out, sufijo="_consenso" if args.consenso else "")
    print(f"\n{len(filas)} tracks analizados"
          + (f" · {len(fallos)} sin poder cargar" if fallos else ""))
    print(f"→ {destino}")
    print("\nPara evaluarlo contra el ground truth:")
    print(f"  python -m benchmark.evaluar --analisis {destino} "
          f"--ground-truth ground_truth/out/rekordbox_tracks.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
