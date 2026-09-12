"""Tests del parser de Rekordbox — detección de formato de Tonality (tarea #5.68).

El campo Tonality de Rekordbox 7.2.16 es notación CLÁSICA (confirmado, 193 tracks). Pero
tres tracks reales traen un tag ajeno en otro formato, todos con AverageBpm="0.00":
  '4A'  (SMOKED OUT)     → Camelot leakeado
  '11m' (DJEMBE TOOL)    → desconocido
  '8m'  (Raid)           → desconocido
El formato se detecta POR EL VALOR: solo la clásica se mapea; el resto → None y se cuenta.
"""
from ground_truth.rekordbox import (
    CAMELOT,
    CLASICA,
    DESCONOCIDA,
    VACIA,
    a_camelot,
    formato_tonalidad,
)


def test_clasica_mapea_a_camelot():
    # Muestras reales del top del conteo (Am 31, Gm 31, Fm 23, Abm 19, F#m 11).
    assert a_camelot("Am") == "8A"
    assert a_camelot("Gm") == "6A"
    assert a_camelot("Fm") == "4A"
    assert a_camelot("Abm") == "1A"
    assert a_camelot("F#m") == "11A"
    assert a_camelot("C") == "8B"      # mayor
    assert a_camelot("Ab") == "4B"


def test_valores_no_clasicos_reales_no_se_adivinan():
    # Los tres valores reales de tracks con BPM=0: NO son clásica → None, nunca adivinar.
    assert a_camelot("4A") is None     # Camelot leakeado (SMOKED OUT)
    assert a_camelot("11m") is None    # desconocido (DJEMBE TOOL)
    assert a_camelot("8m") is None     # desconocido (Raid)
    assert a_camelot("") is None
    assert a_camelot(None) is None


def test_formato_se_detecta_por_el_valor():
    assert formato_tonalidad("Am") == CLASICA
    assert formato_tonalidad("F#m") == CLASICA
    assert formato_tonalidad("4A") == CAMELOT      # forma de Camelot, no es Rekordbox
    assert formato_tonalidad("8A") == CAMELOT
    assert formato_tonalidad("11m") == DESCONOCIDA
    assert formato_tonalidad("8m") == DESCONOCIDA
    assert formato_tonalidad("") == VACIA
    assert formato_tonalidad(None) == VACIA
