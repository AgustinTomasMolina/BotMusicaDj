"""Tests del medidor del `?` de tonalidad (tarea 17.1).

Las keys de los casos con audio salen del generador sintético (`motor.sintetico`). En los
tests de resumen las keys son etiquetas construidas a mano para ejercitar el conteo: no se
afirma la key de ningún track real (spec §5).
"""
import csv
import sqlite3

import numpy as np
import pytest

from benchmark.signo_tonalidad import (
    AVISO_SIN_GT,
    COINCIDE,
    DISTINTA,
    NO_UNANIME,
    Medicion,
    desde_db,
    desde_senal,
    duda_actual,
    duda_propuesta,
    filas_salida,
    grupo_de,
    informe,
    main,
    resumen,
)
from motor.sintetico import click_track
from motor.tonalidad import _CAMELOT

SR = 22050


def _seg(dur, nota, modo, seed):
    return click_track(128.0, dur=dur, sr=SR, nota=nota, modo=modo, seed=seed)[0]


# --- con audio sintético ---------------------------------------------------------------


def test_tramos_unanimes_que_coinciden_con_la_key_mostrada():
    y = _seg(150, "A", "min", 1)
    m = desde_senal("a.wav", "a.wav", y, SR)
    esperada = _CAMELOT[("A", "min")]
    assert m.key_mostrada == esperada
    assert m.tramos == "|".join([esperada] * 3)
    assert grupo_de(m) == COINCIDE
    assert not duda_actual(m) and not duda_propuesta(m)


def test_tramos_unanimes_en_otra_key_que_la_mostrada():
    """300 s: los tramos caen en [27.5,72.5], [127.5,172.5] y [227.5,272.5]; la ventana
    central de tono() en [105,195]. Fuera de los tramos pero dentro de la ventana central va
    C# mayor; el tramo del medio es La menor con C# mayor de fondo. Los tres tramos votan La
    menor y tono() ve C# mayor: el caso que hoy sale sin `?`."""
    medio = _seg(45, "A", "min", 3) + 0.5 * _seg(45, "C#", "maj", 6)
    y = np.concatenate([_seg(105, "A", "min", 1), _seg(22.5, "C#", "maj", 2), medio,
                        _seg(22.5, "C#", "maj", 4), _seg(105, "A", "min", 5)])
    m = desde_senal("b.wav", "b.wav", y, SR)
    tramo = _CAMELOT[("A", "min")]
    assert m.tramos == "|".join([tramo] * 3)
    assert m.acuerdo == "3/3"
    assert m.key_mostrada == _CAMELOT[("C#", "maj")]
    assert grupo_de(m) == DISTINTA
    assert not duda_actual(m), "hoy este track sale SIN ?: es lo que se quiere medir"
    assert duda_propuesta(m)


# --- grupos y resumen ------------------------------------------------------------------

def _m(archivo, key, acuerdo, tramos):
    return Medicion(archivo=archivo, ruta=f"D:/m/{archivo}", key_mostrada=key,
                    acuerdo=acuerdo, tramos=tramos)


MEDS = [
    _m("c1.wav", "8A", "3/3", "8A|8A|8A"),      # coincide, acierta
    _m("c2.wav", "8A", "3/3", "8A|8A|8A"),      # coincide, falla
    _m("d1.wav", "8A", "3/3", "5B|5B|5B"),      # distinta, falla
    _m("d2.wav", "8A", "3/3", "9A|9A|9A"),      # distinta, falla exacta (compatible sí)
    _m("n1.wav", "8A", "2/3", "8A|8A|1A"),      # no unánime, acierta
    _m("n2.wav", "8A", "", ""),                 # sin tramos
]

GT = [{"location": f"C:/crate/{a}", "camelot": k, "bpm": "0", "artist": "", "name": ""}
      for a, k in (("c1.wav", "8A"), ("c2.wav", "3B"), ("d1.wav", "2A"),
                   ("d2.wav", "9A"), ("n1.wav", "8A"), ("n2.wav", "4B"))]


def test_resumen_cuenta_los_grupos():
    r = resumen(filas_salida(MEDS, None))
    assert r["conteo"] == {COINCIDE: 2, DISTINTA: 2, NO_UNANIME: 2}
    assert not r["con_gt"]
    assert AVISO_SIN_GT in informe(r)


def test_resumen_con_ground_truth_simulado():
    r = resumen(filas_salida(MEDS, GT))
    assert r["n_ref"] == 6
    assert r["por_grupo"][COINCIDE] == {"n": 2, "exacta": 50.0, "compatible": 50.0}
    assert r["por_grupo"][DISTINTA] == {"n": 2, "exacta": 0.0, "compatible": 50.0}
    assert r["por_grupo"][NO_UNANIME] == {"n": 2, "exacta": 50.0, "compatible": 50.0}
    # Actual: sin ? = c1, c2, d1, d2 → 1/4 exacta; propuesto: sin ? = c1, c2 → 1/2.
    assert r["actual"]["sin_duda"] == {"n": 4, "exacta": 25.0, "compatible": 50.0}
    assert r["propuesta"]["sin_duda"] == {"n": 2, "exacta": 50.0, "compatible": 50.0}
    assert r["propuesta"]["con_duda"]["n"] == 4
    assert AVISO_SIN_GT not in informe(r)


def test_acuerdo_roto_no_se_esconde_en_un_grupo():
    with pytest.raises(ValueError):
        grupo_de(_m("x.wav", "8A", "tres", "8A|8A|8A"))


def test_db_en_solo_lectura_y_csv_con_punto_y_coma(tmp_path, capsys):
    db = tmp_path / "b.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tracks (path TEXT, key TEXT, key_acuerdo TEXT, key_tramos TEXT)")
    con.executemany("INSERT INTO tracks VALUES (?,?,?,?)",
                    [(f"D:/m/{m.archivo}", m.key_mostrada, m.acuerdo or None, m.tramos or None)
                     for m in MEDS] + [("D:/m/corto.wav", "8A", "0/0", "")])
    con.commit()
    con.close()
    antes = db.read_bytes()

    leidas = {m.archivo: m for m in desde_db(db)}
    assert leidas["corto.wav"].acuerdo == ""            # "0/0" → sin acuerdo, como la etapa A
    assert leidas["d1.wav"].tramos == "5B|5B|5B"
    assert main(["--db", str(db), "--out", str(tmp_path), "--limit", "3"]) == 0
    assert db.read_bytes() == antes
    salida = next(tmp_path.glob("signo_tonalidad_*.csv"))
    with salida.open(encoding="utf-8") as fh:
        filas = list(csv.DictReader(fh, delimiter=";"))
    assert len(filas) == 3 and "grupo" in filas[0]
    assert AVISO_SIN_GT in capsys.readouterr().out
