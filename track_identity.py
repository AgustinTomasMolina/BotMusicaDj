"""Identidad de un tema: ¿este resultado es EL tema que se pidió?

"Temas parecidos" resolvía la semilla con el primer resultado de una búsqueda de texto libre
en Deezer, sin comparar nada: `From The Top` / `IMMINENT - Topic` terminaba en WAP de Cardi B
(10 sustituciones en 39 entradas reales medidas). Acá está la regla que la reemplaza (la
estrategia "B" del diseño de parecidas, §8.3):

1. Normalizar: sin acentos, casefold, sin puntuación, CUALQUIER alfabeto (cirílico, japonés,
   coreano…); el uploader pierde " - Topic"/VEVO; el título pierde SOLO el ruido del upload:
   (Official Video/Audio), (Letra), (… Visual), (Official Visualizer), [sello/OUT NOW],
   "Premiere", hashtags, "Official video" suelto, prefijos de pista ("10.", "B1."). Se separa
   "Artista - Tema" (-, –, —, " : "), "Artista | Tema", "Artista 'Tema'", "Artista「Tema」" y
   "Tema - Artista" cuando el artista es el uploader.
2. Un paréntesis que NO es ruido ni versión es parte del título: "Born Slippy (Nuxx)" no es
   "Born Slippy", "One (Your Name)" no es "One", "(Part 1)" no es "(Part 2)". Si nombra a un
   artista del tema ("Air I Breathe (Sub Focus & Wilkinson)") es un crédito, no título.
3. Versión canónica: original (también Remastered, Album/Single/Explicit Version, Original
   Mix) / extended / radio / club / short / instrumental / acapella / acoustic / sped_up /
   slowed / nightcore / live / vip / dub / remix:<quién> / edit:<quién>… Las de contenido
   distinto (instrumental, acoustic…) mandan sobre radio/extended: "Instrumental Radio Edit"
   es instrumental.
4. Un resultado ES el tema solo si comparten algún artista, el título base es igual y la versión
   es la misma. Otra versión del mismo tema (Radio Edit por Original, Extended…) NO se acepta:
   decisión conservadora hasta que el dueño diga otra cosa (diseño, UNKNOWN 18). Lo que el
   dueño dijo (f40-r2), solo para las versiones de la Station: el EXTENDED del tema se ofrece
   como otra edición, marcada (`es_extended_de`, `dura_como_extended`).
5. Si quedan aceptados de dos obras distintas (artistas sin nada en común) es ambiguo: nada.
   Entre varios del mismo tema desempata la duración más cercana (nunca veta: el diseño midió
   que como veto cuesta aciertos, los videos oficiales duran más).

Nunca se devuelve "el más parecido": o se demuestra que es el tema, o no hay resultado.

Solo stdlib (unicodedata, re): no agrega dependencias. Lo único con red es
`fetch_soundcloud_isrc` (API v2 de SoundCloud, con el client_id que cachea yt-dlp)."""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field, replace

logger = logging.getLogger("track_identity")

# CONGELADAS (cierre del P0, 2026-09-29): no se agregan palabras ni formatos a estas listas; decidir la
# misma grabación solo con texto no escala, lo que falta va por evidencia (ISRC, duración), no por palabras.
# Separadores entre artistas: "A, B", "A & B", "A x B", "A vs B", "A feat. B", "A · B"…
_ARTIST_SEP = re.compile(
    r"\s*(?:,|&|\+|/|\bx\b|\bvs\.?\s|\band\b|\bfeat\.?\s|\bft\.?\s|\bfeaturing\b|\bwith\b|·)\s*", re.I)
# Sufijos de canal que no son parte del nombre del artista.
_CHANNEL_SUFFIX = re.compile(r"\s*-\s*topic\s*$|\s*vevo\s*$|\s*official\s*$", re.I)
# Palabras que dicen "esto es una versión" (remix, edit, extended mix…).
_VERSION_KW = re.compile(
    r"remix|\bmix\b|\bedit\b|\bvip\b|bootleg|\bflip\b|rework|version|instrumental|a\s?capp?ella|"
    r"acoustic|sped\s*up|slowed|nightcore|\b8d\b|bass\s*boosted|\blive\b|en\s+vivo|\bconcert\b|remaster|"
    r"\bdub\b|\bdemo\b|\boriginal\b", re.I)
# Palabras de ruido del upload (el diseño §8.3): un paréntesis que las tiene y no habla de
# versión se descarta entero ("(Last Night in Vegas Visual)", "(Official Video - 4K)").
_NOISE = re.compile(
    r"official|oficial|\bvideo\b|\bv[ií]deo\b|\baudio\b|\blyrics?\b|\bletra\b|visuali[sz]er|\bvisual\b|"
    r"\bhd\b|\bhq\b|\b4k\b|\bmv\b|\bm/v\b|out\s+now|free\s*d(?:ownload|l)|premiere|\bexplicit\b|"
    r"\bclip\b|full\s*album|music\s*video|subtitulad|con\s+letra|\bprod\b|produced\s+by|^\s*from\b|"
    r"^\s*\d{4}\s*$|^\s*(?:19|20)\d{2}\b|\bdir\.?\s+by\b|directed\s+by|performance\s+ver|"
    r"dance\s+(?:practice|performance)", re.I)
# Lo que viene después de " | " y dice evento, vivo, versión o año: el upload es OTRA cosa que
# el tema de estudio ("Coldplay - Fix You | Glastonbury 2024" es el vivo de 7:47).
_EVENT = re.compile(
    r"\b(?:19|20)\d{2}\b|festival|glastonbury|coachella|tomorrowland|lollapalooza|\btour\b|\bgira\b|"
    r"concierto|\bsessions?\b|boiler\s*room|tiny\s*desk|\bkexp\b|\bawards?\b|\bstage\b|\ben\s+vivo\b", re.I)
_STRONG_VERSION = re.compile(
    r"\blive\b|remix|\bmix\b|\bedit\b|acoustic|instrumental|a\s?capp?ella|sped\s*up|slowed|nightcore|"
    r"bootleg|\bvip\b|\bcover\b", re.I)
# [..] que es sello, catálogo o género (no título): "[Monstercat Release]", "[KNTXT010]", "[Techno]".
_LABEL_BRACKET = re.compile(
    r"records?|recordings|\bmusic\b|release|label|exclusive|\bep\b|\blp\b|album|single|\bfree\b|\bdl\b|"
    r"download|premiere|out\s+now|^\s*[A-Za-z]{2,}\s?-?\d{2,}\s*$|^\s*(?:hard\s+)?(?:techno|house|trance|dnb|"
    r"drum\s*(?:&|and|n)\s*bass|dubstep|hardstyle|psytrance|edm|electronic|minimal|progressive|bounce|"
    r"hardcore|garage|breakbeat|trap|phonk)\s*$", re.I)
