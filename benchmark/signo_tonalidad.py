"""¿El `?` de la key certifica la key que se MUESTRA? (tarea 17.1)

La key que se muestra la elige `motor.tonalidad.tono()` (ventana central de 90 s). El `?`
sale del ACUERDO entre los tramos de `tono_consenso()` (`motor.cli.key_dudosa`): hay `?`
si el acuerdo no es unánime o si no hubo tramos. El A/B del 2026-09-14 midió que ese
acuerdo predice el acierto de la key DEL CONSENSO. Lo que no se midió: los 3 tramos pueden
coincidir en una key DISTINTA de la de `tono()`, y en ese caso la key mostrada sale limpia,
sin `?`, certificada por tramos que votaron otra cosa.

Este medidor separa los tracks en tres grupos:

- `unanime_coincide`: tramos unánimes y su key es la mostrada.
- `unanime_distinta`: tramos unánimes en una key distinta de la mostrada (el caso dudoso).
- `no_unanime`: acuerdo no unánime, sin tramos (track corto, "0/0") o sin acuerdo medido.

Con ground truth compara además el `?` ACTUAL (`key_dudosa`) con el PROPUESTO (`?` si no
es unánime O si los tramos no coinciden con la key mostrada), con la misma métrica de
acierto exacto y compatible que `benchmark.evaluar` (el cruce es `evaluar.cruzar`). La
decisión sobre el `?` es del dueño: esto solo mide, no cambia nada.

Fuentes de datos (una sola por corrida, sin reanalizar si ya hay datos):

    # CSV de la etapa A (`benchmark.analizar` SIN --consenso: key_est = tono())
    python -m benchmark.signo_tonalidad --analisis benchmark/out/analisis_....csv \\
        --ground-truth ground_truth/out/rekordbox_tracks.csv --out benchmark/out

    # la biblioteca del motor (key, key_acuerdo, key_tramos ya persistidos por el scan);
    # la ruta sale de --db, de $DJRADIO_DB o del default de `motor.cli`
    python -m benchmark.signo_tonalidad --db

    # audio crudo (corre tono() y tono_consenso(); lento)
    python -m benchmark.signo_tonalidad --audio D:\\musica --limit 200

En casa, contra el ground truth real (el CSV de Rekordbox de `ground_truth.rekordbox` y la
etapa A sobre el mismo crate):

    python -m benchmark.analizar --audio <carpeta del crate>
    python -m benchmark.signo_tonalidad --analisis <CSV que imprime el paso anterior> \\
        --ground-truth ground_truth/out/rekordbox_tracks.csv --out benchmark/out

Salida: resumen por pantalla y un CSV por track con separador `;`. NO toca audio ni base:
la base se abre en solo lectura.
"""
import argparse
import contextlib
import csv
import datetime
import sqlite3
import statistics
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from benchmark.evaluar import acuerdo_unanime, cruzar, leer_csv

COINCIDE = "unanime_coincide"
DISTINTA = "unanime_distinta"
NO_UNANIME = "no_unanime"
GRUPOS = (COINCIDE, DISTINTA, NO_UNANIME)

AVISO_SIN_GT = "sin ground truth: el punto 2 se mide en casa"


@dataclass
class Medicion:
    """Lo que hace falta de un track para ubicarlo en un grupo."""

    archivo: str
    ruta: str
    key_mostrada: str     # la de tono()
    acuerdo: str          # "g/t" de tono_consenso; "" = sin acuerdo medido
    tramos: str           # "8A|8A|8A"; "" si no hubo tramos


@dataclass
class FilaSalida:
    archivo: str
    ruta: str
    key_mostrada: str
    tramos: str
    acuerdo: str
    grupo: str
    duda_actual: str      # si | no
    duda_propuesta: str   # si | no
    key_ref: str          # "" sin ground truth
    exacta: str           # si | no | sin-referencia
    compatible: str


def grupo_de(m: Medicion) -> str:
    """Ubica un track. Un acuerdo con formato roto levanta ValueError (vía
    `acuerdo_unanime`): esconderlo en un grupo sería falsear el conteo."""
    unanime = acuerdo_unanime(m.acuerdo)
    if not unanime:
        return NO_UNANIME
    votos = {v for v in m.tramos.split("|") if v}
    if not votos:
        raise ValueError(f"{m.archivo}: acuerdo {m.acuerdo!r} unánime sin tramos")
    return COINCIDE if votos == {m.key_mostrada} else DISTINTA


