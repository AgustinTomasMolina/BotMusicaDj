"""Sesión de ground truth en UN comando. Pensado para correrse donde está el disco.

    python -m ground_truth.sesion --xml Rekordbox.xml --roots D:\\ --salida gt_out/

Hace todo en orden y sin que haya que acordarse de nada:

  0. Parsea el XML de Rekordbox al CSV de ground truth.
  1. REPORTA LA RESOLUCIÓN ANTES DE ANALIZAR: cuántos tracks trae el XML, cuántos
     resuelven a un archivo real, cuántos quedan ambiguos y cuántos no aparecen.
     Si la resolución está mal, analizar horas de audio no sirve de nada — por eso el
     número se ve primero y hay que confirmarlo (o pasar --si para no preguntar).
  2. Etapa A sobre una muestra con semilla fija, SIN --consenso.
  3. Etapa A sobre LA MISMA muestra, CON --consenso.
  4. Etapa B sobre cada una.
  5. Deja los cuatro CSV y un resumen en la carpeta de salida, y dice qué copiarse.

REANUDABLE. Cada etapa A guarda su CSV apenas termina cada track, así que si se corta a
la mitad, volver a correr el mismo comando retoma donde quedó y NO reanaliza lo ya hecho.
Son horas de audio: perderlas por un corte de luz sería tonto.

`--limit N` corre una pasada corta de prueba antes de la larga. La muestra sale de la
misma semilla, así que los N de la prueba son un subconjunto reproducible.

NO INVENTA DATOS. Sin XML, o con un XML que no resuelve a ningún audio, el comando falla
y dice qué falta. No hay modo "sintético" ni valores por defecto: el ground truth es el
que puso Rekordbox o no hay ground truth.
"""
import argparse
import csv
import datetime
import json
import sys
from dataclasses import asdict
from pathlib import Path

from benchmark.analizar import COLUMNAS as COLUMNAS_A
from benchmark.analizar import SEMILLA, analizar_uno, muestrear
from benchmark.evaluar import cruzar, informe, leer_csv
from benchmark.evaluar import escribir_csv as escribir_evaluacion
from ground_truth.rekordbox import escribir_csv as escribir_gt
from ground_truth.rekordbox import parsear
from ground_truth.resolver import AMBIGUO, NO_ENCONTRADO, construir_indice, resolver

# La semilla y el muestreo salen de benchmark.analizar a proposito: la muestra de la
# sesion tiene que ser LA MISMA que la del comando suelto, o los numeros no se comparan.
ESTADO = "estado_sesion.json"


class SesionIncompleta(RuntimeError):
    """Falta algo para poder correr. Nunca se rellena con datos inventados."""


# --- Resolución -------------------------------------------------------------------------


def resolver_todo(tracks: list[dict], raices: list[str]) -> dict:
    """Ubica el audio de cada track. Devuelve el recuento ANTES de analizar nada."""
    indice = construir_indice(raices)
    rutas, ambiguos = [], []
    no_encontrados, sin_referencia = [], 0
    for t in tracks:
        if t["bpm"] <= 0 and not t["camelot"]:
            sin_referencia += 1
            continue
        r = resolver(t["location"], raices, indice)
        if r.estado == AMBIGUO:
            ambiguos.append((Path(t["location"]).name, r.candidatos))
        elif r.estado == NO_ENCONTRADO:
            no_encontrados.append(Path(t["location"]).name)
        else:
            rutas.append(r.ruta)
    return {"rutas": rutas, "ambiguos": ambiguos, "no_encontrados": no_encontrados,
            "sin_referencia": sin_referencia, "total_xml": len(tracks)}


