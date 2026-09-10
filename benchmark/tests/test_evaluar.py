"""Tests de la etapa B (evaluar): cruce, métricas y calibración.

Toda la etapa B es CSV → números, así que se prueba entera sin un solo archivo de audio.
Los BPM y tonalidades de los CSV de ejemplo son valores construidos a mano para ejercitar
cada caso (no se afirma el BPM de ningún track real, que es lo que prohíbe la spec §5).
"""
import csv

import pytest

from benchmark.evaluar import (
    UMBRAL_BPM,
    calibracion,
    cruzar,
    leer_csv,
    metricas,
)

# --- CSVs de ejemplo -------------------------------------------------------------------

COLS_A = ["archivo", "ruta", "duracion_s", "bpm_est", "key_est", "confianza",
          "acuerdo", "tramos", "t_carga_s", "t_analisis_s", "t_total_s", "metodo"]

COLS_GT = ["track_id", "artist", "name", "bpm", "tonality", "camelot",
           "genre", "duration_s", "kind", "num_cues", "location"]


def _fila_a(archivo, bpm, key, conf=1.0, acuerdo="3/3", tramos="", t=3.0):
    return {"archivo": archivo, "ruta": f"D:/musica/{archivo}", "duracion_s": "240.0",
            "bpm_est": str(bpm), "key_est": key, "confianza": str(conf),
            "acuerdo": acuerdo, "tramos": tramos, "t_carga_s": "1.0",
            "t_analisis_s": str(round(t - 1.0, 2)), "t_total_s": str(t),
            "metodo": "tono_consenso"}


def _fila_gt(track_id, archivo, bpm, camelot, artist="X", name="Y"):
    return {"track_id": str(track_id), "artist": artist, "name": name, "bpm": str(bpm),
            "tonality": "", "camelot": camelot, "genre": "", "duration_s": "240",
            "kind": "WAV File", "num_cues": "0",
            "location": f"C:/Users/dj/Escritorio/CRATE/{archivo}"}


def _escribir(tmp_path, nombre, cols, filas):
    p = tmp_path / nombre
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(filas)
    return p


# --- Cruce -----------------------------------------------------------------------------


def test_cruza_por_basename_ignorando_la_ruta():
    """El análisis tiene rutas de D:\\ y el GT rutas viejas del XML: cruza por nombre."""
    a = [_fila_a("track.wav", 128.0, "8A")]
    g = [_fila_gt(1, "track.wav", 128.0, "8A")]
    res = cruzar(a, g)
    assert len(res["cruces"]) == 1
    assert res["cruces"][0].archivo == "track.wav"


def test_cruce_no_distingue_mayusculas():
    res = cruzar([_fila_a("Track.WAV", 128.0, "8A")], [_fila_gt(1, "track.wav", 128.0, "8A")])
    assert len(res["cruces"]) == 1


def test_homonimos_quedan_fuera_y_se_cuentan():
    """Original vs edit con el mismo nombre: no se elige uno, se excluye."""
    g = [_fila_gt(1, "track.wav", 128.0, "8A"), _fila_gt(2, "track.wav", 150.0, "5A")]
    res = cruzar([_fila_a("track.wav", 128.0, "8A")], g)
    assert res["cruces"] == []
    assert res["ambiguos"] == ["track.wav"]


def test_cuenta_los_que_faltan_de_cada_lado():
    a = [_fila_a("solo_audio.wav", 128.0, "8A"), _fila_a("comun.wav", 128.0, "8A")]
    g = [_fila_gt(1, "comun.wav", 128.0, "8A"), _fila_gt(2, "solo_gt.wav", 130.0, "9A")]
    res = cruzar(a, g)
    assert len(res["cruces"]) == 1
    assert res["solo_analisis"] == ["solo_audio.wav"]
    assert res["solo_gt"] == ["solo_gt.wav"]


# --- BPM: los dos errores ---------------------------------------------------------------


def test_half_time_pasa_el_umbral_pero_deja_el_crudo_grande():
    """El contrato §4 tolera la octava; el crudo es el que delata que difieren."""
    res = cruzar([_fila_a("t.wav", 152.0, "8A")], [_fila_gt(1, "t.wav", 76.0, "8A")])
    c = res["cruces"][0]
    assert c.err_octava == 0.0 and c.veredicto_bpm == "OK"
    assert c.err_crudo == 76.0
    assert c.clase_bpm == "octava"


def test_error_chico_real_rompe_el_umbral():
    res = cruzar([_fila_a("t.wav", 153.2, "8A")], [_fila_gt(1, "t.wav", 152.0, "8A")])
    c = res["cruces"][0]
    assert c.veredicto_bpm == "ROTO"
    assert c.err_crudo == c.err_octava == 1.2
    assert c.clase_bpm == "error-genuino"


def test_los_dos_p95_se_reportan_por_separado():
    """Con puros half-time el tolerante da 0 y el crudo enorme: son métricas distintas."""
    a = [_fila_a(f"t{i}.wav", 152.0, "8A") for i in range(5)]
    g = [_fila_gt(i, f"t{i}.wav", 76.0, "8A") for i in range(5)]
    e = metricas(cruzar(a, g)["cruces"])["extra"]
    assert e["bpm_p95_tolerante"] == 0.0
    assert e["bpm_p95_crudo"] == 76.0


