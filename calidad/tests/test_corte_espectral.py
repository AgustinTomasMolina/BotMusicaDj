"""Tests de la tarea 5.1 (corte espectral vs bitrate declarado).

La clasificación y la validación son lógica pura, así que se prueban sin audio. La parte
que sí necesita señal usa un tono sintético construido para tener un corte conocido: no se
afirma la calidad de ningún archivo real (spec §5).
"""
import numpy as np
import pytest

from calidad.corte_espectral import (
    OK,
    SIN_DATOS,
    SOSPECHOSO,
    clasificar,
    corte_esperado_khz,
    validar,
)

# --- Tabla de esperados ------------------------------------------------------------------


def test_esperado_sube_con_el_bitrate():
    assert corte_esperado_khz(320, False) == 20.0
    assert corte_esperado_khz(256, False) == 19.5
    assert corte_esperado_khz(192, False) == 18.5
    assert corte_esperado_khz(128, False) == 16.0
    assert corte_esperado_khz(64, False) < corte_esperado_khz(128, False)


def test_lossless_espera_banda_completa_sin_importar_el_bitrate():
    """Un WAV declara ~1411 kbps; lo que importa es que sea sin pérdida."""
    assert corte_esperado_khz(1411, True) == 20.0
    assert corte_esperado_khz(0, True) == 20.0


def test_bitrate_intermedio_cae_al_escalon_de_abajo():
    assert corte_esperado_khz(200, False) == corte_esperado_khz(192, False)


# --- Clasificación -----------------------------------------------------------------------


def test_320_que_llega_a_20k_es_ok():
    bandera, _ = clasificar(20.1, 20.0, es_muro=True, lossless=False)
    assert bandera == OK


def test_320_que_corta_en_16k_es_sospechoso():
    """El caso central: dice 320 pero tiene el muro de un 128."""
    bandera, motivo = clasificar(16.0, 20.0, es_muro=True, lossless=False)
    assert bandera == SOSPECHOSO
    assert "muro" in motivo


def test_128_que_corta_en_16k_es_ok():
    """Mismo corte que el anterior, pero acá el tag no miente."""
    bandera, _ = clasificar(16.0, 16.0, es_muro=True, lossless=False)
    assert bandera == OK


def test_la_tolerancia_evita_marcar_por_un_pelo():
    assert clasificar(19.5, 20.0, es_muro=True, lossless=False)[0] == OK      # -0.5
    assert clasificar(18.5, 20.0, es_muro=True, lossless=False)[0] == SOSPECHOSO  # -1.5


def test_lossless_con_muro_avisa_transcode():
    bandera, motivo = clasificar(16.0, 20.0, es_muro=True, lossless=True)
    assert bandera == SOSPECHOSO
    assert "transcode" in motivo


def test_sin_muro_el_motivo_admite_que_puede_ser_legitimo():
    """Rampa suave: puede ser un master oscuro o un rip de vinilo. El texto lo dice."""
    _, motivo = clasificar(15.0, 20.0, es_muro=False, lossless=False)
    assert "legítimo" in motivo


# --- Validación contra la marca manual ---------------------------------------------------


def _fila(archivo, bandera, marca):
    return {"archivo": archivo, "bandera": bandera, "marca_manual": marca, "motivo": ""}


def test_validacion_perfecta():
    filas = ([_fila(f"t{i}.mp3", SOSPECHOSO, "trucho") for i in range(10)]
             + [_fila(f"b{i}.mp3", OK, "bueno") for i in range(10)])
    r = validar(filas)
    assert (r["vp"], r["fn"], r["fp"], r["vn"]) == (10, 0, 0, 10)
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["exactitud"] == 1.0
    assert r["desacuerdos"] == []


