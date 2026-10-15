"""Cues entre MusiFlix y Rekordbox (f56): los POSITION_MARK del XML, de ida y de vuelta.

Lógica pura: no importa `server` ni `db`. El server decide qué temas van y de dónde salen las
rutas; acá se traduce una marca de la página (`motor/cue_marks.py`) a un `POSITION_MARK` y al
revés, y se arma el XML de una playlist.

LA CORRESPONDENCIA (verificada en Rekordbox 7.2.16, `pipeline/PRUEBA_CUES.md` §5: importó las
marcas en su lugar y las reexportó idénticas, atributo por atributo):

    hot cue      Type="0"  Num=0..7 (pad A..H)  Start            Red/Green/Blue = color del pad
    memory cue   Type="0"  Num="-1"             Start            sin color
    hot loop     Type="4"  Num=0..7             Start y End      naranja (siempre)
    memory loop  Type="4"  Num="-1"             Start y End      naranja (siempre)

Los atributos los escribe `pipeline.prueba_cues_xml.atributos_marca`, el mismo código del XML
que se probó (segundos con tres decimales). Los colores salen de `motor.cue_marks.color_de_marca`
(la tabla única). El MEMORY LOOP (`Type=4`, `Num=-1`) sigue la especificación pública pero NO
se probó en un Rekordbox real (la prueba 5.68 no tenía uno, y las 243 marcas `Type=4` del XML
real del dueño viven en pads): queda en la lista de lo que el dueño tiene que mirar.

LO QUE NO VIAJA, DICHO:
- Al IMPORTAR, el color que el DJ eligió en Rekordbox no se guarda: la página no guarda colores,
  cada pad tiene el suyo (tabla del dueño, 2026-10-09) y el loop es naranja. Se cuenta cuántas
  marcas traían otro color (`color_distinto`) para decirlo en pantalla.
- `Type` 1, 2 y 3 (fade-in, fade-out, load) no existen en la página: se cuentan y no entran.
- La GRILLA (`TEMPO`): por defecto NO se exporta. La de la página es ESTIMADA (`motor.bandas`):
  si se escribe, Rekordbox la usa en vez de analizar la suya, y una grilla corrida desfasa el
  quantize y el sync en vivo. Solo con un pedido explícito, y solo en los temas donde la
  estimación dio el beat Y el 1 del compás (`tempo_de_grilla`). Los dos siguen siendo
  ESTIMADOS: el 1 sale de `motor.bandas.estimar_compas`, una heurística que nunca se midió
  contra downbeats reales, y un 1 corrido desarma el beat jump, el quantize por compás y las
  frases. El `Bpm` del TEMPO (afinado, dos decimales) puede diferir del `AverageBpm` (medido,
  un decimal).
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from motor.cue_marks import (
    HOT_CUES,
    KIND_CUE,
    KIND_LOOP,
    KIND_MEMORY,
    MAX_SECONDS,
    InvalidCueMark,
    clean_mark_name,
    color_de_marca,
)
from pipeline.prueba_cues_xml import NUM_MEMORY, TYPE_CUE, TYPE_LOOP, Marca, atributos_marca

# Motivos por los que una marca del XML no entra (las claves viajan a la pantalla con su cuenta).
IGNORADA_TIPO = "tipo"            # Type 1-3 (fade-in, fade-out, load) u otro
IGNORADA_PAD = "pad"              # Num fuera de -1..7
IGNORADA_TIEMPO = "tiempo"        # Start/End que no es un número, negativo o enorme
IGNORADA_LOOP = "loop"            # loop sin End, o con End antes del Start

# Caracteres que XML 1.0 no admite (ni escapados): un título con un \x01 de un tag roto haría un
# XML que Rekordbox no abre. Se sacan al escribir (y solo eso: el resto va tal cual).
_NO_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


# ---------------------------------------------------------------------------------- exportar

def marca_a_position_mark(kind: str, num: int | None, start_ms: int, end_ms: int | None,
                          name: str | None) -> Marca:
    """Una marca de la página → el `Marca` de la prueba verificada (ver la tabla del módulo)."""
    color = color_de_marca(kind, num)          # valida la combinación (hot cue sin pad, etc.)
    start = start_ms / 1000
    if kind == KIND_CUE:
        return Marca(name or "", TYPE_CUE, num, start, color=color)
    if kind == KIND_MEMORY:
        return Marca(name or "", TYPE_CUE, NUM_MEMORY, start)
    if end_ms is None:
        raise InvalidCueMark("un loop necesita la salida")
    return Marca(name or "", TYPE_LOOP, NUM_MEMORY if num is None else num, start,
                 end=end_ms / 1000, color=color)


def limpiar_para_xml(root: ET.Element) -> None:
    """Saca de todos los atributos los caracteres que XML 1.0 prohíbe (ver `_NO_XML`)."""
    for el in root.iter():
        for k, v in list(el.attrib.items()):
            if _NO_XML.search(v):
                el.set(k, _NO_XML.sub("", v))


def tempo_de_grilla(g: dict) -> dict | None:
    """Los atributos de `<TEMPO>` desde la grilla estimada (`motor.bandas.grilla`), o None.

    Solo si hay grilla (`primer_beat_s`) Y se sabe cuál es el 1 del compás (`compas_ref`):
    `Battito="1"` dice que `Inizio` es el primer beat de un compás, y sin saberlo sería mentir.
    `Inizio` = el primer 1 del tema; `Bpm` con dos decimales, como lo escribe Rekordbox."""
    bpm, inizio = g.get("bpm"), g.get("compas_ref")
    if g.get("primer_beat_s") is None or inizio is None or not bpm or bpm <= 0:
        return None
    if not (math.isfinite(bpm) and math.isfinite(inizio)) or inizio < 0:
        return None
    return {"Inizio": f"{inizio:.3f}", "Bpm": f"{bpm:.2f}", "Metro": "4/4", "Battito": "1"}


def armar_xml_playlist(nombre: str, temas: list[dict], orden: list[int]) -> bytes:
    """El XML de una playlist: la colección con cada tema UNA vez (con sus marcas y, si vino,
    su `TEMPO`) y la playlist en `orden` (índices de `temas`, con repetidos si un tema va dos
    veces). Cada tema: los datos de `pipeline.aplicar._atributos_track` (`ruta` = el archivo
    ORIGINAL, `titulo`, `artista`, `bpm`, `camelot`, `duracion_s`) más `marcas` (dicts como
    los de `marca_a_position_mark`) y `tempo` (atributos o None). UTF-8, con declaración y sin
    DOCTYPE."""
    from pipeline.aplicar import armar_xml_rekordbox

    root, tracks = armar_xml_rekordbox(temas, None, nombre_playlist=nombre or "MusiFlix",
                                       orden=orden)
    for t, el in zip(temas, tracks, strict=True):
        if t.get("tempo"):
            ET.SubElement(el, "TEMPO", t["tempo"])
        for m in t.get("marcas") or ():
            pm = marca_a_position_mark(m["kind"], m["num"], m["start_ms"], m["end_ms"], m["name"])
            ET.SubElement(el, "POSITION_MARK", atributos_marca(pm))
    limpiar_para_xml(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------------------------- importar

def _num(texto) -> float | None:
    try:
        v = float(texto)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ms(segundos: float | None) -> int | None:
    if segundos is None or segundos < 0 or segundos > MAX_SECONDS:
        return None
    return int(round(segundos * 1000))


def marcas_de_track(track: ET.Element) -> tuple[list[dict], dict[str, int]]:
    """Los POSITION_MARK de un `<TRACK>` de Rekordbox como marcas de la página, y cuántas no
    entran y por qué. Cada marca: {kind, num, start_ms, end_ms, name}.

    Además de los motivos de `IGNORADA_*`, cuenta (sin descartar la marca):
    - `color_distinto`: traía un color que no es el de su pad en la página (se usa el de la
      página: no se guarda color por marca);
    - `nombre_descartado`: el nombre no se puede guardar (más de 64 caracteres, saltos de línea
      o invisibles; ver `clean_mark_name`): entra sin nombre, no con uno recortado."""
    marcas: list[dict] = []
    cuenta: dict[str, int] = {}

    def sumar(k: str) -> None:
        cuenta[k] = cuenta.get(k, 0) + 1

    for pm in track.findall("POSITION_MARK"):
        tipo = (pm.get("Type") or "").strip()
        if tipo not in (str(TYPE_CUE), str(TYPE_LOOP)):
            sumar(IGNORADA_TIPO)
            continue
        crudo = (pm.get("Num") or "").strip()
        try:
            num = int(crudo) if crudo else NUM_MEMORY
        except ValueError:
            sumar(IGNORADA_PAD)
            continue
        if num != NUM_MEMORY and not 0 <= num < HOT_CUES:
            sumar(IGNORADA_PAD)
            continue
        start_ms = _ms(_num(pm.get("Start")))
        if start_ms is None:
            sumar(IGNORADA_TIEMPO)
            continue
        end_ms = None
        pad = None if num == NUM_MEMORY else num
        if tipo == str(TYPE_LOOP):
            fin = pm.get("End")
            end_ms = _ms(_num(fin)) if fin not in (None, "") else None
            if fin not in (None, "") and end_ms is None:
                sumar(IGNORADA_TIEMPO)
                continue
            if end_ms is None or end_ms <= start_ms:
                sumar(IGNORADA_LOOP)
                continue
            kind = KIND_LOOP
        else:
            kind = KIND_MEMORY if pad is None else KIND_CUE
        try:
            name = clean_mark_name(pm.get("Name"))
        except InvalidCueMark:
            name = None
            sumar("nombre_descartado")
        rgb = tuple(_num(pm.get(c)) for c in ("Red", "Green", "Blue"))
        if all(v is not None for v in rgb) and tuple(int(v) for v in rgb) != color_de_marca(kind, pad):
            sumar("color_distinto")
        marcas.append({"kind": kind, "num": pad, "start_ms": start_ms, "end_ms": end_ms,
                       "name": name})
    return marcas, cuenta


_NOMBRE_PROHIBIDO = re.compile('[<>:"/\\\\|?*\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]+')


def nombre_archivo_xml(nombre_playlist: str) -> str:
    """El nombre del .xml que baja el navegador: seguro en Windows (nada de `<>:"/\\|?*`,
    controles C0 y C1, controles de dirección del texto —un U+202E da vuelta lo que se ve— ni
    punto o espacio al final) y nunca vacío ni un nombre reservado: lleva el prefijo fijo
    «MusiFlix - ». Si el nombre de la playlist no deja nada, «playlist»."""
    limpio = re.sub(r"\s+", " ", _NOMBRE_PROHIBIDO.sub(" ", nombre_playlist or "")).strip(" .")
    base = f"MusiFlix - {limpio or 'playlist'}"[:120].rstrip(" .")
    return f"{base}.xml"
