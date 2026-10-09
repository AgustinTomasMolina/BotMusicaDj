"""Marcas del dueño sobre un track (f48): hot cues, memory cues y loops.

Son datos del DUEÑO, no del análisis: las pone él a mano en el editor de la pantalla de Radio
DJ y valen tanto como las calificaciones de los sets guardados. Tres reglas salen de eso:

1. **Nunca se borran en silencio.** La tabla (`cue_marks`, esquema v6 en `motor/store.py`) NO
   tiene clave foránea a `tracks`: un re-escaneo que reanaliza el archivo (INSERT OR REPLACE
   de su fila) o que lo quita de la base porque hoy no está en disco no se lleva sus marcas.
   Solo las borra un pedido explícito de borrar ESA marca.
2. **Se atan a la ruta del archivo** (`path_key`, la misma clave del store: absoluta +
   `normcase`). Es lo único estable que tiene un track hoy: el store no guarda una huella del
   contenido. Consecuencia, dicha en voz alta: si el archivo se MUEVE o se RENOMBRA y se
   re-escanea, el track nuevo arranca sin marcas y las viejas quedan "huérfanas" (guardadas,
   contadas por `Store.orphan_cue_marks` y avisadas en la pantalla), no borradas. Si el
   archivo vuelve a su ruta, vuelven a aparecer.
3. **Nada se autogenera.** Este módulo no propone marcas: el detector automático es otra
   tarea.

Los tiempos se guardan en MILISEGUNDOS ENTEROS y no en segundos REAL: el editor trabaja con
precisión de milisegundo y un entero no tiene redondeos de punto flotante (12.345 s vuelve
como 12.345, no 12.344999). La API habla en segundos con tres decimales.

Pensado para exportar al XML de Rekordbox (`POSITION_MARK`). La correspondencia ya está
VERIFICADA contra Rekordbox 7.2.16 (`pipeline/PRUEBA_CUES.md` §5, 2026-10-08: importó las
marcas en su lugar y las reexportó idénticas, y las 243 marcas `Type=4` del XML real del dueño
traen `End` y viven en los pads A-H):

- hot cue      → `Type="0"`, `Num` = `num` (0..7, pads A..H), `Start` = start_ms / 1000;
- memory cue   → `Type="0"`, `Num="-1"`;
- loop         → `Type="4"`, `Start` y `End`; `Num` = `num` si el loop vive en un pad (hot
  loop, `num` 0..7) o `"-1"` si no (memory loop, `num` None).

El PAD es único por track entre hot cues y hot loops: el pad C lo ocupa UNA marca, sea cue o
loop (un pad dispara una sola cosa). Desde el esquema v6 un loop puede llevar `num`; antes
(v5) no.

`name` → `Name`. El color no se guarda: sale de `color_de_marca` (la tabla de abajo, la única
fuente: la pantalla la copia en variables CSS y un test compara las dos).
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass

KIND_CUE = "cue"          # hot cue: SIEMPRE en un pad (`num` 0..7)
KIND_MEMORY = "memory"    # memory cue: sin pad y sin salida
KIND_LOOP = "loop"        # loop: entrada y salida; con `num` es un hot loop, sin él un memory loop
KINDS = (KIND_CUE, KIND_MEMORY, KIND_LOOP)

# Los 8 pads de un CDJ / Rekordbox (A..H). `num` va de 0 a 7; la pantalla los muestra 1..8.
HOT_CUES = 8

# Colores de las marcas (pedido del dueño, 2026-10-09). Es la ÚNICA tabla: la pantalla la copia
# en variables CSS (`frontend/src/nocturne.css`: --cue-1..8, --cue-mem, --cue-loop) y
# `tests/test_colores_marcas.py` compara las dos, así no se desincronizan.
#
# - Hot cues: un color por pad, A..H.
# - Loop: SIEMPRE naranja, con o sin pad. Un hot loop en el pad C NO toma el azul del pad C: el
#   naranja es lo que lo distingue de todo lo demás en la onda y en la tabla.
# - Memory cue: sin color propio. Al exportar no lleva color (como en la prueba verificada,
#   PRUEBA_CUES.md §5: se escribió sin color y Rekordbox la mostró con el suyo); en pantalla se
#   dibuja en un neutro.
COLORES_HOT_CUE = (
    (0xFF, 0x4D, 0x5A),   # A rojo      #ff4d5a
    (0x34, 0xD1, 0x7C),   # B verde     #34d17c
    (0x4F, 0xA3, 0xFF),   # C azul      #4fa3ff
    (0xFF, 0xD2, 0x3F),   # D amarillo  #ffd23f
    (0xB4, 0x8C, 0xFF),   # E violeta   #b48cff
    (0x2F, 0xD4, 0xCF),   # F turquesa  #2fd4cf
    (0xFF, 0x7A, 0xB8),   # G rosa      #ff7ab8
    (0xF2, 0xF3, 0xF8),   # H blanco    #f2f3f8
)
COLOR_LOOP = (0xFF, 0x9A, 0x2E)               # naranja #ff9a2e
COLOR_MEMORY_PANTALLA = (0xF2, 0xF3, 0xF8)    # neutro #f2f3f8, SOLO para dibujar

# Topes por track. No son límites del formato: son que 500 memory cues en un tema no es una
# preparación, es un bucle que se escapó (o un pedido que no viene de la pantalla).
MAX_MEMORY = 32
MAX_LOOPS = 32

NAME_MAX = 64

# Además de las categorías de control de Unicode (Cc, Cs) y los separadores de línea y
# párrafo (Zl, Zp): los controles de dirección del texto. Un nombre con U+202E se dibuja al
# revés en la pantalla y en Rekordbox, y no es algo que el dueño escriba a mano.
_BIDI = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
# Cf (formato) también: son invisibles (U+200B, U+FEFF, U+2060, U+00AD...) y hacen que dos
# nombres que se ven iguales sean distintos. Dos excepciones, porque sin ellas no se puede
# escribir texto real: U+200D (ZWJ, une emoji como 👩\u200d🎤) y U+200C (ZWNJ, lo usan el persa y
# otras escrituras).
_CATEGORIAS_PROHIBIDAS = frozenset({"Cc", "Cs", "Zl", "Zp", "Cf"})
_FORMATO_PERMITIDO = frozenset("\u200c\u200d")

# El track más largo que tiene sentido marcar, con mucho margen (un set grabado de 24 h). Un
# tiempo más grande es un pedido roto, no un dato: además, 1e306 × 1000 da infinito y
# `round(inf)` revienta con OverflowError (era un 500 de la API).
MAX_SECONDS = 24 * 3600


class InvalidCueMark(ValueError):
    """Lo que se quiso escribir no es una marca válida (tipo desconocido, tiempo fuera del
    track, loop al revés, número de hot cue ocupado...). Es un error del que llama."""


class CueMarkNotFound(LookupError):
    """No hay una marca con ese id en ese track, o el track no está en la biblioteca."""


@dataclass(frozen=True, slots=True)
class CueMark:
    """Una marca tal como está en la base."""

    id: int
    path_key: str
    kind: str
    num: int | None            # pad 0..7: siempre en un hot cue, opcional en un loop, nunca en memory
    start_ms: int
    end_ms: int | None         # solo loops
    name: str | None
    created_at: str            # ISO 8601 UTC
    updated_at: str

    @property
    def start_s(self) -> float:
        return self.start_ms / 1000

    @property
    def end_s(self) -> float | None:
        return None if self.end_ms is None else self.end_ms / 1000


def require_kind(kind: object) -> str:
    if not isinstance(kind, str) or kind not in KINDS:
        raise InvalidCueMark(f"el tipo de marca es uno de {', '.join(KINDS)}; recibí {kind!r}")
    return kind


def require_num(num: object) -> int:
    """El pad de un hot cue o de un hot loop: entero 0..7 (A..H). `True` no es un 1."""
    if isinstance(num, bool) or not isinstance(num, int) or not 0 <= num < HOT_CUES:
        raise InvalidCueMark(f"el número de pad (hot cue o hot loop) va de 0 a {HOT_CUES - 1}; "
                             f"recibí {num!r}")
    return num


def color_de_marca(kind: object, num: object = None) -> tuple[int, int, int] | None:
    """El color (r, g, b) 0-255 de una marca, el que lleva al exportar (`Red`/`Green`/`Blue`).

    Hot cue → el de su pad; loop → `COLOR_LOOP` con o sin pad; memory cue → None: no tiene
    color propio y se exporta sin color (la pantalla la dibuja en `COLOR_MEMORY_PANTALLA`).
    Una combinación que no es una marca (un hot cue sin pad, una memory con pad, un pad fuera
    de 0..7) es `InvalidCueMark`: devolver un color para eso sería pintar un dato roto.
    """
    kind = require_kind(kind)
    if kind == KIND_MEMORY:
        if num is not None:
            raise InvalidCueMark("una memory cue no lleva pad")
        return None
    if kind == KIND_LOOP:
        if num is not None:
            require_num(num)
        return COLOR_LOOP
    if num is None:
        raise InvalidCueMark("un hot cue siempre tiene pad (0 a 7)")
    return COLORES_HOT_CUE[require_num(num)]


def color_hex(rgb: tuple[int, int, int]) -> str:
    """(255, 77, 90) → "#ff4d5a": la forma en que lo escribe el CSS."""
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def color_hex_de_marca(kind: object, num: object = None) -> str | None:
    """`color_de_marca` en hex ("#ff4d5a"), o None para una memory cue (sin color propio)."""
    rgb = color_de_marca(kind, num)
    return None if rgb is None else color_hex(rgb)


def seconds_to_ms(value: object, campo: str) -> int:
    """Segundos (número finito, ≥ 0) → milisegundos enteros, redondeando al más cercano.

    `True` no es un número acá, ni un texto "12.5": la API recibe JSON y un tiempo que llega
    como texto es un pedido mal armado, no algo que convenga adivinar."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidCueMark(f"`{campo}` es un tiempo en segundos (número); recibí {value!r}")
    try:
        v = float(value)
    except OverflowError:
        raise InvalidCueMark(f"`{campo}` está fuera de rango") from None
    if not math.isfinite(v):
        raise InvalidCueMark(f"`{campo}` tiene que ser un número finito; recibí {value!r}")
    if v < 0:
        raise InvalidCueMark(f"`{campo}` no puede ser negativo; recibí {v:g}")
    if v > MAX_SECONDS:
        raise InvalidCueMark(f"`{campo}` está fuera de rango: {v:g} s es más que "
                             f"{MAX_SECONDS // 3600} horas")
    return int(round(v * 1000))


