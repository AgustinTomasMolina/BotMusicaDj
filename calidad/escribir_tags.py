"""Tarea 5.3 (segunda mitad, parte 2) — ESCRITURA de tags normalizados.

    python -m calidad.escribir_tags --audio <carpeta>                     # dry-run
    python -m calidad.escribir_tags --audio <carpeta> --destino <dir> --escribir

DRY-RUN POR DEFECTO. Sin `--escribir` no toca nada: solo arma el plan y el CSV
antes/después. Y aun con `--escribir`, JAMÁS se escribe sobre el original: se copia al
destino y se taggea la COPIA (regla del proyecto).

QUÉ SE ESCRIBE Y DÓNDE
----------------------
TXXX:MusiFlix — SIEMPRE, en todo archivo procesado. JSON con el corte medido, el muro, la
    bandera, la versión del analizador y la fecha. No pisa ningún campo de nadie y
    responde algo que hoy no tiene respuesta: "¿este archivo pasó por el pipeline?"
    (la auditoría encontró 0 tags propios en 57 archivos).

COMM — SOLO en los sospechosos, y SOLO si está vacío. Un aviso que aparece en el 100% de
    los archivos deja de ser un aviso. Si el comentario tiene contenido ajeno —el
    "1A - Energy 7" que deja Mixed In Key— NO se toca: el dato igual queda en TXXX.

artista / título — solo si hay propuesta Y el campo está vacío. Lo que ya estaba se
    preserva: esto normaliza, no reescribe. Sin propuesta, el campo queda VACÍO; nunca
    "Unknown" (spec §6: un dato que miente es peor que uno ausente).

género — NO se escribe. No hay de dónde sacarlo sin inventarlo.
grupo de duplicados — NO va al tag. Es una propiedad de la biblioteca, no del track, y
    quedaría desactualizado apenas se borre un archivo del grupo. Vive en el CSV de 5.2.

TKEY y TBPM — NUNCA se escriben, siempre se preservan:
  · Los 3 de Serato conservan los suyos.
  · Las 2 keys externas ('Am', 'Gm') se preservan EN NOTACIÓN CLÁSICA, sin convertir a
    Camelot: convertirlas borraría la evidencia de que son externas, y son las dos únicas
    referencias independientes que hay en la biblioteca.
  · La key de Mixed In Key ('1A') se preserva y se marca como MIK — ni externa ni propia.
    MIK es otro algoritmo, no una verdad humana.

Por qué no se usa `tagger.taggear()`: ese escribe TKEY en Camelot y no sabe escribir TXXX
ni preservar lo existente. Acá se necesita justamente lo contrario.
"""
import argparse
import csv
import datetime
import json
import shutil
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from calidad.corte_espectral import SOSPECHOSO
from calidad.corte_espectral import analizar_uno as medir_calidad
from calidad.normalizar_tags import EXTS, analizar
from calidad.tags import MIXED_IN_KEY, SERATO, detectar_taggers, leer_tags

VERSION_ANALIZADOR = "1.0"

ESCRIBIR, PRESERVAR, VACIO, OMITIR = "escribir", "preservar", "vacio", "omitir"


@dataclass
class Cambio:
    """Una fila del CSV antes/después: un campo de un archivo."""

    archivo: str
    campo: str
    antes: str
    despues: str
    accion: str        # escribir | preservar | vacio | omitir
    motivo: str


COLUMNAS = [f.name for f in fields(Cambio)]


def registro_musiflix(cal) -> str:
    """El JSON que va a TXXX:MusiFlix. Legible por máquina y por humano."""
    return json.dumps({
        "v": VERSION_ANALIZADOR,
        "fecha": datetime.date.today().isoformat(),
        "corte_khz": cal.corte_medido_khz,
        "muro_db": cal.muro_db,
        "es_muro": cal.es_muro,
        "bandera": cal.bandera,
        "criterio": cal.criterio,
    }, ensure_ascii=False, separators=(",", ":"))


