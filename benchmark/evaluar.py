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


def acuerdo_unanime(acuerdo: str) -> bool | None:
    """¿El `acuerdo` de `tono_consenso` ("3/3", "2/3", "5/5"...) es unánime?

    Unánime = todos los tramos votaron la misma key: ganados == total, con total > 0. No
    se compara contra "3/3" porque `n_tramos` es configurable.

    Devuelve None cuando no hay acuerdo que leer (campo vacío). Desde `aabd83d` la etapa A
    mide el acuerdo SIEMPRE, así que el vacío solo sale de dos lados: un CSV anterior a ese
    commit (que no tenía el acuerdo), o un track demasiado corto para 3 tramos disjuntos
    (< ~135 s: `tono_consenso` devuelve acuerdo (0, 0) y la etapa A lo escribe vacío). Un
    valor que no tiene la forma "g/t" es un CSV roto y levanta ValueError: adivinarlo sería
    falsear el subconjunto sobre el que se mide el contrato. `metricas` lo valida en todos
    los tracks cruzados, tengan o no referencia de tonalidad.
    """
    texto = (acuerdo or "").strip()
    if not texto:
        return None
    try:
        ganados, total = (int(p) for p in texto.split("/"))
    except ValueError as e:
        raise ValueError(f"acuerdo con formato inesperado: {acuerdo!r} (se espera 'g/t')") from e
    return total > 0 and ganados == total


def metricas(cruces: list[Cruce]) -> dict:
    """Las métricas de §4 que dependen del análisis por track, más los dos p95 de BPM.

    Tonalidad (contrato 2026-09-14): `tonalidad_exacta` y `tonalidad_compatible` se miden
    SOLO sobre los tracks con acuerdo unánime de `tono_consenso`. Si ningún track con
    referencia trae acuerdo quedan AUSENTES — sin medir — y no se rellenan con la cifra
    global: eso sería medir otra cosa con el nombre del contrato. Hoy eso pasa solo con un
    CSV anterior a `aabd83d` o si todos los tracks son cortos (ver `acuerdo_unanime`). La
    global va a `extra` como informativa, junto con la COBERTURA del subconjunto (cuántos y
    qué % de los tracks con referencia son unánimes): un contrato sobre un subconjunto se
    "cumple" achicando el subconjunto, y ese número tiene que verse.

    `extra["acierto_por_acuerdo"]`: acierto exacto y compatible de la key ANALIZADA
    (`key_est`, la de `metodo`) agrupada por el acuerdo entre tramos. El A/B del 2026-09-14
    midió el acierto de la key DEL CONSENSO por su acuerdo (55/36/26%); la key que se
    muestra es la de `tono()`, y que el acuerdo prediga ESE acierto es esta tabla.
    """
    eo = [c.err_octava for c in cruces if c.err_octava is not None]
    ec = [c.err_crudo for c in cruces if c.err_crudo is not None]
    con_ref = [c for c in cruces if c.key_exacta != "sin-referencia"]
    ex = [1.0 if c.key_exacta == "si" else 0.0 for c in con_ref]
    co = [1.0 if c.key_compatible == "si" else 0.0 for c in con_ref]
    ts = [c.t_total_s for c in cruces if c.t_total_s > 0]

    # El formato del acuerdo se valida en TODOS los cruces, también en los sin referencia: la
    # etapa A escribe "g/t" o vacío sin saber del GT, así que un valor roto en un track sin
    # referencia es el mismo CSV roto, no un caso legítimo que se pueda saltear.
    for c in cruces:
        acuerdo_unanime(c.acuerdo)
    unanimidad = [acuerdo_unanime(c.acuerdo) for c in con_ref]
    hay_consenso = any(u is not None for u in unanimidad)
    unanimes = [c for c, u in zip(con_ref, unanimidad, strict=True) if u]

    m: dict[str, float] = {}
    if eo:
        m["bpm_error_p95"] = _p95(eo)
    if unanimes:
        m["tonalidad_exacta"] = 100.0 * statistics.mean(
            1.0 if c.key_exacta == "si" else 0.0 for c in unanimes)
        m["tonalidad_compatible"] = 100.0 * statistics.mean(
            1.0 if c.key_compatible == "si" else 0.0 for c in unanimes)
    if ts:
        m["tiempo_analisis_s"] = _p95(ts)

    extra = {
        "tonalidad_exacta_global": 100.0 * statistics.mean(ex) if ex else None,
        "tonalidad_compatible_global": 100.0 * statistics.mean(co) if co else None,
        "hay_consenso": hay_consenso,
        "n_key_unanimes": len(unanimes),
        # None sin consenso: la cobertura de un subconjunto que no se pudo formar no es 0%.
        "cobertura_unanimes": (100.0 * len(unanimes) / len(con_ref)
                               if con_ref and hay_consenso else None),
        "bpm_p95_crudo": _p95(ec) if ec else None,
        "bpm_p95_tolerante": _p95(eo) if eo else None,
        "bpm_medio_crudo": statistics.mean(ec) if ec else None,
        "bpm_medio_tolerante": statistics.mean(eo) if eo else None,
        "tiempo_medio_s": statistics.mean(ts) if ts else None,
        "tiempo_p95_s": _p95(ts) if ts else None,
        "sobre_umbral_tiempo": sum(1 for t in ts if t > UMBRAL_TIEMPO),
        "n_tiempo": len(ts), "n_bpm": len(eo), "n_key": len(ex),
        "acierto_por_acuerdo": acierto_por_acuerdo(cruces),
    }
    return {"umbrales": m, "extra": extra}


