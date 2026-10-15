"""Tests de `motor/decodificar.py` (tarea 5.71): leer audio sin `audioread`.

El fallback de librosa (audioread) importa `aifc`, `audioop` y `sunau`, que no existen en
Python 3.13, y librosa lo elimina en 1.0. Estos tests fijan tres cosas:

1. Por soundfile, `cargar` y `duracion` dan EXACTAMENTE lo que daban `librosa.load` y
   `librosa.get_duration(path=)`: el motor no cambió de medición.
2. Un archivo que libsndfile no abre (un M4A) se decodifica con ffmpeg y se mide igual que su
   equivalente en WAV. En esta PC no hay ffmpeg, así que el binario es un FALSO: un script que
   entrega el PCM de un WAV sintético y deja anotados los argumentos con que lo llamaron. Eso
   prueba el cableado (subprocess, pipe, ffprobe, recorte, errores), no ffmpeg; para ffmpeg de
   verdad está el último test, que se saltea sin él.
3. Ningún código de producción vuelve a depender de audioread (guarda por AST).

El audio es SINTÉTICO (`motor/sintetico.click_track`); los valores esperados salen de
`librosa.load` sobre el WAV, o sea del camino de soundfile que ya medía.
"""
import ast
import json
import os
import shutil
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

import librosa  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
import soundfile as sf  # noqa: E402

import motor.decodificar as dec  # noqa: E402
from motor.analisis import SR  # noqa: E402
from motor.analisis import cargar as cargar_motor  # noqa: E402
from motor.bandas import compute_bandas  # noqa: E402
from motor.peaks import UnreadableAudio, compute_peaks  # noqa: E402
from motor.sintetico import click_track  # noqa: E402

SR_NATIVO = 44100

FALSO_SCRIPT = r'''
import json, os, sys
import soundfile as sf

args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(["ffprobe" if "-of" in args else "ffmpeg"] + args) + "\n")
modo = os.environ.get("FAKE_MODO", "ok")
y, sr = sf.read(os.environ["FAKE_WAV"], dtype="int16", always_2d=True)
if "-of" in args:                                   # es ffprobe
    if modo == "sin_audio":
        print(json.dumps({"streams": [], "format": {}}))
    else:
        dur = os.environ.get("FAKE_DUR") or "%.6f" % (len(y) / sr)   # lo que DECLARA el archivo
        sin = modo == "sin_duracion_total"
        print(json.dumps({"streams": [{"sample_rate": str(sr), "channels": y.shape[1],
                                       "duration": "N/A" if modo in ("sin_duracion", "sin_duracion_total") else dur}],
                          "format": {"duration": "N/A" if sin else dur}}))
    sys.exit(0)
assert args[args.index("-f") + 1] == "s16le", args  # es ffmpeg: pide PCM 16 bit
datos = y.astype("<i2").tobytes()
if modo == "infinito":                              # un ffmpeg que no termina (con un tope propio)
    for _ in range(100 * 1024 * 1024 // len(datos)):
        sys.stdout.buffer.write(datos)
    sys.exit(0)
if modo == "falla":
    sys.stderr.write("Invalid data found when processing input\n")
    sys.stdout.buffer.write(datos[:1000])
    sys.exit(1)
for _ in range(int(os.environ.get("FAKE_REPETIR", "1"))):   # entrega más (o menos) de lo declarado
    sys.stdout.buffer.write(datos)
'''