def planificar(prop, cal, tags_actuales: dict) -> list[Cambio]:
    """Arma el plan de un archivo. No escribe: decide."""
    arch = prop.archivo
    taggers = detectar_taggers(tags_actuales["claves"], tags_actuales["comentario"])
    cambios: list[Cambio] = []

    # --- artista y título ---
    for campo, actual, propuesto in (("artista", tags_actuales["artista"], prop.artista_propuesto),
                                     ("titulo", tags_actuales["titulo"], prop.titulo_propuesto)):
        if actual:
            cambios.append(Cambio(arch, campo, actual, actual, PRESERVAR,
                                  "ya tenía valor: esto normaliza, no reescribe"))
        elif propuesto:
            extra = f" · {prop.motivo}" if prop.motivo else ""
            cambios.append(Cambio(arch, campo, "", propuesto, ESCRIBIR,
                                  f"del nombre de archivo{extra}"))
        else:
            cambios.append(Cambio(arch, campo, "", "", VACIO,
                                  "no se pudo determinar; queda vacío, no 'Unknown'"))

    # --- género: decisión explícita de no escribirlo ---
    cambios.append(Cambio(arch, "genero", tags_actuales["genero"], tags_actuales["genero"],
                          PRESERVAR if tags_actuales["genero"] else OMITIR,
                          "no se escribe: no hay de dónde sacarlo sin inventarlo"))

    # --- TKEY y TBPM: nunca se escriben ---
    if tags_actuales["key"]:
        if MIXED_IN_KEY in taggers:
            de_quien = "de Mixed In Key (otro algoritmo, no una verdad humana)"
        elif SERATO in taggers:
            de_quien = "de Serato"
        else:
            de_quien = "externa, en notación clásica: es referencia independiente"
        cambios.append(Cambio(arch, "key", tags_actuales["key"], tags_actuales["key"],
                              PRESERVAR, f"se preserva tal cual, sin convertir a Camelot — {de_quien}"))
    if tags_actuales["bpm"]:
        cambios.append(Cambio(arch, "bpm", tags_actuales["bpm"], tags_actuales["bpm"],
                              PRESERVAR, "se preserva: el motor no pisa un BPM existente"))

    # --- TXXX:MusiFlix: siempre ---
    cambios.append(Cambio(arch, "TXXX:MusiFlix", "", registro_musiflix(cal), ESCRIBIR,
                          "registro del pipeline; no pisa ningún campo de nadie"))

    # --- COMM: solo sospechosos y solo si está vacío ---
    com = tags_actuales["comentario"]
    if cal.bandera != SOSPECHOSO:
        cambios.append(Cambio(arch, "comentario", com, com, OMITIR,
                              "no es sospechoso: un aviso en el 100% de los archivos no es un aviso"))
    elif com:
        cambios.append(Cambio(arch, "comentario", com, com, PRESERVAR,
                              "sospechoso, pero el comentario tiene contenido ajeno: "
                              "no se pisa (el dato queda en TXXX)"))
    else:
        cambios.append(Cambio(arch, "comentario", "", f"MusiFlix · revisar · {cal.motivo}",
                              ESCRIBIR, "sospechoso y comentario vacío"))
    return cambios


# --- Escritura sobre la COPIA --------------------------------------------------------------

_ID3 = (".mp3", ".wav", ".aiff", ".aif")
_VORBIS = (".flac", ".ogg", ".opus")
_MP4 = (".m4a", ".mp4", ".m4b")


def _valores(cambios: list[Cambio]) -> dict[str, str]:
    """Solo lo que hay que ESCRIBIR, por campo."""
    return {c.campo: c.despues for c in cambios if c.accion == ESCRIBIR}


