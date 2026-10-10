"""f55: la lógica pura de qué entra al set desde una playlist (`radio_playlist.py`)."""
import radio_playlist as rp


def tema(i, titulo, genero="Techno", *, artista="A", archivo=True, analisis="analizado",
         es_track=True, clave=None, motivo_fallo=None):
    return rp.Tema(item_id=i, posicion=i, titulo=titulo, artista=artista, genero=genero,
                   archivo_ok=archivo, archivo_motivo=None if archivo else "No encuentro el archivo.",
                   analisis=analisis if archivo else "sin-archivo", analisis_motivo=motivo_fallo,
                   clave_archivo=(clave or f"c:/m/{titulo.lower()}.wav") if archivo else None,
                   radio_id=f"{i:016x}" if analisis == "analizado" and archivo else None,
                   es_track=es_track if analisis == "analizado" and archivo else None)


def test_la_clave_de_genero_no_inventa_parecidos():
    assert rp.clave_genero("  Hard   Techno ") == rp.clave_genero("hard techno") == "hard techno"
    assert rp.clave_genero("ＴＥＣＨＮＯ") == "techno"            # ancho completo (NFKC)
    assert rp.clave_genero("Hard Techno") != rp.clave_genero("Techno")
    assert [rp.clave_genero(g) for g in (None, "", "   ", "Sin género", "sin genero")] == [None] * 5


def test_cada_motivo_en_su_orden_y_la_particion_cierra():
    temas = [
        tema(1, "Uno"),
        tema(2, "Otro", "House", archivo=False),        # otro género gana a sin archivo
        tema(3, "Sin", None, archivo=False),            # sin archivo gana a sin género
        tema(4, "Vacio", None),
        tema(5, "Cola", analisis="en-cola"),
        tema(6, "Ahora", analisis="analizando"),
        tema(7, "Roto", analisis="fallo", motivo_fallo="archivo corrupto"),
        tema(8, "Nuevo", analisis="pendiente"),
        tema(9, "Loop", es_track=False),
        tema(10, "Uno"),                                 # mismo artista y título que el 1
        tema(11, "Copia", clave="c:/m/uno.wav"),         # mismo ARCHIVO que el 1
        tema(12, "Dos"),
    ]
    d = rp.clasificar(temas, "techno")
    assert [(x.tema.item_id, x.motivo) for x in d] == [
        (1, None), (2, "otro_genero"), (3, "sin_archivo"), (4, "sin_genero"), (5, "analizando"),
        (6, "analizando"), (7, "fallo_analisis"), (8, "sin_analizar"), (9, "no_es_track"),
        (10, "duplicado"), (11, "duplicado"), (12, None)]
    assert [x.duplicado_de for x in d[9:11]] == [1, 1]
    assert d[6].texto == "el análisis falló: archivo corrupto"
    assert d[2].texto == "No encuentro el archivo."
    r = rp.resumen(d)
    assert (r["entran"], r["total"]) == (2, 12)
    assert r["excluidos"] == {"otro_genero": 1, "sin_genero": 1, "sin_archivo": 1, "analizando": 2,
                              "sin_analizar": 1, "fallo_analisis": 1, "no_es_track": 1, "duplicado": 2}
    assert rp.en_curso(temas, "techno") == 2


def test_una_copia_sin_analizar_no_le_quita_el_lugar_a_la_que_esta_lista():
    """Duplicado = el mismo tema; entra el primero de los que PUEDEN entrar."""
    d = rp.clasificar([tema(1, "Uno", analisis="pendiente"), tema(2, "Uno", clave="c:/m/x.wav")],
                      "techno")
    assert [(x.tema.item_id, x.motivo) for x in d] == [(1, "sin_analizar"), (2, None)]


def test_un_duplicado_en_otro_genero_no_cuenta():
    """El mismo master rotulado House y Techno: en un set de Techno entra el de Techno aunque
    el de House esté antes (el de House es «otro género», no «el primero»)."""
    d = rp.clasificar([tema(1, "Uno", "House"), tema(2, "Uno", clave="c:/m/uno.wav")], "techno")
    assert [(x.tema.item_id, x.motivo) for x in d] == [(1, "otro_genero"), (2, None)]


def test_semillas_generos_y_si_alcanza():
    temas = [tema(1, "Uno"), tema(2, "Dos", "House"), tema(3, "Tres"), tema(4, "Uno"),
             tema(5, "Cinco", "House", analisis="pendiente")]
    assert [t.item_id for t in rp.semillas(temas)] == [1, 2, 3]
    assert [(g["genero"], g["entran"], g["entran_ids"]) for g in rp.generos(temas)] == \
        [("Techno", 2, [1, 3]), ("House", 1, [2])]
    assert rp.puede_armar(temas) == (True, None)
    ok, motivo = rp.puede_armar([tema(1, "Uno"), tema(2, "Dos", analisis="analizando"),
                                 tema(3, "Tres", None)])
    assert (ok, motivo) == (False, "Hace falta al menos 2 temas analizados del mismo género (el "
                                   "que más tiene, 1). 1 todavía analizando. 1 sin género.")
    assert rp.puede_armar([]) == (False, "La playlist está vacía.")