class Falso:
    """El ffmpeg/ffprobe falso: sirve el PCM de `wav` y anota cada llamada."""

    def __init__(self, tmp_path, monkeypatch):
        self.wav = tmp_path / "fuente.wav"
        mono, _ = click_track(128.0, dur=6.0, sr=SR_NATIVO, seed=1)
        # Canales distintos: si el pasaje a mono se hiciera mal (o se tomara un solo canal), el
        # resultado no coincidiría con el del WAV.
        sf.write(str(self.wav), np.stack([0.5 * mono, mono], axis=1), SR_NATIVO, subtype="PCM_16")
        self.log_ruta = tmp_path / "llamadas.jsonl"
        script = tmp_path / "falso_av.py"
        script.write_text(FALSO_SCRIPT, encoding="utf-8")
        for nombre in ("ffmpeg", "ffprobe"):
            if os.name == "nt":
                exe = tmp_path / f"{nombre}.bat"
                exe.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n')
            else:
                exe = tmp_path / nombre
                exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
                exe.chmod(0o755)
            monkeypatch.setenv(nombre.upper() + "_BIN", str(exe))
        monkeypatch.setenv("FAKE_LOG", str(self.log_ruta))
        monkeypatch.setenv("FAKE_WAV", str(self.wav))
        monkeypatch.setenv("FAKE_MODO", "ok")
        self._mp = monkeypatch
        # Un M4A que libsndfile NO reconoce (bytes que no son audio): el caso de la biblioteca.
        self.m4a = tmp_path / "tema.m4a"
        self.m4a.write_bytes(b"\x00\x00\x00\x18ftypM4A " + bytes(range(256)) * 8)

    def modo(self, m):
        self._mp.setenv("FAKE_MODO", m)

    def entrega(self, veces=1, declara=None):
        """Entrega `veces` el audio del WAV (6 s) y declara `declara` segundos (por defecto, los
        6 s reales)."""
        self._mp.setenv("FAKE_REPETIR", str(veces))
        if declara is not None:
            self._mp.setenv("FAKE_DUR", str(declara))

    def llamadas(self):
        if not self.log_ruta.exists():
            return []
        return [json.loads(x) for x in self.log_ruta.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def falso(tmp_path, monkeypatch):
    return Falso(tmp_path, monkeypatch)


# --- por soundfile: lo mismo que librosa ---------------------------------------------------

@pytest.mark.parametrize("params", [
    dict(sr=22050, mono=True),
    dict(sr=None, mono=False),
    dict(sr=22050, mono=True, offset=1.0, duration=2.0),
    dict(sr=None, mono=True, offset=2.5),
])
def test_por_soundfile_da_lo_mismo_que_librosa(falso, params):
    y, sr = dec.cargar(falso.wav, **params)
    esperado, sr_esp = librosa.load(str(falso.wav), **params)
    assert sr == sr_esp
    np.testing.assert_array_equal(y, esperado)
    assert falso.llamadas() == [], "un WAV no tiene por qué pasar por ffmpeg"


def test_duracion_por_soundfile_es_la_de_librosa(falso):
    assert dec.duracion(falso.wav) == librosa.get_duration(path=str(falso.wav)) == 6.0


# --- lo que libsndfile no abre: ffmpeg -----------------------------------------------------

def test_un_m4a_se_mide_igual_que_su_wav(falso):
    """El síntoma de la tarea: el M4A caía a audioread y `cargar` del motor devolvía None
    («no se pudo analizar»). Ahora sale el mismo audio que el del WAV equivalente."""
    y = cargar_motor(falso.m4a)
    esperado = cargar_motor(falso.wav)
    assert y is not None, "el M4A no se pudo cargar"
    assert y.shape == esperado.shape == (6 * SR,)
    np.testing.assert_allclose(y, esperado, atol=1e-6)
    assert [c[0] for c in falso.llamadas()] == ["ffprobe", "ffmpeg"]


@pytest.mark.parametrize("params", [
    dict(sr=None, mono=False),
    dict(sr=22050, mono=True, offset=1.0, duration=2.0),
    dict(sr=11025, mono=True, offset=5.0, duration=30.0),     # la ventana se pasa del final
])
def test_ffmpeg_recorta_offset_y_duracion_como_librosa(falso, params):
    y, sr = dec.cargar(falso.m4a, **params)
    esperado, sr_esp = librosa.load(str(falso.wav), **params)
    assert sr == sr_esp
    assert y.shape == esperado.shape
    np.testing.assert_allclose(y, esperado, atol=1e-6)


def test_offset_pasado_del_final_es_vacio_no_inventado(falso):
    y, _ = dec.cargar(falso.m4a, sr=None, mono=True, offset=60.0, duration=30.0)
    assert y.size == 0


def test_ffmpeg_se_llama_con_file_y_sin_tocar_el_original(falso):
    antes = falso.m4a.read_bytes()
    dec.cargar(falso.m4a, sr=22050)
    llamadas = falso.llamadas()
    assert [c[0] for c in llamadas] == ["ffprobe", "ffmpeg"]
    args = llamadas[1][1:]
    entrada = args[args.index("-i") + 1]
    assert entrada == "file:" + os.path.abspath(falso.m4a)
    assert args[-1] == "-" and args[args.index("-f") + 1] == "s16le"   # a un pipe, nunca a un archivo
    # La salida es la que sondeó ffprobe (2 canales a 44,1 kHz), de la pista de audio 0.
    assert args[args.index("-map") + 1] == "0:a:0"
    assert args[args.index("-ac") + 1] == "2" and args[args.index("-ar") + 1] == str(SR_NATIVO)
    assert falso.m4a.read_bytes() == antes


def test_duracion_de_un_m4a_sale_de_ffprobe(falso):
    assert dec.duracion(falso.m4a) == pytest.approx(6.0, abs=1e-6)


def test_lector_pcm_entrega_el_pcm_del_archivo(falso):
    y, _ = sf.read(str(falso.wav), dtype="int16", always_2d=True)
    with dec.LectorPCM(falso.m4a) as f:
        assert (f.samplerate, f.channels) == (SR_NATIVO, 2)
        assert f.duration == pytest.approx(6.0, abs=1e-6)
        datos = b"".join(f)
    assert datos == y.astype("<i2").tobytes()


def test_peaks_y_bandas_de_un_m4a_son_los_del_wav(falso):
    """Cableado completo (sin parchear LectorPCM): picos y bandas del M4A == las del WAV."""
    p, p_wav = compute_peaks(falso.m4a, bins=300), compute_peaks(falso.wav, bins=300)
    np.testing.assert_allclose(p.peaks, p_wav.peaks, atol=1e-6)
    assert (p.duration_s, p.sample_rate, p.channels) == (6.0, SR_NATIVO, 2)
    b, b_wav = compute_bandas(falso.m4a), compute_bandas(falso.wav)
    np.testing.assert_array_equal(b.q, b_wav.q)


# --- errores: ruidosos, nunca un audio inventado --------------------------------------------

def test_sin_ffmpeg_el_error_dice_que_falta_ffmpeg(falso, tmp_path, monkeypatch):
    monkeypatch.setattr(dec, "_binario", lambda nombre: str(tmp_path / "no-existe" / nombre))
    with pytest.raises(dec.DecodificacionError, match="FFMPEG_BIN"):
        dec.cargar(falso.m4a, sr=22050)
    with pytest.raises(dec.DecodificacionError, match="FFMPEG_BIN"):
        dec.duracion(falso.m4a)
    with pytest.raises(UnreadableAudio, match="FFMPEG_BIN"):
        compute_peaks(falso.m4a)
    assert cargar_motor(falso.m4a) is None          # el motor sigue devolviendo None...


def test_el_motor_dice_por_que_no_pudo_cargar(falso, tmp_path, monkeypatch, caplog):
    """... pero con el motivo en el log: «no se pudo analizar» no puede ser mudo."""
    monkeypatch.setattr(dec, "_binario", lambda nombre: str(tmp_path / "no-existe" / nombre))
    with caplog.at_level("WARNING", logger="motor.analisis"):
        assert cargar_motor(falso.m4a) is None
    (registro,) = caplog.records
    msg = registro.getMessage()
    assert msg.startswith("no se pudo decodificar tema.m4a: libsndfile no lo abre (")
    assert "Format not recognised" in msg and "no hay ffmpeg para intentarlo" in msg


def _motivo_de_soundfile(ruta, seek_s=0.0):
    """El error que da libsndfile por su cuenta: el valor esperado sale de soundfile, no del
    código bajo prueba."""
    try:
        with sf.SoundFile(str(ruta)) as f:
            if seek_s:
                f.seek(int(seek_s * f.samplerate))
    except RuntimeError as e:
        return str(e)
    raise AssertionError("soundfile habría podido leerlo")


@pytest.mark.parametrize("como", ["archivo dañado", "offset fuera del archivo"])
def test_sin_ffmpeg_el_error_trae_el_motivo_de_libsndfile(falso, tmp_path, monkeypatch, como):
    """Un WAV con el encabezado roto o un offset más allá del final NO es «instalá ffmpeg»: el
    mensaje dice qué dijo libsndfile y además que no hay con qué reintentar."""
    monkeypatch.setattr(dec, "_binario", lambda nombre: str(tmp_path / "no-existe" / nombre))
    if como == "archivo dañado":
        ruta = tmp_path / "roto.wav"
        ruta.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk" * 4)
        kw, motivo = {}, _motivo_de_soundfile(ruta)
    else:
        ruta, kw = falso.wav, dict(offset=60.0, duration=30.0)
        motivo = _motivo_de_soundfile(ruta, seek_s=60.0)
    with pytest.raises(dec.DecodificacionError) as e:
        dec.cargar(ruta, sr=22050, **kw)
    assert motivo in str(e.value)
    assert str(e.value).startswith("libsndfile no lo abre (") and "FFMPEG_BIN" in str(e.value)
    if como == "archivo dañado":
        with pytest.raises(dec.DecodificacionError) as e:
            dec.duracion(ruta)
        assert motivo in str(e.value) and "FFMPEG_BIN" in str(e.value)


def test_ffmpeg_que_falla_a_mitad_no_entrega_audio_parcial(falso):
    """... pero `cargar` de decodificar NO se traga el error: ffmpeg salió con código ≠ 0 y lo
    leído es un pedazo; si se devolviera, el análisis mediría un tema cortado."""
    falso.modo("falla")
    with pytest.raises(dec.DecodificacionError, match="Invalid data found"):
        dec.cargar(falso.m4a, sr=22050)
    with pytest.raises(UnreadableAudio, match="Invalid data found"):
        compute_peaks(falso.m4a)


def test_archivo_sin_pista_de_audio(falso):
    falso.modo("sin_audio")
    with pytest.raises(dec.DecodificacionError, match="no tiene pista de audio"):
        dec.cargar(falso.m4a, sr=22050)


def test_duracion_n_a_del_stream_se_lee_del_contenedor(falso):
    falso.modo("sin_duracion")      # ffprobe dice «N/A» en el stream pero el contenedor la declara
    assert dec.sondear(falso.m4a)["duration"] == pytest.approx(6.0, abs=1e-6)


def test_sin_duracion_declarada_igual_se_carga_y_se_dibuja(falso):
    """ffprobe no informa la duración (ni en el stream ni en el contenedor): leer no la necesita.
    Los picos se reparten sobre el largo que se mide leyendo, no sobre uno inventado."""
    falso.modo("sin_duracion_total")
    with dec.LectorPCM(falso.m4a) as f:
        assert f.duration is None and f.channels == 2
    y, sr = dec.cargar(falso.m4a, sr=None, mono=False)
    esperado, _ = librosa.load(str(falso.wav), sr=None, mono=False)
    assert sr == SR_NATIVO
    np.testing.assert_allclose(y, esperado, atol=1e-6)
    p, p_wav = compute_peaks(falso.m4a, bins=300), compute_peaks(falso.wav, bins=300)
    np.testing.assert_allclose(p.peaks, p_wav.peaks, atol=1e-6)
    assert p.duration_s == 6.0
    with pytest.raises(dec.DecodificacionError, match="duración"):
        dec.duracion(falso.m4a)                     # preguntar la duración SÍ la exige


def test_un_ffmpeg_que_no_termina_se_corta_por_tope(falso, monkeypatch):
    """El falso escribe ~100 MB para un tema de 6 s (1 MB de PCM): el tope es 4x lo declarado
    más 1 MB, así que se corta con un error, sin llenar la memoria. Sin el piso de 512 MiB
    (en producción sí corta, pero recién a los 512 MiB): acá se prueba el tope proporcional."""
    monkeypatch.setattr(dec, "PISO_TOPE_BYTES", 0)
    falso.modo("infinito")
    with (pytest.raises(dec.DecodificacionError, match="mucho más audio"),
          dec.LectorPCM(falso.m4a) as f):
        sum(len(b) for b in f)
    with pytest.raises(dec.DecodificacionError, match="mucho más audio"):
        dec.cargar(falso.m4a, sr=22050)
    with pytest.raises(UnreadableAudio, match="mucho más audio"):
        compute_peaks(falso.m4a)


def test_un_archivo_que_declara_de_menos_pero_es_legitimo_no_se_corta(falso, monkeypatch):
    """Un MP3 de 12 s cuya duración declarada es 1 s (concatenados, Xing ajeno) se leía entero
    con audioread: el piso del tope lo deja pasar. Sin el piso, ese mismo caso se corta."""
    falso.entrega(veces=2, declara=1)               # declara 1 s, entrega 12 s
    pcm = 12 * SR_NATIVO * 2 * 2
    with dec.LectorPCM(falso.m4a) as f:
        assert f.duration == 1.0
        assert sum(len(b) for b in f) == pcm
    y, _ = dec.cargar(falso.m4a, sr=None, mono=False)
    assert y.shape == (2, 12 * SR_NATIVO)
    monkeypatch.setattr(dec, "PISO_TOPE_BYTES", 0)
    with (pytest.raises(dec.DecodificacionError, match="mucho más audio"),
          dec.LectorPCM(falso.m4a) as f):
        sum(len(b) for b in f)


def test_el_tope_en_bytes_con_y_sin_piso(falso, monkeypatch):
    """Los números a mano para estéreo, 44,1 kHz, PCM de 2 bytes y FACTOR 4 + 1 MiB."""
    with dec.LectorPCM(falso.m4a) as f:             # declara 6 s: la fórmula da 5.282.176
        assert f._tope == 512 << 20                 # ... y el piso gana
    monkeypatch.setattr(dec, "PISO_TOPE_BYTES", 0)
    with dec.LectorPCM(falso.m4a) as f:
        assert f._tope == 4 * (6 * 44100 * 2 * 2) + (1 << 20) == 5_282_176
    falso.entrega(declara=3600)                     # 1 h declarada: la fórmula supera al piso
    monkeypatch.setattr(dec, "PISO_TOPE_BYTES", 512 << 20)
    with dec.LectorPCM(falso.m4a) as f:
        assert f._tope == 4 * (3600 * 44100 * 2 * 2) + (1 << 20) == 2_541_208_576


@pytest.mark.parametrize("veces, se_corta", [(3, False), (4, False), (5, True)])
def test_umbral_del_tope_sobre_lo_declarado(falso, monkeypatch, veces, se_corta):
    """Con el piso apartado y un declarado de 6 s: entregar 3x o 4x lo declarado pasa y 5x corta."""
    monkeypatch.setattr(dec, "PISO_TOPE_BYTES", 0)
    falso.entrega(veces=veces)
    declarado = 6 * 44100 * 2 * 2
    with dec.LectorPCM(falso.m4a) as f:
        if se_corta:
            with pytest.raises(dec.DecodificacionError, match="mucho más audio"):
                sum(len(b) for b in f)
        else:
            assert sum(len(b) for b in f) == veces * declarado


# --- ningún código de producción depende de audioread --------------------------------------

_SALTAR_DIRS = {".venv", "venv", "node_modules", "__pycache__", "tests", "staging"}


_LIBROSA = ("librosa", "librosa.core", "librosa.core.audio")
_CARGAS = {f"{m}.load" for m in _LIBROSA}
_DURACIONES = {f"{m}.get_duration" for m in _LIBROSA}


def _alias(arbol):
    """`{nombre local: nombre completo}` de los imports del módulo, para ver a través de
    `import librosa as lr`, `from librosa.core import load as l`, etc."""
    nombres = {}
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    nombres[a.asname] = a.name
                else:
                    nombres[a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            for a in n.names:
                nombres[a.asname or a.name] = f"{n.module}.{a.name}"
    return nombres


def _completo(nodo, nombres):
    """El nombre con puntos de `lr.core.load` → `librosa.core.load`, o None si no es una
    cadena de atributos que arranca en un nombre."""
    partes = []
    while isinstance(nodo, ast.Attribute):
        partes.append(nodo.attr)
        nodo = nodo.value
    if not isinstance(nodo, ast.Name):
        return None
    partes.append(nombres.get(nodo.id, nodo.id))
    return ".".join(reversed(partes))


def _usos_prohibidos(ruta):
    """`(línea, qué)` de lo que lee audio por un camino que termina en el fallback de audioread:
    importar `audioread`, `audio_open`, `librosa.load` y `librosa.get_duration(path=)`, también
    con alias (`import librosa as lr`, `from librosa.core import load`, `librosa.core.load`)."""
    try:
        with open(ruta, encoding="utf-8") as f:
            arbol = ast.parse(f.read())
    except (SyntaxError, UnicodeDecodeError):
        return []
    nombres = _alias(arbol)
    malos = []
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "audioread" for a in n.names):
            malos.append((n.lineno, "import audioread"))
        elif isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] == "audioread":
                malos.append((n.lineno, "from audioread"))
            elif n.module in _LIBROSA and any(a.name in ("load", "get_duration") for a in n.names):
                malos.append((n.lineno, "from librosa import load/get_duration"))
        elif isinstance(n, ast.Call):
            nombre = _completo(n.func, nombres)
            if nombre in _CARGAS:
                malos.append((n.lineno, "librosa.load"))
            elif nombre in _DURACIONES and any(k.arg in ("path", "filename") for k in n.keywords):
                malos.append((n.lineno, "librosa.get_duration(path=)"))
            elif isinstance(n.func, ast.Attribute) and n.func.attr == "audio_open":
                malos.append((n.lineno, "audio_open"))
    return malos


