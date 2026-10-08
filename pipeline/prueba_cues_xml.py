"""Prueba manual: ¿cómo toma Rekordbox los cues y un loop de nuestro XML? (tarea 5.68)

    python -m pipeline.prueba_cues_xml --audio "<track>" --salida "<carpeta>/prueba_cues.xml"

Escribe un XML de Rekordbox con UN track y cuatro marcas, para importarlo a mano en un
Rekordbox real y anotar qué muestra. La guía paso a paso está en `pipeline/PRUEBA_CUES.md`.

NO es parte del pipeline. `pipeline.aplicar` sigue sin escribir ningún POSITION_MARK
(lo exige `test_todavia_no_se_inventan_cues`): acá las marcas son de PRUEBA, en segundos
que elige quien la corre, no detectadas. Este script reusa el armado del XML del pipeline
(`armar_xml_rekordbox`: misma estructura, mismo escape, misma `Location`) y solo cuelga
las marcas del `<TRACK>`.

QUÉ SE ESCRIBE Y POR QUÉ
------------------------
Según la especificación pública del formato XML de Rekordbox:
- `Type`: 0 = cue, 1 = fade-in, 2 = fade-out, 3 = load, 4 = loop.
- `Num`: -1 = memory cue; 0..7 = hot cue A..H.
- `Start` / `End` en segundos; el loop lleva `End`.
- `Red` / `Green` / `Blue` (0-255): color, solo en hot cues.

Las marcas por defecto:

    hot cue 1   Type=0  Num=0   Start=30.000              Name="PRUEBA hot 1"   rojo
    hot cue 2   Type=0  Num=1   Start=60.000              Name=""               azul
    memory cue  Type=0  Num=-1  Start=15.000              Name="PRUEBA memory"  sin color
    loop        Type=4  Num=2   Start=90.000  End=94.000  Name="PRUEBA loop 4s" naranja

AMBIGÜEDAD QUE ESTA PRUEBA VIENE A RESOLVER
-------------------------------------------
`ground_truth/cues.py:33-35` lee el XML real del dueño suponiendo otra cosa: trata
`Type=4, Num=0, sin nombre` como "memory cue anónimo", y corrido sobre ese XML contó muchas
más marcas Type 4 que Type 0. Según la especificación, `Type=4` es un loop y `Num=0` es el hot
cue A, y el memory cue es `Num=-1`. Además `ground_truth/rekordbox.py:82-85` no lee `End`,
así que con lo que hay en el repo no se puede saber si esas marcas Type 4 son loops. No se
asume ninguna de las dos lecturas: este XML sigue la especificación pública, y lo que
muestre Rekordbox (y cómo lo vuelva a exportar) dice cuál vale.

El audio NO se modifica: se lee para medir la duración (`motor.analisis.cargar`). BPM y
tonalidad salen de la base del motor si se pasa `--db` y el track está analizado; si no,
esos atributos no se escriben (igual que en el pipeline: nada de 0 ni placeholders).
La base se abre solo para leer, y si su esquema no es el actual no se abre (el `Store`
la migraría, y eso es escribirla).
"""
from __future__ import annotations

import argparse
import contextlib
import sqlite3
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from pipeline.aplicar import armar_xml_rekordbox, guardar_xml_rekordbox

# Valores de la especificación pública del XML de Rekordbox (ver docstring del módulo).
TYPE_CUE = 0
TYPE_LOOP = 4
NUM_MEMORY = -1
HOT_CUES = range(0, 8)   # Num 0..7 = hot cue A..H

# Colores RGB de prueba: distintos entre sí para reconocer cada marca en pantalla. Si
# Rekordbox los cambia por otro color de su paleta, eso también es un resultado.
ROJO = (230, 40, 40)
AZUL = (48, 90, 255)
NARANJA = (255, 160, 0)


class ErrorDePrueba(Exception):
    """Algo que quien corre la prueba tiene que corregir. Se imprime sin traceback."""


class CueFueraDelTrack(ErrorDePrueba, ValueError):
    """Una marca cae fuera del audio. No se escribe nada: un cue fuera del track no se
    puede probar, y la prueba quedaría incompleta sin que se note."""


@dataclass(frozen=True)
class Marca:
    """Un POSITION_MARK. `end` solo en loops."""
    name: str
    type: int
    num: int
    start: float
    end: float | None = None
    color: tuple[int, int, int] | None = None

    @property
    def descripcion(self) -> str:
        if self.type == TYPE_LOOP:
            return f"loop (Num={self.num})"
        if self.num == NUM_MEMORY:
            return "memory cue"
        return f"hot cue {self.num + 1}"


