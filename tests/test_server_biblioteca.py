"""Tests de /api/biblioteca y /api/audio/{id} (home de MusiFlix con la biblioteca local).

Todo sintético: un XML de Rekordbox mínimo escrito acá y WAVs chicos generados con el
módulo `wave` de la stdlib en tmp_path. Nada de audio real ni de rutas personales.

Importar `server` tiene efectos al cargar el módulo que no son de estos endpoints: crea la
carpeta de descargas, `db` arma el engine de SQLite en MUSIFLIX_DATA_DIR, `jobs` se conecta
a Redis si hay REDIS_URL y `load_dotenv()` mete el .env del repo en os.environ para el
resto de la sesión. El fixture `server` los neutraliza SOLO durante el import (descargas y
datos a tmp, sin REDIS_URL, load_dotenv inerte) sin tocar el código de producción. El
TestClient se usa sin `with`, así no corre el lifespan (que inicializa la base del
historial): estos endpoints no la usan.
"""
import importlib
import math
import struct
import sys
import wave
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("httpx")  # lo necesita el TestClient de Starlette
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    datos = tmp_path_factory.mktemp("server-datos")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MUSIFLIX_DATA_DIR", str(datos))
        mp.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
        mp.delenv("REDIS_URL", raising=False)
        mp.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        for mod in ("server", "jobs", "db"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
    return srv


@pytest.fixture
def client(server):
    return TestClient(server.app)


def _wav(ruta: Path, freq: float, segundos: float = 0.25, sr: int = 22050) -> bytes:
    """WAV mono 16 bit con un seno: chico, determinista y distinto por frecuencia."""
    n = int(sr * segundos)
    frames = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / sr)))
                      for i in range(n))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)
    return ruta.read_bytes()


def _location(ruta: Path) -> str:
    """Location como la exporta Rekordbox: file://localhost/<ruta con /, url-encoded>."""
    return "file://localhost/" + quote(ruta.as_posix().lstrip("/"))


def _xml(tracks: list[dict]) -> str:
    filas = "\n".join(
        '    <TRACK TrackID="{id}" Name="{name}" Artist="{artist}" Genre="{genre}" '
        'AverageBpm="{bpm}" Tonality="{ton}" TotalTime="{dur}" Kind="WAV File" '
        'Location="{loc}"/>'.format(**t)
        for t in tracks)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<DJ_PLAYLISTS Version="1.0.0">\n'
            f'  <COLLECTION Entries="{len(tracks)}">\n{filas}\n  </COLLECTION>\n</DJ_PLAYLISTS>\n')


@pytest.fixture
def biblioteca(server, tmp_path, monkeypatch):
    """Biblioteca de 5 tracks: 4 con audio (2 Techno, 1 House, 1 sin género) y 1 Techno
    cuyo archivo no existe en ninguna raíz → no resuelve y tiene que quedar afuera."""
    raiz = tmp_path / "musica"
    audios = {
        "11": _wav(raiz / "techno" / "uno.wav", 220.0),
        "12": _wav(raiz / "techno" / "dos.wav", 330.0),
        "21": _wav(raiz / "house" / "tres.wav", 440.0),
        "31": _wav(raiz / "otros" / "cuatro.wav", 550.0),
    }
    # House va primero en el XML a propósito: así el orden "más temas primero" no coincide
    # con el orden de aparición y el test detecta si se pierde el sort.
    tracks = [
        dict(id="21", name="Tres", artist="Artista C", genre="House", bpm="124.50", ton="C",
             dur="200", loc=_location(raiz / "house" / "tres.wav")),
        dict(id="11", name="Uno", artist="Artista A", genre="Techno", bpm="128.00", ton="Am",
             dur="245", loc=_location(raiz / "techno" / "uno.wav")),
        dict(id="99", name="Fantasma", artist="Nadie", genre="Techno", bpm="130.00", ton="Fm",
             dur="300", loc=_location(tmp_path / "no-existe" / "fantasma.wav")),
        dict(id="12", name="Dos", artist="Artista B", genre="Techno", bpm="0.00", ton="",
             dur="180", loc=_location(raiz / "techno" / "dos.wav")),
        dict(id="31", name="Cuatro", artist="", genre="", bpm="140.26", ton="4A",
             dur="90", loc=_location(raiz / "otros" / "cuatro.wav")),
    ]
    xml = tmp_path / "rekordbox.xml"
    xml.write_text(_xml(tracks), encoding="utf-8")
    monkeypatch.setattr(server, "_LIB_XML", str(xml))
    monkeypatch.setattr(server, "_LIB_ROOTS", [str(raiz)])
    monkeypatch.setattr(server, "_lib_audio", {})
    return {"raiz": raiz, "audios": audios}


