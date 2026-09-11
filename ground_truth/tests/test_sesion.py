"""Tests de la sesión de ground truth.

Ninguno toca audio: lo que hay que proteger acá es que el comando FALLE cuando falta
algo (y diga qué falta) y que la reanudación no vuelva a analizar lo ya hecho. Los
valores de BPM/tonalidad no se inventan en ningún test — el analizador se reemplaza por
un doble que registra a quién lo llamaron.
"""
from pathlib import Path

import pytest

from benchmark.analizar import FilaAnalisis
from ground_truth import sesion

XML_MIN = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
 <COLLECTION Entries="{n}">
{tracks} </COLLECTION>
</DJ_PLAYLISTS>
"""

TRACK = ('  <TRACK TrackID="{i}" Name="{nombre}" Artist="A" AverageBpm="{bpm}"'
         ' Tonality="Am" TotalTime="200" Kind="MP3 File"'
         ' Location="file://localhost/D:/Music/{nombre}"/>\n')


def _xml(tmp_path: Path, nombres, bpm=128.0) -> Path:
    tracks = "".join(TRACK.format(i=i, nombre=n, bpm=bpm)
                     for i, n in enumerate(nombres, 1))
    p = tmp_path / "Rekordbox.xml"
    p.write_text(XML_MIN.format(n=len(nombres), tracks=tracks), encoding="utf-8")
    return p


# --- Lo que tiene que fallar ------------------------------------------------------------


def test_sin_xml_falla_y_dice_que_falta(tmp_path):
    """Sin XML no hay ground truth. El mensaje tiene que nombrar el archivo y cómo sacarlo."""
    faltante = tmp_path / "no_existe.xml"
    with pytest.raises(sesion.SesionIncompleta) as e:
        sesion.correr(faltante, [str(tmp_path)], tmp_path / "out")
    msg = str(e.value)
    assert "no_existe.xml" in msg, "el error no dice QUÉ archivo falta"
    assert "Export Collection" in msg, "el error no dice cómo exportarlo de Rekordbox"


def test_xml_sin_tracks_falla(tmp_path):
    xml = _xml(tmp_path, [])
    with pytest.raises(sesion.SesionIncompleta) as e:
        sesion.correr(xml, [str(tmp_path)], tmp_path / "out")
    assert "ningún <TRACK>" in str(e.value)


def test_si_no_resuelve_nada_no_analiza(tmp_path, monkeypatch):
    """Si ningún track encuentra su audio, analizar no tiene sentido: corta antes."""
    xml = _xml(tmp_path, ["fantasma.mp3"])
    llamadas = []
    monkeypatch.setattr(sesion, "analizar_uno",
                        lambda *a, **k: llamadas.append(a) or None)

    vacia = tmp_path / "sin_audio"
    vacia.mkdir()
    with pytest.raises(sesion.SesionIncompleta) as e:
        sesion.correr(xml, [str(vacia)], tmp_path / "out", confirmar=False)

    assert llamadas == [], "analizó igual aunque no había con qué cruzar"
    assert str(vacia) in str(e.value), "el error no dice dónde buscó"


# --- Resolución -------------------------------------------------------------------------


def test_resolver_todo_separa_ok_ambiguo_y_faltante(tmp_path):
    """Los tres estados tienen que quedar en cubetas distintas, no todos en 'ok'."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "unico.mp3").write_bytes(b"x")
    (a / "repe.mp3").write_bytes(b"x")
    (b / "repe.mp3").write_bytes(b"yy")      # mismo nombre, otro contenido -> ambiguo

    tracks = [
        {"location": "D:/Music/unico.mp3", "bpm": 128.0, "camelot": "8A"},
        {"location": "D:/Music/repe.mp3", "bpm": 124.0, "camelot": "9A"},
        {"location": "D:/Music/falta.mp3", "bpm": 130.0, "camelot": "1A"},
        {"location": "D:/Music/sin_datos.mp3", "bpm": 0.0, "camelot": ""},
    ]
    res = sesion.resolver_todo(tracks, [str(a), str(b)])

    assert [Path(r).name for r in res["rutas"]] == ["unico.mp3"]
    assert [n for n, _ in res["ambiguos"]] == ["repe.mp3"]
    assert res["no_encontrados"] == ["falta.mp3"]
    assert res["sin_referencia"] == 1, "el track sin BPM ni tonalidad no se contó aparte"
    assert res["total_xml"] == 4


# --- Reanudación ------------------------------------------------------------------------


def _fila(ruta: str, bpm: float) -> dict:
    """Fila de etapa A completa. Los números son de utilería y el test no los usa como
    ground truth: solo comprueba que la reanudación devuelva LA MISMA fila, no una nueva."""
    return {"archivo": Path(ruta).name, "ruta": ruta, "duracion_s": 200.0,
            "bpm_est": bpm, "key_est": "8A", "confianza": 0.7, "acuerdo": "",
            "tramos": "", "t_carga_s": 0.1, "t_analisis_s": 0.5, "t_total_s": 0.6,
            "metodo": "tono"}


def test_etapa_a_no_reanaliza_lo_ya_hecho(tmp_path, monkeypatch):
    parcial = tmp_path / "analisis.csv"
    hecho, pendiente = "D:/M/uno.mp3", "D:/M/dos.mp3"
    sesion._guardar_parcial([_fila(hecho, 128.0)], parcial)

    analizados = []

    def _doble(ruta, sr=22050, consenso=False):
        analizados.append(ruta)
        return FilaAnalisis(**_fila(ruta, 124.0))

    monkeypatch.setattr(sesion, "analizar_uno", _doble)
    monkeypatch.setattr("benchmark.tiempo_analisis.calentar", lambda sr=22050: 0.0)

    filas = sesion.etapa_a([hecho, pendiente], False, parcial, "prueba")

    assert analizados == [pendiente], "reanalizó un track que ya estaba en el CSV"
    assert sorted(f["ruta"] for f in filas) == sorted([hecho, pendiente])
    assert [f["bpm_est"] for f in filas if f["ruta"] == hecho] == ["128.0"], \
        "la fila reanudada no volvió del CSV tal cual estaba"


def test_etapa_a_guarda_despues_de_cada_track(tmp_path, monkeypatch):
    """Un corte de luz no puede costar horas de análisis: el CSV se escribe track a track."""
    parcial = tmp_path / "analisis.csv"
    tamanos = []

    def _doble(ruta, sr=22050, consenso=False):
        tamanos.append(len(sesion.leer_hechos(parcial)))   # lo guardado ANTES de este
        return FilaAnalisis(**_fila(ruta, 130.0))

    monkeypatch.setattr(sesion, "analizar_uno", _doble)
    monkeypatch.setattr("benchmark.tiempo_analisis.calentar", lambda sr=22050: 0.0)

    sesion.etapa_a(["a.mp3", "b.mp3", "c.mp3"], False, parcial, "prueba")

    assert tamanos == [0, 1, 2], f"no escribió tras cada track: {tamanos}"
    assert len(sesion.leer_hechos(parcial)) == 3


def test_leer_hechos_sin_archivo_es_vacio(tmp_path):
    assert sesion.leer_hechos(tmp_path / "no_hay.csv") == {}
