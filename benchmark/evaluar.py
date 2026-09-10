"""Etapa B del benchmark: comparar el CSV de la etapa A contra el ground truth.

NO toca audio. Lee dos CSVs y produce las métricas de la spec §4, la lista de tracks
fuera de umbral y la tabla de calibración de la confianza. Por eso corre en cualquier
máquina, sin la biblioteca de audio ni librosa.

    python -m benchmark.evaluar --analisis benchmark/out/analisis_....csv \\
                                --ground-truth ground_truth/out/rekordbox_tracks.csv

El cruce se hace por NOMBRE DE ARCHIVO (basename, sin distinguir mayúsculas). Si un
basename aparece más de una vez de cualquiera de los dos lados, el track se marca ambiguo
y queda FUERA del cómputo: en una biblioteca de DJ los homónimos son normales (original vs
edit, WAV vs MP3) y elegir uno al azar falsea el resultado igual que un error de motor.
"""
import argparse
import csv
import datetime
import statistics
import sys
from dataclasses import dataclass, fields
from pathlib import Path

from benchmark.clasificacion import OCTAVA, TRESILLO, clasificar, resumen
from benchmark.umbrales import UMBRALES
from benchmark.umbrales import evaluar as evaluar_umbrales
from motor.tonalidad import compat_camelot

# Umbral de BPM de la spec §4, para el veredicto por track. La tabla canónica está en
# benchmark/umbrales.py; acá se lee de ahí para no duplicar el contrato.
UMBRAL_BPM = next(u.limite for u in UMBRALES if u.clave == "bpm_error_p95")
UMBRAL_TIEMPO = next(u.limite for u in UMBRALES if u.clave == "tiempo_analisis_s")

# 'compatible' = exacta, relativo o vecino, que en la escala de compat_camelot es >= 0.8.
_COMPAT_MIN = 0.8


@dataclass
class Cruce:
    """Un track presente en las dos puntas, ya comparado."""

    archivo: str
    artista: str
    titulo: str
    # BPM
    bpm_est: float
    bpm_ref: float
    err_crudo: float | None
    err_octava: float | None      # tolerante a mitad/doble — es el del umbral §4
    veredicto_bpm: str            # OK | ROTO | sin-referencia
    clase_bpm: str
    ratio_bpm: float | None
    # Tonalidad
    key_est: str
    key_ref: str
    confianza: float
    acuerdo: str
    tramos: str
    key_exacta: str               # si | no | sin-referencia
    key_compatible: str           # si | no | sin-referencia
    # Tiempo
    t_total_s: float
    metodo: str


def _error_bpm_tolerante(est: float, gt: float) -> float:
    """Error absoluto tolerando ambigüedad de octava (mitad/doble), como pide §4.

    Por diseño puntúa 0.00 un half-time (est=76, gt=152). Es el CONTRATO de la spec, no un
    bug — pero hace invisible esa clase de fallo, por eso se guarda también el crudo.
    """
    return min(abs(est - gt), abs(est - 2 * gt), abs(est - gt / 2))


def leer_csv(ruta: Path) -> list[dict]:
    with ruta.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _indexar(filas: list[dict], clave: str) -> tuple[dict[str, dict], set[str]]:
    """basename.lower() → fila. Devuelve también los basenames repetidos (ambiguos)."""
    idx: dict[str, dict] = {}
    repetidos: set[str] = set()
    for f in filas:
        bruto = (f.get(clave) or "").strip()
        if not bruto:
            continue
        nombre = Path(bruto.replace("\\", "/")).name.lower()
        if not nombre:
            continue
        if nombre in idx:
            repetidos.add(nombre)
        idx[nombre] = f
    return idx, repetidos