def duda_actual(m: Medicion) -> bool:
    """El `?` de hoy, por la MISMA función que usa la UI (no una copia)."""
    from motor.cli import key_dudosa
    return key_dudosa(m.acuerdo or None)


def duda_propuesta(m: Medicion) -> bool:
    """`?` si no es unánime O si los tramos unánimes no coinciden con la key mostrada."""
    return duda_actual(m) or grupo_de(m) == DISTINTA


# --- fuentes ---------------------------------------------------------------------------


def desde_senal(archivo: str, ruta: str, y, sr: int) -> Medicion:
    """Mide una señal con las funciones del motor: la key de `tono()` y los tramos de
    `tono_consenso()`, igual que `medir_bpm_y_tono(consenso=False, con_acuerdo=True)` pero
    sin el BPM, que acá no hace falta."""
    from motor.tonalidad import tono, tono_consenso
    det = tono(y, sr)
    cons = tono_consenso(y, sr)
    ganados, total = cons["acuerdo"]
    return Medicion(archivo=archivo, ruta=ruta, key_mostrada=det["camelot"],
                    acuerdo=f"{ganados}/{total}" if total else "",
                    tramos="|".join(cons["tramos"]))


def desde_audio(rutas: list[str], sr: int = 22050) -> list[Medicion]:
    from motor.analisis import cargar
    out = []
    for i, r in enumerate(rutas, 1):
        y = cargar(r, sr)
        if y is None:
            continue
        out.append(desde_senal(Path(r).name, r, y, sr))
        print(f"  [{i}/{len(rutas)}] {Path(r).name[:50]}", flush=True)
    return out


def desde_csv_analisis(filas: list[dict]) -> list[Medicion]:
    """Filas de la etapa A. Solo sirven las de `metodo=tono`: con --consenso `key_est` es
    la key del voto, no la que se muestra, y el grupo `unanime_distinta` no podría existir."""
    malas = {f.get("metodo", "") for f in filas} - {"tono"}
    if malas:
        raise ValueError(f"el CSV trae metodo {sorted(malas)}: hace falta la etapa A SIN "
                         "--consenso, donde key_est es la de tono()")
    return [Medicion(archivo=f.get("archivo", ""), ruta=f.get("ruta", ""),
                     key_mostrada=(f.get("key_est") or "?").strip(),
                     acuerdo=(f.get("acuerdo") or "").strip(),
                     tramos=(f.get("tramos") or "").strip()) for f in filas]


def desde_db(db: Path) -> list[Medicion]:
    """Tracks de la biblioteca, en SOLO LECTURA (`mode=ro`): el medidor no escribe la base.

    "0/0" (consenso corrido sobre un track corto) se lleva a "" como en el CSV de la etapa
    A; NULL (no medido) también. Los dos caen en `no_unanime` y los dos tienen `?` hoy."""
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        filas = con.execute("SELECT path, key, key_acuerdo, key_tramos FROM tracks "
                            "ORDER BY path").fetchall()
    finally:
        con.close()
    out = []
    for path, key, acuerdo, tramos in filas:
        acuerdo = (acuerdo or "").strip()
        if acuerdo.endswith("/0"):
            acuerdo = ""
        out.append(Medicion(archivo=Path(path).name, ruta=path, key_mostrada=key,
                            acuerdo=acuerdo, tramos=tramos or ""))
    return out


# --- resumen ---------------------------------------------------------------------------


def _pct(xs: list[bool]) -> float | None:
    return 100.0 * statistics.mean(1.0 if x else 0.0 for x in xs) if xs else None


def filas_salida(meds: list[Medicion], gt: list[dict] | None) -> list[FilaSalida]:
    """Una fila por track. Con GT, `exacta`/`compatible` salen de `evaluar.cruzar`."""
    refs = {}
    if gt is not None:
        analisis = [{"archivo": m.archivo, "key_est": m.key_mostrada, "acuerdo": m.acuerdo,
                     "tramos": m.tramos, "bpm_est": "0"} for m in meds]
        refs = {c.archivo.lower(): c for c in cruzar(analisis, gt)["cruces"]}
    out = []
    for m in meds:
        c = refs.get(m.archivo.lower())
        out.append(FilaSalida(
            archivo=m.archivo, ruta=m.ruta, key_mostrada=m.key_mostrada, tramos=m.tramos,
            acuerdo=m.acuerdo, grupo=grupo_de(m),
            duda_actual="si" if duda_actual(m) else "no",
            duda_propuesta="si" if duda_propuesta(m) else "no",
            key_ref="" if c is None or c.key_ref == "?" else c.key_ref,
            exacta=c.key_exacta if c else "sin-referencia",
            compatible=c.key_compatible if c else "sin-referencia"))
    return out