# --------------------------------------------------------------------------- /api/biblioteca

def test_biblioteca_sin_configurar_responde_vacia(server, client, monkeypatch):
    monkeypatch.setattr(server, "_LIB_XML", "")
    monkeypatch.setattr(server, "_LIB_ROOTS", [])
    monkeypatch.setattr(server, "_lib_audio", {})
    r = client.get("/api/biblioteca")
    assert r.status_code == 200
    assert r.json() == {"total": 0, "configurada": False, "motivo": None, "generos": []}


def test_biblioteca_sin_ground_truth_degrada_sin_500(server, client, biblioteca, monkeypatch, caplog):
    # Como en la imagen Docker: las variables están, pero ground_truth/ no se copió.
    # Un None en sys.modules hace que `from ground_truth.rekordbox import ...` tire ImportError.
    monkeypatch.setitem(sys.modules, "ground_truth.rekordbox", None)
    with caplog.at_level("WARNING", logger="bot_web"):
        r = client.get("/api/biblioteca")
    assert r.status_code == 200
    d = r.json()
    assert (d["total"], d["configurada"], d["generos"]) == (0, False, [])
    assert "ground_truth" in d["motivo"], "el front tiene que poder explicar por qué está vacía"
    assert any("ground_truth" in m and "desactivada" in m for m in caplog.messages), \
        "sin warning en el log nadie se entera de que falta el lector"


def test_biblioteca_agrupa_por_genero_y_excluye_los_que_no_resuelven(client, biblioteca):
    d = client.get("/api/biblioteca").json()
    assert d["configurada"] is True and d["motivo"] is None
    assert d["total"] == 4, "el track 99 no tiene archivo: no debería contarse"
    # Orden: los géneros con más temas primero (Techno, aunque House aparece antes en el
    # XML); empate → orden de aparición.
    assert [(g["genero"], [t["id"] for t in g["tracks"]]) for g in d["generos"]] == [
        ("Techno", ["11", "12"]),
        ("House", ["21"]),
        ("Sin género", ["31"]),
    ]
    techno = d["generos"][0]["tracks"]
    assert techno[0] == {"id": "11", "titulo": "Uno", "artista": "Artista A", "bpm": 128.0,
                         "camelot": "8A", "tonalidad": "Am", "genero": "Techno", "dur": 245,
                         "formato": "wav"}
    # BPM 0 = sin analizar → None (no un 0 que miente); sin Tonality → None.
    assert techno[1] == {"id": "12", "titulo": "Dos", "artista": "Artista B", "bpm": None,
                         "camelot": None, "tonalidad": None, "genero": "Techno", "dur": 180,
                         "formato": "wav"}
    sin_genero = d["generos"][2]["tracks"][0]
    assert (sin_genero["bpm"], sin_genero["camelot"], sin_genero["tonalidad"]) == (140.3, None, "4A")


# --------------------------------------------------------------------------- /api/audio/{id}

def test_audio_con_id_valido_sirve_ese_archivo(client, biblioteca):
    for tid in ("12", "31"):   # ninguno es el primero del XML: un "sirve cualquiera" no pasa
        r = client.get(f"/api/audio/{tid}")
        assert r.status_code == 200
        assert r.content == biblioteca["audios"][tid], f"sirvió otro archivo que el del track {tid}"


