"""Tests de la forma de onda del editor de cues (`motor/peaks.py`, f48).

El audio es SINTÉTICO y se sabe dónde hay qué: 10 s de silencio digital, 10 s de un
four-on-the-floor de `motor/sintetico.click_track` (solo en el canal derecho) y 10 s de
silencio. El valor esperado de cada bin sale de un cálculo independiente sobre el archivo leído
entero con `soundfile.read` (el máximo de |muestra| por tramo, con numpy a secas), no del código
bajo prueba, que lee por bloques.
"""
import os
import sys
import tracemalloc
from pathlib import Path

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import soundfile as sf  # noqa: E402

import motor.peaks as peaks_mod  # noqa: E402
from motor.peaks import UnreadableAudio, cached_peaks, compute_peaks  # noqa: E402
from motor.sintetico import click_track  # noqa: E402

SR = 22050
BINS = 300                       # 30 s / 300 = 0.1 s por bin, 2205 frames: divide exacto


def _silencio_kick_silencio(ruta: Path, sr: int = SR) -> None:
    kick, _ = click_track(128.0, dur=10.0, sr=sr, seed=1)
    silencio = np.zeros(10 * sr, dtype=np.float32)
    derecho = np.concatenate([silencio, kick, silencio])
    izquierdo = np.zeros_like(derecho)
    sf.write(str(ruta), np.stack([izquierdo, derecho], axis=1), sr, subtype="PCM_16")


def _oraculo(ruta: Path, bins: int) -> np.ndarray:
    """El máximo de |muestra| de cada tramo, leyendo el archivo ENTERO (la forma ingenua)."""
    y, _ = sf.read(str(ruta), dtype="float32", always_2d=True)
    amp = np.abs(y).max(axis=1)
    assert amp.size % bins == 0, "el oráculo asume que los bins dividen exacto"
    return amp.reshape(bins, -1).max(axis=1)


@pytest.fixture
def audio(tmp_path):
    ruta = tmp_path / "silencio_kick_silencio.wav"
    _silencio_kick_silencio(ruta)
    return ruta


def test_los_picos_son_los_del_audio(audio):
    p = compute_peaks(audio, bins=BINS)
    esperado = _oraculo(audio, BINS)
    np.testing.assert_array_equal(p.peaks, esperado)
    assert (p.duration_s, p.sample_rate, p.channels) == (30.0, SR, 2)
    # Dónde hay qué: silencio digital = 0 exacto; el tramo del kick tiene energía, y su
    # pico es el del kick del generador (seno de 55 Hz con envolvente, ~1.0 de amplitud).
    assert p.peaks[:100].max() == 0.0 and p.peaks[200:].max() == 0.0
    assert p.peaks[100:200].min() > 0.0
    kick, _ = click_track(128.0, dur=10.0, sr=SR, seed=1)
    assert abs(float(p.peaks.max()) - float(np.abs(kick).max())) < 1e-3


def test_el_canal_que_suena_cuenta_aunque_el_otro_este_callado(audio):
    """Un pico por bin sobre TODOS los canales: el kick está solo en el derecho."""
    p = compute_peaks(audio, bins=BINS)
    assert p.peaks[100:200].min() > 0.0, "se promediaron o se ignoró un canal"


@pytest.mark.parametrize("bloque", [1000, 4096, 65536, 10 ** 7])
def test_el_tamano_de_bloque_no_cambia_el_resultado(audio, bloque):
    """Bloques que no dividen los bins, más chicos que un bin y más grandes que el archivo:
    el resultado es el mismo."""
    np.testing.assert_array_equal(compute_peaks(audio, bins=BINS, block_frames=bloque).peaks,
                                  _oraculo(audio, BINS))


def test_mp3_que_declara_de_menos_cae_en_el_ultimo_bin():
    """Un frame más allá del total declarado no se pierde ni rompe: va al último bin."""
    out = np.zeros(4, dtype=np.float32)
    peaks_mod._acumular(out, np.array([0.1, 0.2, 0.9], dtype=np.float32), pos=7, total=8)
    np.testing.assert_array_equal(out, np.array([0, 0, 0, 0.9], dtype=np.float32))


def test_no_toca_el_original(audio):
    antes = (audio.read_bytes(), audio.stat().st_mtime_ns)
    compute_peaks(audio, bins=BINS)
    assert (audio.read_bytes(), audio.stat().st_mtime_ns) == antes