def resumen(filas: list[FilaSalida]) -> dict:
    """Conteo por grupo y, si hay referencias, acierto por grupo y por `?` actual/propuesto.

    `con_gt` es False si ningún track tiene referencia: ahí no hay acierto que dar."""
    conteo = {g: sum(1 for f in filas if f.grupo == g) for g in GRUPOS}
    con_ref = [f for f in filas if f.exacta != "sin-referencia"]

    def _acierto(sub: list[FilaSalida]) -> dict:
        return {"n": len(sub), "exacta": _pct([f.exacta == "si" for f in sub]),
                "compatible": _pct([f.compatible == "si" for f in sub])}

    r = {"n": len(filas), "conteo": conteo, "con_gt": bool(con_ref), "n_ref": len(con_ref)}
    if con_ref:
        r["por_grupo"] = {g: _acierto([f for f in con_ref if f.grupo == g]) for g in GRUPOS}
        for nombre, campo in (("actual", "duda_actual"), ("propuesta", "duda_propuesta")):
            r[nombre] = {"sin_duda": _acierto([f for f in con_ref if getattr(f, campo) == "no"]),
                         "con_duda": _acierto([f for f in con_ref if getattr(f, campo) == "si"])}
    return r


def _p(v: float | None) -> str:
    return "  —  " if v is None else f"{v:5.1f}%"


def informe(r: dict) -> str:
    lineas = [f"Tracks: {r['n']}"]
    for g in GRUPOS:
        lineas.append(f"  {g:18} {r['conteo'][g]:5}")
    if not r["con_gt"]:
        lineas.append(AVISO_SIN_GT)
        return "\n".join(lineas)
    lineas.append(f"\nAcierto de la key mostrada (tono) — {r['n_ref']} con referencia:")
    for g in GRUPOS:
        a = r["por_grupo"][g]
        lineas.append(f"  {g:18} n={a['n']:4}  exacta {_p(a['exacta'])} · "
                      f"compatible {_p(a['compatible'])}")
    for nombre in ("actual", "propuesta"):
        lineas.append(f"  '?' {nombre}:")
        for lado, etiqueta in (("sin_duda", "sin ?"), ("con_duda", "con ?")):
            a = r[nombre][lado]
            lineas.append(f"    {etiqueta:6} n={a['n']:4}  exacta {_p(a['exacta'])} · "
                          f"compatible {_p(a['compatible'])}")
    return "\n".join(lineas)


def escribir_csv(filas: list[FilaSalida], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = out_dir / f"signo_tonalidad_{sello}.csv"
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[f.name for f in fields(FilaSalida)], delimiter=";")
        w.writeheader()
        for f in filas:
            w.writerow(asdict(f))
    return destino


def main(argv=None) -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="benchmark.signo_tonalidad",
                                 description="¿El '?' de la key certifica la key mostrada?")
    fuente = ap.add_mutually_exclusive_group(required=True)
    fuente.add_argument("--analisis", type=Path, help="CSV de la etapa A (sin --consenso).")
    fuente.add_argument("--db", nargs="?", const="", type=str,
                        help="Base de la biblioteca; sin valor: $DJRADIO_DB o el default.")
    fuente.add_argument("--audio", type=Path, help="Carpeta de audio (corre el motor).")
    ap.add_argument("--ground-truth", type=Path, help="CSV de ground_truth.rekordbox.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Solo N tracks, muestra aleatoria con la semilla de la etapa A.")
    ap.add_argument("--out", type=Path, default=Path("benchmark/out"))
    args = ap.parse_args(argv)

    from benchmark.analizar import muestrear, rutas_de
    if args.audio is not None:
        meds = desde_audio(muestrear(rutas_de(args.audio), args.limit))
    else:
        if args.analisis is not None:
            meds = desde_csv_analisis(leer_csv(args.analisis))
        else:
            from motor.cli import db_por_defecto
            meds = desde_db(Path(args.db) if args.db else db_por_defecto())
        # Muestreo sobre las rutas con la misma función (y semilla) que la etapa A.
        elegidas = set(muestrear([m.ruta for m in meds], args.limit))
        meds = [m for m in meds if m.ruta in elegidas]
    if not meds:
        print("No hay tracks para medir.")
        return 1

    gt = leer_csv(args.ground_truth) if args.ground_truth else None
    filas = filas_salida(meds, gt)
    print(informe(resumen(filas)))
    print(f"→ {escribir_csv(filas, args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
