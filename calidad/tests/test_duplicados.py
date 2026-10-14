"""Tests de la tarea 5.2 (duplicados).

Las compuertas y el ranking son lógica pura. La huella se prueba con señales sintéticas
construidas, no con afirmaciones sobre archivos reales (spec §5).
"""
import os
import plistlib
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pytest

import motor.decodificar as dec
from calidad.corte_espectral import FilaCalidad
from calidad.duplicados import (
    CONSERVAR,
    DESCARTAR,
    N_TRAMOS,
    Ficha,
    _puntaje_calidad,
    agrupar,
    cargar_ficha,
    decidir,
    duracion_compatible,
    huella,
    main,
    mismo_audio,
    muestra_para_huella,
    normalizar_ruta,
    referencias_de_xml,
    referencias_itunes,
    referencias_rekordbox,
    similitud,
    unir_referencias,
    ventanas_huella,
)

SR = 22050


# --- Compuerta de duración ---------------------------------------------------------------


def test_duraciones_casi_iguales_son_compatibles():
    """Mismo audio en dos formatos: difiere el padding del encoder, milisegundos."""
    assert duracion_compatible(252.9, 253.2)
    assert duracion_compatible(122.5, 122.5)


def test_duraciones_distintas_no_son_duplicado():
    """El caso ProyectMoli: 8.8 s vs 11.4 s son dos tomas, no dos copias."""
    assert not duracion_compatible(8.8, 11.4)


def test_la_tolerancia_es_relativa_en_tracks_largos():
    assert duracion_compatible(600.0, 605.0)      # 5 s sobre 600 = 0.8%
    assert not duracion_compatible(600.0, 620.0)  # 20 s = 3.3%


def test_duracion_cero_nunca_compatible():
    assert not duracion_compatible(0.0, 0.0)
    assert not duracion_compatible(120.0, 0.0)


# --- Huella --------------------------------------------------------------------------------


def _senal(seed: int, dur: float = 70.0, sr: int = SR) -> np.ndarray:
    """Señal con estructura temporal: bandas que entran y salen en distinto momento."""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    y = np.zeros(n, dtype=np.float64)
    for f in rng.uniform(80, 5000, size=6):
        ini, fin = sorted(rng.uniform(0, dur, size=2))
        env = ((t >= ini) & (t <= fin)).astype(np.float64)
        y += env * np.sin(2 * np.pi * f * t) * rng.uniform(0.2, 1.0)
    y += 0.01 * rng.standard_normal(n)
    return (y / (np.abs(y).max() + 1e-9)).astype(np.float32)


def _senal_densa(seed: int, dur: float = 70.0, sr: int = SR) -> np.ndarray:
    """Señal DENSA: piso de ruido y ~4 eventos por segundo (cada uno un tono con envolvente de
    0,1 a 1,5 s en cualquier banda). Con la `_senal` rala (6 tonos en total) casi cualquier
    tramo se parece a cualquier otro y un test de huella pasa por casualidad."""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    y = 0.02 * rng.standard_normal(n)
    for _ in range(int(dur * 4)):
        ini = rng.uniform(0, max(0.01, dur - 1.6))
        a = int(ini * sr)
        b = min(n, a + int(rng.uniform(0.1, 1.5) * sr))
        t = np.arange(b - a) / sr
        y[a:b] += rng.uniform(0.2, 1.0) * np.hanning(b - a) * np.sin(
            2 * np.pi * rng.uniform(80, 5000) * t)
    return (y / (np.abs(y).max() + 1e-9)).astype(np.float32)

def test_la_misma_senal_da_huella_identica():
    y = _senal(1)
    assert similitud(huella(y), huella(y.copy())) == pytest.approx(1.0)


def test_senales_distintas_dan_huellas_distintas():
    s = similitud(huella(_senal(1)), huella(_senal(2)))
    assert s < 0.9, f"dos señales distintas correlacionaron {s:.3f}"


def test_la_huella_aguanta_perder_los_agudos():
    """Un MP3 corta arriba de 16 kHz; la huella llega hasta 6 kHz, así que no se entera."""
    y = _senal(3)
    esp = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(y.size, 1.0 / SR)
    esp[freqs > 8000] = 0.0                       # simula el corte de un codec
    recortada = np.fft.irfft(esp, y.size).astype(np.float32)
    assert similitud(huella(y), huella(recortada)) > 0.95


def test_audio_muy_corto_no_tiene_huella():
    assert huella(np.zeros(SR, dtype=np.float32)) is None


def test_silencio_no_tiene_huella():
    """Sin variación no hay nada que normalizar; mejor None que un vector de ceros."""
    assert huella(np.zeros(SR * 70, dtype=np.float32)) is None


def test_similitud_tolera_none():
    assert similitud(None, huella(_senal(1))) == 0.0


# --- Dónde mira la huella (tarea 5.27) -------------------------------------------------------


def test_los_tramos_se_reparten_por_todo_el_track():
    """346 s: desde 30 s (intro salteada) hasta el final, cada 13 s. Los valores salen de
    la cuenta a mano: (346 - 30) / 24 = 13.17 -> 13.0 (múltiplo de 0.5 hacia abajo)."""
    v = ventanas_huella(346.0)
    assert len(v) == 24
    assert v[0] == (30.0, 2.5)
    assert v[1] == (43.0, 2.5)
    assert v[-1] == (329.0, 2.5), "el último tramo no llega cerca del final del track"
    assert all(largo == 2.5 for _, largo in v)


