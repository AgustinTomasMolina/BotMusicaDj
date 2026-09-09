"""Benchmark del motor contra el ground truth REAL de Rekordbox (tareas F0 #4, #6, #9).

Corre el motor (BPM + tonalidad) sobre los audios de verdad, resueltos con
`ground_truth.resolver` (los archivos viven en un pendrive / otra carpeta), y compara
contra el BPM/Camelot que puso Rekordbox. Calcula las métricas de la spec §4 que dependen
del análisis por track (las que dependen de la RADIO —transiciones, choques, energía,
latencia— quedan sin medir hasta que exista el motor de secuencia).

    python -m benchmark.motor_real --xml Rekordbox.xml --roots D:\\ --limit 40

Cada corrida deja un CSV por track en `benchmark/out/corrida_<fecha>.csv`. Sin el detalle
por track solo se ve el agregado: no se puede decir CUÁLES tracks fallan ni por qué.

NO modifica los audios: solo los lee (regla del proyecto).
"""
import argparse
import csv
import datetime
import sys
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np

from benchmark.clasificacion import OCTAVA, TRESILLO, clasificar, resumen
from benchmark.umbrales import evaluar
from ground_truth.rekordbox import parsear
from ground_truth.resolver import construir_indice, resolver
from motor.bpm import bpm_refinado
from motor.tonalidad import compat_camelot, tono

# Umbral de BPM de la spec §4, para el veredicto por track. La tabla canónica vive en
# benchmark/umbrales.py; acá se referencia el mismo número para no duplicar el contrato.
_UMBRAL_BPM = 1.0


def _error_bpm(est: float, gt: float) -> float:
    """Error absoluto tolerando ambigüedad de octava (mitad/doble), como pide §4.

    OJO: por diseño esto puntúa 0.00 un half-time (est=76, gt=152). Es el CONTRATO de la
    spec, no un bug — pero hace invisible esa clase de fallo. Por eso se guarda también el
    error crudo (`_error_crudo`): el veredicto sale del tolerante, el diagnóstico del crudo.
    """
    return min(abs(est - gt), abs(est - 2 * gt), abs(est - gt / 2))


def _error_crudo(est: float, gt: float) -> float:
    """Error absoluto sin tolerar nada. Es el que delata octavas y tresillos."""
    return abs(est - gt)


@dataclass
class Registro:
    """Una fila del CSV: todo lo medido para un track."""

    archivo: str
    artista: str
    titulo: str
    ruta: str
    # BPM
    bpm_est: float
    bpm_ref: float
    err_crudo: float | None
    err_octava: float | None      # el tolerante a octava — es el del umbral §4
    veredicto_bpm: str            # OK | ROTO | sin-referencia
    clase_bpm: str                # exacto | octava | tresillo | otro-multiplo-racional | error-genuino
    ratio_bpm: float | None       # ratio est/ref del múltiplo detectado (2.0, 1.5, …)
    # Tonalidad
    key_est: str
    key_ref: str
    confianza: float
    key_exacta: str               # si | no | sin-referencia
    key_compatible: str           # si | no | sin-referencia
    # Tiempos
    t_carga_s: float
    t_analisis_s: float
    t_total_s: float
    duracion_s: float


