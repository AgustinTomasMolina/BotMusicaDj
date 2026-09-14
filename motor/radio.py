"""Capa 3 — la radio: buscar similares y armar el set.

Dos funciones públicas:

- `similar(track, biblioteca, n)` — los n más parecidos por timbre, sin criterio de mezcla.
- `build_set(seed_track, biblioteca, config)` — el set completo, greedy, con el POR QUÉ
  de cada elección.

El scoring NO vive acá: sale entero de `motor/scoring.py` (`score = encaje_musical ×
mezclabilidad`, con el BPM como compuerta dura de ±8%). Este módulo decide QUÉ candidato
gana en cada posición; cuánto vale una transición lo decide `scoring`.

---

Tres decisiones que alguien va a querer "arreglar", y por qué no:

1. **Si no hay candidato mezclable, el set SE CORTA.** No se afloja la tolerancia de BPM.
   La spec §4 pide *0% de transiciones fuera de ±8%*: es un 0%, no un "casi siempre". Una
   transición floja rompe el contrato del motor para todo el mundo; un set corto es un
   problema del usuario, que además se entera de por qué (`RadioSet.stop`). El boceto
   viejo (`src/djradio/radio.py:137`) decía lo contrario — "mejor una transición floja que
   un set corto" — y eso convierte el umbral de §4 en una sugerencia.

2. **El "por qué" se calcula con las MISMAS funciones que la compuerta.** Por eso este
   módulo importa `_dist_bpm_relativa` y `_parse_camelot`, que son privadas de sus
   módulos, en vez de recalcular la fórmula acá. Si el porcentaje que se muestra sale de
   una cuenta distinta de la que decide si el track entra, tarde o temprano la pantalla
   dice "+7.9%" sobre una transición que el motor midió en 8.1%, o al revés. Un motivo que
   no corresponde al track es exactamente lo que §6 llama "un dato que miente".

3. **`artist_gap` también corta.** Si lo único mezclable que queda es otro track del mismo
   artista dentro de la ventana, el set termina ahí en vez de repetir artista. Es una
   preferencia, no un umbral de §4, así que la decisión es discutible — pero un filtro que
   se afloja solo es un filtro que no se puede razonar desde afuera. El corte dice
   `"artist_gap"`, que es distinto de `"sin_candidatos_mezclables"`, y quien llama puede
   bajar `artist_gap` a 0 y volver a pedir.
"""
from __future__ import annotations

import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import numpy as np

from motor.energia import CURVES, energy_target
from motor.modelos import Track
from motor.scoring import TOLERANCIA_BPM, _dist_bpm_relativa, mezclabilidad, score
from motor.tonalidad import _parse_camelot, compat_camelot

# Lecturas de octava del BPM. `_dist_bpm_relativa` considera mezclable un 75 contra un 150
# (medio/doble tiempo); si la transición elegida es una de esas, mostrar "+100% BPM" sería
# técnicamente cierto e inútil, así que el porcentaje se informa contra la interpretación
# que efectivamente se usó y la lectura se dice aparte.
MISMO_TIEMPO = "mismo tiempo"
DOBLE_TIEMPO = "doble tiempo"
MEDIO_TIEMPO = "medio tiempo"

# Códigos de corte de `RadioSet.stop`. None = el set llegó al largo pedido.
STOP_BIBLIOTECA_VACIA = "biblioteca_vacia"
STOP_BIBLIOTECA_AGOTADA = "biblioteca_agotada"
STOP_SIN_MEZCLABLES = "sin_candidatos_mezclables"
STOP_ARTIST_GAP = "artist_gap"


# ---------------------------------------------------------------------------
# El POR QUÉ (spec §6)
# ---------------------------------------------------------------------------

