"""f45: orden "Para mezclar" de la Station (station_mezcla.py + /api/station/analisis|orden).

BPM y key salen SIEMPRE del generador sintético (`motor.sintetico.click_track`, BPM exacto
por construcción) medidos por el camino real del motor (`station_mezcla.analizar_senal` →
`motor.analisis.analizar_senal`): nada inventado (spec §5).
"""
from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import station_mezcla as sm  # noqa: E402

from motor.radio import bpm_delta_pct, key_relation  # noqa: E402
from motor.scoring import bpm_score  # noqa: E402
from motor.sintetico import click_track  # noqa: E402

SR = 22050
SEMILLA = "1000"
# id → (BPM, nota, modo). 100 BPM no entra a ±8 % (ni con octava) de ninguno de los otros.
TEMAS = {
    SEMILLA: (128.0, "A", "min"),
    "1001": (126.0, "E", "min"),
    "1002": (131.0, "A", "min"),
    "1003": (100.0, "C", "maj"),
    "1004": (124.0, "D", "min"),
}
SIN_AUDIO = "1005"


@pytest.fixture(scope="module")
def analisis():
    out = {}
    for sid, (bpm, nota, modo) in TEMAS.items():
        y, _ = click_track(bpm, dur=30.0, sr=SR, nota=nota, modo=modo)
        out[sid] = {**sm.analizar_senal(y), "ok": True, "sc_id": sid}
    out[SIN_AUDIO] = {"ok": False, "motivo": "SoundCloud no ofrece un audio que se pueda reproducir acá."}
    return out


def _items():
    # Orden "de SoundCloud": el de 100 BPM y el sin audio adelante, a propósito.
    orden = ["1003", SIN_AUDIO, "1004", "1001", "1002"]
    return [{"video_id": s, "titulo": f"Tema {s}", "artista": f"Artista {s}", "duracion": 300.0,
             "url": f"https://soundcloud.com/a/{s}"} for s in orden]


def test_el_bpm_medido_es_el_del_generador(analisis):
    for sid, (bpm, _, _) in TEMAS.items():
        medido = analisis[sid]["bpm"]
        assert abs(medido - bpm) <= 1.0, f"{sid}: medido {medido}, generado {bpm}"
        # §6: un decimal, del valor medido (redondear a entero es mentir).
        assert medido == round(analisis[sid]["bpm_crudo"], 1)
    assert any(round(r["bpm_crudo"], 1) != round(r["bpm_crudo"]) for r in analisis.values() if r["ok"]), \
        "precondición: algún BPM medido tiene decimal; si no, este test no distingue el redondeo"


def test_el_set_no_tiene_transiciones_fuera_de_8(analisis):
    o = sm.ordenar_con(SEMILLA, _items(), analisis)
    set_ids = [SEMILLA] + [f["video_id"] for f in o["orden"] if f["grupo"] == "set"]
    assert len(set_ids) == 4, o
    for a, b in zip(set_ids, set_ids[1:], strict=False):
        assert bpm_score(analisis[a]["bpm_crudo"], analisis[b]["bpm_crudo"]) > 0, (a, b)
    assert sm.metricas([analisis[s] for s in set_ids])["fuera_8"] == 0


def test_lo_que_no_entra_va_al_final_con_motivo_y_no_se_descarta(analisis):
    o = sm.ordenar_con(SEMILLA, _items(), analisis)
    grupos = [(f["video_id"], f["grupo"]) for f in o["orden"]]
    assert sorted(s for s, _ in grupos) == sorted(it["video_id"] for it in _items()), "falta o sobra un tema"
    assert grupos[-2:] == [("1003", "fuera"), (SIN_AUDIO, "sin_analisis")], grupos
    fuera = o["orden"][-2]
    assert fuera["motivo"].startswith("Fuera de rango de BPM"), fuera
    assert f"{analisis['1003']['bpm_crudo']:.1f} BPM" in fuera["motivo"]
    assert o["orden"][-1]["motivo"] == analisis[SIN_AUDIO]["motivo"]
    assert "razon" not in o["orden"][-1]


def test_el_por_que_es_el_del_motor(analisis):
    o = sm.ordenar_con(SEMILLA, _items(), analisis)
    previo = SEMILLA
    for f in [f for f in o["orden"] if f["grupo"] == "set"]:
        a, b = analisis[previo], analisis[f["video_id"]]
        pct, lectura = bpm_delta_pct(a["bpm_crudo"], b["bpm_crudo"])
        assert lectura == "mismo tiempo"
        ka = a["key"] + ("?" if a["key_dudosa"] else "")
        kb = b["key"] + ("?" if b["key_dudosa"] else "")
        esperado = f"{pct:+.1f}% BPM | {ka} → {kb} ({key_relation(a['key'], b['key'])})"
        assert f["razon"] == esperado
        previo = f["video_id"]


