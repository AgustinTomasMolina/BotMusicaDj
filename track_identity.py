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
   decisión conservadora hasta que el dueño diga otra cosa (diseño, UNKNOWN 18).
5. Si quedan aceptados de dos obras distintas (artistas sin nada en común) es ambiguo: nada.
   Entre varios del mismo tema desempata la duración más cercana (nunca veta: el diseño midió
   que como veto cuesta aciertos, los videos oficiales duran más).

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
    r"\s*(?:,|&|\+|/|\bx\b|\bvs\.?\s|\band\b|\bfeat\.?\s|\bft\.?\s|\bfeaturing\b|\bwith\b|·)\s*", re.I)
# Sufijos de canal que no son parte del nombre del artista.
_CHANNEL_SUFFIX = re.compile(r"\s*-\s*topic\s*$|\s*vevo\s*$|\s*official\s*$", re.I)
# Palabras que dicen "esto es una versión" (remix, edit, extended mix…).
_VERSION_KW = re.compile(
    r"remix|\bmix\b|\bedit\b|\bvip\b|bootleg|\bflip\b|rework|version|instrumental|a\s?capp?ella|"
    r"acoustic|sped\s*up|slowed|nightcore|\b8d\b|bass\s*boosted|\blive\b|en\s+vivo|\bconcert\b|remaster|"
    r"\bdub\b|\bdemo\b", re.I)
# Palabras de ruido del upload (el diseño §8.3): un paréntesis que las tiene y no habla de
# versión se descarta entero ("(Last Night in Vegas Visual)", "(Official Video - 4K)").
_NOISE = re.compile(
    r"official|oficial|\bvideo\b|\bv[ií]deo\b|\baudio\b|\blyrics?\b|\bletra\b|visuali[sz]er|\bvisual\b|"
    r"\bhd\b|\bhq\b|\b4k\b|\bmv\b|\bm/v\b|out\s+now|free\s*d(?:ownload|l)|premiere|\bexplicit\b|"
    r"\bclip\b|full\s*album|music\s*video|subtitulad|con\s+letra|\bprod\b|produced\s+by|^\s*from\b|"
    r"^\s*\d{4}\s*$|^\s*(?:19|20)\d{2}\b", re.I)
_GROUP = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_FEAT_GROUP = re.compile(r"[\(\[]([^\(\)\[\]]*?)[\s,\-–]*\b(?:feat\.?|ft\.?|featuring)\s+([^\(\)\[\]]+)[\)\]]", re.I)
_FEAT = re.compile(r"\s*\b(?:feat\.?|ft\.?|featuring)\s+([^\(\)\[\]\-–—|]+)", re.I)
_WITH = re.compile(r"\(\s*with\s+([^\)]+)\)", re.I)
# "Artista - Tema": guion con espacio de al menos un lado ("Kanye West- Stronger"), o " : ".
# "Jay-Z" o "blink-182" no se parten (sin espacios alrededor).
_ARTIST_TITLE_SEP = re.compile(r"\s+[-–—]\s*|\s*[-–—]\s+|\s+:\s+")
# Ruido suelto al final del título, sin paréntesis: "M83 'Midnight City' Official video".
_TRAILING_NOISE = re.compile(
    r"\s*[-|]?\s*\b(?:official\s+(?:music\s+)?(?:video|audio|visuali[sz]er|lyric\s+video|mv)|"
    r"(?:official\s+)?lyric\s+video|music\s+video|official\s+mv|official|premiere)\s*$", re.I)
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
_DURATION_UNKNOWN_S = 31.0          # SoundCloud Go+ reporta 30.0: es el preview, no el tema


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
        return "live:" + re.sub(r"\s+", " ", re.sub(r"\blive\b|\ben vivo\b", " ", t)).strip()
    # 2) Remix / bootleg / flip / rework de alguien: otra obra.
    m = re.search(r"^(.*?)\b(remix|bootleg|flip|rework)\b", t)
    if m:
        who = m.group(1).strip()
        return f"{m.group(2)}:{who}" if who else m.group(2)
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
    t = re.sub(r"#\w+", " ", t)
    t = re.sub(r"\.(?:mp3|wav|flac|m4a|aiff?|ogg)\s*$", "", t, flags=re.I)
    t = re.sub(r"^\s*premiere\s*[:\-|–]\s*", "", t, flags=re.I)
    t = re.sub(r"\bfree\s*d(?:ownload|l)\b|<3", " ", t, flags=re.I)
    t = _TRACK_PREFIX.sub("", t)
    # Grupos de adentro hacia afuera: (..)/[..] con ruido y sin versión → afuera; [..] sin
    # versión = sello / catálogo / "OUT NOW" → afuera. Los demás (…) quedan: son del título.
    for _ in range(3):
        t = _INNER_GROUP.sub(
            lambda m: m.group(0) if _VERSION_KW.search(m.group(1))
            else (" " if (m.group(0).startswith("[") or _NOISE.search(m.group(1))) else "\x00" + m.group(1) + "\x01"),
            t)
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

    def _group(m):
        nonlocal version, version_text
        g = m.group(1)
        v = version_of(g)
        if v is not None:
            if version == "original":
                version, version_text = v, g
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
        if version == "original":
            version, version_text = version_of(m.group(1)), m.group(1)
        base = base[:m.start()]
    m = _BARE_VERSION.search(base)
    if m:
        if version == "original":
            version, version_text = version_of(m.group(1)), m.group(1)
        base = base[:m.start()]
    return (normalize(base), version, frozenset(feat), query_text(base), query_text(version_text),
            _alt_titles(re.sub(r"\s+", " ", base)))


