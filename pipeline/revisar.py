"""Paso 1 del pipeline: procesar una carpeta y dejar todo listo para revisar a mano.

    python -m pipeline.revisar --carpeta <descargas>

Corre lo que ya está hecho —calidad (5.1), duplicados (5.2), tags (5.3)— deja las copias
procesadas en el STAGING y escribe un reporte HTML para abrir con doble clic.

NO escribe en la carpeta de iTunes. Eso lo hace `pipeline.aplicar`, y solo con las
decisiones que hayas tomado en el reporte. Nada llega a iTunes sin pasar por tus ojos.

NO toca los originales: las copias se escriben en el staging (verificado por sha256 en la
tarea 5.3 y sostenido acá).
"""
import argparse
import datetime
import sys
from pathlib import Path

from benchmark.analizar import analizar_uno as analizar_motor
from calidad.corte_espectral import EXTS
from calidad.corte_espectral import analizar_uno as medir_calidad
from calidad.duplicados import agrupar, cargar_ficha, decidir
from calidad.escribir_tags import escribir_en_copia, planificar
from calidad.normalizar_tags import analizar as parsear_nombres
from calidad.tags import leer_tags
from motor.tonalidad import camelot_a_clasica
from pipeline import config
from pipeline.reporte import Fila, estado_por_defecto, generar, motivos_pendiente


def procesar(rutas: list[str], staging: Path, progreso: bool = True,
             consenso: bool = False) -> list[Fila]:
    """Corre todo el análisis y escribe las copias. Devuelve las filas del reporte.

    `consenso` tiene que arrancar en False, igual que `benchmark.analizar` y
    `benchmark.motor_real`: si el pipeline usara `tono_consenso()` por su cuenta habría DOS
    tonalidades distintas para el mismo track — una en el tag y en el XML de Rekordbox, y
    otra la que mide el benchmark y consume el scoring. El consenso sigue detrás del mismo
    flag en los tres lados hasta que haya el antes/después contra ground truth que pide §5
    para cambiar el default del motor.

    `pipeline/tests/test_pipeline.py::test_el_pipeline_y_el_benchmark_usan_la_misma_tonalidad`
    falla si las dos rutas se separan.
    """
    props = {p.ruta: p for p in parsear_nombres(rutas)}

    if progreso:
        print("  huellas para detectar duplicados…", flush=True)
    fichas = [cargar_ficha(r) for r in rutas]
    grupos, _ = agrupar(fichas)

    # Qué grupo le toca a cada archivo, y qué se propone hacer con él dentro del grupo.
    grupo_de: dict[str, int] = {}
    accion_dup: dict[str, str] = {}
    calidades: dict[int, object] = {}
    for gid, grupo in enumerate(grupos, 1):
        for i in grupo:
            calidades[i] = medir_calidad(fichas[i].ruta)
        for fila in decidir(grupo, fichas, calidades):
            grupo_de[fila.ruta] = gid
            accion_dup[fila.ruta] = fila.accion

    filas: list[Fila] = []
    for i, ruta in enumerate(rutas, 1):
        cal = medir_calidad(ruta)
        motor = analizar_motor(ruta, consenso=consenso)
        tags_actuales = leer_tags(ruta)
        plan = planificar(props[ruta], cal, tags_actuales)
        copia = escribir_en_copia(ruta, staging, plan)

        gid = grupo_de.get(ruta, 0)
        # Lo que va a quedar en el tag: lo escrito si se escribe, lo que ya estaba si no.
        valor = {c.campo: (c.despues or c.antes) for c in plan}
        artista, titulo = valor.get("artista", ""), valor.get("titulo", "")
        duracion = motor.duracion_s if motor else 0.0
        motivos = motivos_pendiente(cal.bandera, gid, artista, titulo, duracion)
        filas.append(Fila(
            archivo=Path(ruta).name, ruta_staging=str(copia), ruta_original=ruta,
            artista=artista, titulo=titulo, duracion_s=duracion,
            corte_khz=cal.corte_medido_khz, bandera=cal.bandera, muro_db=cal.muro_db,
            bpm=motor.bpm_est if motor else 0.0,
            camelot=motor.key_est if motor else "?",
            clasica=camelot_a_clasica(motor.key_est) if motor else "",
            confianza=motor.confianza if motor else 0.0,
            acuerdo=motor.acuerdo if motor else "",
            grupo_id=gid, accion_duplicado=accion_dup.get(ruta, ""),
            cambios=[(c.campo, c.antes, c.despues, c.motivo)
                     for c in plan if c.accion == "escribir"],
            metodo=motor.metodo if motor else "tono",
            motivos=motivos,
            estado=estado_por_defecto(cal.bandera, gid, artista, titulo, duracion),
        ))
        if progreso and i % 10 == 0:
            print(f"  {i}/{len(rutas)}…", flush=True)
    return filas


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="pipeline.revisar",
        description="Procesa una carpeta y genera el reporte HTML de revisión.")
    ap.add_argument("--carpeta", required=True, type=Path)
    ap.add_argument("--staging", type=Path, default=None,
                    help="Dónde dejar las copias procesadas (default: MUSIFLIX_STAGING).")
    ap.add_argument("--reporte", type=Path, default=None,
                    help="Dónde escribir el HTML (default: junto al staging, con fecha).")
    ap.add_argument("--consenso", action="store_true",
                    help="Usar tono_consenso() para la tonalidad. Es el MISMO flag que "
                         "benchmark.analizar: si se prende acá hay que prenderlo allá, o "
                         "el tag y el benchmark quedan con tonalidades distintas.")
    args = ap.parse_args(argv)

    rutas = sorted(str(p) for p in args.carpeta.rglob("*") if p.suffix.lower() in EXTS)
    if not rutas:
        print(f"No encontré audio en {args.carpeta}")
        return 1

    staging = args.staging or config.staging_dir()
    staging.mkdir(parents=True, exist_ok=True)
    print(f"Procesando {len(rutas)} archivos → staging: {staging}")

    filas = procesar(rutas, staging, consenso=args.consenso)
    # El reporte va FUERA del staging y con fecha: el staging se pisa en cada corrida, y
    # comparar dos tandas es justo lo que hace falta cuando algo cambió.
    sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = args.reporte or (staging.parent / f"revision_{sello}.html")
    generar(filas, staging, destino)

    n: dict[str, int] = {}
    por_motivo: dict[str, int] = {}
    for f in filas:
        n[f.estado] = n.get(f.estado, 0) + 1
        for m in f.motivos:
            por_motivo[m] = por_motivo.get(m, 0) + 1
    print(f"\n{len(filas)} tracks · aprobados por defecto {n.get('aprobado', 0)} · "
          f"pendientes {n.get('pendiente', 0)}")
    print("  pendientes por motivo (un archivo puede tener varios):")
    for m, c in sorted(por_motivo.items(), key=lambda x: -x[1]):
        print(f"    {m:16} {c:3}")
    print(f"\nAbrí el reporte con doble clic:\n  {destino}")
    print("\nRevisá, marcá qué aprobar, bajá decisiones.json y después:")
    print("  python -m pipeline.aplicar --decisiones decisiones.json")
    print("\nNo se escribió nada en iTunes ni se tocó ningún original.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