def escribir_en_copia(origen: str, destino_dir: Path, cambios: list[Cambio]) -> Path:
    """Copia el archivo al destino y taggea LA COPIA. El original no se abre para escritura."""
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / Path(origen).name
    shutil.copy2(origen, destino)          # copy2 preserva mtime; el original solo se lee

    vals = _valores(cambios)
    if not vals:
        return destino
    ext = destino.suffix.lower()
    if ext in _ID3:
        _escribir_id3(destino, vals)
    elif ext in _VORBIS:
        _escribir_vorbis(destino, vals)
    elif ext in _MP4:
        _escribir_mp4(destino, vals)
    return destino


def _aplicar_frames(tags, vals: dict) -> None:
    """Carga los frames ID3 sobre un objeto tags ya abierto."""
    from mutagen.id3 import COMM, TIT2, TPE1, TXXX

    if vals.get("artista"):
        tags.setall("TPE1", [TPE1(encoding=3, text=vals["artista"])])
    if vals.get("titulo"):
        tags.setall("TIT2", [TIT2(encoding=3, text=vals["titulo"])])
    if vals.get("TXXX:MusiFlix"):
        tags.setall("TXXX:MusiFlix",
                    [TXXX(encoding=3, desc="MusiFlix", text=vals["TXXX:MusiFlix"])])
    if vals.get("comentario"):
        tags.setall("COMM", [COMM(encoding=3, lang="spa", desc="", text=vals["comentario"])])


def _escribir_id3(ruta: Path, vals: dict) -> None:
    """ID3 según el CONTENEDOR, que no es lo mismo que el formato de los frames.

    Un WAV y un AIFF llevan ID3 adentro de un chunk del RIFF/IFF. Abrirlos con
    `ID3(ruta).save()` antepone el tag al archivo y lo CORROMPE — `tagger.py:55-58` ya
    documenta esta trampa, y este módulo se la comió igual: en la primera corrida dejó
    26 de 57 copias ilegibles. Solo el MP3 admite el ID3 pelado.
    """
    ext = ruta.suffix.lower()
    if ext == ".wav":
        from mutagen.wave import WAVE

        audio = WAVE(str(ruta))
        if audio.tags is None:
            audio.add_tags()
        _aplicar_frames(audio.tags, vals)
        audio.save()
    elif ext in (".aiff", ".aif"):
        from mutagen.aiff import AIFF

        audio = AIFF(str(ruta))
        if audio.tags is None:
            audio.add_tags()
        _aplicar_frames(audio.tags, vals)
        audio.save()
    else:
        from mutagen.id3 import ID3, ID3NoHeaderError

        try:
            tags = ID3(str(ruta))
        except ID3NoHeaderError:
            tags = ID3()
        _aplicar_frames(tags, vals)
        tags.save(str(ruta))


def _escribir_vorbis(ruta: Path, vals: dict) -> None:
    from mutagen import File as MFile

    m = MFile(str(ruta))
    if m is None:
        return
    if vals.get("artista"):
        m["artist"] = vals["artista"]
    if vals.get("titulo"):
        m["title"] = vals["titulo"]
    if vals.get("TXXX:MusiFlix"):
        m["musiflix"] = vals["TXXX:MusiFlix"]
    if vals.get("comentario"):
        m["comment"] = vals["comentario"]
    m.save()


def _escribir_mp4(ruta: Path, vals: dict) -> None:
    from mutagen.mp4 import MP4

    m = MP4(str(ruta))
    if vals.get("artista"):
        m["\xa9ART"] = [vals["artista"]]
    if vals.get("titulo"):
        m["\xa9nam"] = [vals["titulo"]]
    if vals.get("TXXX:MusiFlix"):
        m["----:com.apple.iTunes:MusiFlix"] = [vals["TXXX:MusiFlix"].encode("utf-8")]
    if vals.get("comentario"):
        m["\xa9cmt"] = [vals["comentario"]]
    m.save()


# --- Informe ---------------------------------------------------------------------------------


def escribir_csv(cambios: list[Cambio], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = out_dir / f"tags_antes_despues_{sello}.csv"
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS)
        w.writeheader()
        for c in cambios:
            w.writerow(asdict(c))
    return destino


