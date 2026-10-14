"""Tests de `cargar_tramos` y `cargar_tramos_en_streaming` (`motor/decodificar.py`, tarea 5.27).

Leer varios tramos de un archivo sin abrirlo una vez por tramo (`cargar_tramos`) y sin
acumular todo el PCM cuando hay que decodificar entero (`cargar_tramos_en_streaming`).
Los valores esperados salen de `dec.cargar` por tramo, que es lo que ya medía el motor.
ffmpeg es el FALSO de `test_decodificar.py` (un script que entrega el PCM de un WAV sintético).
"""
import os
import sys
import tracemalloc

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import soundfile as sf  # noqa: E402

import motor.decodificar as dec  # noqa: E402
from motor.sintetico import click_track  # noqa: E402
from motor.tests.test_decodificar import Falso  # noqa: E402


@pytest.fixture
def falso(tmp_path, monkeypatch):
    return Falso(tmp_path, monkeypatch)


def _grilla(duracion_s):
    """Tres tramos de 1 s repartidos: el primero, uno hacia el medio y uno cerca del final."""
    return [(0.5, 1.0), (duracion_s / 2, 1.0), (duracion_s - 1.5, 1.0)]


def _esperado(ruta, ventanas, sr=22050):
    return [dec.cargar(ruta, sr=sr, mono=True, offset=o, duration=d)[0] for o, d in ventanas]


# --- cargar_tramos: una apertura, lo mismo que cargar() por tramo -----------------------------


def test_cargar_tramos_da_bit_a_bit_lo_que_cargar_por_tramo(falso):
    dur, ventanas, tramos = dec.cargar_tramos(falso.wav, 22050, _grilla)
    assert dur == 6.0 and ventanas == _grilla(6.0)
    for t, e in zip(tramos, _esperado(falso.wav, ventanas), strict=True):
        assert t.size > 0
        np.testing.assert_array_equal(t, e)


def test_cargar_tramos_abre_el_archivo_una_vez(falso, monkeypatch):
    aperturas = []
    real = sf.SoundFile

    class Espia(real):
        def __init__(self, *a, **k):
            aperturas.append(a[0])
            super().__init__(*a, **k)

    monkeypatch.setattr(sf, "SoundFile", Espia)
    dec.cargar_tramos(falso.wav, 22050, _grilla)
    assert len(aperturas) == 1


def test_cargar_tramos_con_sr_nativo_y_sin_mono(falso):
    _, ventanas, tramos = dec.cargar_tramos(falso.wav, None, _grilla, mono=False)
    esperado = [dec.cargar(falso.wav, sr=None, mono=False, offset=o, duration=d)[0]
                for o, d in ventanas]
    for t, e in zip(tramos, esperado, strict=True):
        assert t.shape[0] == 2
        np.testing.assert_array_equal(t, e)


def test_cargar_tramos_pasado_del_final_tira_en_vez_de_inventar(falso):
    with pytest.raises(RuntimeError):
        dec.cargar_tramos(falso.wav, 22050, lambda d: [(d + 30.0, 1.0)])


def test_cargar_tramos_de_lo_que_libsndfile_no_abre_tira(falso):
    with pytest.raises(RuntimeError):
        dec.cargar_tramos(falso.m4a, 22050, _grilla)


# --- en streaming: lo mismo, sin acumular el audio ---------------------------------------------


def test_streaming_por_libsndfile_da_lo_mismo_que_cargar_por_tramo(falso):
    dur, ventanas, tramos = dec.cargar_tramos_en_streaming(falso.wav, 22050, _grilla)
    assert dur == 6.0 and ventanas == _grilla(6.0)
    for t, e in zip(tramos, _esperado(falso.wav, ventanas), strict=True):
        np.testing.assert_array_equal(t, e)
    assert falso.llamadas() == [], "un WAV no tiene por qué pasar por ffmpeg"


def test_streaming_por_ffmpeg_da_lo_mismo_que_el_wav(falso):
    """El M4A que libsndfile no reconoce se lee en UNA pasada de ffmpeg (el falso) y da los
    mismos tramos que su equivalente en WAV."""
    dur, ventanas, tramos = dec.cargar_tramos_en_streaming(falso.m4a, 22050, _grilla)
    assert dur == pytest.approx(6.0, abs=1e-6) and ventanas == _grilla(dur)
    for t, e in zip(tramos, _esperado(falso.wav, ventanas), strict=True):
        assert t.size > 0
        np.testing.assert_allclose(t, e, atol=1e-6)
    assert [c[0] for c in falso.llamadas()] == ["ffprobe", "ffmpeg"], "más de una pasada"