def test_key_dudosa_se_marca_con_signo(analisis):
    # 30 s no alcanzan para 3 tramos de 45 s: acuerdo "0/0" → dudosa (motor.cli.key_dudosa).
    assert analisis[SEMILLA]["key_acuerdo"] == "0/0"
    assert analisis[SEMILLA]["key_dudosa"] is True
    o = sm.ordenar_con(SEMILLA, _items(), analisis)
    primera = next(f for f in o["orden"] if f["grupo"] == "set")
    assert f"{analisis[SEMILLA]['key']}? →" in primera["razon"]


def test_es_determinista(analisis):
    a = sm.ordenar_con(SEMILLA, _items(), analisis)
    b = sm.ordenar_con(SEMILLA, _items(), dict(reversed(list(analisis.items()))))
    assert a == b
    assert sm.config_mezcla(3).randomness == 0.0


def test_semilla_sin_analisis_arranca_por_el_primero_analizado(analisis):
    sin_semilla = {k: v for k, v in analisis.items() if k != SEMILLA}
    o = sm.ordenar_con(SEMILLA, _items(), sin_semilla)
    assert o["semilla_analizada"] is False and o["aviso"]
    primera = o["orden"][0]
    assert primera["video_id"] == "1003" and primera["razon"].startswith("semilla |"), primera


def test_nada_analizado_no_inventa_orden():
    o = sm.ordenar_con(SEMILLA, _items(), {})
    assert o["exito"] is False


def test_publico_no_manda_el_embedding(analisis):
    p = sm.publico(analisis[SEMILLA])
    assert "embedding" not in p and p["bpm"] == analisis[SEMILLA]["bpm"]


# --- audio: un MP3 cortado a mitad (lo que devuelve un Range) se decodifica ---------------------

def _mp3(bpm: float, dur: float = 40.0) -> bytes:
    import soundfile as sf

    y, _ = click_track(bpm, dur=dur, sr=44100, nota="A", modo="min")
    buf = io.BytesIO()
    sf.write(buf, np.column_stack([y, y]), 44100, format="MP3")
    return buf.getvalue()


def test_mp3_cortado_a_mitad_se_decodifica_y_mide_bien():
    data = _mp3(128.0)
    cortado = data[len(data) // 3 + 7:]          # arranca en cualquier byte, no en un frame
    k = sm.primer_frame_mp3(cortado)
    assert k > 0, "un corte arbitrario no debería caer justo en un frame"
    y = sm.decodificar_mp3(cortado)
    assert abs(y.size / SR - 40.0 * 2 / 3) < 1.5
    assert abs(sm.analizar_senal(y)["bpm"] - 128.0) <= 1.0


def test_bytes_que_no_son_mp3_dan_motivo():
    with pytest.raises(sm.AnalisisError):
        sm.decodificar_mp3(bytes(range(256)) * 50)


def test_rango_tramo_central():
    assert sm.rango_tramo(None, 1000) is None
    assert sm.rango_tramo(140.0, 2_000_000) is None            # corto: el archivo entero
    desde, hasta, inicio, largo = sm.rango_tramo(300.0, 4_800_000)
    assert inicio == 80.0 and largo == sm.TRAMO_S
    assert desde == 80 * 16000 and hasta == desde + int(sm.TRAMO_S * 16000)


# --- endpoints -------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    datos = tmp_path_factory.mktemp("server-datos")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MUSIFLIX_DATA_DIR", str(datos))
        mp.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
        mp.setenv("MUSIFLIX_SIN_CALENTAR", "1")
        mp.delenv("REDIS_URL", raising=False)
        mp.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        for mod in ("server", "jobs", "db"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
    from fastapi.testclient import TestClient
    return TestClient(srv.app)


def test_analisis_id_invalido_es_400(client):
    r = client.get("/api/station/analisis", params={"ref": "../etc"})
    assert r.status_code == 400 and r.json()["ok"] is False


def test_analisis_devuelve_lo_medido_sin_embedding(client, analisis, monkeypatch):
    monkeypatch.setattr(sm, "analizar", lambda sid: analisis[sid])
    j = client.get("/api/station/analisis", params={"ref": "1001"}).json()
    assert j["bpm"] == analisis["1001"]["bpm"] and j["key"] == analisis["1001"]["key"]
    assert "embedding" not in j


def test_orden_usa_la_cache(client, analisis, monkeypatch):
    monkeypatch.setattr(sm, "_cache_get", lambda sid: analisis.get(sid))
    r = client.post("/api/station/orden", json={"semilla": SEMILLA, "items": _items()})
    j = r.json()
    assert j["exito"] is True
    assert j == sm.ordenar_con(SEMILLA, _items(), analisis)


@pytest.mark.parametrize("body", [{}, {"semilla": "x", "items": [{"video_id": "1"}]},
                                  {"semilla": "1", "items": []},
                                  {"semilla": "1", "items": [{"video_id": "abc"}]}])
def test_orden_pedido_invalido_es_400(client, body):
    assert client.post("/api/station/orden", json=body).status_code == 400
