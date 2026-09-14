"""Energía percibida de un track, su percentil dentro de la biblioteca y la curva del set.

La spec §7 es clara: la energía se usa como **percentil de la biblioteca**, no como
valor absoluto (un -6 dB no significa lo mismo en una colección de ambient que en una
de hard techno). Baja de prioridad con rotación de 1:30, pero entra en la curva del set.

Tres capas, de track a set:

1. `energia_rms` — cuánta energía tiene ESTE archivo (valor absoluto).
2. `percentil` — dónde cae ese valor dentro de la biblioteca (0..100).
3. `energy_target` — la forma que tiene que dibujar la energía a lo largo del set, y las
   dos métricas con las que §4 mide si la dibujó: `energy_curve_deviation` (principal) y
   `ascending_spearman` (secundaria, sobre el tramo donde la curva sube).
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
    después con `energy_curve_deviation` / `ascending_spearman`.

    Tres formas:

    - ``"peak"``   — sube de 0.35 hasta 0.95 en el 75% del set (`PEAK_AT`) y afloja
      hasta 0.60 en el tramo final. Es el arco clásico: presentar, subir, pegar, bajar.
    - ``"warmup"`` — sube sostenido de 0.30 a 0.80, sin clímax. Para tocar antes que
      otro DJ: el set entrega la pista más arriba de lo que la recibió, y nada más.
    - ``"flat"``   — 0.5 constante. Sirve de control: con esta curva el Spearman de §4
      no está definido (no hay tramo que suba); solo el desvío dice algo.

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

    Fue la métrica de §4 hasta el 2026-09-14 y ya NO es la del contrato: mide monotonía, y
    la curva "peak" baja en su último cuarto, así que un set que la sigue perfecto da 0.72
    y no 1.0, y dos sets que la siguen igual de bien pueden dar 0.70 y 0.22. El contrato
    ahora es `energy_curve_deviation` (principal) y `ascending_spearman` (secundaria).
    Queda como función válida: responde "¿la energía sube a lo largo de TODO el set?".

    Se le pasan las energías en el orden en que suenan: `[t.energy for t in set]`. No
    recibe `Track` para no atar este módulo a `motor/modelos.py`; lo único que necesita
    es la secuencia de números.
    """
    e = np.asarray(list(energies), dtype=np.float64)
    return spearman(np.arange(e.size, dtype=np.float64), e)


def _curve_length(n: int, length: int | None) -> int:
    """El largo contra el que se evalúa la curva: el del set PEDIDO si se conoce.

    `build_set` calcula el objetivo de cada posición con `config.length`. Si el set quedó
    corto (se acabaron los candidatos), medirlo contra una curva estirada a su largo real
    lo compararía con objetivos que nunca se pidieron. Por eso se acepta `length`; sin él,
    se asume que el set salió completo.
    """
    if length is None:
        return max(n, 1)
    if length < n:
        raise ValueError(f"el set pedido ({length}) no puede ser más corto que el sonado ({n})")
    return length


def ascending_positions(n: int, curve: str = "peak", length: int | None = None) -> list[int]:
    """Las posiciones (de las `n` que sonaron) donde la curva pedida SUBE.

    - ``"peak"``   — las de t ≤ `PEAK_AT` (el mismo corte que usa `energy_target`, así el
      clímax cuenta como parte de la subida).
    - ``"warmup"`` — todas.
    - ``"flat"``   — ninguna: la curva no sube, no hay tramo ascendente.

    Es lo que entra al `ascending_spearman`, y su cantidad es la que decide si ese Spearman
    es orientativo (`motor/cli.py`).
    """
    if curve not in CURVES:
        raise ValueError(f"curva desconocida {curve!r}; las soportadas son {CURVES}")
    total = _curve_length(n, length)
    if curve == "flat":
        return []
    if curve == "warmup":
        return list(range(n))
    return [i for i in range(n) if i / max(total - 1, 1) <= PEAK_AT]


def energy_curve_deviation(energies: Sequence[float], curve: str = "peak",
                           length: int | None = None) -> float:
    """Desvío medio de la curva: promedio de |energía_i − energy_target(i, length, curve)|.

    Métrica PRINCIPAL de la curva en §4 (umbral a calibrar, tarea 14). A diferencia del
    Spearman, mide qué tan cerca pasó el set de la curva pedida, no si subió: 0.0 es
    seguirla exacto, y la bajada de "peak" no la penaliza.

    `length` es el largo del set pedido (ver `_curve_length`); por defecto, el sonado.
    `nan` con un set vacío: no hay nada que medir, y un 0.0 se leería como "perfecto".
    """
    e = np.asarray(list(energies), dtype=np.float64)
    if e.size == 0:
        return float("nan")
    total = _curve_length(e.size, length)
    target = np.array([energy_target(i, total, curve) for i in range(e.size)])
    return float(np.mean(np.abs(e - target)))


def ascending_spearman(energies: Sequence[float], curve: str = "peak",
                       length: int | None = None) -> float:
    """Spearman posición vs energía SOLO sobre el tramo donde la curva sube.

    Métrica SECUNDARIA de §4 (≥ 0.5). Un set que sigue "peak" perfecto da 1.0 acá (el
    Spearman sobre el set entero le daba 0.72 por la bajada final).

    `nan` cuando no está definido — nunca 0.0: con "flat" (no hay subida), con menos de
    dos puntos en el tramo ascendente, o con energía constante en ese tramo.
    """
    e = np.asarray(list(energies), dtype=np.float64)
    pos = ascending_positions(e.size, curve, length)
    return spearman(np.asarray(pos, dtype=np.float64), e[pos])
