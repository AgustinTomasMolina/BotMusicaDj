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


# --- el mensaje final no miente con umbrales a calibrar ----------------------------------


def _ultima_linea(out: str) -> str:
    return [linea for linea in out.splitlines() if linea.strip()][-1]


def test_runner_con_a_calibrar_no_dice_que_todos_cumplen(tmp_path, capsys, monkeypatch):
    _con_futuro(monkeypatch)
    ruta = tmp_path / "m.json"
    ruta.write_text(json.dumps(TODO_OK), encoding="utf-8")
    code = runner.main(["--metricas", str(ruta)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert _ultima_linea(out) == "✅ Los umbrales con número cumplen (1 a calibrar: Métrica futura).", out


def test_runner_sin_a_calibrar_dice_que_todos_cumplen(tmp_path, capsys):
    ruta = tmp_path / "m.json"
    ruta.write_text(json.dumps(TODO_OK), encoding="utf-8")
    code = runner.main(["--metricas", str(ruta)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert _ultima_linea(out) == "✅ Todos los umbrales cumplen.", out


# --- nan: sin medir, no roto ------------------------------------------------------------


def test_nan_en_una_metrica_con_numero_es_sin_medir_no_roto():
    """`ascending_spearman` da nan con la curva flat: no hay tramo que suba. Eso no rompe el
    ≥ 0.5; queda sin medir (y el veredicto es 2, 'falta medir', no 1)."""
    filas = {f.umbral.clave: f for f in evaluar({**TODO_OK, "energia_spearman_ascendente": float("nan")})}
    fila = filas["energia_spearman_ascendente"]
    assert fila.ok is None, f"un nan quedó con veredicto {fila.ok}"
    assert veredicto(list(filas.values())) == 2


def test_nan_no_esconde_un_umbral_roto_en_otra_fila():
    filas = evaluar({**TODO_OK, "energia_spearman_ascendente": float("nan"), "bpm_error_p95": 1.5})
    assert veredicto(filas) == 1


def test_runner_imprime_el_nan_como_no_definido(tmp_path, capsys):
    ruta = tmp_path / "m.json"
    # json.dumps escribe NaN (no es JSON estricto, pero json.loads lo lee): el caso real.
    ruta.write_text(json.dumps({**TODO_OK, "energia_spearman_ascendente": float("nan")}),
                    encoding="utf-8")
    code = runner.main(["--metricas", str(ruta)])
    out = capsys.readouterr().out
    assert code == 2, out
    fila = next(linea for linea in out.splitlines() if "Spearman ↑" in linea)
    assert fila.lstrip().startswith("[·]"), fila
    assert fila.endswith("medido: no definido (nan) — sin medir"), fila


# --- cobertura del subconjunto tonal en el runner ----------------------------------------


def _filas_tonalidad(out: str) -> list[str]:
    return [linea for linea in out.splitlines() if "Tonalidad" in linea and "(unánimes)" in linea]


def test_runner_imprime_la_cobertura_al_lado_de_la_tonalidad(tmp_path, capsys):
    """El escenario de la auditoría: 100% medido sobre 1 track unánime de 200. Pasa (exit 0,
    no hay umbral de cobertura), pero la cobertura se ve en las dos filas de tonalidad."""
    ruta = tmp_path / "m.json"
    ruta.write_text(json.dumps({**TODO_OK, "tonalidad_exacta": 100.0, "tonalidad_compatible": 100.0,
                                "cobertura_unanimes": 0.5, "n_key_unanimes": 1, "n_key": 200}),
                    encoding="utf-8")
    code = runner.main(["--metricas", str(ruta)])
    out = capsys.readouterr().out
    assert code == 0, "la cobertura no tiene umbral: no cambia el exit code\n" + out
    filas = _filas_tonalidad(out)
    assert len(filas) == 2, out
    for fila in filas:
        assert fila.endswith("medido: 100.0 %  · cobertura 1/200 (0.5%)"), fila


def test_runner_acepta_la_forma_de_evaluar_metricas(tmp_path, capsys):
    """{"umbrales": ..., "extra": ...} es lo que devuelve `benchmark.evaluar.metricas()`."""
    doc = {"umbrales": {**TODO_OK, "tonalidad_exacta": 90.0},
           "extra": {"cobertura_unanimes": 400 / 6, "n_key_unanimes": 4, "n_key": 6,
                     "tonalidad_exacta_global": 33.3}}
    ruta = tmp_path / "m.json"
    ruta.write_text(json.dumps(doc), encoding="utf-8")
    code = runner.main(["--metricas", str(ruta)])
    out = capsys.readouterr().out
    assert code == 0, out
    exacta = next(f for f in _filas_tonalidad(out) if "exacta" in f)
    assert exacta.endswith("medido: 90.0 %  · cobertura 4/6 (66.7%)"), exacta


def test_runner_sin_cobertura_lo_dice(tmp_path, capsys):
    ruta = tmp_path / "m.json"
    ruta.write_text(json.dumps(TODO_OK), encoding="utf-8")
    code = runner.main(["--metricas", str(ruta)])
    out = capsys.readouterr().out
    assert code == 0, out
    filas = _filas_tonalidad(out)
    assert len(filas) == 2, out
    for fila in filas:
        assert fila.endswith("· cobertura no informada"), fila


def test_runner_sin_tonalidad_medida_no_habla_de_cobertura(tmp_path, capsys):
    sin_tono = {k: v for k, v in TODO_OK.items() if not k.startswith("tonalidad_")}
    ruta = tmp_path / "m.json"
    ruta.write_text(json.dumps(sin_tono), encoding="utf-8")
    assert runner.main(["--metricas", str(ruta)]) == 2
    for fila in _filas_tonalidad(capsys.readouterr().out):
        assert fila.endswith("medido: — sin medir"), fila