SIN_ACUERDO = "sin acuerdo"


def acierto_por_acuerdo(cruces: list[Cruce]) -> list[dict]:
    """Acierto de la key analizada (`key_est`) por grupo de acuerdo, solo tracks con referencia.

    Un grupo por valor de `acuerdo` ("3/3", "2/3", "1/3", "0/3"...) y uno `SIN_ACUERDO` para
    el campo vacío (CSV viejo o track corto). Orden: más tramos primero, y dentro de la misma
    cantidad de tramos, más acuerdo primero; `SIN_ACUERDO` al final. Cada grupo:
    {acuerdo, n, exacta, compatible} con los dos aciertos en %.

    Un acuerdo con formato roto levanta ValueError (vía `acuerdo_unanime`): agruparlo como
    "sin acuerdo" escondería un CSV roto dentro de un número.
    """
    grupos: dict[str, list[Cruce]] = {}
    for c in cruces:
        if c.key_exacta == "sin-referencia":
            continue
        texto = (c.acuerdo or "").strip()
        acuerdo_unanime(texto)     # valida el formato; el resultado no se usa acá
        grupos.setdefault(texto or SIN_ACUERDO, []).append(c)

    def _orden(clave: str) -> tuple:
        if clave == SIN_ACUERDO:
            return (1, 0, 0)
        ganados, total = (int(p) for p in clave.split("/"))
        return (0, -total, -ganados)

    return [{
        "acuerdo": clave, "n": len(g),
        "exacta": 100.0 * statistics.mean(1.0 if c.key_exacta == "si" else 0.0 for c in g),
        "compatible": 100.0 * statistics.mean(1.0 if c.key_compatible == "si" else 0.0 for c in g),
    } for clave, g in sorted(grupos.items(), key=lambda kv: _orden(kv[0]))]


def calibracion(cruces: list[Cruce]) -> dict:
    """¿La confianza predice el acierto de tonalidad? Un número, no una impresión.

    Devuelve el acierto por tramo de confianza y la correlación de Pearson entre la
    confianza y el acierto (0/1). Si la correlación es ~0, la confianza no informa.

    QUÉ ES `confianza` depende de la columna `metodo`, y las etiquetas de los tramos también:
    - `tono_consenso`: la fracción de tramos que votaron la key ganadora (0.33/0.67/1.00 con
      3 tramos) → etiquetas que dicen cuántos tramos coinciden.
    - `tono` (el default) o una mezcla de métodos: la correlación Krumhansl del mejor perfil,
      que NO es un conteo de tramos → etiquetas solo numéricas. No se le da otra lectura: el
      A/B del 2026-09-14 midió que no predice el acierto (Pearson +0.022). La confianza de la
      key con `tono` es el acuerdo, y su acierto está en `acierto_por_acuerdo`.
    `metodo` en el resultado: el método si es uno solo, "" si hay mezcla o no se sabe.
    """
    con_ref = [c for c in cruces if c.key_exacta != "sin-referencia"]
    datos = [(c.confianza, 1.0 if c.key_exacta == "si" else 0.0) for c in con_ref]
    if not datos:
        return {"n": 0, "tramos": [], "pearson": None, "metodo": ""}

    conf = [d[0] for d in datos]
    acierto = [d[1] for d in datos]
    metodos = {c.metodo for c in con_ref}
    metodo = next(iter(metodos)) if len(metodos) == 1 else ""

    if metodo == "tono_consenso":
        bordes = ((0.0, 0.34, "0.33 — los 3 tramos distintos"),
                  (0.34, 0.67, "0.34-0.66"),
                  (0.67, 0.99, "0.67 — 2 de 3 coinciden"),
                  (0.99, 1.01, "1.00 — los 3 coinciden"))
    else:
        # Krumhansl es una correlación: puede ser negativa (silencio, ruido). El primer
        # tramo la incluye para no tirar tracks del conteo sin decirlo.
        bordes = ((-1.01, 0.34, "< 0.34"),
                  (0.34, 0.67, "0.34-0.66"),
                  (0.67, 0.99, "0.67-0.98"),
                  (0.99, 1.01, ">= 0.99"))

    tramos = []
    for lo, hi, etiqueta in bordes:
        g = [a for c, a in datos if lo <= c < hi]
        if g:
            tramos.append((etiqueta, len(g), 100.0 * statistics.mean(g)))

    pearson = None
    if len(datos) >= 3 and len(set(conf)) > 1 and len(set(acierto)) > 1:
        mc, ma = statistics.mean(conf), statistics.mean(acierto)
        num = sum((c - mc) * (a - ma) for c, a in datos)
        den = (sum((c - mc) ** 2 for c in conf) * sum((a - ma) ** 2 for a in acierto)) ** 0.5
        pearson = num / den if den else None

    return {"n": len(datos), "tramos": tramos, "pearson": pearson, "metodo": metodo,
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


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}%"


