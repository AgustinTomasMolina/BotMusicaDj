"""Tests de la auditoría de tags (tarea 5.3, primera mitad).

Todo lo que se prueba acá es lógica sobre strings: no hace falta un solo archivo de audio.
"""
from calidad.tags import (
    EXTERNO,
    INDETERMINADO,
    MIXED_IN_KEY,
    NINGUNO,
    PROPIO,
    SERATO,
    basura_en_nombre,
    detectar_basura,
    detectar_taggers,
    origen_de_key,
    partir_artista_titulo,
)

# --- Detección de quién escribió el tag ---------------------------------------------------


def test_nuestro_comentario_nos_delata():
    """server.py:704 arma 'MusiFlix · calidad A'."""
    assert PROPIO in detectar_taggers([], "MusiFlix · calidad A · corte 20.1 kHz")


def test_firma_de_serato():
    claves = ["TIT2", "GEOB:Serato Overview", "TXXX:SERATO_PLAYCOUNT"]
    assert SERATO in detectar_taggers(claves, "")


def test_firma_de_mixed_in_key_por_clave():
    assert MIXED_IN_KEY in detectar_taggers(["TXXX:EnergyLevel", "GEOB:Key"], "")


def test_firma_de_mixed_in_key_por_comentario():
    """MIK escribe el comentario con la forma exacta '1A - Energy 7'."""
    assert MIXED_IN_KEY in detectar_taggers([], "1A - Energy 7")


def test_un_camelot_de_mixed_in_key_NO_es_nuestro():
    """El caso BabaBass3000: '1A' con firma de MIK.

    La auditoría del 08/09 lo descartó por circular asumiendo que Camelot = nuestro, pero
    Mixed In Key también escribe Camelot. Es de otra herramienta, no de MusiFlix.
    """
    taggers = detectar_taggers(["TXXX:EnergyLevel", "GEOB:Key"], "1A - Energy 7")
    assert origen_de_key("1A", "1A - Energy 7", taggers) == MIXED_IN_KEY
    assert PROPIO not in taggers


def test_un_camelot_nuestro_si_es_nuestro():
    com = "MusiFlix · calidad B"
    assert origen_de_key("8A", com, detectar_taggers([], com)) == PROPIO


def test_camelot_sin_firma_de_nadie_es_indeterminado():
    """Sin evidencia no se afirma: ni propio ni externo."""
    assert origen_de_key("8A", "", []) == INDETERMINADO


def test_notacion_clasica_es_externa():
    """Nuestro tagger nunca escribe 'Am': si está, vino de afuera."""
    assert origen_de_key("Am", "", []) == EXTERNO
    assert origen_de_key("F#m", "", ["serato"]) == EXTERNO


def test_sin_key_no_hay_origen():
    assert origen_de_key("", "", []) == NINGUNO


def test_key_rara_es_indeterminada():
    assert origen_de_key("Hmmm", "", []) == INDETERMINADO


# --- Artista dentro del título -------------------------------------------------------------


def test_parte_artista_y_titulo():
    assert partir_artista_titulo("Boltcore - Try To Make It", "") == (
        "Boltcore", "Try To Make It")


def test_no_parte_si_ya_hay_un_artista_distinto():
    """Partir a ciegas rompería un título que legítimamente lleva guion."""
    assert partir_artista_titulo("Mi Tema - Parte 2", "Otro Artista") == ("", "")


def test_parte_si_el_artista_coincide_con_la_izquierda():
    assert partir_artista_titulo("Boltcore - Try To Make It", "boltcore") == (
        "Boltcore", "Try To Make It")


def test_sin_separador_no_propone_nada():
    assert partir_artista_titulo("Solo un titulo", "") == ("", "")


def test_toma_el_primer_separador():
    """'Boltcore - Try To Make It - JFM' → artista Boltcore, el resto es el título."""
    art, tit = partir_artista_titulo("Boltcore - Try To Make It - JFM", "")
    assert art == "Boltcore" and tit == "Try To Make It - JFM"


# --- Basura de origen ------------------------------------------------------------------------


def test_detecta_sitio_de_descarga_en_el_nombre():
    m = basura_en_nombre("Champion x Four Tet - Talk To Me VIP (cut) myfreemp3.vip .mp3")
    assert "myfreemp3" in m
    assert "(cut)" in m


def test_detecta_coletilla_de_youtube():
    assert "official video" in basura_en_nombre("Artista - Tema (Official Video).mp3")


def test_detecta_sufijo_de_copia():
    assert "copia" in basura_en_nombre("Think about me - edit (1).mp3")


def test_nombre_limpio_no_marca_nada():
    assert basura_en_nombre("Boltcore - Try To Make It.wav") == ""


def test_basura_en_tags_reporta_el_campo():
    motivos, campos = detectar_basura({
        "artista": "", "titulo": "Tema (Official Video)", "genero": "", "comentario": ""})
    assert "official video" in motivos
    assert campos == "titulo"


def test_tags_limpios_no_reportan_basura():
    motivos, campos = detectar_basura(
        {"artista": "Boltcore", "titulo": "Try To Make It", "genero": "Techno", "comentario": ""})
    assert motivos == "" and campos == ""
