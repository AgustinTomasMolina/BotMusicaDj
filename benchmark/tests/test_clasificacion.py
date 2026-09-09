"""Tests del clasificador de desacuerdos de BPM.

Los números son ratios exactas construidas a mano (no medidas de audio), así que no
violan la regla de "nunca inventar un BPM": no se afirma el BPM de ningún track real,
se verifica aritmética de múltiplos.
"""
from benchmark.clasificacion import (
    EXACTO,
    GENUINO,
    OCTAVA,
    OTRO_MULTIPLO,
    SIN_REF,
    TRESILLO,
    clasificar,
    resumen,
)


def test_exacto_dentro_del_umbral():
    assert clasificar(150.0, 150.0).clase == EXACTO
    assert clasificar(150.8, 150.0).clase == EXACTO      # 0.8 <= umbral 1.0


def test_octava_en_los_dos_sentidos():
    assert clasificar(152.0, 76.0).clase == OCTAVA       # motor al doble
    assert clasificar(76.0, 152.0).clase == OCTAVA       # motor a la mitad
    assert clasificar(304.0, 76.0).clase == OCTAVA       # x4


def test_tresillo_en_los_dos_sentidos():
    assert clasificar(170.0, 113.33).clase == TRESILLO   # x3/2
    assert clasificar(113.33, 170.0).clase == TRESILLO   # x2/3


def test_otro_multiplo_racional():
    assert clasificar(160.0, 120.0).clase == OTRO_MULTIPLO   # x4/3
    assert clasificar(120.0, 160.0).clase == OTRO_MULTIPLO   # x3/4


def test_error_genuino_no_es_multiplo():
    """Ratio ~1 pero fuera del umbral: mismo pulso, medido mal."""
    c = clasificar(137.0, 130.0)
    assert c.clase == GENUINO
    # Y uno que no cae cerca de ninguna fracción simple.
    assert clasificar(101.0, 130.0).clase == GENUINO


def test_sin_referencia():
    assert clasificar(150.0, 0.0).clase == SIN_REF


def test_octava_se_prefiere_sobre_racional_lejano():
    """152 vs 76: 2.0 exacto debe ganarle a cualquier otro candidato."""
    c = clasificar(152.0, 76.0)
    assert c.clase == OCTAVA and c.ratio == 2.0


def test_el_umbral_manda_sobre_la_clasificacion():
    """Un desvío por debajo del umbral es EXACTO aunque la ratio no sea 1 exacta."""
    assert clasificar(150.5, 150.0).clase == EXACTO


def test_resumen_cuenta_por_clase():
    r = resumen([EXACTO, EXACTO, OCTAVA, GENUINO])
    assert r[EXACTO] == 2 and r[OCTAVA] == 1 and r[GENUINO] == 1