def _archivos_de_produccion():
    for dirpath, dirs, files in os.walk(RAIZ):
        dirs[:] = [d for d in dirs if d not in _SALTAR_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(".py") and not f.startswith("test_"):
                yield os.path.join(dirpath, f)


@pytest.mark.parametrize("codigo, esperado", [
    ("import audioread", "import audioread"),
    ("import audioread as ar", "import audioread"),
    ("from audioread import ffdec", "from audioread"),
    ("import audioread\naudioread.audio_open('a')", "audio_open"),
    ("import librosa\nlibrosa.load('a.wav')", "librosa.load"),
    ("import librosa as lr\nlr.load('a.wav')", "librosa.load"),
    ("import librosa.core as c\nc.load('a.wav')", "librosa.load"),
    ("import librosa\nlibrosa.core.load('a.wav')", "librosa.load"),
    ("import librosa\nlibrosa.core.audio.load('a.wav')", "librosa.load"),
    ("from librosa import load\nload('a.wav')", "librosa.load"),
    ("from librosa import load as cargar\ncargar('a.wav')", "librosa.load"),
    ("from librosa.core import load", "from librosa import load/get_duration"),
    ("from librosa.core.audio import load", "from librosa import load/get_duration"),
    ("from librosa.core import load\nload('a.wav')", "librosa.load"),
    ("import librosa\nlibrosa.get_duration(path='a.wav')", "librosa.get_duration(path=)"),
    ("import librosa as lr\nlr.get_duration(filename='a.wav')", "librosa.get_duration(path=)"),
    ("import librosa\nlibrosa.core.get_duration(path='a.wav')", "librosa.get_duration(path=)"),
])
def test_el_detector_de_audioread_detecta(tmp_path, codigo, esperado):
    """El detector de abajo tiene que ver cada camino, también con alias (si no, un
    `assert malos == {}` pasa sobre un detector ciego)."""
    f = tmp_path / "mala.py"
    f.write_text(codigo + "\n", encoding="utf-8")
    assert esperado in [que for _, que in _usos_prohibidos(str(f))], codigo


@pytest.mark.parametrize("codigo", [
    "import librosa\nlibrosa.get_duration(y=[0], sr=1)",
    "import numpy as np\nnp.load('a.npy')",
    "import librosa as lr\nlr.resample([0.0], orig_sr=2, target_sr=1)",
    "import soundfile as sf\nsf.read('a.wav')",
    "from motor.decodificar import cargar\ncargar('a.wav', sr=None)",
])
def test_el_detector_no_acusa_lo_que_esta_bien(tmp_path, codigo):
    f = tmp_path / "buena.py"
    f.write_text(codigo + "\n", encoding="utf-8")
    assert _usos_prohibidos(str(f)) == [], codigo


def test_ningun_codigo_depende_de_audioread():
    malos = {os.path.relpath(r, RAIZ): u for r in _archivos_de_produccion()
             if (u := _usos_prohibidos(r))}
    assert malos == {}, ("estos archivos leen audio por un camino que cae a audioread (Python "
                         "3.13 / librosa 1.0): usá motor.decodificar.cargar / duracion / "
                         f"LectorPCM: {malos}")


# --- ffmpeg de verdad (se saltea donde no hay) ----------------------------------------------

def _hay_ffmpeg():
    return all(shutil.which(dec._binario(n)) for n in ("ffmpeg", "ffprobe"))


@pytest.mark.skipif(not _hay_ffmpeg(), reason="no hay ffmpeg/ffprobe en esta máquina")
def test_m4a_real_con_ffmpeg(tmp_path):
    wav = tmp_path / "fuente.wav"
    mono, _ = click_track(128.0, dur=6.0, sr=SR_NATIVO, seed=1)
    sf.write(str(wav), np.stack([0.5 * mono, mono], axis=1), SR_NATIVO, subtype="PCM_16")
    m4a = tmp_path / "tema.m4a"
    subprocess.run([dec._binario("ffmpeg"), "-y", "-v", "error", "-i", str(wav), "-c:a", "aac",
                    "-b:a", "256k", str(m4a)], check=True)
    assert not _abre_soundfile(m4a), "el punto del test es un formato que libsndfile no abre"
    y, sr = dec.cargar(m4a, sr=None, mono=True)
    ref, _ = librosa.load(str(wav), sr=None, mono=True)
    assert sr == SR_NATIVO
    assert abs(len(y) - len(ref)) < 0.1 * SR_NATIVO          # AAC agrega/quita ~decenas de ms
    assert np.sqrt(np.mean(y ** 2)) == pytest.approx(np.sqrt(np.mean(ref ** 2)), rel=0.2)
    assert dec.duracion(m4a) == pytest.approx(6.0, abs=0.15)


def _abre_soundfile(ruta):
    try:
        sf.SoundFile(str(ruta)).close()
    except RuntimeError:
        return False
    return True
