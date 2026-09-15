"""El medidor del umbral de tiempo de §4 tiene que cronometrar lo mismo que corre el pipeline.

Desde el '?' de confianza, el pipeline corre `tono_consenso` en cada track además de
`bpm_refinado` y `tono`. Si esta herramienta no lo cronometrara, §4 se mediría sobre un
análisis más barato que el real y el umbral aprobaría algo que en la práctica cuesta más.

El audio sale de `motor.sintetico` (§5: nada inventado). Los análisis se reemplazan por
dobles con costos conocidos para que el test sea determinista y no dependa de la máquina.
"""
import time

import soundfile as sf

import benchmark.tiempo_analisis as ta
from motor.sintetico import click_track


def test_el_tiempo_de_analisis_incluye_el_consenso(tmp_path, monkeypatch):
    y, sr = click_track(128.0, dur=5.0, nota="A", modo="min", seed=3)
    ruta = tmp_path / "click.wav"
    sf.write(ruta, y, sr)

    def _dormir(segundos):
        def _f(*_a, **_k):
            time.sleep(segundos)
        return _f

    monkeypatch.setattr(ta, "bpm_refinado", _dormir(0.0))
    monkeypatch.setattr(ta, "tono", _dormir(0.0))
    monkeypatch.setattr(ta, "tono_consenso", _dormir(0.3))

    t = ta.medir(str(ruta))

    assert t.consenso_s >= 0.3, f"consenso_s {t.consenso_s:.3f}: el consenso no se cronometró"
    assert t.analisis_s >= 0.3, (
        f"analisis_s {t.analisis_s:.3f} no incluye el consenso ({t.consenso_s:.3f} s)")
    assert t.total_s == t.carga_s + t.bpm_s + t.tono_s + t.consenso_s
