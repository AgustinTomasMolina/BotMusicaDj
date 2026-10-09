"""Tests de la onda de 3 bandas y la grilla estimada (`motor/bandas.py`, f52).

El audio se arma acá con numpy y se sabe DÓNDE está cada cosa por construcción:

- kick:  seno de 55 Hz con envolvente exponencial (el de `motor/sintetico.py`): graves.
- hat:   dos senos de 7 y 9,1 kHz con caída rápida (30 ms): agudos.
- stab:  600 + 900 Hz con caída de 80 ms: medios.

Los BPM y los desfases son los de la construcción, nunca un número inventado (spec §5). Lo
esperado sale de esa construcción o de un cálculo independiente (el archivo leído entero y
filtrado de una vez con scipy), no del código bajo prueba.
"""
import math
import os
import sys
import tracemalloc
from pathlib import Path

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import soundfile as sf  # noqa: E402
from scipy import signal  # noqa: E402

import motor.bandas as mb  # noqa: E402
from motor.peaks import UnreadableAudio, cached_peaks  # noqa: E402

SR = 22050          # 220,5 muestras por cuadro de 10 ms: NO divide exacto, a propósito


def _kick(sr=SR):
    t = np.arange(int(0.12 * sr)) / sr
    return np.sin(2 * np.pi * 55 * t) * np.exp(-t * 28)


def _hat(sr=SR):
    t = np.arange(int(0.03 * sr)) / sr
    return 0.25 * (np.sin(2 * np.pi * 7000 * t) + np.sin(2 * np.pi * 9100 * t + 1)) * np.exp(-t * 150)


def _stab(sr=SR):
    t = np.arange(int(0.08 * sr)) / sr
    return 0.4 * (np.sin(2 * np.pi * 600 * t) + 0.5 * np.sin(2 * np.pi * 900 * t)) * np.exp(-t * 40)


def _pegar(y, s, t, sr=SR):
    """Suma `s` en el instante `t` (redondeado a la muestra). Devuelve el instante REAL, o
    None si no entra."""
    i = int(round(t * sr))
    if i < 0 or i + len(s) > len(y):
        return None
    y[i:i + len(s)] += s
    return i / sr


def _patron(bpm, desfase, dur, sr=SR, hats=True, stabs=True, kicks=True):
    """Kick en cada beat, hat a contratiempo (+P/2) y stab en la segunda semicorchea (+P/4).
    Devuelve (y, instantes de kicks, de hats, de stabs)."""
    p = 60.0 / bpm
    y = np.zeros(int(dur * sr))
    k, h, s = _kick(sr), _hat(sr), _stab(sr)
    ks, hs, ss = [], [], []
    j = 0
    while desfase + j * p < dur:
        t = desfase + j * p
        for activo, sonido, off, lista in ((kicks, k, 0.0, ks), (hats, h, p / 2, hs),
                                           (stabs, s, p / 4, ss)):
            if activo and (r := _pegar(y, sonido, t + off, sr)) is not None:
                lista.append(r)
        j += 1
    return y, ks, hs, ss


def _wav(ruta, y, sr=SR):
    sf.write(str(ruta), (0.5 * np.asarray(y)).astype(np.float32), sr, subtype="PCM_16")
    return ruta


def _picos(v, umbral=0.5, radio=3):
    """Cuadros que son máximo local (en ±radio) y pasan el umbral."""
    out = []
    for k in np.flatnonzero(v >= umbral):
        a, b = max(0, k - radio), min(len(v), k + radio + 1)
        if v[k] == v[a:b].max() and (not out or k - out[-1] > radio):
            out.append(int(k))
    return out


def _calzan(picos, instantes, tol=1):
    """¿Cada instante tiene SU pico a ±tol cuadros, y no sobra ningún pico?"""
    esperados = [math.floor(t * mb.TASA_HZ) for t in instantes]
    if len(picos) != len(esperados):
        return False
    return all(abs(p - e) <= tol for p, e in zip(sorted(picos), esperados, strict=True))


