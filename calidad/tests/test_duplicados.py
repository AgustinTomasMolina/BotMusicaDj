"""Tests de la tarea 5.2 (duplicados).

Las compuertas y el ranking son lógica pura. La huella se prueba con señales sintéticas
construidas, no con afirmaciones sobre archivos reales (spec §5).
"""
import plistlib
from pathlib import Path

import numpy as np
import pytest

from calidad.corte_espectral import FilaCalidad
from calidad.duplicados import (
    CONSERVAR,
    DESCARTAR,
    Ficha,
    _puntaje_calidad,
    agrupar,
    decidir,
    duracion_compatible,
    huella,
    main,
    mismo_audio,
    normalizar_ruta,
    referencias_de_xml,
    referencias_itunes,
    referencias_rekordbox,
    similitud,
    unir_referencias,
)

SR = 22050


# --- Compuerta de duración ---------------------------------------------------------------


def test_duraciones_casi_iguales_son_compatibles():
    """Mismo audio en dos formatos: difiere el padding del encoder, milisegundos."""
    assert duracion_compatible(252.9, 253.2)
    assert duracion_compatible(122.5, 122.5)


def test_duraciones_distintas_no_son_duplicado():
    """El caso ProyectMoli: 8.8 s vs 11.4 s son dos tomas, no dos copias."""
    assert not duracion_compatible(8.8, 11.4)


def test_la_tolerancia_es_relativa_en_tracks_largos():
    assert duracion_compatible(600.0, 605.0)      # 5 s sobre 600 = 0.8%
    assert not duracion_compatible(600.0, 620.0)  # 20 s = 3.3%


def test_duracion_cero_nunca_compatible():
    assert not duracion_compatible(0.0, 0.0)
    assert not duracion_compatible(120.0, 0.0)


# --- Huella --------------------------------------------------------------------------------


def _senal(seed: int, dur: float = 70.0, sr: int = SR) -> np.ndarray:
    """Señal con estructura temporal: bandas que entran y salen en distinto momento."""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    y = np.zeros(n, dtype=np.float64)
    for f in rng.uniform(80, 5000, size=6):
        ini, fin = sorted(rng.uniform(0, dur, size=2))
        env = ((t >= ini) & (t <= fin)).astype(np.float64)
        y += env * np.sin(2 * np.pi * f * t) * rng.uniform(0.2, 1.0)
    y += 0.01 * rng.standard_normal(n)
    return (y / (np.abs(y).max() + 1e-9)).astype(np.float32)


def test_la_misma_senal_da_huella_identica():
    y = _senal(1)
    assert similitud(huella(y), huella(y.copy())) == pytest.approx(1.0)


def test_senales_distintas_dan_huellas_distintas():
    s = similitud(huella(_senal(1)), huella(_senal(2)))
    assert s < 0.9, f"dos señales distintas correlacionaron {s:.3f}"


def test_la_huella_aguanta_perder_los_agudos():
    """Un MP3 corta arriba de 16 kHz; la huella llega hasta 6 kHz, así que no se entera."""
    y = _senal(3)
    esp = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(y.size, 1.0 / SR)
    esp[freqs > 8000] = 0.0                       # simula el corte de un codec
    recortada = np.fft.irfft(esp, y.size).astype(np.float32)
    assert similitud(huella(y), huella(recortada)) > 0.95


def test_audio_muy_corto_no_tiene_huella():
    assert huella(np.zeros(SR, dtype=np.float32)) is None


def test_silencio_no_tiene_huella():
    """Sin variación no hay nada que normalizar; mejor None que un vector de ceros."""
    assert huella(np.zeros(SR * 70, dtype=np.float32)) is None


def test_similitud_tolera_none():
    assert similitud(None, huella(_senal(1))) == 0.0


# --- Las dos compuertas juntas -------------------------------------------------------------


def _ficha(nombre, dur, h):
    return Ficha(ruta=f"D:/{nombre}", archivo=nombre, duracion_s=dur, huella=h)


def test_duracion_distinta_corta_antes_de_mirar_la_huella():
    """Aunque la huella sea idéntica, si la duración no da es otra versión."""
    h = huella(_senal(4))
    a, b = _ficha("a.wav", 100.0, h), _ficha("b.wav", 140.0, h)
    es_dup, s = mismo_audio(a, b)
    assert not es_dup and s == 0.0


