"""Puente entre dos tracks: el camino más barato de A a B en el grafo de mezclabilidad.

"Sé cómo quiero terminar el set y no sé cómo llegar": dado un track de salida A y uno de
llegada B, `build_bridge` devuelve los temas que van en el medio, con el POR QUÉ de cada
salto en el mismo formato que la radio (`+1.8% BPM | 8A → 9A (vecino)`).

El grafo: los nodos son los tracks de la biblioteca y hay arista de `u` a `w` si y solo si
`scoring.mezclabilidad(u, w) > 0` — que es exactamente la compuerta de ±8% de BPM de §4 (la
key nunca anula la mezcla: `compat_camelot` tiene piso 0.2). No hay una segunda definición
de "mezcla": las aristas salen de `scoring.mezclabilidad_vector`, la misma función que usa
`radio.build_set`, y el motivo que se imprime sale de `radio._transition`, la misma que arma
el motivo de la radio.

---

Por qué es un módulo aparte y no una función más de `radio.py`: la radio es un greedy que
elige posición por posición con el encaje musical (timbre, energía, MMR, `artist_gap`); el
puente es una búsqueda de camino mínimo que no mira nada de eso. Comparten las piezas —la
compuerta, el motivo, el filtro de fragmentos, la deduplicación por ruta— y esas se importan
de `radio`/`scoring`, no se copian. Meter el puente adentro de `radio.py` (que ya tiene 800
renglones) mezclaría dos algoritmos que se razonan distinto.

Cinco decisiones, y por qué:

1. **El costo de una arista es `-log(mezclabilidad)`, no `1 - mezclabilidad`.** El total de
   un camino es entonces `-log(Π mezclabilidad)`: se minimiza el PRODUCTO, y un salto flojo
   pesa mucho más que varios medianos. Con `1 - m` pasa algo concreto y malo: como
   `bpm_score` cae LINEAL con la distancia (`1 - d/tol`), sumar `1 - m` entre tracks de la
   misma key es sumar `d/tol`, o sea (casi) el cambio TOTAL de tempo, lo repartas como lo
   repartas. Y el "casi" juega en contra: `d = 1 - lento/rápido` crece menos que lineal
   con el salto, así que un salto grande suma MENOS que varios chicos que cubren lo mismo.
   `1 - m` prefiere quedarse quieto y pegar un solo salto al borde de la compuerta.
   `-log` es convexa en `d` y prefiere la rampa pareja. El caso está en
   `test_el_costo_prefiere_la_rampa_pareja_al_salto_al_borde`: 128 → 137.6 BPM con tres
   intermedios (misma key), donde `Σ(1-m)` elige 128, 128, 128 y después +7.0%
   (0.872 contra 0.900 de la rampa), y `-log` elige la rampa 130.3, 132.7, 135.1
   (≈ +1.8% por salto; 1.014 contra 2.056 del salto).

2. **"3 o 4 temas" son los INTERMEDIOS, sin contar A ni B.** A y B los elige el DJ; lo que
   el puente aporta es lo del medio. El largo se controla con `min_intermediates` y
   `max_intermediates` (default 3 y 4), y es una restricción, no una preferencia: el
   resultado es el camino de menor costo entre todos los que tienen entre `min` y `max`
   intermedios. Si A y B mezclan directo, igual se devuelven 3 o 4 (se pidió un puente de
   ese largo); quien quiera el salto directo pide `min_intermediates=0`. Si el camino más
   barato tiene más intermedios que `max`, no se estira: se devuelve el mejor que entra en
   el rango, o se dice que no hay (ver 4). `max_intermediates` tiene tope
   (`MAX_INTERMEDIATES_CAP`): la búsqueda está acotada por saltos, nunca es infinita.

3. **El camino no repite tracks**, y ni A ni B aparecen en el medio. Un Dijkstra sobre
   estados `(track, intermedios)` no alcanza para eso —el mejor camino a un estado puede
   usar justo el track que el resto necesita—, así que la búsqueda va en dos pasos:
   primero `_cheapest_walk`, ese Dijkstra (A*, con una cota inferior del costo que falta,
   `_lower_bounds`) sin exigir tracks distintos; si lo que devuelve no repite, es el óptimo
   exacto. Si repite, primero se cambian los repetidos por clones (mismo BPM y key: mismo
   costo, `_repair_repeats`), y solo si no hay clones `_cheapest_path` busca sobre CAMINOS
   parciales con una poda que no pierde el óptimo (`_dominated`). Todo es exacto.
   Antes de buscar se mide la CONECTIVIDAD (`_hops`, O(n log n)): si no hay camino a ningún
   largo, o el más corto no entra en el rango, se contesta sin buscar.
   Determinista: mismo input, mismo puente; el orden de la lista de entrada no importa (la
   biblioteca se ordena por ruta). Entre caminos de costo EXACTAMENTE igual (clones) no se
   promete "el de menos saltos" ni "el de rutas menores": gana el que la búsqueda completa
   primero, que va en profundidad a igual prioridad. Cumplir una regla lexicográfica
   obligaría a seguir buscando después del óptimo, y el orden en profundidad es lo que
   bajó 100 clones con 8 intermedios de 67 s a 0.2 s. La única promesa de desempate: con
   `min_intermediates == 0`, el salto directo gana cualquier empate.

4. **Si no hay puente, se dice y se dice por qué. La compuerta NO se afloja.** Es el mismo
   criterio que el corte de la radio (`radio.py`, punto 1): §4 pide 0% de transiciones
   fuera de ±8%. Los motivos se distinguen porque se arreglan distinto:
   `sin_camino` (A y B están en componentes que la compuerta de BPM no une: no hay largo
   que lo arregle) y `largo_fuera_de_rango` (hay camino, pero no con esa cantidad de
   intermedios: el detalle dice cuántos necesita el más corto).

5. **Los fragmentos (menos de 90 s) nunca son intermedios, y A o B no pueden serlo.** El
   filtro es `radio._separar_fragmentos` (`modelos.es_track`), el mismo de la radio. Para
   los extremos es más estricto que la radio con su semilla: el puente entero apunta al BPM
   de B, y en 10 s de audio ese BPM no es una medición. Se devuelve un corte
   `extremo_no_es_track` y no una excepción, así la CLI decide cómo decirlo. Un extremo con
   BPM 0 (el análisis no encontró pulso) tiene su propio corte, `extremo_sin_bpm`: decir
   "nada entra en ±8% de 0.0 BPM" es cierto pero esconde la causa.

No se aplican `artist_gap`, energía ni timbre: el puente responde "¿cómo llego de acá a
allá sin un solo salto fuera de tempo?", y cualquier otro criterio iría en el costo, que
por ahora es solo la mezclabilidad.
"""
from __future__ import annotations