def escribir_csv(registros: list[Registro], out_dir: Path) -> Path:
    """Un CSV por corrida, con fecha en el nombre para no pisar corridas anteriores."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = out_dir / f"corrida_{sello}.csv"
    cols = [f.name for f in fields(Registro)]
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in registros:
            w.writerow(asdict(r))
    return destino


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

    registros: list[Registro] = []
    err_bpm, exactas, compatibles, tiempos = [], [], [], []
    n_key = n_bpm = 0
    fallos = []

    for i, (t, real) in enumerate(candidatos, 1):
        t_carga0 = time.time()
        try:
            y, _ = librosa.load(real, sr=sr, mono=True)
        except Exception as e:  # noqa: BLE001
            fallos.append((Path(real).name, f"load: {e}"))
            continue
        t_carga = time.time() - t_carga0
        if y.size < sr:  # < 1 s de audio: no sirve
            fallos.append((Path(real).name, "audio muy corto"))
            continue

        t0 = time.time()
        est_bpm = bpm_refinado(y, sr)
        det = tono(y, sr)
        t_analisis = time.time() - t0
        tiempos.append(t_analisis)

        # --- BPM ---
        ec = eo = None
        veredicto = "sin-referencia"
        cls = clasificar(est_bpm, t["bpm"], umbral=_UMBRAL_BPM)
        if t["bpm"] > 0:
            n_bpm += 1
            eo = _error_bpm(est_bpm, t["bpm"])
            ec = _error_crudo(est_bpm, t["bpm"])
            err_bpm.append(eo)
            veredicto = "OK" if eo <= _UMBRAL_BPM else "ROTO"

        # --- Tonalidad ---
        est_cam = det["camelot"]
        exacta = compatible = "sin-referencia"
        if t["camelot"]:
            n_key += 1
            es_exacta = est_cam == t["camelot"]
            # 'compatible' = exacta, relativo o vecino (compat >= 0.8 en la escala del motor).
            es_compat = compat_camelot(est_cam, t["camelot"]) >= 0.8
            exactas.append(1.0 if es_exacta else 0.0)
            compatibles.append(1.0 if es_compat else 0.0)
            exacta = "si" if es_exacta else "no"
            compatible = "si" if es_compat else "no"

        registros.append(Registro(
            archivo=Path(real).name, artista=t["artist"], titulo=t["name"], ruta=real,
            bpm_est=round(est_bpm, 2), bpm_ref=round(t["bpm"], 2),
            err_crudo=None if ec is None else round(ec, 2),
            err_octava=None if eo is None else round(eo, 2),
            veredicto_bpm=veredicto, clase_bpm=cls.clase, ratio_bpm=(
                None if cls.ratio is None else round(cls.ratio, 4)),
            key_est=est_cam or "?", key_ref=t["camelot"] or "?",
            confianza=det["confianza"], key_exacta=exacta, key_compatible=compatible,
            t_carga_s=round(t_carga, 2), t_analisis_s=round(t_analisis, 2),
            t_total_s=round(t_carga + t_analisis, 2), duracion_s=round(len(y) / sr, 1),
        ))

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
        "registros": registros,
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
    print(f"\n{'=' * 64}")
    print(f"Tracks en XML: {res['total_xml']}  ·  con audio resuelto: {res['total_resueltos']}"
          f"  ·  analizados: {res['n_tracks_analizados']}")
    print(f"BPM comparados: {res['n_bpm']}  ·  Key comparadas: {res['n_key']}")
    print(f"Tiempo/track: medio {res['tiempo_medio_s']:.1f}s · máx {res['tiempo_max_s']:.1f}s")
    if res["bpm_error_medio"] is not None:
        print(f"Error BPM: medio {res['bpm_error_medio']:.2f} · máx {res['bpm_error_max']:.2f}")
    print(f"{'-' * 64}")
    print("Contra los umbrales de la spec §4:")
    for f in evaluar(m):
        if f.valor is None:
            estado, val = "· sin medir (necesita la radio)", "—"
        else:
            estado = "OK ✓" if f.ok else "ROTO ✗"
            val = f"{f.valor:.2f}{f.umbral.unidad}"
        print(f"  {f.umbral.nombre:34} {val:>12}  (límite {f.umbral.op} "
              f"{f.umbral.limite:g}{f.umbral.unidad})  {estado}")

    con_ref = [r for r in res["registros"] if r.veredicto_bpm != "sin-referencia"]
    if con_ref:
        print(f"{'-' * 64}\nDesacuerdos de BPM por clase ({len(con_ref)} tracks con referencia):")
        for clase, n in resumen([r.clase_bpm for r in con_ref]).items():
            print(f"  {clase:26} {n:4}")

    rotos = [r for r in res["registros"] if r.veredicto_bpm == "ROTO"]
    if rotos:
        print(f"{'-' * 64}\nTracks FUERA del umbral de BPM ({len(rotos)}):")
        for r in sorted(rotos, key=lambda r: -(r.err_octava or 0)):
            print(f"  {r.archivo[:40]:40} est {r.bpm_est:6.1f} ref {r.bpm_ref:6.1f}  "
                  f"crudo {r.err_crudo:6.2f}  octava {r.err_octava:5.2f}  {r.clase_bpm}")

    # Los que el umbral tolera a propósito (§4) pero SON un desacuerdo de nivel métrico.
    # Sin esta sección quedan invisibles: puntúan 0.00 en el p95.
    tolerados = [r for r in res["registros"]
                 if r.veredicto_bpm == "OK" and r.clase_bpm in (OCTAVA, TRESILLO)]
    if tolerados:
        print(f"{'-' * 64}\nDesacuerdos que el umbral TOLERA ({len(tolerados)}) — "
              f"pasan §4 pero motor y referencia no están en el mismo nivel métrico:")
        for r in sorted(tolerados, key=lambda r: -(r.err_crudo or 0)):
            print(f"  {r.archivo[:40]:40} est {r.bpm_est:6.1f} ref {r.bpm_ref:6.1f}  "
                  f"crudo {r.err_crudo:6.2f}  octava {r.err_octava:5.2f}  "
                  f"{r.clase_bpm} (x{r.ratio_bpm})")

    if res["fallos"]:
        print(f"{'-' * 64}\n{len(res['fallos'])} fallo(s) de carga:")
        for nombre, err in res["fallos"][:10]:
            print(f"  - {nombre}: {err}")
    print(f"{'=' * 64}")


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
    ap.add_argument("--out", type=Path, default=Path("benchmark/out"),
                    help="Carpeta donde dejar el CSV por track.")
    args = ap.parse_args(argv)

    res = medir(args.xml, args.roots, args.limit)
    _imprimir(res)
    if res["registros"]:
        destino = escribir_csv(res["registros"], args.out)
        print(f"Detalle por track → {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
