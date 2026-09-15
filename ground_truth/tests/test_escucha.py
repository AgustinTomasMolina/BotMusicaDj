"""Tests de la escucha de discrepancias (`ground_truth.escucha`).

Selección e índice: CSV → decisión, se prueban con tablas armadas a mano como en
`benchmark/tests/test_evaluar.py`. Las keys de esas tablas son valores construidos para
ejercitar cada caso, no se afirma la tonalidad de ningún track real (spec §5).

Fragmentos: WAVs sintéticos de `motor.sintetico.click_track`. Lo que se protege es que el
fragmento exportado sea EXACTAMENTE el audio que analizó el motor. El corte esperado se
saca de otro lado que el código bajo prueba: de dónde cae en memoria la vista que devuelve
la propia función del motor sobre el audio cargado como lo carga el motor (el código bajo
prueba usa `np.arange`). La tolerancia es ±1 muestra nativa: el redondeo al pasar un
índice a 22050 Hz al sample rate del original (`a_muestra_nativa`).
"""
import csv
import hashlib

import numpy as np
import pytest
import soundfile as sf

from benchmark.analizar import SEMILLA
from ground_truth import escucha
from motor.analisis import SR, cargar
from motor.sintetico import click_track
from motor.tonalidad import _tramos_disjuntos, ventana_central

# --- Tablas de ejemplo -----------------------------------------------------------------

COLS_A = ["archivo", "ruta", "duracion_s", "bpm_est", "key_est", "confianza",
          "acuerdo", "tramos", "t_carga_s", "t_analisis_s", "t_total_s", "metodo"]

COLS_GT = ["track_id", "artist", "name", "bpm", "tonality", "camelot",
           "tonalidad_formato", "genre", "duration_s", "kind", "num_cues", "location"]


def _fila_a(archivo, key, acuerdo="3/3", tramos="", carpeta="D:/musica", metodo="tono"):
    return {"archivo": archivo, "ruta": f"{carpeta}/{archivo}", "duracion_s": "240.0",
            "bpm_est": "128.0", "key_est": key, "confianza": "0.7", "acuerdo": acuerdo,
            "tramos": tramos, "t_carga_s": "1.0", "t_analisis_s": "1.0", "t_total_s": "2.0",
            "metodo": metodo}


def _fila_gt(i, archivo, camelot, tonality="", artist="Artista", name="Titulo"):
    return {"track_id": str(i), "artist": artist, "name": name, "bpm": "128.0",
            "tonality": tonality, "camelot": camelot, "tonalidad_formato": "clasica",
            "genre": "", "duration_s": "240", "kind": "WAV File", "num_cues": "0",
            "location": f"C:/Users/dj/Music/{archivo}"}


def _escribir(ruta, cols, filas):
    with ruta.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(filas)
    return ruta


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --- Audio sintético (una vez por módulo: generarlo es lo caro) -------------------------


@pytest.fixture(scope="module")
def estereo_44k(tmp_path_factory):
    """100 s estéreo a 44100: > 90 s, así la ventana central es un recorte de verdad.
    Los canales difieren (ruido con otra semilla) para que un cruce de canales se note."""
    izq, _ = click_track(128.0, dur=100.0, sr=44100, nota="A", modo="min", seed=1)
    der, _ = click_track(128.0, dur=100.0, sr=44100, nota="A", modo="min", seed=2)
    p = tmp_path_factory.mktemp("audio") / "estereo.wav"
    sf.write(str(p), np.stack([izq, der], axis=1), 44100, subtype="FLOAT")
    return p


@pytest.fixture(scope="module")
def mono_largo(tmp_path_factory):
    """140 s mono a 8000 Hz: da para los 3 tramos de 45 s (> 135 s) y carga rápido."""
    y, _ = click_track(128.0, dur=140.0, sr=8000, nota="C", modo="min", seed=3)
    p = tmp_path_factory.mktemp("audio") / "mono_largo.wav"
    sf.write(str(p), y, 8000, subtype="FLOAT")
    return p


def _offset_de_vista(vista, base):
    """Índice de la primera muestra de `vista` dentro de `base`, por su dirección en memoria."""
    assert np.shares_memory(vista, base), "la función del motor ya no devuelve una vista"
    return (vista.__array_interface__["data"][0] - base.__array_interface__["data"][0]) // base.itemsize