def marcas_de_prueba(hot1: float = 30.0, hot2: float = 60.0, memory: float = 15.0,
                     loop: float = 90.0, loop_largo: float = 4.0,
                     nombre_hot1: str = "PRUEBA hot 1") -> list[Marca]:
    """Las cuatro marcas de la prueba, en segundos."""
    return [
        Marca(nombre_hot1, TYPE_CUE, 0, hot1, color=ROJO),
        Marca("", TYPE_CUE, 1, hot2, color=AZUL),
        Marca("PRUEBA memory", TYPE_CUE, NUM_MEMORY, memory),
        Marca(f"PRUEBA loop {loop_largo:g}s", TYPE_LOOP, 2, loop, end=loop + loop_largo,
              color=NARANJA),
    ]


def validar_marcas(marcas: list[Marca], duracion_s: float) -> None:
    """Rechaza marcas que no se pueden escribir. Las que caen fuera del audio levantan
    `CueFueraDelTrack`; las mal formadas, `ErrorDePrueba`. Se juntan todas en un mensaje."""
    fuera, mal = [], []
    for m in marcas:
        if m.num != NUM_MEMORY and m.num not in HOT_CUES:
            mal.append(f"{m.descripcion}: Num={m.num} no es -1 ni 0..7")
        if m.type == TYPE_LOOP:
            if m.end is None or m.end <= m.start:
                mal.append(f"{m.descripcion}: el loop necesita End > Start")
        elif m.end is not None:
            mal.append(f"{m.descripcion}: solo un loop lleva End")
        if m.start < 0:
            mal.append(f"{m.descripcion}: Start negativo ({m.start:.3f} s)")
        if m.start >= duracion_s or (m.end is not None and m.end > duracion_s):
            hasta = f"–{m.end:.3f}" if m.end is not None else ""
            fuera.append(f"{m.descripcion} en {m.start:.3f}{hasta} s")
    hot = [m.num for m in marcas if m.num in HOT_CUES]
    if len(hot) != len(set(hot)):
        mal.append(f"dos marcas usan el mismo slot de hot cue: Num={sorted(hot)}")
    if mal:
        raise ErrorDePrueba("Marcas mal formadas:\n  " + "\n  ".join(mal))
    if fuera:
        raise CueFueraDelTrack(
            f"El audio dura {duracion_s:.3f} s y estas marcas caen fuera:\n  "
            + "\n  ".join(fuera)
            + "\n  No se escribió nada. Elegí un track más largo o mové las marcas "
              "(--hot1, --hot2, --memory, --loop, --loop-largo).")


def atributos_marca(m: Marca) -> dict[str, str]:
    """Atributos del POSITION_MARK, con los tiempos en segundos y tres decimales."""
    attrs = {"Name": m.name, "Type": str(m.type), "Start": f"{m.start:.3f}"}
    if m.end is not None:
        attrs["End"] = f"{m.end:.3f}"
    attrs["Num"] = str(m.num)
    if m.color is not None:
        attrs["Red"], attrs["Green"], attrs["Blue"] = (str(c) for c in m.color)
    return attrs


def medir_duracion(audio: Path) -> float:
    """Duración en segundos, leyendo el audio con la carga del motor. No lo modifica."""
    from motor.analisis import SR, cargar

    y = cargar(audio)
    if y is None:
        raise ErrorDePrueba(f"No se pudo leer el audio (o dura menos de 1 s): {audio}")
    return y.size / SR


def datos_de_la_base(db: Path, audio: Path) -> dict:
    """BPM y key (Camelot) del track si la base del motor lo tiene analizado; `{}` si no.

    La base NO se modifica: primero se mira la versión del esquema con una conexión de solo
    lectura, y si no es la actual no se abre con el `Store` (la migraría)."""
    from motor.store import VERSION_ESQUEMA, Store

    if not db.is_file():
        raise ErrorDePrueba(f"No existe la base {db}")
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        version = int(con.execute("PRAGMA user_version").fetchone()[0])
    finally:
        con.close()
    if version != VERSION_ESQUEMA:
        raise ErrorDePrueba(
            f"La base {db} tiene esquema versión {version} y el actual es {VERSION_ESQUEMA}: "
            f"abrirla la migraría, y esta prueba no escribe la base. Corré sin --db.")
    with Store(db) as store:
        f = store.get_features(audio)
    if f is None:
        return {}
    datos = {}
    if f.bpm and f.bpm > 0:
        datos["bpm"] = f.bpm
    if f.key:
        datos["camelot"] = f.key
    return datos


