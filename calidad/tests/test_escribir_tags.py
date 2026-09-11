"""Tests de la escritura de tags (tarea 5.3)."""
import hashlib
import json

import numpy as np
import pytest

from calidad.corte_espectral import FilaCalidad
from calidad.escribir_tags import (
    ESCRIBIR,
    OMITIR,
    PRESERVAR,
    VACIO,
    escribir_en_copia,
    planificar,
    registro_musiflix,
)
from calidad.normalizar_tags import Propuesta


def _prop(artista="Fran Perrotta", titulo="Static Bow"):
    return Propuesta(
        archivo="x.wav", ruta="x.wav", nombre_base="x", artista_actual="", titulo_actual="",
        artista_propuesto=artista, titulo_propuesto=titulo, orientacion="titulo-artista",
        veces_izq=1, veces_der=7, limpieza="", motivo="por frecuencia", revisar="no")


def _cal(bandera="ok", corte=20.1, muro=3.0, motivo="[bitrate] ok"):
    return FilaCalidad(
        archivo="x.wav", ruta="x.wav", formato="wave", lossless="si",
        bitrate_declarado_kbps=1411, sample_rate_hz=44100, corte_medido_khz=corte,
        criterio="lossless", corte_esperado_khz=None, margen_khz=None, muro_db=muro,
        es_muro="si" if muro >= 25 else "no", bandera=bandera, motivo=motivo,
        marca_manual="")


def _tags(artista="", titulo="", genero="", bpm="", key="", comentario="", claves=None):
    return {"artista": artista, "titulo": titulo, "genero": genero, "bpm": bpm,
            "key": key, "comentario": comentario, "claves": claves or [],
            "formato": "wave", "n_tags": len(claves or [])}


def _por_campo(cambios):
    return {c.campo: c for c in cambios}


# --- Qué se escribe y qué no ----------------------------------------------------------------


def test_txxx_musiflix_se_escribe_siempre():
    """Responde '¿este archivo pasó por el pipeline?', que hoy no tiene respuesta."""
    for bandera in ("ok", "sospechoso"):
        c = _por_campo(planificar(_prop(), _cal(bandera=bandera), _tags()))
        assert c["TXXX:MusiFlix"].accion == ESCRIBIR


def test_el_registro_es_json_valido_y_completo():
    d = json.loads(registro_musiflix(_cal(corte=16.0, muro=66.5)))
    assert d["corte_khz"] == 16.0 and d["muro_db"] == 66.5
    assert d["bandera"] == "ok" and d["v"] and d["fecha"]


def test_comm_solo_en_sospechosos():
    """Un aviso que aparece en el 100% de los archivos no es un aviso."""
    assert _por_campo(planificar(_prop(), _cal("ok"), _tags()))["comentario"].accion == OMITIR
    c = _por_campo(planificar(_prop(), _cal("sospechoso"), _tags()))["comentario"]
    assert c.accion == ESCRIBIR and "MusiFlix" in c.despues


def test_comm_no_pisa_un_comentario_ajeno():
    """El '1A - Energy 7' de Mixed In Key se queda; el dato igual va a TXXX."""
    cambios = planificar(_prop(), _cal("sospechoso"), _tags(comentario="1A - Energy 7"))
    c = _por_campo(cambios)
    assert c["comentario"].accion == PRESERVAR
    assert c["comentario"].despues == "1A - Energy 7"
    assert c["TXXX:MusiFlix"].accion == ESCRIBIR      # el dato no se pierde


def test_genero_nunca_se_escribe():
    c = _por_campo(planificar(_prop(), _cal(), _tags()))["genero"]
    assert c.accion == OMITIR and c.despues == ""


def test_no_se_escribe_ningun_grupo_de_duplicados():
    """El grupo es propiedad de la biblioteca, no del track: vive en el CSV de 5.2."""
    campos = {c.campo for c in planificar(_prop(), _cal(), _tags())}
    assert not any("grupo" in c or "duplicad" in c for c in campos)


# --- Preservar lo que ya estaba ---------------------------------------------------------------


def test_no_pisa_un_artista_existente():
    c = _por_campo(planificar(_prop(), _cal(), _tags(artista="Otro")))["artista"]
    assert c.accion == PRESERVAR and c.despues == "Otro"


def test_escribe_artista_solo_si_esta_vacio():
    c = _por_campo(planificar(_prop(), _cal(), _tags()))["artista"]
    assert c.accion == ESCRIBIR and c.despues == "Fran Perrotta"


def test_sin_propuesta_queda_vacio_no_unknown():
    c = _por_campo(planificar(_prop(artista="", titulo=""), _cal(), _tags()))
    assert c["artista"].accion == VACIO and c["artista"].despues == ""
    assert "unknown" not in c["artista"].despues.lower()


def test_key_externa_se_preserva_sin_convertir_a_camelot():
    """'Am' es una de las dos referencias independientes: convertirla borra esa evidencia."""
    c = _por_campo(planificar(_prop(), _cal(), _tags(key="Am")))["key"]
    assert c.accion == PRESERVAR and c.despues == "Am"
    assert "sin convertir a Camelot" in c.motivo


def test_key_de_mixed_in_key_se_marca_como_mik():
    cambios = planificar(_prop(), _cal(),
                         _tags(key="1A", comentario="1A - Energy 7",
                               claves=["TXXX:EnergyLevel", "GEOB:Key"]))
    c = _por_campo(cambios)["key"]
    assert c.accion == PRESERVAR
    assert "Mixed In Key" in c.motivo and "otro algoritmo" in c.motivo


def test_bpm_existente_se_preserva():
    c = _por_campo(planificar(_prop(), _cal(), _tags(bpm="155")))["bpm"]
    assert c.accion == PRESERVAR and c.despues == "155"