def test_memoria_por_bloques(tmp_path):
    """60 s estéreo a 44.1 kHz: cargado entero en float64 son ~42 MB (21 en float32). Leído por
    bloques, lo que asigna Python/numpy no pasa de unos pocos MB."""
    sr = 44100
    t = np.arange(60 * sr, dtype=np.float32) / sr
    y = (0.5 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    ruta = tmp_path / "largo.wav"
    sf.write(str(ruta), np.stack([y, y], axis=1), sr, subtype="PCM_16")
    del t, y
    entero_f64 = 60 * sr * 2 * 8
    tracemalloc.start()
    try:
        p = compute_peaks(ruta)
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert p.duration_s == 60.0 and abs(float(p.peaks.max()) - 0.5) < 1e-3
    assert pico < 4 * 2 ** 20, f"pico de {pico / 2 ** 20:.1f} MB (entero en float64: " \
                               f"{entero_f64 / 2 ** 20:.0f} MB): no está leyendo por bloques"


# --- caché ------------------------------------------------------------------------------

def test_la_cache_no_recalcula(audio, tmp_path, monkeypatch):
    cache = tmp_path / "peaks"
    primero, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is False
    np.testing.assert_allclose(primero.peaks, _oraculo(audio, BINS), atol=1e-5)

    def no_recalcular(*a, **k):
        raise AssertionError("recalculó con la caché vigente")

    monkeypatch.setattr(peaks_mod, "compute_peaks", no_recalcular)
    segundo, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is True
    np.testing.assert_array_equal(segundo.peaks, primero.peaks)
    assert (segundo.duration_s, segundo.sample_rate, segundo.channels) == (30.0, SR, 2)


def test_si_el_archivo_cambia_se_recalcula(audio, tmp_path):
    cache = tmp_path / "peaks"
    viejo, _ = cached_peaks(audio, cache, bins=BINS)
    # Otro audio en la misma ruta: todo kick (sin silencio al principio).
    kick, _ = click_track(128.0, dur=30.0, sr=SR, seed=2)
    sf.write(str(audio), np.stack([kick, kick], axis=1), SR, subtype="PCM_16")
    nuevo, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is False
    assert nuevo.peaks[:100].max() > 0 and viejo.peaks[:100].max() == 0, \
        "devolvió la onda del archivo viejo"


def test_una_cache_rota_se_rehace(audio, tmp_path):
    cache = tmp_path / "peaks"
    cached_peaks(audio, cache, bins=BINS)
    (archivo,) = list(cache.glob("*.json"))
    archivo.write_text("{ esto no es json", encoding="utf-8")
    p, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is False
    np.testing.assert_allclose(p.peaks, _oraculo(audio, BINS), atol=1e-5)


# --- errores ----------------------------------------------------------------------------

def test_archivo_que_no_existe(tmp_path):
    with pytest.raises(FileNotFoundError):
        compute_peaks(tmp_path / "no.wav")
    with pytest.raises(FileNotFoundError):
        cached_peaks(tmp_path / "no.wav", tmp_path / "peaks")


@pytest.mark.parametrize("como", ["truncado", "basura en el medio"])
def test_flac_que_falla_a_mitad_del_stream(audio, tmp_path, como):
    """libsndfile ABRE bien estos FLAC y falla recién al decodificar el medio: tiene que ser
    UnreadableAudio (la API lo contesta 422), no un RuntimeError suelto."""
    y, sr = sf.read(str(audio), dtype="float32", always_2d=True)
    flac = tmp_path / "danado.flac"
    sf.write(str(flac), y, sr, format="FLAC")
    b = flac.read_bytes()
    mitad = len(b) // 2
    flac.write_bytes(b[:mitad] if como == "truncado"
                     else b[:mitad] + b"\x00" * 5000 + b[mitad + 5000:])
    with pytest.raises(UnreadableAudio, match="se corta o está dañado"):
        compute_peaks(flac, bins=BINS)


def test_wav_truncado_no_dibuja_silencio_inventado(audio, tmp_path):
    """Un WAV cortado a 3/4: los bins se reparten sobre lo que DE VERDAD hay (libsndfile
    corrige solo el largo de un WAV truncado; el decodificador que declara de más está en
    `test_audioread_que_estima_de_mas_se_relee`)."""
    corto = tmp_path / "truncado.wav"
    b = audio.read_bytes()
    corto.write_bytes(b[: 44 + (len(b) - 44) * 3 // 4])        # 3/4 del audio, header intacto
    y, sr = sf.read(str(corto), dtype="float32", always_2d=True)
    p = compute_peaks(corto, bins=100)
    assert p.duration_s == len(y) / sr and p.duration_s < 30.0, p.duration_s
    amp = np.abs(y).max(axis=1)
    bordes = [-((-k * len(amp)) // 100) for k in range(101)]
    esperado = [amp[a:b].max() for a, b in zip(bordes[:-1], bordes[1:], strict=True)]
    np.testing.assert_array_equal(p.peaks, np.asarray(esperado, dtype=np.float32))
    # 100 bins de 0.225 s: silencio hasta los 10 s (bin 44), kick hasta los 20 s (bin 88) y
    # el silencio REAL del final (20 a 22.5 s), no un relleno de lo que falta.
    assert p.peaks[:44].max() == 0.0 and p.peaks[45:88].min() > 0 and p.peaks[90:].max() == 0.0


def test_audioread_que_estima_de_mas_se_relee(audio, monkeypatch):
    """Por el camino de audioread (MP3/M4A vía ffmpeg) la duración es una ESTIMACIÓN. Un
    decodificador falso que dice 60 s y entrega los 30 del archivo: los bins se reparten sobre
    lo entregado (si no, la mitad de la onda sería un silencio que no existe). En esta PC
    audioread no tiene ffmpeg, por eso el decodificador es falso; los datos son los del WAV."""
    import audioread

    y, sr = sf.read(str(audio), dtype="int16", always_2d=True)

    class Falso:
        samplerate, channels, duration = sr, y.shape[1], 2 * len(y) / sr

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            datos = y.astype("<i2").tobytes()
            for i in range(0, len(datos), 4096 + 2):     # pedazos que no caen en un frame
                yield datos[i:i + 4096 + 2]

    monkeypatch.setattr(peaks_mod, "_con_soundfile", lambda *a, **k: None)
    monkeypatch.setattr(audioread, "audio_open", lambda _ruta: Falso())
    p = compute_peaks(audio, bins=BINS)
    assert p.duration_s == 30.0, p.duration_s
    # int16 → /32768: el mismo valor que lee soundfile en float32 del PCM_16.
    np.testing.assert_allclose(p.peaks, _oraculo(audio, BINS), atol=1e-6)


def test_muestras_nan_son_audio_ilegible(tmp_path):
    y = np.full((22050, 1), 0.5, dtype=np.float32)
    y[100] = np.nan
    ruta = tmp_path / "nan.wav"
    sf.write(str(ruta), y, 22050, subtype="FLOAT")
    with pytest.raises(UnreadableAudio, match="NaN"):
        compute_peaks(ruta)


@pytest.mark.parametrize("veneno", ["null", "[]", '{"peaks": [[1]]}', "PICOS_NULL", "PICOS_ANIDADOS",
                                    "PICOS_NEGATIVOS", "DUR_TEXTO", "SR_FLOAT"])
def test_una_cache_con_otra_forma_se_rehace(audio, tmp_path, veneno):
    import json

    cache = tmp_path / "peaks"
    bueno, _ = cached_peaks(audio, cache, bins=BINS)
    (archivo,) = list(cache.glob("*.json"))
    j = json.loads(archivo.read_text(encoding="utf-8"))
    cambios = {"PICOS_NULL": ("peaks", [None] * BINS), "PICOS_ANIDADOS": ("peaks", [[1]] * BINS),
               "PICOS_NEGATIVOS": ("peaks", [-1.0] * BINS), "DUR_TEXTO": ("duration_s", "abc"),
               "SR_FLOAT": ("sample_rate", 22050.5)}
    if veneno in cambios:
        j[cambios[veneno][0]] = cambios[veneno][1]
        archivo.write_text(json.dumps(j), encoding="utf-8")
    else:
        archivo.write_text(veneno, encoding="utf-8")
    p, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is False
    np.testing.assert_array_equal(p.peaks, bueno.peaks)
    assert (p.duration_s, p.sample_rate, p.channels) == (30.0, SR, 2)


def test_la_cache_tiene_tope_y_poda_lo_menos_usado(tmp_path):
    """Con tope 3: al escribir el quinto quedan como mucho 3 archivos, y el que se acaba de
    LEER de la caché sobrevive aunque sea de los primeros que se escribieron."""
    cache = tmp_path / "peaks"
    rutas = []
    for i in range(5):
        r = tmp_path / f"t{i}.wav"
        sf.write(str(r), np.full((2205, 1), 0.1 * (i + 1), dtype=np.float32), 22050)
        rutas.append(r)
    for r in rutas[:3]:
        cached_peaks(r, cache, bins=10, tope=3)
    for a in cache.glob("*.json"):                          # todos viejos...
        os.utime(a, (1, 1))
    _, de_cache = cached_peaks(rutas[0], cache, bins=10, tope=3)   # ...y el 0 se usa ahora
    assert de_cache is True
    for r in rutas[3:]:
        cached_peaks(r, cache, bins=10, tope=3)
    assert len(list(cache.glob("*.json"))) <= 3
    assert cached_peaks(rutas[0], cache, bins=10, tope=3)[1] is True, "podó el recién usado"


def test_archivo_que_no_es_audio(tmp_path):
    ruta = tmp_path / "roto.wav"
    ruta.write_bytes(b"RIFF\x00\x00\x00\x00WAVEesto no es audio" * 10)
    with pytest.raises(UnreadableAudio):
        compute_peaks(ruta)