def bpm_delta_pct(a: float, b: float) -> tuple[float, str]:
    """Salto de tempo de `a` a `b`: `(porcentaje con signo, lectura de octava)`.

    La MAGNITUD es exactamente `_dist_bpm_relativa(a, b) * 100`, la misma que usa la
    compuerta de `scoring.bpm_score`. Eso es deliberado: significa que `abs(pct) < 8`
    si y solo si la transición pasó la compuerta de §4. El signo se agrega acá (la
    distancia no lo tiene) para poder decir "+1.8%" o "-2.4%" como pide §6.

    BPM inválido (0, negativo, NaN) devuelve `nan` y lectura `""`: §6 dice que un dato
    ausente es mejor que uno que miente, y un 0.0 se leería como "mismo tempo".
    """
    if not (a > 0 and b > 0):
        return float("nan"), ""

    # Las tres lecturas que considera `_dist_bpm_relativa`, en ese orden. El empate se
    # rompe por el índice (o sea: a igualdad de diferencia gana "mismo tiempo"), nunca
    # por el orden en que numpy o un dict devuelvan las cosas.
    opciones = ((b, MISMO_TIEMPO), (2 * b, DOBLE_TIEMPO), (b / 2, MEDIO_TIEMPO))
    idx = min(range(len(opciones)), key=lambda i: (abs(a - opciones[i][0]), i))
    efectivo, lectura = opciones[idx]

    # La magnitud SALE de la función que usa la compuerta, no de una cuenta paralela: es la
    # única forma de garantizar que el porcentaje que se muestra y el que decide si el track
    # entra sean el mismo número (el denominador es max(a, b), simétrico). Acá solo se le
    # agrega el signo, que la distancia no tiene.
    pct = math.copysign(_dist_bpm_relativa(a, b) * 100.0, efectivo - a)
    return pct, lectura


def key_relation(a: str, b: str) -> str:
    """Nombre de la relación armónica entre dos keys Camelot, para mostrarla.

    `"mismo"` · `"vecino"` (±1 mismo lado) · `"relativo"` (mismo número, otro lado) ·
    `"±2"` · `"lejano"` · `"desconocida"`.

    Es la etiqueta de lo que `tonalidad.compat_camelot` ya puntúa; el test
    `test_relacion_y_compat_no_se_separan` amarra cada etiqueta a su valor numérico, para
    que este mapeo no pueda quedar desfasado del que decide el score.
    """
    pa, pb = _parse_camelot(a), _parse_camelot(b)
    if pa is None or pb is None:
        return "desconocida"
    na, lado_a = pa
    nb, lado_b = pb
    if na == nb and lado_a == lado_b:
        return "mismo"
    dist = min((na - nb) % 12, (nb - na) % 12)
    if lado_a == lado_b and dist == 1:
        return "vecino"
    if na == nb and lado_a != lado_b:
        return "relativo"
    if lado_a == lado_b and dist == 2:
        return "±2"
    return "lejano"


@dataclass(slots=True, frozen=True)
class Transition:
    """Por qué este track entró en esta posición (spec §6).

    Todos los campos que describen el SALTO (`bpm_delta_pct`, `from_*`, `mixability`, ...)
    son `None` en la semilla, que no viene de ninguna parte. `None` y no 0.0: un 0.0 en
    `bpm_delta_pct` se leería como "mismo tempo que el anterior".
    """

    from_bpm: float | None
    to_bpm: float
    bpm_delta_pct: float | None       # con signo, en % — `None` solo en la semilla
    bpm_octave: str                   # "mismo tiempo" | "doble tiempo" | "medio tiempo" | ""
    from_key: str | None
    to_key: str
    key_relation: str
    key_compat: float | None
    mixability: float | None
    musical_fit: float | None
    total: float | None               # `scoring.score(...)`: encaje × mezclabilidad
    energy: float
    energy_goal: float                # lo que pedía la curva en esta posición

    @property
    def is_seed(self) -> bool:
        return self.from_bpm is None

    def reason(self) -> str:
        """El renglón de §6: ``+1.8% BPM | 8A → 9A (vecino)``.

        El BPM va con un decimal porque redondearlo a entero es mentir (§6). Si la
        transición se apoya en medio o doble tiempo, se dice — es información que cambia
        cómo se mezcla.
        """
        if self.is_seed:
            return f"semilla | {self.to_key} | {self.to_bpm:.1f} BPM"

        delta = self.bpm_delta_pct
        salto = "?% BPM" if delta is None or math.isnan(delta) else f"{delta:+.1f}% BPM"
        if self.bpm_octave and self.bpm_octave != MISMO_TIEMPO:
            salto += f" ({self.bpm_octave})"
        return f"{salto} | {self.from_key} → {self.to_key} ({self.key_relation})"


