"""Tests del fixture de recortes (tarea 5.25).

El barrido contra un track real se corre a mano (ver el __main__ de recortes.py); acá se
verifica que el generador haga lo que dice y que la compuerta de duración se comporte como
se midió, usando señal sintética para que corra siempre.
"""
import numpy as np

from calidad.duplicados import (
    SR_HUELLA,
    Ficha,
    huella,
    mismo_audio,
    muestra_para_huella,
    similitud,
)
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
    a = Ficha("a", "a", y.size / SR, huella(muestra_para_huella(y)))
    b = Ficha("b", "b", r.size / SR, huella(muestra_para_huella(r)))
    agrupa, _ = mismo_audio(a, b)
    assert not agrupa, "un edit de 30 s no puede agruparse con el original"


def _reemplazar(y, otra, desde_s, hasta_s):
    """Copia de `y` con [desde_s, hasta_s) cambiado por audio de `otra`: MISMA duración, medio
    distinto. Es el caso que la compuerta de duración no puede ver."""
    z = y.copy()
    a, b = int(desde_s * SR), int(hasta_s * SR)
    z[a:b] = otra[a:b]
    return z


def test_la_huella_vieja_era_ciega_fuera_de_30_90_s():
    """Documenta la limitación que tenía la huella (tarea 5.25) y que la 5.27 corrigió: con un
    bloque contiguo de 30 a 90 s, un tramo distinto en 120-160 s daba similitud 1.0.

    Es la VENTANA VIEJA armada a mano: sirve de control para que el test de abajo no pase
    por una señal que ya se vea distinta en 30-90 s."""
    y = _senal(seed=5, dur=200.0)
    z = _reemplazar(y, _senal(seed=105, dur=200.0), 120.0, 160.0)
    assert similitud(huella(y[30 * SR:90 * SR]), huella(z[30 * SR:90 * SR])) > 0.999


def test_la_huella_ve_un_medio_distinto_de_la_misma_duracion():
    """Criterio de la 5.27: dos archivos de MISMA duración y distinto contenido en el medio
    (120-160 s, fuera de lo que miraba la huella vieja) NO se agrupan. Antes se agrupaban
    con similitud 1.0 porque toda la separación la ponía una duración que acá es igual."""
    y = _senal(seed=5, dur=200.0)
    z = _reemplazar(y, _senal(seed=105, dur=200.0), 120.0, 160.0)
    a = Ficha("a", "a", y.size / SR, huella(muestra_para_huella(y)))
    b = Ficha("b", "b", z.size / SR, huella(muestra_para_huella(z)))
    agrupa, sim = mismo_audio(a, b)
    assert not agrupa, f"la huella no vio 40 s distintos en el medio (similitud {sim:.4f})"
    assert sim < 0.85


def test_la_misma_senal_con_otra_ganancia_sigue_agrupada():
    """Contrapeso del anterior: ver todo el track no puede volver a la huella quisquillosa.
    El mismo audio a otro volumen es el duplicado de siempre."""
    y = _senal(seed=5, dur=200.0)
    a = Ficha("a", "a", y.size / SR, huella(muestra_para_huella(y)))
    b = Ficha("b", "b", y.size / SR, huella(muestra_para_huella(y * 0.5)))
    agrupa, sim = mismo_audio(a, b)
    assert agrupa and sim > 0.99


def test_la_huella_ve_un_corte_con_el_resto_igual():
    """El corte del medio (cambia la duración) también se ve en la huella nueva, aunque acá
    la separación ya la haría la compuerta de duración: la huella sola da distinto."""
    y = _senal(seed=3, dur=200.0)
    r = recortar_del_medio(y, SR, 20.0, posicion=0.25)
    h_a = huella(muestra_para_huella(y))
    h_b = huella(muestra_para_huella(r))
    assert similitud(h_a, h_b) < 0.99
