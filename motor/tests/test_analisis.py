"""Tests de `motor/analisis.py`: archivo de audio → `TrackFeatures`.

Todo el audio sale del generador (`motor/sintetico.py`) o de señales armadas acá con el
contenido conocido por construcción: BPM, tonalidad y cantidad de golpes no se inventan,
se fabrican (spec §5). Se escriben como WAV float en `tmp_path` y se leen por el mismo
`librosa.load` que usa el motor, así se prueba el camino completo desde el archivo.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import soundfile as sf  # noqa: E402

from motor.analisis import SR, _perfil_de_la_key, analizar_archivo  # noqa: E402
from motor.sintetico import click_track  # noqa: E402
from motor.store import Store  # noqa: E402
from motor.tonalidad import NOTAS, ranking_chroma  # noqa: E402


def _wav(ruta, y, sr=SR):
    sf.write(str(ruta), np.asarray(y, dtype=np.float32), sr, subtype="FLOAT")
    return ruta


# --- lo que mide -----------------------------------------------------------------------


def test_bpm_y_key_del_generador(tmp_path):
    """128 BPM en La menor por construcción → BPM dentro de ±1 y key 8A (La menor en la
    rueda Camelot). La duración y el RMS se comparan contra la señal que se escribió."""
    y, sr = click_track(128.0, dur=10, nota="A", modo="min", seed=3)
    ruta = _wav(tmp_path / "a.wav", y, sr)

    features, duracion = analizar_archivo(ruta)

    assert abs(features.bpm - 128.0) <= 1.0, f"BPM {features.bpm} y el generador hizo 128"
    assert features.key == "8A", f"key {features.key!r} y el generador hizo La menor (8A)"
    assert duracion == pytest.approx(10.0, abs=1 / sr), f"duración {duracion}"
    rms_generado = float(np.sqrt(np.mean(y.astype(np.float64) ** 2)))
    assert features.energy_raw == pytest.approx(rms_generado, rel=1e-4), \
        f"energy_raw {features.energy_raw} y el RMS de la señal escrita es {rms_generado}"
    assert features.percussive_ratio is None, \
        f"percussive_ratio {features.percussive_ratio!r}: no se mide (HPSS), tiene que ser None"


def test_onset_rate_contra_golpes_conocidos(tmp_path):
    """Onsets por segundo contra una señal con un golpe por beat y NADA más: un kick que
    decae del todo antes del siguiente (el de `click_track` se corta a 120 ms y ese corte es
    otro transitorio, así que no sirve de referencia para contar golpes).

    La tolerancia es 15%: `onset_detect` agrega algún onset suelto en los bordes, medido
    ~10% en 20 s. Lo que tiene que atrapar es el doble conteo (2×) y cualquier unidad
    equivocada (frames en vez de segundos da órdenes de magnitud)."""
    bpm, dur = 120.0, 20.0
    n = int(dur * SR)
    tk = np.arange(int(0.3 * SR)) / SR
    kick = np.sin(2 * np.pi * 55 * tk) * np.exp(-tk * 28)   # a 300 ms queda en 2e-4
    y = np.zeros(n)
    golpes = 0
    for i in range(int(dur * bpm / 60)):
        ini = int(round(i * 60 / bpm * SR))
        if ini + kick.size <= n:
            y[ini:ini + kick.size] += kick
            golpes += 1
    y += 0.004 * np.random.default_rng(0).standard_normal(n)
    ruta = _wav(tmp_path / "golpes.wav", y)

    features, _ = analizar_archivo(ruta)
    esperado = golpes / dur
    assert features.onset_rate == pytest.approx(esperado, rel=0.15), \
        f"onset_rate {features.onset_rate:.2f}/s y la señal tiene {esperado:.2f} golpes/s"


def test_archivo_corto_o_ilegible_es_none(tmp_path):
    """Igual que el benchmark: < 1 s o algo que no decodifica no es un track."""
    y, sr = click_track(128.0, dur=0.5)
    corto = _wav(tmp_path / "corto.wav", y, sr)
    roto = tmp_path / "roto.wav"
    roto.write_bytes(b"esto no es un wav")

    assert analizar_archivo(corto) is None, "medio segundo de audio se analizó como track"
    assert analizar_archivo(roto) is None, "un archivo que no decodifica se analizó como track"


def test_la_tonica_del_embedding_es_la_de_la_key():
    """`embed` rota el chroma a la tónica que gana en `ranking_chroma(perfil)`. El perfil
    que le pasa el análisis tiene que hacer ganar EXACTAMENTE la key reportada, en las 24."""
    for nota in NOTAS:
        for modo in ("maj", "min"):
            _, n, m = ranking_chroma(_perfil_de_la_key(nota, modo))[0]
            assert (n, m) == (nota, modo), f"perfil de {nota} {modo} hace ganar {n} {m}"
    assert _perfil_de_la_key(None, None) is None, "sin key tiene que dejar que embed decida"


# --- mismo camino que el benchmark (tarea 5.66) ----------------------------------------


@pytest.fixture(scope="module")
def audio_que_separa_los_metodos(tmp_path_factory):
    """140 s en tres bloques iguales: La menor · Do# mayor · La menor.

    - `tono` mira la ventana central de 90 s, que es mayoría Do# mayor → 3B.
    - `tono_consenso` vota entre los tres bloques: 2 de 3 en La menor → 8A.

    Con un audio donde los dos métodos coinciden, un motor que ignorara el flag `consenso`
    pasaría la comparación contra el benchmark igual. Acá no."""
    bloque = 140.0 / 3
    partes = [click_track(128.0, dur=bloque, nota=n, modo=m, seed=i)[0]
              for i, (n, m) in enumerate((("A", "min"), ("C#", "maj"), ("A", "min")))]
    return _wav(tmp_path_factory.mktemp("separa") / "tres_bloques.wav", np.concatenate(partes))


@pytest.mark.parametrize("consenso, key_esperada", [(False, "3B"), (True, "8A")])
def test_motor_y_benchmark_miden_igual(audio_que_separa_los_metodos, consenso, key_esperada):
    from benchmark.analizar import analizar_uno

    ruta = audio_que_separa_los_metodos
    features, duracion = analizar_archivo(ruta, consenso=consenso)
    fila = analizar_uno(str(ruta), consenso=consenso)

    assert features.key == fila.key_est, \
        f"consenso={consenso}: el motor dice {features.key} y el benchmark {fila.key_est}"
    assert round(features.bpm, 2) == fila.bpm_est, \
        f"consenso={consenso}: el motor mide {features.bpm} BPM y el benchmark {fila.bpm_est}"
    assert round(duracion, 1) == fila.duracion_s, f"duración {duracion} vs {fila.duracion_s}"
    # Y que sea la key que corresponde al método: si los dos caminos ignoraran el flag a la
    # vez, las dos igualdades de arriba pasarían igual.
    assert features.key == key_esperada, \
        f"consenso={consenso} dio {features.key}; el audio está armado para dar {key_esperada}"


def test_el_benchmark_no_tiene_su_propia_copia_de_la_medicion():
    """Estructural: el benchmark llama a las funciones del motor, no a una copia."""
    import benchmark.analizar as bench
    import motor.analisis as motor

    assert bench.cargar is motor.cargar, "benchmark.analizar carga el audio por su cuenta"
    assert bench.medir_bpm_y_tono is motor.medir_bpm_y_tono, \
        "benchmark.analizar mide BPM/tonalidad por su cuenta"


# --- lo no medido no se inventa --------------------------------------------------------


def test_percussive_ratio_no_medido_vuelve_none_de_la_base(tmp_path):
    """El análisis real → la base → de vuelta. En la columna tiene que quedar NULL, no 0.0:
    un 0.0 se leería como "este track no tiene percusión"."""
    y, sr = click_track(126.0, dur=6, nota="C", modo="maj", seed=1)
    ruta = _wav(tmp_path / "c.wav", y, sr)
    features, duracion = analizar_archivo(ruta)

    db = tmp_path / "db.sqlite"
    with Store(db) as store:
        store.upsert(ruta, features, duration=duracion, license="CC0", source_url="sintetico")
        vuelta = store.get_features(ruta)

    assert vuelta.percussive_ratio is None, \
        f"percussive_ratio volvió {vuelta.percussive_ratio!r} de la base y nunca se midió"
    assert vuelta.onset_rate == features.onset_rate, "onset_rate SÍ se mide y se perdió"
    con = sqlite3.connect(str(db))
    crudo = con.execute("SELECT percussive_ratio FROM tracks").fetchone()[0]
    con.close()
    assert crudo is None, f"en la base quedó {crudo!r} en vez de NULL"
