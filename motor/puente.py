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
   exacto. Si repite, `_cheapest_path` busca sobre CAMINOS parciales con una poda que no
   pierde el óptimo (`_dominated`). Las dos son exactas; la primera es la rápida.
   Determinista: mismo input, mismo puente; el orden de la lista de entrada no importa
   (la biblioteca se ordena por ruta y los empates se rompen por índice).

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
   `extremo_no_es_track` y no una excepción, así la CLI decide cómo decirlo.

No se aplican `artist_gap`, energía ni timbre: el puente responde "¿cómo llego de acá a
allá sin un solo salto fuera de tempo?", y cualquier otro criterio iría en el costo, que
por ahora es solo la mezclabilidad.
"""
from __future__ import annotations

import heapq
import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from motor.modelos import DURACION_MINIMA_TRACK_S, Track, es_track
from motor.radio import (
    SetStep,
    _plural,
    _separar_fragmentos,
    _transition,
    _unicos_por_ruta,
)
from motor.scoring import (
    TOLERANCIA_BPM,
    KeysIndexadas,
    _bpm_valido,
    _factor_key,
    mezclabilidad_vector,
)
from motor.tonalidad import compat_camelot

DEFAULT_MIN_INTERMEDIATES = 3
DEFAULT_MAX_INTERMEDIATES = 4
# Tope de intermedios. Más que esto ya no es un puente, es un set: para eso está la radio.
# Existe para que la búsqueda esté acotada por saltos (el costo crece con el largo).
MAX_INTERMEDIATES_CAP = 8

# Códigos de corte de `Bridge.stop`. None = hay puente.
STOP_EXTREMO_NO_TRACK = "extremo_no_es_track"
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

def _lower_bounds(bpms: np.ndarray, keys: KeysIndexadas, hi: int) -> np.ndarray:
    """Cota inferior del costo que le falta a cada estado para llegar a B. Es lo que hace
    que la búsqueda no recorra la biblioteca entera (A* en vez de Dijkstra pelado).

    `bpms` y `keys` son los de todos los nodos, con B al final. `cota[h, w]` = lo mínimo
    que puede costar ir de `w` a B cuando `w` es el intermedio número `h`, o sea con a lo
    sumo `r = hi - h + 1` saltos. `cota[:, B] = 0` (ya llegó).

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
        salto = 1.0 - np.exp2(-delta / r)
        with np.errstate(divide="ignore", invalid="ignore"):
            g = np.where(salto < TOLERANCIA_BPM, -r * np.log1p(-salto / TOLERANCIA_BPM),
                         math.inf)
        total = g + mejor_key[r][keys.codigos[:n]]
        cota[h, :n] = np.where(validos, total * (1.0 - 1e-9), math.inf)
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
        self.bound = _lower_bounds(self.bpms, self.keys, hi)
        self._cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    def mix_from(self, track: Track) -> np.ndarray:
        """La mezclabilidad de `track` contra todos los nodos (pool + B)."""
        return mezclabilidad_vector(track.bpm, self.bpms, track.key, self.keys)

    def costs_from(self, track: Track, key: int) -> np.ndarray:
        """`-log(mezclabilidad)` contra todos los nodos; `inf` = no pasa la compuerta. `key`
        es el índice del propio track (-1 = A), que no es vecino de sí mismo."""
        mix = self.mix_from(track)
        with np.errstate(divide="ignore"):
            costos = np.where(mix > 0.0, -np.log(mix), math.inf)
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
    con su costo final. Desempate determinista: a igual prioridad, el estado de menos
    intermedios y después el de índice (= ruta) menor; un estado solo cambia de padre si
    mejora estrictamente.
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
        h, v = divmod(int(np.argmin(abiertos)), n)
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
    heap es un camino candidato `(prioridad, saltos, índices, costo, hermano)`: se compara
    por prioridad (costo + cota), después por cantidad de saltos (a igual costo, el más
    corto) y después por los índices (el de rutas menores). Los índices de dos entradas
    nunca son iguales, así que la comparación nunca llega más allá.

    Los hijos de un camino NO se meten todos al heap de una vez (con 10.000 tracks que
    mezclan entre sí serían 10.000 entradas por camino): se mete el de menor prioridad, y
    cuando sale, su "hermano" siguiente. Como los vecinos están ordenados por prioridad, el
    heap sigue sacando los caminos en orden exacto.
    """
    target = graph.target
    heap: list = []
    kept: dict[tuple[int, int], list[frozenset[int]]] = {}

    def push_child(path: tuple[int, ...], cost: float, aristas, i: int) -> None:
        """Mete al heap el hijo `i` de `path`; el `hermano` anota cómo pedir el siguiente."""
        vecinos, costos, prio = aristas
        if i < len(vecinos):
            heapq.heappush(heap, (cost + float(prio[i]), len(path) + 1,
                                  (*path, int(vecinos[i])), cost + float(costos[i]),
                                  (path, cost, aristas, i)))

    def expand(path: tuple[int, ...], cost: float, track: Track, key: int) -> None:
        if len(path) == hi:
            # Ya no entra otro intermedio: el único hijo posible es B, si mezcla.
            c = float(graph.costs_from(track, key)[target])
            if c < math.inf:
                heapq.heappush(heap, (cost + c, len(path) + 1, (*path, target), cost + c, None))
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


class _Reach:
    """BFS por la compuerta desde un track, de a una expansión por vez (`step`).

    Cada expansión mide contra los nodos que todavía no se visitaron, con la misma
    `mezclabilidad_vector` (sobre ese subconjunto) que la búsqueda. B nunca se encola: es un
    destino, no un paso intermedio.
    """

    def __init__(self, graph: _Graph, start: Track, start_index: int | None):
        self.graph = graph
        self.visto = np.zeros(len(graph.nodes), dtype=bool)
        if start_index is not None:
            self.visto[start_index] = True
        self.cola: deque[tuple[Track, int]] = deque([(start, 0)])
        self.alcanzados = 0                     # tracks del pool visitados
        self.saltos_a_b: int | None = None

    @property
    def done(self) -> bool:
        return not self.cola

    def step(self) -> None:
        track, dist = self.cola.popleft()
        libres = np.flatnonzero(~self.visto)
        if libres.size == 0:
            self.cola.clear()
            return
        g = self.graph
        sub = KeysIndexadas(g.keys.unicas, g.keys.codigos[libres])
        nuevos = libres[mezclabilidad_vector(track.bpm, g.bpms[libres], track.key, sub) > 0.0]
        self.visto[nuevos] = True
        if self.saltos_a_b is None and self.visto[g.target]:
            self.saltos_a_b = dist + 1
        nuevos = nuevos[nuevos != g.target]
        self.alcanzados += int(nuevos.size)
        self.cola.extend((g.nodes[int(i)], dist + 1) for i in nuevos)


def _connectivity(origin: Track, graph: _Graph) -> tuple[int | None, int, int | None]:
    """Sin límite de saltos: `(saltos de A a B o None, tracks alcanzados desde A, tracks
    que llegan a B o None)`. Solo se corre cuando no hubo puente, para distinguir "no hay
    camino a ningún largo" de "no a este largo".

    Dos BFS a la par, uno desde A y otro desde B (la compuerta es simétrica: `scoring`
    mide la distancia de BPM igual en los dos sentidos, y lo verifica su test): el que
    agota primero su componente corta. Sin eso, un B aislado (un BPM que nadie tiene) en
    una biblioteca de 10.000 tracks obligaba a recorrer la componente entera de A (~3 s)
    para decir algo que desde B se ve en una sola expansión. Cuando el BFS de B se agota,
    A y B están conectados si y solo si A mezcla con algún track de esa componente.
    """
    desde_a = _Reach(graph, origin, None)
    hasta_b = _Reach(graph, graph.nodes[graph.target], graph.target)
    while True:
        desde_a.step()
        if desde_a.saltos_a_b is not None:
            return desde_a.saltos_a_b, desde_a.alcanzados, None
        if desde_a.done:
            return None, desde_a.alcanzados, None
        if hasta_b is not None:
            hasta_b.step()
            if hasta_b.done:
                mix = graph.mix_from(origin)
                if not (mix[hasta_b.visto] > 0.0).any():
                    return None, desde_a.alcanzados, hasta_b.alcanzados
                hasta_b = None                  # conectados: solo falta la distancia


def _nota_fragmentos(fragmentos: Sequence[Track]) -> str:
    if not fragmentos:
        return ""
    n = len(fragmentos)
    return (f"; además hay {_plural(n, 'archivo', 'archivos')} de menos de "
            f"{DURACION_MINIMA_TRACK_S:.0f} s que el puente no usa (loops, samples, notas de "
            f"voz: no son tracks)")


def _no_bridge(origin: Track, target: Track, graph: _Graph, lo: int, hi: int,
               fragmentos: Sequence[Track]) -> Bridge:
    """El `Bridge` vacío con el motivo: `sin_camino` o `largo_fuera_de_rango`."""
    tol = f"±{TOLERANCIA_BPM:.0%}"
    saltos, desde_a, hacia_b = _connectivity(origin, graph)
    extremos = f"{origin.bpm:.1f} BPM y {target.bpm:.1f} BPM"
    if saltos is None:
        if hacia_b is not None:
            lado = (f"ningún track de la biblioteca entra en {tol} de {target.bpm:.1f} BPM"
                    if hacia_b == 0 else
                    f"a {target.bpm:.1f} BPM solo se llega desde "
                    f"{_plural(hacia_b, 'track', 'tracks')}, y ninguno mezcla con "
                    f"{origin.bpm:.1f} BPM ni se une a él")
        elif desde_a == 0:
            lado = f"ningún track de la biblioteca entra en {tol} de {origin.bpm:.1f} BPM"
        else:
            lado = (f"desde {origin.bpm:.1f} BPM se alcanzan "
                    f"{_plural(desde_a, 'track', 'tracks')} saltando dentro de {tol}, y "
                    f"ninguno llega a {target.bpm:.1f} BPM")
        detalle = (f"ninguna cadena de saltos dentro de {tol} une {extremos} ({lado}); no se "
                   f"afloja la tolerancia (spec §4: 0% de transiciones fuera de {tol})")
        return Bridge((), STOP_SIN_CAMINO, detalle + _nota_fragmentos(fragmentos),
                      fragments=len(fragmentos))

    minimo = saltos - 1
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


def build_bridge(origin: Track, target: Track, biblioteca: Sequence[Track],
                 min_intermediates: int = DEFAULT_MIN_INTERMEDIATES,
                 max_intermediates: int = DEFAULT_MAX_INTERMEDIATES) -> Bridge:
    """El puente de menor costo de `origin` (A) a `target` (B). Ver el docstring del módulo.

    - Cada salto pasa la compuerta de ±8% de BPM (`mezclabilidad > 0`), sin excepción.
    - Entre `min_intermediates` y `max_intermediates` tracks en el medio, sin contar A ni B,
      todos distintos y ninguno fragmento (< `DURACION_MINIMA_TRACK_S`).
    - Minimiza `Σ -log(mezclabilidad)`; a igual costo, menos saltos; después, rutas menores.

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

    graph = _Graph(pool, target, max_intermediates)
    hallado = _cheapest_walk(origin, graph, min_intermediates, max_intermediates)
    if hallado is not None and len(set(hallado[0])) != len(hallado[0]):
        # La relajación repite un track: se busca el óptimo sin repetidos, exacto.
        hallado = _cheapest_path(origin, graph, min_intermediates, max_intermediates)
    if hallado is None:
        return _no_bridge(origin, target, graph, min_intermediates, max_intermediates,
                          fragmentos)

    indices, costo = hallado
    tracks = [origin, *(pool[i] for i in indices), target]
    sin_meta = math.nan
    steps = [SetStep(origin, _transition(None, origin, sin_meta))]
    steps += [SetStep(cur, _transition(prev, cur, sin_meta))
              for prev, cur in zip(tracks[:-1], tracks[1:], strict=True)]
    return Bridge(tuple(steps), cost=costo, fragments=len(fragmentos))


__all__ = [
    "DEFAULT_MAX_INTERMEDIATES",
    "DEFAULT_MIN_INTERMEDIATES",
    "MAX_INTERMEDIATES_CAP",
    "STOP_EXTREMO_NO_TRACK",
    "STOP_LARGO",
    "STOP_MISMO_TRACK",
    "STOP_SIN_CAMINO",
    "Bridge",
    "build_bridge",
]