def informe_resolucion(res: dict) -> None:
    tot = res["total_xml"]
    ok = len(res["rutas"])
    print(f"\n{'=' * 68}\nRESOLUCIÓN — antes de analizar nada\n{'=' * 68}")
    print(f"  tracks en el XML          {tot:5}")
    print(f"  sin BPM ni tonalidad      {res['sin_referencia']:5}  (no hay contra qué comparar)")
    print(f"  con audio encontrado      {ok:5}"
          + (f"  ({100 * ok / tot:.0f}%)" if tot else ""))
    print(f"  ambiguos (nombre repetido){len(res['ambiguos']):5}  (se excluyen)")
    print(f"  sin archivo               {len(res['no_encontrados']):5}")
    for nombre, cands in res["ambiguos"][:5]:
        print(f"      ambiguo: {nombre[:44]:44} {len(cands)} candidatos")
    for nombre in res["no_encontrados"][:5]:
        print(f"      sin archivo: {nombre[:52]}")
    if len(res["no_encontrados"]) > 5:
        print(f"      … y {len(res['no_encontrados']) - 5} más")


# --- Reanudación ------------------------------------------------------------------------


def leer_hechos(csv_parcial: Path) -> dict[str, dict]:
    """Lo ya analizado en una corrida anterior, por ruta. Vacío si no hay nada."""
    if not csv_parcial.exists():
        return {}
    try:
        return {f["ruta"]: f for f in leer_csv(csv_parcial)}
    except Exception:  # noqa: BLE001
        return {}


def _guardar_parcial(filas: list[dict], destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS_A)
        w.writeheader()
        w.writerows(filas)


def etapa_a(rutas: list[str], consenso: bool, parcial: Path, etiqueta: str) -> list[dict]:
    """Analiza, guardando después de CADA track para poder retomar."""
    hechos = leer_hechos(parcial)
    if hechos:
        print(f"  [{etiqueta}] retomando: {len(hechos)} ya analizados, "
              f"faltan {len(rutas) - len(hechos)}")

    filas = [hechos[r] for r in rutas if r in hechos]
    pendientes = [r for r in rutas if r not in hechos]
    if not pendientes:
        print(f"  [{etiqueta}] ya estaba completo")
        return filas

    from benchmark.tiempo_analisis import calentar
    calentar()   # el JIT de numba, fuera del cronómetro de cada track

    for i, ruta in enumerate(pendientes, 1):
        fila = analizar_uno(ruta, consenso=consenso)
        if fila is not None:
            filas.append(asdict(fila))
        _guardar_parcial(filas, parcial)     # tras cada track: un corte no pierde nada
        print(f"  [{etiqueta}] {i}/{len(pendientes)} {Path(ruta).name[:46]}", flush=True)
    return filas


# --- Sesión completa --------------------------------------------------------------------