def test_el_ultimo_tramo_siempre_entra_en_el_track():
    for dur in (100.5, 110.0, 130.0, 200.0, 346.0, 600.0, 1800.0):
        off, largo = ventanas_huella(dur)[-1]
        assert off + largo <= dur, f"el último tramo se sale de un track de {dur} s"


def test_la_huella_cubre_casi_todo_el_track_util():
    """La cola sin mirar es, como mucho, un paso entre tramos más 12 s (N_TRAMOS x 0.5 s del
    redondeo hacia abajo del paso): menos de ~2 porciones de 24, nunca un bloque grande."""
    for dur in (130.0, 200.0, 346.0, 600.0, 1800.0):
        v = ventanas_huella(dur)
        paso = v[1][0] - v[0][0]
        cola = dur - (v[-1][0] + v[-1][1])
        assert cola < paso + 12.0, f"{dur} s: quedan {cola:.1f} s sin mirar (paso {paso} s)"
        assert v[-1][0] > 0.9 * dur - 30, f"{dur} s: el último tramo está muy lejos del final"


def test_un_track_corto_se_analiza_entero_en_una_ventana():
    assert ventanas_huella(50.0) == [(0.0, 50.0)]


def test_se_saltea_la_intro_solo_en_tracks_largos():
    assert ventanas_huella(100.0)[0][0] == 0.0      # justo en el borde: no se saltea
    assert ventanas_huella(100.5)[0][0] == 30.0


def test_duracion_cero_o_negativa_no_tiene_ventanas():
    assert ventanas_huella(0.0) == []
    assert ventanas_huella(-3.0) == []


def test_dos_copias_que_difieren_decimas_de_segundo_miran_los_mismos_tramos():
    """Medido con audio real: dos MP3 del mismo tema de 253.16 s y 253.50 s. Con tramos
    proporcionales a la duración exacta la similitud caía de 0.998 a 0.65; con el paso
    cuantizado miran EXACTAMENTE lo mismo."""
    assert ventanas_huella(253.16) == ventanas_huella(253.50)
    assert ventanas_huella(122.48) == ventanas_huella(122.49)


def test_la_muestra_son_los_tramos_pegados_y_miden_siempre_lo_mismo():
    """`huella` parte la muestra en N_TRAMOS iguales: la muestra tiene que ser N_TRAMOS
    tramos de largo exacto, y cada uno tiene que ser el que dice `ventanas_huella`."""
    y = np.arange(SR * 200, dtype=np.float32)
    m = muestra_para_huella(y, SR)
    largo = int(2.5 * SR)
    assert m.size == N_TRAMOS * largo
    for k, (off, _) in enumerate(ventanas_huella(200.0)):
        assert m[k * largo] == int(round(off * SR)), f"el tramo {k} no sale de {off} s"


def _wav(tmp_path, y, nombre="t.wav", sr=SR):
    import soundfile as sf

    ruta = tmp_path / nombre
    sf.write(str(ruta), y, sr, subtype="PCM_16")
    return str(ruta)


def _con_duracion_declarada(monkeypatch, declarada):
    """`cargar_tramos` armando la grilla con una duración que MIENTE (la declarada) en vez de
    la del archivo: un VBR sin cabecera. La pasada secuencial de respaldo no se toca."""
    real = dec.cargar_tramos

    def miente(ruta, sr, ventanas_de, mono=True):
        return real(ruta, sr, lambda _d: ventanas_de(declarada), mono)

    monkeypatch.setattr(dec, "cargar_tramos", miente)


def _referencia_con_una_apertura_por_tramo(ruta):
    """La huella como la calculaba la versión que abría el archivo 24 veces (`cargar` por
    tramo): la referencia bit a bit de la que lee con UNA apertura."""
    muestra = []
    for off, largo in ventanas_huella(dec.duracion(ruta)):
        y, _ = dec.cargar(ruta, sr=SR, mono=True, offset=off, duration=largo)
        muestra.append(y[:int(round(largo * SR))])
    return huella(np.concatenate(muestra), SR)


def test_cargar_ficha_abre_el_archivo_una_sola_vez_y_da_la_huella_de_siempre(
        tmp_path, monkeypatch):
    """Una apertura y 24 seeks (antes: 26 aperturas, que en MP3 escanean el archivo cada una),
    con LA MISMA huella bit a bit que la de abrir por tramo."""
    import soundfile as sf

    y = _senal_densa(11, dur=200.0, sr=44100)
    ruta = _wav(tmp_path, y, sr=44100)          # 44,1 kHz nativo: hay resample por tramo
    aperturas = []
    real = sf.SoundFile

    class Espia(real):
        def __init__(self, *a, **k):
            aperturas.append(a[0])
            super().__init__(*a, **k)

    monkeypatch.setattr(sf, "SoundFile", Espia)
    f = cargar_ficha(ruta)
    monkeypatch.setattr(sf, "SoundFile", real)
    assert len(aperturas) == 1, f"abrió el archivo {len(aperturas)} veces"
    assert f.grilla == tuple(ventanas_huella(f.duracion_s))
    referencia = _referencia_con_una_apertura_por_tramo(ruta)
    assert np.array_equal(f.huella, referencia), "no es la huella de abrir por tramo"


def test_cargar_ficha_en_memoria_y_en_disco_coinciden(tmp_path):
    """La muestra en memoria (`muestra_para_huella`) y la leída del archivo son el mismo
    audio: lo único que cambia es el redondeo de PCM de 16 bit."""
    y = _senal_densa(12, dur=200.0)
    ruta = _wav(tmp_path, y)
    decodificado, _ = dec.cargar(ruta, sr=SR, mono=True)
    en_memoria = huella(muestra_para_huella(decodificado, SR))
    assert similitud(cargar_ficha(ruta).huella, en_memoria) > 0.9999