def test_audio_responde_a_range(client, biblioteca):
    completo = biblioteca["audios"]["11"]
    r = client.get("/api/audio/11", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 100-199/{len(completo)}"
    assert r.content == completo[100:200]


def test_audio_id_inexistente_da_404(client, biblioteca):
    for tid in ("99", "12345", "0"):   # 99 está en el XML pero sin archivo
        r = client.get(f"/api/audio/{tid}")
        assert r.status_code == 404, f"id {tid!r} no debería servir nada"
        assert r.json() == {"error": "track no encontrado"}


def test_audio_no_sirve_archivos_fuera_de_la_biblioteca(server, client, biblioteca):
    # El id NO es una ruta: ni rutas relativas ni absolutas (aunque el archivo exista, como
    # server.py o un audio que sí está en la biblioteca) pueden sacar un archivo.
    servidor = Path(server.__file__).resolve()
    audio_real = biblioteca["raiz"] / "techno" / "uno.wav"
    intentos = [
        "server.py",                     # relativa al cwd de la suite (la raíz del repo)
        "..%2Fserver.py",
        "%2E%2E%2F%2E%2E%2Fserver.py",
        quote(str(servidor), safe=""),
        quote(str(audio_real), safe=""),
        quote(audio_real.as_posix(), safe=""),
    ]
    prohibidos = (servidor.read_bytes(), biblioteca["audios"]["11"])
    for intento in intentos:
        r = client.get(f"/api/audio/{intento}")
        assert r.status_code == 404, f"{intento!r} respondió {r.status_code}"
        assert r.content not in prohibidos, f"{intento!r} filtró un archivo"


# --------------------------------------------------------------------------- /api/cover/{id}
# La home no mostraba carátulas porque el XML de Rekordbox no trae imágenes. La carátula que
# existe de verdad es la que viene EMBEBIDA en el archivo: se escribe acá con mutagen (como
# lo hace tagger.py al descargar) y el endpoint tiene que devolver esos mismos bytes.

def _png(ancho: int = 2, alto: int = 2, rgb=(200, 30, 90)) -> bytes:
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
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + bytes(range(64))


def _wav_con_apic(ruta: Path, imagen: bytes, mime: str, antes: bytes | None = None) -> None:
    """WAV con la carátula como APIC tipo 3 (tapa). `antes`: otra imagen de tipo 0 (otra)
    escrita PRIMERO, para ver que se elige la tapa y no la primera que aparece."""
    from mutagen.id3 import APIC
    from mutagen.wave import WAVE
    _wav(ruta, 300.0)
    audio = WAVE(str(ruta))
    audio.add_tags()
    if antes is not None:
        audio.tags.add(APIC(encoding=3, mime="image/png", type=0, desc="Otra", data=antes))
    audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=imagen))
    audio.save()


def _flac_con_picture(ruta: Path, imagen: bytes) -> None:
    import numpy as np
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


@pytest.fixture
def biblioteca_caratulas(server, tmp_path, monkeypatch):
    """4 tracks: WAV con APIC PNG, WAV con APIC cuyo MIME declarado miente, FLAC con
    PICTURE JPEG y WAV sin carátula."""
    raiz = tmp_path / "musica"
    png, png_otro = _png(), _png(3, 1, (10, 220, 40))
    _wav_con_apic(raiz / "con_png.wav", png, "image/png", antes=_png(1, 1, (0, 0, 255)))
    # Hay APIC con MIME vacío o "image/jpg": el tipo sale del contenido, no del tag.
    _wav_con_apic(raiz / "mime_miente.wav", png_otro, "image/jpg")
    _flac_con_picture(raiz / "con_jpeg.flac", _JPEG)
    _wav(raiz / "sin_tapa.wav", 500.0)
    tracks = [
        dict(id="1", name="Con PNG", artist="A", genre="Techno", bpm="128.00", ton="Am", dur="1",
             loc=_location(raiz / "con_png.wav")),
        dict(id="2", name="Mime", artist="B", genre="Techno", bpm="128.00", ton="Am", dur="1",
             loc=_location(raiz / "mime_miente.wav")),
        dict(id="3", name="Con JPEG", artist="C", genre="House", bpm="124.00", ton="C", dur="1",
             loc=_location(raiz / "con_jpeg.flac")),
        dict(id="4", name="Sin tapa", artist="D", genre="House", bpm="124.00", ton="C", dur="1",
             loc=_location(raiz / "sin_tapa.wav")),
    ]
    xml = tmp_path / "rekordbox.xml"
    xml.write_text(_xml(tracks), encoding="utf-8")
    monkeypatch.setattr(server, "_LIB_XML", str(xml))
    monkeypatch.setattr(server, "_LIB_ROOTS", [str(raiz)])
    monkeypatch.setattr(server, "_lib_audio", {})
    return {"raiz": raiz, "png": png, "png_otro": png_otro}


