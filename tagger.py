"""Escribe metadatos en los archivos descargados: título, artista, género, BPM,
key (Camelot), carátula y una nota de calidad, para que caigan en Rekordbox/Serato
listos para pinchar. Best-effort: si algo falla, se loguea pero NUNCA rompe la descarga.

Soporta ID3 (mp3/wav/aiff), Vorbis (flac) y MP4 (m4a) — que son los formatos que
puede generar la descarga."""
import logging
from pathlib import Path

logger = logging.getLogger("bot_web")


def _titulo_sin_artista(titulo, artista):
    """Recorta del título el prefijo del artista para no duplicarlo en iTunes (#5.35).

    Caso: TPE1='BabaBass3000, Pueblo Gelb' y TIT2='BabaBass3000, Pueblo Gelb - Loose my
    Mind KMA' → iTunes muestra el nombre dos veces. Se recorta SOLO cuando el título arranca
    LITERALMENTE con el mismo artista que vamos a escribir seguido de ' - '. Si aporta algo
    distinto (otro artista, otra grafía, un separador sin espacios como 'kylian-dictador'),
    no matchea y se respeta tal cual. Nunca deja el título vacío."""
    if not titulo or not artista:
        return titulo
    prefijo = f"{artista} - "
    if titulo.startswith(prefijo):
        resto = titulo[len(prefijo):].strip()
        if resto:
            return resto
    return titulo


def _descargar_cover(url: str, timeout: int = 15):
    """Devuelve (bytes, mime) de la carátula, o (None, None)."""
    if not url:
        return None, None
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        mime = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        return r.content, mime
    except Exception as e:
        logger.warning(f"⚠️ Tags: no pude bajar la carátula: {e}")
        return None, None


def _aplicar_id3(tags, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime):
    """Carga frames ID3 sobre un objeto tags (de ID3/WAVE/AIFF)."""
    from mutagen.id3 import TIT2, TPE1, TCON, TBPM, TKEY, COMM, APIC
    if titulo:    tags.setall("TIT2", [TIT2(encoding=3, text=str(titulo))])
    if artista:   tags.setall("TPE1", [TPE1(encoding=3, text=str(artista))])
    if genero:    tags.setall("TCON", [TCON(encoding=3, text=str(genero))])
    if bpm:       tags.setall("TBPM", [TBPM(encoding=3, text=str(int(float(bpm))))])
    if camelot:   tags.setall("TKEY", [TKEY(encoding=3, text=str(camelot))])
    if comentario:
        tags.setall("COMM", [COMM(encoding=3, lang="spa", desc="", text=str(comentario))])
    if cover:
        tags.setall("APIC", [APIC(encoding=3, mime=cover_mime or "image/jpeg",
                                   type=3, desc="Cover", data=cover)])


def _tag_mp3(ruta, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime):
    from mutagen.id3 import ID3, ID3NoHeaderError
    try:
        tags = ID3(str(ruta))
    except ID3NoHeaderError:
        tags = ID3()
    _aplicar_id3(tags, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime)
    tags.save(str(ruta))


def _tag_wave(ruta, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime):
    # WAV: hay que usar el contenedor WAVE (escribe el chunk 'id3 ' DENTRO del RIFF).
    # Usar ID3().save() directo antepone el tag y CORROMPE el WAV. Sin carátula para
    # no inflar el archivo ni romper players quisquillosos con RIFF.
    from mutagen.wave import WAVE
    audio = WAVE(str(ruta))
    if audio.tags is None:
        audio.add_tags()
    _aplicar_id3(audio.tags, titulo, artista, genero, bpm, camelot, comentario, None, None)
    audio.save()


def _tag_aiff(ruta, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime):
    from mutagen.aiff import AIFF
    audio = AIFF(str(ruta))
    if audio.tags is None:
        audio.add_tags()
    _aplicar_id3(audio.tags, titulo, artista, genero, bpm, camelot, comentario, None, None)
    audio.save()


def _tag_flac(ruta, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime):
    from mutagen.flac import FLAC, Picture
    f = FLAC(str(ruta))
    if titulo:    f["title"] = str(titulo)
    if artista:   f["artist"] = str(artista)
    if genero:    f["genre"] = str(genero)
    if bpm:       f["bpm"] = str(int(float(bpm)))
    if camelot:   f["initialkey"] = str(camelot); f["key"] = str(camelot)
    if comentario: f["comment"] = str(comentario)
    if cover:
        pic = Picture()
        pic.type = 3          # front cover
        pic.mime = cover_mime or "image/jpeg"
        pic.data = cover
        f.clear_pictures()
        f.add_picture(pic)
    f.save()


def _tag_mp4(ruta, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime):
    from mutagen.mp4 import MP4, MP4Cover
    m = MP4(str(ruta))
    if titulo:    m["\xa9nam"] = [str(titulo)]
    if artista:   m["\xa9ART"] = [str(artista)]
    if genero:    m["\xa9gen"] = [str(genero)]
    if bpm:       m["tmpo"] = [int(float(bpm))]
    if comentario: m["\xa9cmt"] = [str(comentario)]
    if camelot:   m["----:com.apple.iTunes:initialkey"] = [str(camelot).encode("utf-8")]
    if cover:
        fmt = MP4Cover.FORMAT_PNG if (cover_mime or "").endswith("png") else MP4Cover.FORMAT_JPEG
        m["covr"] = [MP4Cover(cover, imageformat=fmt)]
    m.save()


def taggear(ruta, *, titulo=None, artista=None, genero=None, bpm=None,
            camelot=None, cover_url=None, comentario=None) -> bool:
    """Escribe los tags según el formato del archivo. No lanza excepción."""
    ruta = Path(ruta)
    if not ruta.exists():
        return False
    ext = ruta.suffix.lower().lstrip(".")
    titulo = _titulo_sin_artista(titulo, artista)   # #5.35: no duplicar el artista en el título
    try:
        cover, cover_mime = _descargar_cover(cover_url)
        if ext == "flac":
            _tag_flac(ruta, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime)
        elif ext == "mp3":
            _tag_mp3(ruta, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime)
        elif ext == "wav":
            _tag_wave(ruta, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime)
        elif ext in ("aiff", "aif"):
            _tag_aiff(ruta, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime)
        elif ext in ("m4a", "mp4", "m4b"):
            _tag_mp4(ruta, titulo, artista, genero, bpm, camelot, comentario, cover, cover_mime)
        else:
            logger.info(f"ℹ️ Tags: .{ext} sin taggeo específico, lo dejo sin tags.")
            return False
        logger.info(f"🏷️  Tags escritos en {ruta.name}")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Tags: no pude escribir en {ruta.name}: {e}")
        return False
