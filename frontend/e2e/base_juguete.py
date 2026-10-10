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
# f53: la segunda raíz de música, la de lo que se importa (su nombre es lo que ve la pantalla).
RAIZ_IMPORTAR = "Musica DJ"


def importables(raiz: Path, destino: Path) -> dict:
    """f53: lo que se importa en el E2E, en `raiz` (una SEGUNDA raíz de música permitida).

    - la colección de `biblioteca_importable` colgando de la raíz (Locations percent-encoded,
      uno reubicado desde otra PC, un homónimo ambiguo, uno que no está). Va en la raíz misma y
      no en una subcarpeta: el resolver cuelga la cola de la ruta del XML de cada raíz
      ('<otra PC>/Music/Techno/x.wav' → '<raíz>/Techno/x.wav'), como en la PC del dueño. Su XML
      queda FUERA de las raíces, en `destino`, como el que se elige con «Elegir el XML…»;
    - `Set`: dos clics del generador de `motor.sintetico` (BPM conocido) con tags de título y
      artista y sin género, para importar como carpeta y analizar con el motor."""
    import numpy as np
    import soundfile as sf
    from mutagen.id3 import TIT2, TPE1
    from mutagen.wave import WAVE

    from motor.sintetico import click_track

    xml, _ = biblioteca_importable(raiz, segundos=4.0)
    ruta_xml = destino / "coleccion de prueba.xml"
    ruta_xml.write_text(xml, encoding="utf-8")
    carpeta = raiz / "Set"
    carpeta.mkdir(parents=True, exist_ok=True)
    generados = {}
    for i, (nombre, bpm, titulo) in enumerate((("01 kick.wav", 128.0, "Kick Uno"),
                                               ("02 kick.wav", 132.0, "Kick Dos"))):
        generados[titulo] = bpm
        y, sr = click_track(bpm, dur=8.0, nota="A", modo="min", seed=i)
        ruta = carpeta / nombre
        sf.write(str(ruta), y.astype(np.float32), sr, subtype="PCM_16")
        audio = WAVE(str(ruta))
        audio.add_tags()
        audio.tags.add(TIT2(encoding=3, text=titulo))
        audio.tags.add(TPE1(encoding=3, text="Generador"))
        audio.save()
    # `importar_set`: título → BPM con que se GENERÓ cada clic (el ground truth contra el que
    # se compara lo que mide el motor; nunca un número escrito en el caso del E2E).
    return {"importar_xml": str(ruta_xml), "importar_set": generados, "importar_raiz": raiz.name}


CARPETA_EDITOR = "Editor"


def editor_d(raiz: Path) -> dict:
    """f54: los temas del editor grande de cues (opción D), en `raiz/Editor`.

    Tres WAV generados con lo que se conoce por construcción (nunca medido ni inventado):

    - «Con grilla»: four-on-the-floor a 128 BPM (kick de 55 Hz, hat a contratiempo y stab en la
      segunda semicorchea: los mismos sonidos que `motor/tests/test_bandas.py`), con el
      primer kick a `primer_kick_s` y una tríada de La menor de fondo (key 8A · Am);
    - «Sin grilla»: el MISMO kick en cada corchea a 128 BPM: el período es limpio pero el beat
      no se distingue del contratiempo, así que el motor no declara grilla (y dice por qué);
    - «Sin BPM»: un pad sin golpes; la base lo guarda con BPM 0 (= el análisis no encontró
      pulso), como `Cuatro` del catálogo.

    Acá solo se escriben los archivos; el alta en la base del motor la hace el caso del E2E
    (`base_mutar.py editor_alta`) y la deshace al terminar: los casos de la radio, que corren
    antes, ven la base del catálogo tal cual. Devuelve título → {archivo, bpm, camelot, ...}."""
    import numpy as np
    import soundfile as sf

    sr = 22050
    dur = 48.0
    t = np.arange(int(dur * sr)) / sr
    carpeta = raiz / CARPETA_EDITOR
    carpeta.mkdir(parents=True, exist_ok=True)

    def sonido(f, largo, caida, amp):
        x = np.arange(int(largo * sr)) / sr
        return amp * np.sin(2 * np.pi * f * x) * np.exp(-x * caida)

    kick = sonido(55, 0.12, 28, 1.0)
    hat = sonido(7000, 0.03, 150, 0.25) + sonido(9100, 0.03, 150, 0.25)
    stab = sonido(600, 0.08, 40, 0.4) + sonido(900, 0.08, 40, 0.2)

    def pegar(y, s, inst):
        i = int(round(inst * sr))
        if i >= 0 and i + len(s) <= len(y):
            y[i:i + len(s)] += s

    def patron(bpm, desfase, corcheas=False):
        y = np.zeros_like(t)
        p = 60.0 / bpm
        j = 0
        while desfase + j * p < dur:
            b = desfase + j * p
            pegar(y, kick, b)
            if corcheas:
                pegar(y, kick, b + p / 2)
            else:
                pegar(y, hat, b + p / 2)
                pegar(y, stab, b + p / 4)
            j += 1
        return y

    # La tríada va baja a propósito: con 0.06 de amplitud (en vez de 0.018) la grilla estimada
    # cae ~50 ms antes del kick (medido al armar esta base; anotado en el informe de f54).
    triada = 0.018 * sum(np.sin(2 * np.pi * f * t) for f in (220.0, 261.63, 329.63))
    temas = {
        "Con grilla": {"archivo": "01 con grilla.wav", "bpm": 128.0, "camelot": "8A",
                       "primer_kick_s": 0.25, "y": patron(128.0, 0.25) + triada},
        "Sin grilla": {"archivo": "02 sin grilla.wav", "bpm": 128.0, "camelot": "",
                       "primer_kick_s": 0.1, "y": patron(128.0, 0.1, corcheas=True)},
        "Sin BPM": {"archivo": "03 sin bpm.wav", "bpm": 0.0, "camelot": "", "primer_kick_s": None,
                    "y": 0.4 * np.sin(2 * np.pi * 110 * t) * (1 + 0.3 * np.sin(2 * np.pi * 0.2 * t))},
    }
    out = {}
    for titulo, d in temas.items():
        ruta = carpeta / d["archivo"]
        sf.write(str(ruta), (0.5 * d.pop("y")).astype(np.float32), sr, subtype="PCM_16")
        out[titulo] = {**d, "ruta": str(ruta), "dur": dur}
    return out


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
        "library_roots": [str(raiz), str(destino / RAIZ_IMPORTAR)],
        # Qué tiene que terminar mostrando cada tarjeta de la home: id → [ancho, alto] de SU
        # imagen. Los ids 1 y 2 traen PNG de tamaños distintos (2×2 y 3×1, leídos de la
        # cabecera de los bytes que se embebieron), así una tarjeta que muestra la carátula de
        # otro tema no pasa. El 3 trae un "JPEG" que es solo la cabecera (la API lo sirve con
        # 200 y el navegador no lo puede decodificar: onError → placeholder); el resto no trae
        # carátula (404).
        "caratula_dibujable": {"1": _tamano_png(imagenes["png"]),
                               "2": _tamano_png(imagenes["png_otro"])},
        **importables(destino / RAIZ_IMPORTAR, destino),
        # f54: los temas del editor D (archivos en <raíz de importar>/Editor; el alta en la
        # base del motor la hace el caso que los usa).
        "editor_d": editor_d(destino / RAIZ_IMPORTAR),
        "editor_carpeta": CARPETA_EDITOR,
    }


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]))))
