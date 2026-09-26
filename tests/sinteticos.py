"""Datos sintéticos compartidos: los catálogos de los tests del server y del E2E del front.

Viven acá (y no adentro de cada archivo de test) porque los usan dos consumidores:

- `tests/test_server_radio.py` y `tests/test_server_biblioteca.py`, que prueban la API.
- `frontend/e2e/base_juguete.py`, que arma la MISMA base para el E2E de la pantalla
  (`npm run e2e`). Un catálogo propio del E2E sería otro juego de datos que se desincroniza
  en silencio: la pantalla se compararía contra una biblioteca que la API nunca probó.

No empieza con `test_`: pytest no lo colecta. Nada de audio real ni de rutas personales.

Sobre el BPM y la key: se ESCRIBEN (en la base del motor o en el XML) y los tests verifican
que se sirvan y se muestren tal cual. No son el resultado de un análisis inventado: lo que se
prueba es la capa que los sirve y los dibuja, no la que los mide (esa se prueba contra ground
truth en `motor/tests`).
"""
import math
import struct
import wave
from pathlib import Path
from urllib.parse import quote

import numpy as np

LICENCIA = "compra personal"
ORIGEN = "biblioteca personal"

# Biblioteca del MOTOR (la de la radio).
# (nombre, bpm, camelot, acuerdo, tramos, duración declarada, artista, título, energía cruda)
# Los BPM de los tres primeros caen adentro del ±8% entre sí (§4), así que un set los puede
# encadenar. `cuatro` tiene BPM 0.0 —lo que devuelve el análisis sobre silencio— y por eso
# nunca mezcla: es el track que hace cortar al set sin que la biblioteca esté agotada.
# `loop` dura menos de DURACION_MINIMA_TRACK_S: no es un track.
CATALOGO = [
    # El BPM de la semilla NO es redondo a propósito: §6 dice que redondearlo a entero es
    # mentir, y con un 128.0 un `round(bpm)` pasaría el test igual.
    ("uno.wav", 128.4, "8A", "3/3", "8A|8A|8A", 240.0, "Artista A", "Uno", 0.30),
    ("dos.wav", 130.0, "9A", "2/3", "9A|8A|9A", 300.0, "Artista B", "Dos", 0.20),
    ("tres.wav", 126.0, "8B", None, None, 210.0, "Artista C", "Tres", 0.10),
    ("cuatro.wav", 0.0, "", "0/0", "", 195.0, "Artista D", "Cuatro", 0.05),
    ("loop.wav", 126.0, "8B", "3/3", "8B|8B|8B", 10.0, "Artista E", "Loop", 0.40),
    # La MITAD del BPM de la semilla: el motor lo considera mezclable leyéndolo a doble
    # tiempo (64.2 × 2 = 128.4) y lo dice en el motivo. Está acá para que el test distinga
    # "el motivo lo da el motor" de "el motivo se recalcula en el server": una resta cruda
    # de BPM sobre esta transición diría -50%, y el motor dice +0.0% (doble tiempo).
    ("cinco.wav", 64.2, "8A", "3/3", "8A|8A|8A", 280.0, "Artista F", "Cinco", 0.25),
]