def test_cover_devuelve_la_caratula_embebida_de_ese_track(client, biblioteca_caratulas):
    b = biblioteca_caratulas
    esperado = {"1": (b["png"], "image/png"), "2": (b["png_otro"], "image/png"),
                "3": (_JPEG, "image/jpeg")}
    for tid, (bytes_esperados, mime) in esperado.items():
        r = client.get(f"/api/cover/{tid}")
        assert r.status_code == 200, f"track {tid}: {r.status_code} {r.text[:80]}"
        assert r.content == bytes_esperados, f"track {tid}: devolvió otra imagen que la embebida"
        assert r.headers["content-type"] == mime, f"track {tid}: MIME {r.headers['content-type']}"


def test_cover_sin_caratula_o_sin_track_da_404_con_motivo(client, biblioteca_caratulas):
    r = client.get("/api/cover/4")
    assert r.status_code == 404
    assert r.json() == {"error": "el archivo no trae carátula"}
    r = client.get("/api/cover/99")
    assert r.status_code == 404
    assert r.json() == {"error": "track no encontrado"}
    # El id no es una ruta: ni relativa ni absoluta al archivo que SÍ tiene carátula.
    ruta_real = quote(str(biblioteca_caratulas["raiz"] / "con_png.wav"), safe="")
    for tid in ("..%2Fserver.py", ruta_real, "con_png.wav"):
        r = client.get(f"/api/cover/{tid}")
        assert r.status_code == 404, f"{tid!r} respondió {r.status_code}"
        assert biblioteca_caratulas["png"] not in r.content, f"{tid!r} filtró la carátula"


def test_cover_no_modifica_el_archivo_original(client, biblioteca_caratulas):
    ruta = biblioteca_caratulas["raiz"] / "con_png.wav"
    antes = ruta.read_bytes()
    assert client.get("/api/cover/1").status_code == 200
    assert ruta.read_bytes() == antes, "leer la carátula tocó el archivo original"


def test_biblioteca_informa_el_formato_real_de_cada_archivo(client, biblioteca_caratulas):
    d = client.get("/api/biblioteca").json()
    formatos = {t["id"]: t["formato"] for g in d["generos"] for t in g["tracks"]}
    assert formatos == {"1": "wav", "2": "wav", "3": "flac", "4": "wav"}


# --------------------------------------------------------------------------- auditoría f28
# Todo lo de abajo cierra hallazgos de la auditoría de f28: XSS con una carátula SVG, sin
# tope de memoria, motivo falso sin mutagen, caché sin Last-Modified y formato sin extensión.

_SVG_XSS = (b'<svg xmlns="http://www.w3.org/2000/svg"><script>fetch("/api/playlists/1",'
            b'{method:"DELETE"})</script></svg>')


