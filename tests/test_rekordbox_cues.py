"""Tests de `rekordbox_cues` (f56) y de `Store.import_cue_marks`: los POSITION_MARK de ida y
de vuelta, sin server.

Lo esperado sale de la tabla VERIFICADA en Rekordbox 7.2.16 (`pipeline/PRUEBA_CUES.md` §5) y de
los colores que eligió el dueño (2026-10-09, escritos en hex en `motor/cue_marks.py`): acá van
como números literales, no leídos de la tabla que se prueba. Ninguna ruta ni dato del dueño.
"""
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import rekordbox_cues as rc

from motor.modelos import TrackFeatures
from motor.store import Store

# Los colores del dueño, en números (pad A rojo #ff4d5a, C azul #4fa3ff, H blanco #f2f3f8;
# loop naranja #ff9a2e).
ROJO = {"Red": "255", "Green": "77", "Blue": "90"}
AZUL = {"Red": "79", "Green": "163", "Blue": "255"}
BLANCO = {"Red": "242", "Green": "243", "Blue": "248"}
NARANJA = {"Red": "255", "Green": "154", "Blue": "46"}


def _track(marcas_xml: str) -> ET.Element:
    return ET.fromstring(f'<TRACK TrackID="1" Name="x">{marcas_xml}</TRACK>')


# --- exportar: una marca de la página → POSITION_MARK -----------------------------------------

@pytest.mark.parametrize(("marca", "esperado"), [
    (("cue", 0, 1500, None, "Drop"),
     {"Name": "Drop", "Type": "0", "Start": "1.500", "Num": "0", **ROJO}),
    (("cue", 7, 30250, None, None),
     {"Name": "", "Type": "0", "Start": "30.250", "Num": "7", **BLANCO}),
    (("memory", None, 500, None, "Intro"),
     {"Name": "Intro", "Type": "0", "Start": "0.500", "Num": "-1"}),
    # Hot loop en el pad C: naranja, NO el azul del pad.
    (("loop", 2, 10000, 14000, "Loop 4"),
     {"Name": "Loop 4", "Type": "4", "Start": "10.000", "End": "14.000", "Num": "2", **NARANJA}),
    (("loop", None, 20000, 21500, None),
     {"Name": "", "Type": "4", "Start": "20.000", "End": "21.500", "Num": "-1", **NARANJA}),
])
def test_marca_a_position_mark_es_la_tabla_verificada(marca, esperado):
    from pipeline.prueba_cues_xml import atributos_marca
    assert atributos_marca(rc.marca_a_position_mark(*marca)) == esperado


def test_armar_xml_playlist_colecciona_una_vez_y_respeta_el_orden(tmp_path):
    a, b = tmp_path / "a b.wav", tmp_path / "#c.wav"
    temas = [
        {"ruta": str(a), "titulo": "Uno\x01", "artista": "A", "bpm": 128.4, "camelot": "8A",
         "duracion_s": 240.0, "marcas": [{"kind": "cue", "num": 0, "start_ms": 1000,
                                         "end_ms": None, "name": "Drop"}], "tempo": None},
        {"ruta": str(b), "titulo": "Dos", "artista": "", "bpm": None, "camelot": None,
         "duracion_s": 200.0, "marcas": [],
         "tempo": {"Inizio": "0.600", "Bpm": "128.00", "Metro": "4/4", "Battito": "1"}},
    ]
    xml = rc.armar_xml_playlist("Mi «set» & más", temas, [0, 1, 0])
    assert b"<!DOCTYPE" not in xml and xml.startswith(b"<?xml")
    root = ET.fromstring(xml)
    col = root.find("COLLECTION")
    assert col.get("Entries") == "2"
    t1, t2 = col.findall("TRACK")
    assert (t1.get("Name"), t1.get("AverageBpm"), t1.get("Tonality"), t1.get("TotalTime")) == \
        ("Uno", "128.4", "Am", "240"), "el carácter de control se saca; el resto, tal cual"
    from playlist_import import ruta_de_location
    assert ruta_de_location(t1.get("Location")) == a.resolve().as_posix()
    assert ruta_de_location(t2.get("Location")) == b.resolve().as_posix()
    assert t2.get("AverageBpm") is None and t2.get("Tonality") is None, "sin dato, no se escribe"
    assert [p.attrib for p in t1.findall("POSITION_MARK")] == [
        {"Name": "Drop", "Type": "0", "Start": "1.000", "Num": "0", **ROJO}]
    assert t1.find("TEMPO") is None
    assert t2.find("TEMPO").attrib == {"Inizio": "0.600", "Bpm": "128.00", "Metro": "4/4",
                                       "Battito": "1"}
    nodo = root.find("PLAYLISTS/NODE/NODE")
    assert (nodo.get("Name"), nodo.get("KeyType"), nodo.get("Entries")) == ("Mi «set» & más", "0", "3")
    assert [t.get("Key") for t in nodo.findall("TRACK")] == ["1", "2", "1"]