def test_umbral_bpm_sale_de_la_tabla_de_la_spec():
    assert UMBRAL_BPM == 1.0


# --- Tonalidad --------------------------------------------------------------------------


def test_exacta_y_compatible():
    a = [_fila_a("a.wav", 128.0, "8A"), _fila_a("b.wav", 128.0, "9A"),
         _fila_a("c.wav", 128.0, "3B")]
    g = [_fila_gt(1, "a.wav", 128.0, "8A"),   # exacta
         _fila_gt(2, "b.wav", 128.0, "8A"),   # vecina → compatible pero no exacta
         _fila_gt(3, "c.wav", 128.0, "8A")]   # lejana → ni exacta ni compatible
    cruces = cruzar(a, g)["cruces"]
    por = {c.archivo: c for c in cruces}
    assert por["a.wav"].key_exacta == "si" and por["a.wav"].key_compatible == "si"
    assert por["b.wav"].key_exacta == "no" and por["b.wav"].key_compatible == "si"
    assert por["c.wav"].key_exacta == "no" and por["c.wav"].key_compatible == "no"

    m = metricas(cruces)["umbrales"]
    assert m["tonalidad_exacta"] == pytest.approx(100 / 3)
    assert m["tonalidad_compatible"] == pytest.approx(200 / 3)


def test_sin_camelot_en_el_gt_no_cuenta_como_fallo():
    """Rekordbox deja tracks sin tonalidad; no deben contar como error."""
    cruces = cruzar([_fila_a("t.wav", 128.0, "8A")], [_fila_gt(1, "t.wav", 128.0, "")])["cruces"]
    assert cruces[0].key_exacta == "sin-referencia"
    assert "tonalidad_exacta" not in metricas(cruces)["umbrales"]


# --- Tiempo -----------------------------------------------------------------------------


def test_cuenta_los_que_pasan_el_umbral_de_tiempo():
    a = [_fila_a("a.wav", 128.0, "8A", t=3.0), _fila_a("b.wav", 128.0, "8A", t=12.0)]
    g = [_fila_gt(1, "a.wav", 128.0, "8A"), _fila_gt(2, "b.wav", 128.0, "8A")]
    e = metricas(cruzar(a, g)["cruces"])["extra"]
    assert e["sobre_umbral_tiempo"] == 1
    assert e["n_tiempo"] == 2


# --- Calibración ------------------------------------------------------------------------


def test_calibracion_detecta_confianza_que_predice():
    """Confianza 1.0 siempre acierta y 0.33 siempre falla → Pearson fuerte y positivo."""
    a, g = [], []
    for i in range(6):
        ok = i < 3
        a.append(_fila_a(f"t{i}.wav", 128.0, "8A" if ok else "3B",
                         conf=1.0 if ok else 0.333, acuerdo="3/3" if ok else "1/3"))
        g.append(_fila_gt(i, f"t{i}.wav", 128.0, "8A"))
    cal = calibracion(cruzar(a, g)["cruces"])
    assert cal["n"] == 6
    assert cal["pearson"] > 0.9
    tramos = {t[0]: t[2] for t in cal["tramos"]}
    assert tramos["1.00 — los 3 coinciden"] == 100.0
    assert tramos["0.33 — los 3 tramos distintos"] == 0.0


def test_calibracion_detecta_confianza_que_no_dice_nada():
    """Aciertos y errores repartidos por igual en cada nivel → Pearson ~0."""
    a, g = [], []
    for i in range(8):
        conf = 1.0 if i % 2 == 0 else 0.333
        acierta = i % 4 < 2
        a.append(_fila_a(f"t{i}.wav", 128.0, "8A" if acierta else "3B", conf=conf))
        g.append(_fila_gt(i, f"t{i}.wav", 128.0, "8A"))
    cal = calibracion(cruzar(a, g)["cruces"])
    assert abs(cal["pearson"]) < 0.3


def test_calibracion_vacia_si_no_hay_referencia():
    cal = calibracion(cruzar([_fila_a("t.wav", 128.0, "8A")],
                             [_fila_gt(1, "t.wav", 128.0, "")])["cruces"])
    assert cal["n"] == 0 and cal["pearson"] is None


# --- Ida y vuelta por disco --------------------------------------------------------------


def test_lee_los_csv_de_disco_y_evalua(tmp_path):
    """El camino real: dos archivos CSV en disco, sin audio de por medio."""
    pa = _escribir(tmp_path, "analisis.csv", COLS_A,
                   [_fila_a("uno.wav", 128.0, "8A"), _fila_a("dos.wav", 76.0, "9A")])
    pg = _escribir(tmp_path, "gt.csv", COLS_GT,
                   [_fila_gt(1, "uno.wav", 128.0, "8A"), _fila_gt(2, "dos.wav", 152.0, "9A")])
    res = cruzar(leer_csv(pa), leer_csv(pg))
    assert len(res["cruces"]) == 2
    m = metricas(res["cruces"])
    assert m["umbrales"]["tonalidad_exacta"] == 100.0
    assert m["extra"]["bpm_p95_tolerante"] == 0.0     # el 76 vs 152 es octava
    assert m["extra"]["bpm_p95_crudo"] > 70.0