# Palabras que en un "artista" dicen que no es el artista (tributos, covers, karaoke…).
_NOT_THE_ARTIST = {"tribute", "cover", "covers", "band", "karaoke", "orchestra", "quartet", "experience",
                   "players", "piano", "lullaby", "arcade", "emulation", "ringtone", "ringtones", "singers"}
_GROUP = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_FEAT_GROUP = re.compile(r"[\(\[]([^\(\)\[\]]*?)[\s,\-–]*\b(?:feat\.?|ft\.?|featuring)\s+([^\(\)\[\]]+)[\)\]]", re.I)
# El invitado termina en " - " o "|" con espacios, no en un guion pegado ("ft. JAY-Z", "ft. T-Pain").
_FEAT = re.compile(r"\s*\b(?:feat\.?|ft\.?|featuring)\s+((?:(?!\s+[-–—|]\s)[^\(\)\[\]|])+)", re.I)
_WITH = re.compile(r"\(\s*with\s+([^\)]+)\)", re.I)
# "Artista - Tema": guion con espacio de al menos un lado ("Kanye West- Stronger"), o " : ".
# "Jay-Z" o "blink-182" no se parten (sin espacios alrededor).
_ARTIST_TITLE_SEP = re.compile(r"\s+[-–—]\s*|\s*[-–—]\s+|\s+:\s+")
# Ruido suelto al final del título, sin paréntesis: "M83 'Midnight City' Official video".
_TRAILING_NOISE = re.compile(
    r"\s*[-|]?\s*\b(?:official\s+(?:music\s+)?(?:video|audio|visuali[sz]er|lyric\s+video|mv)|"
    r"(?:official\s+)?lyric\s+video|music\s+video|official\s+mv|official|premiere|m/v|mv)\s*$", re.I)
# Prefijo de número de pista de un vinilo/álbum: "10. Tema", "10.Tema", "B1. Tema", "A2) Tema".
_TRACK_PREFIX = re.compile(r"^\s*[A-Da-d]?\d{1,2}\s*[.)]\s*(?=[^\d\s.])")
# Versión escrita sin paréntesis ni guion al final: "pjanoo radio edit".
_BARE_VERSION = re.compile(
    r"\s(radio\s+edit|extended\s+(?:mix|edit|version)|original\s+mix|club\s+(?:mix|edit)|radio\s+version|"
    r"album\s+version)\s*\)?\s*$", re.I)
# Grupo más interno: "(Album Version (Explicit))" se limpia de adentro hacia afuera.
_INNER_GROUP = re.compile(r"[\(\[]([^\(\)\[\]]*)[\)\]]")
# Delante de radio/club/short/extended solo pueden ir estas palabras; si hay otra cosa es un
# remix de alguien ("Cazzette's NYC Mode Radio Mix" no es la Radio Edit).
_PLAIN_PREFIX = {"original", "the", "official", "clean", "explicit", "extended", "single"}
# Familia de ediciones del mismo tema (para ver si la duración delata otra edición).
_SAME_WORK = {"original", "radio", "extended", "club", "short", "edit"}
_MAX_ARTIST_QUERIES = 3
# SoundCloud Go+ reporta 30.0 s: es el preview, no el tema. SOLO ese valor (con un margen de
# ±1 s por redondeo) y SOLO de SoundCloud (o de una fuente que no se conoce) es "no se sabe".
# Antes cualquier duración ≤ 31 s era "no se sabe" en todas las fuentes y un ringtone de 16 s
# de YouTube pasaba como el tema de 306 s (auditoría f40).
_SC_PREVIEW_S = (29.0, 31.0)


