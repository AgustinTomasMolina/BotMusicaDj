"""Identidad de un tema: ¿este resultado es EL tema que se pidió?

"Temas parecidos" resolvía la semilla con el primer resultado de una búsqueda de texto libre
en Deezer, sin comparar nada: `From The Top` / `IMMINENT - Topic` terminaba en WAP de Cardi B
(10 sustituciones en 39 entradas reales medidas). Acá está la regla que la reemplaza (la
estrategia "B" del diseño de parecidas, §8.3):

1. Normalizar: sin acentos, casefold, sin puntuación; el uploader pierde " - Topic"/VEVO; el
   título pierde (Official Video/Audio), (Letra), Visualizer, [OUT NOW], [sello], hashtags,
   "Premiere:", "FREE DL"… y se separa "Artista - Tema" (con -, – o —).
2. Versión canónica de lo que dice el título: original / extended / radio / club / short /
   instrumental / acapella / sped_up / slowed / live / vip / dub / remix:<quién> / edit:<quién>…
3. Un resultado ES el tema solo si comparten algún artista, el título base es igual y la versión
   es la misma. Otra versión del mismo tema (Radio Edit por Original, Extended…) NO se acepta:
   decisión conservadora hasta que el dueño diga otra cosa (diseño, UNKNOWN 18).
4. Si quedan aceptados de dos obras distintas (artistas sin nada en común) es ambiguo: nada.

Nunca se devuelve "el más parecido": o se demuestra que es el tema, o no hay resultado.

Solo stdlib (unicodedata, re): no agrega dependencias. Lo único con red es
`fetch_soundcloud_isrc` (API v2 de SoundCloud, con el client_id que cachea yt-dlp)."""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger("track_identity")

# Separadores entre artistas: "A, B", "A & B", "A x B", "A vs B", "A feat. B", "A · B"…
_ARTIST_SEP = re.compile(
    r"\s*(?:,|&|\+|/|\bx\b|\bvs\.?\b|\band\b|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bwith\b|·)\s*", re.I)
# Sufijos de canal que no son parte del nombre del artista.
_CHANNEL_SUFFIX = re.compile(r"\s*-\s*topic\s*$|\s*vevo\s*$|\s*official\s*$", re.I)
# Palabras que dicen "esto es una versión" (remix, edit, extended mix…).
_VERSION_KW = re.compile(r"remix|mix\b|edit\b|vip\b|bootleg|flip\b|rework|version|instrumental|a\s?capella|"
                         r"acapella|sped\s*up|slowed|\blive\b|remaster", re.I)
_GROUP = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_FEAT = re.compile(r"\s*[\(\[]?\s*\b(?:feat\.?|ft\.?|featuring)\s+([^\)\]\-]+)[\)\]]?", re.I)
_ARTIST_TITLE_SEP = re.compile(r"\s+[-–—]\s+")
_MAX_ARTIST_QUERIES = 3


