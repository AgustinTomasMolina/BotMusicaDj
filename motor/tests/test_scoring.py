"""Tests del scoring multiplicativo (lógica pura, sin audio)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import math  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from motor.scoring import _dist_bpm_relativa, bpm_score, mezclabilidad, score  # noqa: E402


def test_bpm_es_compuerta():
    assert bpm_score(150, 150) == 1.0
    assert 0.0 < bpm_score(150, 153) < 1.0          # dentro de ±8%
    assert bpm_score(150, 200) == 0.0               # fuera de ±8% → compuerta dura
    assert bpm_score(150, 75) == 1.0                # half-time se considera mezclable


def test_bpm_score_es_simetrico():
    # 'Fuera de ±8%' no puede depender de cuál track va primero (Fable I3).
    assert bpm_score(150, 160) == bpm_score(160, 150)
    assert bpm_score(150, 138) == bpm_score(138, 150)
    # NaN / BPM inválido no evade la compuerta.
    assert bpm_score(float("nan"), 150) == 0.0
    assert bpm_score(0, 150) == 0.0


def test_distancia_simetrica_tambien_en_octava():
    """`dist(a, b) == dist(b, a)` para pares normales Y de medio/doble tiempo. La versión
    con denominador `max(a, b)` sin transformar era simétrica solo en los normales: en
    octava 100→220 medía 4.55% y 220→100 medía 9.09%."""
    pares = [(150, 160), (150, 138), (128, 131.5), (100, 220), (80, 174), (75, 150),
             (140, 72), (174, 90), (60, 128), (95.3, 187.1)]
    for a, b in pares:
        assert _dist_bpm_relativa(a, b) == pytest.approx(_dist_bpm_relativa(b, a), abs=1e-15), \
            f"{a}→{b} mide {_dist_bpm_relativa(a, b):.5f} y {b}→{a} {_dist_bpm_relativa(b, a):.5f}"


def test_octava_fuera_de_tolerancia_se_corta_en_los_dos_sentidos():
    """Los tres casos de la auditoría. El pitch real sale a mano: 100 contra 220 se mezcla
    como 100 contra 110 (medio tiempo), y 110/100 es un 10% más rápido → 1 - 100/110 = 9.09%.
    80 contra 174 es 80 contra 87 → 1 - 80/87 = 8.05%. Los dos, fuera de ±8%."""
    casos = [(100.0, 220.0, 1 - 100 / 110), (80.0, 174.0, 1 - 80 / 87)]
    for a, b, real in casos:
        for x, y in ((a, b), (b, a)):
            assert _dist_bpm_relativa(x, y) == pytest.approx(real, abs=1e-12), \
                f"{x}→{y}: la distancia no es el pitch real {real:.4%}"
            assert bpm_score(x, y) == 0.0, f"{x}→{y} tiene pitch real {real:.2%} y entró"
    # Y lo que sí mezcla en octava sigue entrando: 75 contra 152 es 76 contra 75 (1.3%).
    assert bpm_score(75, 152) > 0.0 and bpm_score(152, 75) > 0.0


def test_mezclabilidad():
    assert mezclabilidad(150, 150, "8A", "8A") == 1.0        # perfecto
    assert mezclabilidad(150, 200, "8A", "8A") == 0.0        # BPM fuera → 0 aunque la key sea perfecta
    media = mezclabilidad(150, 150, "8A", "3B")             # BPM ok, key lejana
    assert 0.2 < media < 0.6                                 # baja pero NO se anula


def test_scoring_es_multiplicativo():
    # Un encaje altísimo NO compensa estar fuera de tempo (la clave de multiplicar vs sumar).
    assert score(0.95, 150, 200, "8A", "8A") == 0.0
    # Con todo alineado, el score es el encaje.
    assert abs(score(0.9, 150, 150, "8A", "8A") - 0.9) < 1e-9


# ---------------------------------------------------------------------------
# Compuerta vectorizada (tarea 1.2) contra la escalar
# ---------------------------------------------------------------------------
#
# `mezclabilidad_vector` existe para que `radio.build_set` no llame a la escalar 10.000
# veces por posición. Tiene que dar EXACTAMENTE lo mismo — no "casi": un bit distinto en un
# empate cambia qué track entra al set. Por eso se compara la representación binaria
# (`_mismos_bits`), no con tolerancia.

TOL = 0.08
CAMELOT = [f"{n}{lado}" for lado in ("A", "B") for n in range(1, 13)]
KEYS_INVALIDAS = ["?", "", "13A", "0A", "8C", "8a", " 8A", "A", "8", "B12"]

# BPM "de a": válidos de distintos géneros y los que la compuerta tiene que tratar como
# no medibles (0, negativo, NaN, ±inf), más extremos que desbordan `2b`.
BPM_A = [0.0, -5.0, float("nan"), float("inf"), float("-inf"), 1e-300, 1e308,
         60.0, 63.0, 70.0, 87.5, 100.0, 120.0, 124.0, 126.0, 128.0, 130.7, 140.0, 174.0, 252.0]


def _bpm_b(a: float) -> np.ndarray:
    """Grilla densa de BPM "de b" para un `a`: un barrido parejo, los especiales, y los
    BORDES de ±8% de las tres lecturas con su vecino flotante de cada lado.

    Bordes, con `rel(x, y) = |x-y| / max(x, y)` (derivados a mano, no del código):
    mismo tiempo `b = 0.92·a` y `b = a/0.92`; doble tiempo (2b) `b = 0.46·a` y `b = a/1.84`;
    medio tiempo (b/2) `b = 1.84·a` y `b = 2a/0.92`.
    """
    barrido = np.linspace(-10.0, 520.0, 2121)
    especiales = [0.0, -0.0, -1.0, float("nan"), float("inf"), float("-inf"), 1e-300, 1e308,
                  5e307]
    bordes = []
    if np.isfinite(a) and a > 0:
        for b in (0.92 * a, a / 0.92, 0.46 * a, a / 1.84, 1.84 * a, 2 * a / 0.92, a, 2 * a, a / 2):
            bordes += [np.nextafter(b, -np.inf), b, np.nextafter(b, np.inf)]
    return np.concatenate([barrido, especiales, bordes]).astype(np.float64)


def _mismos_bits(x: np.ndarray, y: np.ndarray) -> bool:
    """Igualdad de la representación binaria (distingue -0.0 de 0.0 y cualquier último bit)."""
    return np.asarray(x, np.float64).tobytes() == np.asarray(y, np.float64).tobytes()


def test_distancia_vectorizada_es_bit_a_bit_la_escalar():
    from motor.scoring import _dist_bpm_relativa_vector

    for a in BPM_A:
        bs = _bpm_b(a)
        esperado = np.array([_dist_bpm_relativa(a, float(b)) for b in bs])
        obtenido = _dist_bpm_relativa_vector(a, bs)
        malos = np.flatnonzero(esperado.view(np.int64) != obtenido.view(np.int64))
        assert malos.size == 0, (
            f"a={a}: {malos.size} distancias difieren; primera b={bs[malos[0]]!r}: "
            f"escalar {esperado[malos[0]]!r} vs vector {obtenido[malos[0]]!r}")


def test_mezclabilidad_vectorizada_es_bit_a_bit_la_escalar_en_bpm():
    from motor.scoring import KeysIndexadas, mezclabilidad_vector

    for a in BPM_A:
        bs = _bpm_b(a)
        keys = ["9A"] * len(bs)
        esperado = np.array([mezclabilidad(a, float(b), "8A", k) for b, k in zip(bs, keys, strict=True)])
        obtenido = mezclabilidad_vector(a, bs, "8A", KeysIndexadas.de(keys))
        malos = np.flatnonzero(esperado.view(np.int64) != obtenido.view(np.int64))
        assert malos.size == 0, (
            f"a={a}: {malos.size} mezclabilidades difieren; primera b={bs[malos[0]]!r}: "
            f"escalar {esperado[malos[0]]!r} vs vector {obtenido[malos[0]]!r}")
        # La grilla tiene que cruzar la compuerta: si todo diera 0 (o todo > 0), la
        # equivalencia de los bordes no estaría probada.
        if np.isfinite(a) and a > 0:
            assert 0 < np.count_nonzero(obtenido > 0) < len(bs), f"a={a}: la grilla no cruza ±8%"


def test_mezclabilidad_vectorizada_es_la_escalar_en_todas_las_keys():
    """Las 24×24 keys Camelot más `?`, vacía e inválidas, en los dos lados, con BPM que
    mezclan en las tres lecturas y uno que no. Las keys de `b` van repetidas y mezcladas,
    para que la agrupación por texto de `KeysIndexadas` no pueda correr un valor de lugar."""
    from motor.scoring import KeysIndexadas, mezclabilidad_vector

    todas = CAMELOT + KEYS_INVALIDAS
    rng = np.random.default_rng(3)
    keys_b = [todas[i] for i in rng.permutation(np.repeat(np.arange(len(todas)), 4))]
    bpms_b = rng.choice([128.0, 131.0, 64.5, 255.0, 139.5], size=len(keys_b))
    indexadas = KeysIndexadas.de(keys_b)
    for key_a in todas:
        esperado = np.array([mezclabilidad(128.0, float(b), key_a, k)
                             for b, k in zip(bpms_b, keys_b, strict=True)])
        obtenido = mezclabilidad_vector(128.0, bpms_b, key_a, indexadas)
        assert _mismos_bits(esperado, obtenido), (
            f"key_a={key_a!r}: " + ", ".join(
                f"{keys_b[i]!r}@{bpms_b[i]}: {esperado[i]!r} vs {obtenido[i]!r}"
                for i in np.flatnonzero(esperado != obtenido)[:4]))


def test_la_escalar_no_cambio_contra_f794211():
    """La compuerta escalar se reescribió para aceptar arrays (`_rel` con `np.maximum`).
    Sobre BPM medibles tiene que seguir dando lo mismo que la de `f794211`, bit a bit; el
    único cambio buscado es `inf` (H1, ver el test de abajo)."""
    from motor.tests import _radio_f794211 as viejo

    for a in BPM_A:
        for b in _bpm_b(a):
            b = float(b)
            if math.isinf(a) or math.isinf(b):
                continue
            esperado, obtenido = viejo.mezclabilidad(a, b, "8A", "3B"), mezclabilidad(a, b, "8A", "3B")
            assert _mismos_bits(esperado, obtenido), f"{a}→{b}: {esperado!r} vs {obtenido!r}"
            assert _mismos_bits(viejo._dist_bpm_relativa(a, b), _dist_bpm_relativa(a, b)), (a, b)


def test_bpm_infinito_no_pasa_la_compuerta():
    """H1: con `inf` todas las lecturas daban `inf/inf = NaN`, `NaN >= tol` es False y la
    compuerta devolvía mezclabilidad NaN — que `build_set` no descartaba (`NaN <= 0` es
    False). Ahora `inf` es un BPM no medible, igual que 0 o NaN, en los dos caminos."""
    from motor.scoring import KeysIndexadas, mezclabilidad_vector

    inf = float("inf")
    for a, b in ((inf, 128.0), (128.0, inf), (inf, inf), (-inf, 128.0)):
        assert _dist_bpm_relativa(a, b) == 1.0, f"{a}→{b}"
        assert bpm_score(a, b) == 0.0, f"{a}→{b} bpm_score {bpm_score(a, b)!r}"
        assert mezclabilidad(a, b, "8A", "8A") == 0.0, f"{a}→{b}"
    vec = mezclabilidad_vector(128.0, np.array([inf, float("nan"), 128.0]), "8A",
                               KeysIndexadas.de(["8A", "8A", "8A"]))
    assert vec.tolist() == [0.0, 0.0, 1.0], vec


if __name__ == "__main__":
    test_bpm_es_compuerta()
    test_bpm_score_es_simetrico()
    test_distancia_simetrica_tambien_en_octava()
    test_octava_fuera_de_tolerancia_se_corta_en_los_dos_sentidos()
    test_mezclabilidad()
    test_scoring_es_multiplicativo()
    print("OK — tests de scoring pasaron")
