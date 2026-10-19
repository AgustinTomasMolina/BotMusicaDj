"""Tests de la forma de onda del editor de cues (`motor/peaks.py`, f48).

El audio es SINTÉTICO y se sabe dónde hay qué: 10 s de silencio digital, 10 s de un
four-on-the-floor de `motor/sintetico.click_track` (solo en el canal derecho) y 10 s de
silencio. El valor esperado de cada bin sale de un cálculo independiente sobre el archivo leído
entero con `soundfile.read` (el máximo de |muestra| por tramo, con numpy a secas), no del código
bajo prueba, que lee por bloques.
"""
import json
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
    `test_ffmpeg_que_estima_de_mas_se_relee`)."""
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


def _ffmpeg_falso(audio: Path, monkeypatch) -> None:
    """Fuerza el camino de ffmpeg con un `LectorPCM` falso que entrega los datos del WAV y DICE
    durar el doble (como un MP3/M4A, cuya duración es una estimación). En esta PC no hay
    ffmpeg, por eso el decodificador es falso; los datos son los del WAV."""
    import motor.decodificar as dec

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
    monkeypatch.setattr(dec, "LectorPCM", lambda _ruta: Falso())


def test_ffmpeg_que_estima_de_mas_se_relee(audio, monkeypatch):
    """Por el camino de ffmpeg (MP3/M4A) la duración es una ESTIMACIÓN. Un decodificador falso
    que dice 60 s y entrega los 30 del archivo: los bins se reparten sobre lo entregado (si no,
    la mitad de la onda sería un silencio que no existe)."""
    _ffmpeg_falso(audio, monkeypatch)
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


@pytest.mark.parametrize("camino", ["soundfile", "ffmpeg"])
def test_un_bug_del_calculo_no_se_disfraza_de_archivo_danado(audio, monkeypatch, camino):
    """H8 de la segunda auditoría: el try de la lectura envolvía también `_acumular`, así que un
    ValueError del cálculo propio salía como UnreadableAudio "el archivo se corta o está
    dañado" (o "no se pudo decodificar"). El archivo es un WAV sano: el error tiene que salir
    como lo que es."""
    if camino == "ffmpeg":
        _ffmpeg_falso(audio, monkeypatch)

    def roto(*a, **k):
        raise ValueError("bug del cálculo")

    monkeypatch.setattr(peaks_mod, "_acumular", roto)
    with pytest.raises(ValueError, match="bug del cálculo"):
        compute_peaks(audio, bins=BINS)


def _dur_enorme(j):
    j["duration_s"] = 10 ** 400          # JSON válido; math.isfinite tira OverflowError
    return json.dumps(j)


_VENENOS = {
    "PICOS_NULL": lambda j: json.dumps({**j, "peaks": [None] * BINS}),
    "PICOS_ANIDADOS": lambda j: json.dumps({**j, "peaks": [[1]] * BINS}),
    "PICOS_NEGATIVOS": lambda j: json.dumps({**j, "peaks": [-1.0] * BINS}),
    "DUR_TEXTO": lambda j: json.dumps({**j, "duration_s": "abc"}),
    "SR_FLOAT": lambda j: json.dumps({**j, "sample_rate": 22050.5}),
    # H9: picos válidos pero uno de menos o de más; antes nada detectaba `len == bins`.
    "PICOS_DE_MENOS": lambda j: json.dumps({**j, "peaks": j["peaks"][:-1]}),
    "PICOS_DE_MAS": lambda j: json.dumps({**j, "peaks": [*j["peaks"], 0.5]}),
    # H8: eran un 422 permanente (la caché envenenada no se reescribía nunca).
    "DUR_400_DIGITOS": _dur_enorme,
    "JSON_5K_NIVELES": lambda j: "[" * 5000,       # entra en el tope: RecursionError al parsear
    "JSON_100K_NIVELES": lambda j: "[" * 100_000,  # no entra en el tope: ni se parsea
}


@pytest.mark.parametrize("veneno", ["null", "[]", '{"peaks": [[1]]}', *_VENENOS])
def test_una_cache_con_otra_forma_se_rehace(audio, tmp_path, veneno):
    cache = tmp_path / "peaks"
    bueno, _ = cached_peaks(audio, cache, bins=BINS)
    (archivo,) = list(cache.glob("*.json"))
    j = json.loads(archivo.read_text(encoding="utf-8"))
    archivo.write_text(_VENENOS[veneno](j) if veneno in _VENENOS else veneno, encoding="utf-8")
    p, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is False
    np.testing.assert_array_equal(p.peaks, bueno.peaks)
    assert (p.duration_s, p.sample_rate, p.channels) == (30.0, SR, 2)
    # Y la caché quedó corregida: la próxima vez sale de ahí, con los mismos valores.
    p, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is True, "la caché envenenada no se reescribió"
    np.testing.assert_array_equal(p.peaks, bueno.peaks)
    assert p.duration_s == 30.0


def test_una_cache_mas_grande_que_la_que_escribimos_no_se_lee(audio, tmp_path):
    """El tope de tamaño: una caché con la firma y los picos correctos pero inflada (acá con
    espacios, JSON válido) más allá de lo que puede escribir `cached_peaks` no se parsea; se
    rehace. Así un archivo enorme en la carpeta de la caché no se carga entero en memoria."""
    cache = tmp_path / "peaks"
    bueno, _ = cached_peaks(audio, cache, bins=BINS)
    (archivo,) = list(cache.glob("*.json"))
    texto = archivo.read_text(encoding="utf-8")
    tope = peaks_mod._tope_bytes(BINS, os.path.normcase(os.path.abspath(audio)))
    assert len(texto) < tope, "el tope no deja pasar ni la caché que escribe el propio módulo"
    archivo.write_text(texto + " " * tope, encoding="utf-8")
    p, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is False, "leyó una caché más grande que el tope"
    np.testing.assert_array_equal(p.peaks, bueno.peaks)
    assert archivo.stat().st_size == len(texto.encode("utf-8")), "no reescribió la caché"


def _replace_que_falla(monkeypatch, veces: int) -> list:
    """`os.replace` que tira PermissionError las primeras `veces` (como en Windows cuando otro
    hilo tiene abierto el JSON). Devuelve la lista de llamadas."""
    real = os.replace
    llamadas = []

    def falso(a, b):
        llamadas.append((a, b))
        if len(llamadas) <= veces:
            raise PermissionError(13, "El proceso no tiene acceso al archivo", str(b))
        return real(a, b)

    monkeypatch.setattr(os, "replace", falso)
    monkeypatch.setattr(peaks_mod, "REPLACE_ESPERA_S", 0.0)
    return llamadas


def test_un_replace_que_falla_una_vez_se_reintenta(audio, tmp_path, monkeypatch):
    """H11: la carrera lector/escritor de Windows. El reintento escribe la caché."""
    cache = tmp_path / "peaks"
    llamadas = _replace_que_falla(monkeypatch, veces=1)
    p, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is False and len(llamadas) == 2
    np.testing.assert_allclose(p.peaks, _oraculo(audio, BINS), atol=1e-5)
    assert list(cache.glob("*.tmp")) == [], "quedó el .tmp"
    p2, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is True, "el reintento no dejó escrita la caché"
    np.testing.assert_array_equal(p2.peaks, p.peaks)


def _aviso_de_cache(caplog, cache: Path):
    """El ÚNICO aviso de "no se pudo guardar la caché" que dejó `motor.peaks`, y que nombra el
    archivo de la caché (dentro de `cache`). No fallar hacia afuera no puede ser callarse."""
    avisos = [r for r in caplog.records
              if r.name == "motor.peaks" and "no se pudo guardar la caché" in r.getMessage()]
    assert len(avisos) == 1, [r.getMessage() for r in caplog.records]
    assert avisos[0].levelname == "WARNING" and str(cache) in avisos[0].getMessage()
    return avisos[0]


def test_si_la_cache_no_se_puede_escribir_igual_devuelve_los_picos(audio, tmp_path, monkeypatch,
                                                                   caplog):
    """H11: un fallo al ESCRIBIR la caché no tira el pedido ni los picos ya calculados (antes
    el PermissionError salía como 409 "no pude leer el archivo"). Pero queda en el log."""
    import logging

    cache = tmp_path / "peaks"
    llamadas = _replace_que_falla(monkeypatch, veces=10 ** 6)
    with caplog.at_level(logging.WARNING, logger="motor.peaks"):
        p, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is False
    np.testing.assert_allclose(p.peaks, _oraculo(audio, BINS), atol=1e-5)
    assert (p.duration_s, p.sample_rate, p.channels) == (30.0, SR, 2)
    assert len(llamadas) == peaks_mod.REPLACE_INTENTOS
    assert sorted(x.name for x in cache.iterdir()) == [], "quedó el .tmp o una caché a medias"
    assert "El proceso no tiene acceso al archivo" in _aviso_de_cache(caplog, cache).getMessage()


def test_carpeta_de_cache_imposible_igual_devuelve_los_picos(audio, tmp_path, caplog):
    """Si ni se puede crear la carpeta de la caché (acá es un archivo), los picos salen igual,
    y el log lo dice."""
    import logging

    cache = tmp_path / "peaks"
    cache.write_text("no soy una carpeta", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="motor.peaks"):
        p, de_cache = cached_peaks(audio, cache, bins=BINS)
    assert de_cache is False
    np.testing.assert_allclose(p.peaks, _oraculo(audio, BINS), atol=1e-5)
    assert cache.read_text(encoding="utf-8") == "no soy una carpeta"
    _aviso_de_cache(caplog, cache)


def test_un_replace_que_falla_una_vez_no_avisa(audio, tmp_path, monkeypatch, caplog):
    """El reintento que sale bien no es un fallo: no ensucia el log."""
    import logging

    _replace_que_falla(monkeypatch, veces=1)
    with caplog.at_level(logging.WARNING, logger="motor.peaks"):
        cached_peaks(audio, tmp_path / "peaks", bins=BINS)
    assert [r.getMessage() for r in caplog.records if r.name == "motor.peaks"] == []


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
