"""El medidor del umbral de tiempo de §4 tiene que cronometrar el SCAN REAL, no un subconjunto.

El scan real (`motor.analisis.analizar_senal`) no es solo BPM y tonalidad: también corre la
energía, el `embedding` (MFCC/tonnetz/contraste) y los `onsets`. Cuando este medidor solo
cronometraba BPM + tono + consenso, §4 se aprobaba sobre un análisis más barato que el real
—la energía, el embedding y los onsets no se contaban—. Estos tests fijan que ahora sí se
cuentan: se reemplaza un paso real por un doble con costo conocido y se exige que ese costo
aparezca en `analisis_s` (y en el desglose).

El audio sale de `motor.sintetico` (§5: nada inventado). Los pasos se reemplazan por dobles
deterministas para no depender de la máquina.
"""
import time

import numpy as np
import soundfile as sf

import benchmark.tiempo_analisis as ta
from motor import analisis as A
from motor.sintetico import click_track


def _wav(tmp_path):
    y, sr = click_track(128.0, dur=5.0, nota="A", modo="min", seed=3)
    ruta = tmp_path / "click.wav"
    sf.write(ruta, y, sr)
    return str(ruta)


def test_el_scan_real_cronometra_el_embedding(tmp_path, monkeypatch):
    """El embedding es uno de los pasos que el medidor viejo NO contaba. Si no se cuenta, el
    número de §4 miente hacia abajo."""
    ruta = _wav(tmp_path)

    def _embed_lento(*_a, **_k):
        time.sleep(0.3)
        return np.zeros(4)

    monkeypatch.setattr(A, "embed", _embed_lento)

    t = ta.medir(ruta)

    assert t.pasos["embed"] >= 0.3, f"el embedding no se cronometró: {t.pasos.get('embed')}"
    assert t.analisis_s >= 0.3, (
        f"analisis_s {t.analisis_s:.3f} no incluye el embedding ({t.pasos.get('embed')} s)")


def test_el_scan_real_cronometra_el_consenso(tmp_path, monkeypatch):
    """Desde el '?' de confianza el scan corre `tono_consenso` en cada track; tiene que contar."""
    ruta = _wav(tmp_path)

    def _consenso_lento(*_a, **_k):
        time.sleep(0.3)
        return {"acuerdo": (2, 3), "tramos": ["8A", "3B", "9A"]}

    monkeypatch.setattr(A, "tono_consenso", _consenso_lento)

    t = ta.medir(ruta)

    assert t.pasos["tono_consenso"] >= 0.3, f"el consenso no se cronometró: {t.pasos}"
    assert t.analisis_s >= 0.3, f"analisis_s {t.analisis_s:.3f} no incluye el consenso"


def test_el_scan_real_cronometra_los_onsets(tmp_path, monkeypatch):
    """Los onsets sobre la señal completa también eran invisibles en el medidor viejo."""
    ruta = _wav(tmp_path)

    def _onsets_lento(*_a, **_k):
        time.sleep(0.3)
        return 1.0

    monkeypatch.setattr(A, "onsets_por_segundo", _onsets_lento)

    t = ta.medir(ruta)

    assert t.pasos["onsets_por_segundo"] >= 0.3, "los onsets no se cronometraron"
    assert t.analisis_s >= 0.3


def test_el_desglose_cubre_los_pasos_del_scan_real(tmp_path):
    """Un scan real deja los seis pasos en el desglose, y su suma no puede exceder el total
    (los pasos son sub-tramos de la misma llamada: el total es autoritativo)."""
    ruta = _wav(tmp_path)

    t = ta.medir(ruta)

    assert set(t.pasos) == set(ta._PASOS), f"el desglose no cubre los pasos: {set(t.pasos)}"
    assert all(v >= 0 for v in t.pasos.values())
    assert sum(t.pasos.values()) <= t.analisis_s + 1e-3, (
        f"la suma de pasos {sum(t.pasos.values()):.3f} excede el total {t.analisis_s:.3f}")


def test_total_es_carga_mas_analisis(tmp_path):
    ruta = _wav(tmp_path)
    t = ta.medir(ruta)
    assert t.total_s == t.carga_s + t.analisis_s
