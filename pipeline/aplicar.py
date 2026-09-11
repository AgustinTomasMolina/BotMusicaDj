"""Paso 2 del pipeline: mover a iTunes y a Rekordbox SOLO lo aprobado.

    python -m pipeline.aplicar --decisiones decisiones.json

Lee el JSON que baja el reporte HTML y copia únicamente las filas en estado 'aprobado'.
Lo pendiente y lo descartado no se mueven: si no lo miraste, no sale.

DOS SALIDAS, Y NO SON INTERCAMBIABLES
-------------------------------------
1. La carpeta "Añadir automáticamente a iTunes": copia los archivos.
2. Un XML importable por Rekordbox con los mismos tracks.

Los CUE POINTS viajan SOLO por el XML. iTunes no los transporta — no hay campo donde
meterlos y el archivo copiado no los lleva. Si alguna vez parece que "se perdieron los
cues", es por esto: hay que importar el XML en Rekordbox, no alcanza con la carpeta.
Queda escrito acá para no volver a buscarlo.

El destino de iTunes sale de `MUSIFLIX_ITUNES`. Si no está configurado o no existe, esto
falla con un mensaje que dice qué definir; no inventa una carpeta ni escribe en el cwd.
"""
import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from calidad.escribir_tags import escribir_campos
from pipeline import config
from pipeline.reporte import APROBADO


@dataclass
class Resultado:
    copiados: list[str]
    omitidos: dict[str, int]      # estado → cuántos
    faltantes: list[str]          # aprobados cuyo archivo no está en el staging
    destino_itunes: str
    xml: str


def leer_decisiones(ruta: Path) -> list[dict]:
    doc = json.loads(ruta.read_text(encoding="utf-8"))
    if isinstance(doc, list):          # tolera un JSON que sea la lista pelada
        return doc
    return doc.get("decisiones", [])


def _ruta_de(d: dict) -> str:
    return d.get("ruta_staging") or d.get("ruta") or ""


def escribir_xml_rekordbox(decisiones: list[dict], destino: Path,
                           carpeta_itunes: Path) -> Path:
    """XML de colección importable por Rekordbox, apuntando a los archivos ya copiados.

    Es la ÚNICA vía por la que pueden viajar los cue points. El formato es el mismo que
    lee `ground_truth.rekordbox.parsear`, así que lo que se exporta se puede volver a
    auditar con el harness sin escribir un parser nuevo.
    """
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", {"Name": "MusiFlix", "Version": "1.0",
                                    "Company": "MusiFlix"})
    col = ET.SubElement(root, "COLLECTION", {"Entries": str(len(decisiones))})
    for i, d in enumerate(decisiones, 1):
        archivo = Path(_ruta_de(d)).name
        final = carpeta_itunes / archivo
        ET.SubElement(col, "TRACK", {
            "TrackID": str(i),
            "Name": d.get("titulo", "") or Path(archivo).stem,
            "Artist": d.get("artista", ""),
            "Kind": Path(archivo).suffix.lstrip(".").upper() + " File",
            "Location": "file://localhost/" + quote(final.resolve().as_posix(), safe="/:"),
        })
    # Una playlist con todo lo aprobado, para que entre agrupado y no suelto.
    playlists = ET.SubElement(root, "PLAYLISTS")
    nodo = ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "1"})
    lista = ET.SubElement(nodo, "NODE", {"Name": "MusiFlix", "Type": "1",
                                         "KeyType": "0", "Entries": str(len(decisiones))})
    for i in range(1, len(decisiones) + 1):
        ET.SubElement(lista, "TRACK", {"Key": str(i)})

    destino.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(destino, encoding="utf-8", xml_declaration=True)
    return destino


def aplicar(decisiones: list[dict], carpeta_itunes: Path, xml_destino: Path,
            copiar: bool = True) -> Resultado:
    aprobadas = [d for d in decisiones if d.get("estado") == APROBADO]
    omitidos: dict[str, int] = {}
    for d in decisiones:
        e = d.get("estado", "pendiente")
        if e != APROBADO:
            omitidos[e] = omitidos.get(e, 0) + 1

    copiados, faltantes = [], []
    for d in aprobadas:
        origen = Path(_ruta_de(d))
        if not origen.exists():
            faltantes.append(str(origen))
            continue
        if copiar:
            final = carpeta_itunes / origen.name
            shutil.copy2(origen, final)
            # Lo que se completó a mano en el reporte se escribe sobre la copia final.
            # Es la razón de ser de los campos editables: sin esto el reporte avisa del
            # problema pero no lo resuelve, y renombrar 28 archivos ya dentro de iTunes
            # —sin el contexto de la carpeta de descargas— es el trabajo que se evita.
            vals = {k: v for k, v in (("artista", (d.get("artista") or "").strip()),
                                      ("titulo", (d.get("titulo") or "").strip())) if v}
            if vals and d.get("editado"):
                escribir_campos(final, vals)
        copiados.append(origen.name)

    # El XML se arma solo con lo que efectivamente se copió.
    presentes = [d for d in aprobadas if Path(_ruta_de(d)).name in set(copiados)]
    escribir_xml_rekordbox(presentes, xml_destino, carpeta_itunes)

    return Resultado(copiados=copiados, omitidos=omitidos, faltantes=faltantes,
                     destino_itunes=str(carpeta_itunes), xml=str(xml_destino))


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="pipeline.aplicar",
        description="Copia a iTunes y exporta a Rekordbox SOLO lo aprobado en el reporte.")
    ap.add_argument("--decisiones", required=True, type=Path)
    ap.add_argument("--itunes", type=Path, default=None,
                    help="Carpeta de iTunes (default: MUSIFLIX_ITUNES).")
    ap.add_argument("--xml", type=Path, default=None,
                    help="Dónde escribir el XML de Rekordbox (default: MUSIFLIX_REKORDBOX_XML).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Mostrar qué se copiaría, sin copiar.")
    args = ap.parse_args(argv)

    if not args.decisiones.exists():
        print(f"No existe el archivo de decisiones: {args.decisiones}")
        return 1

    try:
        carpeta = args.itunes or config.itunes_dir()
    except config.DestinoNoConfigurado as e:
        print(f"\n{e}\n")
        return 2

    decisiones = leer_decisiones(args.decisiones)
    if not decisiones:
        print("El JSON no trae decisiones.")
        return 1

    xml_destino = args.xml or config.rekordbox_xml()
    res = aplicar(decisiones, carpeta, xml_destino, copiar=not args.dry_run)

    print(f"\n{'DRY-RUN — no se copió nada' if args.dry_run else 'Aplicado'}")
    print(f"  aprobados y copiados : {len(res.copiados)}")
    for estado, n in sorted(res.omitidos.items()):
        print(f"  {estado:20} : {n}  (no se movieron)")
    if res.faltantes:
        print(f"  aprobados SIN archivo en el staging: {len(res.faltantes)}")
        for f in res.faltantes[:5]:
            print(f"      {f}")
    print(f"\n  iTunes   → {res.destino_itunes}")
    print(f"  Rekordbox→ {res.xml}")
    print("\nLos cue points viajan SOLO por el XML: iTunes no los transporta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
