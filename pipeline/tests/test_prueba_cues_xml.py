"""Tests de `pipeline/prueba_cues_xml.py`: el XML de PRUEBA con cues y un loop (tarea 5.68).

El audio sale de `motor/sintetico.py`: la duración, el BPM (128) y la key (A menor = 8A)
son los del generador, por construcción, no inventados (spec §5). Los tiempos de las marcas
no son datos de ningún track: son los segundos de la prueba, elegidos por quien la corre.
"""
import hashlib
import sqlite3
import xml.etree.ElementTree as ET
from urllib.parse import quote

import numpy as np
import pytest
import soundfile as sf

from motor.embeddings import DIM
from motor.energia import energia_rms
from motor.modelos import TrackFeatures
from motor.sintetico import click_track
from motor.store import VERSION_ESQUEMA, Store
from pipeline.aplicar import escribir_xml_rekordbox
from pipeline.prueba_cues_xml import (
    CueFueraDelTrack,
    ErrorDePrueba,
    datos_de_la_base,
    escribir_xml_prueba,
    main,
    marcas_de_prueba,
)

BPM_SINTETICO = 128.0
CAMELOT_SINTETICO = "8A"     # click_track(nota="A", modo="min")


def _sha(ruta) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _wav(ruta, dur):
    y, sr = click_track(BPM_SINTETICO, dur=dur, nota="A", modo="min", seed=3)
    sf.write(str(ruta), y, sr, subtype="PCM_16")
    return ruta, y, sr


@pytest.fixture(scope="module")
def audio_100s(tmp_path_factory):
    """Un track de 100 s: entran todas las marcas por defecto (la última termina en 94 s)."""
    ruta, _, _ = _wav(tmp_path_factory.mktemp("audio") / "prueba 100s (A).wav", 100.0)
    return ruta


def _marcas(xml):
    return [dict(pm.attrib) for pm in ET.parse(xml).getroot().iter("POSITION_MARK")]


def test_el_xml_parsea_y_trae_exactamente_las_cuatro_marcas(audio_100s, tmp_path):
    """Valores EXACTOS de cada atributo: un atributo de más, de menos o redondeado distinto
    también cambia lo que Rekordbox lee."""
    from ground_truth.rekordbox import parsear

    xml = tmp_path / "prueba.xml"
    assert main(["--audio", str(audio_100s), "--salida", str(xml)]) == 0

    assert _marcas(xml) == [
        {"Name": "PRUEBA hot 1", "Type": "0", "Start": "30.000", "Num": "0",
         "Red": "230", "Green": "40", "Blue": "40"},
        {"Name": "", "Type": "0", "Start": "60.000", "Num": "1",
         "Red": "48", "Green": "90", "Blue": "255"},
        {"Name": "PRUEBA memory", "Type": "0", "Start": "15.000", "Num": "-1"},
        {"Name": "PRUEBA loop 4s", "Type": "4", "Start": "90.000", "End": "94.000", "Num": "2",
         "Red": "255", "Green": "160", "Blue": "0"},
    ]
    # Lo lee el mismo parser del ground truth: un solo track, con sus 4 marcas.
    (track,) = parsear(xml)
    assert track["num_cues"] == 4
    assert track["duration_s"] == 100


def test_el_loop_tiene_end_mayor_que_start_y_es_el_unico_con_end(audio_100s, tmp_path):
    xml = tmp_path / "prueba.xml"
    assert main(["--audio", str(audio_100s), "--salida", str(xml), "--loop", "70.5",
                 "--loop-largo", "2"]) == 0
    con_end = [m for m in _marcas(xml) if "End" in m]
    assert [m["Type"] for m in con_end] == ["4"], f"solo el loop lleva End: {con_end}"
    (loop,) = con_end
    assert float(loop["End"]) > float(loop["Start"])
    assert (loop["Start"], loop["End"]) == ("70.500", "72.500")