import heapq
import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from motor.modelos import DURACION_MINIMA_TRACK_S, Track, es_track
from motor.radio import (
    SetStep,
    Transition,
    _plural,
    _separar_fragmentos,
    _transition,
    _unicos_por_ruta,
)
from motor.scoring import (
    FACTORES_OCTAVA,
    TOLERANCIA_BPM,
    KeysIndexadas,
    _bpm_valido,
    _factor_key,
    bpm_score,
    mezclabilidad,
    mezclabilidad_vector,
)
from motor.tonalidad import compat_camelot

DEFAULT_MIN_INTERMEDIATES = 3
DEFAULT_MAX_INTERMEDIATES = 4
# Tope de intermedios. Más que esto ya no es un puente, es un set: para eso está la radio.
# Existe para que la búsqueda esté acotada por saltos (el costo crece con el largo). Era 8 y
# bajó a 6 (auditoría de 35ce676): cuando la relajación repite un track y no hay clones para
# repararla, la búsqueda sobre caminos crece muy rápido con el largo. Medido con 400 tracks
# densos (127-129 BPM con un decimal, keys 1A-6A): 5 intermedios 28 ms, 6 → 61 ms,
# 8 → 3.5 s (8.839 caminos sacados del heap), y con 10.000 tracks sería peor.
MAX_INTERMEDIATES_CAP = 6

# Códigos de corte de `Bridge.stop`. None = hay puente.
STOP_EXTREMO_NO_TRACK = "extremo_no_es_track"
STOP_EXTREMO_SIN_BPM = "extremo_sin_bpm"
STOP_MISMO_TRACK = "mismo_track"
STOP_SIN_CAMINO = "sin_camino"
STOP_LARGO = "largo_fuera_de_rango"


@dataclass(slots=True, frozen=True)
class Bridge:
    """El puente armado: A, los intermedios y B, cada uno con su motivo; o por qué no hay.

    `steps` arranca en A y termina en B. El `Transition` de A es el de una semilla (no viene
    de ninguna parte); el de cada uno de los demás describe el salto DESDE el anterior. Los
    `Transition` no tienen curva de energía: `energy_goal` es NaN (el puente no persigue
    ninguna curva, y poner un número ahí sería inventar un objetivo).

    `direct` es el motivo del salto A → B cuando A y B YA mezclan directo (`None` si no):
    decisión del dueño, el puente igual da los intermedios pedidos, pero quien lo muestra
    avisa que el rodeo es opcional.

    `cost` es `Σ -log(mezclabilidad)` de los saltos (punto 1 del docstring del módulo); 0.0
    es "todos los saltos perfectos". `None` si no hay puente.

    Si no hay puente, `steps` queda vacío, `stop` trae el código (`STOP_*`) y `stop_detail`
    la explicación en castellano. `fragments` son los archivos que el puente no consideró
    como intermedios por durar menos de `DURACION_MINIMA_TRACK_S`.
    """

    steps: tuple[SetStep, ...]
    stop: str | None = None
    stop_detail: str = ""
    cost: float | None = None
    fragments: int = 0
    direct: Transition | None = None

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self):
        return iter(self.steps)

    @property
    def found(self) -> bool:
        return self.stop is None

    @property
    def tracks(self) -> list[Track]:
        return [s.track for s in self.steps]

    @property
    def intermediates(self) -> list[Track]:
        """Lo que el puente aporta: los tracks entre A y B."""
        return [s.track for s in self.steps[1:-1]]

    def reasons(self) -> list[str]:
        return [s.transition.reason() for s in self.steps]


# ---------------------------------------------------------------------------
# La búsqueda
# ---------------------------------------------------------------------------

