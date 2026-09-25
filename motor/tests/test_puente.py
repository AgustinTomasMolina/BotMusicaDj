"""Tests del puente entre dos tracks (`motor.puente.build_bridge`).

Como los de la radio, todo acá es lógica pura sobre `Track` armados a mano: no se mide
audio, así que ningún BPM ni key es "detectado" — son datos de entrada del caso (spec §5
prohíbe inventar valores MEDIDOS, y no hay ninguno). La prueba punta a punta con audio
sintético está en `test_cli.py` (`test_puente_*`).

El ±8% se verifica con `pitch_real`, escrito en `test_radio.py` DE OTRA FORMA que la
compuerta de `scoring`: un test que recalcula con la misma cuenta que prueba no prueba nada.
"""
import itertools
import math
import os
import random
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from motor.modelos import DURACION_MINIMA_TRACK_S  # noqa: E402
from motor.puente import (  # noqa: E402
    MAX_INTERMEDIATES_CAP,
    STOP_EXTREMO_NO_TRACK,
    STOP_EXTREMO_SIN_BPM,
    STOP_LARGO,
    STOP_MISMO_TRACK,
    STOP_SIN_CAMINO,
    _cheapest_path,
    _Graph,
    _hops,
    build_bridge,
)
from motor.radio import bpm_delta_pct, key_relation  # noqa: E402
from motor.scoring import mezclabilidad  # noqa: E402
from motor.tests.test_radio import CAMELOT, pitch_real, track  # noqa: E402


def nombres(tracks) -> list[str]:
    return [t.path.name for t in tracks]


def costo_exhaustivo(a, b, biblio, lo, hi):
    """El mínimo de `Σ -log(mezclabilidad)` probando TODAS las secuencias de intermedios
    distintos, con la función escalar de `scoring` (la búsqueda usa la vectorizada).
    `(costo, secuencia)` o `(None, None)` si ninguna pasa la compuerta en todos los saltos."""
    mejor = (None, None)
    candidatos = [t for t in biblio if t.duration >= DURACION_MINIMA_TRACK_S]
    for k in range(lo, hi + 1):
        for perm in itertools.permutations(candidatos, k):
            seq = [a, *perm, b]
            ms = [mezclabilidad(x.bpm, y.bpm, x.key, y.key) for x, y in zip(seq, seq[1:], strict=False)]
            if min(ms) <= 0.0:
                continue
            c = sum(-math.log(m) for m in ms)
            if mejor[0] is None or c < mejor[0]:
                mejor = (c, perm)
    return mejor


def saltos_en_tolerancia(puente) -> list[str]:
    """Los saltos del puente que quedan FUERA de ±8% (debería ser una lista vacía)."""
    ts = puente.tracks
    return [f"{x.bpm} → {y.bpm} ({pitch_real(x.bpm, y.bpm):.2%})"
            for x, y in zip(ts, ts[1:], strict=False) if pitch_real(x.bpm, y.bpm) >= 0.08]


# ---------------------------------------------------------------------------
# Largo del puente
# ---------------------------------------------------------------------------

def _biblio_128():
    """Seis tracks entre 126 y 130, misma key: todo mezcla con todo."""
    return [track(f"t{i}.wav", 126.0 + 0.8 * i) for i in range(6)]


def test_puente_directo_igual_devuelve_3_o_4_intermedios():
    """A y B mezclan entre sí (mismo BPM y key: salto perfecto), pero se pidió un puente de
    3 a 4 temas: el default no devuelve el salto directo."""
    a, b = track("a.wav", 128.0), track("b.wav", 128.0)
    p = build_bridge(a, b, _biblio_128())
    assert p.found, p.stop_detail
    assert 3 <= len(p.intermediates) <= 4, f"{len(p.intermediates)} intermedios: {p.reasons()}"
    assert p.tracks[0] is a and p.tracks[-1] is b, nombres(p.tracks)
    assert not saltos_en_tolerancia(p), saltos_en_tolerancia(p)


def test_puente_directo_con_minimo_cero_es_el_salto_directo():
    """Con `min_intermediates=0`, el salto directo A → B (costo 0: mismo BPM y key) gana,
    y a igual costo gana el de menos saltos."""
    a, b = track("a.wav", 128.0), track("b.wav", 128.0)
    biblio = [track("clon.wav", 128.0), *_biblio_128()]   # clon: A → clon → B también cuesta 0
    p = build_bridge(a, b, biblio, 0, 4)
    assert nombres(p.tracks) == ["a.wav", "b.wav"], p.reasons()
    assert p.cost == 0.0, p.cost


