"""Base de juguete para el E2E del front (`npm run e2e`, ver frontend/e2e/run.mjs).

Arma en `destino` (un directorio temporal que crea y borra run.mjs) las dos bibliotecas que
lee MusiFlix, con los MISMOS catálogos que usan los tests de la API (tests/sinteticos.py):

- la del motor (radio): `CATALOGO` en una base SQLite escrita con `Store.upsert`;
- la local (home): las pistas de `test_server_biblioteca.py` —las de la biblioteca y las de
  carátulas— en un XML de Rekordbox mínimo.

La única diferencia con los tests de Python es el largo de los WAV: allá duran 0.25 s y acá
un minuto, porque el E2E mira la pantalla MIENTRAS suenan (un audio que termina solo cambia
los botones antes de que se los pueda mirar).

Imprime un JSON con las rutas y qué carátula tiene cada track de la home.
"""
import json
import sys
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(RAIZ_REPO), str(RAIZ_REPO / "tests")]

from sinteticos import (  # noqa: E402
    armar_base_radio,
    biblioteca_importable,
    pistas_biblioteca,
    pistas_caratulas,
    xml_rekordbox,
)

SEGUNDOS = 60.0


def importables(raiz: Path, destino: Path) -> dict:
    """f53: lo que se importa en el E2E, adentro de la raíz de música permitida.

    - `Importar/…`: la colección de `biblioteca_importable` (Locations percent-encoded, uno
      reubicado desde otra PC, un homónimo ambiguo, uno que no está); su XML queda FUERA de la
      raíz, en `destino`, como el que el dueño elige con «Elegir el XML…».
    - `Importar/Set`: dos clics del generador de `motor.sintetico` (BPM conocido) con tags de
      título y artista y sin género, para importar como carpeta y analizar con el motor."""
    import numpy as np
    import soundfile as sf
    from mutagen.id3 import TIT2, TPE1
    from mutagen.wave import WAVE

    from motor.sintetico import click_track

    xml, _ = biblioteca_importable(raiz / "Importar", segundos=4.0)
    ruta_xml = destino / "coleccion de prueba.xml"
    ruta_xml.write_text(xml, encoding="utf-8")
    carpeta = raiz / "Importar" / "Set"
    carpeta.mkdir(parents=True, exist_ok=True)
    for i, (nombre, bpm, titulo) in enumerate((("01 kick.wav", 128.0, "Kick Uno"),
                                               ("02 kick.wav", 132.0, "Kick Dos"))):
        y, sr = click_track(bpm, dur=8.0, nota="A", modo="min", seed=i)
        ruta = carpeta / nombre
        sf.write(str(ruta), y.astype(np.float32), sr, subtype="PCM_16")
        audio = WAVE(str(ruta))
        audio.add_tags()
        audio.tags.add(TIT2(encoding=3, text=titulo))
        audio.tags.add(TPE1(encoding=3, text="Generador"))
        audio.save()
    return {"importar_xml": str(ruta_xml)}


def _tamano_png(datos: bytes) -> list[int]:
    """[ancho, alto] de un PNG, de su chunk IHDR (bytes 16..24)."""
    return [int.from_bytes(datos[16:20], "big"), int.from_bytes(datos[20:24], "big")]


def main(destino: Path) -> dict:
    db = destino / "djradio" / "biblioteca.sqlite"
    armar_base_radio(destino / "radio", db, SEGUNDOS)

    raiz = destino / "musica"
    _, pistas = pistas_biblioteca(raiz, destino / "no-existe" / "fantasma.wav", SEGUNDOS)
    imagenes, caratulas = pistas_caratulas(raiz, SEGUNDOS)
    xml = destino / "rekordbox.xml"
    xml.write_text(xml_rekordbox(pistas + caratulas), encoding="utf-8")
    return {
        "djradio_db": str(db),
        "library_xml": str(xml),
        "library_roots": [str(raiz)],
        # Qué tiene que terminar mostrando cada tarjeta de la home: id → [ancho, alto] de SU
        # imagen. Los ids 1 y 2 traen PNG de tamaños distintos (2×2 y 3×1, leídos de la
        # cabecera de los bytes que se embebieron), así una tarjeta que muestra la carátula de
        # otro tema no pasa. El 3 trae un "JPEG" que es solo la cabecera (la API lo sirve con
        # 200 y el navegador no lo puede decodificar: onError → placeholder); el resto no trae
        # carátula (404).
        "caratula_dibujable": {"1": _tamano_png(imagenes["png"]),
                               "2": _tamano_png(imagenes["png_otro"])},
        **importables(raiz, destino),
    }


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]))))