@pytest.mark.parametrize("declarada,real", [
    (260.0, 200.0),   # el último tramo cae PASADO el final: buscar ahí tira error
    (210.0, 204.0),   # el último tramo (202.5-205 s) cae a medias: viene corto (1.5 s de 2.5)
])
def test_si_la_duracion_declarada_no_coincide_con_el_audio_se_lee_con_la_real(
        tmp_path, monkeypatch, declarada, real):
    """Un VBR sin cabecera puede declarar más duración de la que tiene. Los últimos tramos
    vendrían vacíos o a medias: en vez de una huella con ceros, una pasada secuencial arma
    la grilla con lo que realmente hay."""
    y = _senal_densa(12, dur=real)
    ruta = _wav(tmp_path, y)
    _con_duracion_declarada(monkeypatch, declarada)
    f = cargar_ficha(ruta)
    assert f.grilla == tuple(ventanas_huella(real)), "la grilla no es la del audio real"
    decodificado, _ = dec.cargar(ruta, sr=SR, mono=True)
    esperado = huella(muestra_para_huella(decodificado, SR))
    assert f.huella is not None
    assert similitud(f.huella, esperado) > 0.9999, "no midió sobre el audio real"


def test_un_ultimo_tramo_apenas_corto_se_completa_con_silencio_sin_correr_los_demas(
        tmp_path):
    """Si al último tramo le faltan unas décimas, se completa con silencio hasta su largo
    exacto: `huella` parte la muestra en N_TRAMOS iguales, y un tramo corto correría todos
    los límites. (Función pura: `_muestra_de_tramos`.)"""
    from calidad.duplicados import _muestra_de_tramos

    n = int(2.5 * SR)
    ventanas = [(0.0, 2.5), (10.0, 2.5)]
    tramos = [np.ones(n, dtype=np.float32), np.ones(n - 2205, dtype=np.float32)]   # falta 0,1 s
    m = _muestra_de_tramos(tramos, ventanas)
    assert m.size == 2 * n
    assert m[n - 1] == 1.0 and m[2 * n - 2205 - 1] == 1.0 and m[2 * n - 1] == 0.0
    assert _muestra_de_tramos([tramos[0], tramos[1][:n // 2]], ventanas) is None, \
        "un tramo a la mitad no es 'apenas corto': la grilla no sirve"


def test_cargar_ficha_de_archivo_ilegible_no_tiene_huella_y_dice_por_que(tmp_path, caplog):
    ruta = tmp_path / "roto.wav"
    ruta.write_bytes(b"esto no es audio")
    with caplog.at_level("WARNING", logger="calidad.duplicados"):
        f = cargar_ficha(str(ruta))
    assert f.huella is None
    assert any("roto.wav" in r.getMessage() and "sin huella" in r.getMessage()
               for r in caplog.records), "no avisó por qué quedó sin huella"


# --- Copias que cruzan el múltiplo de 12 s: se comparan sobre los mismos tramos -------------


def _con_cola_de_silencio(y, seg, sr=SR):
    return np.concatenate([y, np.zeros(int(round(seg * sr)), dtype=np.float32)])


def _sin_cola(y, seg, sr=SR):
    return y[:y.size - int(round(seg * sr))].copy()


def _reemplazar(y, otra, desde_s, hasta_s):
    """Copia de `y` con [desde_s, hasta_s) tomado de `otra`: misma duración, otro contenido."""
    z = y.copy()
    a, b = int(desde_s * SR), int(hasta_s * SR)
    z[a:b] = otra[a:b]
    return z

def _comparar_con_variantes(tmp_path, y, variantes):
    """Ficha del original contra cada variante (escrita a disco y borrada). Devuelve
    `[(variante, similitud, ¿agrupa?, ¿las grillas difieren?)]`."""
    base = cargar_ficha(_wav(tmp_path, y, "base.wav"))
    filas = []
    for etiqueta, z in variantes:
        ruta = _wav(tmp_path, z, "variante.wav")
        otra = cargar_ficha(ruta)
        agrupa, s = mismo_audio(base, otra)
        filas.append((etiqueta, s, agrupa, base.grilla != otra.grilla))
        os.unlink(ruta)
    return filas


@pytest.mark.parametrize("generar", [_senal, _senal_densa], ids=["ralo", "denso"])
def test_95_vs_96_s_agrupan_en_todo_el_barrido_de_cola(tmp_path, generar):
    """95 s -> 96 s cruza el múltiplo de 12 s de la grilla (paso 3,5 s -> 4,0 s): sin
    compararlas sobre los mismos tramos, la similitud caía a ~0 en 4 de 10 pares. Con 1 s de
    silencio de cola, y con cada décima de cola hasta el borde de la tolerancia de 2 s."""
    y = generar(21, dur=95.0)
    colas = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
    filas = _comparar_con_variantes(tmp_path, y, [(c, _con_cola_de_silencio(y, c))
                                                  for c in colas])
    assert any(difieren for *_, difieren in filas), "el barrido no cruza ninguna grilla"
    malas = [(c, round(s, 3)) for c, s, agrupa, _ in filas if not agrupa]
    assert not malas, f"copias del mismo audio que NO agrupan (cola, similitud): {malas}"


@pytest.mark.parametrize("generar", [_senal, _senal_densa], ids=["ralo", "denso"])
def test_346_s_con_la_cola_recortada_agrupa_en_todo_el_barrido(tmp_path, generar):
    """346 s con la cola recortada: de 0,5 a 6,5 s (la tolerancia de duración es 2 % = 6,8 s).
    Cruza el múltiplo de 12 s (342 s): antes caía hasta ~0 sobre grillas distintas."""
    y = generar(22, dur=346.0)
    cortes = [0.5, 1.0, 2.0, 3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5]
    filas = _comparar_con_variantes(tmp_path, y, [(c, _sin_cola(y, c)) for c in cortes])
    assert any(difieren for *_, difieren in filas), "el barrido no cruza ninguna grilla"
    malas = [(c, round(s, 3)) for c, s, agrupa, _ in filas if not agrupa]
    assert not malas, f"copias del mismo audio que NO agrupan (corte, similitud): {malas}"


def test_la_similitud_no_depende_del_orden_de_los_argumentos(tmp_path):
    y = _senal_densa(23, dur=95.0)
    a = cargar_ficha(_wav(tmp_path, y, "a.wav"))
    b = cargar_ficha(_wav(tmp_path, _con_cola_de_silencio(y, 1.0), "b.wav"))
    assert a.grilla != b.grilla
    assert mismo_audio(a, b) == mismo_audio(b, a)
    assert mismo_audio(a, b)[1] > 0.99


def test_un_track_corto_de_una_sola_ventana_se_compara_sobre_la_mas_corta(tmp_path):
    """50,0 s vs 50,8 s: la ventana es el track entero y mide distinto en cada uno. Se
    recalcula la LARGA sobre la ventana de la corta (al revés, la corta no tiene esos 0,8 s
    y no habría con qué comparar), en cualquier orden de los argumentos."""
    y = _senal_densa(28, dur=50.0)
    a = cargar_ficha(_wav(tmp_path, y, "a.wav"))
    b = cargar_ficha(_wav(tmp_path, _con_cola_de_silencio(y, 0.8), "b.wav"))
    assert len(a.grilla) == len(b.grilla) == 1 and a.grilla != b.grilla
    for x, z in ((a, b), (b, a)):
        agrupa, s = mismo_audio(x, z)
        assert agrupa and s > 0.99, f"no agrupó dos copias cortas (similitud {s:.3f})"


def test_dos_grillas_cortas_distintas_contra_el_mismo_original_no_comparten_cache(tmp_path):
    """El original (50,8 s) se recalcula DOS veces, una por cada grilla corta (50,0 s y 50,4 s):
    la caché de `agrupar` tiene que distinguir la grilla, no solo la ruta. Con la clave
    `(ruta,)` la segunda comparación reusaba la huella de la grilla equivocada."""
    y = _senal_densa(29, dur=50.8)
    larga = cargar_ficha(_wav(tmp_path, y, "larga.wav"))
    c1 = cargar_ficha(_wav(tmp_path, _sin_cola(y, 0.8), "c1.wav"))
    c2 = cargar_ficha(_wav(tmp_path, _sin_cola(y, 0.4), "c2.wav"))
    assert len({larga.grilla, c1.grilla, c2.grilla}) == 3
    grupos, sims = agrupar([larga, c1, c2])
    assert grupos == [[0, 1, 2]]
    for par in ((0, 1), (0, 2), (1, 2)):
        assert sims[par] > 0.999, f"el par {par} se comparó sobre grillas distintas ({sims[par]:.4f})"


def test_un_contenedor_que_declara_de_mas_se_compara_por_su_duracion_real(tmp_path, monkeypatch):
    """Un M4A (ffmpeg falso) que declara 8 s y trae 6 s contra un WAV de 6,5 s con el mismo
    audio y 0,5 s de silencio. Si la ficha del M4A conservara los 8 s declarados pasaría por
    'la larga' y se recalcularía sobre una grilla que no tiene (6,5 s): no agruparía."""
    import soundfile as sf

    from motor.tests.test_decodificar import Falso

    falso = Falso(tmp_path, monkeypatch)
    falso.entrega(declara=8.0)
    audio, sr = sf.read(str(falso.wav), dtype="float32", always_2d=True)
    con_silencio = np.concatenate([audio, np.zeros((sr // 2, audio.shape[1]), np.float32)])
    wav = tmp_path / "seis_y_medio.wav"
    sf.write(str(wav), con_silencio, sr, subtype="PCM_16")
    m4a, w = cargar_ficha(str(falso.m4a)), cargar_ficha(str(wav))
    assert m4a.duracion_s == pytest.approx(6.0, abs=1e-3), "conservó la duración declarada"
    assert w.duracion_s == pytest.approx(6.5, abs=1e-3) and m4a.grilla != w.grilla
    for x, z in ((m4a, w), (w, m4a)):
        agrupa, s = mismo_audio(x, z)
        assert agrupa and s > 0.99, f"no agrupó (similitud {s:.3f})"


def test_con_la_misma_grilla_no_se_recalcula_nada(tmp_path, monkeypatch):
    """Lo normal (duraciones a milisegundos): el resultado es el de siempre, sin leer
    ningún archivo de más."""
    import calidad.duplicados as d

    y = _senal_densa(24, dur=95.0)
    a = cargar_ficha(_wav(tmp_path, y, "a.wav"))
    b = cargar_ficha(_wav(tmp_path, _con_cola_de_silencio(y, 0.05), "b.wav"))
    assert a.grilla == b.grilla
    monkeypatch.setattr(d, "huella_en_grilla", lambda *a_, **k: pytest.fail("recalculó"))
    agrupa, s = mismo_audio(a, b)
    assert agrupa and s == pytest.approx(similitud(a.huella, b.huella))


def test_agrupar_recalcula_una_vez_por_archivo_y_grilla(tmp_path, monkeypatch):
    """Original de 346 s y dos recortes de cola con la MISMA grilla entre sí: el original se
    recalcula una sola vez (la segunda comparación sale de la caché) y los tres agrupan."""
    import calidad.duplicados as d

    y = _senal_densa(25, dur=346.0)
    fichas = [cargar_ficha(_wav(tmp_path, y, "o.wav")),
              cargar_ficha(_wav(tmp_path, _sin_cola(y, 5.0), "c5.wav")),
              cargar_ficha(_wav(tmp_path, _sin_cola(y, 5.5), "c55.wav"))]
    assert fichas[1].grilla == fichas[2].grilla != fichas[0].grilla
    llamadas = []
    real = d.huella_en_grilla
    monkeypatch.setattr(d, "huella_en_grilla", lambda r, g: (llamadas.append(r), real(r, g))[1])
    grupos, _ = agrupar(fichas)
    assert grupos == [[0, 1, 2]]
    assert len(llamadas) == 1, f"leyó el original {len(llamadas)} veces"


def test_si_no_se_puede_recalcular_avisa_y_compara_tal_cual(tmp_path, monkeypatch, caplog):
    import calidad.duplicados as d

    y = _senal_densa(26, dur=95.0)
    a = cargar_ficha(_wav(tmp_path, y, "a.wav"))
    b = cargar_ficha(_wav(tmp_path, _con_cola_de_silencio(y, 1.0), "b.wav"))
    def falla(*a_, **k):
        raise OSError("x")

    monkeypatch.setattr(d, "huella_en_grilla", falla)
    with caplog.at_level("WARNING", logger="calidad.duplicados"):
        agrupa, s = mismo_audio(a, b)
    assert s == pytest.approx(similitud(a.huella, b.huella))
    assert any("mismos tramos" in r.getMessage() for r in caplog.records)


# --- Límite de la cobertura: lo que la huella NO ve (documentado en el docstring) -----------


def test_una_diferencia_entre_dos_tramos_pasa_sin_ser_vista(tmp_path):
    """FIJA LA LIMITACIÓN REAL (como el test de la 5.25 fijaba la de la ventana contigua):
    la huella mira 2,5 s de cada `paso`, así que 4 s distintos que caen justo en el hueco
    entre dos tramos NO se ven y el par se agrupa. Si este test empieza a fallar es porque
    la huella pasó a ver más (más tramos o tramos más largos): sacar la advertencia del
    docstring de `calidad/duplicados.py`."""
    y = _senal_densa(27, dur=200.0)
    v = ventanas_huella(200.0)
    fin_k = v[14][0] + v[14][1]
    ini_sig = v[15][0]
    desde, hasta = fin_k + 0.25, fin_k + 0.25 + 4.0
    assert hasta < ini_sig, "el hueco entre tramos es menor que el reemplazo del test"
    z = _reemplazar(y, _senal_densa(127, dur=200.0), desde, hasta)
    a = Ficha("a", "a", 200.0, huella(muestra_para_huella(y)), tuple(v))
    b = Ficha("b", "b", 200.0, huella(muestra_para_huella(z)), tuple(v))
    agrupa, s = mismo_audio(a, b)
    assert agrupa and s > 0.999, f"la huella vio los 4 s del hueco (similitud {s:.4f})"


def test_la_misma_diferencia_sobre_un_tramo_si_se_nota(tmp_path):
    """Control del anterior: los mismos 4 s cayendo SOBRE un tramo bajan la similitud, o sea
    que el test del hueco prueba el hueco y no una señal que no cambia."""
    y = _senal_densa(27, dur=200.0)
    v = ventanas_huella(200.0)
    desde, hasta = v[14][0] - 0.75, v[14][0] - 0.75 + 4.0       # cubre el tramo 14 entero
    z = _reemplazar(y, _senal_densa(127, dur=200.0), desde, hasta)
    a = Ficha("a", "a", 200.0, huella(muestra_para_huella(y)), tuple(v))
    b = Ficha("b", "b", 200.0, huella(muestra_para_huella(z)), tuple(v))
    assert mismo_audio(a, b)[1] < 0.99


# --- Las dos compuertas juntas -------------------------------------------------------------


def _ficha(nombre, dur, h):
    return Ficha(ruta=f"D:/{nombre}", archivo=nombre, duracion_s=dur, huella=h)


def test_duracion_distinta_corta_antes_de_mirar_la_huella():
    """Aunque la huella sea idéntica, si la duración no da es otra versión."""
    h = huella(_senal(4))
    a, b = _ficha("a.wav", 100.0, h), _ficha("b.wav", 140.0, h)
    es_dup, s = mismo_audio(a, b)
    assert not es_dup and s == 0.0


def test_misma_duracion_y_misma_huella_es_duplicado():
    h = huella(_senal(5))
    a, b = _ficha("a.wav", 100.0, h), _ficha("b.mp3", 100.1, h)
    es_dup, s = mismo_audio(a, b)
    assert es_dup and s == pytest.approx(1.0)


# --- Agrupamiento ---------------------------------------------------------------------------


def test_agrupa_solo_los_que_son_iguales():
    h1, h2 = huella(_senal(6)), huella(_senal(7))
    fichas = [_ficha("a.wav", 100.0, h1), _ficha("b.mp3", 100.0, h1),
              _ficha("c.wav", 100.0, h2)]
    grupos, _ = agrupar(fichas)
    assert len(grupos) == 1
    assert set(grupos[0]) == {0, 1}


def test_sin_duplicados_no_hay_grupos():
    fichas = [_ficha("a.wav", 100.0, huella(_senal(8))),
              _ficha("b.wav", 100.0, huella(_senal(9)))]
    assert agrupar(fichas)[0] == []


def test_los_que_no_tienen_huella_no_agrupan():
    fichas = [_ficha("a.wav", 100.0, None), _ficha("b.wav", 100.0, None)]
    assert agrupar(fichas)[0] == []


# --- Cuál conservar --------------------------------------------------------------------------


def _cal(corte, muro, lossless, bitrate):
    return FilaCalidad(
        archivo="x", ruta="x", formato="x", lossless=lossless,
        bitrate_declarado_kbps=bitrate, sample_rate_hz=44100,
        corte_medido_khz=corte, criterio="lossless" if lossless == "si" else "lossy",
        corte_esperado_khz=None if lossless == "si" else 20.0,
        margen_khz=None if lossless == "si" else corte - 20.0,
        muro_db=muro, es_muro="si" if muro >= 25 else "no",
        bandera="ok", motivo="", marca_manual="")


def test_un_wav_de_15khz_con_muro_pierde_contra_un_320_real():
    """La regla explícita: decide la calidad MEDIDA, nunca el bitrate declarado."""
    fichas = [_ficha("trucho.wav", 200.0, None), _ficha("bueno.mp3", 200.0, None)]
    calidades = {0: _cal(15.0, 60.0, "si", 1411), 1: _cal(20.0, 30.0, "no", 320)}
    filas = decidir([0, 1], fichas, calidades)
    por = {f.archivo: f for f in filas}
    assert por["bueno.mp3"].accion == CONSERVAR
    assert por["trucho.wav"].accion == DESCARTAR
    assert "más abajo" in por["trucho.wav"].motivo


def test_a_igual_corte_gana_el_sin_muro():
    fichas = [_ficha("a.mp3", 200.0, None), _ficha("b.wav", 200.0, None)]
    calidades = {0: _cal(20.0, 60.0, "no", 320), 1: _cal(20.0, 2.0, "si", 1411)}
    filas = decidir([0, 1], fichas, calidades)
    assert {f.archivo: f.accion for f in filas}["b.wav"] == CONSERVAR


def test_a_igual_medida_gana_el_lossless():
    fichas = [_ficha("a.mp3", 200.0, None), _ficha("b.wav", 200.0, None)]
    calidades = {0: _cal(22.0, 2.0, "no", 320), 1: _cal(22.0, 2.0, "si", 1411)}
    filas = decidir([0, 1], fichas, calidades)
    por = {f.archivo: f for f in filas}
    assert por["b.wav"].accion == CONSERVAR
    assert "sin pérdida" in por["a.mp3"].motivo


def test_el_bitrate_declarado_no_decide():
    """Mismo audio medido igual, pero uno declara 320 y el otro 128: no cambia nada."""
    p_alto = _puntaje_calidad(_cal(20.0, 30.0, "no", 320))
    p_bajo = _puntaje_calidad(_cal(20.0, 30.0, "no", 128))
    assert p_alto == p_bajo


def test_siempre_se_conserva_exactamente_uno():
    fichas = [_ficha(f"f{i}.wav", 200.0, None) for i in range(4)]
    calidades = {i: _cal(20.0 - i, 10.0, "si", 1411) for i in range(4)}
    filas = decidir([0, 1, 2, 3], fichas, calidades)
    assert sum(1 for f in filas if f.accion == CONSERVAR) == 1
    assert sum(1 for f in filas if f.accion == DESCARTAR) == 3


# --- Desempate: preferir la copia ya referenciada (tarea 5.26) ---------------------------------


def _par_equivalente():
    """Dos copias que miden IDÉNTICO: sin referencias, el orden del grupo decide."""
    fichas = [_ficha("primera.wav", 200.0, None), _ficha("segunda.wav", 200.0, None)]
    calidades = {0: _cal(20.0, 2.0, "si", 1411), 1: _cal(20.0, 2.0, "si", 1411)}
    return fichas, calidades


def _ref(ficha, origen):
    return {normalizar_ruta(ficha.ruta): origen}


def test_sin_referencias_el_empate_lo_resuelve_el_orden_del_grupo():
    """La línea base: sin la fuente nueva, nada cambia (y el motivo sigue sin inventar razón)."""
    fichas, calidades = _par_equivalente()
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades)}
    assert por["primera.wav"].accion == CONSERVAR
    assert por["segunda.wav"].motivo == "copia equivalente del mismo audio"


def test_entre_copias_equivalentes_gana_la_referenciada_por_rekordbox():
    fichas, calidades = _par_equivalente()
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades,
                                         _ref(fichas[1], "Rekordbox"))}
    assert por["segunda.wav"].accion == CONSERVAR, "ganó la que NO está referenciada"
    assert por["primera.wav"].accion == DESCARTAR
    assert "ya está referenciada por Rekordbox" in por["segunda.wav"].motivo
    assert por["primera.wav"].motivo == (
        "copia equivalente del mismo audio; se conserva la que ya está referenciada "
        "por Rekordbox y esta no")


def test_el_desempate_vale_tambien_para_itunes():
    fichas, calidades = _par_equivalente()
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades, _ref(fichas[1], "iTunes"))}
    assert por["segunda.wav"].accion == CONSERVAR
    assert "referenciada por iTunes" in por["segunda.wav"].motivo


def test_en_un_grupo_de_tres_gana_la_unica_referenciada():
    fichas = [_ficha(f"f{i}.wav", 200.0, None) for i in range(3)]
    calidades = {i: _cal(20.0, 2.0, "si", 1411) for i in range(3)}
    filas = decidir([0, 1, 2], fichas, calidades, _ref(fichas[2], "Rekordbox"))
    assert [f.archivo for f in filas if f.accion == CONSERVAR] == ["f2.wav"]


def test_la_referencia_nunca_compensa_una_peor_calidad():
    """Desempate, no preferencia: la copia referenciada que corta en 15 kHz pierde igual.

    Y como se la propone descartar, el motivo avisa que rompe lo que Rekordbox guarda de ella.
    """
    fichas = [_ficha("referenciada.wav", 200.0, None), _ficha("mejor.wav", 200.0, None)]
    calidades = {0: _cal(15.0, 60.0, "si", 1411), 1: _cal(20.0, 2.0, "si", 1411)}
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades,
                                         _ref(fichas[0], "Rekordbox"))}
    assert por["mejor.wav"].accion == CONSERVAR
    assert por["referenciada.wav"].accion == DESCARTAR
    assert "más abajo" in por["referenciada.wav"].motivo
    assert "OJO: esta copia está referenciada por Rekordbox" in por["referenciada.wav"].motivo
    assert "referenciada" not in por["mejor.wav"].motivo, "no hubo desempate por referencia"


