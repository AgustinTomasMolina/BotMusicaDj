"""Tests de la escucha de discrepancias (`ground_truth.escucha`).

Selección e índice: CSV → decisión, se prueban con tablas armadas a mano como en
`benchmark/tests/test_evaluar.py`. Las keys de esas tablas son valores construidos para
ejercitar cada caso, no se afirma la tonalidad de ningún track real (spec §5).

Fragmentos: WAVs de RUIDO BLANCO independiente por canal. Lo que se protege es que el
fragmento exportado sea EXACTAMENTE el audio que analizó el motor. El corte esperado se
saca de otro lado que el código bajo prueba: de dónde cae en memoria la vista que devuelve
la propia función del motor sobre el audio cargado como lo carga el motor (el código bajo
prueba usa `np.arange`).

Dos tolerancias, cada una con su causa y ninguna más ancha que eso:
  - En el TIEMPO, 0.5 muestra nativa: el redondeo al entero más cercano al pasar un índice a
    22050 Hz al sample rate del original (`a_muestra_nativa`). A 44100 la cuenta es exacta
    (tolerancia efectiva 0); a 8000 nunca cae justo en .5 (8000·i ≡ 11025 mod 22050 no
    tiene solución), así que un corrimiento de 1 muestra siempre queda afuera.
  - En la AMPLITUD, `TOL_PCM16` = 1/32768: el fragmento es PCM 16 bits. soundfile 0.14 /
    libsndfile 1.2.2 escala por 32768 y redondea al más cercano SIN dither (error ≤ 0.5/32768),
    y satura +1.0 en 32767 (error 1/32768). Medido: con ruido uniforme en [-1, 1] el error
    máximo de ida y vuelta fue exactamente 1/32768. El ruido de prueba va en ±0.9, sin saturar.
    Por qué sigue detectando 1 muestra de corrimiento: dos muestras vecinas de ruido uniforme
    en ±0.9 difieren en promedio 0.6 (≈ 20000 veces la tolerancia), y la comparación exige
    TODAS las muestras del fragmento (millones) dentro de 1/32768 a la vez.
"""
import csv
import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from benchmark.analizar import SEMILLA, FilaAnalisis
from ground_truth import escucha
from motor.analisis import SR, cargar
from motor.tonalidad import _tramos_disjuntos, ventana_central

TOL_PCM16 = 1.0 / 32768

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


def _ruido(segundos, sr, canales, seed):
    """Ruido blanco uniforme en ±0.9, independiente por canal: vecinas muy distintas (un
    corrimiento de 1 muestra no pasa la tolerancia de 16 bits), canales distintos (un cruce
    se nota) y sin llegar a ±1.0 (el PCM 16 no satura)."""
    rng = np.random.default_rng(seed)
    return rng.uniform(-0.9, 0.9, (int(segundos * sr), canales)).astype(np.float32)


@pytest.fixture(scope="module")
def estereo_44k(tmp_path_factory):
    """100 s estéreo a 44100: > 90 s, así la ventana central es un recorte de verdad."""
    p = tmp_path_factory.mktemp("audio") / "estereo.wav"
    sf.write(str(p), _ruido(100.0, 44100, 2, seed=1), 44100, subtype="FLOAT")
    return p


@pytest.fixture(scope="module")
def mono_largo(tmp_path_factory):
    """140 s mono a 8000 Hz: da para los 3 tramos de 45 s (> 135 s) y carga rápido."""
    p = tmp_path_factory.mktemp("audio") / "mono_largo.wav"
    sf.write(str(p), _ruido(140.0, 8000, 1, seed=3)[:, 0], 8000, subtype="FLOAT")
    return p


def _offset_de_vista(vista, base):
    """Índice de la primera muestra de `vista` dentro de `base`, por su dirección en memoria."""
    assert np.shares_memory(vista, base), "la función del motor ya no devuelve una vista"
    return (vista.__array_interface__["data"][0] - base.__array_interface__["data"][0]) // base.itemsize