def _hops(center_bpms: Sequence[float], bpms: np.ndarray) -> np.ndarray:
    """Mínimo de saltos dentro de la compuerta desde cualquiera de `center_bpms` hasta cada
    uno de `bpms` (BFS sin límite; `inf` = inalcanzable). Los centros no son nodos: son el
    punto de partida (B cuando se mide "cuánto le falta a cada track para llegar a B", A
    cuando se cuenta a cuántos se llega desde A).

    Por qué no es un BFS con `mezclabilidad_vector` contra toda la biblioteca en cada paso
    (era `_Reach`, O(n²): 5 s para decir `sin_camino` con 10.000 tracks): la compuerta solo
    mira el BPM (la key nunca anula una mezcla, `compat_camelot` tiene piso 0.2), y en
    `log2(BPM)` es un INTERVALO por cada lectura de octava — `dist_bpm_relativa(a, f·b) =
    1 - 2^-|log2 a - log2 b - log2 f|` es menor que `tol` si y solo si
    `|log2 a - log2 b - log2 f| < -log2(1 - tol)`. Con los BPM ordenados, los vecinos de un
    track son tres rangos contiguos (uno por `scoring.FACTORES_OCTAVA`), y un "siguiente no
    visitado" con compresión de caminos hace que cada track se visite una vez: O(n log n).

    La cuenta en `log2` NO reemplaza a la compuerta: solo decide quién es vecino SEGURO
    (adentro del intervalo por más de 1e-9) o seguro NO vecino (afuera por más de 1e-9). En
    esa franja de 1e-9 del borde la decisión la toma `scoring.bpm_score` (la compuerta
    misma), así que un BPM justo en el 8% se trata exactamente como lo trata la búsqueda.
    """
    n = len(bpms)
    salida = np.full(n, math.inf)
    validos = np.flatnonzero(_bpm_valido(bpms))
    if validos.size == 0:
        return salida
    x_todos = np.log2(bpms[validos])
    orden = np.argsort(x_todos, kind="stable")
    xs_arr = x_todos[orden]                          # log2 de los válidos, ordenados
    idx = validos[orden].tolist()                    # su índice en `bpms`
    xs = xs_arr.tolist()
    bpm_de = bpms[validos[orden]].tolist()
    siguiente = list(range(len(xs) + 1))            # "siguiente no visitado", con compresión

    def buscar(i: int) -> int:
        raiz = i
        while siguiente[raiz] != raiz:
            raiz = siguiente[raiz]
        while siguiente[i] != raiz:
            siguiente[i], i = raiz, siguiente[i]
        return raiz

    ancho = -math.log2(1.0 - TOLERANCIA_BPM)
    margen = 1e-9
    corrimientos = [math.log2(f) for f in FACTORES_OCTAVA]

    def rangos(x: np.ndarray) -> list[list[tuple[int, int]]]:
        """Para cada `x`, el rango `[desde, hasta)` de `xs` que cae en el intervalo de
        cada lectura (ensanchado en `margen`). Un solo `searchsorted` por borde y lectura."""
        cols = [(np.searchsorted(xs_arr, x - s - ancho - margen, "left").tolist(),
                 np.searchsorted(xs_arr, x - s + ancho + margen, "right").tolist())
                for s in corrimientos]
        return [[(c[0][i], c[1][i]) for c in cols] for i in range(len(x))]

    rango_de = rangos(xs_arr)                       # los de cada track, calculados juntos
    centros = [float(b) for b in center_bpms if _bpm_valido(b)]
    x_centros = np.log2(np.array(centros, dtype=np.float64))
    cola: deque[tuple[float, float, list[tuple[int, int]], int]] = deque(
        (b, float(x), r, 0) for b, x, r in zip(centros, x_centros, rangos(x_centros),
                                              strict=True))
    while cola:
        bpm_u, x_u, rango_u, dist = cola.popleft()
        for s, (desde, fin) in zip(corrimientos, rango_u, strict=True):
            centro = x_u - s
            k = buscar(desde)
            while k < fin:
                if (abs(xs[k] - centro) < ancho - margen
                        or bpm_score(bpm_u, bpm_de[k]) > 0.0):
                    salida[idx[k]] = dist + 1
                    siguiente[k] = k + 1              # visitado: no se vuelve a mirar
                    cola.append((bpm_de[k], xs[k], rango_de[k], dist + 1))
                k = buscar(k + 1)
    return salida