def test_si_las_dos_estan_referenciadas_el_criterio_no_desempata_y_avisa():
    """El caso sin criterio de producto: queda el orden del grupo, pero el motivo lo grita."""
    fichas, calidades = _par_equivalente()
    refs = {**_ref(fichas[0], "Rekordbox"), **_ref(fichas[1], "iTunes")}
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades, refs)}
    assert por["primera.wav"].accion == CONSERVAR
    assert "ya está referenciada" not in por["primera.wav"].motivo, \
        "dijo que desempató por la referencia cuando las dos la tienen"
    assert "OJO: esta copia está referenciada por iTunes" in por["segunda.wav"].motivo


def test_la_ruta_se_compara_normalizada(tmp_path):
    """El XML trae 'file:///C:/x/..' y el pipeline una ruta del SO: tienen que ser la misma."""
    ruta = tmp_path / "carpeta con espacios" / "tema.wav"
    assert normalizar_ruta(str(ruta)) == normalizar_ruta(ruta.as_posix())
    assert normalizar_ruta(str(ruta)) != normalizar_ruta(str(ruta.with_name("otro.wav")))


def _location(ruta) -> str:
    """El Location como lo escriben Rekordbox e iTunes: 'file://localhost/C:/Mi%20carpeta/x.wav'
    (en macOS/Linux 'file://localhost/Users/..'), con los espacios y acentos codificados."""
    p = Path(ruta).as_posix()
    return "file://localhost" + ("" if p.startswith("/") else "/") + quote(p, safe="/:")


