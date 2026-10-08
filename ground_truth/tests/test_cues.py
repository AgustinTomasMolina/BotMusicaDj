"""Tests de la lógica de análisis de cues (#5.4 prep).

Las combinaciones Type/Num/End de `test_clase_*` son las que escribió Rekordbox 7.2.16 en su
propio export (y las que reexportó de la prueba 5.68, pipeline/PRUEBA_CUES.md): no se inventan.
Las posiciones de `test_zona_limites` son sintéticas, para ejercitar los umbrales."""
from ground_truth.cues import _zona, clase
from ground_truth.rekordbox import escribir_csv, parsear


def _cue(type="0", num="0", name="", start=0.0, end=None):
    return {"type": type, "num": num, "name": name, "start": start, "end": end}


def test_zona_limites():
    assert _zona(0.10) == "inicio"
    assert _zona(0.14) == "inicio"
    assert _zona(0.15) == "medio"       # 0.15 ya NO es inicio (umbral estricto <0.15)
    assert _zona(0.50) == "medio"
    assert _zona(0.85) == "medio"       # 0.85 todavía es medio (final es >0.85)
    assert _zona(0.86) == "final"
    assert _zona(0.99) == "final"


def test_clase_lectura_verificada_contra_rekordbox():
    # Type=4 Num=0 sin nombre NO es un memory cue anónimo (la lectura vieja): es un loop en A.
    assert clase(_cue(type="4", num="0", start=26.940, end=30.274)) == "hot loop A"
    assert clase(_cue(type="4", num="2", name="PRUEBA loop 4s", start=90.0, end=94.0)) == "hot loop C"
    assert clase(_cue(type="4", num="6", start=193.655, end=196.966)) == "hot loop G"
    assert clase(_cue(type="0", num="-1", name="PRUEBA memory", start=15.0)) == "memory cue"
    assert clase(_cue(type="0", num="0", name="1.1Bars")) == "hot cue A"
    assert clase(_cue(type="0", num="1")) == "hot cue B"
    assert clase(_cue(type="4", num="-1", start=10.0, end=12.0)) == "memory loop"


def test_clase_tipos_y_slots_fuera_de_lo_comun():
    assert clase(_cue(type="1", num="-1")) == "memory fade-in"
    assert clase(_cue(type="3", num="0")) == "hot load A"
    assert clase(_cue(type="9", num="0")) == "hot Type 9 A"
    assert clase(_cue(type="0", num="8")) == "cue (Num 8)"      # no hay hot cue I


_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0"><PRODUCT Name="rekordbox" Version="7.2.16" Company="AlphaTheta"/>
<COLLECTION Entries="1">
<TRACK TrackID="1" Name="Tema" Artist="A" TotalTime="304" AverageBpm="147.00" Tonality="Ebm"
       Location="file://localhost/C:/x/tema.wav">
<POSITION_MARK Name="PRUEBA hot 1" Type="0" Start="30.000" Num="0" Red="230" Green="40" Blue="40" />
<POSITION_MARK Name="PRUEBA memory" Type="0" Start="15.000" Num="-1" />
<POSITION_MARK Name="PRUEBA loop 4s" Type="4" Start="90.000" End="94.000" Num="2" Red="255" Green="160" Blue="0" />
</TRACK></COLLECTION></DJ_PLAYLISTS>"""


def test_el_parser_lee_el_end_de_los_loops(tmp_path):
    """Sin `End` no se puede distinguir un loop de un cue: el parser tiene que leerlo (y el
    CSV llevarlo). Las marcas son las de la prueba 5.68 tal cual las reexportó Rekordbox."""
    xml = tmp_path / "rb.xml"
    xml.write_text(_XML, encoding="utf-8")
    cues = parsear(xml)[0]["cues"]
    assert [(c["type"], c["num"], c["start"], c["end"]) for c in cues] == [
        ("0", "0", 30.0, None), ("0", "-1", 15.0, None), ("4", "2", 90.0, 94.0)]
    _, cues_csv = escribir_csv(parsear(xml), tmp_path)
    filas = cues_csv.read_text(encoding="utf-8").splitlines()
    assert filas[0] == "track_id,cue_name,cue_type,start_s,num,end_s"
    assert filas[1:] == ["1,PRUEBA hot 1,0,30.0,0,", "1,PRUEBA memory,0,15.0,-1,",
                         "1,PRUEBA loop 4s,4,90.0,2,94.0"]