def test_los_tiempos_son_configurables_y_van_con_tres_decimales(audio_100s, tmp_path):
    xml = tmp_path / "prueba.xml"
    assert main(["--audio", str(audio_100s), "--salida", str(xml), "--hot1", "12.3456",
                 "--hot2", "40.1", "--memory", "0.5", "--nombre-hot1", "Drop <&>"]) == 0
    starts = {m["Num"]: m["Start"] for m in _marcas(xml)}
    assert starts == {"0": "12.346", "1": "40.100", "-1": "0.500", "2": "90.000"}
    assert _marcas(xml)[0]["Name"] == "Drop <&>", "el escape del XML se perdió en la ida y vuelta"


def test_un_cue_fuera_del_track_se_rechaza_y_no_se_escribe_nada(tmp_path):
    """50 s de audio: el hot cue 2 (60 s) y el loop (90 s) caen fuera."""
    audio, _, _ = _wav(tmp_path / "corto.wav", 50.0)
    xml = tmp_path / "prueba.xml"
    assert main(["--audio", str(audio), "--salida", str(xml)]) == 2
    assert not xml.exists(), "con un cue fuera del track no se escribe el XML"

    with pytest.raises(CueFueraDelTrack) as e:
        escribir_xml_prueba(audio, 50.0, marcas_de_prueba(), xml)
    assert "hot cue 2" in str(e.value) and "loop" in str(e.value)
    assert "hot cue 1" not in str(e.value), "el hot cue 1 (30 s) SÍ entra en 50 s"
    assert not xml.exists()


def test_un_loop_que_termina_despues_del_final_se_rechaza(audio_100s, tmp_path):
    """El Start entra (90 < 93) pero el End no (94 > 93): el loop se saldría del track."""
    xml = tmp_path / "prueba.xml"
    with pytest.raises(CueFueraDelTrack, match="loop"):
        escribir_xml_prueba(audio_100s, 93.0, marcas_de_prueba(), xml)
    assert not xml.exists()
    # Justo hasta el final sí entra.
    escribir_xml_prueba(audio_100s, 94.0, marcas_de_prueba(), xml)
    assert xml.exists()


def test_location_y_track_son_los_del_escritor_del_pipeline(audio_100s, tmp_path):
    """El <TRACK> sale del mismo armado: Location `file://localhost/...` igual a la que
    escribe el pipeline para ese archivo, y el XML del pipeline sigue sin POSITION_MARK."""
    prueba = tmp_path / "prueba.xml"
    escribir_xml_prueba(audio_100s, 100.0, marcas_de_prueba(), prueba)
    pipe = tmp_path / "pipeline.xml"
    escribir_xml_rekordbox([{"ruta": str(audio_100s), "duracion_s": 100.0}], pipe,
                           audio_100s.parent)

    t_prueba = ET.parse(prueba).getroot().find("COLLECTION/TRACK").attrib
    t_pipe = ET.parse(pipe).getroot().find("COLLECTION/TRACK").attrib
    assert t_prueba == t_pipe
    assert t_prueba["Location"] == ("file://localhost/"
                                    + quote(audio_100s.resolve().as_posix(), safe="/:"))
    assert list(ET.parse(pipe).getroot().iter("POSITION_MARK")) == [], \
        "el XML del pipeline no lleva cues: los de la prueba se colaron en el armado compartido"


def test_sin_base_no_se_escriben_bpm_ni_key(audio_100s, tmp_path):
    xml = tmp_path / "prueba.xml"
    assert main(["--audio", str(audio_100s), "--salida", str(xml)]) == 0
    track = ET.parse(xml).getroot().find("COLLECTION/TRACK").attrib
    assert "AverageBpm" not in track and "Tonality" not in track, track


