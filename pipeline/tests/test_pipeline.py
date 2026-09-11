"""Tests del pipeline revisar → aplicar (tarea 5.6)."""
import json

import pytest

from pipeline import config
from pipeline.aplicar import aplicar, escribir_xml_rekordbox, leer_decisiones
from pipeline.reporte import (
    APROBADO,
    DESCARTADO,
    PENDIENTE,
    Fila,
    estado_por_defecto,
    generar,
)

# --- Configuración de destinos --------------------------------------------------------------


def test_itunes_sin_configurar_falla_con_mensaje_util(monkeypatch):
    """No inventa una carpeta ni escribe en el cwd."""
    monkeypatch.delenv(config.VAR_ITUNES, raising=False)
    with pytest.raises(config.DestinoNoConfigurado) as e:
        config.itunes_dir()
    assert config.VAR_ITUNES in str(e.value)
    assert "No se inventa" in str(e.value)


def test_itunes_que_no_existe_falla(monkeypatch, tmp_path):
    monkeypatch.setenv(config.VAR_ITUNES, str(tmp_path / "no_esta"))
    with pytest.raises(config.DestinoNoConfigurado) as e:
        config.itunes_dir()
    assert "no existe" in str(e.value)


def test_itunes_configurado_y_existente(monkeypatch, tmp_path):
    d = tmp_path / "itunes"
    d.mkdir()
    monkeypatch.setenv(config.VAR_ITUNES, str(d))
    assert config.itunes_dir() == d


def test_el_staging_si_tiene_default(monkeypatch, tmp_path):
    """El staging es interno del pipeline: puede tener default y crearse solo."""
    monkeypatch.setenv(config.VAR_STAGING, str(tmp_path / "stg"))
    assert config.staging_dir().exists()


# --- Estado por defecto ------------------------------------------------------------------------


def test_ok_se_aprueba_solo():
    assert estado_por_defecto("ok", 0) == APROBADO


def test_sospechoso_queda_pendiente():
    """Nunca aprobado por defecto algo que necesita criterio."""
    assert estado_por_defecto("sospechoso", 0) == PENDIENTE


def test_duplicado_queda_pendiente_aunque_sea_ok():
    assert estado_por_defecto("ok", 3) == PENDIENTE


# --- Reporte HTML --------------------------------------------------------------------------------


def _fila(**kw):
    base = dict(archivo="a.wav", ruta_staging="C:/stg/a.wav", ruta_original="C:/orig/a.wav",
                artista="Fran Perrotta", titulo="Static Bow", duracion_s=346.0,
                corte_khz=21.8, bandera="ok", muro_db=4.0, bpm=128.04,
                camelot="2A", clasica="D#m", confianza=1.0, acuerdo="3/3")
    base.update(kw)
    f = Fila(**base)
    f.estado = estado_por_defecto(f.bandera, f.grupo_id)
    return f


def test_el_html_es_autocontenido(tmp_path):
    """Un archivo, sin red, sin framework: abre con doble clic y anda offline."""
    destino = generar([_fila()], tmp_path, tmp_path / "r.html")
    h = destino.read_text(encoding="utf-8")
    assert "http://" not in h and "https://" not in h
    assert "<style>" in h and "<script>" in h        # todo embebido
    assert "cdn" not in h.lower()