def _verificar_corte(exportado, original, ini_22k, largo_22k, sr_nat):
    """El WAV exportado es el trozo del original que empieza en `ini_22k` (a 22050 Hz),
    con tolerancia ±1 muestra nativa. Compara las muestras, no la duración."""
    datos, sr = sf.read(str(exportado), always_2d=True, dtype="float32")
    orig, sr_o = sf.read(str(original), always_2d=True, dtype="float32")
    assert sr == sr_nat == sr_o, f"sample rate exportado {sr}, original {sr_o}"
    assert datos.shape[1] == orig.shape[1], (
        f"canales exportados {datos.shape[1]}, el original tiene {orig.shape[1]}")
    esperado_ini = ini_22k * sr_nat / SR
    esperado_largo = largo_22k * sr_nat / SR
    assert abs(datos.shape[0] - esperado_largo) <= 1, (
        f"el fragmento dura {datos.shape[0]} muestras, se esperaban {esperado_largo:.1f}")
    for s in range(int(np.floor(esperado_ini)) - 1, int(np.ceil(esperado_ini)) + 2):
        if np.array_equal(datos, orig[s:s + datos.shape[0]]):
            assert abs(s - esperado_ini) <= 1, (
                f"el fragmento arranca en la muestra {s}, se esperaba {esperado_ini:.1f} (±1)")
            return
    raise AssertionError(
        f"el fragmento no es el audio original desde la muestra {esperado_ini:.1f} (±1)")


# --- Selección -------------------------------------------------------------------------


def test_seleccion_solo_unanime_con_key_distinta_y_con_referencia():
    analisis = [
        _fila_a("elegido.wav", "8A", "3/3", "5A|5A|5A"),
        _fila_a("dos_de_tres.wav", "8A", "2/3"),
        _fila_a("misma_key.wav", "8A", "3/3"),
        _fila_a("sin_ref.wav", "8A", "3/3"),
        _fila_a("sin_acuerdo.wav", "8A", ""),
        _fila_a("fuera_del_gt.wav", "8A", "3/3"),
    ]
    gt = [
        _fila_gt(1, "elegido.wav", "5A", "Cm", artist="Boltcore", name="Try"),
        _fila_gt(2, "dos_de_tres.wav", "5A"),
        _fila_gt(3, "misma_key.wav", "8A"),
        _fila_gt(4, "sin_ref.wav", ""),
        _fila_gt(5, "sin_acuerdo.wav", "5A"),
    ]
    sel = escucha.seleccionar(analisis, gt)
    assert [(c.archivo, c.ruta, c.key_motor, c.key_rekordbox, c.tonality, c.nombre, c.acuerdo)
            for c in sel["candidatos"]] == [
        ("elegido.wav", "D:/musica/elegido.wav", "8A", "5A", "Cm", "Boltcore — Try", "3/3")]
    k = sel["conteo"]
    assert (k["no_unanime"], k["misma_key"], k["sin_referencia"], k["sin_acuerdo"],
            k["sin_gt"]) == (1, 1, 1, 1, 1), f"recuento de exclusiones: {k}"


def test_muestra_determinista_e_independiente_del_orden_del_csv():
    analisis = [_fila_a(f"t{i:02d}.wav", "8A", "3/3") for i in range(12)]
    gt = [_fila_gt(i, f"t{i:02d}.wav", "5A") for i in range(12)]
    cands = escucha.seleccionar(analisis, gt)["candidatos"]
    assert len(cands) == 12

    a = [c.ruta for c in escucha.elegir(cands, 5)]
    b = [c.ruta for c in escucha.elegir(list(reversed(cands)), 5)]
    assert a == b, "el orden de las filas cambió la muestra"
    assert len(set(a)) == 5 and set(a) <= {c.ruta for c in cands}
    # la muestra es la de benchmark.analizar.muestrear con su SEMILLA, no un random propio
    import random
    todas = sorted(c.ruta for c in cands)
    assert a == sorted(random.Random(SEMILLA).sample(todas, 5))
    # con n >= candidatos, van todos
    assert [c.ruta for c in escucha.elegir(cands, 50)] == todas


# --- --acuerdo-de ----------------------------------------------------------------------