@dataclass(slots=True, frozen=True)
class SetStep:
    """Un track del set junto con el motivo por el que está donde está."""

    track: Track
    transition: Transition


@dataclass(slots=True, frozen=True)
class RadioSet:
    """El set armado: los pasos en orden y, si quedó corto, por qué.

    Se comporta como una secuencia de `SetStep` (`len`, `for`, `[i]`) y expone `.tracks`
    para lo que solo quiere los tracks — `write_m3u8(rset.tracks, ...)` es el caso típico.

    `stop` es `None` cuando el set llegó al largo pedido. Si no, trae el código del corte
    (`STOP_*`) y `stop_detail` la explicación en castellano. Cortar en silencio sería
    devolver un set de 4 donde se pidieron 20 sin decir nada.
    """

    steps: tuple[SetStep, ...]
    stop: str | None = None
    stop_detail: str = ""

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self) -> Iterator[SetStep]:
        return iter(self.steps)

    def __getitem__(self, i: int) -> SetStep:
        return self.steps[i]

    @property
    def tracks(self) -> list[Track]:
        """Solo los tracks, en orden. Lo que consume `export.write_m3u8`."""
        return [s.track for s in self.steps]

    @property
    def energies(self) -> list[float]:
        """Las energías en orden — lo que come `energia.energy_curve_correlation`."""
        return [s.track.energy for s in self.steps]

    def reasons(self) -> list[str]:
        """Un renglón de §6 por track, listo para imprimir en la CLI."""
        return [s.transition.reason() for s in self.steps]

    @property
    def is_complete(self) -> bool:
        return self.stop is None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RadioConfig:
    """Pesos y perillas del armado.

    `seed` es la semilla del GENERADOR ALEATORIO, no el track semilla (ese es el primer
    argumento de `build_set`). Con `randomness=0` no se usa: la elección es el máximo, y
    el desempate es la ruta, así que el set es el mismo siempre (spec §5). Con
    `randomness>0` se muestrea entre los mejores candidatos con `random.Random(seed)` —
    una instancia propia, nunca el `random` global, que cualquier otro módulo del proceso
    podría reseedear sin enterarse de que rompió el determinismo de la radio.

    Pesos del encaje musical: `w_seed` (parecerse a la semilla mantiene la identidad del
    set), `w_prev` (parecerse al anterior da continuidad), `w_energy` (cuánto pesa pegarle
    a la curva) y `mmr_lambda` (cuánto se castiga ser redundante con lo ya elegido).
    """

    length: int = 20
    curve: str = "peak"               # "peak" | "warmup" | "flat" (motor.energia.CURVES)
    artist_gap: int = 4               # mínimo de tracks entre dos temas del mismo artista
    mmr_lambda: float = 0.3           # penalización por redundancia (Maximal Marginal Relevance)
    w_seed: float = 0.5
    w_prev: float = 0.5
    w_energy: float = 0.35
    seed: int = 0                     # semilla del RNG (ver arriba)
    randomness: float = 0.0           # 0 = determinista puro; 1 = muestrea entre los `top_k`
    top_k: int = 5                    # tamaño máximo de la ventana de muestreo

    def __post_init__(self) -> None:
        if self.length < 1:
            raise ValueError(f"un set tiene al menos un track, recibí length={self.length!r}")
        if self.curve not in CURVES:
            raise ValueError(f"curva desconocida {self.curve!r}; las soportadas son {CURVES}")
        # w_energy NO es un peso libre: es el porcentaje de mezcla entre timbre y energia
        # ((1-w)*timbre + w*energia). Fuera de 0..1 el termino del timbre pesa NEGATIVO y el
        # motor pasa a premiar a los candidatos que MENOS se parecen, en silencio. Medido:
        # con w_energy de 0.9, 1.6 y 3.0 sale exactamente el mismo set, porque pasado 1.0 el
        # orden lo decide la energia sola.
        if not 0.0 <= self.w_energy <= 1.0:
            raise ValueError(
                f"`w_energy` es la proporcion de mezcla entre timbre y energia: "
                f"va de 0 a 1, "
                f"recibi {self.w_energy!r}")
        if not 0.0 <= self.randomness <= 1.0:
            raise ValueError(f"`randomness` va de 0 a 1, recibí {self.randomness!r}")
        if self.top_k < 1:
            raise ValueError(f"`top_k` es al menos 1, recibí {self.top_k!r}")
        if self.artist_gap < 0:
            raise ValueError(f"`artist_gap` no puede ser negativo, recibí {self.artist_gap!r}")


