"""Tests de `playlist_import.py` (f53): leer las playlists del XML de Rekordbox, ubicar cada
archivo, y navegar/importar carpetas sin salir de las raíces permitidas.

Todo sintético (`tests/sinteticos.py`, misma estructura que Rekordbox 7.2.16): ni rutas ni
nombres del dueño. Lo esperado sale del fixture (qué archivo se escribió dónde), no del módulo.
"""
import os
import sys

import pytest
from sinteticos import biblioteca_importable, location_rb, wav, xml_rekordbox_playlists

import playlist_import as pi
from playlist_import import Rechazo


def _leer(xml: str, raices):
    col = pi.leer_rekordbox(xml.encode("utf-8"), "coleccion.xml")
    pi.resolver_coleccion(col, [str(r) for r in raices])
    return col


@pytest.fixture
def importable(tmp_path):
    raiz = tmp_path / "musica"
    xml, rutas = biblioteca_importable(raiz)
    return raiz, xml, rutas


# --- parseo de PLAYLISTS -----------------------------------------------------------------------

def test_arbol_de_playlists_con_carpetas_anidadas_y_orden(importable):
    raiz, xml, _ = importable
    col = _leer(xml, [raiz])
    assert [(p["ruta"], p["carpeta"], p["nombre"]) for p in col.playlists] == [
        ("Techno / Peak", "Techno", "Peak"),
        ("Techno / Sub / Cierre", "Techno / Sub", "Cierre"),
        ("Warmup", None, "Warmup"),
    ], "el nodo ROOT no es una carpeta del dueño y las carpetas anidadas van en la ruta"
    peak = col.playlists[0]
    # Orden del XML (2, 1, 3) y el TrackID 999 que la colección no tiene, contado aparte.
    assert peak["entradas"] == ["2", "1", "3"]
    assert peak["inexistentes"] == 1


def test_keytype_1_se_refiere_por_location(importable):
    raiz, xml, _ = importable
    col = _leer(xml, [raiz])
    cierre = col.playlists[1]
    assert cierre["entradas"] == ["1", "4"], "KeyType=1: la Key es la Location del tema"


def test_datos_de_cada_tema_del_xml(importable):
    raiz, xml, _ = importable
    col = _leer(xml, [raiz])
    t1, t2, t4 = col.tracks["1"], col.tracks["2"], col.tracks["4"]
    assert (t1["titulo"], t1["artista"], t1["genero"], t1["bpm"], t1["camelot"], t1["duracion"]) \
        == ("Ácido #1", "Artista Uno", "Techno", 128.4, "8A", 361)
    assert t2["bpm"] is None, "AverageBpm 0.00 = Rekordbox no lo analizó, no es un BPM"
    assert t4["camelot"] is None, "un Tonality '8A' es un tag ajeno: no se mapea ni se adivina"


def test_location_percent_encoded_de_windows_se_encuentra(importable):
    raiz, xml, rutas = importable
    assert "%20" in location_rb(rutas["acido"]) and "%23" in location_rb(rutas["acido"]) \
        and "%25" in location_rb(rutas["acido"]) and "%C3%81" in location_rb(rutas["acido"])
    col = _leer(xml, [raiz])
    estado, ruta, _ = col.resoluciones[col.tracks["1"]["location"]]
    assert estado == "ok" and os.path.samefile(ruta, rutas["acido"])


def test_resolucion_encontrado_reubicado_ambiguo_y_no_encontrado(importable):
    raiz, xml, rutas = importable
    col = _leer(xml, [raiz])
    res = {tid: col.resoluciones[col.tracks[tid]["location"]] for tid in ("1", "2", "3", "4")}
    assert res["2"][0] == "ok" and os.path.samefile(res["2"][1], rutas["reubicado"]), \
        "la ruta de otra PC tiene que reubicarse por la cola (Techno/reubicado.wav)"
    assert res["3"] == ("ambiguo", None, 2), "dos archivos distintos con ese nombre: no se elige"
    assert res["4"] == ("no-encontrado", None, 0)
    resumen = pi.resumen_playlist(col, col.playlists[0])
    assert {k: resumen[k] for k in ("total", "encontrados", "ambiguos", "faltan", "inexistentes")} \
        == {"total": 3, "encontrados": 2, "ambiguos": 1, "faltan": 0, "inexistentes": 1}


