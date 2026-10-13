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
        dur = "%.6f" % (len(y) / sr)
        print(json.dumps({"streams": [{"sample_rate": str(sr), "channels": y.shape[1],
                                       "duration": "N/A" if modo == "sin_duracion" else dur}],
                          "format": {"duration": dur}}))
    sys.exit(0)
assert args[args.index("-f") + 1] == "s16le", args  # es ffmpeg: pide PCM 16 bit
datos = y.astype("<i2").tobytes()
if modo == "falla":
    sys.stderr.write("Invalid data found when processing input\n")
    sys.stdout.buffer.write(datos[:1000])
    sys.exit(1)
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
    assert registro.getMessage().startswith("no se pudo decodificar tema.m4a: no se encontró ffmpeg")


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


# --- ningún código de producción depende de audioread --------------------------------------

_SALTAR_DIRS = {".venv", "venv", "node_modules", "__pycache__", "tests", "staging"}


def _usos_prohibidos(ruta):
    """Líneas que usan `audioread`, `librosa.load` o `librosa.get_duration(path=)`: los tres
    caminos que terminan en el fallback de audioread."""
    try:
        with open(ruta, encoding="utf-8") as f:
            arbol = ast.parse(f.read())
    except (SyntaxError, UnicodeDecodeError):
        return []
    malos = []
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "audioread" for a in n.names):
            malos.append((n.lineno, "import audioread"))
        elif isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] == "audioread":
                malos.append((n.lineno, "from audioread"))
            elif n.module == "librosa" and any(a.name in ("load", "get_duration") for a in n.names):
                malos.append((n.lineno, "from librosa import load/get_duration"))
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name)):
            if n.func.value.id == "librosa" and n.func.attr == "load":
                malos.append((n.lineno, "librosa.load"))
            elif n.func.value.id == "librosa" and n.func.attr == "get_duration" \
                    and any(k.arg in ("path", "filename") for k in n.keywords):
                malos.append((n.lineno, "librosa.get_duration(path=)"))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "audio_open":
            malos.append((n.lineno, "audio_open"))
    return malos


def _archivos_de_produccion():
    for dirpath, dirs, files in os.walk(RAIZ):
        dirs[:] = [d for d in dirs if d not in _SALTAR_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(".py") and not f.startswith("test_"):
                yield os.path.join(dirpath, f)


def test_el_detector_de_audioread_detecta(tmp_path):
    """El detector de abajo tiene que ver cada uno de los caminos (si no, un `assert not malos`
    pasa sobre un detector ciego)."""
    f = tmp_path / "mala.py"
    f.write_text("import audioread\nimport librosa\n"
                 "y = librosa.load('a.wav')\nd = librosa.get_duration(path='a.wav')\n"
                 "d2 = librosa.get_duration(y=y, sr=1)\nr = audioread.audio_open('a')\n"
                 "from librosa import load\n", encoding="utf-8")
    assert sorted(_usos_prohibidos(str(f))) == [
        (1, "import audioread"), (3, "librosa.load"), (4, "librosa.get_duration(path=)"),
        (6, "audio_open"), (7, "from librosa import load/get_duration")]


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
