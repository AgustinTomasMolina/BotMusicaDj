"""Tests de energía: RMS, percentil, curva del set y las métricas con las que §4 mide esa
curva (desvío medio y Spearman del tramo ascendente).

Los valores esperados de la curva y del Spearman están calculados a mano en cada test
(la fórmula está en el docstring de cada función, no hay nada que adivinar). El Spearman
se verificó además contra `scipy.stats.spearmanr` al escribirlo — coincide en los cinco
casos de acá hasta el dígito 15 — pero scipy no se importa en los tests: entra al entorno
como dependencia transitiva de librosa y no es dependencia declarada del motor.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from motor.energia import (  # noqa: E402
    CURVES,
    PEAK_AT,
    ascending_positions,
    ascending_spearman,
    energia_rms,
    energy_curve_correlation,
    energy_curve_deviation,
    energy_target,
    percentil,
    spearman,
)


def test_rms_fuerte_mayor_que_suave():
    fuerte = np.sin(np.linspace(0, 100, 4410)).astype(np.float32)
    suave = 0.1 * fuerte
    assert energia_rms(fuerte) > energia_rms(suave)
    assert energia_rms(np.zeros(1000)) == 0.0


def test_percentil():
    biblioteca = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert percentil(0.35, biblioteca) == 60.0     # 3 de 5 por debajo
    assert percentil(0.05, biblioteca) == 0.0      # el más tranquilo
    assert percentil(0.9, biblioteca) == 100.0     # el más energético


# ---------------------------------------------------------------------------
# Curva de energía
# ---------------------------------------------------------------------------

def test_peak_sube_hasta_el_75_por_ciento_y_despues_baja():
    """Set de 9 tracks: las posiciones 0..8 caen en t = 0, 0.125, ..., 1.0, así que la
    posición 6 es exactamente el 75% (`PEAK_AT`) y el pico tiene que caer AHÍ.

    Se comparan valores concretos, no "es creciente": una curva que sube hasta el final
    también es creciente hasta el 75% y sería otra curva.
    """
    valores = [energy_target(i, 9) for i in range(9)]

    # Rampa de subida: 0.35 + 0.6 * (t / 0.75), con t = i/8.
    assert valores[:7] == pytest.approx([0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95])
    # Bajada: 0.95 - 0.35 * ((t - 0.75) / 0.25).
    assert valores[7:] == pytest.approx([0.775, 0.6])

    assert valores.index(max(valores)) == 6, "el pico se corrió de la posición del 75%"
    assert valores[-1] < valores[6], "'peak' tiene que aflojar después del clímax"
    # El final del set queda por encima del arranque: se baja, no se vuelve al principio.
    assert valores[-1] > valores[0]
    assert PEAK_AT == 0.75


def test_warmup_sube_sostenido_y_no_baja_nunca():
    valores = [energy_target(i, 9, "warmup") for i in range(9)]
    # 0.3 + 0.5 * t, con t = i/8.
    assert valores == pytest.approx([0.3, 0.3625, 0.425, 0.4875, 0.55, 0.6125, 0.675,
                                     0.7375, 0.8])
    assert valores[-1] == pytest.approx(0.8), "warmup entrega la pista más arriba"
    assert all(b > a for a, b in zip(valores, valores[1:], strict=False))


def test_flat_es_constante():
    valores = [energy_target(i, 9, "flat") for i in range(9)]
    assert valores == [0.5] * 9


def test_las_tres_curvas_viven_dentro_de_0_1():
    for curva in CURVES:
        for length in (1, 2, 3, 7, 9, 40, 101):
            valores = [energy_target(i, length, curva) for i in range(length)]
            assert min(valores) >= 0.0, f"{curva}/{length} se fue abajo de 0: {min(valores)}"
            assert max(valores) <= 1.0, f"{curva}/{length} se pasó de 1: {max(valores)}"


def test_length_1_devuelve_el_arranque_de_cada_curva():
    """Un set de un solo track no tiene progresión: `length - 1` es 0 y ahí está la
    división por cero que hay que no cometer."""
    assert energy_target(0, 1) == pytest.approx(0.35)
    assert energy_target(0, 1, "warmup") == pytest.approx(0.3)
    assert energy_target(0, 1, "flat") == 0.5


def test_position_fuera_de_rango_se_recorta_a_los_bordes():
    """Sin recorte, la rama de bajada de 'peak' se iba a negativo: con length=5 y
    position=10 daba -1.5, y un objetivo negativo hace ganar siempre al track más
    tranquilo de la biblioteca."""
    final = energy_target(4, 5)
    arranque = energy_target(0, 5)
    assert energy_target(10, 5) == pytest.approx(final)
    assert energy_target(10, 5) >= 0.0
    assert energy_target(-4, 5) == pytest.approx(arranque)
    assert energy_target(-4, 5, "warmup") == pytest.approx(0.3)


def test_curva_desconocida_y_length_invalido_son_error():
    """Un nombre de curva mal escrito tiene que gritar. El boceto caía en silencio a
    'peak' y el set salía con otra forma que la pedida."""
    with pytest.raises(ValueError, match="curva desconocida"):
        energy_target(0, 10, "peack")
    with pytest.raises(ValueError, match="al menos un track"):
        energy_target(0, 0)


# ---------------------------------------------------------------------------
# Spearman — la base de la métrica secundaria de §4 (sobre el set entero ya no es contrato)
# ---------------------------------------------------------------------------

def test_spearman_orden_perfecto_y_orden_invertido():
    energias = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert energy_curve_correlation(energias) == pytest.approx(1.0)
    assert energy_curve_correlation(list(reversed(energias))) == pytest.approx(-1.0)


def test_spearman_caso_intermedio_conocido():
    """Cuatro puntos con UN par cambiado de lugar (0.3 y 0.2 invertidos).

    A mano: rangos de la energía = [1, 3, 2, 4], rangos de la posición = [1, 2, 3, 4].
    d = [0, 1, -1, 0] → sum(d²) = 2. Sin empates vale rho = 1 - 6·Σd² / (n(n²-1))
    = 1 - 12/60 = 0.8.
    """
    assert energy_curve_correlation([0.1, 0.3, 0.2, 0.4]) == pytest.approx(0.8)


def test_spearman_con_empates_promedia_rangos():
    """Dos tracks del mismo percentil son un empate real, no un desorden.

    Energías [0.2, 0.2, 0.5, 0.9, 0.4]: los dos 0.2 comparten los rangos 1 y 2, o sea 1.5
    cada uno → rangos [1.5, 1.5, 4, 5, 3] contra posiciones [1, 2, 3, 4, 5]. Pearson sobre
    esos dos vectores da 0.6668859288553501 (coincide con scipy.stats.spearmanr).
    """
    assert energy_curve_correlation([0.2, 0.2, 0.5, 0.9, 0.4]) == pytest.approx(
        0.6668859288553501
    )
    # Sin promediar empates (rangos [1, 2, 4, 5, 3] por orden de llegada) daría
    # 0.7 exacto. Que el valor NO sea 0.7 es lo que prueba que el empate se promedió.
    assert energy_curve_correlation([0.2, 0.2, 0.5, 0.9, 0.4]) != pytest.approx(0.7, abs=1e-4)
    # Un empate total entre tres tracks: rangos [2, 2, 2, 4] contra posiciones [1, 2, 3, 4].
    # Pearson sobre eso da 0.7745966692414834 (= sqrt(0.6)); coincide con scipy.
    assert energy_curve_correlation([0.3, 0.3, 0.3, 0.9]) == pytest.approx(0.7745966692414834)


def test_spearman_indefinido_devuelve_nan_no_cero():
    """spec §6: un dato que miente es peor que un dato ausente. Un 0.0 acá se leería como
    'el set no tiene curva' cuando lo que pasa es que no se puede calcular."""
    assert math.isnan(energy_curve_correlation([0.5, 0.5, 0.5])), "serie constante"
    assert math.isnan(energy_curve_correlation([0.7])), "un solo track"
    assert math.isnan(energy_curve_correlation([])), "set vacío"


def test_spearman_es_simetrico_y_no_pearson():
    """Spearman mira el ORDEN, no la distancia: una relación monótona pero muy curva da
    1.0 igual, y ahí es donde se separa de Pearson (que sobre estos datos da 0.86)."""
    x = [1, 2, 3, 4, 5]
    y = [1, 2, 4, 8, 1000]
    assert spearman(x, y) == pytest.approx(1.0)
    assert spearman(y, x) == pytest.approx(1.0)
    pearson = float(np.corrcoef(x, y)[0, 1])
    assert pearson < 0.9, "si Pearson ya diera 1.0, este caso no distinguiría nada"


def test_spearman_exige_series_del_mismo_largo():
    with pytest.raises(ValueError, match="tienen que medir lo mismo"):
        spearman([1, 2, 3], [1, 2])


def test_peak_seguida_perfecto_da_1_en_el_tramo_ascendente_y_072_en_el_set_entero():
    """El caso que motivó el cambio de contrato (spec §4, 2026-09-14).

    El set sigue la propia curva 'peak' (energía = objetivo de cada posición), el mejor
    caso posible del motor. El Spearman sobre el set entero le da 0.7188 por la bajada del
    último cuarto; sobre el tramo ascendente (t ≤ 0.75: posiciones 0..14 de 20, energías
    estrictamente crecientes) tiene que dar 1.0.
    """
    length = 20
    energias = [energy_target(i, length) for i in range(length)]
    assert energy_curve_correlation(energias) == pytest.approx(0.7187969924812028, abs=1e-9)
    assert ascending_spearman(energias, "peak") == pytest.approx(1.0), (
        "un set que sigue 'peak' perfecto no da 1.0 en el tramo ascendente")


# ---------------------------------------------------------------------------
# Contrato de la curva (§4, 2026-09-14): desvío medio + Spearman ascendente
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("curva", CURVES)
def test_desvio_cero_si_el_set_sigue_la_curva_exacta(curva):
    energias = [energy_target(i, 13, curva) for i in range(13)]
    assert energy_curve_deviation(energias, curva) == pytest.approx(0.0, abs=1e-12)


def test_desvio_caso_conocido_a_mano():
    """'peak' con 5 posiciones: t = 0, .25, .5, .75, 1 → objetivos 0.35, 0.55, 0.75, 0.95,
    0.60 (subida lineal 0.35→0.95 hasta t=.75, bajada a 0.60 en t=1).

    Energías [0.45, 0.55, 0.65, 0.95, 0.80] → |dif| = 0.10, 0, 0.10, 0, 0.20 → media 0.08.
    """
    assert energy_curve_deviation([0.45, 0.55, 0.65, 0.95, 0.80], "peak") == pytest.approx(0.08)


def test_desvio_de_un_set_corto_se_mide_contra_la_curva_pedida():
    """Set pedido de 5 que quedó en 2: los objetivos que usó build_set son los de length=5
    (0.35, 0.55), no los de una curva de 2 (0.35, 0.60)."""
    corto = [0.35, 0.55]
    assert energy_curve_deviation(corto, "peak", length=5) == pytest.approx(0.0, abs=1e-12)
    assert energy_curve_deviation(corto, "peak") == pytest.approx(0.025)   # |0.55-0.60|/2
    with pytest.raises(ValueError, match="no puede ser más corto"):
        energy_curve_deviation([0.1, 0.2, 0.3], "peak", length=2)


def test_desvio_de_set_vacio_es_nan():
    assert math.isnan(energy_curve_deviation([], "peak"))


def test_tramo_ascendente_por_curva():
    # peak, 12 tracks: t = i/11 ≤ 0.75 ⇔ i ≤ 8.25 → posiciones 0..8 (9 puntos).
    assert ascending_positions(12, "peak") == list(range(9))
    # peak, 5 tracks: t = 0, .25, .5, .75 entran (el clímax cuenta como subida), t=1 no.
    assert ascending_positions(5, "peak") == [0, 1, 2, 3]
    assert ascending_positions(12, "warmup") == list(range(12))
    assert ascending_positions(12, "flat") == []
    # set corto de 3 sobre un pedido de 5: las 3 posiciones caen en la subida.
    assert ascending_positions(3, "peak", length=5) == [0, 1, 2]


def test_spearman_ascendente_ignora_la_bajada():
    """'peak' de 5: el tramo ascendente son las posiciones 0..3. Energías [0.1, 0.3, 0.2,
    0.4] ahí → un par invertido → 0.8 (mismo cálculo a mano que el caso intermedio de
    arriba). El último track (0.0, la bajada) no entra; en el set entero sí, y lo tira."""
    energias = [0.1, 0.3, 0.2, 0.4, 0.0]
    assert ascending_spearman(energias, "peak") == pytest.approx(0.8)
    assert energy_curve_correlation(energias) != pytest.approx(0.8), \
        "si el set entero diera lo mismo, este caso no probaría que la bajada se excluye"


def test_spearman_ascendente_indefinido_es_nan():
    assert math.isnan(ascending_spearman([0.2, 0.5, 0.9], "flat")), "flat no tiene subida"
    # peak con 2 tracks: t = 0 y 1 → solo la posición 0 sube → 1 punto.
    assert math.isnan(ascending_spearman([0.2, 0.9], "peak")), "1 punto ascendente"
    assert math.isnan(ascending_spearman([0.4], "warmup")), "un solo track"
    assert math.isnan(ascending_spearman([0.5, 0.5, 0.5, 0.1], "peak")), "tramo constante"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
