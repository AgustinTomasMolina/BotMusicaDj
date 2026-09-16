"""Referencia congelada: `build_set` tal cual estaba en `f794211`, antes de vectorizarlo.

Es el ORÁCULO de `test_radio_equivalencia.py` (tarea 1.2). La vectorización de `build_set`
tenía una regla dura: cero cambio de comportamiento — mismos tracks, mismo orden, mismos
cortes, mismos motivos. La única forma de probar eso sin re-derivar el resultado con la
misma cuenta que se prueba es comparar contra el código VIEJO, que no comparte nada con el
nuevo camino vectorizado.

Copiado de `git show f794211:motor/radio.py` y `git show f794211:motor/scoring.py`: el loop
escalar de `build_set` y TODO lo que llama que no sea un tipo de datos. Se cambiaron solo
los nombres (sufijo `_viejo` donde chocaban) y los imports; la lógica es textual. Las
dataclasses (`Transition`, `SetStep`, `RadioSet`) se importan del módulo actual para que
`==` compare resultados de los dos lados; `energy_target` y `compat_camelot` también, porque
no son parte de lo que se optimizó (si cambian, cambian para los dos lados y ese cambio lo
tienen que atrapar sus propios tests).

NO se usa en producción y NO se "arregla": si el motor cambia de comportamiento A
PROPÓSITO, esta referencia deja de ser el contrato y se reemplaza por el commit nuevo,
diciendo en el mensaje qué cambió y por qué.
"""
from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence

import numpy as np

from motor.energia import energy_target
from motor.modelos import Track
from motor.radio import RadioConfig, RadioSet, SetStep, Transition
from motor.tonalidad import _parse_camelot, compat_camelot

# --- motor/scoring.py @ f794211 ------------------------------------------------------------

TOLERANCIA_BPM = 0.08
FACTORES_OCTAVA = (1.0, 2.0, 0.5)


def _rel(x: float, y: float) -> float:
    return abs(x - y) / max(x, y)


def _distancias_por_lectura(a: float, b: float) -> tuple[float, ...]:
    return tuple(_rel(a, f * b) for f in FACTORES_OCTAVA)


def _dist_bpm_relativa(a: float, b: float) -> float:
    if not (a > 0 and b > 0):
        return 1.0
    return min(_distancias_por_lectura(a, b))


def bpm_score(a: float, b: float, tol: float = TOLERANCIA_BPM) -> float:
    d = _dist_bpm_relativa(a, b)
    if d >= tol:
        return 0.0
    return 1.0 - d / tol


def mezclabilidad(bpm_a: float, bpm_b: float, camelot_a: str, camelot_b: str) -> float:
    bs = bpm_score(bpm_a, bpm_b)
    if bs == 0.0:
        return 0.0
    ks = compat_camelot(camelot_a, camelot_b)
    return (bs ** 1.0) * (ks ** 0.6)


def score(encaje_musical: float, bpm_a: float, bpm_b: float,
          camelot_a: str, camelot_b: str) -> float:
    return float(encaje_musical) * mezclabilidad(bpm_a, bpm_b, camelot_a, camelot_b)


# --- motor/radio.py @ f794211 --------------------------------------------------------------

MISMO_TIEMPO = "mismo tiempo"
DOBLE_TIEMPO = "doble tiempo"
MEDIO_TIEMPO = "medio tiempo"
_LECTURAS = {1.0: MISMO_TIEMPO, 2.0: DOBLE_TIEMPO, 0.5: MEDIO_TIEMPO}

STOP_BIBLIOTECA_VACIA = "biblioteca_vacia"
STOP_BIBLIOTECA_AGOTADA = "biblioteca_agotada"
STOP_SIN_MEZCLABLES = "sin_candidatos_mezclables"
STOP_ARTIST_GAP = "artist_gap"


def bpm_delta_pct(a: float, b: float) -> tuple[float, str]:
    if not (a > 0 and b > 0):
        return float("nan"), ""
    distancias = _distancias_por_lectura(a, b)
    idx = min(range(len(distancias)), key=lambda i: (distancias[i], i))
    efectivo = FACTORES_OCTAVA[idx] * b
    lectura = _LECTURAS[FACTORES_OCTAVA[idx]]
    pct = math.copysign(_dist_bpm_relativa(a, b) * 100.0, efectivo - a)
    return pct, lectura


def key_relation(a: str, b: str) -> str:
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


def _matrix(tracks: Sequence[Track]) -> np.ndarray:
    if not tracks:
        return np.zeros((0, 0), dtype=np.float64)
    dims = {t.embedding.shape[0] for t in tracks}
    if len(dims) != 1:
        raise ValueError(
            f"los embeddings tienen que medir todos lo mismo para compararse, recibí {sorted(dims)}"
        )
    return np.vstack([np.asarray(t.embedding, dtype=np.float64) for t in tracks])


