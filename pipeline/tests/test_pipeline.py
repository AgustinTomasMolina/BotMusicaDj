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


def test_el_staging_usa_la_variable_configurada(monkeypatch, tmp_path):
    """Que exista un directorio no dice que sea EL configurado: pasaría ignorando la var."""
    pedido = tmp_path / "stg_elegido"
    monkeypatch.setenv(config.VAR_STAGING, str(pedido))
    obtenido = config.staging_dir()
    assert obtenido == pedido, f"se pidió {pedido} y devolvió {obtenido}"
    assert obtenido.is_dir()


def test_el_staging_tiene_default_si_no_esta_configurado(monkeypatch):
    """Es interno del pipeline: puede crearse solo, pero dentro del repo, no en el cwd."""
    monkeypatch.delenv(config.VAR_STAGING, raising=False)
    d = config.staging_dir()
    assert d.is_dir()
    assert config.BASE_DIR in d.parents, f"el default cayó fuera del repo: {d}"


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
    h = generar([_fila(confianza=0.33, acuerdo="1/3", tramos="2A|5A|9B")], tmp_path,
                tmp_path / "r.html").read_text(encoding="utf-8")
    # 'class="dudoso"' y no "dudoso" a secas: la definición CSS de .dudoso está siempre.
    assert '<span class="dudoso" title="acuerdo 1/3 entre tramos: 2A|5A|9B">2A · D#m ?</span>' in h


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


@pytest.fixture(scope="module")
def audio_tres_bloques(tmp_path_factory):
    """140 s: La menor · Do# mayor · La menor (igual que el de `motor/tests/test_analisis.py`).

    Dura más de 135 s para que `tono_consenso` arme 3 tramos, y los tramos NO coinciden:
    el acuerdo es 2/3. Con un audio corto las dos rutas darían acuerdo "" y la comparación
    pasaría aunque una no lo midiera."""
    import numpy as np
    import soundfile as sf

    from motor.sintetico import click_track

    sr = 22050
    partes = [click_track(128.0, dur=140.0 / 3, sr=sr, nota=n, modo=m, seed=i)[0]
              for i, (n, m) in enumerate((("A", "min"), ("C#", "maj"), ("A", "min")))]
    ruta = tmp_path_factory.mktemp("tres_bloques") / "tres_bloques.wav"
    sf.write(ruta, np.concatenate(partes).astype(np.float32), sr, subtype="FLOAT")
    return ruta


def test_pipeline_y_benchmark_dan_el_mismo_acuerdo_sobre_el_mismo_audio(audio_tres_bloques, tmp_path):
    """La confianza de la key (el acuerdo) tiene que ser la misma en las dos rutas: si el
    pipeline la calculara por su cuenta, el reporte podría marcar dudosa una key que el
    benchmark mide como unánime, o al revés."""
    from benchmark.analizar import analizar_uno
    from pipeline.revisar import procesar

    del_bench = analizar_uno(str(audio_tres_bloques))
    del_pipe = procesar([str(audio_tres_bloques)], tmp_path / "stg", progreso=False)[0]

    assert del_bench.acuerdo == "2/3", \
        f"el caso necesita acuerdo 2/3 en el benchmark, dio {del_bench.acuerdo!r}"
    assert del_pipe.camelot == del_bench.key_est == "3B", (
        f"key: pipeline {del_pipe.camelot}, benchmark {del_bench.key_est} (tono() da 3B)")
    assert del_pipe.acuerdo == del_bench.acuerdo, (
        f"acuerdo: pipeline {del_pipe.acuerdo!r}, benchmark {del_bench.acuerdo!r}")
    assert del_pipe.tramos == del_bench.tramos, (
        f"tramos: pipeline {del_pipe.tramos!r}, benchmark {del_bench.tramos!r}")


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


