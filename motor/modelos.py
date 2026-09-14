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
    rms: float = 0.0
    onset_rate: float = 0.0  # onsets por segundo
    percussive_ratio: float = 0.0  # energía percusiva / total (HPSS)

    def __post_init__(self) -> None:
        self.embedding = _as_vector(self.embedding, "embedding")

    # `eq=False` + `__eq__` propio: el `__eq__` que genera dataclass compara los campos de
    # a tuplas y con un ndarray adentro revienta con "truth value of an array is ambiguous".
    # Devolver siempre False sería peor (mentir en silencio), así que se compara de verdad.
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TrackFeatures):
            return NotImplemented
        return (
            (self.bpm, self.key, self.energy_raw, self.rms, self.onset_rate, self.percussive_ratio)
            == (other.bpm, other.key, other.energy_raw, other.rms, other.onset_rate,
                other.percussive_ratio)
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

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.embedding = _as_vector(self.embedding, "embedding")
        self.license = require_text(self.license, "license")
        self.source_url = require_text(self.source_url, "source_url")
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
             self.source_url, self.artist, self.title)
            == (other.path, other.duration, other.bpm, other.key, other.energy, other.license,
                other.source_url, other.artist, other.title)
            and self.embedding.dtype == other.embedding.dtype
            and np.array_equal(self.embedding, other.embedding)
        )