def test_validacion_cuenta_falsos_positivos_y_negativos():
    filas = [
        _fila("a.mp3", SOSPECHOSO, "trucho"),   # VP
        _fila("b.mp3", SOSPECHOSO, "bueno"),    # FP — master oscuro marcado de más
        _fila("c.mp3", OK, "trucho"),           # FN — transcode que se escapó
        _fila("d.mp3", OK, "bueno"),            # VN
    ]
    r = validar(filas)
    assert (r["vp"], r["fp"], r["fn"], r["vn"]) == (1, 1, 1, 1)
    assert r["precision"] == 0.5 and r["recall"] == 0.5
    assert {d[0] for d in r["desacuerdos"]} == {"b.mp3", "c.mp3"}


def test_filas_sin_marcar_no_cuentan():
    filas = [_fila("a.mp3", SOSPECHOSO, "trucho"), _fila("b.mp3", OK, "")]
    r = validar(filas)
    assert r["n"] == 1 and r["sin_marca"] == 1


def test_sin_datos_no_cuenta_aunque_este_marcado():
    """Un archivo que no se pudo leer no dice nada sobre el detector."""
    r = validar([_fila("a.mp3", SIN_DATOS, "trucho")])
    assert r["n"] == 0 and r["sin_marca"] == 1


def test_acepta_sinonimos_de_la_marca():
    r = validar([_fila("a.mp3", SOSPECHOSO, "  FAKE "), _fila("b.mp3", OK, "Legit")])
    assert (r["vp"], r["vn"]) == (1, 1)


def test_sin_ninguna_marca_no_divide_por_cero():
    r = validar([_fila("a.mp3", OK, "")])
    assert r["n"] == 0
    assert r["precision"] is None and r["recall"] is None and r["exactitud"] is None


# --- Medición sobre una señal de corte conocido ------------------------------------------


def _tono_con_corte(corte_hz: float, sr: int = 44100, dur: float = 5.0) -> np.ndarray:
    """Ruido rosa-ish filtrado a la brava: se anulan los bins por encima de `corte_hz`.

    Es un 'muro' perfecto, construido, con el corte conocido por definición.
    """
    rng = np.random.default_rng(0)
    n = int(sr * dur)
    espectro = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    espectro[freqs > corte_hz] = 0.0
    y = np.fft.irfft(espectro, n).astype(np.float32)
    return y / (np.abs(y).max() + 1e-9)


@pytest.mark.parametrize("corte_hz", [16000, 20000])
def test_mide_el_corte_de_una_senal_construida(corte_hz):
    """El corte medido tiene que caer cerca del que se construyó."""
    from analizar_calidad import _analizar_espectro, _espectro_db

    sr = 44100
    freqs, db = _espectro_db(_tono_con_corte(corte_hz, sr=sr), sr)
    medido = _analizar_espectro(freqs, db, sr)["corte"]
    assert abs(medido - corte_hz) < 1200, f"construí {corte_hz} Hz, medí {medido:.0f}"


def test_archivo_mas_corto_que_la_ventana_igual_se_mide(tmp_path):
    """Regresión: con offset=45 s en un archivo de 5 s, librosa tira NoBackendError en vez
    de devolver vacío. El reintento desde el inicio tiene que seguir, no abandonar — si no,
    todo sample o loop corto quedaba marcado 'sin datos' siendo legible."""
    import soundfile as sf

    from calidad.corte_espectral import SIN_DATOS, analizar_uno

    sr = 44100
    p = tmp_path / "corto.wav"
    sf.write(p, _tono_con_corte(16000, sr=sr, dur=5.0), sr)

    fila = analizar_uno(str(p))
    assert fila.bandera != SIN_DATOS
    assert fila.sample_rate_hz == sr
    assert fila.corte_medido_khz > 10.0


def test_distingue_un_corte_bajo_de_uno_alto():
    from analizar_calidad import _analizar_espectro, _espectro_db

    sr = 44100
    def corte(f):
        fr, db = _espectro_db(_tono_con_corte(f, sr=sr), sr)
        return _analizar_espectro(fr, db, sr)["corte"]

    assert corte(16000) < corte(20000) - 2000