def _verificar_corte(exportado, original, ini_22k, largo_22k, sr_nat):
    """El WAV exportado es el trozo del original que empieza en `ini_22k` (a 22050 Hz):
    arranque a ±0.5 muestra nativa (el redondeo) y cada muestra a ±`TOL_PCM16` (16 bits).
    Compara las muestras, no la duración. Tolerancias explicadas en el docstring del módulo."""
    info = sf.info(str(exportado))
    assert info.subtype == "PCM_16", f"el fragmento es {info.subtype}, se decidió PCM_16"
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
        trozo = orig[s:s + datos.shape[0]]
        if trozo.shape == datos.shape and np.max(np.abs(datos - trozo)) <= TOL_PCM16:
            assert abs(s - esperado_ini) <= 0.5, (
                f"el fragmento arranca en la muestra {s}, se esperaba {esperado_ini:.1f} (±0.5)")
            return
    raise AssertionError(
        f"el fragmento no es el audio original desde la muestra {esperado_ini:.1f} (±1), "
        f"ni siquiera con la tolerancia de 16 bits ({TOL_PCM16:.2e})")


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
    combinadas, sin_par, repetidas = escucha.combinar_acuerdo(analisis, consenso)
    assert [(f["ruta"], f["key_est"], f["acuerdo"], f["tramos"]) for f in combinadas] == [
        ("D:/x/t.wav", "8A", "1/3", "6A|7A|1A"),
        ("D:/y/t.wav", "9A", "3/3", "5A|5A|5A"),
    ]
    assert sin_par == ["D:/z/solo.wav"]
    assert repetidas == []


def test_acuerdo_de_sin_par_exacto_no_toma_el_del_mismo_basename():
    """El par de `D:/x/t.wav` no le sirve a `D:/w/t.wav`, aunque sea el único `t.wav`."""
    analisis = [_fila_a("t.wav", "8A", "", carpeta="D:/w"),
                _fila_a("t.wav", "9A", "", carpeta="D:/x")]
    consenso = [_fila_a("t.wav", "5A", "3/3", "5A|5A|5A", carpeta="D:/x", metodo="tono_consenso")]
    combinadas, sin_par, _ = escucha.combinar_acuerdo(analisis, consenso)
    assert [(f["ruta"], f["key_est"], f["acuerdo"]) for f in combinadas] == [
        ("D:/x/t.wav", "9A", "3/3")], "una ruta sin par exacto tomó el acuerdo de otra carpeta"
    assert sin_par == ["D:/w/t.wav"]


def test_acuerdo_de_con_ruta_repetida_la_excluye_y_la_cuenta_sin_abortar():
    """Una ruta que en --acuerdo-de aparece dos veces (la sesión lo escribe legítimamente) no
    corta la escucha: esa ruta queda fuera, se reporta, y las demás se combinan igual. No se
    elige ninguna de las dos filas, aunque sean distintas."""
    analisis = [_fila_a("r.wav", "8A", "", carpeta="D:/m"),
                _fila_a("ok.wav", "9A", "", carpeta="D:/m")]
    consenso = [_fila_a("r.wav", "6A", "3/3", "6A|6A|6A", carpeta="D:/m", metodo="tono_consenso"),
                _fila_a("r.wav", "7A", "1/3", "7A|1A|2A", carpeta="D:/m", metodo="tono_consenso"),
                _fila_a("ok.wav", "5A", "3/3", "5A|5A|5A", carpeta="D:/m", metodo="tono_consenso")]
    combinadas, sin_par, repetidas = escucha.combinar_acuerdo(analisis, consenso)
    assert [(f["ruta"], f["key_est"], f["acuerdo"]) for f in combinadas] == [
        ("D:/m/ok.wav", "9A", "3/3")], f"la ruta repetida se combinó igual: {combinadas}"
    assert repetidas == ["D:/m/r.wav"], "la ruta repetida en --acuerdo-de no se reporta"
    assert sin_par == [], "una ruta repetida no es una ruta sin par"


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
    assert sf.info(str(salida / f["archivo_ventana_central"])).samplerate == 8000
    assert _sha(mono_largo) == antes

    md = (salida / escucha.INDICE_MD).read_text(encoding="utf-8")
    assert "| 5A (Cm) | 8B (C) | lejano | si | 3/3 |" in md
    # el track sin audio también tiene dónde anotar: justo el que más necesita una nota
    n_faltante = filas[str(faltante)]["n"]
    seccion = md.split(f"### {n_faltante}. a_faltante.wav", 1)[1].split("\n### ", 1)[0].splitlines()
    assert "- **audio no encontrado**: no se generaron fragmentos" in seccion, seccion
    assert "- veredicto (`motor` / `rekordbox` / `ninguna` / `no sé`): " in seccion, (
        f"el track con audio no encontrado no tiene línea de veredicto: {seccion}")
    assert "- notas: " in seccion, f"el track con audio no encontrado no tiene línea de notas: {seccion}"