def musical_fit(sim_seed: float, sim_prev: float, redundancy: float, energy: float,
                goal: float, config: RadioConfig) -> float:
    crudo = config.w_seed * sim_seed + config.w_prev * sim_prev - config.mmr_lambda * redundancy
    timbre = min(max(math.tanh(crudo), 0.0), 1.0)
    energia = 1.0 - abs(float(energy) - float(goal))
    return (1.0 - config.w_energy) * timbre + config.w_energy * energia


def _unicos_por_ruta(tracks: Iterable[Track]) -> list[Track]:
    por_ruta: dict[str, Track] = {}
    for t in tracks:
        clave = str(t.path)
        previo = por_ruta.get(clave)
        if previo is None:
            por_ruta[clave] = t
        elif previo != t:
            raise ValueError(
                f"la biblioteca trae dos tracks distintos con la misma ruta {clave!r}: "
                f"no hay criterio para elegir uno que no dependa del orden de la lista"
            )
    return [por_ruta[k] for k in sorted(por_ruta)]


def _pool(seed_track: Track, biblioteca: Sequence[Track]) -> list[Track]:
    propia = str(seed_track.path)
    return _unicos_por_ruta(t for t in biblioteca if str(t.path) != propia)


def _artist_blocked(candidato: Track, elegidos: list[Track], gap: int) -> bool:
    if gap <= 0 or not candidato.artist or not candidato.artist.strip():
        return False
    quien = candidato.artist.strip().casefold()
    for previo in elegidos[-gap:]:
        if previo.artist and previo.artist.strip().casefold() == quien:
            return True
    return False


def _transition(anterior: Track | None, elegido: Track, goal: float,
                encaje: float | None = None) -> Transition:
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
    redundancy = np.zeros(len(pool), dtype=np.float64)

    elegidos: list[Track] = [seed_track]
    usados: set[int] = set()
    stop: str | None = None
    detalle = ""

    while len(steps) < config.length:
        anterior = elegidos[-1]
        goal = energy_target(len(steps), config.length, config.curve)
        sim_prev = emb @ np.asarray(anterior.embedding, dtype=np.float64)

        libres = 0
        tapados = 0
        ranked: list[tuple[float, int, float]] = []
        for i, cand in enumerate(pool):
            if i in usados:
                continue
            mezcla = mezclabilidad(anterior.bpm, cand.bpm, anterior.key, cand.key)
            if _artist_blocked(cand, elegidos, config.artist_gap):
                tapados += mezcla > 0.0
                continue
            libres += 1
            if mezcla <= 0.0:
                continue
            encaje = musical_fit(float(sim_seed[i]), float(sim_prev[i]), float(redundancy[i]),
                                 cand.energy, goal, config)
            total = score(encaje, anterior.bpm, cand.bpm, anterior.key, cand.key)
            ranked.append((total, i, encaje))

        if not ranked:
            stop, detalle = _motivo_de_corte(anterior, len(pool) - len(usados), libres,
                                             tapados, config)
            break

        ranked.sort(key=lambda r: (-r[0], r[1]))
        _, idx, encaje = _elegir(ranked, config, rng)

        elegido = pool[idx]
        usados.add(idx)
        elegidos.append(elegido)
        steps.append(SetStep(elegido, _transition(anterior, elegido, goal, encaje)))
        redundancy = np.maximum(redundancy, emb @ np.asarray(elegido.embedding, dtype=np.float64))

    return RadioSet(tuple(steps), stop, detalle)


def _motivo_de_corte(anterior: Track, quedan: int, libres: int, tapados: int,
                     config: RadioConfig) -> tuple[str, str]:
    tol = f"±{TOLERANCIA_BPM:.0%}"
    if quedan == 0:
        return STOP_BIBLIOTECA_AGOTADA, "no quedan tracks sin usar en la biblioteca"
    bloqueados_total = quedan - libres
    if tapados > 0:
        return STOP_ARTIST_GAP, (
            f"de los {quedan} candidatos que quedan, los únicos que entran en {tol} de "
            f"{anterior.bpm:.1f} BPM ({tapados}) repiten artista dentro de "
            f"artist_gap={config.artist_gap}; bajar artist_gap los habilita"
        )
    detalle = (
        f"ninguno de los {quedan} candidatos que quedan entra en {tol} de "
        f"{anterior.bpm:.1f} BPM; el set se corta en vez de aflojar la tolerancia "
        f"(spec §4: 0% de transiciones fuera de {tol})"
    )
    if bloqueados_total:
        detalle += (f"; de esos, {bloqueados_total} además repiten artista dentro de "
                    f"artist_gap={config.artist_gap} (bajar el gap no ayuda)")
    return STOP_SIN_MEZCLABLES, detalle


def _elegir(ranked: list[tuple[float, int, float]], config: RadioConfig,
            rng: random.Random) -> tuple[float, int, float]:
    if config.randomness <= 0.0:
        return ranked[0]

    ventana = 1 + int(round(config.randomness * (config.top_k - 1)))
    top = ranked[:max(1, min(ventana, len(ranked)))]
    pesos = [max(r[0], 0.0) for r in top]
    if sum(pesos) <= 0.0:
        return top[0]
    return rng.choices(top, weights=pesos, k=1)[0]