def test_el_rango_de_intermedios_se_respeta_exacto():
    a, b = track("a.wav", 126.0), track("b.wav", 130.0)
    for lo, hi in ((1, 1), (2, 2), (3, 3), (4, 4), (2, 5)):
        p = build_bridge(a, b, _biblio_128(), lo, hi)
        assert p.found, (lo, hi, p.stop_detail)
        assert lo <= len(p.intermediates) <= hi, (lo, hi, nombres(p.intermediates))


@pytest.mark.parametrize("lo, hi", [(-1, 3), (4, 3), (0, MAX_INTERMEDIATES_CAP + 1)])
def test_rango_invalido_es_error(lo, hi):
    with pytest.raises(ValueError):
        build_bridge(track("a.wav", 128.0), track("b.wav", 128.0), _biblio_128(), lo, hi)


# ---------------------------------------------------------------------------
# Octava, costo, sin camino
# ---------------------------------------------------------------------------

def test_puente_por_octava_70_a_140():
    """De 70 a 140: los intermedios están todos del lado de 70 y el último salto cruza la
    octava. El orden óptimo NO es la escalera 71, 72, 73: termina en 72 porque 140 leído a
    medio tiempo (70) está a 2.8% de 72 y a 4.1% de 73 — 71, 73, 72 cuesta menos. El motivo
    del cruce tiene que decir la lectura, y ningún salto se sale de ±8% (pitch real)."""
    a, b = track("a70.wav", 70.0), track("b140.wav", 140.0)
    biblio = [track("x71.wav", 71.0), track("x72.wav", 72.0), track("x73.wav", 73.0),
              track("lejos100.wav", 100.0)]
    p = build_bridge(a, b, biblio, 3, 3)
    assert p.found, p.stop_detail
    assert nombres(p.intermediates) == ["x71.wav", "x73.wav", "x72.wav"], p.reasons()
    assert not saltos_en_tolerancia(p), saltos_en_tolerancia(p)
    ultimo = p.reasons()[-1]
    pct, lectura = bpm_delta_pct(72.0, 140.0)
    assert lectura == "medio tiempo", lectura
    assert ultimo == f"{pct:+.1f}% BPM (medio tiempo) | 8A → 8A (mismo)", \
        f"el cruce de octava no se dice: {ultimo!r}"


def test_el_costo_prefiere_la_rampa_pareja_al_salto_al_borde():
    """El caso que justifica `-log(m)` sobre `1 - m` (punto 1 del docstring del módulo).

    De 128 a 137.6 con tres intermedios: o tres clones de 128 y un salto de +7.0% al borde
    de la compuerta, o una rampa de ~+1.8% por salto. `Σ(1-m)` elige el salto (se verifica
    acá abajo, para que la justificación no sea de palabra); el puente elige la rampa."""
    a, b = track("a.wav", 128.0), track("b.wav", 137.6)
    clones = [track(f"c{i}.wav", 128.0) for i in range(3)]
    rampa = [track("r1.wav", 130.3), track("r2.wav", 132.7), track("r3.wav", 135.1)]
    biblio = clones + rampa

    def suma(seq, f):
        ts = [a, *seq, b]
        return sum(f(mezclabilidad(x.bpm, y.bpm, x.key, y.key)) for x, y in zip(ts, ts[1:], strict=False))

    assert suma(clones, lambda m: 1 - m) < suma(rampa, lambda m: 1 - m), \
        "el caso ya no distingue: 1-m también preferiría la rampa"
    assert suma(rampa, lambda m: -math.log(m)) < suma(clones, lambda m: -math.log(m))

    p = build_bridge(a, b, biblio, 3, 3)
    assert nombres(p.intermediates) == ["r1.wav", "r2.wav", "r3.wav"], p.reasons()
    assert p.cost == pytest.approx(suma(rampa, lambda m: -math.log(m)), abs=1e-12), p.cost


def test_sin_camino_no_afloja_la_compuerta():
    """128 y 174 sin nada en el medio (ni 87 para el medio tiempo): no hay puente a ningún
    largo, y se dice así, con los dos BPM. Hay un 139 a 8.6% del último 128: si alguien
    aflojara la compuerta, aparecería."""
    a, b = track("a.wav", 128.0), track("b.wav", 174.0)
    biblio = [track("h1.wav", 127.0), track("h2.wav", 129.0), track("h3.wav", 128.5),
              track("casi.wav", 140.4), track("d1.wav", 173.0)]
    p = build_bridge(a, b, biblio)
    assert (p.stop, p.steps, p.cost) == (STOP_SIN_CAMINO, (), None), (p.stop, p.reasons())
    assert "128.0 BPM y 174.0 BPM" in p.stop_detail and "±8%" in p.stop_detail, p.stop_detail
    assert ("desde 128.0 BPM, saltando dentro de ±8%, se llega a 3 tracks; a 174.0 BPM se "
            "llega desde 1 track; los dos grupos no se tocan") in p.stop_detail, p.stop_detail


