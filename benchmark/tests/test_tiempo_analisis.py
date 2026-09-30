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


# Los seis pasos que `analizar_senal` ejecuta, escritos acá a mano (NO leídos de
# `ta._PASOS`: comparar el desglose contra la propia lista del código es una tautología).
_PASOS_DEL_SCAN = {"bpm_refinado", "tono", "tono_consenso", "energia_rms", "embed",
                   "onsets_por_segundo"}


def _originales():
    """Las funciones reales, sacadas de sus módulos de origen (no de `motor.analisis`)."""
    from motor.bpm import bpm_refinado
    from motor.embeddings import embed
    from motor.energia import energia_rms
    from motor.tonalidad import tono, tono_consenso
    return {"bpm_refinado": bpm_refinado, "tono": tono, "tono_consenso": tono_consenso,
            "energia_rms": energia_rms, "embed": embed,
            "onsets_por_segundo": A.onsets_por_segundo}


def test_el_desglose_cubre_los_pasos_del_scan_real(tmp_path):
    """Un scan real deja los seis pasos en el desglose, y su suma no puede exceder el total
    (los pasos son sub-tramos de la misma llamada: el total es autoritativo)."""
    ruta = _wav(tmp_path)

    t = ta.medir(ruta)

    assert set(t.pasos) == _PASOS_DEL_SCAN, f"el desglose no cubre los pasos: {set(t.pasos)}"
    assert sum(t.pasos.values()) <= t.analisis_s + 1e-3, (
        f"la suma de pasos {sum(t.pasos.values()):.3f} excede el total {t.analisis_s:.3f}")


def test_el_total_cobra_un_paso_que_no_esta_en_el_desglose(tmp_path, monkeypatch):
    """`analisis_s` sale de cronometrar `analizar_senal` entero, no de sumar el desglose.
    `ventana_central` no está en el desglose: si el total fuera la suma de los pasos, su
    costo (o el de un paso que el scan agregue mañana) desaparecería del número de §4."""
    ruta = _wav(tmp_path)
    real = A.ventana_central

    def _ventana_lenta(*a, **k):
        time.sleep(0.3)
        return real(*a, **k)

    monkeypatch.setattr(A, "ventana_central", _ventana_lenta)

    t = ta.medir(ruta)

    assert t.analisis_s >= 0.3, (
        f"analisis_s {t.analisis_s:.3f} no cobra la ventana_central (0.3 s) — "
        f"¿el total es la suma del desglose {sum(t.pasos.values()):.3f}?")


def test_la_carga_es_la_del_motor_y_entra_en_el_total(tmp_path, monkeypatch):
    """La carga cronometrada es `motor.analisis.cargar` al SR del motor (la misma del scan
    real, `analizar_archivo`) y su costo entra en el total."""
    y, sr = click_track(128.0, dur=5.0, nota="A", modo="min", seed=3)
    llamadas = []

    def _carga_lenta(ruta, sr_pedido):
        llamadas.append(sr_pedido)
        time.sleep(0.3)
        return y

    monkeypatch.setattr(A, "cargar", _carga_lenta)

    t = ta.medir("no-importa.wav")

    assert llamadas == [A.SR] == [22050], f"la carga no fue la del motor a su SR: {llamadas}"
    assert t.carga_s >= 0.3, f"la carga no se cronometró: {t.carga_s:.3f}"
    assert t.total_s >= 0.3 + t.analisis_s, (
        f"total {t.total_s:.3f} no incluye la carga (0.3) + análisis ({t.analisis_s:.3f})")


def test_medir_restaura_las_funciones_del_motor(tmp_path):
    """El desglose reemplaza globals de `motor.analisis` mientras mide. Si no los restaura,
    todo lo que corra después en el proceso (otro scan, la radio) queda cronometrado."""
    ruta = _wav(tmp_path)
    originales = _originales()

    ta.medir(ruta)

    sucios = [p for p, fn in originales.items() if getattr(A, p) is not fn]
    assert sucios == [], f"quedaron envueltos después de medir: {sucios}"


def test_medir_restaura_aunque_el_scan_lance(tmp_path, monkeypatch):
    """Un track que rompe el scan (o un Ctrl+C a mitad) no puede dejar el motor envuelto."""
    ruta = _wav(tmp_path)
    originales = _originales()

    def _embed_que_rompe(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(A, "embed", _embed_que_rompe)
    originales["embed"] = _embed_que_rompe

    try:
        ta.medir(ruta)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("medir se tragó el KeyboardInterrupt")

    sucios = [p for p, fn in originales.items() if getattr(A, p) is not fn]
    assert sucios == [], f"quedaron envueltos después de la excepción: {sucios}"


def test_calentar_calienta_el_scan_del_motor_con_el_sr_de_la_etapa_a(monkeypatch):
    """La etapa A (`benchmark.analizar.analizar`) llama `calentar(sr)`. El warm-up tiene que
    aceptar ese `sr` y delegar en `motor.analisis.calentar` (el camino completo del scan);
    si no calienta, el primer track paga el JIT y el máximo miente."""
    from benchmark.analizar import analizar

    llamadas = []
    monkeypatch.setattr(A, "calentar", lambda sr: llamadas.append(sr) or 0.0)

    analizar([], sr=22050, warmup=True, progreso=False)

    assert llamadas == [22050], f"el warm-up no llegó al motor con el sr pedido: {llamadas}"