def clean_mark_name(name: object) -> str | None:
    """El nombre de una marca: texto de UNA línea o nada.

    Vacío o solo espacios = sin nombre (None). Los espacios de los bordes se sacan. Un salto
    de línea, un carácter de control o un control de dirección NO se reemplazan en silencio:
    se rechazan, porque el nombre viaja a Rekordbox tal cual y un dato que la base cambió sin
    decirlo es un dato que miente."""
    if name is None:
        return None
    if not isinstance(name, str):
        raise InvalidCueMark(f"el nombre de una marca es un texto; recibí {name!r}")
    for c in name:
        if (unicodedata.category(c) in _CATEGORIAS_PROHIBIDAS and c not in _FORMATO_PERMITIDO) \
                or c in _BIDI:
            raise InvalidCueMark(
                f"el nombre de una marca va en una sola línea y sin caracteres de control ni "
                f"invisibles (tiene U+{ord(c):04X})")
    name = name.strip()
    if not name:
        return None
    if len(name) > NAME_MAX:
        raise InvalidCueMark(f"el nombre de una marca tiene como mucho {NAME_MAX} caracteres; "
                             f"recibí {len(name)}")
    return name


def validate_times(kind: str, start_ms: int, end_ms: int | None, duration_s: float) -> None:
    """Que la marca caiga ADENTRO del track: entrada en [0, duración) y, en un loop, salida
    después de la entrada y como mucho al final. La duración es la que midió el análisis
    (la de la base), la misma que muestra la pantalla."""
    dur_ms = int(round(float(duration_s) * 1000))
    if start_ms >= dur_ms:
        raise InvalidCueMark(f"la marca cae en {start_ms / 1000:.3f} s y el track dura "
                             f"{dur_ms / 1000:.3f} s")
    if kind == KIND_LOOP:
        if end_ms is None:
            raise InvalidCueMark("un loop necesita la salida (`fin`)")
        if end_ms <= start_ms:
            raise InvalidCueMark(f"la salida del loop ({end_ms / 1000:.3f} s) tiene que ser "
                                 f"después de la entrada ({start_ms / 1000:.3f} s)")
        if end_ms > dur_ms:
            raise InvalidCueMark(f"la salida del loop ({end_ms / 1000:.3f} s) está después del "
                                 f"final del track ({dur_ms / 1000:.3f} s)")
    elif end_ms is not None:
        raise InvalidCueMark("solo un loop tiene salida (`fin`)")