def test_un_location_fuera_de_las_raices_no_se_usa(tmp_path):
    """El XML llega del navegador: un Location a un archivo que existe FUERA de las carpetas
    permitidas no se toma (sería leer cualquier archivo de la PC)."""
    afuera = tmp_path / "afuera" / "secreto.wav"
    wav(afuera, 300.0)
    raiz = tmp_path / "musica"
    raiz.mkdir()
    xml = xml_rekordbox_playlists(
        [dict(id="1", name="X", artist="", loc=location_rb(afuera))],
        [("playlist", "P", "0", ["1"])])
    col = _leer(xml, [raiz])
    assert col.resoluciones[col.tracks["1"]["location"]][0] == "no-encontrado"


def test_temas_de_playlist_sin_rutas_inventadas(importable):
    raiz, xml, rutas = importable
    col = _leer(xml, [raiz])
    temas = pi.temas_de_playlist(col, col.playlists[0])
    assert [(t["titulo"], t["resolucion"], t["archivo"], t["homonimos"]) for t in temas] == [
        ("Reubicado", "ok", "reubicado.wav", None),
        ("Ácido #1", "ok", "Ácido #1 (100%).wav", None),
        ("Repetido", "ambiguo", "repetido.wav", 2),
    ]
    assert temas[2]["ruta"] is None
    assert temas[2]["genero"] == "", "el XML no trae género: queda vacío, no se inventa"


def test_sin_raices_nada_se_encuentra(importable):
    _, xml, _ = importable
    col = _leer(xml, [])
    assert {e for e, _, _ in col.resoluciones.values()} == {"no-encontrado"}


# --- seguridad del XML ---------------------------------------------------------------------------