def test_si_el_archivo_declara_de_mas_la_grilla_se_rehace_con_la_duracion_real(falso):
    """ffprobe dice 10 s y el audio dura 6: la grilla de 10 s pone un tramo pasado del final.
    Al contar los frames reales se hace una segunda pasada con la grilla de 6 s."""
    falso.entrega(declara=10.0)
    dur, ventanas, tramos = dec.cargar_tramos_en_streaming(falso.m4a, 22050, _grilla)
    assert dur == pytest.approx(6.0, abs=1e-6), "devuelve la duración REAL, no la declarada"
    assert ventanas == _grilla(6.0), "la grilla no es la del audio real"
    for t, e in zip(tramos, _esperado(falso.wav, ventanas), strict=True):
        assert t.size > 0
        np.testing.assert_allclose(t, e, atol=1e-6)
    assert [c[0] for c in falso.llamadas()] == ["ffprobe", "ffmpeg", "ffprobe", "ffmpeg"]


def test_sin_duracion_declarada_la_primera_pasada_solo_cuenta(falso):
    falso.modo("sin_duracion_total")
    dur, ventanas, tramos = dec.cargar_tramos_en_streaming(falso.m4a, 22050, _grilla)
    assert dur == pytest.approx(6.0, abs=1e-6) and ventanas == _grilla(dur)
    for t, e in zip(tramos, _esperado(falso.wav, ventanas), strict=True):
        assert t.size > 0
        np.testing.assert_allclose(t, e, atol=1e-6)
    assert [c[0] for c in falso.llamadas()] == ["ffprobe", "ffmpeg", "ffprobe", "ffmpeg"]


def test_streaming_respeta_el_tope_de_bytes_y_avisa(falso, monkeypatch):
    """Un ffmpeg que entrega mucho más de lo declarado se corta por el tope de `LectorPCM`:
    sale `DecodificacionError` con el motivo, no una huella sobre audio de más."""
    falso.modo("infinito")
    monkeypatch.setattr(dec, "PISO_TOPE_BYTES", 0)
    with pytest.raises(dec.DecodificacionError, match="mucho más audio"):
        dec.cargar_tramos_en_streaming(falso.m4a, 22050, _grilla)


def test_streaming_sin_ffmpeg_dice_que_falta_ffmpeg(falso, tmp_path, monkeypatch):
    monkeypatch.setattr(dec, "_binario", lambda nombre: str(tmp_path / "no-existe" / nombre))
    with pytest.raises(dec.DecodificacionError, match="ffmpeg"):
        dec.cargar_tramos_en_streaming(falso.m4a, 22050, _grilla)


def test_ffmpeg_que_falla_a_mitad_no_entrega_tramos(falso):
    falso.modo("falla")
    with pytest.raises(dec.DecodificacionError, match="no pudo decodificar"):
        dec.cargar_tramos_en_streaming(falso.m4a, 22050, _grilla)


def test_un_frame_partido_entre_bloques_no_corre_el_audio(falso, monkeypatch):
    """El PCM llega en bloques de BLOQUE_BYTES: con un bloque que no es múltiplo del frame
    (estéreo de 16 bit = 4 bytes) los frames partidos se juntan con el siguiente bloque."""
    monkeypatch.setattr(dec, "BLOQUE_BYTES", 4099)
    _, ventanas, tramos = dec.cargar_tramos_en_streaming(falso.m4a, 22050, _grilla)
    for t, e in zip(tramos, _esperado(falso.wav, ventanas), strict=True):
        np.testing.assert_allclose(t, e, atol=1e-6)


# --- memoria: O(tramos), no O(archivo) -------------------------------------------------------


def _pico_mib(f):
    tracemalloc.start()
    try:
        f()
        return tracemalloc.get_traced_memory()[1] / (1 << 20)
    finally:
        tracemalloc.stop()


def test_la_memoria_del_streaming_no_crece_con_la_duracion(falso, tmp_path, monkeypatch):
    """Un audio sintético de 10 min en estéreo (53 MiB de PCM a 22,05 kHz): decodificarlo
    entero con `cargar` junta más de 100 MiB (PCM + float32 + mono), el streaming se queda
    con los 3 s de los tramos más un bloque."""
    mono, _ = click_track(128.0, dur=20.0, sr=22050, seed=2)
    largo = np.tile(np.stack([0.5 * mono, mono], axis=1), (30, 1))           # 600 s
    wav = tmp_path / "largo.wav"
    sf.write(str(wav), largo, 22050, subtype="PCM_16")
    monkeypatch.setenv("FAKE_WAV", str(wav))
    del largo

    def entero():
        dec.cargar(falso.m4a, sr=22050, mono=True)

    def en_streaming():
        dur, _, tramos = dec.cargar_tramos_en_streaming(falso.m4a, 22050, _grilla)
        assert dur == pytest.approx(600.0, abs=1e-3) and all(t.size > 0 for t in tramos)

    pico_entero = _pico_mib(entero)
    pico_streaming = _pico_mib(en_streaming)
    assert pico_entero > 100, f"la calibración falló: decodificar entero pico {pico_entero:.0f} MiB"
    assert pico_streaming < 10, f"el streaming junta {pico_streaming:.1f} MiB (entero: {pico_entero:.0f})"