def test_veredictos_van_en_el_md_y_el_csv_es_de_lectura(monkeypatch, tmp_path):
    c = escucha.Candidato(ruta="D:/m/t.wav", archivo="t.wav", nombre="Boltcore — Try",
                          key_motor="5A", key_rekordbox="8B", tonality="C",
                          acuerdo="3/3", tramos="5A|5A|5A")
    fr = escucha.Fragmentos(estado=escucha.OK, inicio_s=25.0, fin_s=115.0,
                            central="01_t_ventana_central.wav", tramos=["01_t_tramo1.wav"])
    filas = [escucha.fila_indice(1, c, fr)]

    escucha.escribir_indice_csv(filas, tmp_path / "e.csv")
    with (tmp_path / "e.csv").open(encoding="utf-8-sig", newline="") as fh:
        columnas = csv.DictReader(fh).fieldnames
    assert not {"veredicto", "notas"} & set(columnas), (
        f"el CSV de solo lectura trae columnas para completar: {columnas}")

    escucha.escribir_indice_md(filas, tmp_path / "e.md")
    md = (tmp_path / "e.md").read_text(encoding="utf-8")
    cabecera, tracks = md.split("## Tracks", 1)
    assert "Los veredictos se anotan en ESTE archivo" in cabecera
    assert "no lo abras y guardes con Excel" in cabecera and "`3/3` en una fecha" in cabecera
    assert "promedio de los canales" in cabecera and "contrafase" in cabecera
    plana = " ".join(cabecera.split())   # el texto va cortado en líneas del .md
    assert "se exporta en estéreo" not in plana, "afirma estéreo también para un original mono"
    assert "con los canales del archivo original: si es estéreo, lo que suene solo en contrafase" in plana
    assert "recortan los picos por encima de ±1.0" in plana and "suena saturado" in plana, (
        "la cabecera no avisa del recorte a ±1.0 del PCM 16 bits")
    # 90 s + 3 × 45 s, estéreo 16 bits a 44.1 kHz = 39.7 MB (cuenta hecha a mano)
    assert "unos 40 MB por track" in cabecera and "float" not in cabecera.lower()
    seccion = tracks.split("### 1. Boltcore — Try", 1)[1].splitlines()
    assert "- veredicto (`motor` / `rekordbox` / `ninguna` / `no sé`): " in seccion
    assert "- notas: " in seccion

    monkeypatch.setattr(escucha, "N_TRAMOS", 4)   # los nombres de tramo salen de N_TRAMOS
    escucha.escribir_indice_md(filas, tmp_path / "e4.md")
    md4 = (tmp_path / "e4.md").read_text(encoding="utf-8")
    assert "`NN_<nombre>_tramo1.wav`, `tramo2`, `tramo3`, `tramo4` — los 4 fragmentos" in md4, (
        "la lista de tramos no sale de N_TRAMOS")


def test_no_pisa_un_indice_existente(tmp_path):
    a = _escribir(tmp_path / "a.csv", COLS_A, [_fila_a("t.wav", "8A", "3/3")])
    g = _escribir(tmp_path / "gt.csv", COLS_GT, [_fila_gt(1, "t.wav", "5A")])
    salida = tmp_path / "escucha"
    salida.mkdir()
    (salida / escucha.INDICE_CSV).write_text("veredicto cargado", encoding="utf-8")
    with pytest.raises(escucha.EscuchaIncompleta):
        escucha.correr(a, g, salida)
    assert (salida / escucha.INDICE_CSV).read_text(encoding="utf-8") == "veredicto cargado"