@pytest.mark.parametrize(("grilla", "esperado"), [
    ({"primer_beat_s": 0.1, "compas_ref": 0.569, "bpm": 128.0012},
     {"Inizio": "0.569", "Bpm": "128.00", "Metro": "4/4", "Battito": "1"}),
    ({"primer_beat_s": 0.1, "compas_ref": None, "bpm": 128.0}, None),     # no se sabe cuál es el 1
    ({"primer_beat_s": None, "compas_ref": None, "bpm": 128.0}, None),    # no hay grilla
    ({"primer_beat_s": 0.1, "compas_ref": 0.5, "bpm": None}, None),
    ({"motivo": "no pude leer el audio"}, None),
])
def test_tempo_solo_con_grilla_y_compas(grilla, esperado):
    assert rc.tempo_de_grilla(grilla) == esperado


@pytest.mark.parametrize(("nombre", "esperado"), [
    ("GROOOOVE", "MusiFlix - GROOOOVE.xml"),
    ('a/b\\c:d*e?f"g<h>i|j', "MusiFlix - a b c d e f g h i j.xml"),
    ("", "MusiFlix - playlist.xml"),
    ("Cierre. ", "MusiFlix - Cierre.xml"),
    # Lo que no deja nada: «playlist», nunca «MusiFlix -.xml».
    ("///", "MusiFlix - playlist.xml"),
    ("..", "MusiFlix - playlist.xml"),
    # Controles C1 y de dirección del texto (U+202E da vuelta lo que se ve) se sacan.
    ("\x85Peak\u202eTime\u200f\u2066", "MusiFlix - Peak Time.xml"),
])
def test_nombre_del_archivo_seguro_en_windows(nombre, esperado):
    assert rc.nombre_archivo_xml(nombre) == esperado


# --- importar: POSITION_MARK → marcas de la página --------------------------------------------

def test_marcas_de_track_lee_cada_tipo_y_cuenta_lo_que_no_entra():
    track = _track(
        '<POSITION_MARK Name="Drop" Type="0" Start="30.000" Num="0" Red="255" Green="77" Blue="90"/>'
        '<POSITION_MARK Name="" Type="0" Start="60.123" Num="1" Red="48" Green="90" Blue="255"/>'
        '<POSITION_MARK Name="PRUEBA memory" Type="0" Start="15.000" Num="-1"/>'
        '<POSITION_MARK Name="loop" Type="4" Start="90.000" End="94.000" Num="2" Red="255" Green="160" Blue="0"/>'
        '<POSITION_MARK Name="" Type="4" Start="5.0" End="6.5" Num="-1"/>'
        '<POSITION_MARK Name="" Type="4" Start="7.0" End="8.0"/>'          # sin Num = memory loop
        '<POSITION_MARK Name="fade" Type="1" Start="1.0" Num="-1"/>'
        '<POSITION_MARK Name="" Type="0" Start="2.0" Num="8"/>'
        '<POSITION_MARK Name="" Type="4" Start="9.0" Num="3"/>'           # loop sin End
        '<POSITION_MARK Name="" Type="4" Start="9.0" End="8.0" Num="4"/>'  # End antes del Start
        '<POSITION_MARK Name="" Type="0" Start="nan" Num="5"/>'
        '<POSITION_MARK Name="" Type="0" Start="-1" Num="5"/>'
        f'<POSITION_MARK Name="{"x" * 65}" Type="0" Start="3.0" Num="6"/>')
    marcas, cuenta = rc.marcas_de_track(track)
    assert marcas == [
        {"kind": "cue", "num": 0, "start_ms": 30000, "end_ms": None, "name": "Drop"},
        {"kind": "cue", "num": 1, "start_ms": 60123, "end_ms": None, "name": None},
        {"kind": "memory", "num": None, "start_ms": 15000, "end_ms": None, "name": "PRUEBA memory"},
        {"kind": "loop", "num": 2, "start_ms": 90000, "end_ms": 94000, "name": "loop"},
        {"kind": "loop", "num": None, "start_ms": 5000, "end_ms": 6500, "name": None},
        {"kind": "loop", "num": None, "start_ms": 7000, "end_ms": 8000, "name": None},
        {"kind": "cue", "num": 6, "start_ms": 3000, "end_ms": None, "name": None},
    ]
    # El pad B traía el azul de la prueba (no el verde del dueño) y el loop el naranja de la
    # prueba (no el del dueño): 2 colores distintos. El nombre de 65 caracteres no entra.
    assert cuenta == {"color_distinto": 2, "tipo": 1, "pad": 1, "loop": 2, "tiempo": 2,
                      "nombre_descartado": 1}


