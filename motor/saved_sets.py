"""Sets guardados y calificación de transiciones (tarea 16): el dataset de feedback.

Un set guardado es la FOTO de lo que se escuchó, no una receta para re-armarlo. Por cada
posición se guarda la ruta del track y lo que se mostró en ese momento —BPM con un decimal,
key en las dos notaciones, el `?` de confianza, la energía y el porqué que dio el motor— junto
con los números con los que el motor decidió (mezclabilidad, encaje, score). La razón es la
de §6: si mañana se re-escanea un track (cambia su BPM) o cambia el scoring, la calificación
"esta transición fue mala" tiene que seguir hablando de lo que sonó, no de un re-armado que
nadie escuchó. Por eso NADA de este módulo re-arma ni recalcula al leer: la foto se arma UNA
vez (`snapshot_steps`) y de ahí en más solo se lee.

La calificación usa la escala de la tarea 14 ("escuchar 3 radios y anotar cada transición"),
para que la misma herramienta sirva para las dos tareas: `ok` / `regular` / `mala`, con un
motivo en texto libre obligatorio para `mala` y opcional para las otras dos. Una transición
puede quedar sin calificar. La transición `n` es el paso de la posición `n` a la `n + 1`
(las dos contadas desde 1, como las imprime `radio`).

La persistencia vive en `motor/store.py` (tablas `saved_sets`, `saved_set_steps` y
`saved_set_ratings`, esquema v4); acá está qué se guarda, cómo se valida y cómo se muestra.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields

# Los tres niveles, en el orden en que se cuentan en los resúmenes.
RATING_OK = "ok"
RATING_REGULAR = "regular"
RATING_BAD = "mala"
RATINGS = (RATING_OK, RATING_REGULAR, RATING_BAD)

# Tope del nombre de un set y del motivo de una calificación. No es un límite del dato: es que
# un POST de un megabyte de "nombre" no es un nombre, y la base no tiene por qué guardarlo.
NAME_MAX = 200
REASON_MAX = 2000


class InvalidSavedSet(ValueError):
    """Lo que se quiso escribir no es válido (nivel desconocido, `mala` sin motivo, una
    transición que el set no tiene...). Es un error del que llama, no de la base."""


class SavedSetNotFound(LookupError):
    """No hay un set guardado con ese id (nunca existió o se borró)."""


@dataclass(frozen=True, slots=True)
class StepSnapshot:
    """Un paso del set tal como se mostró. Todo lo que dice `_shown` o es texto ES lo que
    se vio; los números crudos (`bpm`, `mixability`...) son los que usó el motor para
    decidir, y están para ajustar pesos con este feedback, no para volver a mostrarlos
    redondeados de otra forma.

    Un número que no se pudo medir (NaN, un BPM 0.0 del que no sale porcentaje) queda `None`:
    SQLite guarda un NaN como NULL de todas formas, y §6 prefiere un dato ausente."""

    position: int                  # 1 = la semilla
    path: str                      # la ruta tal cual la guardó el scan (`Track.path`)
    path_key: str                  # la clave del store (absoluta + normcase), para saber si sigue
    label: str
    artist: str | None
    title: str | None
    title_shown: str               # `title` o, sin tag, el nombre del archivo: lo que muestra la API
    duration: float
    is_track: bool                 # `modelos.es_track` en el momento de guardar
    license: str                   # obligatorios en cualquier modelo de track (spec §5)
    source_url: str
    bpm: float
    bpm_shown: str                 # "128.4": UN decimal, como la CLI y la API
    key: str                       # Camelot
    key_classic: str | None        # "Am"; None si la key no es un código Camelot
    key_acuerdo: str | None
    key_doubtful: bool             # el `?` que se mostró
    energy: float                  # percentil 0..1 dentro de la biblioteca DE ESE MOMENTO
    energy_pct: int                # el mismo percentil 0-100 que imprimió la terminal
    reason: str                    # `Transition.reason()` sin tocar
    is_seed: bool
    from_bpm: float | None
    bpm_delta_pct: float | None
    bpm_octave: str
    from_key: str | None
    key_relation: str
    key_compat: float | None
    mixability: float | None
    musical_fit: float | None
    score: float | None
    energy_goal: float | None


# Columnas de `saved_set_steps` en el orden de los campos: el store escribe y lee con ESTA
# lista, así que un campo nuevo que no llegue a la tabla falla en el INSERT y no se pierde.
STEP_FIELDS = tuple(f.name for f in fields(StepSnapshot))


def _finite(value) -> float | None:
    if value is None:
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def snapshot_steps(rset) -> list[StepSnapshot]:
    """La foto de un `RadioSet` recién armado, paso por paso.

    Cada valor que se muestra sale de la MISMA función que lo muestra en la terminal y en la
    API (`cli.key_dudosa`, `cli.percentil_energia`, `tonalidad.camelot_a_clasica`,
    `Transition.reason`): si se copiaran las reglas acá, la foto podría decir otra cosa que la
    pantalla que se calificó.
    """
    from motor.cli import key_dudosa, percentil_energia
    from motor.modelos import es_track
    from motor.store import Store
    from motor.tonalidad import camelot_a_clasica

    fotos = []
    for n, paso in enumerate(rset.steps, 1):
        t, tr = paso.track, paso.transition
        fotos.append(StepSnapshot(
            position=n, path=str(t.path), path_key=Store._key(t.path), label=t.label,
            artist=t.artist, title=t.title, title_shown=t.title or t.path.stem,
            duration=float(t.duration), is_track=es_track(t.duration),
            license=t.license, source_url=t.source_url,
            bpm=float(t.bpm), bpm_shown=f"{t.bpm:.1f}", key=t.key,
            key_classic=camelot_a_clasica(t.key) or None, key_acuerdo=t.key_acuerdo,
            key_doubtful=key_dudosa(t.key_acuerdo),
            energy=float(t.energy), energy_pct=percentil_energia(t.energy),
            reason=tr.reason(), is_seed=tr.is_seed,
            from_bpm=_finite(tr.from_bpm), bpm_delta_pct=_finite(tr.bpm_delta_pct),
            bpm_octave=tr.bpm_octave, from_key=tr.from_key, key_relation=tr.key_relation,
            key_compat=_finite(tr.key_compat), mixability=_finite(tr.mixability),
            musical_fit=_finite(tr.musical_fit), score=_finite(tr.total),
            energy_goal=_finite(tr.energy_goal),
        ))
    return fotos


def shown_header(rset, config) -> dict:
    """Lo que se muestra del set y no es de ningún paso: el largo pedido, la curva, la
    semilla del azar y el randomness (el encabezado), y el corte con su detalle y los
    fragmentos ignorados (el pie). Entra en la huella con la foto de los pasos."""
    return {"requested": config.length, "curve": config.curve, "seed": config.seed,
            "randomness": config.randomness, "stop": rset.stop,
            "stop_detail": rset.stop_detail, "fragments": rset.fragments}


def fingerprint(steps: Sequence[StepSnapshot], header: dict) -> str:
    """Huella de lo que se MOSTRÓ: sha256 de la foto de los pasos Y de la cabecera
    (`shown_header`), en JSON canónico. Cubre TODOS los campos de `StepSnapshot`, no una
    selección: un campo que quedara afuera sería uno que puede cambiar sin que nadie se
    entere (hay un test que cambia cada uno por separado).

    La API la devuelve con cada set (`/api/radio/set`) y el guardado la exige: si al
    re-armar para guardar sale la misma lista de tracks pero con otro dato (un re-escaneo
    cambió un BPM entre que se mostró y se guardó), los ids coinciden y la huella no. Sin
    esto se guardaría como "lo que escuchaste" un BPM que nunca estuvo en pantalla.
    """
    texto = json.dumps({"steps": [asdict(s) for s in steps], "header": header},
                       sort_keys=True, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:32]


def set_fingerprint(rset, config) -> str:
    """La huella de un `RadioSet` recién armado con su config: lo que devuelve la API."""
    return fingerprint(snapshot_steps(rset), shown_header(rset, config))


# Caracteres de control (saltos de línea, tab, NUL...). En un nombre o un motivo parten las
# tablas de la terminal (`sets listar`, `sets ver`) y los renglones del CSV: se cambian por
# un espacio. "\r\n" es UN salto de línea, así que queda UN espacio.
_CONTROL = re.compile(r"\r\n|[\x00-\x1f\x7f]")


def _one_line(texto: str) -> str:
    return _CONTROL.sub(" ", texto)


def clean_name(name: object) -> str | None:
    """El nombre de un set: texto o nada. Vacío o solo espacios = sin nombre (None), no un
    nombre "   ". Los caracteres de control pasan a espacio (`_CONTROL`); fuera de eso no
    se recorta ni se reescribe: es un dato del usuario."""
    if name is None:
        return None
    if not isinstance(name, str):
        raise InvalidSavedSet(f"el nombre de un set es un texto, recibí {name!r}")
    name = _one_line(name)
    if not name.strip():
        return None
    if len(name) > NAME_MAX:
        raise InvalidSavedSet(f"el nombre de un set tiene como mucho {NAME_MAX} caracteres, "
                              f"recibí {len(name)}")
    return name


def require_rating(rating: object, reason: object) -> str | None:
    """Valida una calificación y devuelve el motivo que se guarda (None = sin motivo).

    - `rating` es uno de `RATINGS`, exacto: "Mala" o "bien" no se interpretan.
    - `mala` exige motivo: una transición mala sin por qué no sirve para ajustar nada, que es
      para lo que existe este dataset (y es lo que pide la planilla de la tarea 14).
    - En `ok` / `regular` el motivo es opcional; vacío o solo espacios cuenta como ausente.
    - Los caracteres de control pasan a espacio, como en `clean_name`.
    """
    if rating not in RATINGS:
        raise InvalidSavedSet(f"la calificación es una de {', '.join(RATINGS)}; recibí {rating!r}")
    if reason is not None and not isinstance(reason, str):
        raise InvalidSavedSet(f"el motivo es un texto, recibí {reason!r}")
    if reason is not None:
        reason = _one_line(reason)
    texto = reason if reason is not None and reason.strip() else None
    if rating == RATING_BAD and texto is None:
        raise InvalidSavedSet("una transición `mala` necesita el motivo (qué sonó mal): sin eso "
                              "la marca no sirve para ajustar el motor")
    if texto is not None and len(texto) > REASON_MAX:
        raise InvalidSavedSet(f"el motivo tiene como mucho {REASON_MAX} caracteres, "
                              f"recibí {len(texto)}")
    return texto


@dataclass(frozen=True, slots=True)
class Rating:
    transition: int                # n = de la posición n a la n + 1
    rating: str
    reason: str | None
    rated_at: str                  # ISO 8601 UTC


@dataclass(frozen=True, slots=True)
class SavedStep:
    """Un paso leído de la base: la foto y si el archivo sigue en la biblioteca HOY."""

    snapshot: StepSnapshot
    in_library: bool


@dataclass(frozen=True, slots=True)
class SavedSet:
    id: int
    created_at: str                # ISO 8601 UTC
    name: str | None
    config: dict                   # el `RadioConfig` usado, campo por campo
    requested: int                 # largo pedido
    stop: str | None               # `RadioSet.stop` de ese armado
    stop_detail: str
    fragments: int
    steps: tuple[SavedStep, ...]
    ratings: tuple[Rating, ...]    # ordenadas por transición

    @property
    def transitions(self) -> int:
        return max(len(self.steps) - 1, 0)

    def rating_of(self, n: int) -> Rating | None:
        return next((r for r in self.ratings if r.transition == n), None)

    def summary(self) -> dict[str, int]:
        return summarize(self.transitions, [r.rating for r in self.ratings])

    @property
    def missing(self) -> int:
        """Cuántos tracks del set ya no están en la biblioteca."""
        return sum(1 for s in self.steps if not s.in_library)


@dataclass(frozen=True, slots=True)
class SavedSetInfo:
    """Una fila del listado: lo necesario para elegir un set sin leer su foto entera."""

    id: int
    created_at: str
    name: str | None
    seed_label: str
    tracks: int
    requested: int
    curve: str | None
    summary: dict[str, int]
    missing: int


def summarize(transitions: int, ratings: Sequence[str]) -> dict[str, int]:
    """`{ok, regular, mala, sin_calificar}`. `sin_calificar` sale de la resta, así que las
    cuatro suman siempre las transiciones del set."""
    cuenta = {r: sum(1 for x in ratings if x == r) for r in RATINGS}
    return {**cuenta, "sin_calificar": transitions - sum(cuenta.values())}


# --- presentación (la comparten `sets ver` y `radio`) ---------------------------------------

def snapshot_row(s: StepSnapshot) -> str:
    """El renglón de un track del set guardado, con EXACTAMENTE las columnas de `radio`
    (`cli._fila`): BPM con un decimal, Camelot y clásica, el `?`, la energía y el label. Sale
    de la foto, no del track de hoy. Un test lo compara byte a byte contra `cli._fila`."""
    from motor.cli import MARCA_DUDOSA

    clasica = s.key_classic or "?"
    marca = MARCA_DUDOSA if s.key_doubtful else " "
    return (f"{s.bpm_shown:>6} BPM  {s.key:>3} {clasica:<3}{marca} "
            f"energía {s.energy_pct:3d}  {s.label}")


def rating_text(r: Rating | None) -> str:
    if r is None:
        return "sin calificar"
    return r.rating if r.reason is None else f"{r.rating} — {r.reason}"


# --- CSV para la planilla de la tarea 14 ----------------------------------------------------

CSV_COLUMNS = (
    "set", "nombre_set", "guardado", "transicion", "desde_pos", "hasta_pos",
    "desde", "hasta", "bpm_desde", "bpm_hasta", "key_desde", "key_hasta",
    "acuerdo_key_desde", "acuerdo_key_hasta", "porque", "mezclabilidad", "encaje", "score",
    "calificacion", "motivo", "calificada", "desde_en_biblioteca", "hasta_en_biblioteca",
)

# Una celda que empieza con estos caracteres Excel la toma como FÓRMULA al abrir el CSV: el
# porqué del motor ("+1.8% BPM | ...") daría #¿NOMBRE?, y un título de tag que empiece con
# "=" se ejecutaría (inyección de fórmulas). Se les antepone un apóstrofo, que es la marca
# de "esto es texto" de las planillas.
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _celda(valor) -> str:
    if valor is None:
        return ""
    texto = str(valor)
    return "'" + texto if texto.startswith(_FORMULA) else texto


def _bpm_celda(s: StepSnapshot) -> str:
    # Con la unidad pegada Excel no lo convierte en número: "128.0" en una planilla en inglés
    # se mostraría 128 (redondear es mentir, §6) y en una en castellano ni siquiera es número.
    return f"{s.bpm_shown} BPM"


def _acuerdo_celda(s: StepSnapshot) -> str:
    # "3/3" Excel lo abre como fecha (3 de marzo). "3 de 3" no se deforma; sin acuerdo medido
    # queda vacío, y el `?` va pegado a la key, como en la terminal.
    if not s.key_acuerdo:
        return "no medido"
    if "/" not in s.key_acuerdo:
        return s.key_acuerdo
    g, t = s.key_acuerdo.split("/", 1)
    return f"{g} de {t}"


def _num_celda(valor: float | None) -> str:
    return "" if valor is None else repr(float(valor))


CSV_SEPARATORS = (";", ",")


def ratings_csv(sets: Sequence[SavedSet], separator: str = ";") -> str:
    """Las transiciones de los sets, una por fila, con su calificación (o vacía).

    Formato elegido para que Excel no deforme nada (ver `_celda`, `_bpm_celda`,
    `_acuerdo_celda`): decimales con punto, fechas ISO 8601 con `T` y `Z` (Excel no las
    convierte), BPM con la unidad pegada y el acuerdo como "3 de 3". Lo escribe `sets
    exportar` en UTF-8 con BOM para que Excel lea los acentos.

    Separador `;` por defecto (decisión del dueño): es el separador de listas de Excel con
    configuración regional argentina, así el archivo se abre bien con doble clic. `,` para
    Excel en inglés o para leerlo con otras herramientas.
    """
    if separator not in CSV_SEPARATORS:
        raise ValueError(f"el separador es uno de {CSV_SEPARATORS}, recibí {separator!r}")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=separator, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for s in sets:
        for n in range(1, s.transitions + 1):
            a, b = s.steps[n - 1], s.steps[n]
            fa, fb = a.snapshot, b.snapshot
            r = s.rating_of(n)
            w.writerow([_celda(x) for x in (
                s.id, s.name, s.created_at, f"{n} → {n + 1}", n, n + 1,
                fa.label, fb.label, _bpm_celda(fa), _bpm_celda(fb),
                fa.key + ("?" if fa.key_doubtful else ""),
                fb.key + ("?" if fb.key_doubtful else ""),
                _acuerdo_celda(fa), _acuerdo_celda(fb),
                fb.reason, _num_celda(fb.mixability), _num_celda(fb.musical_fit),
                _num_celda(fb.score),
                r.rating if r else "", r.reason if r else "", r.rated_at if r else "",
                "sí" if a.in_library else "no", "sí" if b.in_library else "no",
            )])
    return buf.getvalue()


def config_json(config) -> str:
    """El `RadioConfig` usado, entero y en JSON canónico: con esto se sabe cómo se armó el
    set (curva, pesos, semilla del azar...). Se guarda como dict y no se reconstruye un
    `RadioConfig` al leer: si mañana la clase cambia sus validaciones, el set viejo se tiene
    que poder leer igual."""
    return json.dumps(asdict(config), sort_keys=True, allow_nan=False)


__all__ = [
    "CSV_COLUMNS", "RATINGS", "RATING_BAD", "RATING_OK", "RATING_REGULAR", "STEP_FIELDS",
    "CSV_SEPARATORS", "InvalidSavedSet", "Rating", "SavedSet", "SavedSetInfo",
    "SavedSetNotFound", "SavedStep", "StepSnapshot", "clean_name", "config_json",
    "fingerprint", "rating_text", "ratings_csv", "require_rating", "set_fingerprint",
    "shown_header", "snapshot_row", "snapshot_steps", "summarize",
]