def informe(cambios: list[Cambio], n_archivos: int, escribio: bool) -> None:
    print(f"\n{'=' * 78}")
    print(f"{'ESCRITO' if escribio else 'DRY-RUN (no se escribió nada)'} — "
          f"{n_archivos} archivos · {len(cambios)} decisiones")
    print(f"{'=' * 78}")

    por_accion: dict[tuple, int] = {}
    for c in cambios:
        por_accion[(c.campo, c.accion)] = por_accion.get((c.campo, c.accion), 0) + 1
    print(f"\n  {'campo':16} {'acción':11} {'archivos'}")
    for (campo, accion), n in sorted(por_accion.items()):
        print(f"  {campo:16} {accion:11} {n:>5}")

    escrituras = [c for c in cambios if c.accion == ESCRIBIR and c.campo in ("artista", "titulo")]
    if escrituras:
        print(f"\n  Artista/título que se escriben ({len(escrituras)}):")
        vistos = set()
        for c in escrituras:
            if c.archivo in vistos:
                continue
            vistos.add(c.archivo)
            del_arch = [x for x in escrituras if x.archivo == c.archivo]
            campos = " | ".join(f"{x.campo}='{x.despues[:30]}'" for x in del_arch)
            print(f"    {c.archivo[:44]:44} {campos}")

    coms = [c for c in cambios if c.campo == "comentario" and c.accion == ESCRIBIR]
    print(f"\n  COMM que se escriben ({len(coms)}) — solo sospechosos con comentario vacío:")
    for c in coms:
        print(f"    {c.archivo[:44]:44} {c.despues[:60]}")

    preservados = [c for c in cambios if c.accion == PRESERVAR and c.campo in ("key", "bpm")]
    if preservados:
        print(f"\n  TKEY/TBPM preservados ({len(preservados)}):")
        for c in preservados:
            print(f"    {c.archivo[:40]:40} {c.campo:4}='{c.antes}'  {c.motivo[:52]}")
    print(f"{'=' * 78}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="calidad.escribir_tags",
        description="Normaliza tags sobre una COPIA. Dry-run por defecto.")
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--destino", type=Path, default=None,
                    help="Carpeta donde dejar las copias taggeadas. Obligatoria con --escribir.")
    ap.add_argument("--escribir", action="store_true",
                    help="Escribir de verdad. Sin esto no se toca nada.")
    ap.add_argument("--out", type=Path, default=Path("calidad/out"))
    args = ap.parse_args(argv)

    if args.escribir and not args.destino:
        print("--escribir necesita --destino: nunca se escribe sobre el original.")
        return 1
    if args.destino and args.audio.resolve() == args.destino.resolve():
        print("El destino no puede ser la carpeta de origen: se escribiría sobre el original.")
        return 1

    rutas = sorted(str(p) for p in args.audio.rglob("*") if p.suffix.lower() in EXTS)
    if not rutas:
        print(f"No encontré audio en {args.audio}")
        return 1

    print(f"Analizando {len(rutas)} archivos…")
    props = {p.ruta: p for p in analizar(rutas)}

    cambios: list[Cambio] = []
    for i, ruta in enumerate(rutas, 1):
        cal = medir_calidad(ruta)
        plan = planificar(props[ruta], cal, leer_tags(ruta))
        cambios.extend(plan)
        if args.escribir:
            escribir_en_copia(ruta, args.destino, plan)
        if i % 10 == 0:
            print(f"  {i}/{len(rutas)}…", flush=True)

    informe(cambios, len(rutas), args.escribir)
    print(f"Antes/después por campo → {escribir_csv(cambios, args.out)}")
    if args.escribir:
        print(f"Copias taggeadas en → {args.destino}")
        print("Los originales no se abrieron para escritura.")
    else:
        print("\nDRY-RUN: no se escribió ni se copió nada. "
              "Para aplicar: --destino <dir> --escribir")
    return 0


if __name__ == "__main__":
    sys.exit(main())