@pytest.mark.parametrize(("datos", "status", "pista"), [
    (b"", 400, "vacío"),
    (b"   \n ", 400, "vacío"),
    (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><DJ_PLAYLISTS/>', 400, "DOCTYPE"),
    (b'<?xml version="1.0"?><!ENTITY a "x"><DJ_PLAYLISTS/>', 400, "DOCTYPE"),
    (b'<DJ_PLAYLISTS><COLLECTION><TRACK', 400, "roto"),
    (b'<otra_cosa/>', 400, "No es un XML de Rekordbox"),
    ('<DJ_PLAYLISTS/>'.encode("utf-16"), 400, "UTF-8"),
])
def test_xml_rechazado_con_motivo(datos, status, pista):
    with pytest.raises(Rechazo) as e:
        pi.leer_rekordbox(datos, "x.xml")
    assert e.value.status == status and pista in e.value.mensaje, e.value.mensaje


def test_xml_enorme_es_413(monkeypatch):
    monkeypatch.setattr(pi, "XML_MAX_BYTES", 100)
    with pytest.raises(Rechazo) as e:
        pi.validar_xml(b"<DJ_PLAYLISTS>" + b" " * 200 + b"</DJ_PLAYLISTS>")
    assert e.value.status == 413


# --- carpetas ------------------------------------------------------------------------------------

@pytest.mark.parametrize("ruta", [
    "..", "a/../..", "C:/Windows", "C:", "/etc", "\\Windows", "\\\\servidor\\musica",
    "\\\\?\\C:\\x", "CON", "nul.txt", "a/COM1", "LPT9.wav", "a\x00b", "x:stream", "a/ . /b",
])
def test_rutas_relativas_peligrosas_se_rechazan(ruta):
    with pytest.raises(Rechazo) as e:
        pi.validar_relativa(ruta)
    assert e.value.status == 400


def test_ruta_relativa_valida_se_parte():
    assert pi.validar_relativa("Techno\\Peak Time/sub") == ["Techno", "Peak Time", "sub"]
    assert pi.validar_relativa("") == []


def _junction(link, destino):
    """Junction de Windows (no pide permisos de admin, a diferencia de un symlink)."""
    import _winapi
    _winapi.CreateJunction(str(destino), str(link))


@pytest.mark.skipif(sys.platform != "win32", reason="junctions: solo Windows")
def test_un_junction_que_sale_de_la_raiz_no_se_sigue(tmp_path):
    raiz, afuera = tmp_path / "musica", tmp_path / "afuera"
    (raiz / "Techno").mkdir(parents=True)
    wav(afuera / "secreto.wav", 300.0)
    _junction(raiz / "escape", afuera)
    with pytest.raises(Rechazo) as e:
        pi.carpeta_segura([str(raiz)], 0, "escape")
    assert e.value.status == 400 and "fuera" in e.value.mensaje
    real, base, _ = pi.carpeta_segura([str(raiz)], 0, "")
    assert [s["nombre"] for s in pi.listar_carpeta(real, base)["subcarpetas"]] == ["Techno"], \
        "el junction que sale de la raíz no puede aparecer en la lista"
    archivos, _ = pi.audios_de_carpeta(real, base, recursivo=True)
    assert archivos == []


def test_carpeta_segura_ids_y_sin_raices(tmp_path):
    with pytest.raises(Rechazo) as e:
        pi.carpeta_segura([], 0, "")
    assert e.value.status == 409 and "MUSIFLIX_LIBRARY_ROOTS" in e.value.mensaje
    for malo, status in ((5, 404), ("x", 400), (True, 404), (-1, 404)):
        with pytest.raises(Rechazo) as e:
            pi.carpeta_segura([str(tmp_path)], malo, "")
        assert e.value.status == status, malo
    with pytest.raises(Rechazo) as e:
        pi.carpeta_segura([str(tmp_path)], 0, "no-existe")
    assert e.value.status == 404


def test_listar_y_orden_natural(tmp_path):
    raiz = tmp_path / "musica"
    for n in ("Tema 10.wav", "tema 2.wav", "Tema 1.mp3", "notas.txt"):
        wav(raiz / "Set" / n, 300.0) if n.endswith(".wav") else (raiz / "Set" / n).write_bytes(b"x")
    wav(raiz / "Set" / "Sub" / "otro.wav", 300.0)
    real, base, _ = pi.carpeta_segura([str(raiz)], "0", "")
    assert pi.listar_carpeta(real, base)["subcarpetas"] == [
        {"nombre": "Set", "audios": 3, "subcarpetas": 1}]
    real, base, _ = pi.carpeta_segura([str(raiz)], 0, "Set")
    archivos, pasado = pi.audios_de_carpeta(real, base)
    assert [p.name for p in archivos] == ["Tema 1.mp3", "tema 2.wav", "Tema 10.wav"]
    assert not pasado
    archivos, _ = pi.audios_de_carpeta(real, base, recursivo=True)
    assert [p.relative_to(real).as_posix() for p in archivos] == [
        "Sub/otro.wav", "Tema 1.mp3", "tema 2.wav", "Tema 10.wav"]
    archivos, pasado = pi.audios_de_carpeta(real, base, tope=2)
    assert len(archivos) == 2 and pasado, "pasarse del tope se avisa"


def test_raices_publicas_sin_rutas(tmp_path):
    a, b = tmp_path / "x" / "Musica", tmp_path / "y" / "Musica"
    a.mkdir(parents=True)
    pub = pi.raices_publicas([str(a), str(b)])
    assert pub == [{"id": 0, "nombre": "Musica", "disponible": True},
                   {"id": 1, "nombre": "Musica (2)", "disponible": False}]


def test_tags_de_carpeta_reales_con_mutagen(tmp_path):
    from mutagen.id3 import TCON, TIT2, TPE1
    from mutagen.wave import WAVE

    con = tmp_path / "con_tags.wav"
    wav(con, 300.0, segundos=2.0)
    audio = WAVE(str(con))
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="Título del tag"))
    audio.tags.add(TPE1(encoding=3, text="Artista del tag"))
    audio.tags.add(TCON(encoding=3, text="Minimal"))
    audio.save()
    sin = tmp_path / "sin tags.wav"
    wav(sin, 300.0, segundos=1.0)
    roto = tmp_path / "roto.mp3"
    roto.write_bytes(b"esto no es un mp3" * 10)

    t = pi.tema_de_archivo(con)
    assert (t["titulo"], t["artista"], t["genero"], t["duracion"], t["import_motivo"]) == \
        ("Título del tag", "Artista del tag", "Minimal", 2, None)
    t = pi.tema_de_archivo(sin)
    assert (t["titulo"], t["artista"], t["genero"], t["bpm"]) == ("sin tags", "", "", None), \
        "sin tags: el título es el nombre del archivo y el género queda vacío (no se inventa)"
    t = pi.tema_de_archivo(roto)
    assert t["titulo"] == "roto" and t["import_motivo"] and "tags" in t["import_motivo"]


def test_sesiones_vencen_y_tienen_tope(monkeypatch):
    s = pi.Sesiones(ttl_s=10, maximo=2)
    col = pi.Coleccion("x.xml", {}, [])
    reloj = [1000.0]
    monkeypatch.setattr(pi.time, "monotonic", lambda: reloj[0])
    a, b = s.guardar(col), s.guardar(col)
    c = s.guardar(col)
    assert s.leer(a) is None and s.leer(b) is col and s.leer(c) is col, "tope: sale la más vieja"
    reloj[0] += 11
    assert s.leer(b) is None, "venció"
    assert s.leer(None) is None and s.leer(123) is None
