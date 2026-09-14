"""Tests de la tabla de umbrales (spec §4) y del veredicto del benchmark.

Foco: los umbrales "a calibrar" (límite sin número todavía). Se reportan, no deciden el
veredicto, y cuando se calibran pasan a bloquear como cualquier otro.
"""
import json
from dataclasses import replace

import pytest

from benchmark import __main__ as runner
from benchmark import umbrales
from benchmark.umbrales import UMBRALES, Umbral, evaluar, veredicto

# Valores que cumplen todos los umbrales con número (construidos a mano contra la tabla).
TODO_OK = {
    "bpm_error_p95": 0.5, "tonalidad_exacta": 90.0, "tonalidad_compatible": 97.0,
    "transiciones_fuera_bpm": 0.0, "choques_armonicos": 5.0,
    "energia_spearman_ascendente": 0.8, "tiempo_analisis_s": 4.0, "latencia_radio_ms": 50.0,
}


def test_la_tabla_tiene_el_contrato_nuevo_de_la_curva():
    por = {u.clave: u for u in UMBRALES}
    assert "energia_spearman" not in por, "el Spearman plano sigue siendo contrato"
    assert por["energia_spearman_ascendente"].limite == 0.5
    assert por["energia_spearman_ascendente"].op == ">="
    desvio = por["energia_desvio_curva"]
    assert desvio.a_calibrar and desvio.calibrar == "tarea 14"
    assert desvio.objetivo() == "a calibrar (tarea 14)"


def test_a_calibrar_no_cambia_el_veredicto_y_su_valor_se_reporta():
    filas = evaluar({**TODO_OK, "energia_desvio_curva": 0.42})
    assert veredicto(filas) == 0
    fila = next(f for f in filas if f.umbral.clave == "energia_desvio_curva")
    assert fila.valor == 0.42 and fila.ok is None


def test_a_calibrar_sin_valor_tampoco_cuenta_como_falta_medir():
    assert "energia_desvio_curva" not in TODO_OK
    assert veredicto(evaluar(TODO_OK)) == 0


def test_los_umbrales_con_numero_siguen_bloqueando():
    assert veredicto(evaluar({**TODO_OK, "energia_spearman_ascendente": 0.49})) == 1
    sin_tiempo = {k: v for k, v in TODO_OK.items() if k != "tiempo_analisis_s"}
    assert veredicto(evaluar(sin_tiempo)) == 2


def test_al_calibrarlo_pasa_a_bloquear(monkeypatch):
    """Cambiar UNA línea (None → número, sin `calibrar`) basta para que decida."""
    calibrada = [replace(u, limite=0.15, calibrar="") if u.clave == "energia_desvio_curva" else u
                 for u in UMBRALES]
    monkeypatch.setattr(umbrales, "UMBRALES", calibrada)
    assert veredicto(evaluar({**TODO_OK, "energia_desvio_curva": 0.20})) == 1
    assert veredicto(evaluar({**TODO_OK, "energia_desvio_curva": 0.10})) == 0
    assert veredicto(evaluar(TODO_OK)) == 2, "calibrado y sin medir tiene que faltar"


def test_umbral_inconsistente_es_error():
    with pytest.raises(ValueError, match="dónde se calibra"):
        Umbral("x", "X", None, "<=")
    with pytest.raises(ValueError, match="calibrado a medias"):
        Umbral("x", "X", 0.15, "<=", calibrar="tarea 14")


def test_runner_imprime_a_calibrar_y_sale_0(tmp_path, capsys):
    ruta = tmp_path / "m.json"
    ruta.write_text(json.dumps({**TODO_OK, "energia_desvio_curva": 0.123}), encoding="utf-8")
    code = runner.main(["--metricas", str(ruta)])
    out = capsys.readouterr().out
    assert code == 0, out
    fila = next(linea for linea in out.splitlines() if "Curva de energía (desvío medio)" in linea)
    assert fila.lstrip().startswith("[~]"), fila
    assert "a calibrar (tarea 14)" in fila and "medido: 0.123" in fila, fila