def test_el_bpm_va_con_un_decimal(tmp_path):
    """Redondear a entero es mentir (§6)."""
    h = generar([_fila(bpm=128.04)], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert "128.0" in h
    assert ">128<" not in h


def test_muestra_camelot_y_clasica(tmp_path):
    h = generar([_fila()], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert "2A" in h and "D#m" in h


def test_confianza_baja_va_atenuada_y_con_interrogante(tmp_path):
    h = generar([_fila(confianza=0.33, acuerdo="1/3")], tmp_path,
                tmp_path / "r.html").read_text(encoding="utf-8")
    assert "dudoso" in h and "?" in h


def test_confianza_alta_no_va_atenuada(tmp_path):
    h = generar([_fila(confianza=1.0)], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert "<b>2A · D#m</b>" in h


def test_sin_artista_no_dice_unknown(tmp_path):
    h = generar([_fila(artista="", titulo="")], tmp_path,
                tmp_path / "r.html").read_text(encoding="utf-8")
    assert "(sin artista)" in h and "unknown" not in h.lower()


def test_la_preescucha_no_hace_autoplay(tmp_path):
    h = generar([_fila()], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert 'preload="none"' in h
    assert "autoplay" not in h
    assert "volume=0.5" in h.replace(" ", "")      # nunca a volumen alto


def test_los_duplicados_se_agrupan(tmp_path):
    filas = [_fila(archivo="a.wav", grupo_id=1), _fila(archivo="b.wav", grupo_id=1),
             _fila(archivo="c.wav")]
    for f in filas:
        f.estado = estado_por_defecto(f.bandera, f.grupo_id)
    h = generar(filas, tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert 'class="grupo"' in h
    assert "grupo 1" in h


def test_el_aviso_de_los_cues_esta_en_el_reporte(tmp_path):
    """Que no se busque después por qué 'se perdieron los cues'."""
    h = generar([_fila()], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert "CUE POINTS" in h and "Rekordbox" in h


# --- Aplicar ----------------------------------------------------------------------------------


def _decisiones(tmp_path, estados):
    stg = tmp_path / "stg"
    stg.mkdir(exist_ok=True)
    ds = []
    for i, e in enumerate(estados):
        p = stg / f"t{i}.wav"
        p.write_bytes(b"RIFF0000WAVE")
        ds.append({"archivo": p.name, "ruta_staging": str(p), "artista": "A",
                   "titulo": f"T{i}", "grupo_id": 0, "estado": e})
    return ds


def test_solo_se_copia_lo_aprobado(tmp_path):
    itunes = tmp_path / "itunes"
    itunes.mkdir()
    ds = _decisiones(tmp_path, [APROBADO, APROBADO, DESCARTADO, PENDIENTE])
    res = aplicar(ds, itunes, tmp_path / "rb.xml")
    assert len(res.copiados) == 2
    assert res.omitidos == {DESCARTADO: 1, PENDIENTE: 1}
    assert sorted(p.name for p in itunes.iterdir()) == ["t0.wav", "t1.wav"]


def test_lo_pendiente_no_llega_a_itunes(tmp_path):
    """La regla: si no lo miraste, no sale."""
    itunes = tmp_path / "itunes"
    itunes.mkdir()
    aplicar(_decisiones(tmp_path, [PENDIENTE, PENDIENTE]), itunes, tmp_path / "rb.xml")
    assert list(itunes.iterdir()) == []


def test_dry_run_no_copia(tmp_path):
    itunes = tmp_path / "itunes"
    itunes.mkdir()
    res = aplicar(_decisiones(tmp_path, [APROBADO]), itunes, tmp_path / "rb.xml",
                  copiar=False)
    assert res.copiados == ["t0.wav"]
    assert list(itunes.iterdir()) == []


def test_un_aprobado_sin_archivo_se_reporta(tmp_path):
    itunes = tmp_path / "itunes"
    itunes.mkdir()
    ds = [{"archivo": "fantasma.wav", "ruta_staging": str(tmp_path / "no_esta.wav"),
           "artista": "", "titulo": "", "grupo_id": 0, "estado": APROBADO}]
    res = aplicar(ds, itunes, tmp_path / "rb.xml")
    assert res.copiados == [] and len(res.faltantes) == 1


# --- XML de Rekordbox ---------------------------------------------------------------------------


def test_el_xml_lo_lee_nuestro_propio_parser(tmp_path):
    """Se exporta en el formato que ya sabemos auditar (ground_truth.rekordbox)."""
    from ground_truth.rekordbox import parsear

    itunes = tmp_path / "itunes"
    itunes.mkdir()
    ds = _decisiones(tmp_path, [APROBADO, APROBADO])
    xml = tmp_path / "rb.xml"
    aplicar(ds, itunes, xml)
    tracks = parsear(xml)
    assert len(tracks) == 2
    assert {t["name"] for t in tracks} == {"T0", "T1"}


def test_el_xml_apunta_a_la_carpeta_de_itunes_no_al_staging(tmp_path):
    """El XML tiene que apuntar a donde quedó el archivo, no a la copia intermedia."""
    from ground_truth.rekordbox import parsear

    itunes = tmp_path / "itunes"
    itunes.mkdir()
    xml = tmp_path / "rb.xml"
    aplicar(_decisiones(tmp_path, [APROBADO]), itunes, xml)
    loc = parsear(xml)[0]["location"]
    assert "itunes" in loc.lower() and "stg" not in loc.lower()


def test_el_xml_solo_lleva_lo_copiado(tmp_path):
    from ground_truth.rekordbox import parsear

    itunes = tmp_path / "itunes"
    itunes.mkdir()
    xml = tmp_path / "rb.xml"
    aplicar(_decisiones(tmp_path, [APROBADO, DESCARTADO, PENDIENTE]), itunes, xml)
    assert len(parsear(xml)) == 1


def test_xml_vacio_si_no_hay_aprobados(tmp_path):
    itunes = tmp_path / "itunes"
    itunes.mkdir()
    xml = tmp_path / "rb.xml"
    aplicar(_decisiones(tmp_path, [PENDIENTE]), itunes, xml)
    from ground_truth.rekordbox import parsear
    assert parsear(xml) == []


# --- Formato del JSON de decisiones ---------------------------------------------------------------


def test_lee_el_json_con_envoltorio(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"version": 1, "decisiones": [{"estado": APROBADO}]}),
                 encoding="utf-8")
    assert leer_decisiones(p) == [{"estado": APROBADO}]


def test_tolera_un_json_que_sea_la_lista_pelada(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps([{"estado": APROBADO}]), encoding="utf-8")
    assert leer_decisiones(p) == [{"estado": APROBADO}]


def test_el_xml_no_falla_con_titulo_vacio(tmp_path):
    """Sin título se usa el nombre del archivo, no 'Unknown'."""
    from ground_truth.rekordbox import parsear

    itunes = tmp_path / "itunes"
    itunes.mkdir()
    xml = tmp_path / "rb.xml"
    escribir_xml_rekordbox(
        [{"ruta_staging": str(tmp_path / "sin_titulo.wav"), "titulo": "", "artista": ""}],
        xml, itunes)
    t = parsear(xml)[0]
    assert t["name"] == "sin_titulo" and t["artist"] == ""