def _g(delta: np.ndarray) -> np.ndarray:
    """Lo mínimo que puede costar, en BPM, un salto que cubre `delta` en `log2` del BPM (ya
    plegado por octavas): `-log(1 - (1 - 2^-δ) / tol)`, `inf` si no entra en la compuerta.
    Ver el punto 2 de `_lower_bounds`."""
    salto = 1.0 - np.exp2(-np.maximum(delta, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(salto < TOLERANCIA_BPM, -np.log1p(-salto / TOLERANCIA_BPM), math.inf)


def _lower_bounds(bpms: np.ndarray, keys: KeysIndexadas, hi: int,
                  hops_to_b: np.ndarray) -> np.ndarray:
    """Cota inferior del costo que le falta a cada estado para llegar a B. Es lo que hace
    que la búsqueda no recorra la biblioteca entera (A* en vez de Dijkstra pelado).

    `bpms` y `keys` son los de todos los nodos, con B al final. `cota[h, w]` = lo mínimo
    que puede costar ir de `w` a B cuando `w` es el intermedio número `h`, o sea con a lo
    sumo `r = hi - h + 1` saltos. `cota[:, B] = 0` (ya llegó). `hops_to_b` (de `_hops`) es
    el mínimo de saltos de cada track a B: si es mayor que `r`, la cota es `inf` — con los
    saltos que quedan no se llega, por barato que sea cada uno. Es lo que poda de entrada
    un grupo de BPM separado de B por un hueco (house 118-140 contra dnb 160-178): sin
    esto la cota de BPM plegado es finita para todo ese grupo y la búsqueda lo recorría.

    NO es otra compuerta ni otro score: no decide qué mezcla ni cuánto (eso es
    `mezclabilidad`), solo cuánto como MÍNIMO va a costar lo que falta. Si fuera más grande
    que el costo real, la búsqueda podría devolver un camino que no es el más barato; los
    tests contra la búsqueda exhaustiva y `test_la_cota_nunca_supera_el_costo_real` lo
    vigilan. Como `-log(bpm_score × factor_key) = -log(bpm_score) - log(factor_key)`, la
    cota es la suma de dos cotas, una por cada término:

    BPM (el término que decide cuánto se poda):

    1. Con `δ` = distancia entre `log2` de los BPM, plegada por octavas (en la circunferencia
       de largo 1), todo salto que pasa la compuerta tiene `dist_bpm_relativa >= 1 - 2^-δ`:
       las tres lecturas de `scoring.FACTORES_OCTAVA` corren `log2` en un entero, y la
       distancia plegada es la menor de todas esas.
    2. Entonces `-log(bpm_score) >= g(δ) = -log(1 - (1 - 2^-δ) / tol)`. `g` es creciente y
       convexa.
    3. `δ` plegada cumple la desigualdad triangular: `r` saltos que van de `w` a B cubren al
       menos `δ(w, B)`. Por convexidad, `Σ g(δ_i) >= r · g(δ(w, B) / r)`, que además baja
       con `r`: con el máximo de saltos que quedan es la cota más floja, y vale para todos.

    Key: el camino más barato de la key de `w` a la de B en a lo sumo `r` pasos por la rueda,
    pagando en cada paso `-log(factor_key)` — medido con `compat_camelot` y
    `scoring._factor_key` sobre las keys que hay en la biblioteca, no con una tabla escrita
    acá. Ningún camino real puede pagar menos: su secuencia de keys es uno de esos caminos.

    Las dos partes son CONSISTENTES (ninguna baja más que el costo de un salto), que es lo
    que deja asentar cada estado una sola vez en `_cheapest_walk`. Se achica en una parte en
    mil millones para que el redondeo de floats no la haga pasar del costo real por un bit.
    """
    n = len(bpms) - 1
    cota = np.zeros((hi + 2, n + 1))
    if hi == 0:
        return cota
    bpm_b = float(bpms[-1])
    validos = _bpm_valido(bpms[:n]) & bool(_bpm_valido(bpm_b))
    if not validos.any():
        cota[1:hi + 1, :n] = math.inf              # sin BPM medible nada llega a B
        return cota
    with np.errstate(divide="ignore", invalid="ignore"):
        vuelta = np.abs(np.log2(np.where(validos, bpms[:n], 1.0)) - math.log2(bpm_b)) % 1.0
    delta = np.minimum(vuelta, 1.0 - vuelta)

    # Key: `paso[i, j]` = costo de key de ir de la key `i` a la `j` (índices de `keys.unicas`);
    # `hasta_b[s][i]` = el más barato de `i` a la key de B en EXACTAMENTE `s` pasos.
    unicas = keys.unicas
    paso = np.array([[-math.log(_factor_key(compat_camelot(x, y))) for y in unicas]
                     for x in unicas])
    key_b = int(keys.codigos[-1])
    exacto = paso[:, key_b].copy()                 # s = 1
    mejor_key = [None, exacto.copy()]              # mejor_key[r] = min sobre s <= r
    for _ in range(2, hi + 2):
        exacto = np.min(paso + exacto[None, :], axis=1)
        mejor_key.append(np.minimum(mejor_key[-1], exacto))

    for h in range(1, hi + 1):
        r = hi - h + 1
        g = r * _g(delta / r)                      # convexidad: rampa pareja de r saltos
        total = g + mejor_key[r][keys.codigos[:n]]
        cota[h, :n] = np.where(validos & (hops_to_b <= r), total * (1.0 - 1e-9), math.inf)
    return cota


class _Graph:
    """Las aristas que salen de cada track, calculadas a pedido.

    Los nodos son `pool` (índices 0..n-1, ordenados por ruta) y B (índice `n`). A no es
    destino de ninguna arista: un camino no vuelve a pasar por su origen. Las aristas de `u`
    salen de UNA llamada a `mezclabilidad_vector(u, todos)` — la compuerta y el score de la
    radio, sin copia: hay arista si y solo si la mezclabilidad es > 0, y cuesta
    `-log(mezclabilidad)`.
    """

    def __init__(self, pool: list[Track], target: Track, hi: int):
        self.nodes = [*pool, target]
        self.target = len(pool)
        self.bpms = np.array([float(t.bpm) for t in self.nodes], dtype=np.float64)
        self.keys = KeysIndexadas.de([t.key for t in self.nodes])
        self.hops_to_b = _hops([float(target.bpm)], self.bpms[:-1])
        self.bound = _lower_bounds(self.bpms, self.keys, hi, self.hops_to_b)
        self._cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    def mix_from(self, track: Track) -> np.ndarray:
        """La mezclabilidad de `track` contra todos los nodos (pool + B)."""
        return mezclabilidad_vector(track.bpm, self.bpms, track.key, self.keys)

    def costs_from(self, track: Track, key: int) -> np.ndarray:
        """`-log(mezclabilidad)` contra todos los nodos; `inf` = no pasa la compuerta. `key`
        es el índice del propio track (-1 = A), que no es vecino de sí mismo."""
        mix = self.mix_from(track)
        with np.errstate(divide="ignore"):
            # `+ 0.0`: `-log(1.0)` es -0.0, y un costo "-0.000" en pantalla es ruido.
            costos = np.where(mix > 0.0, -np.log(mix) + 0.0, math.inf)
        if key >= 0:
            costos[key] = math.inf
        return costos

    def edges(self, key: int, track: Track, level: int
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """`(vecinos, costos, prioridades)` de `track` cuando es el intermedio número
        `level`, ordenados por prioridad (costo + cota del vecino) y después por índice.
        Cacheado por `(track, level)`: la búsqueda sobre caminos pide los mismos muchas veces.
        """
        hit = self._cache.get((key, level))
        if hit is None:
            costos = self.costs_from(track, key)
            prio = costos + self.bound[level + 1]
            vecinos = np.flatnonzero(prio < math.inf)   # pasa la compuerta y B sigue a mano
            orden = np.argsort(prio[vecinos], kind="stable")
            vecinos = vecinos[orden]
            hit = (vecinos, costos[vecinos], prio[vecinos])
            self._cache[(key, level)] = hit
        return hit


def _cheapest_walk(origin: Track, graph: _Graph, lo: int, hi: int
                   ) -> tuple[tuple[int, ...], float] | None:
    """A* sobre estados `(track, intermedios hasta acá)` SIN exigir que el camino no repita
    tracks. Devuelve `(índices de los intermedios, costo)` o `None`.

    Es la relajación del problema: todo camino sin repetidos es también un recorrido de
    acá, así que (a) si esto no encuentra nada, no hay puente, y (b) si lo que encuentra
    no repite tracks, es EL óptimo del problema de verdad. Casi siempre es así — repetir
    un track obliga a ir y volver, y en una biblioteca real casi siempre hay un tercero
    igual de barato —, y cuando no, `build_bridge` cae a `_cheapest_path`, que es exacta.

    Por qué existe además de `_cheapest_path`: esta asienta cada estado UNA vez y lo
    expande con una sola operación de numpy contra toda la biblioteca, mientras que la
    búsqueda sobre caminos saca del heap cada camino parcial más barato que el óptimo, y
    entre dos grupos de BPM distintos de una biblioteca de 10.000 tracks eso son millones.

    El orden es `costo + cota` (`_lower_bounds`), que es consistente: cada estado se asienta
    con su costo final. Desempate determinista: a igual prioridad se asienta primero el
    estado de MÁS intermedios (en profundidad: con 300 clones a costo 0 y 6 intermedios,
    el de menos intermedios primero asentaba ~1.500 estados antes de completar un camino;
    en profundidad, menos de 20) y después el de índice (= ruta) menor. Un estado solo
    cambia de padre si mejora estrictamente, y un camino completo solo reemplaza a otro si
    cuesta estrictamente menos: el salto directo (con `lo == 0`) se anota antes de asentar
    nada y gana cualquier empate.
    """
    n = graph.target
    inf = math.inf
    dist = np.full((hi + 1, n), inf)
    padre = np.full((hi + 1, n), -1, dtype=np.intp)
    abiertos = np.full((hi + 1, n), inf)      # prioridad de los no asentados; inf = asentado

    desde_a = graph.costs_from(origin, -1)
    mejor, mejor_estado = inf, None
    if lo == 0 and desde_a[n] < inf:
        mejor, mejor_estado = float(desde_a[n]), (0, -1)
    if hi >= 1:
        dist[1] = desde_a[:n]
        abiertos[1] = desde_a[:n] + graph.bound[1, :n]

    while n:                                       # sin pool no hay intermedios que asentar
        fila_invertida, v = divmod(int(np.argmin(abiertos[::-1])), n)   # más profundo primero
        h = hi - fila_invertida
        if not abiertos[h, v] < mejor:
            break                                  # nada abierto puede mejorar lo hallado
        abiertos[h, v] = inf
        d = float(dist[h, v])
        c = graph.costs_from(graph.nodes[v], v)
        if h >= lo and d + c[n] < mejor:
            mejor, mejor_estado = d + float(c[n]), (h, v)
        if h < hi:
            fila = d + c[:n]
            mejora = fila < dist[h + 1]
            dist[h + 1][mejora] = fila[mejora]
            padre[h + 1][mejora] = v
            abiertos[h + 1][mejora] = fila[mejora] + graph.bound[h + 1, :n][mejora]

    if mejor_estado is None:
        return None
    h, v = mejor_estado
    recorrido: list[int] = []
    while h >= 1:
        recorrido.append(v)
        v = int(padre[h, v])
        h -= 1
    return tuple(reversed(recorrido)), mejor


def _dominated(kept: list[frozenset[int]], prefix: frozenset[int], room: int) -> bool:
    """¿Sobra este camino parcial? La poda que hace exacta la búsqueda sin explotar.

    Un camino parcial llega a un track `v` después de pasar por `prefix` (sin contar A ni
    `v`), y todavía puede sumar hasta `room` intermedios antes de B. Ya salieron del heap
    —con costo menor o igual, porque el heap va en orden— los caminos `kept` al mismo `v`
    con la misma cantidad de saltos. Lo que falta de acá a B cuesta lo mismo para todos
    ellos: solo importa QUÉ tracks ya usó cada uno, porque el resto no los puede repetir.

    Este camino hace falta solo si existe una continuación `F` (hasta `room` tracks, sin
    tocar `prefix`) que choca con TODOS los `kept` — o sea, una que ninguno de los más
    baratos puede usar. Si no existe, cualquier final que sirva para este camino sirve igual
    para alguno de los `kept`, que cuesta lo mismo o menos: se descarta sin perder el
    óptimo. Con `room == 0` alcanza con uno; en la práctica se guardan uno o dos por estado.

    La búsqueda de `F` es exhaustiva pero chica: ramifica sobre los tracks de un `kept` que
    `F` todavía no toca (a lo sumo `MAX_INTERMEDIATES_CAP` - 1) con profundidad `room`.
    """
    return not _hitting_continuation(kept, prefix, room, frozenset())


def _hitting_continuation(kept: list[frozenset[int]], forbidden: frozenset[int], room: int,
                          chosen: frozenset[int]) -> bool:
    """¿Hay un `F ⊇ chosen`, con `|F| <= |chosen| + room` y `F ∩ forbidden = ∅`, que toque a
    todos los conjuntos de `kept`?"""
    for usado in kept:
        if usado & chosen:
            continue
        opciones = sorted(usado - forbidden)
        if room == 0 or not opciones:
            return False
        return any(_hitting_continuation(kept, forbidden, room - 1, chosen | {e})
                   for e in opciones)
    return True


def _cheapest_path(origin: Track, graph: _Graph, lo: int, hi: int
                   ) -> tuple[tuple[int, ...], float] | None:
    """El camino de menor `Σ -log(mezclabilidad)` de `origin` a B con entre `lo` y `hi`
    intermedios DISTINTOS, o `None`. Devuelve `(índices de los intermedios, costo)`.

    A* sobre caminos parciales, con la misma cota que `_cheapest_walk`. Cada entrada del
    heap es un camino candidato `(prioridad, -saltos, índices, costo, hermano)`: se compara
    por prioridad (costo + cota), después por cantidad de saltos — a igual prioridad, el
    MÁS largo — y después por los índices. Los índices de dos entradas nunca son iguales,
    así que la comparación nunca llega más allá.

    Por qué el más largo primero: con empates exactos (clones: mismo BPM y key, costo 0) el
    más corto primero recorría TODOS los caminos de un largo antes de probar uno más largo
    (medido en la auditoría: 100 clones y 8 intermedios, 67 s). En profundidad, el primer
    camino completo aparece en `hi + 1` pasos. El orden de costo no cambia: sigue siendo
    exacto.

    Los hijos de un camino NO se meten todos al heap de una vez (con 10.000 tracks que
    mezclan entre sí serían 10.000 entradas por camino): se mete el de menor prioridad, y
    cuando sale, su "hermano" siguiente. Como los vecinos están ordenados por prioridad, el
    heap sigue sacando los caminos en orden exacto. Antes de meter un hijo se descarta si
    repite un track, si llegaría a B demasiado corto o si ya está dominado (`_dominated`
    con lo que salió hasta ahora): así un racimo de clones no llena el heap de caminos que
    al salir se iban a tirar.
    """
    target = graph.target
    heap: list = []
    kept: dict[tuple[int, int], list[frozenset[int]]] = {}

    def push_child(path: tuple[int, ...], cost: float, aristas, i: int) -> None:
        """Mete al heap el primer hijo útil de `path` desde el `i`; el `hermano` anota cómo
        pedir el siguiente."""
        vecinos, costos, prio = aristas
        largo = len(path) + 1
        prefijo = frozenset(path)
        while i < len(vecinos):
            w = int(vecinos[i])
            if w == target:
                util = largo - 1 >= lo
            else:
                util = w not in prefijo and not _dominated(
                    kept.get((w, largo), []), prefijo, hi - largo)
            if util:
                heapq.heappush(heap, (cost + float(prio[i]), -largo, (*path, w),
                                      cost + float(costos[i]), (path, cost, aristas, i)))
                return
            i += 1

    def expand(path: tuple[int, ...], cost: float, track: Track, key: int) -> None:
        if len(path) == hi:
            # Ya no entra otro intermedio: el único hijo posible es B, si mezcla.
            c = float(graph.costs_from(track, key)[target])
            if c < math.inf:
                heapq.heappush(heap, (cost + c, -(len(path) + 1), (*path, target), cost + c,
                                      None))
            return
        push_child(path, cost, graph.edges(key, track, len(path)), 0)

    expand((), 0.0, origin, -1)
    while heap:
        _, _, path, cost, hermano = heapq.heappop(heap)
        if hermano is not None:
            padre, costo_padre, aristas, i = hermano
            push_child(padre, costo_padre, aristas, i + 1)

        v = path[-1]
        if v == target:
            if len(path) - 1 >= lo:
                return path[:-1], cost
            continue                          # muy corto: B todavía no puede llegar
        if v in path[:-1]:
            continue                          # repetiría un track
        previos = kept.setdefault((v, len(path)), [])
        prefijo = frozenset(path[:-1])
        if _dominated(previos, prefijo, hi - len(path)):
            continue
        previos.append(prefijo)
        expand(path, cost, graph.nodes[v], v)
    return None


def _nota_fragmentos(fragmentos: Sequence[Track]) -> str:
    if not fragmentos:
        return ""
    n = len(fragmentos)
    return (f"; además hay {_plural(n, 'archivo', 'archivos')} de menos de "
            f"{DURACION_MINIMA_TRACK_S:.0f} s que el puente no usa (loops, samples, notas de "
            f"voz: no son tracks)")


def _no_bridge(origin: Track, target: Track, graph: _Graph, lo: int, hi: int,
               fragmentos: Sequence[Track], saltos: float) -> Bridge:
    """El `Bridge` vacío con el motivo: `sin_camino` o `largo_fuera_de_rango`.

    `saltos` es el mínimo de saltos de A a B sin límite (`inf` si no hay camino a ningún
    largo), medido con `_hops`: la misma compuerta, sin recorrer la biblioteca con
    `mezclabilidad_vector` en cada paso.
    """
    tol = f"±{TOLERANCIA_BPM:.0%}"
    extremos = f"{origin.bpm:.1f} BPM y {target.bpm:.1f} BPM"
    if math.isinf(saltos):
        desde_a = int(np.isfinite(_hops([float(origin.bpm)], graph.bpms[:-1])).sum())
        hacia_b = int(np.isfinite(graph.hops_to_b).sum())
        if desde_a == 0:
            lado = f"ningún track de la biblioteca entra en {tol} de {origin.bpm:.1f} BPM"
        elif hacia_b == 0:
            lado = f"ningún track de la biblioteca entra en {tol} de {target.bpm:.1f} BPM"
        else:
            lado = (f"desde {origin.bpm:.1f} BPM, saltando dentro de {tol}, se llega a "
                    f"{_plural(desde_a, 'track', 'tracks')}; a {target.bpm:.1f} BPM se llega "
                    f"desde {_plural(hacia_b, 'track', 'tracks')}; los dos grupos no se tocan")
        detalle = (f"ninguna cadena de saltos dentro de {tol} une {extremos} ({lado}); no se "
                   f"afloja la tolerancia (spec §4: 0% de transiciones fuera de {tol})")
        return Bridge((), STOP_SIN_CAMINO, detalle + _nota_fragmentos(fragmentos),
                      fragments=len(fragmentos))

    minimo = int(saltos) - 1
    pedido = (f"{lo}" if lo == hi else f"entre {lo} y {hi}")
    if minimo > hi:
        detalle = (f"el camino más corto dentro de {tol} entre {extremos} necesita "
                   f"{_plural(minimo, 'intermedio', 'intermedios')} y se pidieron {pedido}")
    else:
        detalle = (f"hay camino dentro de {tol} entre {extremos} con "
                   f"{_plural(minimo, 'intermedio', 'intermedios')}, pero no con {pedido} "
                   f"sin repetir tracks")
    return Bridge((), STOP_LARGO, detalle + _nota_fragmentos(fragmentos),
                  fragments=len(fragmentos))


def _repair_repeats(indices: tuple[int, ...], pool: list[Track]) -> tuple[int, ...] | None:
    """Saca los repetidos de un recorrido cambiándolos por CLONES, o `None` si no alcanza.

    Dos tracks con el mismo BPM y el mismo texto de key tienen exactamente las mismas
    aristas y los mismos costos (`mezclabilidad` no mira otra cosa), así que cambiar la
    segunda aparición de `x` por un clone de `x` que el recorrido no use deja el costo
    idéntico. Como el recorrido era óptimo sin exigir tracks distintos, el camino reparado
    es óptimo exigiéndolo. Es el caso que hacía caer a `_cheapest_path` con miles de clones
    (bibliotecas con BPM de un decimal: cientos de tracks con el mismo BPM y key). Se elige
    el clone de índice (= ruta) menor: determinista.
    """
    usados = set(indices)
    clases: dict[tuple[float, str], list[int]] | None = None
    vistos: set[int] = set()
    salida: list[int] = []
    for i in indices:
        if i not in vistos:
            vistos.add(i)
            salida.append(i)
            continue
        if clases is None:
            clases = {}
            for j, t in enumerate(pool):
                clases.setdefault((float(t.bpm), t.key), []).append(j)
        reemplazo = next((j for j in clases[(float(pool[i].bpm), pool[i].key)]
                          if j not in usados), None)
        if reemplazo is None:
            return None
        usados.add(reemplazo)
        vistos.add(reemplazo)
        salida.append(reemplazo)
    return tuple(salida)


def build_bridge(origin: Track, target: Track, biblioteca: Sequence[Track],
                 min_intermediates: int = DEFAULT_MIN_INTERMEDIATES,
                 max_intermediates: int = DEFAULT_MAX_INTERMEDIATES) -> Bridge:
    """El puente de menor costo de `origin` (A) a `target` (B). Ver el docstring del módulo.

    - Cada salto pasa la compuerta de ±8% de BPM (`mezclabilidad > 0`), sin excepción.
    - Entre `min_intermediates` y `max_intermediates` tracks en el medio, sin contar A ni B,
      todos distintos y ninguno fragmento (< `DURACION_MINIMA_TRACK_S`).
    - Minimiza `Σ -log(mezclabilidad)`. Entre empates exactos, ver el punto 3 del módulo.
    - Si A y B ya mezclan directo, `Bridge.direct` trae ese salto (el aviso de la CLI).

    `ValueError` si el rango de intermedios no tiene sentido (negativo, `min > max` o
    `max > MAX_INTERMEDIATES_CAP`): es un error de quien llama, no un puente imposible.
    """
    if not 0 <= min_intermediates <= max_intermediates:
        raise ValueError(f"el rango de intermedios tiene que cumplir 0 <= mínimo <= máximo, "
                         f"recibí {min_intermediates!r}..{max_intermediates!r}")
    if max_intermediates > MAX_INTERMEDIATES_CAP:
        raise ValueError(f"a lo sumo {MAX_INTERMEDIATES_CAP} intermedios (más que eso es un "
                         f"set, no un puente: usá la radio), recibí {max_intermediates!r}")

    extremos = {str(origin.path), str(target.path)}
    pool, fragmentos = _separar_fragmentos(
        _unicos_por_ruta(t for t in biblioteca if str(t.path) not in extremos))

    no_tracks = [t for t in (origin, target) if not es_track(t.duration)]
    if no_tracks:
        detalle = "; ".join(
            f"{t.label} dura {t.duration:.1f} s y un extremo del puente necesita al menos "
            f"{DURACION_MINIMA_TRACK_S:.0f} s: es un loop, un sample o una nota de voz, no un "
            f"track, y su BPM no es una medición" for t in no_tracks)
        return Bridge((), STOP_EXTREMO_NO_TRACK, detalle, fragments=len(fragmentos))
    if str(origin.path) == str(target.path):
        return Bridge((), STOP_MISMO_TRACK,
                      f"el origen y el destino son el mismo track ({origin.label}): no hay "
                      f"nada que unir", fragments=len(fragmentos))

    sin_bpm = [t for t in (origin, target) if not _bpm_valido(t.bpm)]
    if sin_bpm:
        detalle = "; ".join(
            f"{t.label} no tiene BPM medido ({float(t.bpm):.1f}: el análisis no encontró un "
            f"pulso, por ejemplo en silencio o en audio sin ritmo), y la compuerta de "
            f"±{TOLERANCIA_BPM:.0%} no puede medir ningún salto desde o hacia ese track"
            for t in sin_bpm)
        return Bridge((), STOP_EXTREMO_SIN_BPM, detalle, fragments=len(fragmentos))

    directo = (_transition(origin, target, math.nan)
               if mezclabilidad(origin.bpm, target.bpm, origin.key, target.key) > 0.0
               else None)
    graph = _Graph(pool, target, max_intermediates)
    # Conectividad antes que costo: el mínimo de saltos de A a B sale de `_hops` sin
    # recorrer la biblioteca, y si no hay camino a ningún largo, o el más corto no entra en
    # el rango pedido, se contesta sin buscar.
    if directo is not None:
        saltos = 1.0
    else:
        vecinos_a = graph.mix_from(origin)[:-1] > 0.0
        saltos = 1.0 + float(graph.hops_to_b[vecinos_a].min()) if vecinos_a.any() else math.inf
    if math.isinf(saltos) or saltos - 1 > max_intermediates:
        return replace(_no_bridge(origin, target, graph, min_intermediates, max_intermediates,
                                  fragmentos, saltos), direct=directo)

    hallado = _cheapest_walk(origin, graph, min_intermediates, max_intermediates)
    if hallado is not None and len(set(hallado[0])) != len(hallado[0]):
        # La relajación repite un track. Primero se intenta cambiar los repetidos por
        # clones (mismo costo: sigue siendo el óptimo); si no hay, búsqueda exacta sin
        # repetidos.
        reparado = _repair_repeats(hallado[0], pool)
        hallado = ((reparado, hallado[1]) if reparado is not None else
                   _cheapest_path(origin, graph, min_intermediates, max_intermediates))
    if hallado is None:
        return replace(_no_bridge(origin, target, graph, min_intermediates, max_intermediates,
                                  fragmentos, saltos), direct=directo)

    indices, costo = hallado
    tracks = [origin, *(pool[i] for i in indices), target]
    sin_meta = math.nan
    steps = [SetStep(origin, _transition(None, origin, sin_meta))]
    steps += [SetStep(cur, _transition(prev, cur, sin_meta))
              for prev, cur in zip(tracks[:-1], tracks[1:], strict=True)]
    return Bridge(tuple(steps), cost=costo, fragments=len(fragmentos), direct=directo)


__all__ = [
    "DEFAULT_MAX_INTERMEDIATES",
    "DEFAULT_MIN_INTERMEDIATES",
    "MAX_INTERMEDIATES_CAP",
    "STOP_EXTREMO_NO_TRACK",
    "STOP_EXTREMO_SIN_BPM",
    "STOP_LARGO",
    "STOP_MISMO_TRACK",
    "STOP_SIN_CAMINO",
    "Bridge",
    "build_bridge",
]