def test_misma_duracion_y_misma_huella_es_duplicado():
    h = huella(_senal(5))
    a, b = _ficha("a.wav", 100.0, h), _ficha("b.mp3", 100.1, h)
    es_dup, s = mismo_audio(a, b)
    assert es_dup and s == pytest.approx(1.0)


# --- Agrupamiento ---------------------------------------------------------------------------


def test_agrupa_solo_los_que_son_iguales():
    h1, h2 = huella(_senal(6)), huella(_senal(7))
    fichas = [_ficha("a.wav", 100.0, h1), _ficha("b.mp3", 100.0, h1),
              _ficha("c.wav", 100.0, h2)]
    grupos, _ = agrupar(fichas)
    assert len(grupos) == 1
    assert set(grupos[0]) == {0, 1}


def test_sin_duplicados_no_hay_grupos():
    fichas = [_ficha("a.wav", 100.0, huella(_senal(8))),
              _ficha("b.wav", 100.0, huella(_senal(9)))]
    assert agrupar(fichas)[0] == []


def test_los_que_no_tienen_huella_no_agrupan():
    fichas = [_ficha("a.wav", 100.0, None), _ficha("b.wav", 100.0, None)]
    assert agrupar(fichas)[0] == []


# --- Cuál conservar --------------------------------------------------------------------------


def _cal(corte, muro, lossless, bitrate):
    return FilaCalidad(
        archivo="x", ruta="x", formato="x", lossless=lossless,
        bitrate_declarado_kbps=bitrate, sample_rate_hz=44100,
        corte_medido_khz=corte, criterio="lossless" if lossless == "si" else "lossy",
        corte_esperado_khz=None if lossless == "si" else 20.0,
        margen_khz=None if lossless == "si" else corte - 20.0,
        muro_db=muro, es_muro="si" if muro >= 25 else "no",
        bandera="ok", motivo="", marca_manual="")


def test_un_wav_de_15khz_con_muro_pierde_contra_un_320_real():
    """La regla explícita: decide la calidad MEDIDA, nunca el bitrate declarado."""
    fichas = [_ficha("trucho.wav", 200.0, None), _ficha("bueno.mp3", 200.0, None)]
    calidades = {0: _cal(15.0, 60.0, "si", 1411), 1: _cal(20.0, 30.0, "no", 320)}
    filas = decidir([0, 1], fichas, calidades)
    por = {f.archivo: f for f in filas}
    assert por["bueno.mp3"].accion == CONSERVAR
    assert por["trucho.wav"].accion == DESCARTAR
    assert "más abajo" in por["trucho.wav"].motivo


def test_a_igual_corte_gana_el_sin_muro():
    fichas = [_ficha("a.mp3", 200.0, None), _ficha("b.wav", 200.0, None)]
    calidades = {0: _cal(20.0, 60.0, "no", 320), 1: _cal(20.0, 2.0, "si", 1411)}
    filas = decidir([0, 1], fichas, calidades)
    assert {f.archivo: f.accion for f in filas}["b.wav"] == CONSERVAR


def test_a_igual_medida_gana_el_lossless():
    fichas = [_ficha("a.mp3", 200.0, None), _ficha("b.wav", 200.0, None)]
    calidades = {0: _cal(22.0, 2.0, "no", 320), 1: _cal(22.0, 2.0, "si", 1411)}
    filas = decidir([0, 1], fichas, calidades)
    por = {f.archivo: f for f in filas}
    assert por["b.wav"].accion == CONSERVAR
    assert "sin pérdida" in por["a.mp3"].motivo


def test_el_bitrate_declarado_no_decide():
    """Mismo audio medido igual, pero uno declara 320 y el otro 128: no cambia nada."""
    p_alto = _puntaje_calidad(_cal(20.0, 30.0, "no", 320))
    p_bajo = _puntaje_calidad(_cal(20.0, 30.0, "no", 128))
    assert p_alto == p_bajo