def wav(ruta: Path, freq: float, segundos: float = 0.25, sr: int = 22050) -> bytes:
    """WAV mono 16 bit con un seno: chico, determinista y distinto por frecuencia.

    Hasta un segundo se calcula muestra por muestra. Más largo (el E2E necesita audio que
    siga sonando mientras mira la pantalla), se repite el primer segundo: con una frecuencia
    entera el seno completa ciclos enteros en un segundo y la costura no se nota. Así un
    archivo de un minuto no cuesta un millón de `math.sin`.
    """
    n = int(sr * segundos)
    m = min(n, sr)
    uno = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / sr)))
                   for i in range(m))
    frames = (uno * (n // m + 1))[: 2 * n] if m else b""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)
    return ruta.read_bytes()


def embedding(i: int) -> np.ndarray:
    """Vector de timbre determinista y distinto por track (la semilla del RNG es el índice)."""
    from motor.embeddings import DIM

    return np.random.default_rng(i).normal(size=DIM).astype(np.float32)


def armar_base_radio(raiz: Path, db: Path, segundos: float = 0.25) -> tuple[dict, dict]:
    """Base del motor con el CATALOGO y sus WAVs en `raiz`. Devuelve (bytes, rutas) por
    nombre de archivo.

    La `duration` que guarda la base es la declarada en el `upsert`, no la del WAV: los
    archivos son cortos para que la suite no escriba megabytes, y la radio decide con la
    duración de la BASE."""
    from motor.modelos import TrackFeatures
    from motor.store import Store

    audios, rutas = {}, {}
    with Store(db) as store:
        for i, (nombre, bpm, key, ac, tr, dur, artista, titulo, e) in enumerate(CATALOGO):
            ruta = raiz / nombre
            audios[nombre] = wav(ruta, 220.0 + 110.0 * i, segundos)
            rutas[nombre] = ruta
            store.upsert(ruta,
                         TrackFeatures(bpm=bpm, key=key, energy_raw=e, embedding=embedding(i),
                                       key_acuerdo=ac, key_tramos=tr),
                         duration=dur, license=LICENCIA, source_url=ORIGEN,
                         artist=artista, title=titulo)
    return audios, rutas


# ------------------------------------------------------- biblioteca local (XML de Rekordbox)

def location(ruta: Path) -> str:
    """Location como la exporta Rekordbox: file://localhost/<ruta con /, url-encoded>."""
    return "file://localhost/" + quote(ruta.as_posix().lstrip("/"))


def xml_rekordbox(tracks: list[dict]) -> str:
    filas = "\n".join(
        '    <TRACK TrackID="{id}" Name="{name}" Artist="{artist}" Genre="{genre}" '
        'AverageBpm="{bpm}" Tonality="{ton}" TotalTime="{dur}" Kind="WAV File" '
        'Location="{loc}"/>'.format(**t)
        for t in tracks)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<DJ_PLAYLISTS Version="1.0.0">\n'
            f'  <COLLECTION Entries="{len(tracks)}">\n{filas}\n  </COLLECTION>\n</DJ_PLAYLISTS>\n')


def pistas_biblioteca(raiz: Path, fantasma: Path, segundos: float = 0.25) -> tuple[dict, list]:
    """Biblioteca de 5 tracks: 4 con audio (2 Techno, 1 House, 1 sin género) y 1 Techno
    cuyo archivo (`fantasma`) no existe en ninguna raíz → no resuelve y tiene que quedar
    afuera. Devuelve (bytes por id, filas del XML)."""
    audios = {
        "11": wav(raiz / "techno" / "uno.wav", 220.0, segundos),
        "12": wav(raiz / "techno" / "dos.wav", 330.0, segundos),
        "21": wav(raiz / "house" / "tres.wav", 440.0, segundos),
        "31": wav(raiz / "otros" / "cuatro.wav", 550.0, segundos),
    }
    # House va primero en el XML a propósito: así el orden "más temas primero" no coincide
    # con el orden de aparición y el test detecta si se pierde el sort.
    tracks = [
        dict(id="21", name="Tres", artist="Artista C", genre="House", bpm="124.50", ton="C",
             dur="200", loc=location(raiz / "house" / "tres.wav")),
        dict(id="11", name="Uno", artist="Artista A", genre="Techno", bpm="128.00", ton="Am",
             dur="245", loc=location(raiz / "techno" / "uno.wav")),
        dict(id="99", name="Fantasma", artist="Nadie", genre="Techno", bpm="130.00", ton="Fm",
             dur="300", loc=location(fantasma)),
        dict(id="12", name="Dos", artist="Artista B", genre="Techno", bpm="0.00", ton="",
             dur="180", loc=location(raiz / "techno" / "dos.wav")),
        dict(id="31", name="Cuatro", artist="", genre="", bpm="140.26", ton="4A",
             dur="90", loc=location(raiz / "otros" / "cuatro.wav")),
    ]
    return audios, tracks


# La home no mostraba carátulas porque el XML de Rekordbox no trae imágenes. La carátula que
# existe de verdad es la que viene EMBEBIDA en el archivo: se escribe con mutagen (como lo
# hace tagger.py al descargar).

def png(ancho: int = 2, alto: int = 2, rgb=(200, 30, 90)) -> bytes:
    """PNG real y chico (con CRC válidos), para no depender de Pillow."""
    import zlib

    def chunk(tipo: bytes, datos: bytes) -> bytes:
        return (struct.pack(">I", len(datos)) + tipo + datos
                + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF))
    fila = b"\x00" + bytes(rgb) * ancho
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(fila * alto)) + chunk(b"IEND", b""))


