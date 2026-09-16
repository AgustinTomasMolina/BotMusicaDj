"""`build_set` vectorizado (tarea 1.2) contra el `build_set` escalar de `f794211`.

La regla de la optimización fue "cero cambio de comportamiento": los sets tienen que ser
IDÉNTICOS — mismos tracks en el mismo orden, mismo `stop` y `stop_detail`, mismas
`Transition` campo por campo con `==` (sin tolerancia: un bit distinto en un empate de score
cambia qué track entra, y eso ES un cambio de comportamiento).

El oráculo es el código viejo copiado en `_radio_f794211.py`, no una re-derivación del
resultado. Las bibliotecas son sintéticas y deterministas, armadas para pasar por todos los
caminos: cortes por `sin_candidatos_mezclables`, por `artist_gap` y por biblioteca agotada;
lecturas de octava; keys `?`, inválidas y en minúscula; BPM 0; artistas con mayúsculas y
espacios distintos; y clones exactos (mismo BPM, key, energía y embedding, otra ruta) que
empatan el score al bit y obligan a desempatar por ruta.

Los BPM, keys y energías son datos de entrada del caso, no mediciones (spec §5).
"""
import itertools
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from motor import radio
from motor.modelos import Track
from motor.radio import RadioConfig
from motor.tests import _radio_f794211 as viejo

CAMELOT = [f"{n}{lado}" for lado in ("A", "B") for n in range(1, 13)]
KEYS_RARAS = ["?", "8a", "13A", " 9B", ""]
ARTISTAS = ["Fran Perrotta", "fran perrotta ", "Amelie", "AMELIE", "Dax J", None, "   "]
DIM = 6


def _biblioteca(modo: str, seed: int, n: int = 40) -> list[Track]:
    """Biblioteca determinista para un `modo`:

    - `denso`: BPM entre 124 y 127 — casi todo mezcla; el set suele completarse.
    - `octavas`: BPM alrededor de 63, 126 y 252 — la compuerta decide por medio/doble tiempo.
    - `disperso`: BPM de 60 a 200 — el set se corta por `sin_candidatos_mezclables`.
    - `artistas`: BPM denso pero 3 artistas — el set se corta por `artist_gap`.
    - `clones`: cada track tiene 2 copias exactas con otra ruta — empates de score al bit.
    - `chica`: 7 tracks de BPM denso y sin artista — un set de 20 agota la biblioteca.
    """
    if modo == "chica":
        n = 7
    rng = np.random.default_rng(seed)
    bases = rng.standard_normal((5, DIM))
    tracks: list[Track] = []
    i = 0
    while len(tracks) < n:
        if modo == "octavas":
            bpm = float(rng.choice([63.0, 126.0, 252.0]) * rng.uniform(0.95, 1.05))
        elif modo == "disperso":
            bpm = float(rng.uniform(60.0, 200.0))
        else:
            bpm = float(rng.uniform(124.0, 127.0))
        bpm = round(bpm * 2) / 2                     # medios BPM: repetidos exactos
        if rng.random() < 0.04:
            bpm = 0.0                                # sin BPM medido
        key = str(rng.choice(CAMELOT)) if rng.random() > 0.15 else str(rng.choice(KEYS_RARAS))
        energy = float(rng.choice([0.0, 0.25, 0.5, 0.75, 1.0]))
        # 60% con un embedding repetido: similitudes exactamente iguales entre tracks.
        vec = bases[rng.integers(0, len(bases))] if rng.random() < 0.6 else rng.standard_normal(DIM)
        vec = vec / np.linalg.norm(vec)
        if modo == "chica":
            artist = None
        elif modo == "artistas":
            artist = ["Uno", "Dos", "Tres"][int(rng.integers(0, 3))]
        else:
            artist = ARTISTAS[int(rng.integers(0, len(ARTISTAS)))]
        copias = 3 if modo == "clones" else 1
        for _ in range(copias):
            tracks.append(Track(
                path=Path("/equiv") / f"{modo}-{seed}-{i:04d}.wav", duration=300.0, bpm=bpm,
                key=key, energy=energy, embedding=vec.copy(), license="CC-BY",
                source_url=f"https://example.org/{i}", artist=artist, title=f"t{i}"))
            i += 1
    return tracks[:n]


MODOS = ("denso", "octavas", "disperso", "artistas", "clones", "chica")
SEEDS_BIBLIOTECA = (1, 2)

CONFIGS = [
    RadioConfig(length=length, curve=curve, artist_gap=gap, mmr_lambda=mmr,
                randomness=randomness, seed=17)
    for length, curve, gap, mmr, randomness in itertools.product(
        (1, 5, 20), ("peak", "warmup", "flat"), (0, 4), (0.0, 0.3), (0.0, 0.4, 1.0))
]