# --- (a) cada banda tiene SUS golpes ------------------------------------------------------

@pytest.fixture(scope="module")
def tres_elementos(tmp_path_factory):
    ruta = tmp_path_factory.mktemp("bandas") / "tres.wav"
    y, ks, hs, ss = _patron(128.0, 0.0123, 20.0)
    _wav(ruta, y)
    return mb.compute_bandas(ruta), ks, hs, ss


def test_los_picos_de_cada_banda_son_los_de_su_elemento(tres_elementos):
    b, ks, hs, ss = tres_elementos
    g, m, a = b.valores()
    assert _calzan(_picos(g), ks), f"graves: picos {_picos(g)[:6]}…, kicks en {ks[:6]}…"
    assert _calzan(_picos(a), hs), f"agudos: picos {_picos(a)[:6]}…, hats en {hs[:6]}…"
    assert _calzan(_picos(m), ss), f"medios: picos {_picos(m)[:6]}…, stabs en {ss[:6]}…"


def test_ninguna_banda_copia_los_golpes_de_otra(tres_elementos):
    """Donde suena el kick los agudos están callados y al revés; los medios no repiten ni el
    kick ni el hat (el kick sintético tiene un clic de ataque que SÍ es medios: queda chico)."""
    b, ks, hs, ss = tres_elementos
    g, m, a = b.valores()

    def en(v, instantes):
        return max(v[max(0, math.floor(t * 100) - 1):math.floor(t * 100) + 3].max()
                   for t in instantes)

    assert en(a, ks) < 0.05, "los agudos copian el kick"
    assert en(g, hs) < 0.05, "los graves copian el hat"
    assert en(m, ks) < 0.5 and en(m, hs) < 0.1, "los medios copian el kick o el hat"
    assert en(g, ss) < 0.2 and en(a, ss) < 0.2, "graves o agudos copian el stab"


def test_un_hat_en_un_solo_canal_se_ve(tmp_path):
    """La mezcla mono promedia los canales: un hat solo en el derecho y un kick solo en el
    izquierdo siguen apareciendo cada uno en su banda."""
    y_k, ks, _, _ = _patron(128.0, 0.05, 10.0, hats=False, stabs=False)
    y_h, _, hs, _ = _patron(128.0, 0.05, 10.0, kicks=False, stabs=False)
    ruta = tmp_path / "paneado.wav"
    sf.write(str(ruta), (0.5 * np.stack([y_k, y_h], axis=1)).astype(np.float32), SR,
             subtype="PCM_16")
    g, _, a = mb.compute_bandas(ruta).valores()
    assert _calzan(_picos(g), ks) and _calzan(_picos(a), hs)


# --- el filtrado por segmentos es el mismo que sobre el archivo entero --------------------