def test_sin_camino_cuenta_los_dos_grupos():
    """Al revés que el anterior: los números de cada lado son los de ESE lado."""
    a, b = track("a.wav", 174.0), track("b.wav", 128.0)
    biblio = [track("d1.wav", 173.0), *(track(f"h{i}.wav", 126.0 + i) for i in range(4))]
    p = build_bridge(a, b, biblio)
    assert p.stop == STOP_SIN_CAMINO, p.stop
    assert ("desde 174.0 BPM, saltando dentro de ±8%, se llega a 1 track; a 128.0 BPM se "
            "llega desde 4 tracks; los dos grupos no se tocan") in p.stop_detail, p.stop_detail


def test_sin_camino_con_un_extremo_aislado():
    """Nadie entra en ±8% de B: el motivo lo dice así, sin contar grupos."""
    a, b = track("a.wav", 128.0), track("b.wav", 200.0)
    p = build_bridge(a, b, _biblio_128())
    assert p.stop == STOP_SIN_CAMINO, p.stop
    assert "ningún track de la biblioteca entra en ±8% de 200.0 BPM" in p.stop_detail, \
        p.stop_detail


@pytest.mark.parametrize("cual", ["origen", "destino"])
def test_un_extremo_sin_bpm_medido_tiene_corte_propio(cual):
    """BPM 0.0 = el análisis no encontró pulso. Decir "ningún track entra en ±8% de 0.0 BPM"
    es cierto pero esconde la causa: el corte lo dice."""
    mudo = track("mudo.wav", 0.0)
    otro = track("otro.wav", 128.0)
    a, b = (mudo, otro) if cual == "origen" else (otro, mudo)
    p = build_bridge(a, b, _biblio_128())
    assert (p.stop, p.steps) == (STOP_EXTREMO_SIN_BPM, ()), (p.stop, p.stop_detail)
    assert "mudo.wav no tiene BPM medido (0.0" in p.stop_detail, p.stop_detail


def test_sin_intermedios_suficientes_dice_cuantos_necesita():
    """Hay camino con 1 intermedio pero se piden 3 y no hay tracks para tanto: el motivo es
    `largo_fuera_de_rango`, distinto de `sin_camino`, porque se arregla pidiendo menos."""
    a, b = track("a.wav", 118.0), track("b.wav", 130.0)
    biblio = [track("m.wav", 124.0)]
    p = build_bridge(a, b, biblio, 3, 4)
    assert p.stop == STOP_LARGO, (p.stop, p.stop_detail)
    assert "con 1 intermedio, pero no con entre 3 y 4" in p.stop_detail, p.stop_detail
    assert nombres(build_bridge(a, b, biblio, 1, 1).intermediates) == ["m.wav"]


def test_el_camino_mas_corto_necesita_mas_de_lo_pedido():
    """Una escalera de 5 escalones de ~6%: de la punta a la otra hacen falta 5 intermedios,
    y con `max=4` no hay puente. El detalle dice cuántos necesita."""
    bpms = [100.0 * 1.06 ** i for i in range(7)]
    a, b = track("a.wav", bpms[0]), track("b.wav", bpms[-1])
    biblio = [track(f"e{i}.wav", x) for i, x in enumerate(bpms[1:-1], 1)]
    p = build_bridge(a, b, biblio, 3, 4)
    assert p.stop == STOP_LARGO, p.stop
    assert "necesita 5 intermedios y se pidieron entre 3 y 4" in p.stop_detail, p.stop_detail
    assert nombres(build_bridge(a, b, biblio, 3, 5).intermediates) == \
        ["e1.wav", "e2.wav", "e3.wav", "e4.wav", "e5.wav"]


# ---------------------------------------------------------------------------
# Fragmentos y extremos
# ---------------------------------------------------------------------------

def test_un_fragmento_nunca_es_intermedio():
    """El único camino de 120 a 135 pasa por un 127.5, pero dura 10 s: no hay puente. El
    mismo audio con duración de track sí lo arma. El corte cuenta el fragmento ignorado."""
    a, b = track("a.wav", 120.0), track("b.wav", 135.0)
    corto = track("puente.wav", 127.5, duration=10.0)
    p = build_bridge(a, b, [corto], 1, 1)
    assert p.stop == STOP_SIN_CAMINO, (p.stop, p.reasons())
    assert p.fragments == 1 and "1 archivo de menos de 90 s" in p.stop_detail, p.stop_detail
    largo = track("puente.wav", 127.5)
    assert nombres(build_bridge(a, b, [largo], 1, 1).intermediates) == ["puente.wav"]