# ---------------------------------------------------------------------------
# Similares
# ---------------------------------------------------------------------------

def _matrix(tracks: Sequence[Track]) -> np.ndarray:
    """Los embeddings apilados en una matriz (n, dim), float64.

    Los embeddings ya vienen z-scoreados y con norma 1 (`embeddings.normalize_matrix`),
    así que `matriz @ v` ES el vector de similitudes coseno. No se re-normaliza acá: si
    alguien pasa vectores sin normalizar, el número que sale es un producto punto y no un
    coseno, y taparlo con una normalización silenciosa escondería el error de origen.
    """
    if not tracks:
        return np.zeros((0, 0), dtype=np.float64)
    dims = {t.embedding.shape[0] for t in tracks}
    if len(dims) != 1:
        raise ValueError(
            f"los embeddings tienen que medir todos lo mismo para compararse, recibí {sorted(dims)}"
        )
    return np.vstack([np.asarray(t.embedding, dtype=np.float64) for t in tracks])


def similar(track: Track, biblioteca: Sequence[Track], n: int = 10) -> list[tuple[Track, float]]:
    """Los `n` tracks más parecidos a `track`, con su similitud coseno, de mayor a menor.

    Solo timbre: no mira BPM ni tonalidad (para eso está `build_set`). El propio `track`
    queda afuera por ruta — no por identidad de objeto, porque el mismo track cargado dos
    veces del store son dos objetos distintos y devolverlo como "su propio similar" sería
    ruido garantizado.

    Determinista: los empates de similitud se rompen por ruta ascendente, nunca por el
    orden en que vino la biblioteca. Dos scores exactamente iguales existen de verdad
    (tracks duplicados, o un embedding repetido), y ahí el resultado no puede depender de
    cómo se llenó una lista.

    Biblioteca vacía, `n <= 0`, o una biblioteca que solo contiene al propio track →
    lista vacía. `n` mayor que la biblioteca devuelve todo lo que hay, sin rellenar.
    """
    if n <= 0:
        return []

    propia = str(track.path)
    candidatos = [t for t in biblioteca if str(t.path) != propia]
    if not candidatos:
        return []

    emb = _matrix(candidatos)
    v = np.asarray(track.embedding, dtype=np.float64)
    if emb.shape[1] != v.shape[0]:
        raise ValueError(
            f"el embedding del track mide {v.shape[0]} y los de la biblioteca {emb.shape[1]}"
        )

    sims = emb @ v
    orden = sorted(range(len(candidatos)), key=lambda i: (-float(sims[i]), str(candidatos[i].path)))
    return [(candidatos[i], float(sims[i])) for i in orden[:n]]


# ---------------------------------------------------------------------------
# El set
# ---------------------------------------------------------------------------