def normalize(s: str | None) -> str:
    """Forma de comparación: sin acentos, casefold, sin apóstrofos, solo letras y números."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = re.sub(r"[’'`´]", "", s)
    s = s.replace("$", "s")
    s = re.sub(r"[^0-9a-zñ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_artists(s: str | None) -> frozenset:
    """Nombres normalizados de los artistas de un campo ("A & B feat. C", "X - Topic")."""
    s = _CHANNEL_SUFFIX.sub("", s or "")
    return frozenset(p for p in (normalize(x) for x in _ARTIST_SEP.split(s)) if p)


def _artist_names(s: str | None) -> list[str]:
    """Los nombres tal como vienen (sin normalizar), para armar consultas a Deezer."""
    s = _CHANNEL_SUFFIX.sub("", s or "")
    return [p.strip() for p in _ARTIST_SEP.split(s) if p and p.strip()]


def version_of(text: str) -> str | None:
    """Versión canónica de un grupo entre paréntesis ("Radio Edit" → "radio"), o None si el
    grupo no habla de versión."""
    t = normalize(text)
    if not _VERSION_KW.search(text or ""):
        return None
    if re.fullmatch(r"(original( mix)?|original version|remaster(ed)?( \d{4})?(.*)?|\d{4} remaster(ed)?)", t):
        return "original"
    for kw, canon in (("extended", "extended"), ("radio", "radio"), ("club", "club"), ("short", "short"),
                      ("instrumental", "instrumental"), ("a capella", "acapella"), ("acapella", "acapella"),
                      ("sped up", "sped_up"), ("slowed", "slowed"), ("live", "live"), ("vip", "vip"),
                      ("dub", "dub")):
        if re.search(rf"\b{kw}\b", t) and not re.search(r"\b(remix|edit|bootleg|flip|rework)\b",
                                                       t.replace("radio edit", "")):
            return canon
    m = re.search(r"^(.*?)\b(remix|edit|bootleg|flip|rework|mix)\b", t)
    if m:
        who = m.group(1).strip()
        return f"{m.group(2)}:{who}" if who else m.group(2)
    return "otra:" + t


@dataclass(frozen=True)
class Identity:
    artists: frozenset
    base_title: str
    version: str = "original"          # "original" si el título no dice nada
    feat: frozenset = field(default_factory=frozenset)
    artist_names: tuple = ()           # nombres sin normalizar (solo para las consultas)
    artist_text: str = ""              # el campo de artista entero, sin " - Topic" (consultas)


def _clean_title(t: str | None) -> str:
    """Saca del título lo que no es el tema: hashtags, "Premiere:", "FREE DL", [sello/catálogo/
    OUT NOW], " | Boiler Room…". Los (…) sin palabra de versión — (Official Audio), (Letra),
    (… Visual), (Official Visualizer) — no se tocan acá: `_title_and_version` los saca del
    título base (y los de versión, "(Radio Edit)", pasan a ser la versión)."""
    t = re.sub(r"#\w+", " ", t or "")
    t = re.sub(r"^\s*premiere\s*:\s*", "", t, flags=re.I)
    t = re.sub(r"\bfree\s*d(?:ownload|l)\b|<3", " ", t, flags=re.I)
    t = re.sub(r"\[([^\]]*)\]", lambda m: m.group(0) if _VERSION_KW.search(m.group(1)) else " ", t)
    t = re.sub(r"\s*[\(\[]\s*[\)\]]", " ", t)
    t = re.sub(r"\s*\|\s*.*$", "", t)
    return re.sub(r"\s+", " ", t).strip(" -–—")


def _title_and_version(t: str) -> tuple[str, str, frozenset]:
    feat: set = set()
    for m in _FEAT.finditer(t):
        feat |= split_artists(m.group(1))
    t = _FEAT.sub(" ", t)
    version = "original"
    for g in _GROUP.findall(t):
        v = version_of(g)
        if v is not None and version == "original":
            version = v
    base = _GROUP.sub(" ", t)       # fuera todo (…): ruido del upload y versión (ya anotada)
    # "Tema - Radio Edit" (así lo escribe Deezer a veces) → versión
    m = re.search(r"\s[-–—]\s([^-–—]+)$", base)
    if m and version_of(m.group(1)):
        if version == "original":
            version = version_of(m.group(1))
        base = base[:m.start()]
    return normalize(base), version, frozenset(feat)


def parse_entry(title: str, uploader: str) -> Identity:
    """Identidad de un resultado de YouTube/SoundCloud: el título manda ("Artista - Tema");
    si no trae artista, el artista es el uploader (sin " - Topic")."""
    t = _clean_title(title)
    parts = _ARTIST_TITLE_SEP.split(t, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        left, song = parts
    else:
        left, song = uploader or "", t
    base, version, feat = _title_and_version(song)
    return Identity(split_artists(left), base, version, feat, tuple(_artist_names(left)),
                    _CHANNEL_SUFFIX.sub("", left).strip())


def parse_fields(artist: str, title: str) -> Identity:
    """Identidad de campos ya separados (Deezer: artist.name + title)."""
    base, version, feat = _title_and_version(_clean_title(title))
    return Identity(split_artists(artist), base, version, feat, tuple(_artist_names(artist)),
                    _CHANNEL_SUFFIX.sub("", artist or "").strip())


def artists_match(a: Identity, b: Identity) -> bool:
    """¿Comparten algún artista? Por nombre, o por los tokens del nombre completo
    ("Chase and Status" = "Chase & Status")."""
    all_a, all_b = a.artists | a.feat, b.artists | b.feat
    if all_a & all_b:
        return True
    tokens_a = {w for x in all_a for w in x.split()}
    tokens_b = {w for x in all_b for w in x.split()}
    return (bool(a.artists) and bool(b.artists)
            and any(set(x.split()) <= tokens_b for x in a.artists)
            and any(set(y.split()) <= tokens_a for y in b.artists))


def is_same_track(wanted: Identity, candidate: Identity) -> bool:
    """El candidato ES el tema pedido: artista en común, mismo título base, misma versión."""
    return (bool(wanted.base_title) and wanted.base_title == candidate.base_title
            and wanted.version == candidate.version and artists_match(wanted, candidate))


def deezer_queries(entry: Identity) -> list[str]:
    """Consultas a Deezer para una entrada, la estricta primero: `artist:"a" track:"t"` por
    cada artista, después el nombre completo + el título base, después "a t" por artista.
    Nunca el título solo: con títulos genéricos ("From The Top", "Exodus", "Intro") eso trae
    cualquier cosa."""
    if not entry.base_title or not entry.artist_names:
        return []
    names = list(entry.artist_names[:_MAX_ARTIST_QUERIES])
    out: list[str] = []

    def add(q: str):
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in out:
            out.append(q)

    for a in names:
        add(f'artist:"{a}" track:"{entry.base_title}"')
    add(f"{entry.artist_text} {entry.base_title}")     # el nombre entero ("Chase & Status")
    for a in names:
        add(f"{a} {entry.base_title}")
    return out


def deezer_identity(track: dict) -> Identity:
    return parse_fields((track.get("artist") or {}).get("name") or "", track.get("title") or "")


def pick_track(entry: Identity, tracks: list[dict]) -> dict | None:
    """El track de Deezer que es la entrada, o None. Entre varios aceptados (mismo tema en dos
    lanzamientos) se queda con el primero; si los aceptados son de obras distintas (artistas
    sin nada en común), es ambiguo y no se elige ninguno."""
    accepted = [t for t in tracks if is_same_track(entry, deezer_identity(t))]
    if not accepted:
        return None
    first = deezer_identity(accepted[0])
    for t in accepted[1:]:
        if not artists_match(first, deezer_identity(t)):
            logger.info(f"🔎 Identidad ambigua: «{accepted[0].get('title')}» de dos artistas distintos; no elijo ninguno.")
            return None
    return accepted[0]


# ---------------------------------------------------------------- SoundCloud → ISRC

_SC_API = "https://api-v2.soundcloud.com/tracks/{}"


def fetch_soundcloud_isrc(track_id: str | int | None, timeout: float = 8.0) -> str | None:
    """ISRC de un track de SoundCloud (publisher_metadata.isrc de la API v2), o None.

    Medido en el diseño: 12 de 20 tracks de SoundCloud lo traen y 11 están en Deezer por
    ISRC (exacto, sin comparar textos). La API v2 pide el client_id público que yt-dlp saca
    de la web y cachea; si no está cacheado (o venció), se deja que yt-dlp lo renueve abriendo
    el track y se reintenta una vez. Cualquier falla → None (se sigue por título y artista)."""
    tid = str(track_id or "").strip()
    if not tid.isdigit():
        return None
    try:
        import requests
        import yt_dlp
    except ImportError:
        return None

    def _client_id(refresh: bool) -> str | None:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as y:
            if refresh:
                y.extract_info(f"https://api.soundcloud.com/tracks/{tid}", download=False, process=False)
            return y.cache.load("soundcloud", "client_id")

    for refresh in (False, True):
        try:
            cid = _client_id(refresh)
            if not cid:
                continue
            r = requests.get(_SC_API.format(tid), params={"client_id": cid},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
            if r.status_code in (401, 403):
                continue
            if not r.ok:
                return None
            isrc = ((r.json() or {}).get("publisher_metadata") or {}).get("isrc")
            isrc = re.sub(r"[^A-Za-z0-9]", "", isrc or "").upper()
            return isrc if len(isrc) == 12 else None
        except Exception as e:
            logger.info(f"ℹ️ No pude leer el ISRC de SoundCloud {tid}: {e}")
            return None
    return None