def _diferencias(a, b) -> list[str]:
    """Qué difiere entre dos `RadioSet`, en castellano, para el mensaje del assert."""
    out = []
    if [s.track.path.name for s in a] != [s.track.path.name for s in b]:
        out.append(f"tracks {[s.track.path.name for s in a]} != {[s.track.path.name for s in b]}")
    if (a.stop, a.stop_detail) != (b.stop, b.stop_detail):
        out.append(f"corte {(a.stop, a.stop_detail)!r} != {(b.stop, b.stop_detail)!r}")
    for k, (sa, sb) in enumerate(zip(a, b, strict=False)):
        if sa.transition != sb.transition:
            out.append(f"transición {k}: {sa.transition} != {sb.transition}")
    return out


# Qué caminos recorrió la grilla, para verificar al final que no fue una grilla ciega.
_COBERTURA: Counter = Counter()


@pytest.mark.parametrize("modo", MODOS)
@pytest.mark.parametrize("seed_biblioteca", SEEDS_BIBLIOTECA)
def test_build_set_vectorizado_da_lo_mismo_que_f794211(modo, seed_biblioteca):
    biblioteca = _biblioteca(modo, seed_biblioteca)
    semillas = [biblioteca[0], biblioteca[len(biblioteca) // 2]]
    fallas = []
    for semilla, config in itertools.product(semillas, CONFIGS):
        esperado = viejo.build_set(semilla, biblioteca, config)
        obtenido = radio.build_set(semilla, biblioteca, config)
        dif = _diferencias(esperado, obtenido)
        if dif:
            fallas.append(f"{semilla.path.name} {config}: " + " | ".join(dif[:3]))
        _COBERTURA[esperado.stop] += 1
    assert not fallas, (f"{len(fallas)} de {len(semillas) * len(CONFIGS)} sets difieren del "
                        f"build_set de f794211; primero: {fallas[0]}")


def test_la_grilla_de_equivalencia_recorre_todos_los_cortes():
    """Si la grilla de arriba nunca cortara por artist_gap (por ejemplo), la equivalencia
    de ese camino no estaría probada aunque el test diera verde. Corre después de la grilla
    (pytest respeta el orden del archivo); si se corre solo, la arma de nuevo."""
    if not _COBERTURA:
        for modo, seed in itertools.product(MODOS, SEEDS_BIBLIOTECA):
            biblioteca = _biblioteca(modo, seed)
            for config in CONFIGS:
                _COBERTURA[viejo.build_set(biblioteca[0], biblioteca, config).stop] += 1
    for corte in (None, viejo.STOP_SIN_MEZCLABLES, viejo.STOP_ARTIST_GAP,
                  viejo.STOP_BIBLIOTECA_AGOTADA):
        assert _COBERTURA[corte] > 0, f"ningún set de la grilla terminó con stop={corte!r}"


def test_hay_empates_exactos_de_score_en_la_grilla():
    """Los clones tienen que producir empates al bit en el ranking; si no, el desempate por
    ruta no se está ejercitando. Se cuenta con las funciones VIEJAS: en la primera posición
    de un set armado desde un clon, sus copias empatan entre sí."""
    biblioteca = _biblioteca("clones", 1)
    semilla = biblioteca[0]
    config = RadioConfig(length=2, artist_gap=0)
    rset = viejo.build_set(semilla, biblioteca, config)
    elegido = rset[1].track
    empatados = [
        c for c in biblioteca
        if c.path != semilla.path and c.bpm == elegido.bpm and c.key == elegido.key
        and c.energy == elegido.energy and np.array_equal(c.embedding, elegido.embedding)
    ]
    assert len(empatados) >= 2, "el elegido no tiene clones: no hubo empate que desempatar"
    assert elegido.path == min(c.path for c in empatados), (
        f"con score empatado ganó {elegido.path.name} y no la ruta menor")


@pytest.mark.parametrize("perfil", ["realista", "peor_caso"])
def test_equivalencia_a_escala_con_la_biblioteca_del_benchmark(perfil):
    """La misma comparación sobre 1.500 tracks del generador de `benchmark/latencia_radio`
    (keys `?`, BPM 0, octavas, artistas repetidos): lo que se mide es lo que se prueba."""
    from benchmark.latencia_radio import biblioteca_sintetica

    biblioteca = biblioteca_sintetica(1500, seed=5, perfil=perfil)
    semilla = next(t for t in biblioteca if 130.0 <= t.bpm <= 134.0) \
        if perfil == "realista" else biblioteca[0]
    for config in (RadioConfig(length=20), RadioConfig(length=20, randomness=0.7, seed=3),
                   RadioConfig(length=20, curve="warmup", artist_gap=0, mmr_lambda=0.0)):
        esperado = viejo.build_set(semilla, biblioteca, config)
        obtenido = radio.build_set(semilla, biblioteca, config)
        dif = _diferencias(esperado, obtenido)
        assert not dif, f"{perfil} {config}: {dif[:3]}"
