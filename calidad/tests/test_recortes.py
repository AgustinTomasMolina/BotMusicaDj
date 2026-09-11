"""Tests del fixture de recortes (tarea 5.25).

El barrido contra un track real se corre a mano (ver el __main__ de recortes.py); acá se
verifica que el generador haga lo que dice y que la compuerta de duración se comporte como
se midió, usando señal sintética para que corra siempre.
"""
import numpy as np

from calidad.duplicados import SR_HUELLA, Ficha, huella, mismo_audio, similitud
from calidad.tests.recortes import recortar_del_medio

SR = SR_HUELLA


def _senal(seed=0, dur=200.0, sr=SR):
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    y = np.zeros(n)
    for f in rng.uniform(80, 5000, size=6):
        ini, fin = sorted(rng.uniform(0, dur, size=2))
        y += ((t >= ini) & (t <= fin)) * np.sin(2 * np.pi * f * t) * rng.uniform(0.2, 1.0)
    return (y / (np.abs(y).max() + 1e-9)).astype(np.float32)


def test_saca_la_cantidad_pedida():
    y = _senal(dur=100.0)
    r = recortar_del_medio(y, SR, 10.0)
    assert abs((y.size - r.size) - 10 * SR) <= 1


def test_no_modifica_el_original():
    """Regla del proyecto: nunca tocar el audio original, ni en memoria."""
    y = _senal(dur=60.0)
    antes = y.copy()
    recortar_del_medio(y, SR, 5.0)
    assert np.array_equal(y, antes)


def test_recorte_cero_no_cambia_nada():
    y = _senal(dur=60.0)
    assert recortar_del_medio(y, SR, 0.0).size == y.size


def test_recorte_mas_grande_que_el_track_devuelve_copia():
    y = _senal(dur=30.0)
    r = recortar_del_medio(y, SR, 999.0)
    assert r.size == y.size and r is not y


def test_corta_en_la_posicion_pedida():
    """Con posicion=0.25 el corte va al primer cuarto, no al medio."""
    y = np.arange(SR * 100, dtype=np.float32)
    r = recortar_del_medio(y, SR, 10.0, posicion=0.25)
    # El valor que estaba en el 25% ya no debería estar; el del 75% sí.
    assert float(y[int(y.size * 0.25)]) not in set(r[::1000].tolist())
    assert r.size == y.size - 10 * SR


def test_un_edit_realista_no_se_agrupa_con_el_original():
    """30 s de diferencia: la compuerta de duración tiene que separarlos."""
    y = _senal(seed=1, dur=200.0)
    r = recortar_del_medio(y, SR, 30.0)
    a = Ficha("a", "a", y.size / SR, huella(y[30 * SR:90 * SR]))
    b = Ficha("b", "b", r.size / SR, huella(r[30 * SR:90 * SR]))
    agrupa, _ = mismo_audio(a, b)
    assert not agrupa, "un edit de 30 s no puede agruparse con el original"


def test_la_huella_no_ve_un_corte_fuera_de_su_ventana():
    """Documenta la limitación medida: la huella mira 30-90 s y nada más.

    Un corte en el minuto 2 deja la ventana intacta, así que la huella da 1.0 y TODA la
    separación la aporta la duración. Si este test empieza a fallar es porque la huella
    pasó a mirar el track completo, que es justamente la mejora pendiente.
    """
    y = _senal(seed=2, dur=200.0)
    r = recortar_del_medio(y, SR, 20.0, posicion=0.6)   # ~120 s, fuera de 30-90
    h_a = huella(y[30 * SR:90 * SR])
    h_b = huella(r[30 * SR:90 * SR])
    assert similitud(h_a, h_b) > 0.999


def test_la_huella_si_ve_un_corte_dentro_de_su_ventana():
    y = _senal(seed=3, dur=200.0)
    r = recortar_del_medio(y, SR, 20.0, posicion=0.25)  # ~50 s, dentro de 30-90
    h_a = huella(y[30 * SR:90 * SR])
    h_b = huella(r[30 * SR:90 * SR])
    assert similitud(h_a, h_b) < 0.99
