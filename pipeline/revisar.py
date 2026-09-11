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
from pipeline.reporte import Fila, estado_por_defecto, generar


def procesar(rutas: list[str], staging: Path, progreso: bool = True) -> list[Fila]:
    """Corre todo el análisis y escribe las copias. Devuelve las filas del reporte."""
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
        motor = analizar_motor(ruta, consenso=True)
        tags_actuales = leer_tags(ruta)
        plan = planificar(props[ruta], cal, tags_actuales)
        copia = escribir_en_copia(ruta, staging, plan)

        gid = grupo_de.get(ruta, 0)
        # Lo que va a quedar en el tag: lo escrito si se escribe, lo que ya estaba si no.
        valor = {c.campo: (c.despues or c.antes) for c in plan}
        filas.append(Fila(
            archivo=Path(ruta).name, ruta_staging=str(copia), ruta_original=ruta,
            artista=valor.get("artista", ""), titulo=valor.get("titulo", ""),
            duracion_s=motor.duracion_s if motor else 0.0,
            corte_khz=cal.corte_medido_khz, bandera=cal.bandera, muro_db=cal.muro_db,
            bpm=motor.bpm_est if motor else 0.0,
            camelot=motor.key_est if motor else "?",
            clasica=camelot_a_clasica(motor.key_est) if motor else "",
            confianza=motor.confianza if motor else 0.0,
            acuerdo=motor.acuerdo if motor else "",
            grupo_id=gid, accion_duplicado=accion_dup.get(ruta, ""),
            cambios=[(c.campo, c.antes, c.despues, c.motivo)
                     for c in plan if c.accion == "escribir"],
            estado=estado_por_defecto(cal.bandera, gid),
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
                    help="Dónde escribir el HTML (default: dentro del staging).")
    args = ap.parse_args(argv)

    rutas = sorted(str(p) for p in args.carpeta.rglob("*") if p.suffix.lower() in EXTS)
    if not rutas:
        print(f"No encontré audio en {args.carpeta}")
        return 1

    staging = args.staging or config.staging_dir()
    staging.mkdir(parents=True, exist_ok=True)
    print(f"Procesando {len(rutas)} archivos → staging: {staging}")

    filas = procesar(rutas, staging)
    destino = args.reporte or (staging / "revision.html")
    generar(filas, staging, destino)

    n = {}
    for f in filas:
        n[f.estado] = n.get(f.estado, 0) + 1
    grupos = len({f.grupo_id for f in filas if f.grupo_id})
    print(f"\n{len(filas)} tracks · aprobados por defecto {n.get('aprobado', 0)} · "
          f"pendientes {n.get('pendiente', 0)}")
    print(f"  sospechosos: {sum(1 for f in filas if f.bandera == 'sospechoso')} · "
          f"grupos de duplicados: {grupos}")
    print(f"\nAbrí el reporte con doble clic:\n  {destino}")
    print("\nRevisá, marcá qué aprobar, bajá decisiones.json y después:")
    print("  python -m pipeline.aplicar --decisiones decisiones.json")
    print("\nNo se escribió nada en iTunes ni se tocó ningún original.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