@pytest.mark.parametrize("cual", ["origen", "destino"])
def test_un_extremo_fragmento_no_arma_puente(cual):
    loop = track("loop.wav", 128.0, duration=8.0)
    otro = track("otro.wav", 128.0)
    a, b = (loop, otro) if cual == "origen" else (otro, loop)
    p = build_bridge(a, b, _biblio_128())
    assert (p.stop, p.steps) == (STOP_EXTREMO_NO_TRACK, ()), (p.stop, p.reasons())
    assert "loop.wav dura 8.0 s" in p.stop_detail, p.stop_detail


def test_origen_y_destino_iguales():
    a = track("a.wav", 128.0)
    p = build_bridge(a, a, _biblio_128())
    assert p.stop == STOP_MISMO_TRACK, p.stop


# ---------------------------------------------------------------------------
# El porqué, determinismo, sin repetidos
# ---------------------------------------------------------------------------

def test_cada_paso_trae_el_motivo_de_la_radio():
    a, b = track("a.wav", 124.0, "8A"), track("b.wav", 132.0, "10A")
    biblio = [track("x.wav", 126.0, "9A"), track("y.wav", 128.0, "9A"),
              track("z.wav", 130.0, "10A")]
    p = build_bridge(a, b, biblio, 3, 3)
    ts = p.tracks
    assert p.reasons()[0] == "semilla | 8A | 124.0 BPM", p.reasons()[0]
    for motivo, x, y in zip(p.reasons()[1:], ts[:-1], ts[1:], strict=True):
        pct, _ = bpm_delta_pct(x.bpm, y.bpm)
        assert motivo == f"{pct:+.1f}% BPM | {x.key} → {y.key} ({key_relation(x.key, y.key)})", \
            motivo
    total = sum(-math.log(s.transition.mixability) for s in p.steps[1:])
    assert p.cost == pytest.approx(total, abs=1e-12), (p.cost, total)