def cruzar(analisis: list[dict], gt: list[dict]) -> dict:
    """Une las dos tablas por basename y compara. No descarta silenciosamente: cuenta."""
    idx_a, rep_a = _indexar(analisis, "archivo")
    idx_g, rep_g = _indexar(gt, "location")
    ambiguos = sorted(rep_a | rep_g)

    cruces: list[Cruce] = []
    for nombre, fa in idx_a.items():
        if nombre in ambiguos:
            continue
        fg = idx_g.get(nombre)
        if fg is None:
            continue

        bpm_est, bpm_ref = _f(fa.get("bpm_est")), _f(fg.get("bpm"))
        ec = eo = None
        veredicto = "sin-referencia"
        if bpm_ref > 0:
            eo = round(_error_bpm_tolerante(bpm_est, bpm_ref), 2)
            ec = round(abs(bpm_est - bpm_ref), 2)
            veredicto = "OK" if eo <= UMBRAL_BPM else "ROTO"
        cls = clasificar(bpm_est, bpm_ref, umbral=UMBRAL_BPM)

        key_est = (fa.get("key_est") or "?").strip()
        key_ref = (fg.get("camelot") or "").strip()
        exacta = compatible = "sin-referencia"
        if key_ref:
            exacta = "si" if key_est == key_ref else "no"
            compatible = "si" if compat_camelot(key_est, key_ref) >= _COMPAT_MIN else "no"

        cruces.append(Cruce(
            archivo=fa.get("archivo", nombre),
            artista=fg.get("artist", ""), titulo=fg.get("name", ""),
            bpm_est=bpm_est, bpm_ref=bpm_ref, err_crudo=ec, err_octava=eo,
            veredicto_bpm=veredicto, clase_bpm=cls.clase,
            ratio_bpm=None if cls.ratio is None else round(cls.ratio, 4),
            key_est=key_est, key_ref=key_ref or "?",
            confianza=_f(fa.get("confianza")), acuerdo=fa.get("acuerdo", ""),
            tramos=fa.get("tramos", ""),
            key_exacta=exacta, key_compatible=compatible,
            t_total_s=_f(fa.get("t_total_s")), metodo=fa.get("metodo", ""),
        ))

    cruces.sort(key=lambda c: c.archivo.lower())
    return {
        "cruces": cruces,
        "ambiguos": ambiguos,
        "solo_analisis": sorted(set(idx_a) - set(idx_g) - set(ambiguos)),
        "solo_gt": sorted(set(idx_g) - set(idx_a) - set(ambiguos)),
        "n_analisis": len(idx_a), "n_gt": len(idx_g),
    }


def _p95(xs: list[float]) -> float:
    """Percentil 95 sin numpy (interpolación lineal), para no atar la etapa B a numpy."""
    if not xs:
        return 0.0
    orden = sorted(xs)
    if len(orden) == 1:
        return orden[0]
    pos = 0.95 * (len(orden) - 1)
    bajo = int(pos)
    frac = pos - bajo
    if bajo + 1 >= len(orden):
        return orden[-1]
    return orden[bajo] + frac * (orden[bajo + 1] - orden[bajo])


def metricas(cruces: list[Cruce]) -> dict:
    """Las métricas de §4 que dependen del análisis por track, más los dos p95 de BPM."""
    eo = [c.err_octava for c in cruces if c.err_octava is not None]
    ec = [c.err_crudo for c in cruces if c.err_crudo is not None]
    ex = [1.0 if c.key_exacta == "si" else 0.0 for c in cruces if c.key_exacta != "sin-referencia"]
    co = [1.0 if c.key_compatible == "si" else 0.0 for c in cruces if c.key_compatible != "sin-referencia"]
    ts = [c.t_total_s for c in cruces if c.t_total_s > 0]

    m: dict[str, float] = {}
    if eo:
        m["bpm_error_p95"] = _p95(eo)
    if ex:
        m["tonalidad_exacta"] = 100.0 * statistics.mean(ex)
        m["tonalidad_compatible"] = 100.0 * statistics.mean(co)
    if ts:
        m["tiempo_analisis_s"] = _p95(ts)

    extra = {
        "bpm_p95_crudo": _p95(ec) if ec else None,
        "bpm_p95_tolerante": _p95(eo) if eo else None,
        "bpm_medio_crudo": statistics.mean(ec) if ec else None,
        "bpm_medio_tolerante": statistics.mean(eo) if eo else None,
        "tiempo_medio_s": statistics.mean(ts) if ts else None,
        "tiempo_p95_s": _p95(ts) if ts else None,
        "sobre_umbral_tiempo": sum(1 for t in ts if t > UMBRAL_TIEMPO),
        "n_tiempo": len(ts), "n_bpm": len(eo), "n_key": len(ex),
    }
    return {"umbrales": m, "extra": extra}