def _un_candidato_sin_audio(tmp_path):
    """Un candidato válido cuyo audio no está: la corrida escribe índice sin cargar audio."""
    fila = {**_fila_a("t.wav", "8A", "3/3"), "ruta": str(tmp_path / "no_esta" / "t.wav")}
    a = _escribir(tmp_path / "a.csv", COLS_A, [fila])
    g = _escribir(tmp_path / "gt.csv", COLS_GT, [_fila_gt(1, "t.wav", "5A")])
    return a, g


def test_salida_con_cualquier_archivo_falla_sin_escribir(tmp_path):
    a, g = _un_candidato_sin_audio(tmp_path)
    salida = tmp_path / "musica"
    salida.mkdir()
    (salida / "01_t_ventana_central.wav").write_bytes(b"no pisar")
    with pytest.raises(escucha.EscuchaIncompleta) as e:
        escucha.correr(a, g, salida)
    assert "no existir o estar vacía" in str(e.value), f"mensaje: {e.value}"
    assert sorted(p.name for p in salida.iterdir()) == ["01_t_ventana_central.wav"], (
        "escribió en una --salida que no estaba vacía")
    assert (salida / "01_t_ventana_central.wav").read_bytes() == b"no pisar"


def test_salida_inexistente_o_vacia_se_usa(tmp_path):
    a, g = _un_candidato_sin_audio(tmp_path)
    vacia = tmp_path / "vacia"
    vacia.mkdir()
    for salida in (tmp_path / "nueva" / "escucha", vacia):
        res = escucha.correr(a, g, salida)
        assert res["elegidos"] == 1
        assert sorted(p.name for p in salida.iterdir()) == [escucha.INDICE_CSV, escucha.INDICE_MD]


def test_acuerdo_de_sin_ninguna_ruta_en_comun_falla(tmp_path):
    """Misma canción, otra grafía de ruta: sin par no hay acuerdo, y eso no es 'nada que oír'."""
    a = _escribir(tmp_path / "a.csv", COLS_A, [_fila_a("t.wav", "8A", "", carpeta="D:/musica")])
    c = _escribir(tmp_path / "c.csv", COLS_A, [_fila_a("t.wav", "8A", "3/3", carpeta="D:\\musica",
                                                        metodo="tono_consenso")])
    g = _escribir(tmp_path / "gt.csv", COLS_GT, [_fila_gt(1, "t.wav", "5A")])
    salida = tmp_path / "escucha"
    with pytest.raises(escucha.EscuchaIncompleta) as e:
        escucha.correr(a, g, salida, acuerdo_de=c)
    msg = str(e.value)
    assert "Ninguna ruta coincidió entre las dos pasadas" in msg, f"mensaje: {msg}"
    assert "sesiones distintas" in msg and "D:/musica/t.wav" in msg and "D:\\musica/t.wav" in msg
    assert not salida.exists()


