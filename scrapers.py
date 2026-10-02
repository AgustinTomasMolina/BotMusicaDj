"""
Scrapers de sitios de MP3 (búsqueda directa con audio descargable)
==================================================================
Sitios soportados que devuelven MP3 directos:
  - ligaudio  (web.ligaudio.ru)
  - hitplayer (box.hitplayer.ru)

Cada función devuelve una lista de dicts con el mismo formato que el resto
del bot:
  {titulo, artista, duracion(seg), url, stream_url, fuente, thumbnail}
Si el sitio no contesta, lanza `fuente_errores.FuenteCaida` (no [], que es "no lo tiene").

Nota: estos sitios entregan URLs firmadas/temporales. Como el navegador del
usuario y este servidor comparten la misma IP pública (corre local), las URLs
de reproducción/descarga funcionan desde la página.
"""

import html
import logging
import re
import urllib.parse

import requests
from fuente_errores import FuenteCaida

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}
TIMEOUT = 20


def _get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def _abs(u: str) -> str:
    """Completa URLs que empiezan con //. Lo que no queda http(s) se descarta ("") (f40-r2):
    estas URLs terminan en ffprobe/ffmpeg y en el navegador, y con `file:`, `concat:` o
    `javascript:` una página scrapeada podría hacer leer un archivo local o inyectar un link."""
    if not u:
        return u
    u = u.strip()
    if u.startswith("//"):
        u = "https:" + u
    return u if re.match(r"(?i)https?://[^\s]+$", u) else ""


def _dur_a_seg(txt: str) -> int:
    """Convierte '2:55' o '1:02:33' a segundos."""
    try:
        partes = [int(p) for p in txt.strip().split(":")]
        seg = 0
        for p in partes:
            seg = seg * 60 + p
        return seg
    except Exception:
        return 0


def _limpiar(t: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


# ============================================================
#  LIGAUDIO  (web.ligaudio.ru)
# ============================================================
def buscar_ligaudio(query: str, limit: int = 10) -> list:
    url = f"https://web.ligaudio.ru/mp3/{urllib.parse.quote(query)}"
    try:
        doc = _get(url)
    except Exception as e:
        # f40: una caída se SEÑALA (antes era [], lo mismo que "no lo tiene").
        logger.warning(f"⚠️ ligaudio no respondió: {e}")
        raise FuenteCaida(f"ligaudio: {type(e).__name__}") from e

    bloques = doc.split('<div class="item"')[1:]
    out = []
    for b in bloques:
        title_m = re.search(r'itemprop="name">([^<]+)<', b)
        artist_m = re.search(r'itemprop="byArtist"><a[^>]*>([^<]+)<', b)
        dur_m = re.search(r'<span class="d">([^<]+)<', b)
        play_m = re.search(r'data-audio="([^"]+)"', b)
        down_m = re.search(r'class="down"[^>]*href="([^"]+)"', b)
        img_m = re.search(r'<img[^>]+src="([^"]+)"', b)

        if not title_m or not (play_m or down_m):
            continue

        stream = _abs(play_m.group(1)) if play_m else ""
        descarga = (_abs(down_m.group(1)) if down_m else "") or stream
        if not descarga:                 # ni un link http(s): no hay audio que ofrecer
            continue
        out.append({
            "titulo": _limpiar(title_m.group(1)),
            "artista": _limpiar(artist_m.group(1)) if artist_m else "ligaudio",
            "duracion": _dur_a_seg(dur_m.group(1)) if dur_m else 0,
            "url": descarga,
            "stream_url": stream or descarga,
            "fuente": "ligaudio",
            "thumbnail": (_abs(img_m.group(1)) or None) if img_m else None,
        })
        if len(out) >= limit:
            break

    logger.info(f"🎧 ligaudio: {len(out)} resultados para '{query}'")
    return out


# ============================================================
#  HITPLAYER  (box.hitplayer.ru)
# ============================================================
_HIT_RE = re.compile(
    r'class="dwnld[^"]*"[^>]*href="([^"]+\.mp3[^"]*)".*?'
    r'<span class="tt">([^<]+)</span>\s*'
    r'<span class="a"><a[^>]*>([^<]*)',
    re.DOTALL,
)


def buscar_hitplayer(query: str, limit: int = 10) -> list:
    url = f"https://box.hitplayer.ru/?s={urllib.parse.quote(query)}"
    try:
        doc = _get(url)
    except Exception as e:
        logger.warning(f"⚠️ hitplayer no respondió: {e}")
        raise FuenteCaida(f"hitplayer: {type(e).__name__}") from e

    out = []
    for m in _HIT_RE.finditer(doc):
        descarga = _abs(m.group(1))
        if not descarga:                 # f40-r2: solo http(s)
            continue
        out.append({
            "titulo": _limpiar(m.group(2)),
            "artista": _limpiar(m.group(3)) or "hitplayer",
            "duracion": 0,       # HitPlayer no publica la duración: 0 = "no se sabe" (versiones la mide)
            "url": descarga,
            "stream_url": descarga,  # el mp3 directo sirve para reproducir
            "fuente": "hitplayer",
            "thumbnail": None,
        })
        if len(out) >= limit:
            break

    logger.info(f"🎧 hitplayer: {len(out)} resultados para '{query}'")
    return out


# Fuentes disponibles como (nombre, función)
FUENTES_SCRAPER = [
    ("ligaudio", buscar_ligaudio),
    ("hitplayer", buscar_hitplayer),
]