def test_con_tono_y_acuerdo_unanime_la_key_va_limpia_aunque_krumhansl_sea_bajo(tmp_path):
    """La confianza Krumhansl de tono() está medida como no predictiva: un 0.30 ahí no dice
    nada. Lo que decide es el acuerdo entre tramos, y 3/3 es unánime."""
    f = _fila(confianza=0.30, metodo="tono", acuerdo="3/3", tramos="2A|2A|2A")
    h = generar([f], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert "<b>2A · D#m</b>" in h, "con acuerdo unánime la key tiene que ir limpia"
    assert 'class="dudoso"' not in h, "la key unánime salió atenuada"
    assert "2A · D#m ?" not in h, "la key unánime salió con '?'"


def test_la_key_si_se_atenua_con_consenso(tmp_path):
    f = _fila(confianza=0.33, acuerdo="1/3", metodo="tono_consenso")
    h = generar([f], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert 'class="dudoso"' in h and "2A · D#m ?" in h


def test_con_tono_y_acuerdo_2_de_3_la_key_va_dudosa_con_el_porque(tmp_path):
    """El default (tono) con tramos en desacuerdo: la key de tono, atenuada, y en el tooltip
    el acuerdo y qué votó cada tramo, para que se vea por qué."""
    f = _fila(confianza=0.91, metodo="tono", acuerdo="2/3", tramos="2A|2A|9B")
    h = generar([f], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    esperado = '<span class="dudoso" title="acuerdo 2/3 entre tramos: 2A|2A|9B">2A · D#m ?</span>'
    assert esperado in h, "2/3 no salió atenuado con el acuerdo y los tramos en el tooltip"
    assert "<b>2A · D#m</b>" not in h, "2/3 salió además como key segura"


@pytest.mark.parametrize("acuerdo, motivo", [
    ("", "sin tramos para comparar: no hay acuerdo medido entre tramos "
         "(la etapa A lo deja vacío cuando el track dura menos de ~135 s)"),
    ("0/0", "sin tramos para comparar: acuerdo 0/0, el track no alcanzó para votar "
            "entre tramos disjuntos"),
])
def test_sin_tramos_para_comparar_la_key_va_dudosa(tmp_path, acuerdo, motivo):
    """Sin evidencia de confianza no se finge: dudosa, con el motivo en el tooltip."""
    f = _fila(confianza=0.91, metodo="tono", acuerdo=acuerdo, tramos="")
    h = generar([f], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    esperado = f'<span class="dudoso" title="{motivo}">2A · D#m ?</span>'
    assert esperado in h, f"acuerdo {acuerdo!r} no salió dudoso con el motivo 'sin tramos'"
    assert "<b>2A · D#m</b>" not in h, f"acuerdo {acuerdo!r} salió como key segura"


def test_ningun_tramo_pudo_votar_la_key_va_dudosa(tmp_path):
    """"0/3" con "?|?|?": hubo tramos pero ninguno dio una key. Cero evidencia de confianza."""
    f = _fila(confianza=0.91, metodo="tono", acuerdo="0/3", tramos="?|?|?")
    h = generar([f], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    esperado = '<span class="dudoso" title="acuerdo 0/3 entre tramos: ?|?|?">2A · D#m ?</span>'
    assert esperado in h, "0/3 sin votos no salió dudoso"
    assert "<b>2A · D#m</b>" not in h, "0/3 sin votos salió como key segura"


@pytest.mark.parametrize("acuerdo", ["2/3/4", "abc"])
def test_un_acuerdo_ilegible_no_tira_el_reporte_y_marca_solo_esa_fila(tmp_path, acuerdo):
    rota = _fila(archivo="roto.wav", camelot="2A", clasica="D#m", acuerdo=acuerdo, tramos="")
    sana = _fila(archivo="sano.wav", camelot="8A", clasica="Am", acuerdo="3/3",
                 tramos="8A|8A|8A")
    h = generar([rota, sana], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert '<span class="dudoso" title="acuerdo ilegible">2A · D#m ?</span>' in h, \
        f"el acuerdo {acuerdo!r} no marcó su fila como dudosa con el motivo"
    assert "<b>8A · Am</b>" in h, "la fila sana dejó de salir limpia por culpa de la rota"


def test_el_reporte_manda_el_acuerdo_y_el_comentario_a_decisiones_json(tmp_path):
    """Sin navegador no se puede correr bajar(): se fija el dato en la fila y la línea del JS
    que lo lee. Si cualquiera de las dos puntas falta, el acuerdo no llega a aplicar."""
    f = _fila(acuerdo="2/3", tramos="2A|2A|9B", comentario='1A - Energy 7 "x"')
    h = generar([f], tmp_path, tmp_path / "r.html").read_text(encoding="utf-8")
    assert 'data-acuerdo="2/3" data-comentario="1A - Energy 7 &quot;x&quot;"' in h
    assert "acuerdo:t.dataset.acuerdo||'', comentario:t.dataset.comentario||''," in h


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


# --- El XML tiene que llevar el DATO, no solo ser legible ---------------------------------


def _decision_completa(tmp_path, **kw):
    stg = tmp_path / "stg"
    stg.mkdir(exist_ok=True)
    p = stg / "track.wav"
    p.write_bytes(b"RIFF0000WAVE")
    d = {"archivo": p.name, "ruta_staging": str(p), "artista": "Fran Perrotta",
         "titulo": "Velvet hours", "grupo_id": 0, "estado": APROBADO,
         "bpm": 128.4, "camelot": "2A", "clasica": "D#m", "duracion_s": 346.5}
    d.update(kw)
    return d


def test_ida_y_vuelta_el_bpm_y_la_tonalidad_sobreviven(tmp_path):
    """El test que faltaba: afirma sobre el CONTENIDO, no sobre que el XML sea legible.

    El anterior comprobaba "el parser devuelve 3 tracks", que pasa igual con un XML que
    solo tenga nombres. Este falla si falta un atributo.
    """
    from ground_truth.rekordbox import parsear

    itunes = tmp_path / "itunes"
    itunes.mkdir()
    xml = tmp_path / "rb.xml"
    aplicar([_decision_completa(tmp_path)], itunes, xml)

    t = parsear(xml)[0]
    assert t["bpm"] == 128.4, "el AverageBpm no volvió del XML"
    assert t["tonality"] == "D#m", "la Tonality no volvió del XML"
    assert t["camelot"] == "2A", "la Tonality no se convierte de vuelta al Camelot original"
    assert t["duration_s"] == 346, "el TotalTime no volvió del XML"
    assert t["artist"] == "Fran Perrotta" and t["name"] == "Velvet hours"


def test_el_bpm_no_se_redondea_a_entero(tmp_path):
    """128.4 tiene que volver 128.4, no 128 (§6)."""
    from ground_truth.rekordbox import parsear

    itunes = tmp_path / "itunes"
    itunes.mkdir()
    xml = tmp_path / "rb.xml"
    aplicar([_decision_completa(tmp_path, bpm=128.4)], itunes, xml)
    assert parsear(xml)[0]["bpm"] == 128.4


def test_las_24_tonalidades_hacen_ida_y_vuelta(tmp_path):
    """El mapa del motor y el del parser de Rekordbox tienen que cerrar en las 24.

    Si alguien toca uno de los dos y no el otro, esto lo agarra.
    """
    from ground_truth.rekordbox import a_camelot
    from motor.tonalidad import camelot_a_clasica

    for n in range(1, 13):
        for lado in ("A", "B"):
            cam = f"{n}{lado}"
            clasica = camelot_a_clasica(cam)
            assert clasica, f"{cam} no tiene notación clásica"
            assert a_camelot(clasica) == cam, (
                f"{cam} → '{clasica}' → {a_camelot(clasica)}: no cierra")


def test_un_valor_que_no_se_pudo_determinar_se_OMITE(tmp_path):
    """Nada de 0 ni placeholders: Rekordbox no pregunta, se queda con lo que le den."""
    import xml.etree.ElementTree as ET

    itunes = tmp_path / "itunes"
    itunes.mkdir()
    xml = tmp_path / "rb.xml"
    aplicar([_decision_completa(tmp_path, bpm=0, camelot="", clasica="", duracion_s=0,
                                artista="")], itunes, xml)
    track = ET.parse(xml).getroot().find("COLLECTION").find("TRACK")
    for attr in ("AverageBpm", "Tonality", "TotalTime", "Artist"):
        assert attr not in track.attrib, f"{attr} se escribió con un valor inventado"
    assert track.get("Location")          # lo que sí se sabe, se escribe


def test_la_tonalidad_se_deriva_del_camelot_si_falta_la_clasica(tmp_path):
    from ground_truth.rekordbox import parsear

    itunes = tmp_path / "itunes"
    itunes.mkdir()
    xml = tmp_path / "rb.xml"
    aplicar([_decision_completa(tmp_path, clasica="")], itunes, xml)
    assert parsear(xml)[0]["camelot"] == "2A"


def test_todavia_no_se_inventan_cues(tmp_path):
    """Los POSITION_MARK llegan con #5.4. Un cue en el lugar equivocado se dispara en vivo."""
    import xml.etree.ElementTree as ET

    itunes = tmp_path / "itunes"
    itunes.mkdir()
    xml = tmp_path / "rb.xml"
    aplicar([_decision_completa(tmp_path)], itunes, xml)
    assert list(ET.parse(xml).getroot().iter("POSITION_MARK")) == []


# --- La duda de la key viaja a Rekordbox en Comments ---------------------------------------


def _track_xml(tmp_path, **kw):
    import xml.etree.ElementTree as ET

    itunes = tmp_path / "itunes"
    itunes.mkdir(exist_ok=True)
    xml = tmp_path / "rb.xml"
    aplicar([_decision_completa(tmp_path, **kw)], itunes, xml)
    return ET.parse(xml).getroot().find("COLLECTION").find("TRACK")


def test_key_unanime_no_escribe_comments(tmp_path):
    t = _track_xml(tmp_path, acuerdo="3/3", comentario="")
    assert "Comments" not in t.attrib, f"unánime escribió Comments={t.get('Comments')!r}"
    assert t.get("Tonality") == "D#m"


def test_key_unanime_con_comentario_previo_tampoco_escribe_comments(tmp_path):
    t = _track_xml(tmp_path, acuerdo="3/3", comentario="1A - Energy 7")
    assert "Comments" not in t.attrib, f"unánime escribió Comments={t.get('Comments')!r}"


def test_key_2_de_3_deja_el_acuerdo_en_comments_y_la_tonality_igual(tmp_path):
    t = _track_xml(tmp_path, acuerdo="2/3", comentario="")
    assert t.get("Comments") == "key 2/3"
    assert t.get("Tonality") == "D#m", "la key dudosa se tiene que escribir igual"


@pytest.mark.parametrize("acuerdo", ["", "0/3", "0/0", "abc"])
def test_key_sin_evidencia_deja_key_interrogacion(tmp_path, acuerdo):
    t = _track_xml(tmp_path, acuerdo=acuerdo, comentario="")
    assert t.get("Comments") == "key ?", f"acuerdo {acuerdo!r} → Comments={t.get('Comments')!r}"
    assert t.get("Tonality") == "D#m"


def test_comentario_previo_se_conserva_y_se_agrega_la_nota(tmp_path):
    t = _track_xml(tmp_path, acuerdo="1/3",
                   comentario="MusiFlix · revisar · [bitrate inflado] muro a 16.0 kHz")
    assert t.get("Comments") == "MusiFlix · revisar · [bitrate inflado] muro a 16.0 kHz | key 1/3"


def test_decisiones_json_viejo_sin_acuerdo_no_escribe_comments(tmp_path):
    """Un decisiones.json bajado antes de este cambio no trae `acuerdo`: no se inventa."""
    d = _decision_completa(tmp_path, comentario="1A - Energy 7")
    assert "acuerdo" not in d
    t = _track_xml(tmp_path, comentario="1A - Energy 7")
    assert "Comments" not in t.attrib, f"JSON viejo escribió Comments={t.get('Comments')!r}"
    assert t.get("Tonality") == "D#m"


def test_sin_tonality_no_hay_nota_de_key(tmp_path):
    t = _track_xml(tmp_path, acuerdo="2/3", camelot="", clasica="")
    assert "Tonality" not in t.attrib and "Comments" not in t.attrib, t.attrib


def test_revisar_lleva_el_comentario_que_queda_en_el_tag(tmp_path):
    """El comentario ajeno del archivo tiene que llegar a la Fila, o aplicar no tiene qué
    conservar y la nota de la key lo taparía en Rekordbox."""
    import numpy as np
    import soundfile as sf

    from calidad.escribir_tags import escribir_campos
    from pipeline.revisar import procesar

    orig = tmp_path / "orig"
    orig.mkdir()
    ruta = orig / "con_comentario.wav"
    sf.write(ruta, (0.3 * np.sin(2 * np.pi * 220.0 * np.arange(22050 * 20) / 22050))
             .astype(np.float32), 22050)
    escribir_campos(ruta, {"comentario": "1A - Energy 7"})

    fila = procesar([str(ruta)], tmp_path / "stg", progreso=False)[0]
    assert fila.comentario == "1A - Energy 7", f"comentario {fila.comentario!r}"


def test_el_xml_no_vive_en_el_staging(monkeypatch, tmp_path):
    """Es la única vía de los cues: no puede estar en la carpeta que se pisa cada corrida."""
    monkeypatch.setenv(config.VAR_STAGING, str(tmp_path / "stg"))
    monkeypatch.delenv(config.VAR_REKORDBOX, raising=False)
    destino = config.rekordbox_xml("2026-09-11_120000")
    assert config.staging_dir() not in destino.parents
    assert "2026-09-11_120000" in destino.name