def _oraculo(ruta):
    """Las 3 envolventes con el archivo leído ENTERO y filtrado de una vez (scipy a secas, con
    los cortes y el orden documentados), rellenando con silencio afuera del archivo."""
    y, sr = sf.read(str(ruta), dtype="float32", always_2d=True)
    x = y.mean(axis=1, dtype=np.float32).astype(np.float64)
    m = math.ceil(0.1 * sr)
    ext = np.concatenate([np.zeros(m), x, np.zeros(m)])
    lp = signal.butter(4, 150.0, "lowpass", fs=sr, output="sos")
    hp = signal.butter(4, 2500.0, "highpass", fs=sr, output="sos")
    g = signal.sosfiltfilt(lp, ext, padtype=None)[m:-m]
    a = signal.sosfiltfilt(hp, ext, padtype=None)[m:-m]
    bandas = np.abs(np.stack([g, x - g - a, a]))
    n = len(x)
    cuadros = -(-n * 100 // sr)
    bordes = [-(-k * sr // 100) for k in range(cuadros + 1)]
    return np.stack([[banda[i:j].max() for i, j in zip(bordes[:-1], bordes[1:], strict=True)]
                     for banda in bandas]), sr, n


@pytest.fixture(scope="module")
def estereo(tmp_path_factory):
    """12 s estéreo: el patrón en el izquierdo y otro BPM en el derecho, más ruido."""
    rng = np.random.default_rng(7)
    izq, *_ = _patron(128.0, 0.03, 12.0)
    der, *_ = _patron(97.0, 0.2, 12.0, stabs=False)
    der += 0.05 * rng.standard_normal(len(der))
    ruta = tmp_path_factory.mktemp("bandas") / "estereo.wav"
    sf.write(str(ruta), (0.4 * np.stack([izq, der], axis=1)).astype(np.float32), SR,
             subtype="PCM_16")
    return ruta, _oraculo(ruta)


@pytest.mark.parametrize(("bloque", "segmento"), [(1000, 4096), (4096, 1 << 17), (65536, 777),
                                                  (10 ** 7, 1 << 17), (333, 100)])
def test_por_bloques_y_segmentos_da_lo_mismo_que_el_archivo_entero(estereo, bloque, segmento):
    """Bloques que no dividen nada, segmentos más chicos que el margen y más grandes que el
    archivo: el resultado es el del archivo filtrado de una vez."""
    ruta, (esperado, sr, n) = estereo
    env, sr2, ch, total = mb.envolventes(ruta, block_frames=bloque, segmento=segmento)
    assert (sr2, ch, total) == (sr, 2, n)
    assert env.shape == esperado.shape
    np.testing.assert_allclose(env, esperado, rtol=0, atol=2e-6)


def test_la_normalizacion_es_la_documentada(estereo):
    """q = round(min(env / ref, 1) · 255), ref = max(p99 de los cuadros de la banda que pasan
    −40 dBFS, el p99 más alto − 40 dB, −40 dBFS), calculado acá a mano sobre el oráculo."""
    ruta, (esperado, _, n) = estereo
    b = mb.compute_bandas(ruta)
    piso = 10 ** (-40 / 20)
    p = np.array([np.percentile(banda[banda > piso], 99) for banda in esperado])
    ref = np.maximum(p, max(p.max() * 10 ** (-40 / 20), piso))
    q = np.round(np.minimum(esperado / ref[:, None], 1) * 255)
    np.testing.assert_allclose(b.referencias, ref, rtol=1e-5)
    assert np.abs(b.q.astype(int) - q).max() <= 1      # el redondeo de float32 vs float64
    assert b.duration_s == n / SR and (b.sample_rate, b.channels) == (SR, 2)


def test_una_banda_vacia_no_se_estira(tmp_path):
    """Un seno de 55 Hz pulsado: los agudos no tienen NADA y los medios solo la fuga del
    filtro (~−70 dB). Normalizados cada uno por su propio p99 se dibujarían a tope: golpes que
    no se oyen. El piso relativo (−40 dB del más fuerte) los deja chicos, como son. (Entra y
    sale con un fundido: un seno cortado de golpe es un clic, y un clic SÍ tiene medios.)"""
    t = np.arange(10 * SR) / SR
    fundido = 0.5 - 0.5 * np.cos(np.pi * np.minimum(1.0, np.minimum(t, t[-1] - t) / 0.2))
    y = 0.8 * np.sin(2 * np.pi * 55 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t)) * fundido
    g, m, a = mb.compute_bandas(_wav(tmp_path / "sub.wav", y)).valores()
    assert g.max() == 1.0
    assert m.max() < 0.05, f"medios estirados hasta {m.max():.3f}"
    assert a.max() < 0.05, f"agudos estirados hasta {a.max():.3f}"


def test_un_archivo_casi_en_silencio_no_se_dibuja_a_tope(tmp_path):
    """Ruido a −80 dBFS (dither): nada por debajo del piso absoluto de −40 dBFS se estira, así
    que se dibuja casi plano y no como ruido a tope."""
    rng = np.random.default_rng(1)
    y = 2e-4 * rng.standard_normal(5 * SR)
    v = mb.compute_bandas(_wav(tmp_path / "dither.wav", y)).valores()
    assert v.max() < 0.1, f"el silencio se dibuja hasta {v.max():.3f}"


def test_no_toca_el_original(tmp_path):
    ruta = _wav(tmp_path / "o.wav", _patron(128.0, 0.0, 3.0)[0])
    antes = (ruta.read_bytes(), ruta.stat().st_mtime_ns)
    mb.compute_bandas(ruta)
    mb.cached_bandas(ruta, tmp_path / "peaks")
    assert (ruta.read_bytes(), ruta.stat().st_mtime_ns) == antes


def test_memoria_acotada_por_segmentos(tmp_path):
    """120 s estéreo a 44,1 kHz: entero en float64 mono son 42 MB (85 en estéreo). Por
    segmentos, lo que asigna numpy no pasa de unos pocos MB y no crece con la duración."""
    sr = 44100
    t = np.arange(120 * sr, dtype=np.float32) / sr
    y = (0.5 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    ruta = tmp_path / "largo.wav"
    sf.write(str(ruta), np.stack([y, y], axis=1), sr, subtype="PCM_16")
    del t, y
    tracemalloc.start()
    try:
        b = mb.compute_bandas(ruta)
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert b.cuadros == 12000 and b.duration_s == 120.0
    assert pico < 16 * 2 ** 20, f"pico de {pico / 2 ** 20:.1f} MB: no está filtrando por segmentos"


# --- (b) reducir por máximo -----------------------------------------------------------------

def test_reducir_por_maximo_no_pierde_un_golpe_aislado():
    q = np.zeros((3, 36000), dtype=np.uint8)
    q[0, 12345] = 255                       # un kick aislado en 6 minutos
    q[2, 35999] = 128                       # y un hat en el último cuadro
    r = mb.reducir_max(q, 1000)
    assert r.shape == (3, 1000)
    assert r[0, 12345 * 1000 // 36000] == 255 and int(r[0].sum()) == 255, \
        "el kick desapareció o se repartió (¿promedio en vez de máximo?)"
    assert r[2, 999] == 128 and int(r[2].sum()) == 128


def test_reducir_con_menos_cuadros_que_puntos_no_inventa_resolucion():
    q = np.arange(30, dtype=np.uint8).reshape(3, 10)
    np.testing.assert_array_equal(mb.reducir_max(q, 1000), q)


def test_un_kick_aislado_sobrevive_al_achicar_el_tema_entero(tmp_path):
    """60 s de silencio con UN kick a los 37,21 s: reducido a 200 puntos (0,3 s por punto) el
    kick está en su punto, al valor que tiene en la resolución completa."""
    y = np.zeros(60 * SR)
    t = _pegar(y, _kick(), 37.21)
    b = mb.compute_bandas(_wav(tmp_path / "uno.wav", y))
    d, h, r = mb.tramo(b, 0.0, b.duration_s, 200)
    assert (d, h, r.shape) == (0.0, 60.0, (3, 200))
    assert r[0].max() == b.q[0].max() == 255
    assert int(np.argmax(r[0])) == math.floor(t * 100) * 200 // b.cuadros


def test_tramo_bordes_reales_de_los_cuadros(tres_elementos):
    b = tres_elementos[0]
    d, h, r = mb.tramo(b, 1.234, 2.0, 1000)
    assert (d, h) == (1.23, 2.0)
    np.testing.assert_array_equal(r, b.q[:, 123:200])         # 77 cuadros < 1000 puntos
    d, h, r = mb.tramo(b, 0.0, b.duration_s, 500)              # 2000 cuadros → 4 por punto
    np.testing.assert_array_equal(r, b.q.reshape(3, 500, 4).max(axis=2))


# --- (c) la grilla ------------------------------------------------------------------------

def _circular(a, b, p):
    return (a - b + p / 2) % p - p / 2


@pytest.mark.parametrize("bpm", [120.0, 125.0, 128.0, 133.3, 140.0])
@pytest.mark.parametrize("fraccion", [0.0, 0.026, 0.43, 0.987])
def test_la_grilla_recupera_el_desfase_conocido(tmp_path, bpm, fraccion):
    """El primer kick en `fraccion` del beat (0 a casi 1: también el que cae pegado al beat
    siguiente). 120 y 125 BPM dan un número ENTERO de cuadros por beat (sin el «dither» que
    afina la fase entre beats): es el peor caso."""
    p = 60.0 / bpm
    y, ks, _, _ = _patron(bpm, fraccion * p, 40.0)
    v = mb.compute_bandas(_wav(tmp_path / "g.wav", y)).valores()
    g = mb.estimar_grilla(v[0], bpm)
    assert g["primer_beat_s"] is not None, g
    err = _circular(g["primer_beat_s"], ks[0], p)
    assert abs(err) <= 0.010, f"la grilla está {err * 1000:+.1f} ms corrida"
    assert g["confianza"] >= mb.CONFIANZA_MIN and g["bpm_base"] == bpm
    # Y calza en TODO el tema, no solo al principio: cada kick tiene su línea a ±10 ms.
    lineas = g["primer_beat_s"] + g["periodo_s"] * np.round((np.array(ks) - g["primer_beat_s"])
                                                            / g["periodo_s"])
    peor = np.abs(lineas - np.array(ks)).max()
    assert peor <= 0.010, f"con {g['bpm']} BPM la grilla se corre {peor * 1000:.1f} ms"


@pytest.mark.parametrize("error_bpm", [+0.3, -0.12])
def test_la_grilla_afina_un_bpm_de_la_base_un_poco_corrido(tmp_path, error_bpm):
    """El motor mide con error (p95 ~0,12 BPM). En 3 minutos, 0,3 BPM de error corren una
    grilla fija ~0,3 s: sin afinar no habría grilla (o mentiría al final del tema)."""
    bpm = 128.0
    y, ks, _, _ = _patron(bpm, 0.2, 180.0, hats=False)
    v = mb.compute_bandas(_wav(tmp_path / "g.wav", y)).valores()
    g = mb.estimar_grilla(v[0], bpm + error_bpm)
    assert g["primer_beat_s"] is not None, g
    assert abs(g["bpm"] - bpm) <= 0.005 and g["bpm_base"] == bpm + error_bpm
    p = g["periodo_s"]
    # La grilla calza al principio Y al final del tema.
    for t in (ks[0], ks[-1]):
        n = round((t - g["primer_beat_s"]) / p)
        assert abs(g["primer_beat_s"] + n * p - t) <= 0.010, f"a los {t:.1f} s la línea está corrida"


def _bandas_de(tmp_path, y, nombre="x.wav"):
    return mb.compute_bandas(_wav(tmp_path / nombre, y)).valores()


@pytest.mark.parametrize("caso", ["ruido", "silencio", "pad", "kick_y_contratiempo", "bpm_lejos"])
def test_sin_pulso_claro_no_hay_grilla(tmp_path, caso):
    """Sin un golpe de graves que caiga marcado en cada beat, `primer_beat_s` es None y la
    confianza queda debajo del umbral: la pantalla no dibuja una grilla que miente."""
    rng = np.random.default_rng(5)
    bpm = 128.0
    t = np.arange(60 * SR) / SR
    if caso == "ruido":
        y = 0.3 * rng.standard_normal(60 * SR)
    elif caso == "silencio":
        y = np.zeros(60 * SR)
    elif caso == "pad":
        y = 0.4 * np.sin(2 * np.pi * 55 * t) * (1 + 0.3 * np.sin(2 * np.pi * 0.2 * t))
    elif caso == "kick_y_contratiempo":
        # Un kick igual de fuerte en cada corchea: ¿cuál es el beat? No se sabe.
        y = _patron(bpm, 0.1, 60.0, hats=False, stabs=False)[0] \
            + _patron(bpm, 0.1 + 30 / bpm, 60.0, hats=False, stabs=False)[0]
    else:
        # Los kicks están a 128, la base dice 129: fuera de la ventana de afinado.
        y = _patron(bpm, 0.1, 360.0, hats=False, stabs=False)[0]
        bpm = 129.0
    g = mb.estimar_grilla(_bandas_de(tmp_path, y)[0], bpm)
    assert g["primer_beat_s"] is None and g["confianza"] < mb.CONFIANZA_MIN, g
    assert g["motivo"]


def test_sin_bpm_medido_no_hay_grilla():
    g = mb.estimar_grilla(np.ones(1000), 0.0)
    assert (g["primer_beat_s"], g["bpm"], g["motivo"]) == (None, None, "el track no tiene BPM medido")


# --- el compás ----------------------------------------------------------------------------

def _arreglo(bpm, desfase, pickup, cambios=True, acento_en_el_3=False):
    """100 beats. El compás 0 empieza en el beat `pickup` (los anteriores son la anacrusa).
    Con `cambios`: los hats entran en el compás 4, los stabs en el 8, el kick se va en el 12 y
    vuelve en el 14: todo en el 1. Devuelve (y, instante del primer kick, instante del primer 1)."""
    p = 60.0 / bpm
    y = np.zeros(int((desfase + 100 * p + 1) * SR))
    k, h, s = _kick(), _hat(), _stab()
    primero = uno = None
    for j in range(100):
        t = desfase + j * p
        compas, en_el_compas = divmod(j - pickup, 4)
        if not (cambios and 12 <= compas < 14):
            r = _pegar(y, k, t)
            primero = primero if primero is not None else r
            if en_el_compas == 0 and compas >= 0 and uno is None:
                uno = r
        if not cambios or compas >= 4:
            _pegar(y, h, t + p / 2)
        if (not cambios or compas >= 8) and not acento_en_el_3:
            _pegar(y, s, t + p / 4)
        if acento_en_el_3 and en_el_compas == 2:
            _pegar(y, s, t + p / 4)
    return y, primero, uno


@pytest.mark.parametrize(("bpm", "desfase", "pickup"), [(128.0, 0.1, 2), (128.0, 0.1, 0),
                                                         (124.0, 0.33, 1), (132.0, 0.05, 3)])
def test_el_1_del_compas_sale_de_los_cambios_de_arreglo(tmp_path, bpm, desfase, pickup):
    y, primero, uno = _arreglo(bpm, desfase, pickup)
    b = mb.compute_bandas(_wav(tmp_path / "a.wav", y))
    g = mb.grilla(b, bpm)
    assert g["compas_ref"] is not None, g
    assert abs(g["compas_ref"] - uno) <= 0.010, \
        f"el 1 quedó en {g['compas_ref']:.3f} s; es {uno:.3f} s (primer kick {primero:.3f} s)"
    assert g["compas_confianza"] >= mb.CONFIANZA_COMPAS_MIN and g["compas_motivo"] is None


@pytest.mark.parametrize("caso", ["loop", "acento_en_el_3"])
def test_sin_cambios_de_arreglo_no_se_inventa_el_1(tmp_path, caso):
    """Un loop que no cambia, o uno con un acento que se repite en el 3 de cada compás: no hay
    forma de saber cuál es el 1 (el acento podría estar en cualquier beat). `compas_ref` None,
    aunque la grilla de beats sí exista."""
    y, _, _ = _arreglo(128.0, 0.1, 2, cambios=False, acento_en_el_3=caso == "acento_en_el_3")
    g = mb.grilla(mb.compute_bandas(_wav(tmp_path / "l.wav", y)), 128.0)
    assert g["primer_beat_s"] is not None, "el test no probaría nada: no hay grilla de beats"
    assert g["compas_ref"] is None and g["compas_motivo"], g


def test_la_grilla_lista_para_json(tres_elementos):
    b = tres_elementos[0]
    g = mb.grilla(b, 128.0)
    assert set(g) == {"estimada", "bpm", "bpm_base", "periodo_s", "primer_beat_s", "confianza",
                      "motivo", "beats_por_compas", "compas_ref", "compas_confianza",
                      "compas_motivo"}
    assert g["estimada"] is True and g["beats_por_compas"] == 4
    for k in ("bpm", "bpm_base", "periodo_s", "primer_beat_s", "confianza", "compas_confianza"):
        assert type(g[k]) is float, (k, type(g[k]))
    assert abs(g["periodo_s"] - 60 / g["bpm"]) < 1e-5


# --- (e) la caché ---------------------------------------------------------------------------

@pytest.fixture
def corto(tmp_path):
    return _wav(tmp_path / "corto.wav", _patron(128.0, 0.05, 8.0)[0])


def test_la_cache_no_recalcula_y_devuelve_lo_mismo(corto, tmp_path, monkeypatch):
    cache = tmp_path / "peaks"
    primero, de_cache = mb.cached_bandas(corto, cache)
    assert de_cache is False
    directo = mb.compute_bandas(corto)
    np.testing.assert_array_equal(primero.q, directo.q)

    def no_recalcular(*a, **k):
        raise AssertionError("recalculó con la caché vigente")

    monkeypatch.setattr(mb, "compute_bandas", no_recalcular)
    segundo, de_cache = mb.cached_bandas(corto, cache)
    assert de_cache is True
    np.testing.assert_array_equal(segundo.q, primero.q)
    assert (segundo.referencias, segundo.duration_s, segundo.sample_rate, segundo.channels) == \
        (primero.referencias, primero.duration_s, primero.sample_rate, primero.channels)
    (archivo,) = list(cache.glob("*" + mb.CACHE_SUFIJO))
    assert archivo.stat().st_size < 3 * primero.cuadros, "la caché no está comprimida"


def test_si_el_archivo_cambia_se_recalcula(corto, tmp_path):
    cache = tmp_path / "peaks"
    viejo, _ = mb.cached_bandas(corto, cache)
    _wav(corto, _patron(128.0, 0.05, 8.0, kicks=False)[0])        # mismo nombre, sin kicks
    nuevo, de_cache = mb.cached_bandas(corto, cache)
    assert de_cache is False
    np.testing.assert_array_equal(nuevo.q, mb.compute_bandas(corto).q)
    assert not np.array_equal(nuevo.q, viejo.q), "devolvió las bandas del archivo viejo"


@pytest.mark.parametrize("veneno", ["basura", "vacio", "otra_magia", "zlib_cortado",
                                    "cuadros_de_mas", "referencia_nan", "otra_version"])
def test_una_cache_con_otra_forma_se_rehace(corto, tmp_path, veneno):
    import json
    import struct

    cache = tmp_path / "peaks"
    bueno, _ = mb.cached_bandas(corto, cache)
    (archivo,) = list(cache.glob("*" + mb.CACHE_SUFIJO))
    datos = archivo.read_bytes()
    (largo,) = struct.unpack("<I", datos[4:8])
    enc = json.loads(datos[8:8 + largo])
    cuerpo = datos[8 + largo:]

    def con(enc2):
        cab = json.dumps(enc2).encode()
        return datos[:4] + struct.pack("<I", len(cab)) + cab + cuerpo

    archivo.write_bytes({"basura": b"esto no es una cache" * 10, "vacio": b"",
                         "otra_magia": b"XXXX" + datos[4:],
                         "zlib_cortado": datos[:-10],
                         "cuadros_de_mas": con({**enc, "cuadros": enc["cuadros"] + 1}),
                         "referencia_nan": con({**enc, "referencias": [float("nan")] * 3}),
                         "otra_version": con({**enc, "version": enc["version"] + 1})}[veneno])
    b, de_cache = mb.cached_bandas(corto, cache)
    assert de_cache is False
    np.testing.assert_array_equal(b.q, bueno.q)


def test_la_poda_de_bandas_no_toca_los_picos_y_al_reves(tmp_path):
    """Las dos cachés viven en la misma carpeta con el mismo tope de archivos, cada una
    contando y podando lo suyo."""
    cache = tmp_path / "peaks"
    rutas = [_wav(tmp_path / f"t{i}.wav", np.full(2205, 0.1 * (i + 1))) for i in range(5)]
    for r in rutas:
        cached_peaks(r, cache, bins=10, tope=3)
    picos = sorted(p.name for p in cache.glob("*.json"))
    assert len(picos) <= 3
    for r in rutas:
        mb.cached_bandas(r, cache, tope=3)
    assert len(list(cache.glob("*" + mb.CACHE_SUFIJO))) <= 3, "las bandas no se podan"
    assert sorted(p.name for p in cache.glob("*.json")) == picos, "la poda de bandas borró picos"
    for r in rutas:
        cached_peaks(r, cache, bins=10, tope=3)
    assert len(list(cache.glob("*" + mb.CACHE_SUFIJO))) >= 1, "la poda de picos borró bandas"


# --- errores --------------------------------------------------------------------------------

def test_archivo_que_no_existe(tmp_path):
    with pytest.raises(FileNotFoundError):
        mb.compute_bandas(tmp_path / "no.wav")
    with pytest.raises(FileNotFoundError):
        mb.cached_bandas(tmp_path / "no.wav", tmp_path / "peaks")


def test_archivo_que_no_es_audio(tmp_path):
    ruta = tmp_path / "roto.wav"
    ruta.write_bytes(b"RIFF\x00\x00\x00\x00WAVEesto no es audio" * 10)
    with pytest.raises(UnreadableAudio):
        mb.compute_bandas(ruta)


def test_flac_que_falla_a_mitad_del_stream(tmp_path):
    ruta = tmp_path / "danado.flac"
    sf.write(str(ruta), (0.4 * _patron(128.0, 0.0, 6.0)[0]).astype(np.float32), SR, format="FLAC")
    b = ruta.read_bytes()
    ruta.write_bytes(b[:len(b) // 2])
    with pytest.raises(UnreadableAudio, match="se corta o está dañado"):
        mb.compute_bandas(ruta)


def test_muestras_nan_son_audio_ilegible(tmp_path):
    y = np.full((SR, 1), 0.5, dtype=np.float32)
    y[100] = np.nan
    ruta = tmp_path / "nan.wav"
    sf.write(str(ruta), y, SR, subtype="FLOAT")
    with pytest.raises(UnreadableAudio, match="NaN"):
        mb.compute_bandas(ruta)


def test_muestreo_demasiado_bajo_para_los_agudos(tmp_path):
    ruta = tmp_path / "telefono.wav"
    sf.write(str(ruta), np.zeros(4000, dtype=np.float32), 4000)
    with pytest.raises(UnreadableAudio, match="muy baja"):
        mb.compute_bandas(ruta)


def test_audioread_por_bloques_da_lo_mismo(corto, monkeypatch):
    """Por el camino de audioread (MP3/M4A vía ffmpeg) llegan pedazos de PCM que no caen en
    un frame. Un decodificador falso con los datos del WAV: mismas bandas que soundfile."""
    import audioread

    y, sr = sf.read(str(corto), dtype="int16", always_2d=True)

    class Falso:
        samplerate, channels = sr, y.shape[1]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

        def __iter__(self):
            datos = y.astype("<i2").tobytes()
            for i in range(0, len(datos), 4097):
                yield datos[i:i + 4097]

    esperado = mb.compute_bandas(corto)
    monkeypatch.setattr(audioread, "audio_open", lambda _ruta: Falso())
    import soundfile

    def no_abre(*a, **k):
        raise RuntimeError("formato no soportado")

    monkeypatch.setattr(soundfile, "SoundFile", no_abre)
    b = mb.compute_bandas(corto)
    np.testing.assert_array_equal(b.q, esperado.q)
    assert b.duration_s == esperado.duration_s


def test_la_cache_de_bandas_va_en_la_carpeta_de_los_picos(corto, tmp_path):
    cache = tmp_path / "peaks"
    mb.cached_bandas(corto, cache)
    cached_peaks(corto, cache)
    nombres = sorted(p.suffix for p in cache.iterdir())
    assert nombres == [mb.CACHE_SUFIJO, ".json"], nombres
    # Misma clave (la ruta): el mismo nombre de archivo, otra extensión.
    assert len({Path(p).stem for p in cache.iterdir()}) == 1