def _biblioteca_de(server, tmp_path, monkeypatch, archivos: dict[str, Path]) -> None:
    """XML con un track por archivo (id → ruta) y la biblioteca apuntando ahí."""
    tracks = [dict(id=tid, name=f"T{tid}", artist="A", genre="Techno", bpm="128.00", ton="Am",
                   dur="1", loc=_location(ruta)) for tid, ruta in archivos.items()]
    xml = tmp_path / "rekordbox.xml"
    xml.write_text(_xml(tracks), encoding="utf-8")
    monkeypatch.setattr(server, "_LIB_XML", str(xml))
    monkeypatch.setattr(server, "_LIB_ROOTS", [str(tmp_path / "musica")])
    monkeypatch.setattr(server, "_lib_audio", {})


def test_cover_svg_declarado_como_imagen_no_se_sirve(server, client, tmp_path, monkeypatch):
    # A1: el MIME sale del CONTENIDO. Un APIC "image/svg+xml" con <script> servido desde el
    # origen de la app ejecutaba (reproducido en Chrome por la auditoría).
    raiz = tmp_path / "musica"
    _wav_con_apic(raiz / "svg.wav", _SVG_XSS, "image/svg+xml")
    # Lo mismo con un MIME declarado inofensivo: tampoco se confía en "image/png".
    _wav_con_apic(raiz / "svg_png.wav", _SVG_XSS, "image/png")
    _biblioteca_de(server, tmp_path, monkeypatch, {"1": raiz / "svg.wav", "2": raiz / "svg_png.wav"})
    for tid in ("1", "2"):
        r = client.get(f"/api/cover/{tid}")
        assert r.status_code == 404, f"track {tid}: sirvió el SVG ({r.headers.get('content-type')})"
        assert r.json() == {"error": "la carátula no es JPEG, PNG, GIF ni WebP"}
        assert b"<script" not in r.content


def test_cover_se_sirve_con_nosniff_y_csp(client, biblioteca_caratulas):
    r = client.get("/api/cover/1")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_cover_mas_grande_que_el_tope_da_404(server, client, tmp_path, monkeypatch):
    # M1: un APIC enorme iba entero a memoria y a la red. Justo en el tope pasa; uno más, no.
    raiz = tmp_path / "musica"
    tope = server._CARATULA_MAX_BYTES
    png = _png()
    justo = png + b"\x00" * (tope - len(png))
    _wav_con_apic(raiz / "justo.wav", justo, "image/png")
    _wav_con_apic(raiz / "grande.wav", justo + b"\x00", "image/png")
    _biblioteca_de(server, tmp_path, monkeypatch, {"1": raiz / "justo.wav", "2": raiz / "grande.wav"})
    r = client.get("/api/cover/1")
    assert r.status_code == 200 and len(r.content) == tope, "una tapa de exactamente 10 MB entra"
    r = client.get("/api/cover/2")
    assert r.status_code == 404
    assert r.json() == {"error": "carátula demasiado grande"}