def musical_fit(
    sim_seed: float,
    sim_prev: float,
    redundancy: float,
    energy: float,
    goal: float,
    config: RadioConfig,
) -> float:
    """Cuánto "pertenece" un candidato a este set, 0..1, IGNORANDO la mezclabilidad.

    Es el `encaje_musical` que consume `scoring.score`; la compuerta de BPM/key la aplica
    `scoring`, no esto.

    Junta cuatro cosas:

    - `sim_seed` — parecido con la semilla: mantiene la identidad del set.
    - `sim_prev` — parecido con el track anterior: continuidad de una transición a la otra.
    - `redundancy` — el mayor parecido contra los tracks ya elegidos DESPUÉS de la semilla,
      restado (Maximal Marginal Relevance). Sin esto el greedy se queda pegado en un racimo
      de tracks casi idénticos: el más parecido al anterior suele ser también parecido al
      anterior del anterior, y el set termina siendo cinco veces el mismo tema.

      La semilla queda FUERA de la redundancia a propósito, y no es un detalle: si entrara,
      cada candidato pagaría como penalización lo mismo que cobra en `sim_seed`, y con
      `mmr_lambda > w_seed` el término se da vuelta y el motor empieza a premiar a los que
      NO se parecen a la semilla. Eso es lo contrario de una radio. Parecerse a la semilla
      es la identidad del set (`w_seed`); repetir lo que ya sonó después es la redundancia.
    - la distancia a `goal`, el valor que pide la curva de energía en esa posición.

    El `tanh` comprime la parte de similitud ANTES de mezclarla con la energía. Sin eso
    (observación del boceto viejo) un par de similitudes altas dominan la suma y la curva
    de energía deja de pesar: la diferencia entre 0.95 y 0.99 de coseno no es cuatro veces
    más importante que estar a 0.4 de la energía buscada, y sin comprimir lo parece.

    El coseno puede ser negativo (dos timbres opuestos); el recorte a 0..1 evita que un
    encaje negativo dé vuelta el signo del score al multiplicarse por la mezclabilidad.
    """
    crudo = config.w_seed * sim_seed + config.w_prev * sim_prev - config.mmr_lambda * redundancy
    timbre = min(max(math.tanh(crudo), 0.0), 1.0)
    energia = 1.0 - abs(float(energy) - float(goal))   # 0..1: 1 = le pegó justo a la curva
    return (1.0 - config.w_energy) * timbre + config.w_energy * energia


def _pool(seed_track: Track, biblioteca: Sequence[Track]) -> list[Track]:
    """Candidatos: la biblioteca sin la semilla y sin rutas repetidas, ordenada por ruta.

    Ordenar por ruta acá es lo que hace determinista todo lo de abajo: el desempate de
    scores iguales pasa a ser "el de ruta menor" sin tener que ordenar de nuevo en cada
    posición. La deduplicación es por ruta y no por objeto por el mismo motivo que en
    `similar`: dos cargas del mismo archivo son dos objetos.
    """
    vistos = {str(seed_track.path)}
    unicos: list[Track] = []
    for t in sorted(biblioteca, key=lambda t: str(t.path)):
        clave = str(t.path)
        if clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(t)
    return unicos


def _artist_blocked(candidato: Track, elegidos: list[Track], gap: int) -> bool:
    """¿El candidato repite artista dentro de las últimas `gap` posiciones?

    Un track SIN artista nunca bloquea ni queda bloqueado: `None` no es un artista, es un
    dato que falta, y tratarlo como uno haría que dos tracks sin metadata se excluyeran
    entre sí. La comparación es case-insensitive con los bordes recortados, porque
    "Fran Perrotta" y "fran perrotta " son el mismo tipo y el metadata real viene sucio.
    """
    if gap <= 0 or not candidato.artist or not candidato.artist.strip():
        return False
    quien = candidato.artist.strip().casefold()
    for previo in elegidos[-gap:]:
        if previo.artist and previo.artist.strip().casefold() == quien:
            return True
    return False


