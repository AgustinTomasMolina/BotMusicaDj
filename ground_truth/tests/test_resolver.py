"""Tests de la resolución de rutas del ground truth.

Usan archivos temporales de verdad (no audio: al resolver solo importa la ruta).
"""
from ground_truth.resolver import (
    AMBIGUO,
    NO_ENCONTRADO,
    OK,
    construir_indice,
    resolver,
)


def _tocar(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_ruta_original_si_existe(tmp_path):
    f = _tocar(tmp_path / "crate" / "track.wav")
    r = resolver(str(f), [str(tmp_path)])
    assert r.estado == OK and r.ruta == str(f)


def test_reubica_por_ancla(tmp_path):
    """.../Escritorio/GROOOOVE/x.wav → <raíz>/GROOOOVE/x.wav"""
    destino = _tocar(tmp_path / "GROOOOVE" / "x.wav")
    vieja = "C:/Users/otro/OneDrive/Escritorio/GROOOOVE/x.wav"
    r = resolver(vieja, [str(tmp_path)])
    assert r.estado == OK and r.ruta == str(destino)


def test_basename_unico_resuelve(tmp_path):
    destino = _tocar(tmp_path / "sub" / "unico.wav")
    r = resolver("D:/viejo/unico.wav", [str(tmp_path)], construir_indice([str(tmp_path)]))
    assert r.estado == OK and r.ruta == str(destino)


def test_basename_homonimo_es_ambiguo(tmp_path):
    """Original vs edit con el mismo nombre: no se elige, se excluye."""
    _tocar(tmp_path / "originales" / "track.wav")
    _tocar(tmp_path / "edits" / "track.wav")
    r = resolver("D:/viejo/track.wav", [str(tmp_path)], construir_indice([str(tmp_path)]))
    assert r.estado == AMBIGUO
    assert r.ruta is None                 # no se resuelve a la suerte
    assert len(r.candidatos) == 2
    assert not r                          # __bool__ es False: no entra al cómputo


def test_sin_candidatos_es_no_encontrado(tmp_path):
    r = resolver("D:/viejo/no_esta.wav", [str(tmp_path)], construir_indice([str(tmp_path)]))
    assert r.estado == NO_ENCONTRADO and r.ruta is None


def test_location_vacio(tmp_path):
    assert resolver("", [str(tmp_path)]).estado == NO_ENCONTRADO


def test_por_cola_reubica_sin_ancla_y_desempata_homonimos(tmp_path):
    """f53: la ruta de otra PC no tiene 'Escritorio'; la cola (Techno/x.wav) decide entre
    homónimos. Sin `por_cola` los defaults de siempre: ambiguo."""
    exacto = _tocar(tmp_path / "Techno" / "x.wav")
    _tocar(tmp_path / "House" / "x.wav")
    vieja = "C:/Users/otro/Music/Techno/x.wav"
    idx = construir_indice([str(tmp_path)])
    r = resolver(vieja, [str(tmp_path)], idx, por_cola=True)
    assert r.estado == OK and r.ruta == str(exacto)
    assert resolver(vieja, [str(tmp_path)], idx).estado == AMBIGUO


def test_confinar_ignora_lo_que_esta_fuera_de_las_raices(tmp_path):
    afuera = _tocar(tmp_path / "afuera" / "x.wav")
    raiz = tmp_path / "musica"
    raiz.mkdir()
    assert resolver(str(afuera), [str(raiz)]).estado == OK            # defaults de siempre
    assert resolver(str(afuera), [str(raiz)], confinar=True).estado == NO_ENCONTRADO


def test_el_ancla_gana_sobre_el_homonimo(tmp_path):
    """Si el ancla da una ruta concreta, no se cae al índice aunque haya homónimos."""
    exacto = _tocar(tmp_path / "GROOOOVE" / "track.wav")
    _tocar(tmp_path / "otra" / "track.wav")
    r = resolver("C:/x/Escritorio/GROOOOVE/track.wav", [str(tmp_path)],
                 construir_indice([str(tmp_path)]))
    assert r.estado == OK and r.ruta == str(exacto)


def test_un_location_que_es_una_carpeta_no_es_un_audio(tmp_path):
    """Auditoría f53: `exists` aceptaba una CARPETA como el archivo del tema."""
    raiz = tmp_path / "musica"
    carpeta = raiz / "Techno" / "parece.wav"      # una carpeta con nombre de audio
    carpeta.mkdir(parents=True)
    r = resolver(str(carpeta), [str(raiz)], confinar=True)
    assert r.estado != OK and r.ruta is None, f"una carpeta resolvió como audio: {r}"
    # Por el ancla tampoco: <ancla>/Techno/parece.wav cuelga de la raíz y es una carpeta.
    r = resolver("C:/Users/x/Escritorio/Techno/parece.wav", [str(raiz)])
    assert r.estado != OK, f"por el ancla, una carpeta resolvió como audio: {r}"


def test_por_cola_no_sigue_dos_puntos(tmp_path):
    """Auditoría f53 (por separado de `confinar`): una cola con '..' saldría de la raíz."""
    from ground_truth.resolver import _por_cola
    afuera = _tocar(tmp_path / "secreto" / "a.wav")
    raiz = tmp_path / "musica"
    raiz.mkdir()
    assert _por_cola("C:/x/../secreto/a.wav", [str(raiz)]) == [], "la cola con '..' salió de la raíz"
    # La misma cola sin '..' sí se encuentra (el archivo existe y la función anda).
    assert _por_cola("C:/otra/secreto/a.wav", [str(tmp_path)]) == [str(afuera)]
