"""Tests del export M3U8 (`motor/export.py`).

El bug caro de este módulo es el ORDEN: un M3U8 con los mismos tracks en otro orden es
otro set, y un test que cuente líneas no lo ve. Por eso acá el archivo escrito se PARSEA
DE VUELTA y se compara ruta por ruta contra los tracks de entrada.

El parser de los tests (`_parse_m3u8`) es a propósito ingenuo — corta por "\\n" y strippea
— para que la decisión de fin de línea del módulo (CRLF, ver `export.NEWLINE`) quede
cubierta por un consumidor real y no por una constante copiada del código bajo prueba.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from motor.export import write_m3u8  # noqa: E402
from motor.modelos import Track  # noqa: E402

LICENCIA = "CC-BY-4.0"
ORIGEN = "https://ejemplo.test/track"


def _track(nombre, bpm=128.0, key="8A", energy=0.5, duration=300.0, artist="Artista",
           title="Titulo", carpeta="C:/musica"):
    """Track de prueba. Los valores no salen de ningún analizador: son datos de entrada
    del export, que no mide nada — solo escribe lo que le dan."""
    return Track(
        path=Path(carpeta) / nombre,
        duration=duration,
        bpm=bpm,
        key=key,
        energy=energy,
        embedding=np.zeros(4, dtype=np.float32),
        license=LICENCIA,
        source_url=ORIGEN,
        artist=artist,
        title=title,
    )


def _crudo(destino: Path) -> str:
    """El archivo tal cual está en disco, SIN traducción de saltos de línea.

    `Path.read_text` abre en modo texto y convierte cada "\\r\\n" en "\\n": leído así, el
    CRLF que escribe el módulo sería invisible y los tests estarían mirando otro archivo
    que el que va a abrir Rekordbox. Por eso se leen bytes y se decodifica a mano.
    """
    return destino.read_bytes().decode("utf-8")


def _lineas(destino: Path) -> list[str]:
    """Parser ingenuo a propósito: corta por "\\n" y strippea, como cualquier lector
    casero. El strip es lo que absorbe el "\\r" del CRLF; sin él cada ruta saldría con un
    "\\r" pegado, que es el modo de falla conocido del CRLF y por eso se modela acá."""
    return [line.strip() for line in _crudo(destino).split("\n") if line.strip()]


def _parse_m3u8(destino: Path) -> list[str]:
    """Las rutas del M3U8, en orden, como las tomaría un reproductor."""
    return [line for line in _lineas(destino) if not line.startswith("#")]


def _extinf(destino: Path) -> list[str]:
    return [line for line in _lineas(destino) if line.startswith("#EXTINF")]


def _djradio(destino: Path) -> list[str]:
    return [line for line in _lineas(destino) if line.startswith("#DJRADIO")]


def test_el_orden_del_set_sobrevive_al_round_trip(tmp_path):
    """Lo único que Rekordbox tiene que respetar. Se escriben cinco tracks cuyo nombre no
    está ordenado alfabéticamente, justamente para que un export que ordene por nombre
    (o que use un dict/set por el medio) falle acá."""
    tracks = [_track(f"{n}.mp3") for n in ("zulu", "alfa", "mike", "bravo", "kilo")]
    destino = write_m3u8(tracks, tmp_path / "set.m3u8")

    rutas = _parse_m3u8(destino)
    assert rutas == [str(t.path) for t in tracks]
    # Y que no sea el orden alfabético por casualidad.
    assert rutas != sorted(rutas)


def test_cabecera_y_estructura_de_tres_lineas_por_track(tmp_path):
    tracks = [_track("a.mp3", bpm=124.5, key="9A", energy=0.42, duration=301.4),
              _track("b.mp3", bpm=126.0, key="9B", energy=0.61, duration=180.0)]
    destino = write_m3u8(tracks, tmp_path / "set.m3u8")
    lines = _lineas(destino)

    assert lines[0] == "#EXTM3U"
    assert lines[1] == "#EXTINF:301,Artista — Titulo"
    assert lines[2] == "#DJRADIO:bpm=124.5 key=9A energy=0.42"
    assert lines[3] == str(Path("C:/musica/a.mp3"))
    assert lines[4] == "#EXTINF:180,Artista — Titulo"
    assert lines[5] == "#DJRADIO:bpm=126.0 key=9B energy=0.61"
    assert lines[6] == str(Path("C:/musica/b.mp3"))
    assert len(lines) == 7


def test_el_bpm_sale_con_un_decimal(tmp_path):
    """spec §6: redondear el BPM a entero es mentir. 128.0 y 128.4 no mezclan igual."""
    tracks = [_track("a.mp3", bpm=128.0), _track("b.mp3", bpm=128.4),
              _track("c.mp3", bpm=127.96)]
    destino = write_m3u8(tracks, tmp_path / "set.m3u8")

    bpms = [ln.split("bpm=")[1].split(" ")[0] for ln in _djradio(destino)]
    assert bpms == ["128.0", "128.4", "128.0"], "el BPM tiene que ir con UN decimal"


def test_relative_to_adentro_del_arbol_sale_relativa(tmp_path):
    root = tmp_path / "pendrive"
    tracks = [_track("uno.mp3", carpeta=root / "techno"),
              _track("dos.mp3", carpeta=root / "house" / "deep")]
    destino = write_m3u8(tracks, tmp_path / "set.m3u8", relative_to=root)

    assert _parse_m3u8(destino) == [str(Path("techno/uno.mp3")), str(Path("house/deep/dos.mp3"))]


def test_relative_to_afuera_del_arbol_queda_absoluta(tmp_path):
    """Una ruta fuera del árbol NO se puede relativizar sin mentir: el archivo no va a
    estar en el destino. Se deja absoluta, que al menos es cierta en esta máquina."""
    root = tmp_path / "pendrive"
    adentro = _track("uno.mp3", carpeta=root / "techno")
    afuera = _track("dos.mp3", carpeta=tmp_path / "otra_carpeta")
    destino = write_m3u8([adentro, afuera], tmp_path / "set.m3u8", relative_to=root)

    rutas = _parse_m3u8(destino)
    assert rutas[0] == str(Path("techno/uno.mp3"))
    assert rutas[1] == str(afuera.path)
    assert Path(rutas[1]).is_absolute()


def test_track_sin_artista_ni_titulo_usa_el_stem_del_archivo(tmp_path):
    tracks = [_track("mi archivo raro.mp3", artist=None, title=None),
              _track("otro.mp3", artist=None, title="Solo Titulo"),
              _track("tercero.mp3", artist="Solo Artista", title=None)]
    destino = write_m3u8(tracks, tmp_path / "set.m3u8")

    labels = [ln.split(",", 1)[1] for ln in _extinf(destino)]
    assert labels == ["mi archivo raro", "Solo Titulo", "tercero"]


def test_duracion_desconocida_va_como_menos_uno(tmp_path):
    """-1 es el 'no sé cuánto dura' del formato. Un 0 se muestra como track de duración
    cero, que es un dato falso (spec §6)."""
    destino = write_m3u8([_track("a.mp3", duration=0.0)], tmp_path / "set.m3u8")
    assert _extinf(destino) == ["#EXTINF:-1,Artista — Titulo"]


def test_fin_de_linea_crlf_fijo_y_salto_final(tmp_path):
    """La decisión está escrita en `export.NEWLINE`: CRLF en toda plataforma, para que el
    mismo set exportado en dos máquinas dé el mismo archivo (determinismo, spec §5).

    LÍMITE CONOCIDO de este test: detecta que alguien elija LF (se verificó rompiéndolo),
    pero NO detecta que alguien saque el `newline=""` y deje decidir al sistema operativo,
    porque en Windows el default del sistema YA es CRLF y la salida sale idéntica. Ese
    mutante solo se cae corriendo la suite en Linux o macOS. Anotado en vez de disimulado:
    un test que no vieron fallar es un test ciego (CONTRIBUTING.md).
    """
    destino = write_m3u8([_track("a.mp3")], tmp_path / "set.m3u8")
    crudo = destino.read_bytes()

    assert crudo.startswith(b"#EXTM3U\r\n")
    assert crudo.endswith(b"\r\n")
    assert crudo.count(b"\n") == crudo.count(b"\r\n") == 4, "algún salto quedó sin \\r"
    assert b"\xef\xbb\xbf" not in crudo, "el BOM le rompe la cabecera a algunos parsers"


def test_un_salto_de_linea_en_el_titulo_no_rompe_la_estructura(tmp_path):
    """Un tag con saltos adentro (pasa con descripciones pegadas de Bandcamp o YouTube)
    partía el #EXTINF en dos, y el pedazo de abajo quedaba como un renglón suelto que el
    reproductor lee como RUTA: un archivo roto en la playlist (anotado en la tarea #15).
    Se prueban los saltos de Windows, Unix y Mac viejo y el separador Unicode, cada uno
    en el artista o en el título."""
    tracks = [_track("a.mp3", title="Real Love\r\n(ONYX002)"),
              _track("b.mp3", artist="Cuatro\nMil", title="Hz"),
              _track("c.mp3", artist=None, title="Linea\runo dos"),
              _track("d.mp3", title=" Normal  con  dobles   espacios")]
    destino = write_m3u8(tracks, tmp_path / "set.m3u8")
    crudo = _crudo(destino)

    assert _parse_m3u8(destino) == [str(t.path) for t in tracks], \
        "un salto en un título metió un renglón suelto que se lee como ruta"
    assert _extinf(destino) == ["#EXTINF:300,Artista — Real Love (ONYX002)",
                                "#EXTINF:300,Cuatro Mil — Hz",
                                "#EXTINF:300,Linea uno dos",
                                "#EXTINF:300,Artista —  Normal  con  dobles   espacios"]
    # Un título SIN saltos sale tal cual, espacios incluidos (el parser de arriba strippea,
    # por eso se mira el crudo).
    assert "\r\n#EXTINF:300,Artista —  Normal  con  dobles   espacios\r\n" in crudo
    # 1 cabecera + 3 renglones por track, y ningún salto que no sea el CRLF del formato.
    assert crudo.splitlines() == crudo.split("\r\n")[:-1], "quedó un salto suelto adentro"
    assert len(crudo.splitlines()) == 1 + 3 * len(tracks)


def test_set_vacio_escribe_solo_la_cabecera(tmp_path):
    destino = write_m3u8([], tmp_path / "vacio.m3u8")
    assert destino.read_bytes() == b"#EXTM3U\r\n"
    assert _parse_m3u8(destino) == []


def test_devuelve_la_ruta_escrita_y_acepta_str(tmp_path):
    destino = write_m3u8([_track("a.mp3")], str(tmp_path / "set.m3u8"))
    assert destino == tmp_path / "set.m3u8"
    assert _parse_m3u8(destino) == [str(Path("C:/musica/a.mp3"))]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