def _make(artist_field: str, song: str) -> Identity:
    artist_field = unicodedata.normalize("NFKC", artist_field or "")
    arts = split_artists(artist_field)
    base, version, feat, qt, vt, alts = _title_and_version(song, arts)
    names = tuple(n for n in _artist_names(artist_field) if normalize(n))
    return Identity(arts, base, version, feat, names,
                    _CHANNEL_SUFFIX.sub("", _strip_artist_groups(artist_field)).strip(), qt, vt, alts)


def _same_artist_text(a: str, b: str) -> bool:
    x, y = split_artists(a), split_artists(b)
    return bool(x and y) and (bool(x & y) or {n.replace(" ", "") for n in x} == {n.replace(" ", "") for n in y})


def parse_entry(title: str, uploader: str) -> Identity:
    """Identidad de un resultado de YouTube/SoundCloud: el título manda ("Artista - Tema");
    si no trae artista, el artista es el uploader (sin " - Topic")."""
    t = _clean_title(title)
    # "Artista「Tema」" (Japón) y "Artista 'Tema'" / "Artista "Tema"" (videos oficiales).
    m = (re.fullmatch(r"(?P<a>[^「『]+?)\s*[「『](?P<t>[^」』]+)[」』]\s*(?P<r>.*)", t)
         or re.fullmatch(r"(?P<a>[^'\"“‘]+?)\s+[\"“'‘](?P<t>[^\"”'’]+)[\"”'’](?P<r>\s.*)?", t))
    if (m and m.group("a").strip() and not normalize(m.group("r") or "")
            and not _ARTIST_TITLE_SEP.search(m.group("a"))):     # "A - Tema 'Remix'" no es esto
        return _make(m.group("a"), m.group("t"))
    # "Artista | Tema" solo si lo de la izquierda es el uploader; si no, "Tema | Boiler Room…".
    # " // CLIP", " // Visualizer": lo que va después de // no es el título.
    t = re.split(r"\s+//\s+", t)[0]
    parts = re.split(r"\s+\|\s+", t)
    if len(parts) >= 2:
        if _same_artist_text(parts[0], uploader) and normalize(parts[1]):
            return _make(parts[0], parts[1])
        t = parts[0]
    parts = _ARTIST_TITLE_SEP.split(t, maxsplit=1)
    if len(parts) == 2 and normalize(parts[0]) and normalize(parts[1]):
        left, right = parts
        # "Tema - Artista": el artista es el uploader y está a la derecha, no a la izquierda.
        # Los (…) que venían pegados al artista ("Tema - Artista (X Remix)") son del tema.
        if _same_artist_text(right, uploader) and not _same_artist_text(left, uploader):
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


def parse_fields(artist: str, title: str) -> Identity:
    """Identidad de campos ya separados (Deezer: artist.name + title)."""
    return _make(artist or "", _clean_title(title))


def artists_match(a: Identity, b: Identity) -> bool:
    """¿Comparten algún artista? Por nombre, por el nombre sin espacios ("KanyeWestVEVO" →
    "kanyewest" = "kanye west"), o por los tokens del nombre completo en las DOS direcciones
    ("Chase and Status" = "Chase & Status"; "Avicii" no es "Avicii Tribute")."""
    all_a, all_b = a.artists | a.feat, b.artists | b.feat
    if all_a & all_b:
        return True
    if {x.replace(" ", "") for x in all_a} & {y.replace(" ", "") for y in all_b}:
        return True
    tokens_a = {w for x in all_a for w in x.split()}
    tokens_b = {w for x in all_b for w in x.split()}
    return (bool(a.artists) and bool(b.artists)
            and any(set(x.split()) <= tokens_b for x in a.artists)
            and any(set(y.split()) <= tokens_a for y in b.artists))


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


