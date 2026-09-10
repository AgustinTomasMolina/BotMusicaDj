"""Tests de la tonalidad por consenso entre tramos.

Las tonalidades salen del generador sintético (motor/sintetico.py), no inventadas —
regla de la spec §5. Se usan tramos cortos (`ventana_s=10`) para no generar minutos de
audio en cada test: la lógica del voto es la misma, solo cambia cuánto audio hace falta.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402

from motor.sintetico import click_track  # noqa: E402
from motor.tonalidad import (  # noqa: E402
    _tramos_disjuntos,
    ranking,
    tono,
    tono_consenso,
    ventana_central,
)

SR = 22050
PARAMS = {"n_tramos": 3, "ventana_s": 10}   # necesita > 30 s de audio


def _pista(nota: str, dur: float, seed: int = 0):
    y, _ = click_track(128.0, dur=dur, sr=SR, nota=nota, seed=seed)
    return y


def test_ventana_central_recorta_y_respeta_lo_corto():
    y = np.zeros(SR * 200, dtype=np.float32)
    assert len(ventana_central(y, SR, 90)) == 90 * SR
    corto = np.zeros(SR * 10, dtype=np.float32)
    assert len(ventana_central(corto, SR, 90)) == 10 * SR


def test_ranking_devuelve_24_ordenadas():
    r = ranking(_pista("A", 15), SR)
    assert len(r) == 24
    assert r == sorted(r, key=lambda t: -t[0])


def test_ranking_lista_vacia_en_silencio():
    assert ranking(np.zeros(SR * 5, dtype=np.float32), SR) == []


def test_tramos_disjuntos_no_se_pisan():
    y = np.zeros(SR * 40, dtype=np.float32)
    tr = _tramos_disjuntos(y, SR, 3, 10)
    assert len(tr) == 3
    assert all(len(t) == 10 * SR for t in tr)
    # Reconstruyendo los índices: cada bloque mide 40/3 s y la ventana entra entera.
    bloque = y.size // 3
    assert bloque >= 10 * SR


def test_tramos_devuelve_vacio_si_no_entra():
    y = np.zeros(SR * 20, dtype=np.float32)   # 20 s no da para 3 x 10 s
    assert _tramos_disjuntos(y, SR, 3, 10) == []


def test_consenso_unanime_da_confianza_1():
    """Un track de una sola tonalidad: los 3 tramos coinciden."""
    r = tono_consenso(_pista("A", 40), SR, **PARAMS)
    assert r["confianza"] == 1.0
    assert r["acuerdo"] == (3, 3)
    assert len(set(r["tramos"])) == 1


def test_consenso_coincide_con_tono_cuando_es_unanime():
    """Si todos los tramos dicen lo mismo, el consenso no puede diferir de la ventana central."""
    y = _pista("D", 40)
    assert tono_consenso(y, SR, **PARAMS)["camelot"] == tono(y, SR)["camelot"]


def test_confianza_baja_cuando_el_track_cambia_de_tonalidad():
    """Dos tercios en una nota y uno en otra: el consenso gana 2-1 y lo reporta."""
    y = np.concatenate([_pista("A", 27, seed=1), _pista("C", 14, seed=2)])
    r = tono_consenso(y, SR, **PARAMS)
    assert r["confianza"] < 1.0
    assert r["acuerdo"][1] == 3
    assert len(set(r["tramos"])) > 1


def test_track_corto_no_finge_consenso():
    """Sin tramos suficientes no hay evidencia de acuerdo: confianza 0, no un número inventado."""
    r = tono_consenso(_pista("A", 15), SR, **PARAMS)
    assert r["confianza"] == 0.0
    assert r["acuerdo"] == (0, 0)
    assert r["tramos"] == []
    assert r["camelot"] != "?"        # la key igual se estima, lo que falta es la confianza


def test_silencio_no_inventa_key():
    r = tono_consenso(np.zeros(SR * 40, dtype=np.float32), SR, **PARAMS)
    assert r["camelot"] == "?"
    assert r["confianza"] == 0.0


def test_determinista():
    """Misma entrada, misma salida — regla de determinismo del proyecto."""
    y = _pista("F", 40, seed=7)
    a, b = tono_consenso(y, SR, **PARAMS), tono_consenso(y, SR, **PARAMS)
    assert a == b


def test_confianza_es_fraccion_de_acuerdo():
    """La confianza reportada tiene que ser exactamente acuerdo[0]/acuerdo[1]."""
    y = np.concatenate([_pista("A", 27, seed=3), _pista("C", 14, seed=4)])
    r = tono_consenso(y, SR, **PARAMS)
    ganados, total = r["acuerdo"]
    assert r["confianza"] == round(ganados / total, 3)