@pytest.mark.skipif(os.name != "nt", reason="normcase solo cambia mayúsculas/separadores en Windows")
def test_la_ruta_ignora_mayusculas_y_separadores_en_windows():
    """'C:/Users/X/a.wav' (XML) y 'c:\\users\\x\\A.WAV' (el SO) son el mismo archivo."""
    assert normalizar_ruta("C:/Users/X/a.wav") == normalizar_ruta("c:\\users\\x\\A.WAV")


def test_una_carpeta_relativa_coincide_con_la_ruta_absoluta_del_xml(tmp_path, monkeypatch):
    """`--carpeta descargas` da fichas relativas y el XML trae rutas absolutas: sin absolutizar
    nunca coinciden y el desempate queda apagado sin avisar."""
    (tmp_path / "descargas").mkdir()
    monkeypatch.chdir(tmp_path)
    fichas = [Ficha(os.path.join("descargas", n), n, 200.0, None) for n in ("a.wav", "b.wav")]
    _, calidades = _par_equivalente()
    xml = _xml_rekordbox(tmp_path / "rb.xml", [tmp_path / "descargas" / "b.wav"])
    refs = referencias_de_xml(rekordbox_xml=xml)
    por = {f.archivo: f.accion for f in decidir([0, 1], fichas, calidades, refs)}
    assert por == {"a.wav": DESCARTAR, "b.wav": CONSERVAR}, "el desempate no se aplicó"