# --- Escritura sobre la copia -------------------------------------------------------------------


def _wav(ruta, sr=22050, dur=2.0):
    import soundfile as sf
    rng = np.random.default_rng(0)
    sf.write(ruta, (0.1 * rng.standard_normal(int(sr * dur))).astype(np.float32), sr)
    return ruta


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def test_el_original_no_se_toca(tmp_path):
    """La regla del proyecto: los originales no se abren para escritura."""
    origen = _wav(tmp_path / "orig.wav")
    antes = _sha(origen)
    escribir_en_copia(str(origen), tmp_path / "salida",
                      planificar(_prop(), _cal(), _tags()))
    assert _sha(origen) == antes


def test_la_copia_wav_sigue_siendo_legible(tmp_path):
    """Regresión: escribir ID3 pelado sobre un WAV rompe el RIFF.

    La primera versión de este módulo dejó 26 de 57 copias ilegibles por eso —
    `tagger.py:55-58` ya documentaba la trampa. Hay que usar el contenedor WAVE.
    """
    from mutagen import File

    origen = _wav(tmp_path / "orig.wav")
    copia = escribir_en_copia(str(origen), tmp_path / "salida",
                              planificar(_prop(), _cal(), _tags()))
    m = File(str(copia))
    assert m is not None, "la copia quedó ilegible: se corrompió el WAV"
    assert type(m).__name__ == "WAVE"


def test_la_copia_lleva_los_tags(tmp_path):
    from mutagen import File

    origen = _wav(tmp_path / "orig.wav")
    copia = escribir_en_copia(str(origen), tmp_path / "salida",
                              planificar(_prop(), _cal("sospechoso"), _tags()))
    tags = File(str(copia)).tags
    # Que el frame EXISTA no dice nada: pasaría con el artista equivocado. Se comparan
    # los valores contra lo que el plan dijo que iba a escribir.
    assert str(tags["TPE1"]) == "Fran Perrotta"
    assert str(tags["TIT2"]) == "Static Bow"

    registro = json.loads(str(next(v for k, v in tags.items()
                                   if str(k).startswith("TXXX:MusiFlix"))))
    assert registro["corte_khz"] == 20.1 and registro["bandera"] == "sospechoso"
    assert registro["v"] and registro["fecha"]

    comentario = str(next(v for k, v in tags.items() if str(k).startswith("COMM")))
    assert comentario.startswith("MusiFlix"), comentario
    assert "[bitrate] ok" in comentario      # el motivo real de la calidad, no un texto fijo


def test_lo_preservado_no_se_reescribe_en_la_copia(tmp_path):
    """Si el plan dice preservar, el valor no aparece entre los que se escriben."""
    origen = _wav(tmp_path / "orig.wav")
    cambios = planificar(_prop(), _cal(), _tags(artista="Ya Estaba"))
    copia = escribir_en_copia(str(origen), tmp_path / "salida", cambios)
    from mutagen import File
    tags = File(str(copia)).tags
    # No se escribió TPE1 porque estaba preservado; el archivo copiado no lo trae.
    assert "TPE1" not in {str(k) for k in tags}


@pytest.mark.parametrize("nombre,contenedor", [("a.wav", "WAVE"), ("a.flac", "FLAC"),
                                               ("a.ogg", "OggVorbis")])
def test_la_copia_queda_legible_en_cada_contenedor(tmp_path, nombre, contenedor):
    """Que el archivo LLEGUE no alcanza: hay que poder ABRIRLO después de taggearlo.

    Esta es exactamente la forma que dejó pasar los 26 WAVs corruptos de #5.3: el test
    comprobaba `copia.exists()` y el nombre, y un WAV destrozado por un ID3 pelado existe
    y se llama igual. Acá se abre la copia con mutagen y se exige un objeto del
    contenedor correcto, no solo "algo".
    """
    import soundfile as sf
    from mutagen import File

    origen = tmp_path / nombre
    rng = np.random.default_rng(0)
    sf.write(origen, (0.1 * rng.standard_normal(22050 * 2)).astype(np.float32), 22050)

    copia = escribir_en_copia(str(origen), tmp_path / "salida",
                              planificar(_prop(), _cal(), _tags()))
    assert copia.exists() and copia.name == nombre

    m = File(str(copia))
    assert m is not None, f"{nombre}: la copia quedó ilegible después de taggearla"
    assert type(m).__name__ == contenedor, (
        f"{nombre}: se esperaba {contenedor} y mutagen leyó {type(m).__name__} — "
        "el contenedor se corrompió al escribir")
    # Y que el audio siga ahí: un header sano con el stream roto también engaña.
    assert m.info.length > 1.0, f"{nombre}: la copia perdió el audio"


def test_la_copia_mp3_queda_legible(tmp_path):
    """El MP3 va aparte porque no se puede generar con soundfile (hace falta un encoder).

    Se arma a mano una tira de frames MPEG-1 Layer III válidos: mutagen necesita varios
    consecutivos para sincronizar, no alcanza con un header suelto.
    """
    from mutagen import File

    # MPEG-1 Layer III, 128 kbps, 44100 Hz, sin padding, mono.
    # Tamaño de frame = 144 * 128000 / 44100 = 417 bytes.
    cabecera = b"\xff\xfb\x90\x00"
    frame = cabecera + b"\x00" * (417 - len(cabecera))
    origen = tmp_path / "a.mp3"
    origen.write_bytes(frame * 40)

    copia = escribir_en_copia(str(origen), tmp_path / "salida",
                              planificar(_prop(), _cal(), _tags()))
    m = File(str(copia))
    assert m is not None, "la copia mp3 quedó ilegible después de taggearla"
    assert type(m).__name__ == "MP3"
    assert str(m.tags["TPE1"]) == "Fran Perrotta"