def test_el_orden_de_la_biblioteca_no_cambia_el_puente():
    """Spec §5: mismo input, mismo puente. Con empates exactos (clones) a propósito, que es
    donde un desempate por orden de lista se notaría."""
    a, b = track("a.wav", 124.0), track("b.wav", 131.0)
    biblio = [track(f"t{i:02d}.wav", 124.0 + 0.5 * (i // 2)) for i in range(16)]
    esperado = nombres(build_bridge(a, b, biblio).tracks)
    rng = random.Random(3)
    for _ in range(10):
        mezcla = biblio[:]
        rng.shuffle(mezcla)
        assert nombres(build_bridge(a, b, mezcla).tracks) == esperado


def test_si_la_relajacion_repite_un_track_se_busca_sin_repetidos():
    """A, B y dos clones perfectos, y un tercer track con key lejana. La relajación (sin
    exigir tracks distintos) encuentra A → x → y → x → B a costo 0; el puente tiene que
    usar el tercero en vez de repetir."""
    a, b = track("a.wav", 128.0), track("b.wav", 128.0)
    biblio = [track("x.wav", 128.0), track("y.wav", 128.0), track("z.wav", 129.0, "3B")]
    p = build_bridge(a, b, biblio, 3, 3)
    assert p.found, p.stop_detail
    assert sorted(nombres(p.intermediates)) == ["x.wav", "y.wav", "z.wav"], nombres(p.intermediates)


def _biblio_azar(rng: random.Random, n: int, bpm_lo=90.0, bpm_hi=180.0, fragmentos=True):
    return [track(f"r{i:04d}.wav", round(rng.uniform(bpm_lo, bpm_hi), 1), rng.choice(CAMELOT + ["?"]),
                  duration=(30.0 if fragmentos and rng.random() < 0.1 else 300.0))
            for i in range(n)]


def test_grilla_grande_ningun_salto_fuera_de_tolerancia():
    """400 tracks al azar (con semilla) y 60 pares: todo puente hallado tiene 3 o 4
    intermedios distintos, ninguno es fragmento ni A ni B, y ningún salto se sale de ±8%
    contra el pitch real."""
    rng = random.Random(20260925)
    # Densa (casi todo par tiene puente) y rala (20 tracks de 60 a 200 BPM: muchos pares
    # sin puente legal, que es donde una compuerta aflojada se delata inventando uno).
    for n, lo_bpm, hi_bpm, min_hallados, min_sin in ((400, 90.0, 180.0, 40, 0),
                                                     (20, 60.0, 200.0, 5, 5)):
        biblio = _biblio_azar(rng, n, lo_bpm, hi_bpm)
        largos = [t for t in biblio if t.duration >= DURACION_MINIMA_TRACK_S]
        hallados = sin = 0
        for _ in range(60):
            a, b = rng.sample(largos, 2)
            p = build_bridge(a, b, biblio)
            if not p.found:
                assert p.stop in (STOP_SIN_CAMINO, STOP_LARGO), p.stop
                sin += 1
                continue
            hallados += 1
            medio = p.intermediates
            assert 3 <= len(medio) <= 4, len(medio)
            assert len({t.path for t in medio}) == len(medio), nombres(medio)
            assert all(t.duration >= DURACION_MINIMA_TRACK_S for t in medio), nombres(medio)
            assert not {a.path, b.path} & {t.path for t in medio}, nombres(medio)
            assert not saltos_en_tolerancia(p), (a.bpm, b.bpm, saltos_en_tolerancia(p))
        assert hallados >= min_hallados and sin >= min_sin, \
            f"biblioteca de {n}: {hallados} con puente y {sin} sin: el caso no ejercita lo que dice"


def test_el_costo_es_el_minimo_contra_busqueda_exhaustiva():
    """Bibliotecas chicas al azar: el costo del puente es EXACTAMENTE el mínimo de todas las
    secuencias posibles (con la mezclabilidad escalar), y si no hay ninguna, no hay puente.
    BPM en un rango angosto para que haya muchos caminos y muchos casi-empates."""
    rng = random.Random(7)
    comparados = 0
    for _ in range(40):
        biblio = _biblio_azar(rng, 9, 118.0, 138.0)
        largos = [t for t in biblio if t.duration >= DURACION_MINIMA_TRACK_S]
        a, b = rng.sample(largos, 2)
        resto = [t for t in biblio if t is not a and t is not b]
        lo, hi = rng.choice([(3, 4), (1, 2), (0, 3), (4, 4)])
        costo, _ = costo_exhaustivo(a, b, resto, lo, hi)
        p = build_bridge(a, b, biblio, lo, hi)
        if costo is None:
            assert not p.found, f"la exhaustiva no encontró nada y el puente sí: {p.reasons()}"
            continue
        comparados += 1
        assert p.found, p.stop_detail
        assert p.cost == pytest.approx(costo, abs=1e-9), (p.cost, costo, nombres(p.tracks))
    assert comparados >= 30, comparados


# Keys concentradas en una región de la rueda (1A..6A) y BPM con clones y octavas (62/64
# ↔ 125-131 ↔ 256): es donde el camino más barato cambia de key de a un vecino por vez
# (8A → 9A → 10A cuesta 0.268, el salto directo 0.416) y donde la poda por dominancia
# tiene que mirar varios pasos hacia adelante. La auditoría de 35ce676 encontró acá las
# dos mutaciones que la versión anterior de estos tests no veía.
BPM_CADENA = [62.0, 64.0, 124.0, 125.0, 126.0, 126.0, 128.0, 131.0, 256.0]
KEYS_CADENA = ["1A", "2A", "3A", "4A", "5A", "6A", "3B", "8B"]


def _biblio_cadena(rng: random.Random, n: int, pref: str = "k"):
    return [track(f"{pref}{i}.wav", rng.choice(BPM_CADENA), rng.choice(KEYS_CADENA))
            for i in range(n)]


@pytest.mark.parametrize("distribucion", ["clones_keys_repetidas", "cadena_de_keys"])
def test_la_busqueda_sin_repetidos_es_exacta_por_si_sola(distribucion):
    """`_cheapest_path` (la que corre cuando la relajación repite un track) contra la
    exhaustiva, directo: si no, casi nunca se ejercitaría y una poda mal hecha pasaría
    desapercibida. Con clones y keys repetidas para que la poda por dominancia trabaje, y
    con keys encadenadas + octavas para que tenga que mirar varios pasos adelante."""
    rng = random.Random(11)
    for _ in range(60):
        if distribucion == "cadena_de_keys":
            biblio = _biblio_cadena(rng, 8, "q")
        else:
            biblio = [track(f"q{i}.wav", rng.choice([124.0, 125.0, 126.0, 128.0, 131.0]),
                            rng.choice(["8A", "9A", "3B"])) for i in range(9)]
        a, b = biblio[0], biblio[1]
        resto = biblio[2:]
        lo, hi = rng.choice([(3, 4), (2, 2), (0, 4), (4, 5), (3, 3)])
        costo, _ = costo_exhaustivo(a, b, resto, lo, hi)
        hallado = _cheapest_path(a, _Graph(sorted(resto, key=lambda t: str(t.path)), b, hi), lo, hi)
        if costo is None:
            assert hallado is None, hallado
            continue
        indices, c = hallado
        assert len(set(indices)) == len(indices) and lo <= len(indices) <= hi, indices
        assert c == pytest.approx(costo, abs=1e-9), (c, costo)


def test_la_cota_nunca_supera_el_costo_real():
    """La cota de A* (`_lower_bounds`) tiene que ser <= el costo real de lo que falta, para
    todo track y todo largo restante. El costo real se calcula acá por programación dinámica
    sobre la mezclabilidad escalar (recorridos de 1 a `r` saltos, que cuestan igual o menos
    que los caminos sin repetidos: si la cota vale para ellos, vale para el puente).

    Si la cota se pasara, A* podría devolver un puente que no es el más barato, y los tests
    exhaustivos solo lo verían si el caso justo aparece en su muestra. Además se exige que
    la cota trabaje (no sea 0 en todos lados): una cota nula es válida e inútil."""
    rng = random.Random(5)
    hi = 4
    positivas = 0
    for i in range(60):
        biblio = (_biblio_azar(rng, 7, 60.0, 190.0, fragmentos=False) if i % 2 else
                  _biblio_cadena(rng, 7))
        b, pool = biblio[0], sorted(biblio[1:], key=lambda t: str(t.path))
        cota = _Graph(pool, b, hi).bound

        def c(x, y):
            m = mezclabilidad(x.bpm, y.bpm, x.key, y.key)
            return -math.log(m) if m > 0.0 else math.inf

        exacto = [c(w, b) for w in pool]          # s = 1
        hasta = [exacto[:]]                       # hasta[s-1][w] = mínimo con <= s saltos
        for _s in range(2, hi + 1):
            exacto = [min((c(w, x) + exacto[j] for j, x in enumerate(pool) if x is not w),
                          default=math.inf) for w in pool]
            hasta.append([min(p, q) for p, q in zip(hasta[-1], exacto, strict=True)])
        for h in range(1, hi + 1):
            r = hi - h + 1
            for j, w in enumerate(pool):
                real = hasta[r - 1][j]
                assert cota[h, j] <= real + 1e-12, (w.bpm, w.key, b.bpm, b.key, r, cota[h, j], real)
                positivas += 0.0 < cota[h, j] < math.inf
    assert positivas > 100, f"la cota fue positiva y finita solo {positivas} veces: no poda nada"


def test_regresion_cota_de_key_por_la_rueda():
    """Caso de la auditoría: si la cota de key solo mirara el salto directo de key (y no el
    camino por vecinos), dejaría de ser una cota y el puente saldría 0.5943 en vez de 0.5830."""
    a, b = track("a.wav", 125.0, "2A"), track("b.wav", 126.0, "3B")
    pool = [track(f"p{i}.wav", bpm, key) for i, (bpm, key) in enumerate(
        [(125.0, "7A"), (125.0, "2A"), (256.0, "1A"), (64.0, "1A"), (62.0, "2A"),
         (125.0, "4A"), (126.0, "3A")])]
    costo, _ = costo_exhaustivo(a, b, pool, 3, 4)
    p = build_bridge(a, b, pool, 3, 4)
    assert p.cost == pytest.approx(costo, abs=1e-12), (p.cost, costo, nombres(p.tracks))
    assert costo == pytest.approx(0.5830, abs=1e-4), costo


def test_regresion_poda_mira_todo_lo_que_falta():
    """Caso de la auditoría: si la poda por dominancia contara un intermedio menos del que
    queda, descartaría el camino bueno y `_cheapest_path` daría 1.8358 en vez de 1.4555."""
    a, b = track("a.wav", 128.0, "1A"), track("b.wav", 131.0, "3A")
    pool = [track(f"p{i}.wav", bpm, key) for i, (bpm, key) in enumerate(
        [(124.0, "6A"), (256.0, "3A"), (126.0, "4A"), (126.0, "2A"), (64.0, "8B")])]
    costo, _ = costo_exhaustivo(a, b, pool, 3, 3)
    _, c = _cheapest_path(a, _Graph(pool, b, 3), 3, 3)
    assert c == pytest.approx(costo, abs=1e-12), (c, costo)
    assert costo == pytest.approx(1.4555, abs=1e-4), costo


def test_hops_es_el_bfs_de_la_compuerta():
    """`_hops` (BFS por intervalos en log2 del BPM) contra un BFS escrito acá con
    `mezclabilidad` escalar, par por par. Con BPM pegados al borde del 8% (en las tres
    lecturas) para que la franja donde decide `bpm_score` trabaje, BPM 0 y octavas."""
    rng = random.Random(3)
    for _ in range(40):
        base = rng.uniform(80.0, 160.0)
        bpms = [base]
        for _ in range(25):
            x = rng.choice(bpms) * rng.choice([1.0, 2.0, 0.5])
            bpms.append(rng.choice([x / 0.92, x * 0.92, x / 0.92 * (1 + 1e-12),
                                    x / 0.92 * (1 - 1e-11), x * 0.92 * (1 + 1e-11),
                                    x * rng.uniform(0.9, 1.1), 0.0]))
        centro, resto = bpms[0], np.array(bpms[1:])
        esperado = [math.inf] * len(resto)
        frontera, d = [centro], 0
        while frontera:
            d += 1
            nueva = []
            for u in frontera:
                for j, w in enumerate(resto):
                    if esperado[j] == math.inf and mezclabilidad(u, float(w), "8A", "8A") > 0:
                        esperado[j] = d
                        nueva.append(float(w))
            frontera = nueva
        assert list(_hops([centro], resto)) == esperado, (centro, list(resto))


class _Contador:
    """Cuenta las expansiones (`_Graph.costs_from`: una operación de numpy contra toda la
    biblioteca, el costo dominante de la búsqueda) y los `heappop` de `_cheapest_path`, y
    corta con error si pasan de `tope`: una regresión de rendimiento tiene que fallar
    rápido y diciendo cuánto, no colgar la suite."""

    def __init__(self, monkeypatch, tope: int = 100_000):
        import heapq

        from motor import puente
        self.expansiones = self.pops = 0
        original_costs, original_pop = puente._Graph.costs_from, heapq.heappop

        def costs_from(graph, t, key):
            self.expansiones += 1
            assert self.expansiones <= tope, f"más de {tope} expansiones"
            return original_costs(graph, t, key)

        def heappop(heap):
            self.pops += 1
            assert self.pops <= tope, f"más de {tope} caminos sacados del heap"
            return original_pop(heap)

        monkeypatch.setattr(puente._Graph, "costs_from", costs_from)
        monkeypatch.setattr(puente.heapq, "heappop", heappop)


def test_clones_exactos_no_disparan_la_busqueda_lenta(monkeypatch):
    """A y B a 128.0/8A con 300 clones exactos y 6 intermedios: la relajación da un
    recorrido de costo 0 que repite clones, y se repara cambiando cada repetido por otro
    clone (mismo BPM y key = mismas aristas, mismo costo). Antes esto caía a la búsqueda
    sobre caminos y tardaba minutos. Y como la relajación va en profundidad a igual
    prioridad, completa el camino sin asentar los 1.800 estados empatados a costo 0."""
    clones = [track(f"c{i:03d}.wav", 128.0, "8A") for i in range(300)]
    a, b = track("a.wav", 128.0, "8A"), track("b.wav", 128.0, "8A")
    cuenta = _Contador(monkeypatch)
    p = build_bridge(a, b, clones, 6, 6)
    assert p.cost == 0.0 and len(p.intermediates) == 6, (p.cost, nombres(p.intermediates))
    assert len({t.path for t in p.intermediates}) == 6, nombres(p.intermediates)
    assert cuenta.pops == 0, f"cayó a la búsqueda sobre caminos ({cuenta.pops} pops)"
    assert cuenta.expansiones <= 20, f"{cuenta.expansiones} expansiones para 6 clones"


def test_la_busqueda_sin_repetidos_no_degenera_con_empates(monkeypatch):
    """`_cheapest_path` sola, con 150 clones a costo 0 y 6 intermedios: a igual prioridad
    tiene que ir en profundidad (si no, recorre todos los caminos de un largo antes de uno
    más largo) y descartar los caminos dominados antes de meterlos al heap."""
    clones = [track(f"c{i:03d}.wav", 128.0, "8A") for i in range(150)]
    a, b = track("a.wav", 128.0, "8A"), track("b.wav", 128.0, "8A")
    cuenta = _Contador(monkeypatch, tope=5_000)
    indices, c = _cheapest_path(a, _Graph(clones, b, 6), 6, 6)
    assert c == 0.0 and len(set(indices)) == 6, (c, indices)
    assert cuenta.pops <= 50, f"{cuenta.pops} caminos sacados del heap para 6 clones"


def test_la_busqueda_sin_repetidos_poda_antes_de_empujar(monkeypatch):
    """200 tracks densos (127-129 BPM con un decimal, keys 1A-6A) y 6 intermedios, con
    `_cheapest_path` directo: descartar los caminos dominados ANTES de meterlos al heap los
    saca 350 veces; sin esa poda, 616 (mismo óptimo, medido)."""
    rng = random.Random(1)
    pool = sorted((track(f"d{i:03d}.wav", round(rng.uniform(127, 129), 1),
                         rng.choice(CAMELOT[:6])) for i in range(200)),
                  key=lambda t: str(t.path))
    a, b = track("a.wav", 127.2, "1A"), track("b.wav", 128.8, "3A")
    cuenta = _Contador(monkeypatch, tope=5_000)
    indices, c = _cheapest_path(a, _Graph(pool, b, 6), 6, 6)
    assert len(set(indices)) == 6 and c == pytest.approx(0.4257, abs=1e-4), (indices, c)
    assert cuenta.pops <= 450, f"{cuenta.pops} caminos sacados del heap (con la poda: 350)"


def _dos_generos(rng: random.Random, hueco: tuple[float, float], n: int = 300):
    """House 118 hasta el hueco y dnb desde el hueco hasta 178, BPM con un decimal."""
    lo, hi = hueco
    return ([track(f"h{i:03d}.wav", round(rng.uniform(118.0, lo), 1), rng.choice(CAMELOT))
             for i in range(n)] +
            [track(f"d{i:03d}.wav", round(rng.uniform(hi, 178.0), 1), rng.choice(CAMELOT))
             for i in range(n)])


def test_sin_camino_se_contesta_sin_buscar(monkeypatch):
    """Conectividad antes que costo: con un hueco que la compuerta no cruza (141-159 entre
    house y dnb, y 170/2 = 85 no llega al house), `sin_camino` sale de `_hops` sin expandir
    un solo estado. Antes la búsqueda recorría todo el house (12.7 s con 10.000 tracks)."""
    biblio = _dos_generos(random.Random(9), (140.0, 160.0))
    a, b = track("a.wav", 128.0), track("b.wav", 170.0)
    cuenta = _Contador(monkeypatch)
    p = build_bridge(a, b, biblio, 6, 6)
    assert p.stop == STOP_SIN_CAMINO, p.stop
    assert cuenta.expansiones == 0, f"{cuenta.expansiones} expansiones para decir sin_camino"


def test_la_cota_sabe_cuantos_saltos_faltan(monkeypatch):
    """Con un hueco que SÍ se cruza (140 → 152, 7.9%): la cota vale `inf` para los tracks
    que no llegan a B con los saltos que quedan (`_hops`), y eso no deja expandir el house
    lejano. Medido con estos 600 tracks: 134 expansiones con esa condición, 253 sin ella."""
    biblio = _dos_generos(random.Random(9), (140.0, 152.0))
    a, b = track("a.wav", 128.0), track("b.wav", 170.0)
    cuenta = _Contador(monkeypatch)
    p = build_bridge(a, b, biblio, 3, 4)
    assert p.found, p.stop_detail
    assert not saltos_en_tolerancia(p), saltos_en_tolerancia(p)
    assert cuenta.expansiones <= 180, f"{cuenta.expansiones} expansiones (con la cota: 134)"


def test_si_a_y_b_ya_mezclan_se_avisa():
    """Decisión del dueño: el puente sigue dando 3-4 intermedios, pero avisa que A y B ya
    mezclan directo, con el motivo del motor, para que el DJ sepa que el rodeo es opcional.
    Si no mezclan directo, no hay aviso."""
    a, b = track("a.wav", 128.0, "8A"), track("b.wav", 128.5, "8A")
    p = build_bridge(a, b, _biblio_128())
    assert p.found and len(p.intermediates) >= 3, p.reasons()
    pct, _ = bpm_delta_pct(128.0, 128.5)
    assert p.direct is not None and p.direct.reason() == f"{pct:+.1f}% BPM | 8A → 8A (mismo)", \
        p.direct
    lejos = build_bridge(a, track("b.wav", 140.0, "8A"), _biblio_128() +
                         [track("x.wav", 133.0), track("y.wav", 136.0)])
    assert lejos.found and lejos.direct is None, (lejos.reasons(), lejos.direct)


def test_el_embedding_no_importa():
    """El puente no mira timbre: cambiar los embeddings no cambia nada."""
    a, b = track("a.wav", 124.0), track("b.wav", 131.0)
    biblio = _biblio_128()
    otro = [track(t.path.name, t.bpm, t.key, emb=tuple(np.roll([1.0, 0.2, 0.0, 0.0], i)))
            for i, t in enumerate(biblio)]
    assert nombres(build_bridge(a, b, biblio).tracks) == nombres(build_bridge(a, b, otro).tracks)


def test_rutas_repetidas_distintas_son_error():
    """Misma regla que la radio (`radio._unicos_por_ruta`)."""
    biblio = [track("x.wav", 126.0), track("x.wav", 127.0), *_biblio_128()]
    with pytest.raises(ValueError):
        build_bridge(track("a.wav", 126.0), track("b.wav", 128.0), biblio)