def _mp3_id3_corrupto(ruta: Path) -> None:
    """3 KB que dicen tener una etiqueta ID3 de ~256 MB (tamaño syncsafe al máximo)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(b"ID3\x04\x00\x00\x7f\x7f\x7f\x7f" + b"\x00" * 3000)


def test_cover_cabecera_id3_corrupta_no_reserva_memoria(server, tmp_path):
    # M1: mutagen pedía read(256 MB) y Python reserva el búfer entero antes de leer.
    import tracemalloc
    ruta = tmp_path / "musica" / "corrupto.mp3"
    _mp3_id3_corrupto(ruta)
    tracemalloc.start()
    try:
        motivo = server._caratula_embebida(str(ruta))
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert motivo == "no pude leer el archivo"
    assert pico < 8 * 1024 * 1024, f"pico de {pico / 2**20:.0f} MB para un archivo de 3 KB"


def test_cabeceras_plausibles_riff_con_chunk_mas_grande_que_el_archivo(server, tmp_path):
    ruta = tmp_path / "ok.wav"
    _wav(ruta, 440.0)
    assert server._cabeceras_plausibles(str(ruta)) is True, "un WAV sano no puede quedar afuera"
    datos = bytearray(ruta.read_bytes())
    datos += b"id3 " + (200 * 1024 * 1024).to_bytes(4, "little") + b"ID3"   # chunk que no entra
    roto = tmp_path / "roto.wav"
    roto.write_bytes(bytes(datos))
    assert server._cabeceras_plausibles(str(roto)) is False


def test_cover_limita_la_concurrencia(server, client, biblioteca_caratulas, monkeypatch):
    # M1: la home pide ~18 carátulas juntas; a mutagen entran de a pocas.
    import threading
    import time as _time
    activos, pico, lock = [0], [0], threading.Lock()
    real = server._caratula_embebida

    def lento(ruta):
        with lock:
            activos[0] += 1
            pico[0] = max(pico[0], activos[0])
        _time.sleep(0.05)
        with lock:
            activos[0] -= 1
        return real(ruta)
    monkeypatch.setattr(server, "_caratula_embebida", lento)
    import asyncio as _asyncio

    import httpx

    async def muchos():
        transporte = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://t") as c:
            return await _asyncio.gather(*(c.get(f"/api/cover/{1 + k % 3}") for k in range(18)))
    rs = _asyncio.run(muchos())
    assert all(r.status_code == 200 for r in rs)
    assert 1 < pico[0] <= 4, f"hubo {pico[0]} lecturas a la vez (tope 4)"


def test_cover_sin_mutagen_lo_dice_y_avisa_una_vez(server, client, biblioteca_caratulas, monkeypatch, caplog):
    # B2: sin mutagen decía "el archivo no trae carátula" (falso) con un log debug.
    monkeypatch.setitem(sys.modules, "mutagen", None)
    monkeypatch.setattr(server, "_aviso_sin_mutagen", False)
    with caplog.at_level("WARNING", logger="bot_web"):
        r1 = client.get("/api/cover/1")
        r2 = client.get("/api/cover/3")
    for r in (r1, r2):
        assert r.status_code == 404
        assert r.json() == {"error": "esta instalación no tiene mutagen: no se pueden leer carátulas"}
    avisos = [m for m in caplog.messages if "mutagen" in m]
    assert len(avisos) == 1, f"se esperaba un warning, hubo {len(avisos)}: {avisos}"


def test_cover_last_modified_y_304(server, client, biblioteca_caratulas):
    # B3: con solo max-age, re-taggear dejaba la tapa vieja una hora.
    import os
    from email.utils import formatdate
    ruta = biblioteca_caratulas["raiz"] / "con_png.wav"
    os.utime(ruta, (1_700_000_000, 1_700_000_000))
    r = client.get("/api/cover/1")
    assert r.headers["last-modified"] == formatdate(1_700_000_000, usegmt=True)
    assert client.get("/api/cover/1", headers={"If-Modified-Since": r.headers["last-modified"]}).status_code == 304
    viejo = formatdate(1_600_000_000, usegmt=True)
    assert client.get("/api/cover/1", headers={"If-Modified-Since": viejo}).status_code == 200
    os.utime(ruta, (1_800_000_000, 1_800_000_000))   # "se re-taggeó"
    r2 = client.get("/api/cover/1", headers={"If-Modified-Since": r.headers["last-modified"]})
    assert r2.status_code == 200 and r2.content == biblioteca_caratulas["png"], "después de re-taggear tiene que ir de nuevo"


def test_biblioteca_formato_none_sin_extension(server, client, tmp_path, monkeypatch):
    # B8: sin extensión no hay formato que informar (antes la mutación "→ 'wav'" sobrevivía).
    raiz = tmp_path / "musica"
    _wav(raiz / "sin_extension", 300.0)
    _wav(raiz / "con.wav", 330.0)
    _biblioteca_de(server, tmp_path, monkeypatch, {"1": raiz / "sin_extension", "2": raiz / "con.wav"})
    d = client.get("/api/biblioteca").json()
    formatos = {t["id"]: t["formato"] for g in d["generos"] for t in g["tracks"]}
    assert formatos == {"1": None, "2": "wav"}