_XML_REKORDBOX = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
 <COLLECTION Entries="{n}">
{tracks} </COLLECTION>
</DJ_PLAYLISTS>
"""


def test_ruta_repetida_en_analisis_se_excluye_y_se_cuenta(mono_largo, tmp_path, monkeypatch, capsys):
    """El caso REAL, por el flujo real de `ground_truth.sesion`: dos entradas del XML con el
    mismo nombre (una de una carpeta vieja, otra de una carpeta movida) y UN solo archivo en
    disco resuelven las dos a esa ruta (`resolver`, paso 3 + `_distintos`), y `etapa_a`
    escribe dos filas idénticas. La escucha no aborta: esa ruta queda fuera, se cuenta, y los
    otros tracks se procesan.

    El analizador se reemplaza por un doble (las keys son de utilería, spec §5): lo que se
    prueba es la ruta repetida, no la medición. Los audios de los otros dos tracks son reales
    para que la exportación corra de verdad (uno da `ok`, el otro dura < 1 s y no decodifica).

    Se corre dos veces: con el GT de la sesión (donde el nombre también está repetido y
    `cruzar` ya lo daría ambiguo) y con ese GT dejando UNA sola entrada del nombre. La segunda
    es la que detecta quedarse con una de las dos filas: con el GT real eso lo taparía el
    ambiguo del lado del GT.
    """
    from ground_truth import rekordbox, sesion

    disco = tmp_path / "disco"
    disco.mkdir()
    repetido = disco / "repetido.wav"
    repetido.write_bytes(b"un solo archivo en disco")
    otro = disco / "otro.wav"
    otro.write_bytes(mono_largo.read_bytes())
    corto = disco / "corto.wav"
    sf.write(str(corto), _ruido(0.5, 8000, 1, seed=7)[:, 0], 8000, subtype="FLOAT")

    ubicaciones = [(tmp_path / "carpeta_vieja" / "repetido.wav", "Am"),   # "missing file"
                   (tmp_path / "carpeta_movida" / "repetido.wav", "Am"),
                   (tmp_path / "otra_vieja" / "otro.wav", "Am"),
                   (tmp_path / "otra_vieja" / "corto.wav", "Em")]
    xml = tmp_path / "Rekordbox.xml"
    xml.write_text(_XML_REKORDBOX.format(n=len(ubicaciones), tracks="".join(
        f'  <TRACK TrackID="{i}" Name="T{i}" Artist="A" AverageBpm="128.0" Tonality="{ton}"'
        f' TotalTime="200" Kind="WAV File" Location="file://localhost/{p.as_posix()}"/>\n'
        for i, (p, ton) in enumerate(ubicaciones, 1))), encoding="utf-8")
    tracks = rekordbox.parsear(xml)
    gt_sesion, _ = rekordbox.escribir_csv(tracks, tmp_path / "gt_out")

    res = sesion.resolver_todo(tracks, [str(disco)])
    assert sorted(res["rutas"]) == sorted([str(repetido), str(repetido), str(otro), str(corto)]), (
        f"la resolución ya no da la ruta repetida: la premisa del test cambió: {res}")
    assert res["ambiguos"] == [] and res["no_encontrados"] == []

    def _doble(ruta, sr=22050, consenso=False):
        return FilaAnalisis(archivo=Path(ruta).name, ruta=ruta, duracion_s=200.0,
                            bpm_est=128.0, key_est="5A", confianza=0.7, acuerdo="3/3",
                            tramos="5A|5A|5A", t_carga_s=0.1, t_analisis_s=0.5,
                            t_total_s=0.6, metodo="tono")

    monkeypatch.setattr(sesion, "analizar_uno", _doble)
    monkeypatch.setattr("benchmark.tiempo_analisis.calentar", lambda sr=22050: 0.0)
    analisis_csv = tmp_path / "gt_out" / "analisis_sin-consenso.csv"
    sesion.etapa_a(sorted(res["rutas"]), False, analisis_csv, "sin-consenso")
    filas_csv = escucha.leer_csv(analisis_csv)
    dos = [f for f in filas_csv if f["ruta"] == str(repetido)]
    assert len(dos) == 2 and dos[0] == dos[1], f"etapa_a no escribió las dos filas idénticas: {dos}"
    capsys.readouterr()

    gt_una_entrada = tmp_path / "gt_una_entrada.csv"
    with gt_sesion.open(encoding="utf-8", newline="") as fh:
        filas_gt = list(csv.DictReader(fh))
    _escribir(gt_una_entrada, COLS_GT, [f for f in filas_gt if "carpeta_movida" not in f["location"]])

    for gt, salida in ((gt_sesion, tmp_path / "escucha_gt_sesion"),
                       (gt_una_entrada, tmp_path / "escucha_gt_una_entrada")):
        out = escucha.correr(analisis_csv, gt, salida, n=10)
        impreso = capsys.readouterr().out

        assert "1 rutas repetidas en --analisis (excluidas)" in impreso, (
            f"[{gt.name}] la exclusión no se reporta:\n{impreso}")
        assert out["conteo"]["rutas_repetidas"] == 1, f"[{gt.name}] conteo: {out['conteo']}"
        assert {f["ruta"]: f["estado"] for f in out["filas"]} == {
            str(otro): escucha.OK, str(corto): escucha.AUDIO_NO_DECODIFICA}, (
            f"[{gt.name}] los elegidos no son los otros dos tracks: {out['filas']}")
        assert (out["candidatos"], out["elegidos"]) == (2, 2), f"[{gt.name}] {out}"
        md = (salida / escucha.INDICE_MD).read_text(encoding="utf-8")
        assert str(repetido) not in md, f"[{gt.name}] la ruta repetida quedó en el índice"
        # elegidos ordenados por ruta: corto.wav es el 01 (no decodifica), otro.wav el 02
        assert sorted(p.name for p in salida.iterdir()) == sorted(
            [escucha.INDICE_CSV, escucha.INDICE_MD, "02_otro_ventana_central.wav",
             "02_otro_tramo1.wav", "02_otro_tramo2.wav", "02_otro_tramo3.wav"]), (
            f"[{gt.name}] lo exportado no es lo de los dos tracks válidos")

    # EL FLUJO DE CASA: los CSV existentes son anteriores a aabd83d. La pasada sin consenso
    # trae acuerdo vacío y la pasada con consenso de la MISMA sesión repite la misma ruta. Con
    # --acuerdo-de la escucha tiene que seguir igual, no abortar.
    def _doble_viejo(ruta, sr=22050, consenso=False):
        if consenso:
            return FilaAnalisis(archivo=Path(ruta).name, ruta=ruta, duracion_s=200.0,
                                bpm_est=128.0, key_est="1A", confianza=1.0, acuerdo="3/3",
                                tramos="1A|1A|1A", t_carga_s=0.1, t_analisis_s=0.5,
                                t_total_s=0.6, metodo="tono_consenso")
        return FilaAnalisis(archivo=Path(ruta).name, ruta=ruta, duracion_s=200.0,
                            bpm_est=128.0, key_est="5A", confianza=0.7, acuerdo="",
                            tramos="", t_carga_s=0.1, t_analisis_s=0.5, t_total_s=0.6,
                            metodo="tono")

    monkeypatch.setattr(sesion, "analizar_uno", _doble_viejo)
    viejo_sin = tmp_path / "viejo" / "analisis_sin-consenso.csv"
    viejo_con = tmp_path / "viejo" / "analisis_con-consenso.csv"
    sesion.etapa_a(sorted(res["rutas"]), False, viejo_sin, "sin-consenso")
    sesion.etapa_a(sorted(res["rutas"]), True, viejo_con, "con-consenso")
    assert sum(f["ruta"] == str(repetido) for f in escucha.leer_csv(viejo_con)) == 2, (
        "la pasada con consenso ya no repite la ruta: la premisa del test cambió")
    capsys.readouterr()

    salida_casa = tmp_path / "escucha_flujo_de_casa"
    out = escucha.correr(viejo_sin, gt_una_entrada, salida_casa, acuerdo_de=viejo_con, n=10)
    impreso = capsys.readouterr().out
    assert "1 rutas repetidas en --analisis (excluidas)" in impreso, impreso
    assert {f["ruta"]: f["estado"] for f in out["filas"]} == {
        str(otro): escucha.OK, str(corto): escucha.AUDIO_NO_DECODIFICA}, (
        f"con --acuerdo-de los elegidos no son los otros dos tracks: {out['filas']}")
    assert all(f["key_motor_camelot"] == "5A" for f in out["filas"]), (
        f"la key no salió de la pasada sin consenso: {out['filas']}")
    assert repetido.read_bytes() == b"un solo archivo en disco"


def test_sin_elegidos_no_crea_la_salida(tmp_path, capsys):
    """Con 0 elegidos no se escribe nada, tampoco la carpeta: una vacía confunde ("¿corrió?")
    y además haría fallar la próxima corrida si no se borra."""
    a = _escribir(tmp_path / "a.csv", COLS_A, [_fila_a("t.wav", "8A", "3/3")])
    g = _escribir(tmp_path / "gt.csv", COLS_GT, [_fila_gt(1, "t.wav", "8A")])   # misma key
    salida = tmp_path / "nueva" / "escucha"
    res = escucha.correr(a, g, salida)
    assert (res["candidatos"], res["elegidos"], res["conteo"]["misma_key"]) == (0, 0, 1), res
    assert "no se escribió ninguna salida" in capsys.readouterr().out
    assert not salida.exists() and not salida.parent.exists(), (
        "con 0 elegidos creó la carpeta de salida")