# Cabecera JFIF + relleno: el endpoint no decodifica la imagen, solo la devuelve tal cual.
# (Un navegador NO la puede dibujar: el E2E la usa para ver que una imagen rota termina en
# el placeholder y no en un marco vacío.)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + bytes(range(64))


def wav_con_apic(ruta: Path, imagen: bytes, mime: str, antes: bytes | None = None,
                 segundos: float = 0.25) -> None:
    """WAV con la carátula como APIC tipo 3 (tapa). `antes`: otra imagen de tipo 0 (otra)
    escrita PRIMERO, para ver que se elige la tapa y no la primera que aparece."""
    from mutagen.id3 import APIC
    from mutagen.wave import WAVE
    wav(ruta, 300.0, segundos)
    audio = WAVE(str(ruta))
    audio.add_tags()
    if antes is not None:
        audio.tags.add(APIC(encoding=3, mime="image/png", type=0, desc="Otra", data=antes))
    audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=imagen))
    audio.save()


def flac_con_picture(ruta: Path, imagen: bytes) -> None:
    import soundfile as sf
    from mutagen.flac import FLAC, Picture
    ruta.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(ruta), (0.2 * np.sin(np.linspace(0, 200, 4410))).astype("float32"), 22050,
             format="FLAC")
    f = FLAC(str(ruta))
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/jpeg", imagen
    f.add_picture(pic)
    f.save()


def pistas_caratulas(raiz: Path, segundos: float = 0.25) -> tuple[dict, list]:
    """4 tracks: WAV con APIC PNG, WAV con APIC cuyo MIME declarado miente, FLAC con
    PICTURE JPEG y WAV sin carátula. Devuelve (las imágenes, filas del XML)."""
    imagenes = {"png": png(), "png_otro": png(3, 1, (10, 220, 40))}
    wav_con_apic(raiz / "con_png.wav", imagenes["png"], "image/png",
                 antes=png(1, 1, (0, 0, 255)), segundos=segundos)
    # Hay APIC con MIME vacío o "image/jpg": el tipo sale del contenido, no del tag.
    wav_con_apic(raiz / "mime_miente.wav", imagenes["png_otro"], "image/jpg", segundos=segundos)
    flac_con_picture(raiz / "con_jpeg.flac", JPEG)
    wav(raiz / "sin_tapa.wav", 500.0, segundos)
    tracks = [
        dict(id="1", name="Con PNG", artist="A", genre="Techno", bpm="128.00", ton="Am", dur="1",
             loc=location(raiz / "con_png.wav")),
        dict(id="2", name="Mime", artist="B", genre="Techno", bpm="128.00", ton="Am", dur="1",
             loc=location(raiz / "mime_miente.wav")),
        dict(id="3", name="Con JPEG", artist="C", genre="House", bpm="124.00", ton="C", dur="1",
             loc=location(raiz / "con_jpeg.flac")),
        dict(id="4", name="Sin tapa", artist="D", genre="House", bpm="124.00", ton="C", dur="1",
             loc=location(raiz / "sin_tapa.wav")),
    ]
    return imagenes, tracks