def test_acuerdo_de_cruza_por_ruta_no_por_basename():
    analisis = [_fila_a("t.wav", "8A", "", carpeta="D:/x"),
                _fila_a("t.wav", "9A", "", carpeta="D:/y"),
                _fila_a("solo.wav", "1A", "", carpeta="D:/z")]
    consenso = [_fila_a("t.wav", "6A", "1/3", "6A|7A|1A", carpeta="D:/x", metodo="tono_consenso"),
                _fila_a("t.wav", "5A", "3/3", "5A|5A|5A", carpeta="D:/y", metodo="tono_consenso")]
    combinadas, sin_par = escucha.combinar_acuerdo(analisis, consenso)
    assert [(f["ruta"], f["key_est"], f["acuerdo"], f["tramos"]) for f in combinadas] == [
        ("D:/x/t.wav", "8A", "1/3", "6A|7A|1A"),
        ("D:/y/t.wav", "9A", "3/3", "5A|5A|5A"),
    ]
    assert sin_par == ["D:/z/solo.wav"]


def test_acuerdo_de_que_no_es_consenso_falla():
    analisis = [_fila_a("t.wav", "8A", "")]
    sin_consenso = [_fila_a("t.wav", "8A", "", metodo="tono")]
    with pytest.raises(escucha.EscuchaIncompleta) as e:
        escucha.combinar_acuerdo(analisis, sin_consenso)
    assert "no es una corrida con consenso" in str(e.value)


def test_sin_acuerdo_y_sin_acuerdo_de_falla_sin_escribir(tmp_path):
    a = _escribir(tmp_path / "a.csv", COLS_A, [_fila_a("t.wav", "8A", "")])
    g = _escribir(tmp_path / "gt.csv", COLS_GT, [_fila_gt(1, "t.wav", "5A")])
    salida = tmp_path / "escucha"
    with pytest.raises(escucha.EscuchaIncompleta) as e:
        escucha.correr(a, g, salida)
    assert "--acuerdo-de" in str(e.value), "el error no dice cómo resolverlo"
    assert not salida.exists(), "escribió la salida aunque faltaba el acuerdo"


def test_analisis_con_consenso_en_lugar_de_sin_consenso_falla(tmp_path):
    """La trampa de los CSV viejos: la key de la pasada con consenso NO es la del motor."""
    a = _escribir(tmp_path / "a.csv", COLS_A,
                  [_fila_a("t.wav", "8A", "3/3", metodo="tono_consenso")])
    g = _escribir(tmp_path / "gt.csv", COLS_GT, [_fila_gt(1, "t.wav", "5A")])
    with pytest.raises(escucha.EscuchaIncompleta) as e:
        escucha.correr(a, g, tmp_path / "escucha")
    assert "key_est es la del voto" in str(e.value)


# --- Fragmentos ------------------------------------------------------------------------


def test_ventana_central_exportada_es_la_analizada_estereo_44k(estereo_44k, tmp_path):
    antes = _sha(estereo_44k)
    fr = escucha.exportar_track(str(estereo_44k), "01_x", tmp_path)
    assert fr.estado == escucha.OK

    y = cargar(estereo_44k, SR)
    vista = ventana_central(y, SR)
    ini = _offset_de_vista(vista, y)
    assert fr.central == "01_x_ventana_central.wav"
    _verificar_corte(tmp_path / fr.central, estereo_44k, ini, vista.size, 44100)
    assert abs(sf.info(str(tmp_path / fr.central)).frames - 90 * 44100) <= 1, \
        "la ventana central del motor ya no dura 90 s"
    assert (fr.inicio_s, fr.fin_s) == (ini / SR, (ini + vista.size) / SR)
    assert _sha(estereo_44k) == antes, "se modificó el audio original"


def test_tramos_exportados_son_los_del_consenso(mono_largo, tmp_path):
    antes = _sha(mono_largo)
    fr = escucha.exportar_track(str(mono_largo), "02_y", tmp_path)

    y = cargar(mono_largo, SR)
    vistas = _tramos_disjuntos(y, SR, 3, 45)
    assert len(vistas) == 3
    assert fr.tramos == ["02_y_tramo1.wav", "02_y_tramo2.wav", "02_y_tramo3.wav"]
    for nombre, vista in zip(fr.tramos, vistas, strict=True):
        _verificar_corte(tmp_path / nombre, mono_largo, _offset_de_vista(vista, y),
                         vista.size, 8000)
    assert _sha(mono_largo) == antes, "se modificó el audio original"


def test_nombre_seguro_para_windows():
    assert escucha.nombre_seguro('AC/DC: "Back" <in> Black? *|\\ . ') == "AC_DC_ _Back_ _in_ Black_ ___"
    assert escucha.nombre_seguro("x" * 200) == "x" * 60
    assert escucha.nombre_seguro(" ... ") == "track"


# --- Corrida completa e índice ---------------------------------------------------------