def test_la_raiz_posix_del_xml_se_repone(tmp_path):
    """`_ruta_local` hace lstrip('/') y '/home/dj/b.wav' llegaría como 'home/dj/b.wav'."""
    xml = tmp_path / "rb.xml"
    xml.write_text('<DJ_PLAYLISTS><COLLECTION Entries="1"><TRACK TrackID="1" '
                   'Location="file://localhost/home/dj/b.wav"/></COLLECTION></DJ_PLAYLISTS>',
                   encoding="utf-8")
    fichas = [Ficha("/home/dj/a.wav", "a.wav", 200.0, None),
              Ficha("/home/dj/b.wav", "b.wav", 200.0, None)]
    _, calidades = _par_equivalente()
    por = {f.archivo: f.accion for f in
           decidir([0, 1], fichas, calidades, referencias_de_xml(rekordbox_xml=xml))}
    assert por == {"a.wav": DESCARTAR, "b.wav": CONSERVAR}


def test_el_borde_del_redondeo_no_es_empate_y_el_motivo_no_dice_equivalente():
    """20.04 y 20.06 kHz caen a lados distintos del redondeo a 0.1: NO empatan. Gana el de
    mejor puntaje aunque el otro esté referenciado, y el motivo no puede decir 'equivalente'
    ni insinuar que la referencia decidió."""
    fichas = [_ficha("referenciada.wav", 200.0, None), _ficha("mejor.wav", 200.0, None)]
    calidades = {0: _cal(20.04, 2.0, "si", 1411), 1: _cal(20.06, 2.0, "si", 1411)}
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades, _ref(fichas[0], "Rekordbox"))}
    assert por["mejor.wav"].accion == CONSERVAR
    assert por["referenciada.wav"].accion == DESCARTAR
    perdedora = por["referenciada.wav"].motivo
    assert "equivalente" not in perdedora, perdedora
    assert "no es un empate" in perdedora and "OJO" in perdedora
    assert "referenciada" not in por["mejor.wav"].motivo


