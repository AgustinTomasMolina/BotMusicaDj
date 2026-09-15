"""Tests de la tabla de umbrales (spec §4) y del veredicto del benchmark.

Cubre (1) el contrato de la curva de energía YA CALIBRADO (tarea 14: 0.22 sobre la mediana
del desvío por-set) y (2) el mecanismo "a calibrar" de forma genérica, con un umbral
SINTÉTICO — así no queda atado a qué métrica real esté sin número en un momento dado.
"""
import json
from dataclasses import replace

import pytest

from benchmark import __main__ as runner
from benchmark import umbrales
from benchmark.umbrales import UMBRALES, Umbral, evaluar, veredicto

# Valores que cumplen TODOS los umbrales con número (construidos a mano contra la tabla).
TODO_OK = {
    "bpm_error_p95": 0.5, "tonalidad_exacta": 90.0, "tonalidad_compatible": 97.0,
    "transiciones_fuera_bpm": 0.0, "choques_armonicos": 5.0,
    "energia_desvio_curva": 0.18, "energia_spearman_ascendente": 0.8,
    "tiempo_analisis_s": 4.0, "latencia_radio_ms": 50.0,
}

# Umbral sintético "a calibrar", para probar el mecanismo sin depender de cuál métrica real
# esté sin número. Se inyecta con monkeypatch donde hace falta.
_FUTURO = Umbral("metrica_futura", "Métrica futura", None, "<=", calibrar="prueba")


def _con_futuro(monkeypatch):
    monkeypatch.setattr(umbrales, "UMBRALES", [*UMBRALES, _FUTURO])


def test_la_curva_de_energia_esta_calibrada():
    por = {u.clave: u for u in UMBRALES}
    assert "energia_spearman" not in por, "el Spearman plano sigue siendo contrato"
    assert por["energia_spearman_ascendente"].limite == 0.5
    d = por["energia_desvio_curva"]
    assert not d.a_calibrar and d.limite == 0.22 and d.op == "<="
    assert d.objetivo() == "<= 0.22"


def test_la_curva_de_energia_bloquea():
    assert veredicto(evaluar({**TODO_OK, "energia_desvio_curva": 0.22})) == 0   # borde: pasa
    assert veredicto(evaluar({**TODO_OK, "energia_desvio_curva": 0.23})) == 1   # se pasa: rompe
    sin = {k: v for k, v in TODO_OK.items() if k != "energia_desvio_curva"}
    assert veredicto(evaluar(sin)) == 2, "calibrado y sin medir tiene que faltar"


def test_a_calibrar_no_cambia_el_veredicto_y_su_valor_se_reporta(monkeypatch):
    _con_futuro(monkeypatch)
    filas = evaluar({**TODO_OK, "metrica_futura": 999.0})
    assert veredicto(filas) == 0
    fila = next(f for f in filas if f.umbral.clave == "metrica_futura")
    assert fila.valor == 999.0 and fila.ok is None


def test_a_calibrar_sin_valor_no_cuenta_como_falta_medir(monkeypatch):
    _con_futuro(monkeypatch)
    assert veredicto(evaluar(TODO_OK)) == 0   # metrica_futura ausente pero a-calibrar → no falta


def test_los_umbrales_con_numero_siguen_bloqueando():
    assert veredicto(evaluar({**TODO_OK, "energia_spearman_ascendente": 0.49})) == 1
    sin_tiempo = {k: v for k, v in TODO_OK.items() if k != "tiempo_analisis_s"}
    assert veredicto(evaluar(sin_tiempo)) == 2


def test_al_calibrar_un_umbral_pasa_a_bloquear(monkeypatch):
    """Cambiar None → número (sin `calibrar`) basta para que decida."""
    calibrada = [*UMBRALES, replace(_FUTURO, limite=0.15, calibrar="")]
    monkeypatch.setattr(umbrales, "UMBRALES", calibrada)
    assert veredicto(evaluar({**TODO_OK, "metrica_futura": 0.20})) == 1
    assert veredicto(evaluar({**TODO_OK, "metrica_futura": 0.10})) == 0
    assert veredicto(evaluar(TODO_OK)) == 2, "calibrado y sin medir tiene que faltar"


def test_umbral_inconsistente_es_error():
    with pytest.raises(ValueError, match="dónde se calibra"):
        Umbral("x", "X", None, "<=")
    with pytest.raises(ValueError, match="calibrado a medias"):
        Umbral("x", "X", 0.15, "<=", calibrar="tarea 14")


def test_runner_marca_a_calibrar_y_sale_0(tmp_path, capsys, monkeypatch):
    _con_futuro(monkeypatch)
    ruta = tmp_path / "m.json"
    ruta.write_text(json.dumps({**TODO_OK, "metrica_futura": 0.123}), encoding="utf-8")
    code = runner.main(["--metricas", str(ruta)])
    out = capsys.readouterr().out
    assert code == 0, out
    fila = next(linea for linea in out.splitlines() if "Métrica futura" in linea)
    assert fila.lstrip().startswith("[~]"), fila
    assert "a calibrar (prueba)" in fila and "medido: 0.123" in fila, fila