def _base_con(tmp_path, audio):
    db = tmp_path / "copia.sqlite"
    y, _ = sf.read(str(audio), dtype="float32")
    features = TrackFeatures(bpm=BPM_SINTETICO, key=CAMELOT_SINTETICO,
                             energy_raw=energia_rms(y),
                             embedding=np.random.default_rng(0).normal(size=DIM))
    with Store(db) as store:
        store.upsert(audio, features, duration=100.0, license="CC0",
                     source_url="motor/sintetico.py")
    return db


def test_con_base_lleva_bpm_y_key_y_la_base_no_se_toca(audio_100s, tmp_path):
    db = _base_con(tmp_path, audio_100s)
    antes_db, antes_audio = _sha(db), _sha(audio_100s)
    xml = tmp_path / "prueba.xml"
    assert main(["--audio", str(audio_100s), "--salida", str(xml), "--db", str(db)]) == 0
    track = ET.parse(xml).getroot().find("COLLECTION/TRACK").attrib
    assert track["AverageBpm"] == "128.0"
    assert track["Tonality"] == "Am"
    assert "Comments" not in track, "la prueba no escribe Comments sobre el track"
    assert _sha(db) == antes_db, "la base se modificó"
    assert _sha(audio_100s) == antes_audio, "el audio se modificó"


def test_una_base_de_esquema_viejo_no_se_abre(audio_100s, tmp_path):
    """El Store la migraría al abrirla: eso es escribir la base del dueño."""
    db = tmp_path / "vieja.sqlite"
    con = sqlite3.connect(str(db))
    con.execute(f"PRAGMA user_version = {VERSION_ESQUEMA - 1}")
    con.execute("CREATE TABLE tracks (path TEXT PRIMARY KEY)")
    con.commit()
    con.close()
    antes = _sha(db)
    with pytest.raises(ErrorDePrueba, match="migraría"):
        datos_de_la_base(db, audio_100s)
    assert _sha(db) == antes


def test_una_base_con_la_version_al_dia_pero_sin_tablas_no_se_abre(audio_100s, tmp_path):
    """Auditoría de f47: el chequeo miraba solo `user_version`, y el Store crea el esquema en
    una base sin tablas: abrirla así también es escribir la base del dueño."""
    db = tmp_path / "vacia.sqlite"
    con = sqlite3.connect(str(db))
    con.execute(f"PRAGMA user_version = {VERSION_ESQUEMA}")
    con.commit()
    con.close()
    antes = _sha(db)
    with pytest.raises(ErrorDePrueba, match="no tiene la tabla de tracks"):
        datos_de_la_base(db, audio_100s)
    assert _sha(db) == antes, "la base se modificó"
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT name FROM sqlite_master").fetchall() == [], "se creó el esquema"
    con.close()


def test_la_salida_tiene_que_ser_xml_para_no_pisar_el_audio(audio_100s, tmp_path):
    antes = _sha(audio_100s)
    assert main(["--audio", str(audio_100s), "--salida", str(audio_100s)]) == 1
    assert _sha(audio_100s) == antes


def test_no_pisa_un_xml_que_ya_existe_sin_forzar(audio_100s, tmp_path):
    """Auditoría de f47: el reexport de Rekordbox (la colección entera) se guarda en la misma
    carpeta que la prueba; correr la prueba de nuevo no puede pisarlo sin pedirlo."""
    xml = tmp_path / "prueba.xml"
    otro = '<?xml version="1.0"?><DJ_PLAYLISTS><COLLECTION Entries="346"/></DJ_PLAYLISTS>'
    xml.write_text(otro, encoding="utf-8")
    assert main(["--audio", str(audio_100s), "--salida", str(xml)]) == 1
    assert xml.read_text(encoding="utf-8") == otro, "pisó el XML existente sin --forzar"
    assert main(["--audio", str(audio_100s), "--salida", str(xml), "--forzar"]) == 0
    assert [m["Name"] for m in _marcas(xml)] == ["PRUEBA hot 1", "", "PRUEBA memory", "PRUEBA loop 4s"]
