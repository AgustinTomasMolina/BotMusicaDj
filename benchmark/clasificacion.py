"""Clasificación de los desacuerdos de BPM entre el motor y la referencia.

Un error de BPM grande casi nunca es "el motor midió mal el pulso": casi siempre es que
motor y referencia están en NIVELES MÉTRICOS distintos (uno cuenta el doble, o en
tresillos). Distinguirlos importa porque la acción es distinta:

  - octava      → nadie se equivocó, es convención (half-time). El umbral §4 lo tolera
                  a propósito, así que estos casos PASAN y quedan invisibles en el p95.
                  Son los que hay que sacar a la luz.
  - tresillo    → ratio 3/2 o 2/3. Es el fallo que ataca `motor.bpm._resolver_metrica`.
  - otro-múltiplo-racional → 4/3, 3/4, 3, 1/3… nivel métrico raro; mirar el track.
  - error-genuino → la ratio no es una fracción simple: el motor (o la referencia)
                  realmente erró el pulso. Este es el único que se arregla con código.

La tolerancia de esta clasificación (`tol_rel`) es SOLO para etiquetar. El veredicto
pasa/no-pasa sale del umbral de la spec §4 y no se toca acá.
"""
from dataclasses import dataclass

OCTAVA = "octava"
TRESILLO = "tresillo"
OTRO_MULTIPLO = "otro-multiplo-racional"
GENUINO = "error-genuino"
EXACTO = "exacto"
SIN_REF = "sin-referencia"

# (ratio est/ref, clase). El orden no importa: se elige el candidato más cercano.
_RATIOS: list[tuple[float, str]] = [
    (1.0, EXACTO),
    (2.0, OCTAVA), (1 / 2, OCTAVA), (4.0, OCTAVA), (1 / 4, OCTAVA),
    (3 / 2, TRESILLO), (2 / 3, TRESILLO),
    (3.0, OTRO_MULTIPLO), (1 / 3, OTRO_MULTIPLO),
    (4 / 3, OTRO_MULTIPLO), (3 / 4, OTRO_MULTIPLO),
    (5 / 4, OTRO_MULTIPLO), (4 / 5, OTRO_MULTIPLO),
    (5 / 3, OTRO_MULTIPLO), (3 / 5, OTRO_MULTIPLO),
    (6.0, OTRO_MULTIPLO), (1 / 6, OTRO_MULTIPLO),
]


@dataclass(frozen=True)
class Clasificacion:
    clase: str
    ratio: float | None      # ratio nominal elegida (2.0, 1.5, …), None si no hubo
    desvio: float | None     # BPM de distancia al múltiplo exacto


def clasificar(est: float, ref: float, umbral: float = 1.0,
               tol_rel: float = 0.02) -> Clasificacion:
    """Etiqueta el desacuerdo entre `est` (motor) y `ref` (referencia).

    `umbral` es el de la spec §4 (1.0 BPM): por debajo de eso es EXACTO.
    `tol_rel` es cuánto puede desviarse del múltiplo teórico para seguir contando como
    ese múltiplo (2 % relativo, con piso en `umbral`).
    """
    if ref is None or ref <= 0:
        return Clasificacion(SIN_REF, None, None)
    if abs(est - ref) <= umbral:
        return Clasificacion(EXACTO, 1.0, abs(est - ref))

    mejor: tuple[float, float, str] | None = None   # (distancia, ratio, clase)
    for ratio, clase in _RATIOS:
        objetivo = ref * ratio
        tol = max(umbral, tol_rel * objetivo)
        d = abs(est - objetivo)
        if d <= tol and (mejor is None or d < mejor[0]):
            mejor = (d, ratio, clase)

    if mejor is None:
        return Clasificacion(GENUINO, None, None)
    d, ratio, clase = mejor
    # Ratio ~1 pero fuera del umbral: el pulso es el mismo y aun así erró → genuino.
    if clase == EXACTO:
        return Clasificacion(GENUINO, 1.0, d)
    return Clasificacion(clase, ratio, d)


def resumen(clases: list[str]) -> dict[str, int]:
    """Cuenta por clase, en un orden estable para imprimir."""
    orden = [EXACTO, OCTAVA, TRESILLO, OTRO_MULTIPLO, GENUINO, SIN_REF]
    conteo = {c: 0 for c in orden}
    for c in clases:
        conteo[c] = conteo.get(c, 0) + 1
    return {c: n for c, n in conteo.items() if n or c in (EXACTO, GENUINO)}