# Por qué el contrato de tonalidad puede quedar sin medir. NO es "falta --consenso": desde
# `aabd83d` la etapa A mide el acuerdo siempre, y `--consenso` cambiaría la KEY (la
# decisión es que la key salga de tono()). Las dos causas reales están en `acuerdo_unanime`.
CONTRATO_SIN_ACUERDO = (
    "CONTRATO — sin medir: ningún track con referencia trae acuerdo entre tramos. O el CSV "
    "es anterior a aabd83d (reanalizar con la etapa A actual, que mide el acuerdo siempre), "
    "o todos duran menos de ~135 s y no dan para 3 tramos disjuntos")


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

    print(f"{'-' * 72}\nTonalidad (n={e['n_key']} con referencia):")
    if e["hay_consenso"]:
        print(f"  CONTRATO — solo tracks con acuerdo unánime de tono_consenso: "
              f"{_pct(met['umbrales'].get('tonalidad_exacta'))} exacta · "
              f"{_pct(met['umbrales'].get('tonalidad_compatible'))} compatible")
        print(f"  cobertura del subconjunto: {e['n_key_unanimes']}/{e['n_key']} "
              f"({_pct(e['cobertura_unanimes'])})   <- si baja, el contrato mide menos tracks")
    else:
        print(f"  {CONTRATO_SIN_ACUERDO}")
    print(f"  informativo — global, todos los tracks: "
          f"{_pct(e['tonalidad_exacta_global'])} exacta · "
          f"{_pct(e['tonalidad_compatible_global'])} compatible")
    if e["acierto_por_acuerdo"]:
        print(f"  acierto de la key analizada (key_est) por acuerdo entre tramos "
              f"(método: {', '.join(sorted(metodo)) or '?'}):")
        for g in e["acierto_por_acuerdo"]:
            print(f"    {g['acuerdo']:12} n={g['n']:4}  exacta {_pct(g['exacta']):>6} · "
                  f"compatible {_pct(g['compatible']):>6}")

    print(f"{'-' * 72}\nContra los umbrales de la spec §4:")
    for f in evaluar_umbrales(met["umbrales"]):
        if f.valor is None:
            estado, val = "· sin medir (necesita la radio)", "—"
            if f.umbral.clave.startswith("tonalidad_"):
                estado = "· sin medir (sin acuerdo entre tramos: CSV viejo o tracks cortos)"
        elif f.umbral.a_calibrar:
            estado, val = "~ sin veredicto", f"{f.valor:.2f}{f.umbral.unidad}"
        else:
            estado = {True: "OK ✓", False: "ROTO ✗", None: "· no definido (nan)"}[f.ok]
            val = f"{f.valor:.2f}{f.umbral.unidad}"
        extra_cob = ""
        if f.umbral.clave.startswith("tonalidad_") and e["hay_consenso"]:
            extra_cob = f"  [cobertura {e['n_key_unanimes']}/{e['n_key']}]"
        print(f"  {f.umbral.nombre:34} {val:>12}  ({f.umbral.objetivo()})  {estado}{extra_cob}")

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
        if cal["metodo"] == "tono_consenso":
            print("  confianza = fracción de tramos que votaron la key (tono_consenso)")
        else:
            que = ("correlación Krumhansl de tono()" if cal["metodo"] == "tono"
                   else "método mezclado o desconocido: no es una sola escala")
            print(f"  confianza = {que} — NO es acuerdo entre tramos; "
                  f"el acierto por acuerdo está arriba, en Tonalidad")
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
