"""Tests del parseo de nombres (tarea 5.3). Todo string, sin audio."""
from calidad.normalizar_tags import (
    DIRECTA,
    INVERTIDA,
    SIN_PARTIR,
    Propuesta,
    limpiar,
    partir,
    separar_version,
)
from calidad.normalizar_tags import analizar as _analizar

# --- Partir --------------------------------------------------------------------------------


def test_parte_por_el_primer_separador():
    assert partir("Boltcore - Try To Make It - JFM") == ("Boltcore", "Try To Make It - JFM")


def test_no_parte_sin_espacios_alrededor():
    """'kylian-dictador' es un nombre, no 'kylian' de 'dictador'."""
    assert partir("kylian-dictador") is None


def test_no_parte_sin_separador():
    assert partir("looopiiiii") is None


def test_saca_el_numero_de_catalogo_de_adelante():
    """En 'ONYX002 - Artista - Tema' el primer guion no separa artista de título."""
    assert partir("ONYX002 - 4000Hz - Real Love") == ("4000Hz", "Real Love")
    assert partir("001 - Paralich - Gasolina") == ("Paralich", "Gasolina")


def test_no_parte_si_un_lado_queda_muy_corto():
    assert partir("A - B") is None


def test_acepta_guion_largo():
    assert partir("Artista – Tema") == ("Artista", "Tema")


# --- Versión vs artista ----------------------------------------------------------------------


def test_separa_la_version_entre_parentesis():
    assert separar_version("Fran Perrotta (Original mix)") == ("Fran Perrotta", "Original mix")


def test_separa_la_version_suelta():
    assert separar_version("fran perrotta edit") == ("fran perrotta", "edit")


def test_no_toca_un_parentesis_que_no_es_version():
    """'(ONYX002)' es un número de catálogo, no una versión: se queda donde está."""
    nombre, version = separar_version("Real Love (ONYX002)")
    assert nombre == "Real Love (ONYX002)" and version == ""


def test_artista_sin_version_queda_igual():
    assert separar_version("Skrillex & Bobby Raps") == ("Skrillex & Bobby Raps", "")


# --- Limpieza ---------------------------------------------------------------------------------


def test_limpia_el_sitio_de_descarga():
    limpio, motivos = limpiar("Talk To Em myfreemp3.vip ")
    assert limpio == "Talk To Em"
    assert "descarga" in motivos


def test_limpia_cut_y_sufijo_de_copia():
    assert limpiar("Talk To Me VIP (cut)")[0] == "Talk To Me VIP"
    assert limpiar("Think about me (1)")[0] == "Think about me"


def test_no_toca_un_texto_limpio():
    limpio, motivos = limpiar("Try To Make It")
    assert limpio == "Try To Make It" and motivos == ""


def test_no_borra_un_parentesis_legitimo():
    assert limpiar("Siren (Blanke Remix)")[0] == "Siren (Blanke Remix)"


# --- Orientación por frecuencia ----------------------------------------------------------------


def _props(nombres, tmp_path) -> dict[str, Propuesta]:
    """Crea archivos vacíos con esos nombres y analiza. Devuelve {nombre_base: Propuesta}.

    El análisis solo mira el nombre y los tags; con archivos vacíos alcanza, porque acá se
    prueba el parseo, no la lectura de audio.
    """
    rutas = []
    for n in nombres:
        p = tmp_path / n
        p.write_bytes(b"")
        rutas.append(str(p))
    return {p.nombre_base: p for p in _analizar(rutas)}


def test_un_artista_que_se_repite_a_la_derecha_invierte(tmp_path):
    """El caso Fran Perrotta: el mismo nombre a la derecha en varios archivos."""
    props = _props([
        "Antu Inan - Fran Perrotta (Original mix).wav",
        "Static Bow - Fran Perrotta (Original mix).wav",
        "Velvet hours - Fran Perrotta.wav",
    ], tmp_path)
    for p in props.values():
        assert p.orientacion == INVERTIDA
        assert p.artista_propuesto == "Fran Perrotta"