def _transition(anterior: Track | None, elegido: Track, goal: float,
                encaje: float | None = None) -> Transition:
    """Arma el POR QUÉ de un paso. `anterior=None` → es la semilla."""
    if anterior is None:
        return Transition(
            from_bpm=None, to_bpm=float(elegido.bpm), bpm_delta_pct=None, bpm_octave="",
            from_key=None, to_key=elegido.key, key_relation="", key_compat=None,
            mixability=None, musical_fit=None, total=None,
            energy=float(elegido.energy), energy_goal=float(goal),
        )

    pct, lectura = bpm_delta_pct(anterior.bpm, elegido.bpm)
    mezcla = mezclabilidad(anterior.bpm, elegido.bpm, anterior.key, elegido.key)
    return Transition(
        from_bpm=float(anterior.bpm),
        to_bpm=float(elegido.bpm),
        bpm_delta_pct=pct,
        bpm_octave=lectura,
        from_key=anterior.key,
        to_key=elegido.key,
        key_relation=key_relation(anterior.key, elegido.key),
        key_compat=compat_camelot(anterior.key, elegido.key),
        mixability=mezcla,
        musical_fit=None if encaje is None else float(encaje),
        total=None if encaje is None else score(encaje, anterior.bpm, elegido.bpm,
                                                anterior.key, elegido.key),
        energy=float(elegido.energy),
        energy_goal=float(goal),
    )


def build_set(seed_track: Track, biblioteca: Sequence[Track],
              config: RadioConfig | None = None) -> RadioSet:
    """Arma el set arrancando por `seed_track`, greedy, una posición por vez.

    En cada posición:

    1. Candidatos = biblioteca − ya elegidos − los que repiten artista dentro de
       `artist_gap`.
    2. Se descarta todo lo que tenga `mezclabilidad == 0` — o sea, todo lo que esté fuera
       de ±8% de BPM (spec §4).
    3. De lo que queda, gana el mayor `scoring.score(encaje, ...)`. Con `randomness>0` se
       muestrea entre los `top_k` mejores en vez de tomar el primero.
    4. Si no quedó nada, **el set termina acá**: no se afloja la tolerancia (ver el docstring
       del módulo, punto 1). El motivo queda en `RadioSet.stop`.

    Devuelve un `RadioSet`, que arranca siempre por la semilla y trae el motivo de cada
    elección (§6). La semilla nunca se repite adentro del set, y ningún track aparece dos
    veces.
    """
    config = config or RadioConfig()
    rng = random.Random(config.seed)

    primera_meta = energy_target(0, config.length, config.curve)
    steps = [SetStep(seed_track, _transition(None, seed_track, primera_meta))]

    pool = _pool(seed_track, biblioteca)
    if not pool:
        return RadioSet(tuple(steps), STOP_BIBLIOTECA_VACIA,
                        "la biblioteca no aporta ningún track distinto de la semilla")
    if config.length == 1:
        return RadioSet(tuple(steps))

    emb = _matrix(pool)
    v_seed = np.asarray(seed_track.embedding, dtype=np.float64)
    if emb.shape[1] != v_seed.shape[0]:
        raise ValueError(
            f"el embedding de la semilla mide {v_seed.shape[0]} y los de la biblioteca "
            f"{emb.shape[1]}: no se pueden comparar"
        )
    sim_seed = emb @ v_seed
    # Redundancia MMR: el MAYOR parecido contra lo ya elegido DESPUÉS de la semilla (ver
    # `musical_fit`). En la primera posición todavía no hay nada que repetir, así que
    # arranca en cero y no en las similitudes contra la semilla.
    redundancy = np.zeros(len(pool), dtype=np.float64)

    elegidos: list[Track] = [seed_track]
    usados: set[int] = set()
    stop: str | None = None
    detalle = ""

    while len(steps) < config.length:
        anterior = elegidos[-1]
        goal = energy_target(len(steps), config.length, config.curve)
        sim_prev = emb @ np.asarray(anterior.embedding, dtype=np.float64)

        libres = 0          # candidatos que pasaron el artist_gap (para distinguir el corte)
        ranked: list[tuple[float, int, float]] = []   # (total, índice en pool, encaje)
        for i, cand in enumerate(pool):
            if i in usados:
                continue
            if _artist_blocked(cand, elegidos, config.artist_gap):
                continue
            libres += 1
            # Compuerta de §4: fuera de ±8% de BPM la mezclabilidad es 0 y el track no
            # existe para esta posición, por más que suene igual que el anterior.
            if mezclabilidad(anterior.bpm, cand.bpm, anterior.key, cand.key) <= 0.0:
                continue
            encaje = musical_fit(float(sim_seed[i]), float(sim_prev[i]), float(redundancy[i]),
                                 cand.energy, goal, config)
            total = score(encaje, anterior.bpm, cand.bpm, anterior.key, cand.key)
            ranked.append((total, i, encaje))

        if not ranked:
            if libres == 0:
                quedan = len(pool) - len(usados)
                stop = STOP_BIBLIOTECA_AGOTADA if quedan == 0 else STOP_ARTIST_GAP
                detalle = (
                    "no quedan tracks sin usar en la biblioteca" if quedan == 0 else
                    f"los {quedan} tracks que quedan repiten artista dentro de "
                    f"artist_gap={config.artist_gap}"
                )
            else:
                stop = STOP_SIN_MEZCLABLES
                detalle = (
                    f"ninguno de los {libres} candidatos entra en ±{TOLERANCIA_BPM:.0%} de "
                    f"{anterior.bpm:.1f} BPM; el set se corta en vez de aflojar la tolerancia "
                    f"(spec §4: 0% de transiciones fuera de ±{TOLERANCIA_BPM:.0%})"
                )
            break

        # Orden: mayor score primero; a igual score, el de índice menor en `pool`, que
        # está ordenado por ruta. El desempate es estable y no depende del recorrido.
        ranked.sort(key=lambda r: (-r[0], r[1]))
        _, idx, encaje = _elegir(ranked, config, rng)

        elegido = pool[idx]
        usados.add(idx)
        elegidos.append(elegido)
        steps.append(SetStep(elegido, _transition(anterior, elegido, goal, encaje)))
        redundancy = np.maximum(redundancy, emb @ np.asarray(elegido.embedding, dtype=np.float64))

    return RadioSet(tuple(steps), stop, detalle)