def test_siempre_se_conserva_exactamente_uno():
    fichas = [_ficha(f"f{i}.wav", 200.0, None) for i in range(4)]
    calidades = {i: _cal(20.0 - i, 10.0, "si", 1411) for i in range(4)}
    filas = decidir([0, 1, 2, 3], fichas, calidades)
    assert sum(1 for f in filas if f.accion == CONSERVAR) == 1
    assert sum(1 for f in filas if f.accion == DESCARTAR) == 3


# --- Desempate: preferir la copia ya referenciada (tarea 5.26) ---------------------------------


def _par_equivalente():
    """Dos copias que miden IDÉNTICO: sin referencias, el orden del grupo decide."""
    fichas = [_ficha("primera.wav", 200.0, None), _ficha("segunda.wav", 200.0, None)]
    calidades = {0: _cal(20.0, 2.0, "si", 1411), 1: _cal(20.0, 2.0, "si", 1411)}
    return fichas, calidades


def _ref(ficha, origen):
    return {normalizar_ruta(ficha.ruta): origen}


def test_sin_referencias_el_empate_lo_resuelve_el_orden_del_grupo():
    """La línea base: sin la fuente nueva, nada cambia (y el motivo sigue sin inventar razón)."""
    fichas, calidades = _par_equivalente()
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades)}
    assert por["primera.wav"].accion == CONSERVAR
    assert por["segunda.wav"].motivo == "copia equivalente del mismo audio"


def test_entre_copias_equivalentes_gana_la_referenciada_por_rekordbox():
    fichas, calidades = _par_equivalente()
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades,
                                         _ref(fichas[1], "Rekordbox"))}
    assert por["segunda.wav"].accion == CONSERVAR, "ganó la que NO está referenciada"
    assert por["primera.wav"].accion == DESCARTAR
    assert "ya está referenciada por Rekordbox" in por["segunda.wav"].motivo
    assert por["primera.wav"].motivo == (
        "copia equivalente del mismo audio; se conserva la que ya está referenciada "
        "por Rekordbox y esta no")


def test_el_desempate_vale_tambien_para_itunes():
    fichas, calidades = _par_equivalente()
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades, _ref(fichas[1], "iTunes"))}
    assert por["segunda.wav"].accion == CONSERVAR
    assert "referenciada por iTunes" in por["segunda.wav"].motivo


def test_en_un_grupo_de_tres_gana_la_unica_referenciada():
    fichas = [_ficha(f"f{i}.wav", 200.0, None) for i in range(3)]
    calidades = {i: _cal(20.0, 2.0, "si", 1411) for i in range(3)}
    filas = decidir([0, 1, 2], fichas, calidades, _ref(fichas[2], "Rekordbox"))
    assert [f.archivo for f in filas if f.accion == CONSERVAR] == ["f2.wav"]


def test_la_referencia_nunca_compensa_una_peor_calidad():
    """Desempate, no preferencia: la copia referenciada que corta en 15 kHz pierde igual.

    Y como se la propone descartar, el motivo avisa que rompe lo que Rekordbox guarda de ella.
    """
    fichas = [_ficha("referenciada.wav", 200.0, None), _ficha("mejor.wav", 200.0, None)]
    calidades = {0: _cal(15.0, 60.0, "si", 1411), 1: _cal(20.0, 2.0, "si", 1411)}
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades,
                                         _ref(fichas[0], "Rekordbox"))}
    assert por["mejor.wav"].accion == CONSERVAR
    assert por["referenciada.wav"].accion == DESCARTAR
    assert "más abajo" in por["referenciada.wav"].motivo
    assert "OJO: esta copia está referenciada por Rekordbox" in por["referenciada.wav"].motivo
    assert "referenciada" not in por["mejor.wav"].motivo, "no hubo desempate por referencia"


def test_si_las_dos_estan_referenciadas_el_criterio_no_desempata_y_avisa():
    """El caso sin criterio de producto: queda el orden del grupo, pero el motivo lo grita."""
    fichas, calidades = _par_equivalente()
    refs = {**_ref(fichas[0], "Rekordbox"), **_ref(fichas[1], "iTunes")}
    por = {f.archivo: f for f in decidir([0, 1], fichas, calidades, refs)}
    assert por["primera.wav"].accion == CONSERVAR
    assert "ya está referenciada" not in por["primera.wav"].motivo, \
        "dijo que desempató por la referencia cuando las dos la tienen"
    assert "OJO: esta copia está referenciada por iTunes" in por["segunda.wav"].motivo