def test_las_filas_conservan_el_orden_de_siempre_y_la_referencia_solo_elige_ganador():
    """Un perdedor referenciado no sube de lugar: el orden de los perdedores es el de sin
    referencias (el de main). Ganador 0 (referenciado, 20 kHz); 1 y 2 miden 18 y el 2 está
    referenciado: sin referencias [0, 1, 2], y con referencias tiene que seguir igual."""
    fichas = [_ficha(f"f{i}.wav", 200.0, None) for i in range(3)]
    calidades = {0: _cal(20.0, 2.0, "si", 1411), 1: _cal(18.0, 2.0, "si", 1411),
                 2: _cal(18.0, 2.0, "si", 1411)}
    sin = [f.archivo for f in decidir([0, 1, 2], fichas, calidades)]
    refs = {**_ref(fichas[0], "Rekordbox"), **_ref(fichas[2], "iTunes")}
    con = decidir([0, 1, 2], fichas, calidades, refs)
    assert sin == ["f0.wav", "f1.wav", "f2.wav"]
    assert [f.archivo for f in con] == sin, "la referencia reordenó a los perdedores"
    assert [f.accion for f in con] == [CONSERVAR, DESCARTAR, DESCARTAR]


def test_xml_con_estructura_ajena_falla_con_value_error_y_exit_2(tmp_path):
    """Un plist válido pero que no es un Library.xml no puede dar AttributeError."""
    for i, contenido in enumerate(({"Tracks": [1, 2]}, {"Tracks": {"1": 5}},
                                   {"Tracks": {"1": {"Location": 7}}})):
        lib = tmp_path / f"ajeno{i}.xml"
        lib.write_bytes(plistlib.dumps(contenido))
        with pytest.raises(ValueError, match="Library.xml"):
            referencias_itunes(lib)
        assert main(["--audio", str(tmp_path), "--itunes-xml", str(lib)]) == 2