def test_la_version_pasa_al_titulo_al_invertir(tmp_path):
    props = _props([
        "Antu Inan - Fran Perrotta (Original mix).wav",
        "Static Bow - Fran Perrotta (Original mix).wav",
        "Velvet hours - Fran Perrotta.wav",
    ], tmp_path)
    p = props["Antu Inan - Fran Perrotta (Original mix)"]
    assert p.artista_propuesto == "Fran Perrotta"
    assert p.titulo_propuesto == "Antu Inan (Original mix)"


def test_sin_repeticion_se_asume_izquierda_igual_artista(tmp_path):
    props = _props(["Jimmy Peel - Drop that.wav", "SMBG - BODY.wav"], tmp_path)
    for p in props.values():
        assert p.orientacion == DIRECTA
        assert p.revisar == "no"


def test_invierte_por_el_sufijo_no_por_parecido_de_nombre(tmp_path):
    """'I Just Landed - Franco Perrotta': se invierte por el SUFIJO, no por parecerse a 'Fran'.

    La señal es que '(Original mix)' acompañó a inversiones ya detectadas por nombre y a
    ningún directo. Invertir por similitud de nombre sería adivinar; por frecuencia del
    sufijo es el mismo tipo de evidencia que la orientación por nombre.
    """
    props = _props([
        "Antu Inan - Fran Perrotta (Original mix).wav",
        "Static Bow - Fran Perrotta (Original mix).wav",
        "I Real - Fran Perrotta (Original mix).wav",
        "I Just Landed - Franco Perrotta (Original mix).wav",
    ], tmp_path)
    p = props["I Just Landed - Franco Perrotta (Original mix)"]
    assert p.orientacion == INVERTIDA
    assert p.artista_propuesto == "Franco Perrotta"   # NO se fusiona con 'Fran Perrotta'
    assert p.revisar == "si"                          # queda marcado igual
    assert "sufijo" in p.motivo


def test_un_sufijo_que_no_acompana_inversiones_no_invierte(tmp_path):
    """'(Extended Mix)' aparece en un solo archivo, directo: no alcanza como señal."""
    props = _props([
        "Antu Inan - Fran Perrotta (Original mix).wav",
        "Static Bow - Fran Perrotta (Original mix).wav",
        "Chris Lake - LA NOCHE (Extended Mix).wav",
    ], tmp_path)
    p = props["Chris Lake - LA NOCHE (Extended Mix)"]
    assert p.orientacion == DIRECTA
    assert p.artista_propuesto == "Chris Lake"


def test_varios_separadores_se_marcan(tmp_path):
    props = _props(["Boltcore - Try To Make It - JFM.wav"], tmp_path)
    p = props["Boltcore - Try To Make It - JFM"]
    assert p.revisar == "si"
    assert "más de un separador" in p.motivo


def test_sin_separador_queda_vacio_no_unknown(tmp_path):
    """Regla §6: un dato que no se pudo determinar se deja vacío."""
    props = _props(["looopiiiii.wav"], tmp_path)
    p = props["looopiiiii"]
    assert p.orientacion == SIN_PARTIR
    assert p.artista_propuesto == "" and p.titulo_propuesto == ""
    assert "unknown" not in (p.artista_propuesto + p.titulo_propuesto).lower()


def test_canoniza_a_la_grafia_mas_frecuente():
    from calidad.normalizar_tags import canonizar
    mapa = canonizar(["Fran Perrotta"] * 5 + ["fran perrotta"] * 2)
    assert mapa == {"fran perrotta": "Fran Perrotta"}


def test_desempata_contra_el_all_caps():
    """PARALICH vs Paralich, uno cada uno: gana el que no está todo en mayúsculas."""
    from calidad.normalizar_tags import canonizar
    assert canonizar(["PARALICH", "Paralich"]) == {"PARALICH": "Paralich"}


def test_una_sola_grafia_no_se_toca():
    from calidad.normalizar_tags import canonizar
    assert canonizar(["Skrillex", "Sub Focus"]) == {}