def test_un_nombre_con_saltos_de_linea_entra_sin_nombre():
    """Un Name con \\r\\n (escapado en el XML) no se guarda recortado ni «arreglado»: la marca
    entra sin nombre y se cuenta."""
    marcas, cuenta = rc.marcas_de_track(_track(
        '<POSITION_MARK Name="Drop&#13;&#10;2" Type="0" Start="1.000" Num="0"/>'
        '<POSITION_MARK Name="Bien" Type="0" Start="2.000" Num="1"/>'))
    assert [(m["num"], m["name"]) for m in marcas] == [(0, None), (1, "Bien")]
    assert cuenta == {"nombre_descartado": 1}


# --- Store.import_cue_marks ---------------------------------------------------------------------

def _base(tmp_path):
    from motor.embeddings import DIM
    audio = tmp_path / "t.wav"
    audio.write_bytes(b"x")
    db = tmp_path / "b.sqlite"
    with Store(db) as s:
        s.upsert(audio, TrackFeatures(bpm=128.4, key="8A", energy_raw=0.3,
                                      embedding=np.ones(DIM, dtype=np.float32),
                                      key_acuerdo="3/3", key_tramos="8A|8A|8A"), duration=100.0)
    return db, audio


def _filas(store, audio):
    return [(m.kind, m.num, m.start_ms, m.end_ms, m.name) for m in store.list_cue_marks(audio)]


def test_importar_es_idempotente_y_la_pagina_gana(tmp_path):
    db, audio = _base(tmp_path)
    xml = [{"kind": "cue", "num": 0, "start_ms": 1000, "end_ms": None, "name": "A rb"},
           {"kind": "loop", "num": 2, "start_ms": 5000, "end_ms": 9000, "name": None},
           {"kind": "memory", "num": None, "start_ms": 500, "end_ms": None, "name": None}]
    with Store(db) as s:
        s.add_cue_mark(audio, "cue", 2.0, num=0, name="A página")      # pad A ocupado
        c = s.import_cue_marks(audio, xml)
        assert c == {"agregadas": 2, "ya_estaban": 0, "conservadas": 1, "reemplazadas": 0,
                     "fuera_del_tema": 0, "sin_lugar": 0}
        antes = _filas(s, audio)
        assert antes == [("cue", 0, 2000, None, "A página"), ("loop", 2, 5000, 9000, None),
                         ("memory", None, 500, None, None)]
        c2 = s.import_cue_marks(audio, xml)
        assert (c2["agregadas"], c2["ya_estaban"], c2["conservadas"]) == (0, 2, 1)
        assert _filas(s, audio) == antes, "importar dos veces lo mismo duplicó marcas"
        # Pisar: el pad A pasa a ser el de Rekordbox; lo demás de la página se queda.
        s.add_cue_mark(audio, "memory", 3.0)
        c3 = s.import_cue_marks(audio, xml, pisar=True)
        assert (c3["reemplazadas"], c3["ya_estaban"], c3["agregadas"]) == (1, 2, 0)
        assert _filas(s, audio) == [("cue", 0, 1000, None, "A rb"), ("loop", 2, 5000, 9000, None),
                                    ("memory", None, 500, None, None), ("memory", None, 3000, None, None)]


def test_importar_respeta_el_largo_y_los_topes(tmp_path, monkeypatch):
    import motor.cue_marks as cm
    monkeypatch.setattr(cm, "MAX_MEMORY", 2)
    db, audio = _base(tmp_path)
    xml = [{"kind": "memory", "num": None, "start_ms": t, "end_ms": None, "name": None}
           for t in (1000, 2000, 3000)]
    xml += [{"kind": "loop", "num": None, "start_ms": 99000, "end_ms": 100500, "name": None},
            {"kind": "cue", "num": 1, "start_ms": 100000, "end_ms": None, "name": None}]
    with Store(db) as s:
        c = s.import_cue_marks(audio, xml, duration_s=100.0)
        assert (c["agregadas"], c["sin_lugar"], c["fuera_del_tema"]) == (2, 1, 2)
        assert _filas(s, audio) == [("memory", None, 1000, None, None),
                                    ("memory", None, 2000, None, None)]


def test_importar_a_un_tema_que_el_motor_no_analizo(tmp_path):
    """Las marcas se atan a la ruta: un tema importado todavía sin analizar las guarda igual."""
    db, _ = _base(tmp_path)
    otro = tmp_path / "sin analizar.wav"
    otro.write_bytes(b"x")
    with Store(db) as s:
        c = s.import_cue_marks(otro, [{"kind": "cue", "num": 3, "start_ms": 1, "end_ms": None,
                                       "name": None}])
        assert c["agregadas"] == 1
        assert _filas(s, otro) == [("cue", 3, 1, None, None)]