def escribir_xml_prueba(audio: Path, duracion_s: float, marcas: list[Marca], destino: Path,
                        datos: dict | None = None) -> Path:
    """XML de Rekordbox con un track y sus marcas. Valida ANTES de escribir: si una marca
    no entra, no se crea el archivo.

    El `<TRACK>` sale de `armar_xml_rekordbox`, el mismo armado del pipeline. Pasar la
    carpeta del audio como "carpeta de iTunes" hace que `Location` apunte al audio mismo,
    con el mismo `file://localhost/...` que escribe el pipeline. No se pasa `acuerdo`, así
    que no hay `Comments`: la prueba no le cambia al track más de lo necesario.
    """
    validar_marcas(marcas, duracion_s)
    d = {"ruta": str(audio), "duracion_s": duracion_s, **(datos or {})}
    root, (track,) = armar_xml_rekordbox([d], audio.parent)
    for m in marcas:
        ET.SubElement(track, "POSITION_MARK", atributos_marca(m))
    return guardar_xml_rekordbox(root, destino)


def main(argv=None) -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="pipeline.prueba_cues_xml",
        description="XML de PRUEBA para ver cómo toma Rekordbox 2 hot cues, una memory cue "
                    "y un loop. Guía: pipeline/PRUEBA_CUES.md")
    ap.add_argument("--audio", required=True, type=Path, help="El track (no se modifica).")
    ap.add_argument("--salida", required=True, type=Path, help="Dónde escribir el .xml.")
    ap.add_argument("--db", type=Path, default=None,
                    help="Base del motor, solo para leer BPM y key si el track está "
                         "analizado. Sin esto, el XML no lleva BPM ni key.")
    ap.add_argument("--hot1", type=float, default=30.0, help="Hot cue 1, en s (default 30).")
    ap.add_argument("--hot2", type=float, default=60.0, help="Hot cue 2, en s (default 60).")
    ap.add_argument("--memory", type=float, default=15.0, help="Memory cue, en s (default 15).")
    ap.add_argument("--loop", type=float, default=90.0, help="Inicio del loop, en s (default 90).")
    ap.add_argument("--loop-largo", type=float, default=4.0,
                    help="Largo del loop, en s (default 4).")
    ap.add_argument("--nombre-hot1", default="PRUEBA hot 1", help="Nombre del hot cue 1.")
    args = ap.parse_args(argv)

    try:
        if not args.audio.is_file():
            raise ErrorDePrueba(f"No existe el audio: {args.audio}")
        if args.salida.suffix.lower() != ".xml":
            raise ErrorDePrueba(f"La salida tiene que ser un .xml: {args.salida}")
        marcas = marcas_de_prueba(args.hot1, args.hot2, args.memory, args.loop,
                                  args.loop_largo, args.nombre_hot1)
        duracion = medir_duracion(args.audio)
        datos = datos_de_la_base(args.db, args.audio) if args.db else {}
        destino = escribir_xml_prueba(args.audio, duracion, marcas, args.salida, datos)
    except CueFueraDelTrack as e:
        print(f"\nAVISO: {e}\n")
        return 2
    except ErrorDePrueba as e:
        print(f"\n{e}\n")
        return 1

    print(f"\nXML de prueba → {destino}")
    print(f"  track    : {args.audio.name}  ({duracion:.3f} s)")
    sin_dato = "(sin dato, no se escribe)"
    print(f"  BPM      : {datos['bpm']:.1f}" if "bpm" in datos else f"  BPM      : {sin_dato}")
    tonality = ET.parse(destino).getroot().find("COLLECTION/TRACK").get("Tonality")
    print(f"  key      : {datos['camelot']} (Tonality={tonality})" if tonality
          else f"  key      : {sin_dato}")
    for m in marcas:
        a = atributos_marca(m)
        print(f"  {m.descripcion:16} Type={a['Type']} Num={a['Num']:>2} Start={a['Start']}"
              + (f" End={a['End']}" if "End" in a else "") + f" Name={a['Name']!r}")
    print("\nSeguí pipeline/PRUEBA_CUES.md. Usá un track sin cues valiosos: Rekordbox puede "
          "pisarlos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