def normalize(s: str | None) -> str:
    """Forma de comparación: sin acentos, casefold, sin apóstrofos, solo letras y números de
    CUALQUIER alfabeto (antes quedaban solo a-z: "Кино - Группа крови" quedaba vacío)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = re.sub(r"[’'`´]", "", s)
    s = s.replace("$", "s")
    s = re.sub(r"[\W_]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def query_text(s: str | None) -> str:
    """Texto para CONSULTAR a Deezer: como `normalize` pero sin descomponer (NFKD separa las
    marcas de ポ o de 봄 y la consulta dejaría de ser el título)."""
    s = unicodedata.normalize("NFC", s or "").casefold()
    s = re.sub(r"[’'`´]", "", s)
    s = re.sub(r"[\W_]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _strip_artist_groups(s: str) -> str:
    # "BTS (방탄소년단)": el paréntesis del artista es un alias, no otro artista.
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", " ", s or "").strip()


def split_artists(s: str | None) -> frozenset:
    """Nombres normalizados de los artistas de un campo ("A & B feat. C", "X - Topic")."""
    s = _CHANNEL_SUFFIX.sub("", _strip_artist_groups(s or ""))
    return frozenset(p for p in (normalize(x) for x in _ARTIST_SEP.split(s)) if p)


def _artist_names(s: str | None) -> list[str]:
    """Los nombres tal como vienen (sin normalizar), para armar consultas a Deezer."""
    s = _CHANNEL_SUFFIX.sub("", _strip_artist_groups(s or ""))
    return [p.strip() for p in _ARTIST_SEP.split(s) if p and p.strip()]


def _without_noise_words(t: str) -> str:
    return re.sub(r"\b(official|oficial|video|audio|hd|hq|4k|mv)\b", " ", t).strip()


def version_of(text: str) -> str | None:
    """Versión canónica de un grupo entre paréntesis ("Radio Edit" → "radio"), o None si el
    grupo no habla de versión."""
    if not _VERSION_KW.search(text or ""):
        return None
    t = re.sub(r"\s+", " ", _without_noise_words(normalize(text))).strip()
    # 1) Contenido distinto: manda sobre radio/extended ("Instrumental Radio Edit" = instrumental).
    for pattern, canon in ((r"\ba ?capp?ella\b", "acapella"), (r"\binstrumental\b", "instrumental"),
                           (r"\bacoustic\b", "acoustic"), (r"\bsped ?up\b", "sped_up"), (r"\bslowed\b", "slowed"),
                           (r"\bnightcore\b", "nightcore"), (r"\b8d\b", "8d"), (r"\bbass ?boosted\b", "bass_boosted"),
                           (r"\bdemo\b", "demo")):
        if re.search(pattern, t):
            return canon
    # Un vivo es UNA grabación: "(Live)" no es "(Live at Wembley)" ni "(Live Session For…)".
    if re.search(r"\blive\b|\ben vivo\b|\bconcert\b", t):
        detail = re.sub(r"\s+", " ", re.sub(r"\blive\b|\ben vivo\b", " ", t)).strip()
        # El año no distingue si queda otro dato ("Live Aid 1985" = "Live Aid"); solo, sí ("Live 1991").
        sin_anio = re.sub(r"\s+", " ", re.sub(r"\b(?:19|20)\d{2}\b", " ", detail)).strip()
        return "live:" + (sin_anio or detail)
    # 2) Remix / bootleg / flip / rework de alguien: otra obra.
    #    f40-r2 (aprobado por el dueño): "Remix Extended" / "Extended Remix" es la edición LARGA
    #    de ESE remix, otra edición que el remix a secas: lleva el sufijo "+extended" ("Omnya
    #    Remix Extended" → "remix:omnya+extended"). Antes el "extended" se perdía (quedaba
    #    "remix:omnya", igual que "(Omnya Remix)") y una versión de 282 s pasaba por la de 232 s.
    #    No agrega palabras a las listas congeladas: "extended" ya era una de ellas.
    m = re.search(r"^(.*?)\b(remix|bootleg|flip|rework)\b", t)
    if m:
        extendido = bool(re.search(r"\bextended\b", t))
        who = re.sub(r"\s+", " ", re.sub(r"\bextended\b", " ", m.group(1))).strip()
        canon = f"{m.group(2)}:{who}" if who else m.group(2)
        return canon + "+extended" if extendido else canon
    # 3) El original con otro nombre: Remastered, Original Mix, Album/Single/Explicit Version…
    if re.search(r"\bremaster", t) or re.fullmatch(
            r"(digital )?(original|album|single|explicit|video original|lp|main)( version| mix)?|original", t):
        return "original"
    # 4) Ediciones del mismo tema, solo si delante no dice de quién ("Original Radio Edit" sí;
    #    "Cazzette's NYC Mode Radio Mix" es de Cazzette: otra obra).
    for pattern, canon in ((r"\bradio (edit|version|mix)\b", "radio"), (r"\bextended\b", "extended"),
                           (r"\bclub (mix|edit|version)\b", "club"), (r"\bshort (mix|edit|version)\b", "short")):
        m = re.search(pattern, t)
        if m:
            who = " ".join(w for w in t[:m.start()].split() if w not in _PLAIN_PREFIX)
            return f"{canon}:{who}" if who else canon
    m = re.search(r"^(.*?)\bedit\b", t)
    if m:
        who = m.group(1).strip()
        return f"edit:{who}" if who else "edit"
    if re.search(r"\bvip\b", t):
        return "vip"
    if re.search(r"\bdub\b", t):
        return "dub"
    m = re.search(r"^(.*?)\bmix\b", t)
    if m:
        who = m.group(1).strip()
        return f"mix:{who}" if who else "mix"
    return "otra:" + t


@dataclass(frozen=True)
class Identity:
    artists: frozenset
    base_title: str
    version: str = "original"          # "original" si el título no dice nada
    feat: frozenset = field(default_factory=frozenset)
    artist_names: tuple = ()           # nombres sin normalizar (solo para las consultas)
    artist_text: str = ""              # el campo de artista entero, sin " - Topic" (consultas)
    query_title: str = ""              # título base para consultar (sin NFKD)
    version_text: str = ""             # la versión como está escrita ("radio edit"), para consultar
    alt_titles: frozenset = field(default_factory=frozenset)   # "봄날 (Spring Day)": los dos nombres


def _clean_title(t: str | None) -> str:
    """Saca del título el ruido del upload y nada más (el resto es parte del título)."""
    t = unicodedata.normalize("NFKC", t or "")            # （…） de ancho completo → (…)
    t = re.sub(r"#[^\W\d]\w*", " ", t)                    # "#techno" es ruido; "#52" es parte del título
    t = re.sub(r"(?<=[\)\]])\s*#\d+\s*$", "", t)          # "… (1998 Remastered Version) #02": número de pista
    t = re.sub(r"\.(?:mp3|wav|flac|m4a|aiff?|ogg)\s*$", "", t, flags=re.I)
    t = re.sub(r"^\s*premiere\s*[:\-|–]\s*", "", t, flags=re.I)
    t = re.sub(r"\bfree\s*d(?:ownload|l)\b|<3", " ", t, flags=re.I)
    t = _TRACK_PREFIX.sub("", t)
    # Grupos de adentro hacia afuera: (..)/[..] con ruido y sin versión → afuera; [..] de sello,
    # catálogo, género u "OUT NOW", o puesto después de otro grupo ("(Original Mix) [Filth on
    # Acid]") → afuera. Los demás quedan: son del título ("Creep [Part 2]", "(Nuxx)").
    def _inner(m):
        g = m.group(1)
        if _VERSION_KW.search(g):
            return m.group(0)
        if _NOISE.search(g):
            return " "
        if m.group(0).startswith("[") and (
                _LABEL_BRACKET.search(g) or m.string[:m.start()].rstrip().endswith((")", "]"))
                or (len(g.split()) == 1 and not re.search(r"\d", g))):     # "[Drumcode]": una marca
            return " "
        return "\x00" + g + "\x01"

    for _ in range(3):
        t = _INNER_GROUP.sub(_inner, t)
    t = t.replace("\x00", "(").replace("\x01", ")")
    t = re.sub(r"\s*[\(\[]\s*[\)\]]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—|")
    for _ in range(2):
        t = _TRAILING_NOISE.sub("", t).strip(" -–—|")
    return t


def _alt_titles(base: str) -> frozenset:
    """"봄날 (Spring Day)", "Группа крови (Blood Type)": título en otro alfabeto + su nombre en
    latino entre paréntesis. Los dos nombres valen. Solo en ese caso: "Born Slippy (Nuxx)" o
    "One (Your Name)" (latino afuera) siguen siendo UN título entero."""
    m = re.fullmatch(r"\s*([^()]+?)\s*\(([^()]+)\)\s*", base)
    if not m:
        return frozenset()
    outside, inside = m.group(1), m.group(2)
    if re.search(r"[A-Za-z]", outside) or not re.search(r"[^\W\d_]", outside) or not re.search(r"[A-Za-z]", inside):
        return frozenset()
    return frozenset({normalize(outside), normalize(inside)}) - {""}


def _title_and_version(t: str, artists: frozenset = frozenset()) -> tuple[str, str, frozenset, str, str, frozenset]:
    """(título base normalizado, versión, invitados, título para consultar, versión escrita,
    títulos alternativos)."""
    t = t.strip().strip("'\"‘’“”").strip()          # "BLACKPINK - ‘뚜두뚜두 (DDU-DU DDU-DU)’"
    feat: set = set()

    def _feat_in_group(m):
        # "(Radio Edit - feat. A and B)" → invitados A, B y queda "(Radio Edit)".
        feat.update(split_artists(m.group(2)))
        inner = m.group(1).strip(" -–,")
        return f" ({inner}) " if inner else " "

    t = _FEAT_GROUP.sub(_feat_in_group, t)
    for m in _FEAT.finditer(t):
        feat |= split_artists(m.group(1))
    t = _FEAT.sub(" ", t)
    for m in _WITH.finditer(t):
        feat |= split_artists(m.group(1))
    t = _WITH.sub(" ", t)
    version, version_text = "original", ""

    def _sumar(v, g):
        """La primera versión manda; la única excepción es un "(Extended Mix)" aparte que sigue
        a un remix: "(Omnya Remix) (Extended Mix)" es el Extended de ESE remix, igual que
        "(Omnya Remix Extended)" (f40-r2). Antes el segundo grupo se tiraba sin mirarlo."""
        nonlocal version, version_text
        if version == "original":
            version, version_text = v, g
        elif v == "extended" and re.match(r"(?:remix|bootleg|flip|rework)\b", version) \
                and not version.endswith("+extended"):
            version, version_text = version + "+extended", f"{version_text} {g}"

    def _group(m):
        g = m.group(1)
        v = version_of(g)
        if v is not None:
            _sumar(v, g)
            return " "
        names = split_artists(g)
        if names and artists and names & artists:      # crédito: "(Sub Focus & Wilkinson)"
            feat.update(names)
            return " "
        return " (" + g + ") "                          # parte del título: "(Nuxx)", "(Part 1)"

    base = _GROUP.sub(_group, t)
    # "Tema - Radio Edit" (así lo escriben Deezer y los Topic) → versión
    m = re.search(r"\s[-–—]\s*([^-–—]+)$", base)
    if m and version_of(m.group(1)):
        _sumar(version_of(m.group(1)), m.group(1))
        base = base[:m.start()]
    m = _BARE_VERSION.search(base)
    if m:
        _sumar(version_of(m.group(1)), m.group(1))
        base = base[:m.start()]
    # "Matador Remasterizado 2008", "De Música Ligera Remasterizado 2007": el original, sin paréntesis.
    base = re.sub(r"\s(?:remasterizad[oa]|remastered|remaster)(?:\s+(?:19|20)\d{2})?\s*$", " ", base, flags=re.I)
    return (normalize(base), version, frozenset(feat), query_text(base), query_text(version_text),
            _alt_titles(re.sub(r"\s+", " ", base)))


def _make(artist_field: str, song: str, extra_alts: frozenset = frozenset()) -> Identity:
    artist_field = unicodedata.normalize("NFKC", artist_field or "")
    arts = split_artists(artist_field)
    base, version, feat, qt, vt, alts = _title_and_version(song, arts)
    if extra_alts:
        alts = frozenset(alts | extra_alts | {base}) - {""}
    names = tuple(n for n in _artist_names(artist_field) if normalize(n))
    return Identity(arts, base, version, feat, names,
                    _CHANNEL_SUFFIX.sub("", _strip_artist_groups(artist_field)).strip(), qt, vt, alts)


def _same_artist_text(a: str, b: str) -> bool:
    x, y = split_artists(a), split_artists(b)
    return bool(x and y) and (bool(x & y) or {n.replace(" ", "") for n in x} == {n.replace(" ", "") for n in y})


def _split_sides(t: str, uploader: str) -> tuple[str, str] | None:
    parts = _ARTIST_TITLE_SEP.split(t, maxsplit=1)
    if len(parts) == 2 and normalize(parts[0]) and normalize(parts[1]):
        return parts[0], parts[1]
    return None


def parse_entry(title: str, uploader: str) -> Identity:
    """Identidad de un resultado de YouTube/SoundCloud: el título manda ("Artista - Tema");
    si no trae artista, el artista es el uploader (sin " - Topic")."""
    t = _clean_title(title)
    # "Artista「Tema」" (Japón) y "Artista 'Tema'" / "Artista "Tema"" (videos oficiales). Lo que
    # sigue en latino después de 「…」 es el nombre en inglés: "YOASOBI「夜に駆ける」Racing Into The Night".
    m = re.fullmatch(r"(?P<a>[^「『]+?)\s*[「『](?P<t>[^」』]+)[」』]\s*(?P<r>.*)", t)
    if m and m.group("a").strip() and not _ARTIST_TITLE_SEP.search(m.group("a")):
        resto = m.group("r") or ""
        if not normalize(resto):
            return _make(m.group("a"), m.group("t"))
        if re.fullmatch(r"[A-Za-z0-9 ,.'!?&-]+", resto.strip()):
            return _make(m.group("a"), m.group("t"), frozenset({normalize(resto)}))
    m = re.fullmatch(r"(?P<a>[^'\"“‘]+?)\s+[\"“'‘](?P<t>[^\"”'’]+)[\"”'’](?P<r>\s.*)?", t)
    if (m and m.group("a").strip() and not normalize(m.group("r") or "")
            and not _ARTIST_TITLE_SEP.search(m.group("a"))):     # "A - Tema 'Remix'" no es esto
        return _make(m.group("a"), m.group("t"))
    # " // CLIP", " // Visualizer": lo que va después de // no es el título.
    t = re.split(r"\s+//\s+", t)[0]
    forced = None
    parts = re.split(r"\s+\|{1,2}\s+", t)
    if (len(parts) == 2 and _NOISE.search(parts[0]) and not _ARTIST_TITLE_SEP.search(parts[0])
            and not _same_artist_text(parts[0], uploader)
            and _ARTIST_TITLE_SEP.search(parts[1]) and not _EVENT.search(parts[1])):
        # f43 (OK del dueño): "GTG Premiere | Kashpitzky - For The Vision". A la izquierda solo
        # hay ruido del upload (`_NOISE`, sin separador propio) y la derecha trae su propio
        # "Artista - Tema" sin evento: el prefijo se descarta y se lee la derecha. Sin palabras
        # nuevas en las listas congeladas; "Adele | Hello" y "| Glastonbury 2024" no entran acá.
        # Si la izquierda ES el uploader ("Audio Bullys | We Don't Care - Radio Edit" subido por
        # Audio Bullys), es el artista aunque tenga una palabra de `_NOISE`: sigue la regla de
        # "Artista | Tema" de abajo, como antes de f43.
        t = parts[1]
        parts = [t]
    if len(parts) >= 2:
        rest = " ".join(parts[1:])
        if _same_artist_text(parts[0], uploader) and normalize(parts[1]) and not _EVENT.search(rest):
            return _make(parts[0], parts[1])                  # "BICEP | GLUE"
        if _EVENT.search(rest) or _STRONG_VERSION.search(rest) or (_VERSION_KW.search(rest) and not _NOISE.search(rest)):
            # "Fix You | Glastonbury 2024": es un vivo/evento; la versión queda desconocida.
            # ("| Official Music Video - HD Version" es ruido, no versión.)
            forced = "desconocida:" + normalize(rest)
        elif (not _ARTIST_TITLE_SEP.search(parts[0]) and not _NOISE.search(rest) and normalize(parts[1])
              and len(parts) == 2):
            return _make(parts[0], parts[1])                  # "Adele | Hello" (subido por otro)
        t = parts[0]
    ident = _parse_plain(t, uploader)
    return replace(ident, version=forced) if forced else ident


def _parse_plain(t: str, uploader: str, swap: bool = False) -> Identity:
    sides = _split_sides(t, uploader)
    if sides:
        left, right = sides
        # "Tema - Artista": el artista es el uploader y está a la derecha, no a la izquierda.
        # Los (…) que venían pegados al artista ("Tema - Artista (X Remix)") son del tema.
        if swap or (_same_artist_text(right, uploader) and not _same_artist_text(left, uploader)):
            groups = " ".join(re.findall(r"[\(\[][^\)\]]*[\)\]]", right))
            left, right = _strip_artist_groups(right), f"{left} {groups}".strip()
        return _make(left, right)
    # Sin separador: el artista es el uploader. Si el título empieza con su nombre, se saca.
    up = _CHANNEL_SUFFIX.sub("", uploader or "").strip()
    if up and normalize(t).startswith(normalize(up) + " "):
        rest = re.sub(r"^\s*" + re.escape(up) + r"\s*", "", t, flags=re.I)
        if normalize(rest):
            t = rest
    return _make(uploader or "", t)


def parse_entry_swapped(title: str, uploader: str) -> Identity | None:
    """La otra lectura de "A - B" ("God's Plan - Drake", subido por un tercero): B artista, A
    tema. Solo se usa si la lectura normal no encontró nada; el resultado igual tiene que pasar
    la regla de identidad entera (artista Y título), así que no abre sustituciones."""
    t = _clean_title(title)
    if re.search(r"\s+\|{1,2}\s+|[「『\"“]", t):
        return None
    t = re.split(r"\s+//\s+", t)[0]
    sides = _split_sides(t, uploader)
    if not sides or (_same_artist_text(sides[1], uploader) and not _same_artist_text(sides[0], uploader)):
        return None                                   # sin separador, o ya se leyó al revés
    return _parse_plain(t, uploader, swap=True)


def parse_fields(artist: str, title: str) -> Identity:
    """Identidad de campos ya separados (Deezer: artist.name + title). Algunos títulos de Deezer
    repiten al artista adelante ("Beethoven - Moonlight Sonata", "Richter: On the Nature of
    Daylight"): si lo de la izquierda es el artista, se saca."""
    t = _clean_title(title)
    m = re.match(r"\s*(.+?)(?:\s+[-–—]\s+|:\s+)(.+)$", t)
    if m and _is_artist_prefix(m.group(1), artist or ""):
        t = m.group(2)
    return _make(artist or "", t)


def _is_artist_prefix(prefix: str, artist: str) -> bool:
    p, a = normalize(prefix), normalize(_CHANNEL_SUFFIX.sub("", artist))
    if not p or not a:
        return False
    return p == a or p == a.replace(" ", "") or (len(p) >= 4 and a.split()[-1] == p)


def artists_match(a: Identity, b: Identity) -> bool:
    """¿Comparten algún artista? Ver `_artist_relation`."""
    return _artist_relation(a, b) is not None


def _artist_relation(a: Identity, b: Identity) -> str | None:
    """"directo": por nombre, por el nombre sin espacios ("KanyeWestVEVO" → "kanyewest" =
    "kanye west"), o por los tokens del nombre completo en las DOS direcciones ("Chase and
    Status" = "Chase & Status"; "Avicii" no es "Avicii Tribute"). "alias": solo por `_alias`
    (dos alfabetos, apellido), que es evidencia más débil y necesita duración. None: no."""
    all_a, all_b = a.artists | a.feat, b.artists | b.feat
    if all_a & all_b:
        return "directo"
    if {x.replace(" ", "") for x in all_a} & {y.replace(" ", "") for y in all_b}:
        return "directo"
    tokens_a = {w for x in all_a for w in x.split()}
    tokens_b = {w for x in all_b for w in x.split()}
    if (bool(a.artists) and bool(b.artists)
            and any(set(x.split()) <= tokens_b for x in a.artists)
            and any(set(y.split()) <= tokens_a for y in b.artists)):
        return "directo"
    if any(_alias(x, y) or _alias(y, x) for x in a.artists for y in b.artists):
        return "alias"
    return None


def _latin(w: str) -> bool:
    return bool(re.search(r"[a-z]", w))


def _alias(short: str, long: str) -> bool:
    """Un nombre contenido en otro que igual es el mismo artista (una sola dirección):
    - el mismo nombre en dos alfabetos: "Kenshi Yonezu" dentro de "米津玄師 Kenshi Yonezu";
    - el apellido de un nombre completo: "Beethoven" = "Ludwig van Beethoven" (apellido de 5+
      letras y nombre de 3+ palabras: "Snake" no es "DJ Snake", "The" no es "The Weeknd").
    Nunca si lo que sobra dice tributo/cover/karaoke…: "Avicii" no es "Avicii Tribute"."""
    s, lw = short.split(), long.split()
    if not s or not set(s) < set(lw):
        return False
    extra = set(lw) - set(s)
    if extra & _NOT_THE_ARTIST:
        return False
    if all(_latin(w) for w in s) and not any(_latin(w) for w in extra):
        return True
    if not any(_latin(w) for w in s) and all(_latin(w) for w in extra):
        return True
    return len(s) == 1 and len(s[0]) >= 5 and len(lw) >= 3 and lw[-1] == s[0]


def same_title(a: Identity, b: Identity) -> bool:
    """Mismo título base, o uno es el nombre alternativo del otro ("봄날 (Spring Day)" = "Spring Day")."""
    if not a.base_title or not b.base_title:
        return False
    if a.base_title == b.base_title:
        return True
    return bool(({a.base_title} | a.alt_titles) & ({b.base_title} | b.alt_titles)) and bool(a.alt_titles or b.alt_titles)


def is_same_track(wanted: Identity, candidate: Identity) -> bool:
    """El candidato ES el tema pedido: artista en común, mismo título base, misma versión."""
    return (same_title(wanted, candidate)
            and wanted.version == candidate.version and artists_match(wanted, candidate))


# Grados de evidencia de "es la misma grabación", del más fuerte al más débil.
EVIDENCE_GRADES = ("isrc+duracion", "isrc", "texto+duracion", "texto")
# Ediciones del mismo tema que la regla de la Radio Edit por duración puede aceptar (nunca vivo,
# remix ni instrumental).
_EDITIONS = {"radio", "extended", "club", "short", "edit"}


def _dur_diff(w: float | None, c: float | None) -> float | None:
    return abs(w - c) if (w and c) else None


def evidencia_misma_grabacion(wanted: Identity, cand: Identity, wanted_s=None, cand_s=None, *,
                              via: str = "texto", edicion: bool = False, audio_exacto: bool = False) -> str | None:
    """¿`cand` (Deezer) es la misma grabación que `wanted` (el upload)? Devuelve el GRADO de la
    evidencia ("isrc+duracion" > "isrc" > "texto+duracion" > "texto") o None si no alcanza.
    Todas las aceptaciones pasan por acá; las reglas que miran al resto del pool (ambigüedad,
    invitados, la edición que delata la duración) quedan en `pick_track_evidencia`.

    Base: mismo título ∧ misma versión ∧ artista en común. Además:
    - via="isrc" (el track que Deezer da para el ISRC del upload): el ISRC lo carga quien sube el
      tema, así que el artista puede no coincidir (Kobosil sube "Eiskalt" de Kuko: el crédito
      está en `contributors`), pero entonces la duración tiene que confirmar a ±5 %. Sin duración
      (preview de 30 s) y con otro artista, no: "Hurt" de la cuenta de Johnny Cash con el ISRC
      del tributo "Johnny Crash".
    - texto: el upload dura más del doble → no; el candidato no tiene al PRIMER artista del
      upload y la duración difiere más del 10 % → no ("J Balvin, Willy William - Mi Gente" de
      186 s no es el "Mi Gente" de Willy William de 137 s); si el artista solo coincide por
      alias (otro alfabeto, apellido), la duración tiene que cuadrar (±max(10 s, 10 %)). Con
      `audio_exacto` (SoundCloud: el upload ES el audio, no un video con intro), un upload más
      LARGO que no cuadra es otra edición: "Argy & Omnya - Aria" de 314,8 s es la Extended, no el
      "Aria" de 236 s (uno más corto puede ser un recorte o estar acelerado: se deja).
    - edicion=True (regla de la Radio Edit, aprobada por el dueño): upload SIN versión y
      candidato que es una edición del mismo tema (radio/extended/club/short/edit) cuya duración
      coincide a ±max(3 s, 2 %)."""
    rel = _artist_relation(wanted, cand)
    w, c = duration_or_none(wanted_s), duration_or_none(cand_s)
    diff = _dur_diff(w, c)
    if via == "isrc":
        if wanted.version != cand.version:
            return None
        confirma = diff is not None and diff <= 0.05 * w
        if not same_title(wanted, cand):
            # Otro título (el compositor adelante, un subtítulo): solo si la duración es la misma
            # a ±max(3 s, 2 %) — "Beethoven - Moonlight Sonata (Glenn playing…)" 298,1 s = 298 s.
            return "isrc+duracion" if diff is not None and diff <= max(3.0, 0.02 * w) else None
        if rel is None and not confirma:
            return None
        return "isrc+duracion" if confirma else "isrc"
    if not same_title(wanted, cand) or rel is None:
        return None
    if edicion:
        if wanted.version != "original" or cand.version not in _EDITIONS:
            return None
        return "texto+duracion" if diff is not None and diff <= max(3.0, 0.02 * w) else None
    if wanted.version != cand.version:
        return None
    if diff is None:
        return None if rel == "alias" else "texto"
    if w > 2 * c:
        return None
    cuadra = duraciones_cuadran(w, c)
    if not cuadra and ((audio_exacto and w > c) or rel == "alias" or not _names_first_artist(wanted, cand)):
        return None
    return "texto+duracion" if cuadra else "texto"


def _names_first_artist(wanted: Identity, cand: Identity) -> bool:
    primero = split_artists(wanted.artist_names[0]) if wanted.artist_names else frozenset()
    return not primero or artists_match(Identity(primero, ""), Identity(cand.artists | cand.feat, ""))


def duration_or_none(s, fuente: str | None = None) -> float | None:
    """Duración utilizable o None: nan, inf y ≤ 0 son "no se sabe" (con inf, una tolerancia en
    % confirmaba cualquier cosa). El 30.0 de un preview Go+ también, pero solo si viene de
    SoundCloud o de una fuente que no se conoce (`fuente=None`): un audio de 30 s de YouTube o
    de un MP3 directo ES de 30 s, y uno de 16 s es de 16 s (un ringtone, no el tema)."""
    try:
        s = float(s)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(s) or s <= 0:
        return None
    if (fuente is None or fuente.lower() == "soundcloud") and _SC_PREVIEW_S[0] <= s <= _SC_PREVIEW_S[1]:
        return None
    return s


def duraciones_cuadran(w: float, c: float) -> bool:
    """¿Dos duraciones CONOCIDAS son la misma edición? ±max(10 s, 10 %) de `w`: la tolerancia
    con la que `evidencia_misma_grabacion` da "+duracion" a una aceptación por texto (cubre el
    redondeo a segundos y un silencio al principio o al final). La de ±max(3 s, 2 %) es otra
    cosa: la de la regla de la Radio Edit, que acepta OTRA etiqueta de versión solo por
    duración y por eso pide más precisión."""
    return abs(w - c) <= max(10.0, 0.10 * w)


# ---------------------------------------------------------------- el Extended del tema (f40-r2)
# Decisión del dueño (f40-r2): "el extended dura más, y para los DJ eso es ORO". Una versión
# del MISMO tema que dice ser la edición larga (Extended / Extended Mix / Original Mix, o
# "Remix Extended" del MISMO remix) y dura MÁS que el tema de la Station se ofrece como otra
# edición, marcada. Nunca algo más corto (radio edits, fragmentos) ni algo más largo sin esa
# etiqueta (un video con intro, un vivo).
#
# f40-r3 (decisión del dueño): "Club Mix" NO es el Extended: a veces es otra mezcla. Un Club
# Mix más largo que el tema cae en la regla de "más largo sin la etiqueta": no se ofrece.
#
# Cota superior: 3× la duración del tema. Los casos reales miden 1,22× («The Point Of Living
# (Omnya Remix Extended)» 282 s contra 232 s), 1,33× («Aria» Extended 314,8 s contra 236 s) y
# 1,77× («Hera (Original Mix)» 6:04 contra 3:26); una radio edit de 2:30 con su extended de 7
# minutos da 2,8×. Más de 3× ya no es una edición del tema sino otra cosa con el mismo nombre
# (un loop de una hora, un mix entero, un set).
EXTENDED_MAX_RATIO = 3.0
# "Original Mix" es, en la música de club, el nombre de la edición larga; la
# etiqueta se lee de la versión TAL COMO ESTÁ ESCRITA (`version_text`): "Album Version" o
# "Remastered" también son "original" para la identidad, pero no dicen "soy la larga".
_EXTENDED_LABEL = re.compile(r"\bextended\b|\boriginal mix\b")
# Versiones de la Station cuyo Extended es el del original: el tema mismo y su Radio Edit.
_EXTENDED_DEL_ORIGINAL = {"original", "radio"}


def es_extended_de(wanted: Identity, cand: Identity) -> bool:
    """¿`cand` DICE ser el Extended del tema `wanted`? Solo el texto (la duración la mira
    `dura_como_extended`): mismo título, artista en común por nombre (no por alias: acá se acepta
    OTRA etiqueta de versión y la evidencia tiene que ser fuerte) con el primer artista del
    tema, y la etiqueta de edición larga. Un remix no es el original: si la Station es «X (Omnya
    Remix)», solo «X (Omnya Remix Extended)» es su Extended, no «X (Extended Mix)»; si la
    Station es el original (o su Radio Edit), el Extended de un remix no cuenta."""
    if not same_title(wanted, cand) or _artist_relation(wanted, cand) != "directo":
        return False
    if not _names_first_artist(wanted, cand) or not _EXTENDED_LABEL.search(normalize(cand.version_text)):
        return False
    if wanted.version in _EXTENDED_DEL_ORIGINAL:
        return cand.version in ("extended", "original")
    return cand.version == wanted.version + "+extended"


def dura_como_extended(wanted_s, cand_s) -> bool | None:
    """¿La duración confirma una edición LARGA? None si alguna no se sabe (no se puede
    confirmar). True si el candidato dura más que el tema más allá de la tolerancia de "misma
    edición" (`duraciones_cuadran`) y como mucho `EXTENDED_MAX_RATIO` veces. False si no: más
    corto, igual de largo (entonces no es otra edición) o demasiado largo."""
    w, c = duration_or_none(wanted_s), duration_or_none(cand_s)
    if w is None or c is None:
        return None
    return c > w and not duraciones_cuadran(w, c) and c <= EXTENDED_MAX_RATIO * w


def search_limit(entry: Identity) -> int:
    """Con versión (Radio Edit, remix…) hacen falta más resultados: el original y sus remixes
    ocupan los primeros 10 ("Adagio For Strings (Radio Edit)" no aparecía)."""
    return 10 if entry.version == "original" else 25


def deezer_queries(entry: Identity) -> list[str]:
    """Consultas a Deezer para una entrada, la estricta primero: `artist:"a" track:"t"` por
    cada artista, después el nombre completo + el título base (+ la versión si la trae),
    después "a t" por artista. Nunca el título solo: con títulos genéricos ("From The Top",
    "Exodus", "Intro") eso trae cualquier cosa."""
    if not entry.query_title or not entry.artists or not entry.artist_names:
        return []
    names = list(entry.artist_names[:_MAX_ARTIST_QUERIES])
    out: list[str] = []

    def add(q: str):
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in out:
            out.append(q)

    for a in names:
        add(f'artist:"{a}" track:"{entry.query_title}"')
    add(f"{entry.artist_text} {entry.query_title}")     # el nombre entero ("Chase & Status")
    if entry.version_text:
        add(f"{entry.artist_text} {entry.query_title} {entry.version_text}")
    for alt in sorted(entry.alt_titles):                 # "봄날 (Spring Day)" → también "spring day"
        add(f'artist:"{names[0]}" track:"{alt}"')
    for a in names:
        add(f"{a} {entry.query_title}")
    return out


def deezer_identity(track: dict) -> Identity:
    """Identidad de un track de Deezer. `contributors` (solo viene en /track/…, p. ej. por ISRC)
    suma los coautores: "Eiskalt (Short Mix)" es de Kuko y Kobosil."""
    ident = parse_fields((track.get("artist") or {}).get("name") or "", track.get("title") or "")
    otros = ", ".join(c.get("name") or "" for c in track.get("contributors") or [] if isinstance(c, dict))
    return replace(ident, feat=ident.feat | (split_artists(otros) - ident.artists)) if otros else ident


def pick_track(entry: Identity, tracks: list[dict], duration_s: float | None = None) -> dict | None:
    return pick_track_evidencia(entry, tracks, duration_s)[0]


def pick_track_evidencia(entry: Identity, tracks: list[dict], duration_s: float | None = None,
                         audio_exacto: bool = False, identify=None) -> tuple[dict | None, str | None]:
    """(track de Deezer que es la entrada, grado de evidencia) o (None, None). Cada candidato
    pasa por `evidencia_misma_grabacion`; acá quedan solo las reglas que necesitan ver el pool
    entero (por eso no pueden vivir en la función de a pares). Si los aceptados son de obras distintas
    (artistas sin nada en común), es ambiguo y no se elige ninguno. Entre varios del mismo tema
    (dos lanzamientos, "Levels" y "Levels (Original Version)") gana el de duración más cercana
    a la entrada; sin duración, el primero.

    Casos en que el texto no alcanza y se rechaza (nunca se elige otra versión):
    - la entrada nombra invitados ("Old Town Road ft. Billy Ray Cyrus") que el aceptado no
      tiene, en Deezer hay otra versión del tema con esos invitados (el Remix) y la duración
      del aceptado no cuadra (113 s contra 158 s). Si cuadra ("Latch" 257 s, el crédito que
      Deezer no escribe) se acepta;
    - la entrada NO nombra invitados, todos los aceptados traen invitados que la entrada no
      menciona ("Alors On Danse (Featuring Erik Hassle)") y hay una edición del mismo tema sin
      ellos: es otra grabación;
    - la duración delata otra edición del mismo tema: el aceptado está lejos (> 10 %) y otra
      edición (Radio Edit, Extended…) coincide (±max(10 s, 5 %)). "Born Slippy (Nuxx)" de
      268 s no es el original de 454 s.
    En ese último caso, y cuando no hay ningún aceptado, un upload SIN versión puede ser una
    edición del tema: se acepta si UNA sola edición coincide a ±max(3 s, 2 %) (regla de la
    Radio Edit por duración, aprobada por el dueño): Born Slippy de 268 s → la Radio Edit de 264.

    `identify` (track → Identity; por defecto `deezer_identity`) permite aplicar las MISMAS
    reglas a un pool de otra fuente: la Station de SoundCloud lo usa con `parse_entry` sobre
    el título y el uploader de cada resultado de su búsqueda (cada track con `duration` en s)."""
    dur = duration_or_none(duration_s)
    pool = [(t, (identify or deezer_identity)(t)) for t in tracks]
    grados = {id(t): evidencia_misma_grabacion(entry, i, dur, t.get("duration"), audio_exacto=audio_exacto)
              for t, i in pool}
    accepted = [(t, i) for t, i in pool if grados[id(t)]]
    if not accepted:
        return _edicion_por_duracion(entry, pool, dur)
    first = accepted[0][1]
    for _, i in accepted[1:]:
        if not artists_match(first, i):
            logger.info(f"🔎 Identidad ambigua: «{accepted[0][0].get('title')}» de dos artistas distintos; no elijo ninguno.")
            return None, None

    def cerca(t):
        d = duration_or_none(t.get("duration"))
        return bool(dur and d) and abs(d - dur) <= max(10.0, 0.10 * dur)

    if entry.feat:
        con_feat = [(t, i) for t, i in accepted if _has_guests(i, entry.feat)]
        if con_feat:
            accepted = con_feat
        elif any(same_title(entry, i) and artists_match(entry, i) and i.version != entry.version
                 and _has_guests(i, entry.feat) for _, i in pool):
            accepted = [(t, i) for t, i in accepted if cerca(t)]
            if not accepted:
                logger.info("🔎 El aceptado no tiene a los invitados, otra versión sí y la duración no cuadra: no elijo.")
                return None, None
    else:
        sin_extra = [(t, i) for t, i in accepted if not _unnamed_guests(entry, i)]
        if sin_extra:
            accepted = sin_extra
        elif any(same_title(entry, i) and i.version in _SAME_WORK and artists_match(entry, i)
                 and not _unnamed_guests(entry, i) for _, i in pool):
            logger.info(f"🔎 «{accepted[0][0].get('title')}» trae invitados que el upload no nombra: no elijo.")
            return None, None
    if not dur:
        return accepted[0][0], grados[id(accepted[0][0])]
    best, _ = min(accepted, key=lambda ti: abs((ti[0].get("duration") or 0) - dur))   # min es estable
    if abs((best.get("duration") or 0) - dur) > max(10.0, 0.10 * dur):
        for t, i in pool:
            if (i.version in _SAME_WORK and i.version != entry.version and same_title(entry, i)
                    and artists_match(entry, i) and abs((t.get("duration") or 0) - dur) <= max(10.0, 0.05 * dur)):
                logger.info(f"🔎 La duración ({dur:.0f} s) es la de «{t.get('title')}», no la de «{best.get('title')}».")
                return _edicion_por_duracion(entry, pool, dur)
    # El elegido no cuadra en duración y hay otro con el mismo texto y otra duración ("Army Of Me"
    # de 234 s y de 318 s, upload de 272 s): no se sabe cuál es.
    if grados[id(best)] == "texto" and any(
            abs((t.get("duration") or 0) - (best.get("duration") or 0)) > max(10.0, 0.10 * (best.get("duration") or 0))
            for t, i in pool if is_same_track(entry, i)):
        logger.info(f"🔎 «{best.get('title')}» tiene lanzamientos de distinta duración y ninguno cuadra: no elijo.")
        return None, None
    # Otra grabación del tema (un vivo, un remix; no una edición) coincide a ±3 s y el elegido no:
    # "Genesis - Mama" de 416,96 s es el vivo de 417, no el remaster de 410. Solo con audio exacto
    # (SoundCloud): en un video la intro mueve la duración y un remix o un vivo cae cerca por
    # casualidad (medido: costaba 7 aciertos en videos de YouTube).
    d_best = abs((best.get("duration") or 0) - dur)
    for t, i in (pool if audio_exacto and d_best > 3.0 else []):
        d = _dur_diff(dur, duration_or_none(t.get("duration")))
        if (i.version != entry.version and i.version not in _SAME_WORK and same_title(entry, i) and artists_match(entry, i)
                and d is not None and d <= 3.0):
            logger.info(f"🔎 «{t.get('title')}» dura lo mismo que el upload: no elijo «{best.get('title')}».")
            return _edicion_por_duracion(entry, pool, dur)
    return best, grados[id(best)]


def _edicion_por_duracion(entry: Identity, pool: list, dur: float | None) -> tuple[dict | None, str | None]:
    """Regla de la Radio Edit por duración: la edición del mismo tema cuya duración coincide a
    ±max(3 s, 2 %). Si coinciden ediciones distintas (Radio Edit y Club Mix), es ambiguo: nada."""
    eds = [(t, i) for t, i in pool
           if evidencia_misma_grabacion(entry, i, dur, t.get("duration"), edicion=True)]
    if not eds or len({i.version for _, i in eds}) > 1:
        return None, None
    best = min(eds, key=lambda ti: abs((ti[0].get("duration") or 0) - dur))[0]
    logger.info(f"🎯 Por duración ({dur:.0f} s): «{best.get('title')}».")
    return best, "texto+duracion"


def _has_guests(ident: Identity, guests: frozenset) -> bool:
    todos = {n.replace(" ", "") for n in ident.artists | ident.feat}
    return all(g.replace(" ", "") in todos for g in guests)


def _unnamed_guests(entry: Identity, cand: Identity) -> frozenset:
    """Artistas del candidato que la entrada no nombra (ni como artista ni como invitado)."""
    nombrados = entry.artists | entry.feat
    return frozenset(n for n in cand.artists | cand.feat
                     if not artists_match(Identity(frozenset({n}), ""), Identity(nombrados, "")))


# ---------------------------------------------------------------- SoundCloud → ISRC

_SC_API = "https://api-v2.soundcloud.com/tracks/{}"


def soundcloud_client_id(refresh: bool = False, track_id: str | None = None) -> str | None:
    """El client_id público de la API v2 que yt-dlp saca de la web de SoundCloud y cachea.

    `refresh` (después de un 401/403): con `track_id`, se abre ese track con yt-dlp, que al
    recibir el 401 con el client_id vencido lo renueva solo (lo que hacía siempre
    `fetch_soundcloud_isrc`); sin `track_id` (p. ej. antes de una búsqueda), se borra el
    cacheado y yt-dlp lo vuelve a sacar de la web al inicializar su extractor. Las fallas de
    red o de yt-dlp suben: el que llama decide y nunca loguea el client_id."""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as y:
        if refresh and track_id:
            y.extract_info(f"https://api.soundcloud.com/tracks/{track_id}", download=False, process=False)
        elif refresh:
            y.cache.store("soundcloud", "client_id", None)
            y.get_info_extractor("Soundcloud").initialize()
        return y.cache.load("soundcloud", "client_id")


def fetch_soundcloud_isrc(track_id: str | int | None, timeout: float = 8.0) -> str | None:
    """ISRC de un track de SoundCloud (publisher_metadata.isrc de la API v2), o None.

    Medido en el diseño: 12 de 20 tracks de SoundCloud lo traen y 11 están en Deezer por
    ISRC. La API v2 pide el client_id público que yt-dlp saca de la web y cachea; si no está
    cacheado o venció (401/403), se deja que yt-dlp lo renueve abriendo el track y se
    reintenta una vez. Cualquier falla → None (se sigue por título y artista). El log nunca
    lleva la excepción entera: su mensaje puede traer la URL con el client_id."""
    tid = str(track_id if track_id is not None else "").strip()
    if not re.fullmatch(r"[0-9]{1,20}", tid):
        return None
    try:
        import requests
        import yt_dlp  # noqa: F401  (sin yt-dlp no hay client_id: se sigue por título y artista)
    except ImportError:
        return None

    for refresh in (False, True):
        try:
            cid = soundcloud_client_id(refresh, tid)
            if not cid:
                continue
            r = requests.get(_SC_API.format(tid), params={"client_id": cid},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
            if r.status_code in (401, 403):
                continue
            if not r.ok:
                return None
            body = r.json()
            meta = (body.get("publisher_metadata") if isinstance(body, dict) else None) or {}
            isrc = re.sub(r"[^A-Za-z0-9]", "", str(meta.get("isrc") or "")).upper()
            return isrc if len(isrc) == 12 else None
        except Exception as e:
            logger.info(f"ℹ️ No pude leer el ISRC de SoundCloud {tid}: {type(e).__name__}")
            return None
    return None