def _elegir(ranked: list[tuple[float, int, float]], config: RadioConfig,
            rng: random.Random) -> tuple[float, int, float]:
    """Elige un candidato de la lista ya ordenada.

    Con `randomness == 0` devuelve el primero sin tocar el RNG: determinismo puro, y
    además el generador no avanza, así que subir y bajar `randomness` entre corridas no
    corre la secuencia aleatoria de las demás posiciones.

    Con `randomness > 0` muestrea entre los mejores `ventana` candidatos, con probabilidad
    proporcional al score — no uniforme: la variedad no puede costar que entre un candidato
    mucho peor con la misma probabilidad que el mejor. La ventana crece linealmente de 1 a
    `top_k` con `randomness`.
    """
    if config.randomness <= 0.0:
        return ranked[0]

    ventana = 1 + int(round(config.randomness * (config.top_k - 1)))
    top = ranked[:max(1, min(ventana, len(ranked)))]
    pesos = [max(r[0], 0.0) for r in top]
    if sum(pesos) <= 0.0:
        # Todos los scores en 0: `rng.choices` reventaría con pesos que suman cero, y
        # sortear uniformemente sería inventar una preferencia donde no hay ninguna.
        return top[0]
    return rng.choices(top, weights=pesos, k=1)[0]


__all__ = [
    "RadioConfig",
    "RadioSet",
    "SetStep",
    "Transition",
    "bpm_delta_pct",
    "build_set",
    "key_relation",
    "musical_fit",
    "similar",
]
