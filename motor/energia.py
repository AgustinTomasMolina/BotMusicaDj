"""Energía percibida de un track, su percentil dentro de la biblioteca y la curva del set.

La spec §7 es clara: la energía se usa como **percentil de la biblioteca**, no como
valor absoluto (un -6 dB no significa lo mismo en una colección de ambient que en una
de hard techno). Baja de prioridad con rotación de 1:30, pero entra en la curva del set.

Tres capas, de track a set:

1. `energia_rms` — cuánta energía tiene ESTE archivo (valor absoluto).
2. `percentil` — dónde cae ese valor dentro de la biblioteca (0..100).
3. `energy_target` / `spearman` — la forma que tiene que dibujar la energía a lo largo
   del set, y la métrica con la que §4 mide si la dibujó.
"""
from collections.abc import Sequence

import numpy as np

# Formas de curva soportadas por `energy_target`.
CURVES = ("peak", "warmup", "flat")

# Dónde cae el clímax en la curva "peak": a los tres cuartos del set, para dejar un
# cuarto de bajada. Un pico al final no es un set, es un corte.
PEAK_AT = 0.75


def energia_rms(y: np.ndarray) -> float:
    """Energía percibida ≈ RMS de la señal (0..1 para audio normalizado)."""
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(y ** 2)))


def percentil(valor: float, valores) -> float:
    """Percentil (0..100) de `valor` dentro de `valores` (la biblioteca).
    100 = el más energético de la biblioteca; 0 = el más tranquilo."""
    a = np.asarray(list(valores), dtype=np.float64)
    if a.size == 0:
        return 0.0
    return float((a < valor).mean() * 100.0)


# ---------------------------------------------------------------------------
# Curva de energía del set
# ---------------------------------------------------------------------------

def energy_target(position: int, length: int, curve: str = "peak") -> float:
    """Energía buscada (0..1) en la posición `position` de un set de `length` tracks.

    Es el objetivo, no el resultado: quien arma el set (`build_set`) elige, entre los
    candidatos mezclables, el que más se acerca a este número. El resultado se mide
    después con `spearman` / `energy_curve_correlation`.

    Tres formas:

    - ``"peak"``   — sube de 0.35 hasta 0.95 en el 75% del set (`PEAK_AT`) y afloja
      hasta 0.60 en el tramo final. Es el arco clásico: presentar, subir, pegar, bajar.
    - ``"warmup"`` — sube sostenido de 0.30 a 0.80, sin clímax. Para tocar antes que
      otro DJ: el set entrega la pista más arriba de lo que la recibió, y nada más.
    - ``"flat"``   — 0.5 constante. Sirve de control: con esta curva la correlación de
      §4 no significa nada, porque no hay nada que correlacionar.

    Todas devuelven un valor dentro de 0..1 **siempre**, incluso con `position` fuera
    de rango: `position` se recorta a [0, length-1] antes de normalizar. Sin ese recorte
    la rama de bajada de "peak" se va a negativo (con length=5 y position=10 daba -1.5),
    y un objetivo negativo hace que el track más tranquilo de la biblioteca gane siempre.

    Con `length=1` el set tiene una sola posición y devuelve el valor de arranque de la
    curva: no hay progresión que dibujar.
    """
    if curve not in CURVES:
        raise ValueError(f"curva desconocida {curve!r}; las soportadas son {CURVES}")
    if length < 1:
        raise ValueError(f"un set tiene al menos un track, recibí length={length!r}")

    # Recorte explícito antes de dividir. `max(length - 1, 1)` evita la división por cero
    # con length=1, y el clip evita que t se salga de [0, 1].
    position = min(max(int(position), 0), length - 1)
    t = position / max(length - 1, 1)

    if curve == "flat":
        return 0.5
    if curve == "warmup":
        return 0.3 + 0.5 * t
    if t <= PEAK_AT:
        return 0.35 + 0.6 * (t / PEAK_AT)
    return 0.95 - 0.35 * ((t - PEAK_AT) / (1 - PEAK_AT))


def _ranks(a: np.ndarray) -> np.ndarray:
    """Rangos 1..n, promediando los empates (mismo criterio que `scipy.stats.rankdata`).

    Los empates importan acá: la energía es un percentil de la biblioteca y dos tracks
    del mismo percentil son un empate real, no un desorden. Asignarles rangos distintos
    según el orden de llegada haría que la métrica dependiera del orden de entrada.
    """
    order = np.argsort(a, kind="stable")
    ranks = np.empty(a.size, dtype=np.float64)
    ranks[order] = np.arange(1, a.size + 1, dtype=np.float64)

    ordenados = a[order]
    i = 0
    while i < a.size:
        j = i
        while j + 1 < a.size and ordenados[j + 1] == ordenados[i]:
            j += 1
        if j > i:
            # Promedio de los rangos i+1 .. j+1.
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """Correlación de rangos de Spearman entre `x` e `y`: Pearson sobre los rangos.

    Devuelve -1..1, o `nan` cuando la correlación no está definida: menos de dos puntos,
    o una de las dos series constante (varianza cero de los rangos). `nan` y no 0.0 a
    propósito — spec §6: un dato que miente es peor que un dato ausente, y un 0.0 acá se
    leería como "no hay curva" cuando lo que pasa es que no se puede saber.

    numpy puro, sin scipy: scipy entra hoy al entorno como dependencia transitiva de
    librosa, pero no es dependencia directa del motor (`pyproject.toml` declara librosa y
    numpy y nada más) y esto son diez líneas de rangos.
    """
    a = np.asarray(list(x), dtype=np.float64)
    b = np.asarray(list(y), dtype=np.float64)
    if a.size != b.size:
        raise ValueError(f"las dos series tienen que medir lo mismo, recibí {a.size} y {b.size}")
    if a.size < 2:
        return float("nan")

    ra, rb = _ranks(a), _ranks(b)
    sa, sb = ra.std(), rb.std()
    if sa == 0.0 or sb == 0.0:
        return float("nan")
    r = float(((ra - ra.mean()) * (rb - rb.mean())).mean() / (sa * sb))
    # El redondeo puede escupir 1.0000000000000002 en un set perfectamente ordenado, y una
    # correlación mayor que 1 no existe. Recortar al rango real en vez de publicar el ruido.
    return min(max(r, -1.0), 1.0)


def energy_curve_correlation(energies: Sequence[float]) -> float:
    """Spearman entre la POSICIÓN en el set (0, 1, 2, ...) y la energía de cada track.

    Es la métrica del umbral de §4: **≥ 0.5** entre posición y energía. Mide que la
    energía suba a lo largo del set, no que siga exactamente la curva pedida — por eso
    tolera el tramo de bajada de "peak" (el último cuarto tira la correlación abajo, y
    aun así un arco bien armado queda holgadamente por encima de 0.5).

    Se le pasan las energías en el orden en que suenan: `[t.energy for t in set]`. No
    recibe `Track` para no atar este módulo a `motor/modelos.py`; lo único que necesita
    es la secuencia de números.
    """
    e = np.asarray(list(energies), dtype=np.float64)
    return spearman(np.arange(e.size, dtype=np.float64), e)