def isrc_confirms(wanted: Identity, found: Identity, wanted_s: float | None, found_s: float | None) -> bool:
    """¿El track que Deezer dio para un ISRC es lo que dice el upload? El ISRC lo carga quien
    sube el tema y puede ser el de otra versión ("Stronger" con el ISRC de la instrumental):
    la versión tiene que ser la misma, y el título base igual o la duración a ±5 %. El artista
    no se exige (un sello o un coautor sube el tema: Kobosil → Kuko)."""
    if wanted.version != found.version:
        return False
    if same_title(wanted, found):
        return True
    if wanted_s and found_s and wanted_s > _DURATION_UNKNOWN_S:
        return abs(wanted_s - found_s) <= 0.05 * wanted_s
    return False


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
    return parse_fields((track.get("artist") or {}).get("name") or "", track.get("title") or "")


def pick_track(entry: Identity, tracks: list[dict], duration_s: float | None = None) -> dict | None:
    """El track de Deezer que es la entrada, o None. Si los aceptados son de obras distintas
    (artistas sin nada en común), es ambiguo y no se elige ninguno. Entre varios del mismo tema
    (dos lanzamientos, "Levels" y "Levels (Original Version)") gana el de duración más cercana
    a la entrada; sin duración, el primero.

    Dos casos en que el texto no alcanza y se rechaza (nunca se elige otra versión):
    - la entrada nombra invitados ("Old Town Road ft. Billy Ray Cyrus") que el aceptado no
      tiene, y en Deezer hay OTRA versión del tema con esos invitados (el Remix): no se sabe
      cuál es;
    - la duración delata otra edición del mismo tema: el aceptado está lejos (> 10 %) y otra
      edición (Radio Edit, Extended…) coincide (±max(10 s, 5 %)). "Born Slippy (Nuxx)" de
      268 s es la Radio Edit de 264 s, no el original de 454 s."""
    pool = [(t, deezer_identity(t)) for t in tracks]
    accepted = [(t, i) for t, i in pool if is_same_track(entry, i)]
    if not accepted:
        return None
    first = accepted[0][1]
    for _, i in accepted[1:]:
        if not artists_match(first, i):
            logger.info(f"🔎 Identidad ambigua: «{accepted[0][0].get('title')}» de dos artistas distintos; no elijo ninguno.")
            return None
    if entry.feat:
        con_feat = [(t, i) for t, i in accepted if _has_guests(i, entry.feat)]
        if con_feat:
            accepted = con_feat
        elif any(same_title(entry, i) and artists_match(entry, i) and i.version != entry.version
                 and _has_guests(i, entry.feat) for _, i in pool):
            logger.info(f"🔎 «{accepted[0][0].get('title')}» no tiene a los invitados; otra versión sí: no elijo.")
            return None
    dur = duration_s if duration_s and duration_s > _DURATION_UNKNOWN_S else None
    if not dur:
        return accepted[0][0]
    best, _ = min(accepted, key=lambda ti: abs((ti[0].get("duration") or 0) - dur))   # min es estable
    if abs((best.get("duration") or 0) - dur) > max(10.0, 0.10 * dur):
        for t, i in pool:
            if (i.version in _SAME_WORK and i.version != entry.version and same_title(entry, i)
                    and artists_match(entry, i) and abs((t.get("duration") or 0) - dur) <= max(10.0, 0.05 * dur)):
                logger.info(f"🔎 La duración ({dur:.0f} s) es la de «{t.get('title')}», no la de «{best.get('title')}»: no elijo.")
                return None
    return best


def _has_guests(ident: Identity, guests: frozenset) -> bool:
    todos = {n.replace(" ", "") for n in ident.artists | ident.feat}
    return all(g.replace(" ", "") in todos for g in guests)


# ---------------------------------------------------------------- SoundCloud → ISRC

_SC_API = "https://api-v2.soundcloud.com/tracks/{}"


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
            body = r.json()
            meta = (body.get("publisher_metadata") if isinstance(body, dict) else None) or {}
            isrc = re.sub(r"[^A-Za-z0-9]", "", str(meta.get("isrc") or "")).upper()
            return isrc if len(isrc) == 12 else None
        except Exception as e:
            logger.info(f"ℹ️ No pude leer el ISRC de SoundCloud {tid}: {type(e).__name__}")
            return None
    return None
