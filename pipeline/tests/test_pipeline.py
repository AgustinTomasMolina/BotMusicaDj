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
                camelot="2A", clasica="D#m", confianza=1.0, acuerdo="3/3",
                metodo="tono_consenso")
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


# --- 5.66: pipeline y motor tienen que usar la MISMA tonalidad ---------------------------


def test_el_default_de_consenso_es_el_mismo_en_las_dos_rutas():
    """Barato y rápido: si alguien cambia un default y no el otro, esto falla.

    Que difieran significa dos tonalidades para el mismo track — una va al tag y al XML de
    Rekordbox, la otra es la que mide el benchmark y consume el scoring.
    """
    import inspect

    from benchmark.analizar import analizar_uno
    from pipeline.revisar import procesar

    d_bench = inspect.signature(analizar_uno).parameters["consenso"].default
    d_pipe = inspect.signature(procesar).parameters["consenso"].default
    assert d_bench == d_pipe, (
        f"el benchmark usa consenso={d_bench} y el pipeline consenso={d_pipe}: "
        "habría dos tonalidades distintas para el mismo track")
    assert d_pipe is False, ("el default tiene que seguir siendo tono(); cambiarlo "
                             "necesita el A/B contra ground truth que pide §5")


def test_pipeline_y_benchmark_dan_la_misma_tonalidad_sobre_el_mismo_audio(tmp_path):
    """La prueba de verdad: las dos rutas sobre el mismo archivo, mismo resultado."""
    import numpy as np
    import soundfile as sf

    from benchmark.analizar import analizar_uno
    from pipeline.revisar import procesar

    sr = 22050
    t = np.arange(sr * 100) / sr
    y = (0.4 * np.sin(2 * np.pi * 110.0 * t) + 0.2 * np.sin(2 * np.pi * 165.0 * t))
    ruta = tmp_path / "tono.wav"
    sf.write(ruta, y.astype(np.float32), sr)

    del_bench = analizar_uno(str(ruta))
    del_pipe = procesar([str(ruta)], tmp_path / "stg", progreso=False)[0]

    assert del_pipe.camelot == del_bench.key_est, (
        f"el pipeline dice {del_pipe.camelot} y el benchmark {del_bench.key_est}")
    assert del_pipe.metodo == del_bench.metodo == "tono"


# --- 5.65: sin nombre y no-track no pueden auto-aprobarse -------------------------------


def test_sin_nombre_no_se_auto_aprueba():
    """28 archivos entrarían a iTunes como '(sin título)' y habría que renombrarlos a mano."""
    from pipeline.reporte import MOT_SIN_NOMBRE, motivos_pendiente

    assert estado_por_defecto("ok", 0, "", "", 300.0) == PENDIENTE
    assert estado_por_defecto("ok", 0, "Artista", "", 300.0) == PENDIENTE
    assert MOT_SIN_NOMBRE in motivos_pendiente("ok", 0, "", "Titulo", 300.0)


def test_con_nombre_completo_si_se_aprueba():
    assert estado_por_defecto("ok", 0, "Artista", "Titulo", 300.0) == APROBADO


def test_lo_que_no_es_track_no_se_auto_aprueba():
    """El loop de 0:31 no va a iTunes."""
    from pipeline.reporte import MOT_NO_TRACK, motivos_pendiente

    assert estado_por_defecto("ok", 0, "A", "T", 31.0) == PENDIENTE
    assert MOT_NO_TRACK in motivos_pendiente("ok", 0, "A", "T", 31.0)
    assert estado_por_defecto("ok", 0, "A", "T", 96.0) == APROBADO


def test_un_archivo_puede_tener_varios_motivos():
    from pipeline.reporte import (
        MOT_DUPLICADO,
        MOT_NO_TRACK,
        MOT_SIN_NOMBRE,
        MOT_SOSPECHOSO,
        motivos_pendiente,
    )

    m = motivos_pendiente("sospechoso", 2, "", "", 20.0)
    assert set(m) == {MOT_SOSPECHOSO, MOT_DUPLICADO, MOT_SIN_NOMBRE, MOT_NO_TRACK}


def test_el_reporte_da_campos_editables_solo_donde_hacen_falta(tmp_path):
    from pipeline.reporte import MOT_SIN_NOMBRE

    con = _fila(artista="", titulo="", ruta_original="C:/orig/misterioso.wav")
    con.motivos = [MOT_SIN_NOMBRE]
    sin = _fila(artista="A", titulo="T")
    h = generar([con, sin], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert h.count('class="ed-artista"') == 1
    assert "misterioso.wav" in h        # el nombre original, que es lo único que hay


def test_la_key_no_se_atenua_cuando_el_metodo_no_da_confianza_util(tmp_path):
    """Con tono() la confianza es la correlación Krumhansl, medida como no predictiva."""
    f = _fila(confianza=0.30, metodo="tono")
    h = generar([f], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert "no predice fiabilidad" in h
    assert 'class="dudoso"' not in h      # la definicion CSS existe igual; lo que importa es el uso


def test_la_key_si_se_atenua_con_consenso(tmp_path):
    f = _fila(confianza=0.33, acuerdo="1/3", metodo="tono_consenso")
    h = generar([f], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert 'class="dudoso"' in h and "?" in h


def test_aplicar_escribe_lo_que_se_completo_a_mano(tmp_path):
    """Los valores editados en el reporte tienen que llegar al tag de la copia final."""
    import numpy as np
    import soundfile as sf
    from mutagen import File

    stg = tmp_path / "stg"
    stg.mkdir()
    p = stg / "misterioso.wav"
    sf.write(p, np.zeros(22050, dtype=np.float32), 22050)
    itunes = tmp_path / "itunes"
    itunes.mkdir()

    aplicar([{"archivo": p.name, "ruta_staging": str(p), "artista": "Fran Perrotta",
              "titulo": "Velvet hours", "editado": True, "grupo_id": 0,
              "estado": APROBADO}], itunes, tmp_path / "rb.xml")

    tags = File(str(itunes / p.name)).tags
    assert str(tags["TPE1"]) == "Fran Perrotta"
    assert str(tags["TIT2"]) == "Velvet hours"