def test_error_de_modo():
    assert escucha.error_de_modo("8A", "11B") is True     # Am vs A: misma tónica
    assert escucha.error_de_modo("5A", "8B") is True      # Cm vs C
    assert escucha.error_de_modo("8A", "8B") is False     # Am vs C: relativo, otra tónica
    assert escucha.error_de_modo("8A", "9A") is False     # Am vs Em: vecino
    assert escucha.error_de_modo("8A", "?") is False


def test_audio_faltante_no_frena_y_el_indice_es_correcto(mono_largo, tmp_path):
    faltante = tmp_path / "desenchufado" / "a_faltante.wav"
    filas_a = [
        {**_fila_a("a_faltante.wav", "8A", "3/3", "8A|8A|8A"), "ruta": str(faltante)},
        {**_fila_a(mono_largo.name, "5A", "3/3", "5A|5A|5A"), "ruta": str(mono_largo)},
    ]
    gt = [_fila_gt(1, "a_faltante.wav", "9A", "Em", artist="", name=""),
          _fila_gt(2, mono_largo.name, "8B", "C", artist="Boltcore", name="Try")]
    a = _escribir(tmp_path / "a.csv", COLS_A, filas_a)
    g = _escribir(tmp_path / "gt.csv", COLS_GT, gt)
    salida = tmp_path / "escucha"
    antes = _sha(mono_largo)

    res = escucha.correr(a, g, salida, n=10)
    assert (res["candidatos"], res["elegidos"]) == (2, 2)

    raw = (salida / escucha.INDICE_CSV).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "el CSV no tiene BOM: Excel lo abre como ANSI"
    with (salida / escucha.INDICE_CSV).open(encoding="utf-8-sig", newline="") as fh:
        filas = {f["ruta"]: f for f in csv.DictReader(fh)}

    f = filas[str(faltante)]
    assert (f["estado"], f["archivo_ventana_central"], f["archivos_tramos"], f["nombre"]) == (
        "audio no encontrado", "", "", "a_faltante.wav")
    assert (f["key_motor_camelot"], f["key_motor_clasica"], f["key_rekordbox_camelot"],
            f["key_rekordbox_tonality"], f["relacion"], f["error_de_modo"]) == (
        "8A", "Am", "9A", "Em", "vecino", "no")

    f = filas[str(mono_largo)]
    assert f["estado"] == "ok"
    assert (f["nombre"], f["key_motor_camelot"], f["key_motor_clasica"],
            f["key_rekordbox_camelot"], f["key_rekordbox_tonality"], f["relacion"],
            f["error_de_modo"], f["acuerdo"], f["tramos"]) == (
        "Boltcore — Try", "5A", "Cm", "8B", "C", "lejano", "si", "3/3", "5A|5A|5A")
    prefijo = f"{f['n'].zfill(2)}_mono_largo"
    assert f["archivo_ventana_central"] == f"{prefijo}_ventana_central.wav"
    assert f["archivos_tramos"] == "|".join(f"{prefijo}_tramo{k}.wav" for k in (1, 2, 3))
    y = cargar(mono_largo, SR)
    ini = _offset_de_vista(ventana_central(y, SR), y)
    assert f["ventana_inicio_s"] == f"{ini / SR:.2f}"
    assert f["ventana_inicio"] == f"{int(ini / SR) // 60:02d}:{int(ini / SR) % 60:02d}"
    assert (f["veredicto"], f["notas"]) == ("", ""), "las columnas del oído no salen vacías"
    assert sf.info(str(salida / f["archivo_ventana_central"])).samplerate == 8000
    assert _sha(mono_largo) == antes

    md = (salida / escucha.INDICE_MD).read_text(encoding="utf-8")
    assert "| 5A (Cm) | 8B (C) | lejano | si | 3/3 |" in md


def test_no_pisa_un_indice_existente(tmp_path):
    a = _escribir(tmp_path / "a.csv", COLS_A, [_fila_a("t.wav", "8A", "3/3")])
    g = _escribir(tmp_path / "gt.csv", COLS_GT, [_fila_gt(1, "t.wav", "5A")])
    salida = tmp_path / "escucha"
    salida.mkdir()
    (salida / escucha.INDICE_CSV).write_text("veredicto cargado", encoding="utf-8")
    with pytest.raises(escucha.EscuchaIncompleta):
        escucha.correr(a, g, salida)
    assert (salida / escucha.INDICE_CSV).read_text(encoding="utf-8") == "veredicto cargado"