def calibracion(cruces: list[Cruce]) -> dict:
    """¿La confianza predice el acierto de tonalidad? Un número, no una impresión.

    Devuelve el acierto por tramo de confianza y la correlación de Pearson entre la
    confianza y el acierto (0/1). Si la correlación es ~0, la confianza no informa.
    """
    datos = [(c.confianza, 1.0 if c.key_exacta == "si" else 0.0)
             for c in cruces if c.key_exacta != "sin-referencia"]
    if not datos:
        return {"n": 0, "tramos": [], "pearson": None}

    conf = [d[0] for d in datos]
    acierto = [d[1] for d in datos]

    tramos = []
    for lo, hi, etiqueta in ((0.0, 0.34, "0.33 — los 3 tramos distintos"),
                             (0.34, 0.67, "0.34-0.66"),
                             (0.67, 0.99, "0.67 — 2 de 3 coinciden"),
                             (0.99, 1.01, "1.00 — los 3 coinciden")):
        g = [a for c, a in datos if lo <= c < hi]
        if g:
            tramos.append((etiqueta, len(g), 100.0 * statistics.mean(g)))

    pearson = None
    if len(datos) >= 3 and len(set(conf)) > 1 and len(set(acierto)) > 1:
        mc, ma = statistics.mean(conf), statistics.mean(acierto)
        num = sum((c - mc) * (a - ma) for c, a in datos)
        den = (sum((c - mc) ** 2 for c in conf) * sum((a - ma) ** 2 for a in acierto)) ** 0.5
        pearson = num / den if den else None

    return {"n": len(datos), "tramos": tramos, "pearson": pearson,
            "acierto_global": 100.0 * statistics.mean(acierto)}


def escribir_csv(cruces: list[Cruce], out_dir: Path, sufijo: str = "") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = out_dir / f"evaluacion_{sello}{sufijo}.csv"
    cols = [f.name for f in fields(Cruce)]
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for c in cruces:
            w.writerow({k: getattr(c, k) for k in cols})
    return destino