def _xml_rekordbox(destino, rutas):
    pistas = "".join(f'<TRACK TrackID="{i}" Name="t" Location="{_location(r)}"/>'
                     for i, r in enumerate(rutas, 1))
    destino.write_text(f'<DJ_PLAYLISTS><COLLECTION Entries="{len(rutas)}">{pistas}'
                       "</COLLECTION></DJ_PLAYLISTS>", encoding="utf-8")
    return destino


def test_el_xml_de_rekordbox_desempata_con_rutas_reales(tmp_path):
    a, b = tmp_path / "descarga uno" / "a.wav", tmp_path / "biblioteca" / "b.wav"
    fichas = [Ficha(str(a), "a.wav", 200.0, None), Ficha(str(b), "b.wav", 200.0, None)]
    _, calidades = _par_equivalente()
    refs = referencias_de_xml(rekordbox_xml=_xml_rekordbox(tmp_path / "rb.xml", [b]))
    por = {f.archivo: f.accion for f in decidir([0, 1], fichas, calidades, refs)}
    assert por == {"a.wav": DESCARTAR, "b.wav": CONSERVAR}


def test_la_biblioteca_de_itunes_desempata_con_rutas_reales(tmp_path):
    a, b = tmp_path / "descarga" / "a.wav", tmp_path / "iTunes Media" / "b.wav"
    lib = tmp_path / "Library.xml"
    lib.write_bytes(plistlib.dumps({"Tracks": {
        "101": {"Track ID": 101, "Location": _location(b)},
        "102": {"Track ID": 102}}}))                      # una pista sin Location no rompe
    fichas = [Ficha(str(a), "a.wav", 200.0, None), Ficha(str(b), "b.wav", 200.0, None)]
    _, calidades = _par_equivalente()
    refs = referencias_de_xml(itunes_xml=lib)
    assert set(refs.values()) == {"iTunes"}
    por = {f.archivo: f.accion for f in decidir([0, 1], fichas, calidades, refs)}
    assert por == {"a.wav": DESCARTAR, "b.wav": CONSERVAR}


def test_una_ruta_en_las_dos_bibliotecas_dice_las_dos():
    a, b = {"c:/x.wav": "Rekordbox"}, {"c:/x.wav": "iTunes", "c:/y.wav": "iTunes"}
    assert unir_referencias(a, b) == {"c:/x.wav": "Rekordbox e iTunes", "c:/y.wav": "iTunes"}
    assert unir_referencias(a, a) == a


def test_un_xml_que_no_es_de_la_biblioteca_falla_en_vez_de_ignorarse(tmp_path):
    """Ignorarlo propondría descartar justo la copia con el trabajo encima."""
    otro = tmp_path / "otro.xml"
    otro.write_text("<algo/>", encoding="utf-8")
    with pytest.raises(ValueError):
        referencias_rekordbox(otro)
    with pytest.raises(ValueError):
        referencias_itunes(otro)
    sin_pistas = tmp_path / "plist_sin_pistas.xml"
    sin_pistas.write_bytes(plistlib.dumps({"Major Version": 1}))   # plist válido, pero sin 'Tracks'
    with pytest.raises(ValueError, match="Tracks"):
        referencias_itunes(sin_pistas)
    assert main(["--audio", str(tmp_path), "--rekordbox-xml", str(otro)]) == 2
    assert main(["--audio", str(tmp_path), "--itunes-xml", str(otro)]) == 2
