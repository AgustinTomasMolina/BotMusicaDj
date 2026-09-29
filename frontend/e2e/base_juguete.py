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
    CATALOGO,
    LICENCIA,
    ORIGEN,
    armar_base_radio,
    embedding,
    location,
    pistas_biblioteca,
    pistas_caratulas,
    wav,
    xml_rekordbox,
)

SEGUNDOS = 60.0


def _tamano_png(datos: bytes) -> list[int]:
    """[ancho, alto] de un PNG, de su chunk IHDR (bytes 16..24)."""
    return [int.from_bytes(datos[16:20], "big"), int.from_bytes(datos[20:24], "big")]


def _pistas_radio(destino: Path, db: Path) -> tuple[list, dict]:
    """Desde f33 la radio arma desde una playlist, y a una playlist los temas con archivo
    entran desde la home (`lib_id`). Así que los archivos de la base del motor también van
    al XML, como en la vida real (la base del motor es la colección de Rekordbox escaneada).

    Con "· radio" en el nombre: la home ya tiene un "Uno", un "Dos"… y los casos de la home
    los buscan por título. Además del CATALOGO:
    - `seis.wav`: en la base del motor, mezclable con Uno (129.0 BPM, 8A) — el E2E lo pone en
      la playlist como Hard Bounce y verifica que un set de Techno no lo muestre.
    - `click.wav`: 10 s de `motor.sintetico` (BPM y tonalidad conocidos), SIN analizar: el
      caso del análisis en segundo plano lo analiza desde la pantalla.
    - `roto.wav`: bytes que no son audio. Su análisis falla, y la pantalla tiene que decirlo.
    Devuelve (filas del XML, id del XML por nombre de archivo)."""
    import numpy as np
    import soundfile as sf

    from motor.modelos import TrackFeatures
    from motor.sintetico import click_track
    from motor.store import Store

    raiz = destino / "radio"
    wav(raiz / "seis.wav", 777.0, SEGUNDOS)
    with Store(db) as store:
        store.upsert(raiz / "seis.wav",
                     TrackFeatures(bpm=129.0, key="8A", energy_raw=0.35, embedding=embedding(10),
                                   key_acuerdo="3/3", key_tramos="8A|8A|8A"),
                     duration=250.0, license=LICENCIA, source_url=ORIGEN, artist="Artista G",
                     title="Seis")
    y, sr = click_track(126.0, dur=10.0, nota="A", modo="min")
    sf.write(str(raiz / "click.wav"), y.astype(np.float32), sr, subtype="FLOAT")
    (raiz / "roto.wav").write_bytes(b"esto no es audio" * 8)

    nombres = [c[0] for c in CATALOGO] + ["seis.wav", "click.wav", "roto.wav"]
    ids = {n: str(901 + i) for i, n in enumerate(nombres)}
    filas = [dict(id=ids[n], name=f"{Path(n).stem} · radio", artist="E2E", genre="Radio E2E",
                  bpm="0.00", ton="", dur="60", loc=location(raiz / n)) for n in nombres]
    return filas, ids


def main(destino: Path) -> dict:
    db = destino / "djradio" / "biblioteca.sqlite"
    armar_base_radio(destino / "radio", db, SEGUNDOS)
    radio, radio_ids = _pistas_radio(destino, db)

    raiz = destino / "musica"
    _, pistas = pistas_biblioteca(raiz, destino / "no-existe" / "fantasma.wav", SEGUNDOS)
    imagenes, caratulas = pistas_caratulas(raiz, SEGUNDOS)
    xml = destino / "rekordbox.xml"
    xml.write_text(xml_rekordbox(pistas + caratulas + radio), encoding="utf-8")
    return {
        "djradio_db": str(db),
        "library_xml": str(xml),
        "library_roots": [str(raiz), str(destino / "radio")],
        # id del XML (el `lib_id` de la home) de cada archivo de la radio, para armar las
        # playlists por la API en el setup del E2E (pantalla.mjs, `prepararPlaylists`).
        "radio_lib": radio_ids,
        "catalogo": [{"archivo": c[0], "artista": c[6], "titulo": c[7]} for c in CATALOGO],
        # Qué tiene que terminar mostrando cada tarjeta de la home: id → [ancho, alto] de SU
        # imagen. Los ids 1 y 2 traen PNG de tamaños distintos (2×2 y 3×1, leídos de la
        # cabecera de los bytes que se embebieron), así una tarjeta que muestra la carátula de
        # otro tema no pasa. El 3 trae un "JPEG" que es solo la cabecera (la API lo sirve con
        # 200 y el navegador no lo puede decodificar: onError → placeholder); el resto no trae
        # carátula (404).
        "caratula_dibujable": {"1": _tamano_png(imagenes["png"]),
                               "2": _tamano_png(imagenes["png_otro"])},
    }


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]))))
