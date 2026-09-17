"""Capa 2a — los dos tipos de track que circulan por el motor.

Son DOS porque hay dos momentos distintos, y la diferencia no es cosmética:

- `TrackFeatures` es lo que sale del análisis de UN archivo: valores CRUDOS, que se
  pueden calcular mirando ese track y nada más.
- `Track` es lo que consume el motor de recomendación: el mismo track ya puesto en
  contexto de la biblioteca — embedding normalizado contra la escala de toda la
  colección y energía convertida a PERCENTIL (spec §7: "un -6 dB no significa lo
  mismo en una colección de ambient que en una de hard techno").

Quien convierte uno en otro es el store (`motor/store.py`), que es el único que tiene
la biblioteca entera para comparar.

`licencia` y `origen` (spec §5, "Cosas que no se tocan") son OBLIGATORIOS en `Track`
y lo impone el código, no un comentario: son campos sin default y además se valida que
no lleguen vacíos. El boceto viejo los declaraba `str | None = None` dos líneas debajo
de un comentario que decía que eran obligatorios — o sea que no lo eran.
"""
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def require_text(value: object, field: str) -> str:
    """Exige un texto con contenido. Devuelve el valor tal cual (no lo reescribe: recortar
    espacios por atrás sería modificar en silencio un dato del usuario).

    Es la ÚNICA compuerta de `license` / `source_url`: la usan `Track.__post_init__` y
    `Store.upsert`, para que no se pueda meter un track sin licencia ni por el constructor
    ni por la caché.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"`{field}` es obligatorio y no puede venir vacío (spec §5: licencia y origen "
            f"son obligatorios en cualquier modelo de track desde el primer día); recibí {value!r}"
        )
    return value


def require_finite_bpm(value: object) -> float:
    """Exige un BPM finito. Devuelve el valor tal cual (no lo convierte).

    Finito, y NO "> 0", a propósito (hallazgo H1, tarea 1.2):

    - `inf` y NaN no los produce el análisis y rompen la compuerta: con `inf` todas las
      distancias de `scoring` daban `inf/inf = NaN`, y un NaN no es `>= tolerancia`, así que
      el track entraba al set con score NaN. `scoring._bpm_valido` ya los trata como no
      medibles; esto los frena antes, en la construcción.
    - 0.0 SÍ lo produce el análisis real: `bpm.bpm_refinado` devuelve el tempo de
      `beat_track` tal cual cuando es <= 0, y sobre silencio (30 s o 3 s de ceros, o ruido de
      amplitud 1e-9) da exactamente 0.0 — medido. Rechazarlo haría que un solo track mudo
      en la carpeta rompiera `Store.load_library` para toda la biblioteca. La compuerta ya
      lo trata como "sin BPM": nunca mezcla, y el motivo muestra `?%` en vez de un número.
    - negativo no lo produce el análisis (beat_track no da tempos negativos) y la compuerta
      lo trata igual que 0; no se rechaza para no inventar una regla que nada necesita.

    La usan `Track.__post_init__` y `Store.upsert`, como `require_text`: un BPM infinito no
    entra ni por el constructor ni por la caché (si entrara a la caché, cargar la biblioteca
    entera fallaría al armar ese `Track`).
    """
    if not math.isfinite(float(value)):
        raise ValueError(
            f"`bpm` tiene que ser un número finito (0.0 = sin BPM medido), recibí {value!r}")
    return value


_FORMA_ACUERDO = re.compile(r"^(\d+)/(\d+)$")


def require_acuerdo_key(acuerdo: object, tramos: object) -> None:
    """Exige que la confianza de la key sea un par coherente, o que no esté. No devuelve
    nada: o pasa o levanta `ValueError`.

    Qué acepta: `(None, None)` —no se midió— o `("g/t", "8A|3B|9A")` con `g <= t` y
    EXACTAMENTE `t` votos (ninguno si `t` es 0). Ese invariante es el que produce
    `tono_consenso`, que devuelve `acuerdo=(ganados, len(votos))` junto a esos mismos votos.

    Por qué existe: un `"3/3"` con los votos vacíos diría "los tres tramos coincidieron" sin
    tener tramos, y un `"4/3"` o un `"abc"` no dicen nada — son datos inventados, y el
    proyecto no los quiere (spec §6). Es la misma compuerta que `require_text` para la
    licencia, en el mismo lugar: la frontera de ESCRITURA de la caché (`Store.upsert`).

    Dónde NO se usa, a propósito: al LEER. `Store._features` arma un `TrackFeatures` con lo
    que haya en la fila, y `Track` ni mira el formato. Si una base editada a mano tiene
    `"abc"`, la CLI tiene que poder mostrar ese track con `?` (`cli.key_dudosa` atrapa el
    `ValueError` de `acuerdo_unanime`); levantar acá haría que una fila corrupta tirara
    `load_library` para la biblioteca entera.
    """
    if acuerdo is None and tramos is None:
        return
    if acuerdo is None or tramos is None:
        raise ValueError(
            f"`key_acuerdo` y `key_tramos` van juntos: o los dos None (no se midió) o los dos "
            f"con valor; recibí acuerdo={acuerdo!r} y tramos={tramos!r}")
    if not isinstance(acuerdo, str) or not (m := _FORMA_ACUERDO.match(acuerdo.strip())):
        raise ValueError(
            f"`key_acuerdo` tiene la forma 'ganados/total' de `tono_consenso` (ej. '2/3'); "
            f"recibí {acuerdo!r}")
    ganados, total = int(m.group(1)), int(m.group(2))
    if ganados > total:
        raise ValueError(
            f"`key_acuerdo` {acuerdo!r}: no puede haber más tramos de acuerdo ({ganados}) que "
            f"tramos ({total})")
    if not isinstance(tramos, str):
        raise ValueError(f"`key_tramos` tiene que ser texto ('8A|3B|9A'); recibí {tramos!r}")
    votos = tramos.split("|") if tramos else []
    if len(votos) != total:
        raise ValueError(
            f"`key_acuerdo` {acuerdo!r} dice {total} tramos y `key_tramos` {tramos!r} trae "
            f"{len(votos)}: un acuerdo sin sus votos no se puede mostrar sin inventarlos")


def _as_vector(value: object, field: str) -> np.ndarray:
    """Coerción a `np.ndarray` 1-D, sin tocar el dtype.

    1-D es el contrato del embedding (spec §5: "cualquier backend devuelve un `np.ndarray`
    1-D y el resto del sistema no se entera"). El dtype se respeta porque el store guarda
    float32 y lee float32: si acá se forzara float64, el round-trip dejaría de ser exacto.
    """
    v = np.asarray(value)
    if v.ndim != 1:
        raise ValueError(f"`{field}` tiene que ser un vector 1-D, recibí shape {v.shape}")
    return v


@dataclass(slots=True, eq=False)
class TrackFeatures:
    """Features crudas que salen del análisis de audio, antes de normalizar.

    `energy_raw` es un valor absoluto (RMS): recién se convierte a percentil cuando el
    store tiene una biblioteca contra la cual compararlo.

    No lleva `license` ni `source_url` a propósito: esto no es un track, es el resultado
    de medir un archivo de audio. La identidad y la procedencia las pone el store en el
    `upsert`, que sí las exige.
    """

    bpm: float
    key: str  # notación Camelot, ej. "8A"
    energy_raw: float
    embedding: np.ndarray  # vector de timbre, SIN normalizar

    # Detalle de la energía, útil para debug y para reajustar pesos.
    #
    # Default `None` y no 0.0: `None` es "no se midió". Un 0.0 por defecto se guardaría en
    # la base como si fuera una medición (spec §6: un dato que miente es peor que uno
    # ausente) — "ratio percusivo 0" dice "no tiene nada de percusión", que en un track de
    # techno es exactamente lo contrario de la verdad.
    #
    # `percussive_ratio` hoy NUNCA se mide: necesita HPSS, y con HPSS el análisis costaba
    # ~20 s/track contra los ≤10 s del §4 (medido, ver `motor/tonalidad.py`). Queda el campo
    # para cuando haya una forma barata de medirlo, pero `motor.analisis` lo deja en None.
    rms: float | None = None
    onset_rate: float | None = None  # onsets por segundo
    percussive_ratio: float | None = None  # energía percusiva / total (HPSS) — no medido

    # CONFIANZA de la key: el acuerdo entre tramos de `tono_consenso`, no el campo
    # `confianza` de `tono()` (está medido que ese no predice nada, Pearson +0.02; el
    # acuerdo sí: 3/3 → 55% exacta, 2/3 → 36%, 1/3 → 26% — A/B 2026-09-14). La key la
    # sigue eligiendo `tono()`: esto es SOLO para poder mostrar `?` (spec §6).
    #
    # `key_acuerdo` es "ganados/total" en crudo ("3/3", "2/3", "0/0") y `key_tramos` lo que
    # votó cada tramo ("8A|3B|8A"). `None` en los dos = el consenso NO se corrió, que es
    # distinto de haberlo corrido sin evidencia ("0/0"): lo primero se arregla reanalizando,
    # lo segundo no se arregla con nada porque el track es muy corto. Un "3/3" por defecto
    # diría que la key es confiable sin que nadie la haya medido.
    key_acuerdo: str | None = None   # "g/t" de `tono_consenso`; None = no se midió
    key_tramos: str | None = None    # "8A|3B|8A"; "" si no hubo tramos; None = no se midió

    def __post_init__(self) -> None:
        self.embedding = _as_vector(self.embedding, "embedding")

    # `eq=False` + `__eq__` propio: el `__eq__` que genera dataclass compara los campos de
    # a tuplas y con un ndarray adentro revienta con "truth value of an array is ambiguous".
    # Devolver siempre False sería peor (mentir en silencio), así que se compara de verdad.
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TrackFeatures):
            return NotImplemented
        return (
            (self.bpm, self.key, self.energy_raw, self.rms, self.onset_rate,
             self.percussive_ratio, self.key_acuerdo, self.key_tramos)
            == (other.bpm, other.key, other.energy_raw, other.rms, other.onset_rate,
                other.percussive_ratio, other.key_acuerdo, other.key_tramos)
            and self.embedding.dtype == other.embedding.dtype
            and np.array_equal(self.embedding, other.embedding)
        )


@dataclass(slots=True, eq=False)
class Track:
    """Un track ya analizado y puesto en contexto de la biblioteca, listo para el motor.

    `energy` es el PERCENTIL 0..1 dentro de la biblioteca (0 = el más tranquilo de la
    colección, 1 = el más energético), no el RMS crudo: ese vive en `TrackFeatures`.
    `embedding` viene z-scoreado por dimensión y con norma 1 (`embeddings.normalize_matrix`),
    así el producto punto entre dos tracks ES la similitud coseno.

    `license` y `source_url` no tienen default: un `Track` sin licencia ni origen no se
    puede construir.
    """

    path: Path
    duration: float
    bpm: float
    key: str
    energy: float  # percentil 0..1 dentro de la biblioteca
    embedding: np.ndarray  # z-scored por dimensión + L2 por fila

    # Obligatorios (spec §5). Van antes que los opcionales porque un campo sin default no
    # puede ir después de uno con default: la obligatoriedad está en la firma, no en un
    # comentario.
    license: str
    source_url: str

    artist: str | None = None
    title: str | None = None

    # Confianza de la key: ver `TrackFeatures.key_acuerdo`. Viaja hasta acá porque es lo
    # que la CLI imprime al lado de la key (`list`, `similar`, `radio`), y sin el acuerdo
    # esas tablas presentarían una key dudosa como si fuera segura (spec §6).
    key_acuerdo: str | None = None

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.embedding = _as_vector(self.embedding, "embedding")
        self.license = require_text(self.license, "license")
        self.source_url = require_text(self.source_url, "source_url")
        self.bpm = require_finite_bpm(self.bpm)
        # 0..1, no 0..100: `energia.percentil` devuelve 0..100 y el store divide. Si acá
        # entra un 80.0 es que alguien salteó esa conversión, y el motor lo trataría como
        # un track 80 veces más energético que el máximo posible.
        if not 0.0 <= float(self.energy) <= 1.0:
            raise ValueError(
                f"`energy` es un percentil 0..1 dentro de la biblioteca, recibí {self.energy!r} "
                f"(¿escala 0..100 sin convertir?)"
            )

    @property
    def label(self) -> str:
        """Nombre legible para logs, tablas de la CLI y playlists."""
        if self.artist and self.title:
            return f"{self.artist} — {self.title}"
        return self.title or self.path.stem

    def __eq__(self, other: object) -> bool:  # mismo motivo que en TrackFeatures
        if not isinstance(other, Track):
            return NotImplemented
        return (
            (self.path, self.duration, self.bpm, self.key, self.energy, self.license,
             self.source_url, self.artist, self.title, self.key_acuerdo)
            == (other.path, other.duration, other.bpm, other.key, other.energy, other.license,
                other.source_url, other.artist, other.title, other.key_acuerdo)
            and self.embedding.dtype == other.embedding.dtype
            and np.array_equal(self.embedding, other.embedding)
        )
