"""Tests de `radio_playlist.py` sin server: las reglas del dueño (f33) una por una, y el
progreso del análisis en segundo plano con un analizador de mentira que se puede frenar.

El analizador de mentira no devuelve BPM ni key: devuelve "anduvo" o un motivo. Lo que se
prueba acá es cómo se cuenta el progreso, no una medición (esa va contra audio sintético en
`test_server_radio_playlist.py`).
"""
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import radio_playlist as rp


@dataclass
class _Track:          # lo único que `radio_playlist` le mira a un Track del motor
    path: Path
    license: str = "lic"
    source_url: str = "orig"


def _items(tmp_path, *filas):
    """Items con archivo real en `tmp_path` (nombre, título, artista, género, fuente)."""
    out = []
    for i, (nombre, titulo, artista, genero, fuente) in enumerate(filas, 1):
        ruta = None
        if nombre:
            ruta = tmp_path / nombre
            ruta.write_bytes(b"x")
        out.append({"id": i, "titulo": titulo, "artista": artista, "genero": genero,
                    "fuente": fuente, "url": "", "ruta": str(ruta) if ruta else None})
    return out


def _analizados(*rutas):
    return {rp.claves_de_ruta(str(r))[-1]: _Track(Path(r)) for r in rutas}


def test_normalizar_y_sin_genero():
    assert rp.normalizar("  Hard   BOUNCE ") == "hard bounce"
    assert rp.normalizar("ＴＥＣＨＮＯ") == "techno", "NFKC: el ancho completo de un copiar-pegar"
    assert [rp.clave_genero(g) for g in (None, "", "   ", "Sin género", " SIN GENERO ", "Techno")] == \
        [None, None, None, None, None, "techno"]


def test_licencia_y_origen_segun_de_donde_salio():
    assert rp.licencia_y_origen({"fuente": "biblioteca", "url": "x"}) == \
        ("biblioteca personal", "coleccion Rekordbox local")
    assert rp.licencia_y_origen({"fuente": "youtube", "url": " https://y/1 "}) == \
        ("descarga, licencia sin verificar", "https://y/1")
    assert rp.licencia_y_origen({"fuente": "soundcloud", "url": ""}) == \
        ("descarga, licencia sin verificar", "soundcloud")
    assert rp.licencia_y_origen({"fuente": "", "url": None}) is None


def test_un_analisis_que_fallo_no_se_queda_con_el_lugar_del_duplicado(tmp_path):
    """Si el primero de dos iguales no se puede analizar, el segundo igual puede entrar; si
    el primero está POR analizar, el segundo es el duplicado (no se analiza de gusto)."""
    items = _items(tmp_path,
                   ("a.wav", "Tema", "Art", "Techno", "biblioteca"),     # falló
                   ("b.wav", "tema ", "ART", "techno", "biblioteca"),    # mismo tema: entra
                   ("c.wav", "Otro", "Art", "Techno", "biblioteca"),     # por analizar
                   ("d.wav", "OTRO", "art", "Techno", "biblioteca"))     # duplicado de c
    fallo = {str(tmp_path / "a.wav"): "roto"}.get
    c = rp.clasificar(items, _analizados(tmp_path / "b.wav", tmp_path / "d.wav"), fallo=fallo)
    assert [(x.estado, x.duplicado_de) for x in c] == [
        ("fallo_analisis", None), ("listo", None), ("por_analizar", None), ("duplicado", 3)]
    assert c[0].motivo == "el análisis falló: roto"


def test_el_mismo_archivo_con_otro_nombre_es_duplicado(tmp_path):
    """Dos items que apuntan al MISMO archivo (p. ej. uno bajado y otro de la home, con otro
    título): el archivo es la identidad, aunque los nombres no coincidan."""
    items = _items(tmp_path, ("a.wav", "Tema", "Art", "Techno", "biblioteca"),
                   (None, "Otro nombre", "Otro", "Techno", "biblioteca"))
    items[1]["ruta"] = items[0]["ruta"].upper() if os.name == "nt" else items[0]["ruta"]
    c = rp.clasificar(items, _analizados(tmp_path / "a.wav"))
    assert [(x.estado, x.duplicado_de) for x in c] == [("listo", None), ("duplicado", 1)]
    assert c[1].motivo == "duplicado de «Art — Tema» (#1 de la playlist): entra solo el primero"


def test_excluidos_es_una_particion_de_la_playlist(tmp_path):
    items = _items(tmp_path,
                   ("a.wav", "A", "x", "Techno", "biblioteca"),
                   ("b.wav", "B", "x", "Industrial", "biblioteca"),
                   ("c.wav", "C", "x", None, "biblioteca"),
                   (None, "D", "x", "Techno", "youtube"),
                   ("e.wav", "E", "x", "Techno", ""),
                   ("f.wav", "F", "x", "Industrial", "biblioteca"))    # por analizar, otro género
    items[3]["url"] = "https://y/2"
    c = rp.clasificar(items, _analizados(tmp_path / "a.wav", tmp_path / "b.wav"))
    entran, ex = rp.excluidos_para(c, "techno")
    assert (entran, ex) == (1, {"otro_genero": 2, "por_analizar": 0, "fallo_analisis": 0,
                                "sin_archivo": 1, "archivo_no_existe": 0, "sin_genero": 1,
                                "sin_origen": 1, "duplicado": 0})
    assert entran + sum(ex.values()) == len(items)
    pool = rp.pool_para(c, c[0], [c[1].track, c[0].track])
    assert (pool.tracks, pool.genero) == ([c[0].track], "Techno")


def test_el_progreso_se_cuenta_tema_por_tema(tmp_path):
    rutas = [tmp_path / f"{n}.wav" for n in "abc"]
    for r in rutas:
        r.write_bytes(b"x")
    tareas = [rp.TareaAnalisis(i, f"T{i}", str(r), "lic", "orig") for i, r in enumerate(rutas, 1)]
    paso = [threading.Event() for _ in tareas]
    entro = [threading.Event() for _ in tareas]

    def analizar(t):
        entro[t.item_id - 1].set()
        paso[t.item_id - 1].wait(5)
        return "no decodifica" if t.item_id == 2 else None

    a = rp.AnalisisEnFondo()
    arranco, p = a.arrancar(7, "Mi playlist", tareas, analizar)
    assert arranco and (p["corriendo"], p["total"]) == (True, 3)
    assert entro[0].wait(5)
    assert a.progreso(7) == {"corriendo": True, "hechos": 0, "total": 3, "actual": "T1",
                             "fallidos": [], "ocupado_por": None}
    assert a.progreso(8)["ocupado_por"] == {"id": 7, "nombre": "Mi playlist"}
    assert a.arrancar(8, "otra", tareas[:1], analizar)[0] is False, "arrancó un segundo análisis"
    paso[0].set()
    assert entro[1].wait(5)
    assert (a.progreso(7)["hechos"], a.progreso(7)["actual"]) == (1, "T2")
    paso[1].set()
    paso[2].set()
    assert a.esperar(5)
    assert a.progreso(7) == {"corriendo": False, "hechos": 3, "total": 3, "actual": None,
                             "fallidos": [{"item_id": 2, "titulo": "T2", "motivo": "no decodifica"}],
                             "ocupado_por": None}
    assert (a.motivo_fallo(str(rutas[1])), a.motivo_fallo(str(rutas[0]))) == ("no decodifica", None)
    rutas[1].write_bytes(b"cambiado")                      # otro mtime: se puede reintentar
    os.utime(rutas[1], (1, 1))
    assert a.motivo_fallo(str(rutas[1])) is None