def informe(res: dict) -> None:
    cruces = res["cruces"]
    print(f"\n{'=' * 72}")
    print(f"Cruzados: {len(cruces)}  ·  análisis: {res['n_analisis']}  ·  GT: {res['n_gt']}")
    print(f"Excluidos: {len(res['ambiguos'])} ambiguos (nombre repetido) · "
          f"{len(res['solo_analisis'])} sin GT · {len(res['solo_gt'])} sin audio analizado")
    if not cruces:
        print("Nada que evaluar: no hubo tracks en común.")
        return

    met = metricas(cruces)
    e = met["extra"]
    metodo = {c.metodo for c in cruces if c.metodo}
    if metodo:
        print(f"Método: {', '.join(sorted(metodo))}")

    print(f"{'-' * 72}\nBPM (n={e['n_bpm']}):")
    print(f"  p95 TOLERANTE a octava : {e['bpm_p95_tolerante']:.2f}  "
          f"(medio {e['bpm_medio_tolerante']:.2f})   <- el del umbral §4")
    print(f"  p95 CRUDO              : {e['bpm_p95_crudo']:.2f}  "
          f"(medio {e['bpm_medio_crudo']:.2f})   <- el que delata half-time")
    print(f"\nTiempo (n={e['n_tiempo']}): medio {e['tiempo_medio_s']:.2f} s · "
          f"p95 {e['tiempo_p95_s']:.2f} s · sobre {UMBRAL_TIEMPO:g} s: "
          f"{e['sobre_umbral_tiempo']}/{e['n_tiempo']}")

    print(f"{'-' * 72}\nContra los umbrales de la spec §4:")
    for f in evaluar_umbrales(met["umbrales"]):
        if f.valor is None:
            estado, val = "· sin medir (necesita la radio)", "—"
        else:
            estado = "OK ✓" if f.ok else "ROTO ✗"
            val = f"{f.valor:.2f}{f.umbral.unidad}"
        print(f"  {f.umbral.nombre:34} {val:>12}  (límite {f.umbral.op} "
              f"{f.umbral.limite:g}{f.umbral.unidad})  {estado}")

    con_ref = [c for c in cruces if c.veredicto_bpm != "sin-referencia"]
    if con_ref:
        print(f"{'-' * 72}\nDesacuerdos de BPM por clase ({len(con_ref)} con referencia):")
        for clase, n in resumen([c.clase_bpm for c in con_ref]).items():
            print(f"  {clase:26} {n:4}")

    rotos = [c for c in cruces if c.veredicto_bpm == "ROTO"]
    if rotos:
        print(f"{'-' * 72}\nTracks FUERA del umbral de BPM ({len(rotos)}):")
        for c in sorted(rotos, key=lambda c: -(c.err_octava or 0)):
            print(f"  {c.archivo[:40]:40} est {c.bpm_est:6.1f} ref {c.bpm_ref:6.1f}  "
                  f"crudo {c.err_crudo:6.2f}  octava {c.err_octava:5.2f}  {c.clase_bpm}")

    tolerados = [c for c in cruces
                 if c.veredicto_bpm == "OK" and c.clase_bpm in (OCTAVA, TRESILLO)]
    if tolerados:
        print(f"{'-' * 72}\nDesacuerdos que el umbral TOLERA ({len(tolerados)}) — "
              f"pasan §4 pero no están en el mismo nivel métrico:")
        for c in sorted(tolerados, key=lambda c: -(c.err_crudo or 0)):
            print(f"  {c.archivo[:40]:40} est {c.bpm_est:6.1f} ref {c.bpm_ref:6.1f}  "
                  f"crudo {c.err_crudo:6.2f}  {c.clase_bpm} (x{c.ratio_bpm})")

    fallan_key = [c for c in cruces if c.key_exacta == "no"]
    if fallan_key:
        print(f"{'-' * 72}\nTonalidad que NO coincide con el GT ({len(fallan_key)}):")
        for c in sorted(fallan_key, key=lambda c: c.confianza):
            det = f" tramos [{c.tramos}]" if c.tramos else ""
            print(f"  {c.archivo[:38]:38} est {c.key_est:>3} ref {c.key_ref:>3}  "
                  f"conf {c.confianza:.2f} {c.acuerdo:>4}  "
                  f"compat={'si' if c.key_compatible == 'si' else 'NO'}{det}")

    cal = calibracion(cruces)
    if cal["n"]:
        print(f"{'-' * 72}\nCalibración: ¿la confianza predice el acierto? (n={cal['n']})")
        print(f"  acierto global: {cal['acierto_global']:.1f}%")
        for etiqueta, n, acc in cal["tramos"]:
            print(f"  confianza {etiqueta:30} n={n:3}  acierto {acc:5.1f}%")
        if cal["pearson"] is None:
            print("  Pearson confianza↔acierto: sin varianza suficiente para calcularlo")
        else:
            print(f"  Pearson confianza↔acierto: {cal['pearson']:+.3f}")
    print(f"{'=' * 72}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="benchmark.evaluar",
        description="Etapa B: compara el CSV de la etapa A contra el ground truth. No toca audio.")
    ap.add_argument("--analisis", required=True, type=Path, help="CSV de benchmark.analizar.")
    ap.add_argument("--ground-truth", required=True, type=Path,
                    help="CSV de ground_truth.rekordbox (rekordbox_tracks.csv).")
    ap.add_argument("--out", type=Path, default=Path("benchmark/out"))
    args = ap.parse_args(argv)

    res = cruzar(leer_csv(args.analisis), leer_csv(args.ground_truth))
    informe(res)
    if res["cruces"]:
        destino = escribir_csv(res["cruces"], args.out)
        print(f"Detalle por track → {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