def test_la_ruta_se_compara_normalizada(tmp_path):
    """El XML trae 'file:///C:/x/..' y el pipeline una ruta del SO: tienen que ser la misma."""
    ruta = tmp_path / "carpeta con espacios" / "tema.wav"
    assert normalizar_ruta(str(ruta)) == normalizar_ruta(ruta.as_posix())
    assert normalizar_ruta(str(ruta)) != normalizar_ruta(str(ruta.with_name("otro.wav")))


def _xml_rekordbox(destino, rutas):
    pistas = "".join(f'<TRACK TrackID="{i}" Name="t" Location="{Path(r).as_uri()}"/>'
                     for i, r in enumerate(rutas, 1))
    destino.write_text(f'<DJ_PLAYLISTS><COLLECTION Entries="{len(rutas)}">{pistas}'
                       "</COLLECTION></DJ_PLAYLISTS>", encoding="utf-8")
    return destino


def test_el_xml_de_rekordbox_desempata_con_rutas_reales(tmp_path):
    a, b = tmp_path / "descarga uno" / "a.wav", tmp_path / "biblioteca" / "b.wav"
    fichas = [Ficha(str(a), "a.wav", 200.0, None), Ficha(str(b), "b.wav", 200.0, None)]
    _, calidades = _par_equivalente()
    refs = referencias_de_xml(rekordbox_xml=_xml_rekordbox(tmp_path / "rb.xml", [b]))
    por = {f.archivo: f.accion for f in decidir([0, 1], fichas, calidades, refs)}
    assert por == {"a.wav": DESCARTAR, "b.wav": CONSERVAR}


def test_la_biblioteca_de_itunes_desempata_con_rutas_reales(tmp_path):
    a, b = tmp_path / "descarga" / "a.wav", tmp_path / "iTunes Media" / "b.wav"
    lib = tmp_path / "Library.xml"
    # iTunes escribe 'file://localhost/C:/...'; as_uri() da 'file:///C:/...'.
    ubicacion = "file://localhost" + b.as_uri()[len("file://"):]
    lib.write_bytes(plistlib.dumps({"Tracks": {
        "101": {"Track ID": 101, "Location": ubicacion},
        "102": {"Track ID": 102}}}))                      # una pista sin Location no rompe
    fichas = [Ficha(str(a), "a.wav", 200.0, None), Ficha(str(b), "b.wav", 200.0, None)]
    _, calidades = _par_equivalente()
    refs = referencias_de_xml(itunes_xml=lib)
    assert set(refs.values()) == {"iTunes"}
    por = {f.archivo: f.accion for f in decidir([0, 1], fichas, calidades, refs)}
    assert por == {"a.wav": DESCARTAR, "b.wav": CONSERVAR}


def test_una_ruta_en_las_dos_bibliotecas_dice_las_dos():
    a, b = {"c:/x.wav": "Rekordbox"}, {"c:/x.wav": "iTunes", "c:/y.wav": "iTunes"}
    assert unir_referencias(a, b) == {"c:/x.wav": "Rekordbox e iTunes", "c:/y.wav": "iTunes"}
    assert unir_referencias(a, a) == a


def test_un_xml_que_no_es_de_la_biblioteca_falla_en_vez_de_ignorarse(tmp_path):
    """Ignorarlo propondría descartar justo la copia con el trabajo encima."""
    otro = tmp_path / "otro.xml"
    otro.write_text("<algo/>", encoding="utf-8")
    with pytest.raises(ValueError):
        referencias_rekordbox(otro)
    with pytest.raises(ValueError):
        referencias_itunes(otro)
    sin_pistas = tmp_path / "plist_sin_pistas.xml"
    sin_pistas.write_bytes(plistlib.dumps({"Major Version": 1}))   # plist válido, pero sin 'Tracks'
    with pytest.raises(ValueError, match="Tracks"):
        referencias_itunes(sin_pistas)
    assert main(["--audio", str(tmp_path), "--rekordbox-xml", str(otro)]) == 2
    assert main(["--audio", str(tmp_path), "--itunes-xml", str(otro)]) == 2