def correr(xml: Path, raices: list[str], salida: Path, limite: int | None = None,
           semilla: int = SEMILLA, confirmar: bool = True) -> dict:
    if not xml.exists():
        raise SesionIncompleta(
            f"No existe el XML: {xml}\n"
            "  Exportalo desde Rekordbox: File → Export Collection in xml format.\n"
            "  Sin XML no hay ground truth, y no se inventa uno.")

    salida.mkdir(parents=True, exist_ok=True)
    tracks = parsear(xml)
    if not tracks:
        raise SesionIncompleta(f"El XML no trae ningún <TRACK>: {xml}")
    gt_csv, _ = escribir_gt(tracks, salida)
    print(f"Ground truth: {len(tracks)} tracks → {gt_csv}")

    res = resolver_todo(tracks, raices)
    informe_resolucion(res)
    if not res["rutas"]:
        raise SesionIncompleta(
            f"Ninguno de los {len(tracks)} tracks del XML resolvió a un archivo real.\n"
            f"  Raíces buscadas: {', '.join(raices)}\n"
            "  ¿Está enchufado el disco? ¿La letra de unidad es la correcta?\n"
            "  No se analiza nada hasta que la resolución dé algo.")

    # Muestra reproducible: misma semilla + mismas rutas = misma lista. Por eso reanudar
    # una corrida con --limit retoma exactamente los mismos tracks.
    todas = sorted(res["rutas"])
    rutas = muestrear(todas, limite, semilla)
    if len(rutas) < len(todas):
        print(f"\n  MUESTRA de {len(rutas)} sobre {len(todas)} "
              f"(semilla {semilla}) " "— pasada corta de prueba")

    if confirmar:
        print(f"\nSe van a analizar {len(rutas)} tracks, DOS veces "
              f"(sin consenso y con consenso).")
        try:
            if input("¿Sigo? [s/N] ").strip().lower() not in ("s", "si", "sí", "y"):
                print("Cortado antes de analizar. No se tocó nada.")
                return {}
        except EOFError:
            print("(sin terminal interactiva: seguí con --si para saltar esta pregunta)")
            return {}

    (salida / ESTADO).write_text(json.dumps({
        "xml": str(xml), "raices": raices, "semilla": semilla, "limite": limite,
        "n_rutas": len(rutas), "iniciada": datetime.datetime.now().isoformat()},
        indent=1), encoding="utf-8")

    resultados = {}
    for etiqueta, consenso in (("sin-consenso", False), ("con-consenso", True)):
        print(f"\n{'=' * 68}\nETAPA A — {etiqueta}\n{'=' * 68}")
        parcial = salida / f"analisis_{etiqueta}.csv"
        filas = etapa_a(rutas, consenso, parcial, etiqueta)

        print(f"\n{'=' * 68}\nETAPA B — {etiqueta}\n{'=' * 68}")
        cruce = cruzar(filas, tracks)
        informe(cruce)
        eval_csv = escribir_evaluacion(cruce["cruces"], salida, sufijo=f"_{etiqueta}")
        resultados[etiqueta] = {"analisis": str(parcial), "evaluacion": str(eval_csv),
                                "n": len(filas), "cruzados": len(cruce["cruces"])}

    _resumen(salida, res, resultados, semilla, limite)
    return resultados


def _resumen(salida: Path, res: dict, resultados: dict, semilla: int,
             limite: int | None) -> None:
    doc = {
        "generado": datetime.datetime.now().isoformat(),
        "resolucion": {"total_xml": res["total_xml"], "con_audio": len(res["rutas"]),
                       "ambiguos": len(res["ambiguos"]),
                       "sin_archivo": len(res["no_encontrados"]),
                       "sin_referencia": res["sin_referencia"]},
        "semilla": semilla, "limite": limite,
        "pasadas": resultados,
    }
    (salida / "resumen.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                                         encoding="utf-8")

    print(f"\n{'=' * 68}\nQUÉ COPIARTE DE VUELTA\n{'=' * 68}")
    print("La etapa B corre en cualquier máquina: con estos CSV alcanza, no hace falta")
    print("el audio ni el disco.\n")
    total = 0
    for p in sorted(salida.glob("*.csv")) + sorted(salida.glob("*.json")):
        tam = p.stat().st_size
        total += tam
        print(f"  {tam / 1024:8.1f} KB  {p.name}")
    print(f"  {'-' * 8}")
    print(f"  {total / 1024:8.1f} KB  en total"
          + (f"  ({total / 1024 / 1024:.1f} MB)" if total > 1024 * 1024 else ""))
    print(f"\nCarpeta: {salida.resolve()}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="ground_truth.sesion",
        description="Sesión completa de ground truth: parsear, resolver, analizar, evaluar.")
    ap.add_argument("--xml", required=True, type=Path,
                    help="XML exportado de Rekordbox (File → Export Collection in xml format).")
    ap.add_argument("--roots", nargs="+", required=True,
                    help="Dónde buscar los audios. Ej: D:\\")
    ap.add_argument("--salida", type=Path, default=Path("gt_out"))
    ap.add_argument("--limit", type=int, default=None,
                    help="Pasada corta de prueba con N tracks, de la misma muestra.")
    ap.add_argument("--seed", type=int, default=SEMILLA)
    ap.add_argument("--si", action="store_true",
                    help="No preguntar antes de analizar (para correrlo desatendido).")
    args = ap.parse_args(argv)

    try:
        correr(args.xml, args.roots, args.salida, args.limit, args.seed,
               confirmar=not args.si)
    except SesionIncompleta as e:
        print(f"\n{e}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
