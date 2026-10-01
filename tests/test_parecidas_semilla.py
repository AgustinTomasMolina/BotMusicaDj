"""Temas parecidos: la semilla y las versiones por plataforma son EL tema pedido o nada (f33).

El bug: "Temas parecidos" de `From The Top` / `IMMINENT - Topic` armaba una lista de rap
alrededor de WAP de Cardi B, porque la semilla era el primer resultado de Deezer sin comparar
nada. Sobre 40 entradas reales el código anterior aceptaba OTRA canción en 10 de 39 casos
decidibles. Sin red: las respuestas de Deezer están grabadas en
`tests/fixtures/parecidas_semilla_40.json` (junto con la verdad etiquetada a mano y el motivo)
y reemplazan a `similares._get`.
"""
import importlib
import json
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest
import similares
import track_identity as ti

FIX = Path(__file__).parent / "fixtures"
DATOS = json.loads((FIX / "parecidas_semilla_40.json").read_text(encoding="utf-8"))
E = {e["n"]: e for e in DATOS["entradas"]}
A = {e["n"]: e for e in DATOS["hallazgos"]}      # casos de la auditoría (160 entradas nuevas)


def fila_deezer(t: dict) -> dict:
    """Una fila grabada con la forma de la API de Deezer (artist como objeto)."""
    f = {"id": t["id"], "title": t["title"], "artist": {"id": t["artist_id"], "name": t["artist"]},
         "duration": t["duration"], "album": {"id": None}}
    if t.get("contributors"):                       # solo los tracks pedidos por ISRC
        f["contributors"] = [{"name": n} for n in t["contributors"]]
    return f


class DeezerGrabado:
    """Reemplazo de `similares._get` con las respuestas grabadas de UNA entrada. Anota las
    consultas no grabadas (contestan vacío) y los tracks que se piden por id."""

    def __init__(self, entrada: dict):
        self.e = entrada
        self.no_grabadas: list[str] = []
        self.tracks_pedidos: list[str] = []

    def __call__(self, url: str) -> dict:
        if "/track/isrc:" in url:
            filas = self.e["deezer"].get("isrc:" + unquote(url.split("/track/isrc:", 1)[1]))
            if filas is None:
                self.no_grabadas.append(url)
                return {}
            return fila_deezer(filas[0]) if filas else {"error": {"type": "DataException", "code": 800}}
        if "/search?q=" in url:
            q = unquote(url.split("/search?q=", 1)[1].split("&limit=", 1)[0])
            filas = self.e["deezer"].get(q)
            if filas is None:
                self.no_grabadas.append(q)
                return {"data": []}
            return {"data": [fila_deezer(t) for t in filas]}
        if "/track/" in url:
            self.tracks_pedidos.append(url.rsplit("/", 1)[1])
        return {}


def resolver(monkeypatch, n, con_isrc: bool = True):
    """Resuelve la entrada `n` (del set de 40, o "aNN" de los hallazgos) como el endpoint:
    título, uploader, ISRC y duración del resultado."""
    e = E[n] if n in E else A[n]
    dz = DeezerGrabado(e)
    monkeypatch.setattr(similares, "_get", dz)
    seed, motivo = similares.resolver_seed_detalle(e["titulo"], e["artista"], e["isrc"] if con_isrc else None,
                                                   e["duracion"], e["fuente"])
    assert dz.no_grabadas == [], f"el código hizo consultas que el fixture no tiene: {dz.no_grabadas}"
    return seed, motivo


def nombre(t):
    return None if t is None else f"{t['artist']['name']} - {t['title']}"


# --- casos puntuales ----------------------------------------------------------------------

def test_imminent_topic_no_se_encuentra_y_nunca_es_wap(monkeypatch):
    e = E[0]
    assert (e["titulo"], e["artista"]) == ("From The Top", "IMMINENT - Topic")
    assert e["antes"]["title"].startswith("WAP"), "el fixture tiene que reproducir el bug (antes: WAP)"
    seed, motivo = resolver(monkeypatch, 0)
    assert nombre(seed) is None, f"From The Top de IMMINENT no está en Deezer; aceptó {nombre(seed)}"
    assert motivo == similares.SEED_NOT_FOUND


def test_titulo_ambiguo_de_soundcloud_sin_isrc_en_deezer_se_rechaza(monkeypatch):
    # SC «IMMINENT - From The Top» subido por el sello RICOCHET: su ISRC no está en Deezer y
    # "From The Top" trae Lil Texas, WAP… ninguno de IMMINENT.
    seed, motivo = resolver(monkeypatch, 1)
    assert (nombre(seed), motivo) == (None, similares.SEED_NOT_FOUND)


def test_titulo_ambiguo_de_dos_artistas_distintos_no_elige_ninguno():
    # Filas GRABADAS de la búsqueda "Exodus": el Exodus de Bob Marley y el de otro artista.
    # Una entrada acreditada a los dos es ambigua: dos obras distintas con el mismo título.
    filas = [fila_deezer(t) for t in E[3]["deezer"]["Exodus"]]
    marley = next(t for t in filas if t["artist"]["name"] == "Bob Marley & The Wailers" and t["title"] == "Exodus")
    otro = next(t for t in filas if t["title"] == "Exodus" and t["artist"]["name"] != marley["artist"]["name"]
                and not ti.artists_match(ti.deezer_identity(marley), ti.deezer_identity(t)))
    entrada = ti.parse_entry(f"Bob Marley & {otro['artist']['name']} - Exodus", "")
    assert ti.pick_track(entrada, [marley]) is marley, "control: con un solo candidato lo acepta"
    assert ti.pick_track(entrada, [marley, otro]) is None, "dos obras distintas: tiene que rechazar"


def test_topic_resuelve_el_tema_exacto(monkeypatch):
    # «The Aftermath» / «Dyen - Topic»: antes Noxiouz (otra canción).
    seed, _ = resolver(monkeypatch, 4)
    assert seed and seed["id"] in (700407652, 1637952262), f"esperaba DYEN - The Aftermath; aceptó {nombre(seed)}"
    assert E[4]["antes"]["artist"] == "Noxiouz"


def test_derjay_es_derjay_no_bonez_mc(monkeypatch):
    assert E[7]["antes"]["artist"] == "Bonez MC"
    for con_isrc in (True, False):          # por ISRC y también solo por título + artista
        seed, _ = resolver(monkeypatch, 7, con_isrc)
        assert seed and seed["id"] == 4012896961, f"con_isrc={con_isrc}: aceptó {nombre(seed)}"


def test_exodus_de_imminent_no_es_la_banda_exodus_ni_bob_marley(monkeypatch):
    # «Premiere: IMMINENT, Billy Currie - Exodus [RCHEP001]»: antes Bob Marley.
    assert E[3]["antes"]["artist"] == "Bob Marley & The Wailers"
    for con_isrc in (True, False):
        seed, _ = resolver(monkeypatch, 3, con_isrc)
        assert seed and seed["id"] == 3233929051, f"con_isrc={con_isrc}: aceptó {nombre(seed)}"
    # Y la banda Exodus sigue resolviendo SU tema (control del otro lado).
    seed, _ = resolver(monkeypatch, 2)
    assert seed and seed["id"] == 3144617


def test_isrc_resuelve_lo_que_el_titulo_no_puede(monkeypatch):
    # SC «Eiskalt (Short Mix)» subido por Kobosil; Deezer lo tiene bajo Kuko. Por texto no se
    # puede demostrar (el artista no coincide) → nada. Por ISRC es exacto.
    seed, motivo = resolver(monkeypatch, 23, con_isrc=False)
    assert (nombre(seed), motivo) == (None, similares.SEED_NOT_FOUND)
    seed, _ = resolver(monkeypatch, 23, con_isrc=True)
    assert seed and seed["id"] == 2988969511, f"el ISRC {E[23]['isrc']} es Kuko - Eiskalt; aceptó {nombre(seed)}"


def test_official_audio_visual_y_letra_resuelven_el_original(monkeypatch):
    casos = {
        14: 525334532,     # FISHER - Losing It (Official Audio): el original, no la Radio Edit (574823082)
        36: 2472925871,    # Dom Dolla - Saving Up (Last Night in Vegas Visual): antes ninguno
        18: 3171003131,    # Bad Bunny - DtMF (Letra) subido por iPerol: antes DTM Life
    }
    for n, esperado in casos.items():
        seed, _ = resolver(monkeypatch, n)
        assert seed and seed["id"] == esperado, f"[{n}] {E[n]['titulo']}: aceptó {nombre(seed)}"


def test_edit_no_oficial_y_set_no_son_el_tema(monkeypatch):
    # Un edit de Charly Chaser no es el original de Bad Gyal; un Essential Mix de 2 h no es un tema.
    for n in (24, 17):
        seed, _ = resolver(monkeypatch, n)
        assert nombre(seed) is None, f"[{n}] {E[n]['titulo']}: aceptó {nombre(seed)}"


def test_deezer_caido_no_se_confunde_con_no_esta(monkeypatch):
    monkeypatch.setattr(similares, "_get", lambda url: {})       # así contesta _get sin red
    assert similares.resolver_seed_detalle("From The Top", "IMMINENT - Topic", None) == \
        (None, similares.DEEZER_UNAVAILABLE)


# --- normalización ------------------------------------------------------------------------

@pytest.mark.parametrize("titulo, uploader, artistas, base, version", [
    ("From The Top", "IMMINENT - Topic", {"imminent"}, "from the top", "original"),
    ("The Aftermath", "Dyen - Topic", {"dyen"}, "the aftermath", "original"),
    ("FISHER - Losing It (Official Audio)", "FISHER", {"fisher"}, "losing it", "original"),
    ("Dom Dolla - Saving Up (Last Night in Vegas Visual)", "Dom Dolla", {"dom dolla"}, "saving up", "original"),
    ("Lola Brooke - Get Money (Official Visualizer)", "Lola Brooke", {"lola brooke"}, "get money", "original"),
    ("Bad Bunny - DtMF (Letra)", "iPerol", {"bad bunny"}, "dtmf", "original"),
    ("Lil Texas - From The Top [OUT NOW]", "BARONG FAMILY", {"lil texas"}, "from the top", "original"),
    ("Premiere: IMMINENT, Billy Currie - Exodus [RCHEP001]", "Techno Germany", {"imminent", "billy currie"}, "exodus", "original"),
    ("DERJAY - BOUNCE DA HARD", "DERJAY", {"derjay"}, "bounce da hard", "original"),
    ("Bassjackers x Charlie Sparks – Jump Around (Official Audio)", "BSMNT", {"bassjackers", "charlie sparks"}, "jump around", "original"),
    ("BAD GYAL - DA ME (Charly Chaser HARD BOUNCE EDIT)", "X", {"bad gyal"}, "da me", "edit:charly chaser hard bounce"),
    ("Losing It (Radio Edit)", "FISHER", {"fisher"}, "losing it", "radio"),
    # Un [sello] con guion adelante no puede partir "Artista - Tema" en el lugar equivocado.
    ("[OUT NOW - RICOCHET] IMMINENT - From The Top", "RICOCHET", {"imminent"}, "from the top", "original"),
])
def test_parse_entry_normaliza(titulo, uploader, artistas, base, version):
    ident = ti.parse_entry(titulo, uploader)
    assert (set(ident.artists), ident.base_title, ident.version) == (artistas, base, version)


def test_la_consulta_estricta_va_primero_y_sin_topic():
    qs = ti.deezer_queries(ti.parse_entry("From The Top", "IMMINENT - Topic"))
    assert qs == ['artist:"IMMINENT" track:"from the top"', "IMMINENT from the top"]


def test_otra_version_del_mismo_tema_no_se_acepta():
    # Decisión conservadora (pendiente del dueño): una Radio Edit no es "el tema" pedido.
    pedido = ti.parse_entry("FISHER - Losing It", "FISHER")
    assert ti.is_same_track(pedido, ti.parse_fields("Fisher", "Losing It"))
    assert not ti.is_same_track(pedido, ti.parse_fields("Fisher", "Losing It (Radio Edit)"))


# --- auditoría: paréntesis que son título, versiones, alfabetos, formatos -------------------

@pytest.mark.parametrize("pedido, deezer_artista, deezer_titulo", [
    ("Underworld - Born Slippy (Nuxx)", "Underworld", "Born slippy"),      # (Nuxx) es parte del título
    ("Swedish House Mafia - One (Your Name) (Official Video)", "Swedish House Mafia", "One"),
    ("Pink Floyd - Another Brick In The Wall (Part 1)", "Pink Floyd", "Another Brick in the Wall (Part 2)"),
    ("Ed Sheeran - Perfect (Acoustic)", "Ed Sheeran", "Perfect"),
    ("Avicii - Levels (Instrumental Radio Edit)", "Avicii", "Levels (Radio Edit)"),
    ("Avicii - Levels (Radio Edit)", "Avicii", "Levels (Cazzette's NYC Mode Radio Mix)"),
    ("Ed Sheeran - 'Perfect' (Exclusive Live Session For Global's 'Make Some Noise')", "Ed Sheeran", "Perfect (Live)"),
    ("Simon & Garfunkel - The Sound of Silence (from The Concert in Central Park)", "Simon & Garfunkel", "The Sound Of Silence"),
])
def test_un_parentesis_que_no_es_ruido_cambia_el_tema(pedido, deezer_artista, deezer_titulo):
    assert not ti.is_same_track(ti.parse_entry(pedido, "x"), ti.parse_fields(deezer_artista, deezer_titulo))


@pytest.mark.parametrize("pedido, uploader, deezer_artista, deezer_titulo", [
    ("Underworld - Born Slippy .NUXX", "MuteSong", "Underworld", "Born Slippy (Nuxx)"),
    ("Kraftwerk - The Model (official video)", "x", "Kraftwerk", "The Model (2009 Remaster)"),
    ("Oasis - Live Forever (Official HD Remastered Video)", "Oasis", "Oasis", "Live Forever"),
    ("Kanye West - Stronger (Album Version)", "x", "Kanye West", "Stronger"),
    ("Bomfunk MC's - Freestyler (Video Original Version)", "x", "Bomfunk MC's", "Freestyler"),
    ("Bound 2 (Album Version (Explicit))", "Kanye West", "Kanye West", "Bound 2"),
    ("Black Skinhead (Digital Album Version (Explicit))", "Kanye West", "Kanye West", "Black Skinhead"),
    ("Swedish House Mafia (feat. John Martin) - Don't You Worry Child (Original Radio Edit)", "x",
     "Swedish House Mafia", "Don't You Worry Child (Radio Edit)"),
    ("Sub Focus, Wilkinson - Air I Breathe", "x", "Sub Focus", "Air I Breathe (Sub Focus & Wilkinson)"),
    ("Кино - Группа крови", "x", "Кино", "Группа крови"),
    ("Группа крови", "Кино - Topic", "Кино", "Группа крови"),
    ("BTS (방탄소년단) '봄날 (Spring Day)' Official MV", "HYBE LABELS", "BTS", "Spring Day"),
    ("Perfume - ポリリズム", "x", "Perfume", "ポリリズム"),
    ("BICEP | GLUE (Official Video)", "BICEP", "Bicep", "Glue"),
    ("björk : army of me (HD)", "björk", "Björk", "Army Of Me"),
    ("M83 'Midnight City' Official video", "M83", "M83", "Midnight City"),
    ("Kanye West- Stronger (Explicit)", "x", "Kanye West", "Stronger"),
    ("10.Avicii - Levels (Radio Edit)", "x", "Avicii", "Levels (Radio Edit)"),
    ("B1. Daydream", "I HATE MODELS", "I Hate Models", "Daydream"),
    ("Regal - Fenix (Amelie Lens Remix) PREMIERE", "x", "REGAL", "Fenix (Amelie Lens Remix)"),
    ("Another Brick in the Wall, Pt. 1 - Pink Floyd", "Pink Floyd", "Pink Floyd", "Another Brick in the Wall, Pt. 1"),
    ("Eric prydz - pjanoo radio edit", "x", "Eric Prydz", "Pjanoo (Radio Edit)"),
    ("Stronger", "KanyeWestVEVO", "Kanye West", "Stronger"),
    ("Perfume - ポリリズム（take Remix)", "x", "Perfume", "ポリリズム (take Remix)"),
    ("Muse - Uprising (Official (HD) Video)", "x", "Muse", "Uprising"),
    ("Tiësto - Adagio For Strings (BYØRN EDIT).mp3", "x", "Tiësto", "Adagio For Strings (BYØRN Edit)"),
])
def test_formatos_reales_que_si_son_el_tema(pedido, uploader, deezer_artista, deezer_titulo):
    ident = ti.parse_entry(pedido, uploader)
    assert ti.is_same_track(ident, ti.parse_fields(deezer_artista, deezer_titulo)), ident


def test_normalize_conserva_otros_alfabetos():
    assert ti.normalize("Группа КРОВИ") == "группа крови"
    assert ti.normalize("봄날 (Spring Day)") == ti.normalize("봄날") + " spring day" and ti.normalize("봄날")
    assert ti.parse_entry("Кино - Группа крови", "x").base_title == "группа крови"


@pytest.mark.parametrize("texto, version", [
    ("Instrumental Radio Edit", "instrumental"), ("Radio Edit", "radio"), ("Original Radio Edit", "radio"),
    ("Remastered 2011", "original"), ("2009 Remaster", "original"), ("Album Version", "original"),
    ("Single Version", "original"), ("Explicit Version", "original"), ("Official HD Remastered Video", "original"),
    ("Original Mix", "original"), ("Extended Mix", "extended"), ("Acoustic", "acoustic"),
    ("Cazzette's NYC Mode Radio Mix", "radio:cazzettes nyc mode"), ("Live", "live:"),
    ("Live at Wembley", "live:at wembley"), ("Diplo & Jauz Remix", "remix:diplo jauz"),
])
def test_version_canonica(texto, version):
    assert ti.version_of(texto) == version


def test_la_version_va_en_la_consulta_y_el_limite_sube():
    e = ti.parse_entry("Adagio For Strings (Radio Edit)", "Tiësto")
    assert ti.deezer_queries(e) == ['artist:"Tiësto" track:"adagio for strings"', "Tiësto adagio for strings",
                                    "Tiësto adagio for strings radio edit"]
    assert ti.search_limit(e) == 25 and ti.search_limit(ti.parse_entry("Adagio For Strings", "Tiësto")) == 10


def test_artistas_en_las_dos_direcciones_y_vevo():
    avicii = ti.parse_fields("Avicii", "Levels")
    assert not ti.artists_match(avicii, ti.parse_fields("Avicii Tribute", "Levels")), "un tributo no es el artista"
    assert not ti.artists_match(ti.parse_fields("Avicii Tribute", "Levels"), avicii)
    assert ti.artists_match(ti.parse_entry("Stronger", "KanyeWestVEVO"), ti.parse_fields("Kanye West", "Stronger"))
    assert ti.artists_match(ti.parse_entry("Baddadan", "Chase and Status"), ti.parse_fields("Chase & Status", "Baddadan"))


def test_la_duracion_desempata_entre_lanzamientos_del_mismo_tema():
    # Filas GRABADAS de «björk : army of me»: dos "Army Of Me" de Björk (234 s y 318 s).
    a = A["a10"]
    filas = [fila_deezer(t) for q in a["deezer"] for t in a["deezer"][q] if t["title"] == "Army Of Me"]
    cortas, largas = [t for t in filas if t["duration"] == 234], [t for t in filas if t["duration"] == 318]
    assert cortas and largas
    entrada = ti.parse_entry(a["titulo"], a["artista"])
    for orden in ([largas[0], cortas[0]], [cortas[0], largas[0]]):
        assert ti.pick_track(entrada, orden, 240)["duration"] == 234, "tiene que ganar la más cercana a 240 s"
        assert ti.pick_track(entrada, orden, 310)["duration"] == 318, "y la más cercana a 310 s"
        # 267 s no cuadra con ninguno y los dos lanzamientos duran distinto: no se sabe cuál es.
        assert ti.pick_track(entrada, orden, 267) is None


@pytest.mark.parametrize("n", sorted(A))
def test_hallazgos_de_la_auditoria(monkeypatch, n):
    a = A[n]
    seed, _ = resolver(monkeypatch, n)
    v = a["verdad"]
    if v["estado"] == "none":
        assert seed is None, f"{a['titulo']} ({a['motivo']}): aceptó {nombre(seed)}"
    elif v["estado"] == "unknown":                 # la auditoría no lo pudo decidir: sin verdad
        pytest.skip(a["motivo"])
    elif v["estado"] == "neg":                     # esos ids son seguro otra grabación
        assert seed is None or seed["id"] not in v["ids"], f"{a['titulo']} ({a['motivo']}): aceptó {nombre(seed)}"
    else:
        assert seed is None or seed["id"] in v["ids"], f"{a['titulo']} ({a['motivo']}): aceptó {nombre(seed)}"


# Resultado esperado de cada hallazgo (id, o None = rechazo honesto con su motivo).
ESPERADO = {
    # Radio Edit por duración (aprobada por el dueño): a50 Born Slippy 268 s → Radio Edit 264 s,
    # a26 Get Lucky, a60 Gravity (Edit), a82 Around the World, a138 Levels.
    "a50": 140857827, "a131": None, "a118": 424036432, "a140": 14383882, "a137": 14383880, "a138": 14383880,
    "a16": None, "a67": 1178682, "a19": 448260732, "a20": 1783253587, "a68": 389034231, "a145": 373789461,
    "a100": 3212563611, "a2": None, "a26": 66609426, "a34": None, "a119": None, "a115": 68350113,
    "a10": None,        # 267 s: "Army Of Me" de 234 y de 318 s, ninguno cuadra → no se sabe
    "a44": 92198180, "a112": 1178682, "a141": 14383880, "a97": 144535404, "a106": 116914090, "a60": 699067122,
    "a65": None, "a159": None, "a82": 14598347,
    # auditoría 2
    "b205": None, "b30": None, "b201": None, "b28": 75526535, "b56": 2333553245, "b208": 60904700,
    "b185": 1355376732, "b157": 2966772721, "b69": 3786035712, "b119": 6686670, "b94": 533609232,
    "b137": 70322130, "b133": 4091936771, "b194": 1843514397, "b189": 824731152, "b123": 13247459,
    "b8": 2393352885, "b90": None, "b102": None, "b63": None,
    # auditoría 3
    "c117": 128846783,  # el "Hurt" de Johnny Cash por texto; nunca el del tributo (14990882)
    "c154": None, "c174": 3158428,
}


def test_hallazgos_resultado_exacto(monkeypatch):
    assert set(ESPERADO) == set(A)
    real = {n: (resolver(monkeypatch, n)[0] or {}).get("id") for n in sorted(A)}
    assert real == ESPERADO


@pytest.mark.parametrize("pedido, uploader, deezer_artista, deezer_titulo, es", [
    # segmento después de | con evento/año/vivo → la versión queda desconocida
    ("Coldplay - Fix You | Glastonbury 2024", "BBC Music", "Coldplay", "Fix You", False),
    ("Coldplay - Fix You | Live", "x", "Coldplay", "Fix You", False),
    ("Meek Mill - Dreams and Nightmares | Lyrics", "x", "Meek Mill", "Dreams and Nightmares", True),
    ("Amr Diab - Tamally Maak | Official Music Video - HD Version", "x", "Amr Diab", "Tamally Maak", True),
    ("Adele | Hello", "Lionel Richie", "Adele", "Hello", True),
    ("KANYE WEST | STRONGER", "KanyeWestVEVO", "Kanye West", "Stronger", True),
    ("QUEVEDO || Gran Via #52", "x", "Quevedo", "Gran Via #52", True),        # '||' parte, '#52' no es hashtag
    ("QUEVEDO || BZRP Music Sessions #52", "x", "Quevedo", "BZRP Music Sessions #52", False),   # "Sessions" = evento
    # corchetes: título, sello o marca
    ("Radiohead - Creep [Part 2]", "x", "Radiohead", "Creep", False),
    ("SHM - One [Your Name]", "x", "SHM", "One", False),
    ("SHM - One [Your Name]", "x", "SHM", "One (Your Name)", True),
    ("Pegboard Nerds - Hero [Monstercat Release]", "Monstercat", "Pegboard Nerds", "Hero", True),
    ("Adam Beyer - Your Mind [Drumcode]", "x", "Adam Beyer", "Your Mind", True),
    # original con otro nombre
    ("Nena - 99 Luftballons (ORIGINAL)", "x", "Nena", "99 Luftballons", True),
    ("Soda Stereo - De Música Ligera", "x", "Soda Stereo", "De Música Ligera Remasterizado 2007", True),
    # vivos: el año no separa si hay otro dato, solo sí
    ("Queen - Bohemian Rhapsody (Live Aid 1985)", "x", "Queen", "Bohemian Rhapsody (Live Aid)", True),
    ("Queen - Bohemian Rhapsody (Live 1986)", "x", "Queen", "Bohemian Rhapsody (Live)", False),
    # artistas: dos alfabetos y apellido
    ("米津玄師 Kenshi Yonezu - Lemon", "x", "Kenshi Yonezu", "Lemon", True),
    ("Beethoven - Moonlight Sonata", "x", "Ludwig van Beethoven", "Moonlight Sonata", True),
    ("Beethoven - Moonlight Sonata", "x", "Ludwig van Beethoven", "Beethoven - Moonlight Sonata", True),
    ("Avicii - Levels", "x", "Avicii Tribute Band", "Levels", False),
    ("Snake - Song", "x", "DJ Snake", "Song", False),
    ("The - Song", "x", "The Weeknd", "Song", False),
    # números al principio del título que no son número de pista
    ("1.5 Degrees", "Artist", "Artist", "1.5 Degrees", True),
    ("22. Tema", "Artist", "Artist", "Tema", True),
    ("Molchat Doma - Sudno (dir. by @blood.doves)", "x", "Molchat Doma", "Sudno", True),
    ("NewJeans (뉴진스) 'Hype Boy' Official MV (Performance ver.1)", "HYBE LABELS", "NewJeans", "Hype Boy", True),
    ("BLACKPINK - '뚜두뚜두 (DDU-DU DDU-DU)' M/V", "BLACKPINK", "BLACKPINK", "DDU-DU DDU-DU", True),
])
def test_formatos_de_la_auditoria_2(pedido, uploader, deezer_artista, deezer_titulo, es):
    ident = ti.parse_entry(pedido, uploader)
    assert ti.is_same_track(ident, ti.parse_fields(deezer_artista, deezer_titulo)) is es, ident


def test_pipe_con_el_uploader_a_la_izquierda():
    # Tres segmentos: solo se lee "Artista | Tema" si la izquierda es el uploader (también
    # un canal VEVO pegado, "KanyeWestVEVO").
    for pedido, uploader in (("BICEP | GLUE | Lyrics", "BICEP"), ("KANYE WEST | STRONGER | Lyrics", "KanyeWestVEVO")):
        ident = ti.parse_entry(pedido, uploader)
        assert (ident.base_title, ident.version) == (pedido.split(" | ")[1].lower(), "original"), ident
    assert ti.parse_entry("QUEVEDO || Gran Via #52", "x").base_title == "gran via 52", "'#52' no es un hashtag"


def test_un_tributo_no_es_el_artista_ni_por_apellido():
    beethoven = ti.parse_fields("Beethoven", "Für Elise")
    assert ti.artists_match(beethoven, ti.parse_fields("Ludwig van Beethoven", "Für Elise"))
    assert not ti.artists_match(beethoven, ti.parse_fields("Tribute to Beethoven", "Für Elise"))


def test_titulo_con_numero_y_punto_no_pierde_el_numero():
    assert ti.parse_entry("1.5 Degrees", "Artist").base_title == "1 5 degrees"
    assert ti.parse_entry("7. rings", "Ariana Grande").base_title == "rings"          # número de pista
    assert ti.parse_entry("24.7", "Artist").base_title == "24 7"


def test_lectura_al_reves_solo_si_la_normal_no_encuentra(monkeypatch):
    seed, _ = resolver(monkeypatch, "b94")
    assert seed and seed["id"] == 533609232
    assert ti.parse_entry_swapped("God's Plan - Drake", "uploader").artists == {"drake"}
    assert ti.parse_entry_swapped("Pink Floyd - Money", "Pink Floyd") is not None
    assert ti.parse_entry_swapped("Money - Pink Floyd", "Pink Floyd") is None, "ya se leyó al revés"
    assert ti.parse_entry_swapped("Money", "Pink Floyd") is None, "sin separador no hay otra lectura"


@pytest.mark.parametrize("artista_deezer, titulo_deezer, dur_deezer, dur_upload, grado", [
    # por ISRC
    ("Kanye West", "Stronger", 312, 311, "isrc+duracion"),
    ("Kanye West", "Stronger", 312, None, "isrc"),               # mismo título y artista, sin duración
    ("Kanye West", "Stronger", 312, 250, "isrc"),                # la duración no confirma pero el artista sí
    ("Johnny Crash", "Stronger", 312, None, None),               # A1: otro artista y sin duración → no
    ("Johnny Crash", "Stronger", 312, 30.0, None),               # 30 s = preview de SoundCloud: no se sabe
    ("Johnny Crash", "Stronger", 312, 250, None),                # otro artista y la duración no cuadra
    ("Johnny Crash", "Stronger", 312, 305, "isrc+duracion"),     # otro artista, la duración confirma a ±5 %
    ("Kanye West", "Otra Cosa", 312, 311, "isrc+duracion"),      # otro título: solo con duración a ±max(3 s, 2 %)
    ("Kanye West", "Otra Cosa", 312, 300, None),
    ("Kanye West", "Otra Cosa", 312, None, None),
    ("Kanye West", "Otra Cosa", 312, float("inf"), None),
    ("Kanye West", "Otra Cosa", 312, float("nan"), None),
    ("Kanye West", "Otra Cosa", 312, -5, None),
    ("Kanye West", "Stronger (Instrumental)", 312, 312, None),   # otra versión, nunca
])
def test_evidencia_por_isrc(artista_deezer, titulo_deezer, dur_deezer, dur_upload, grado):
    assert ti.evidencia_misma_grabacion(ti.parse_entry("Kanye West - Stronger", "x"),
                                        ti.parse_fields(artista_deezer, titulo_deezer), dur_upload, dur_deezer,
                                        via="isrc") == grado


@pytest.mark.parametrize("pedido, dur_upload, artista_deezer, titulo_deezer, dur_deezer, grado", [
    ("Kanye West - Stronger", 315, "Kanye West", "Stronger", 312, "texto+duracion"),
    ("Kanye West - Stronger", None, "Kanye West", "Stronger", 312, "texto"),
    ("Kanye West - Stronger", 267, "Kanye West", "Stronger", 312, "texto"),          # video más corto: se acepta
    ("Kanye West - Stronger", 706, "Kanye West", "Stronger", 312, None),             # más del doble
    # A2: el primer artista del upload no está y la duración no cuadra
    ("J Balvin, Willy William - Mi Gente", 186, "Willy William", "Mi Gente", 137, None),
    ("J Balvin, Willy William - Mi Gente", 186, "J Balvin", "Mi Gente", 187, "texto+duracion"),
    ("J Balvin, Willy William - Mi Gente", 140, "Willy William", "Mi Gente", 137, "texto+duracion"),
    # alias (otro alfabeto, apellido): necesita que la duración cuadre
    ("米津玄師 Kenshi Yonezu - Lemon", 260, "Kenshi Yonezu", "Lemon", 256, "texto+duracion"),
    ("米津玄師 Kenshi Yonezu - Lemon", 400, "Kenshi Yonezu", "Lemon", 256, None),
    ("米津玄師 Kenshi Yonezu - Lemon", None, "Kenshi Yonezu", "Lemon", 256, None),
])
def test_evidencia_por_texto(pedido, dur_upload, artista_deezer, titulo_deezer, dur_deezer, grado):
    assert ti.evidencia_misma_grabacion(ti.parse_entry(pedido, "x"), ti.parse_fields(artista_deezer, titulo_deezer),
                                        dur_upload, dur_deezer) == grado


def test_evidencia_audio_exacto_mas_largo_es_otra_edicion():
    e, d = ti.parse_entry("Argy & Omnya - Aria", "x"), ti.parse_fields("Argy", "Aria")
    assert ti.evidencia_misma_grabacion(e, d, 314.8, 236) == "texto"                 # un video: puede ser la intro
    assert ti.evidencia_misma_grabacion(e, d, 314.8, 236, audio_exacto=True) is None  # SoundCloud: es otra edición
    assert ti.evidencia_misma_grabacion(e, d, 200, 236, audio_exacto=True) == "texto"  # más corto: recorte, se deja


@pytest.mark.parametrize("dur_upload, titulos, esperado", [
    (264, [("Born Slippy (Nuxx)", 454), ("Born Slippy (Nuxx) (Radio Edit)", 264)], "Born Slippy (Nuxx) (Radio Edit)"),
    (268, [("Born Slippy (Nuxx)", 454), ("Born Slippy (Nuxx) (Radio Edit)", 264)], "Born Slippy (Nuxx) (Radio Edit)"),
    (272, [("Born Slippy (Nuxx)", 454), ("Born Slippy (Nuxx) (Radio Edit)", 264)], None),      # 8 s > max(3 s, 2 %)
    (199, [("Levels (Original Version)", 339), ("Levels (Radio Edit)", 199)], "Levels (Radio Edit)"),
    (199, [("Levels (Radio Edit)", 199), ("Levels (Club Mix)", 200)], None),                  # dos ediciones: ambiguo
    (199, [("Levels (Radio Edit)", 199), ("Levels (Radio Edit)", 198)], "Levels (Radio Edit)"),   # dos lanzamientos
    (199, [("Levels (Live)", 199)], None),                                                     # un vivo, nunca
    (199, [("Levels (Skrillex Remix)", 199)], None),                                          # un remix, nunca
    (199, [("Levels (Instrumental Radio Edit)", 199)], None),                                 # instrumental, nunca
])
def test_radio_edit_por_duracion(dur_upload, titulos, esperado):
    filas = [{"id": k, "title": t, "artist": {"id": 1, "name": "Avicii" if "Levels" in t else "Underworld"},
              "duration": d} for k, (t, d) in enumerate(titulos)]
    pedido = "Avicii - Levels" if "Levels" in titulos[0][0] else "Underworld - Born Slippy (Nuxx)"
    track, grado = ti.pick_track_evidencia(ti.parse_entry(pedido, "x"), filas, dur_upload)
    assert (track or {}).get("title") == esperado
    assert grado == ("texto+duracion" if esperado else None)
    # con versión en el título del upload la regla no aplica
    assert ti.pick_track(ti.parse_entry(pedido + " (Extended Mix)", "x"), filas, dur_upload) is None


def test_invitados_que_el_upload_no_nombra():
    # Filas GRABADAS de «Stromae - Alors on danse»: sin duración, la versión "Featuring Erik
    # Hassle" no es el tema pedido si hay una edición sin invitados (la Radio Edit).
    filas = [fila_deezer(t) for q in A["b201"]["deezer"] for t in A["b201"]["deezer"][q]
             if t["id"] in (7046192, 6297555)]
    filas = list({t["id"]: t for t in filas}.values())
    assert {t["id"] for t in filas} == {7046192, 6297555}
    assert ti.pick_track(ti.parse_entry("Stromae - Alors on danse", "x"), filas, None) is None
    solo = [t for t in filas if t["id"] == 7046192]
    assert ti.pick_track(ti.parse_entry("Stromae - Alors on danse", "x"), solo, None)["id"] == 7046192, \
        "sin otra edición, el crédito se acepta (Netsky - Rio)"


def test_feat_con_guion_y_numero_de_pista():
    u = ti.parse_entry("Rihanna - Umbrella ft. JAY-Z", "x")
    assert (u.base_title, set(u.feat)) == ("umbrella", {"jay z"})
    assert ti.parse_entry("Artist - Song ft. T-Pain", "x").base_title == "song"
    assert ti.parse_entry("Artist - Song ft. T-Pain - Official", "x").feat == frozenset({"t pain"})
    assert ti.parse_entry("Iron Maiden - 2 Minutes To Midnight (1998 Remastered Version) #02", "x").base_title == \
        "2 minutes to midnight"
    assert ti.parse_entry("QUEVEDO || Gran Via #52", "x").base_title == "gran via 52"


def test_grado_en_el_detalle(monkeypatch):
    # Kobosil sube "Eiskalt (Short Mix)" (preview de 30 s) con el ISRC del de Kuko: coincide el
    # coautor (contributors) → "isrc"; sin ISRC no se puede.
    e = E[23]
    monkeypatch.setattr(similares, "_get", DeezerGrabado(e))
    seed, motivo, grado = similares.resolver_semilla(e["titulo"], e["artista"], e["isrc"], e["duracion"], e["fuente"])
    assert (seed["id"], grado) == (2988969511, "isrc")
    a = A["a82"]
    monkeypatch.setattr(similares, "_get", DeezerGrabado(a))
    seed, _, grado = similares.resolver_semilla(a["titulo"], a["artista"], None, a["duracion"], a["fuente"])
    assert (seed["id"], grado) == (14598347, "texto+duracion")


@pytest.mark.parametrize("dur", [None, 900.0])
def test_isrc_con_titulo_distinto_no_se_acepta(monkeypatch, dur):
    # El ISRC de un upload "Otra Cosa" que en Deezer es "Stronger" de 312 s: ni sin duración ni
    # con una duración que no cuadra.
    fila = {"id": 1178682, "title": "Stronger", "artist": {"id": 230, "name": "Kanye West"}, "duration": 312}
    monkeypatch.setattr(similares, "_get", lambda url: fila if "/track/isrc:" in url else {"data": []})
    assert similares.resolver_seed_detalle("Otra Cosa", "Kanye West", "USUM70741299", dur) == \
        (None, similares.SEED_NOT_FOUND)


@pytest.mark.parametrize("dur", [float("inf"), float("nan"), 0.0, -3.0])
def test_duracion_invalida_es_no_se_sabe(monkeypatch, dur):
    # "Levels" de 199 s sin duración utilizable: se elige el primero, no se desempata ni se veta.
    filas = [{"id": 1, "title": "Levels", "artist": {"id": 9, "name": "Avicii"}, "duration": 339},
             {"id": 2, "title": "Levels", "artist": {"id": 9, "name": "Avicii"}, "duration": 199}]
    assert ti.pick_track(ti.parse_entry("Avicii - Levels", "x"), filas, dur)["id"] == 1
    assert ti.duration_or_none(dur) is None


@pytest.mark.parametrize("dur, fuente, esperado", [
    (30.0, "soundcloud", None),      # el preview Go+: no se sabe
    (30.0, "SoundCloud", None),
    (30.0, None, None),              # fuente desconocida: se lo trata como el preview
    (29.5, "soundcloud", None),      # con el redondeo
    (30.0, "youtube", 30.0),         # 30 s de YouTube es un audio de 30 s
    (30.0, "hitplayer", 30.0),
    (16.0, "youtube", 16.0),         # f40: un ringtone de 16 s ES de 16 s (antes, ≤ 31 s = no se sabe)
    (16.0, None, 16.0),
    (16.0, "soundcloud", 16.0),      # en SoundCloud también: solo el 30 es el preview
    (31.5, "soundcloud", 31.5),
])
def test_duracion_solo_el_30_de_soundcloud_es_no_se_sabe(dur, fuente, esperado):
    assert ti.duration_or_none(dur, fuente) == esperado


def test_un_fragmento_de_16_s_no_es_el_tema():
    # Antes 16 s era "no se sabe" y el candidato pasaba por texto; ahora es una duración
    # conocida y el tema de 306 s dura más del doble.
    e = ti.parse_entry("SPÆCE - B WITH U", "SPÆCE")
    assert ti.evidencia_misma_grabacion(e, e, 306, 16) is None
    assert ti.evidencia_misma_grabacion(e, e, 306, 300) == "texto+duracion"


@pytest.mark.parametrize("w, c, cuadra", [(244.8, 268.0, True), (244.8, 270.0, False), (60.0, 70.0, True),
                                          (60.0, 70.5, False), (244.8, 221.0, True), (244.8, 220.0, False)])
def test_duraciones_cuadran_es_10_s_o_10_por_ciento(w, c, cuadra):
    # ±max(10 s, 10 %) de la primera: 244,8 → ±24,48 s; 60 → ±10 s.
    assert ti.duraciones_cuadran(w, c) is cuadra


def test_upload_de_mas_del_doble_no_es_el_tema():
    filas = [{"id": 1, "title": "HUMBLE.", "artist": {"id": 9, "name": "Kendrick Lamar"}, "duration": 177}]
    e = ti.parse_entry("Kendrick Lamar - Humble", "x")
    assert ti.pick_track(e, filas, 706)is None
    assert ti.pick_track(e, filas, 184)["id"] == 1


def test_el_isrc_se_verifica_contra_el_upload(monkeypatch):
    # SoundCloud «Stronger» con el ISRC de «Stronger (instrumental)»: el ISRC no se toma.
    a = A["a67"]
    assert a["deezer"][f"isrc:{a['isrc']}"][0]["title"] == "Stronger (instrumental)"
    seed, _ = resolver(monkeypatch, "a67")
    assert seed and seed["id"] == 1178682, f"aceptó {nombre(seed)}"


def test_sin_artista_no_se_pregunta_a_deezer(monkeypatch):
    pedidos = []
    monkeypatch.setattr(similares, "_get", lambda url: pedidos.append(url) or {"data": []})
    assert similares.resolver_seed_detalle("Halo", ".", None) == (None, similares.NOT_SEARCHABLE)
    assert pedidos == []
    detalle = similares.sin_semilla(similares.NOT_SEARCHABLE)["detalle"]
    assert "no busqué" in detalle and "No está en Deezer" not in detalle


def test_cada_motivo_tiene_su_detalle():
    detalles = {m: similares.sin_semilla(m)["detalle"] for m in
                (similares.SEED_NOT_FOUND, similares.DEEZER_UNAVAILABLE, similares.NOT_SEARCHABLE)}
    assert len(set(detalles.values())) == 3
    assert detalles[similares.DEEZER_UNAVAILABLE].startswith("No pude consultar Deezer")
    assert detalles[similares.SEED_NOT_FOUND].startswith("No está en Deezer")


# --- ISRC de SoundCloud ----------------------------------------------------------------------

class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self.ok, self._body = status, status < 400, body

    def json(self):
        return self._body


def _fake_sc(monkeypatch, respuestas, cid="CID_SECRETO"):
    import requests
    import yt_dlp
    llamadas, renovaciones = [], []

    class Cache:
        def load(self, *a):
            return cid

    class YDL:
        def __init__(self, *a, **k):
            self.cache = Cache()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, **k):
            renovaciones.append(url)
            return {}

    def get(url, params=None, headers=None, timeout=None):
        llamadas.append((url, params))
        r = respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", YDL)
    return llamadas, renovaciones


def test_isrc_de_soundcloud_normaliza(monkeypatch):
    llamadas, renov = _fake_sc(monkeypatch, [_Resp(200, {"publisher_metadata": {"isrc": "us-at2 2005111"}})])
    assert ti.fetch_soundcloud_isrc("1234567") == "USAT22005111"
    assert [u for u, _ in llamadas] == ["https://api-v2.soundcloud.com/tracks/1234567"] and renov == []


def test_isrc_con_client_id_vencido_renueva_y_reintenta(monkeypatch):
    llamadas, renov = _fake_sc(monkeypatch, [_Resp(401), _Resp(200, {"publisher_metadata": {"isrc": "USAT22005111"}})])
    assert ti.fetch_soundcloud_isrc("1234567") == "USAT22005111"
    assert len(llamadas) == 2 and renov == ["https://api.soundcloud.com/tracks/1234567"]


def test_isrc_con_timeout_da_none_sin_filtrar_el_client_id(monkeypatch, caplog):
    import requests
    _fake_sc(monkeypatch, [requests.exceptions.ConnectTimeout(
        "HTTPSConnectionPool: /tracks/1234567?client_id=CID_SECRETO (timeout)")])
    caplog.set_level("INFO", logger="track_identity")
    assert ti.fetch_soundcloud_isrc("1234567") is None
    assert "CID_SECRETO" not in caplog.text and "ConnectTimeout" in caplog.text


@pytest.mark.parametrize("tid", ["", None, "abc", "123abc", "-1", "²", "١٢٣", "𝟙𝟚", "1" * 21])
def test_isrc_con_id_invalido_no_sale_a_la_red(monkeypatch, tid):
    llamadas, renov = _fake_sc(monkeypatch, [_Resp(200, {"publisher_metadata": {"isrc": "USAT22005111"}})])
    assert ti.fetch_soundcloud_isrc(tid) is None
    assert llamadas == [] and renov == []


# --- las 40 entradas ----------------------------------------------------------------------

def clasificar(e: dict, seed: dict | None) -> str:
    v = e["verdad"]
    if v["estado"] == "unknown":
        return "unknown"
    if v["estado"] == "none":
        return "ok" if seed is None else "sustitucion"
    if seed is None:
        return "rechazo_mal"
    if seed["id"] in v["ids"] or seed["id"] in v["otras_versiones"]:
        return "ok"
    return "sustitucion"


def test_las_40_entradas_cero_sustituciones(monkeypatch):
    antes = {"ok": 0, "rechazo_mal": 0, "sustitucion": 0, "unknown": 0}
    ahora = dict(antes)
    malos = []
    for n, e in E.items():
        antes[clasificar(e, None if not e["antes"] else {"id": e["antes"]["id"]})] += 1
        seed, _ = resolver(monkeypatch, n)
        c = clasificar(e, seed)
        ahora[c] += 1
        if c in ("sustitucion", "rechazo_mal"):
            malos.append(f"[{n}] {c}: {e['artista']} | {e['titulo']} -> {nombre(seed)}")
    assert antes["sustitucion"] == 10, f"el fixture tiene que reproducir las 10 sustituciones de antes: {antes}"
    assert not [m for m in malos if "sustitucion" in m], "\n".join(malos)
    # Costo conocido: [33] es la subida a SoundCloud de 520 s de un "Inferno" que en Deezer dura
    # 416 s; con audio exacto un upload más largo que no cuadra es otra edición (la misma regla que
    # rechaza la Extended de "Aria"). El diseño ya lo tenía como "misma obra; ¿misma mezcla? UNKNOWN".
    assert [m.split()[0] for m in malos] == ["[33]"], "\n".join(malos)
    assert ahora == {"ok": 38, "rechazo_mal": 1, "sustitucion": 0, "unknown": 1}


# --- meta de las filas ----------------------------------------------------------------------

def test_meta_de_solo_pide_el_tema_verificado(monkeypatch):
    similares._META_CACHE.clear()
    dz = DeezerGrabado(E[4])
    monkeypatch.setattr(similares, "_get", dz)
    monkeypatch.setattr(similares, "_bpm_de_preview", lambda url: None)
    similares.meta_de("The Aftermath", "Dyen - Topic", True)
    assert dz.tracks_pedidos and set(dz.tracks_pedidos) <= {"700407652", "1637952262"}, \
        f"el BPM se pidió a otro track: {dz.tracks_pedidos}"


def test_meta_de_un_edit_no_toma_los_datos_del_original(monkeypatch):
    # El primer resultado de «BAD GYAL da me» es el ORIGINAL de Bad Gyal: su BPM y su género
    # no son los del edit hard bounce. Sin tema demostrado, BPM y género quedan vacíos.
    similares._META_CACHE.clear()
    dz = DeezerGrabado(E[24])
    monkeypatch.setattr(similares, "_get", dz)
    assert similares.meta_de(E[24]["titulo"], E[24]["artista"], False) == {"bpm": None, "genero": None}
    assert dz.tracks_pedidos == [], f"pidió datos de otro track: {dz.tracks_pedidos}"


# --- opciones por plataforma de cada parecido -------------------------------------------------

# Opciones REALES que devolvió /api/parecidas_lista (auditoría, 2026-09-29) para dos parecidos.
MIX = {
    "Ice Spice - Thootie": [
        {"titulo": "Ice Spice, Tokischa - Thootie", "artista": "Ice Spice", "fuente": "youtube", "duracion": 153},
        {"titulo": "Thootie", "artista": "Ice Spice", "fuente": "soundcloud", "duracion": 30.0},
        {"titulo": "Thottie Frutti", "artista": "ICE SPICE", "fuente": "ligaudio", "duracion": 136},
    ],
    "BIA - TWIN": [
        {"titulo": "BIA - TWIN (Official Audio)", "artista": "BIA", "fuente": "youtube", "duracion": 134},
        {"titulo": "ruth b - dandelions ( slowed + reverb )", "artista": "N.B.A", "fuente": "soundcloud", "duracion": 264.8},
        {"titulo": "FALLBACK", "artista": "BIA", "fuente": "ligaudio", "duracion": 151},
    ],
}


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


def test_opciones_de_descarta_las_de_otro_tema(server, monkeypatch):
    monkeypatch.setattr(server, "_buscar_mix", lambda q, limite: [dict(c) for c in MIX[q]])
    ice = server._opciones_de("Ice Spice - Thootie", "wav", 3, ti.parse_fields("Ice Spice", "Thootie"))
    assert [(o["fuente"], o["titulo"]) for o in ice] == [("youtube", "Ice Spice, Tokischa - Thootie"), ("soundcloud", "Thootie")]
    bia = server._opciones_de("BIA - TWIN", "wav", 3, ti.parse_fields("BIA", "TWIN"))
    assert [(o["fuente"], o["titulo"]) for o in bia] == [("youtube", "BIA - TWIN (Official Audio)")]
    # Modo lista (sin identidad): sigue igual que antes, una opción por plataforma.
    assert [o["fuente"] for o in server._opciones_de("BIA - TWIN", "wav")] == ["youtube", "soundcloud", "ligaudio"]


def test_buscar_lista_le_pasa_la_identidad_a_cada_linea(server, monkeypatch):
    # El camino real de parecidas: _buscar_lista (en paralelo) con una identidad por línea.
    monkeypatch.setattr(server, "_buscar_mix", lambda q, limite: [dict(c) for c in MIX[q]])
    lineas = ["Ice Spice - Thootie", "BIA - TWIN"]
    idents = [ti.parse_fields("Ice Spice", "Thootie"), ti.parse_fields("BIA", "TWIN")]
    res = server._buscar_lista(lineas, "wav", idents)
    assert [[(o["fuente"], o["titulo"]) for o in ops] for ops in res] == [
        [("youtube", "Ice Spice, Tokischa - Thootie"), ("soundcloud", "Thootie")],
        [("youtube", "BIA - TWIN (Official Audio)")]]
    assert [len(ops) for ops in server._buscar_lista(lineas, "wav")] == [3, 3], "modo lista: sin filtro"


def test_parecidas_lista_sin_semilla_dice_similitud_no_disponible(server, monkeypatch):
    from fastapi.testclient import TestClient
    e = E[0]
    monkeypatch.setattr(similares, "_get", DeezerGrabado(e))
    busco = []
    monkeypatch.setattr(server, "_buscar_lista", lambda *a, **k: busco.append(a) or [])
    r = TestClient(server.app).get("/api/parecidas_lista", params={
        "titulo": e["titulo"], "artista": e["artista"], "fuente": "youtube", "fuente_id": "x"})
    esperado = json.loads((FIX / "parecidas_no_disponible.json").read_text(encoding="utf-8"))
    assert r.status_code == 200
    assert r.json() == esperado, "el front (y el E2E) dependen de esta forma exacta"
    assert esperado["mensaje"] == "Similitud no disponible para este track" and "seed" not in r.json()
    assert busco == [], "sin semilla no se busca ninguna opción de plataforma"


@pytest.mark.parametrize("dur, llega", [("inf", None), ("nan", None), ("-1", None), ("0", None), ("187", 187.0)])
def test_parecidas_lista_limpia_la_duracion(server, monkeypatch, dur, llega):
    from fastapi.testclient import TestClient
    recibido = []
    monkeypatch.setattr(similares, "construir_playlist",
                        lambda *a, **k: recibido.append((a, k)) or similares.sin_semilla(similares.SEED_NOT_FOUND))
    TestClient(server.app).get("/api/parecidas_lista", params={"titulo": "From The Top", "artista": "IMMINENT - Topic",
                                                               "duracion": dur, "fuente": "SoundCloud"})
    assert recibido and recibido[0][0][-1] == llega, recibido
    assert recibido[0][1] == {"fuente": "soundcloud"}, "la fuente llega a la resolución (audio exacto)"


def test_parecidas_lista_de_soundcloud_usa_el_isrc(server, monkeypatch):
    # Kobosil «Eiskalt (Short Mix)» de SoundCloud: por texto no se puede; por ISRC sí.
    from fastapi.testclient import TestClient
    e = E[23]
    monkeypatch.setattr(similares, "_get", DeezerGrabado(e))
    pedidos = []
    monkeypatch.setattr(server.track_identity, "fetch_soundcloud_isrc", lambda tid: pedidos.append(tid) or e["isrc"])
    monkeypatch.setattr(server, "_buscar_lista", lambda lineas, formato, identidades=None: [[] for _ in lineas])
    r = TestClient(server.app).get("/api/parecidas_lista", params={
        "titulo": e["titulo"], "artista": e["artista"], "fuente": "soundcloud", "fuente_id": "1234567"})
    assert pedidos == ["1234567"], "el id de SoundCloud tiene que llegar hasta el pedido del ISRC"
    assert r.json()["exito"] is True and (r.json()["seed"]["titulo"], r.json()["seed"]["artista"]) == ("Eiskalt (Short Mix)", "Kuko")
    assert r.json()["seed"]["evidencia"] == "isrc", "el grado de evidencia va en el detalle de la semilla"


# --- semilla sí, parecidas no ---------------------------------------------------------------
# El caso real (2026-09-29): parecidas de un tema de Electro de un sello chico. Deezer encontró
# la semilla pero dio un solo artista relacionado y ningún tema para comparar; la respuesta era
# `exito: true` con la lista vacía y la pantalla mostraba el cartel del MODO LISTA ("revisá que
# haya un tema por línea"), un pedido que el DJ nunca hizo.

class _ConRelacionados(DeezerGrabado):
    """El Deezer grabado de una entrada más `n` artistas relacionados. Los relacionados no están
    grabados (contestan vacío), y sus álbumes y tops tampoco: son artistas sin nada que comparar."""

    def __init__(self, entrada: dict, n: int):
        super().__init__(entrada)
        self.n = n

    def __call__(self, url: str) -> dict:
        if "/related" in url:
            return {"data": [{"id": 900_000 + i, "name": f"Relacionado {i}"} for i in range(self.n)]}
        return super().__call__(url)


def _parecidas_de_eiskalt(server, monkeypatch, deezer):
    from fastapi.testclient import TestClient
    e = E[23]
    monkeypatch.setattr(similares, "_get", deezer(e))
    monkeypatch.setattr(server.track_identity, "fetch_soundcloud_isrc", lambda tid: e["isrc"])
    monkeypatch.setattr(server, "_buscar_lista", lambda lineas, formato, identidades=None: [[] for _ in lineas])
    return TestClient(server.app).get("/api/parecidas_lista", params={
        "titulo": e["titulo"], "artista": e["artista"], "fuente": "soundcloud", "fuente_id": "1234567"}).json()


def test_parecidas_con_semilla_y_sin_candidatos_dice_por_que(server, monkeypatch):
    d = _parecidas_de_eiskalt(server, monkeypatch, lambda e: _ConRelacionados(e, 2))
    esperado = json.loads((FIX / "parecidas_sin_candidatos.json").read_text(encoding="utf-8"))
    assert d == esperado, "el E2E usa este archivo como respuesta de la API: tienen que coincidir"
    assert (d["exito"], d["total"], d["grupos"], d["seed"]["titulo"]) == (True, 0, [], "Eiskalt (Short Mix)")
    assert (d["motivo"], d["mensaje"], d["detalle"]) == (
        "sin_candidatos", "No encontré temas parecidos a «Eiskalt (Short Mix)»",
        "Deezer devolvió 2 artistas relacionados con Kuko, pero ningún tema con preview para "
        "comparar (ni suyos ni de ellos).")


def test_parecidas_sin_relacionados_lo_dice_distinto(server, monkeypatch):
    """"No devolvió", no "no tiene": `_get` contesta vacío igual cuando Deezer no respondió
    (auditoría de f35), así que el texto describe lo que llegó."""
    d = _parecidas_de_eiskalt(server, monkeypatch, DeezerGrabado)
    assert (d["motivo"], d["detalle"]) == (
        "sin_candidatos", "Deezer no devolvió artistas relacionados con Kuko ni otros temas suyos "
                          "con preview para comparar.")


def test_parecidas_que_no_estan_en_ninguna_plataforma_lo_dicen(server, monkeypatch):
    from fastapi.testclient import TestClient
    canciones = [{"titulo": f"Tema {i}", "artista": "Otro"} for i in range(7)]
    monkeypatch.setattr(similares, "construir_playlist", lambda *a, **k: {
        "exito": True, "seed": {"titulo": "Semilla", "artista": "A"}, "canciones": canciones,
        "relacionados": 3})
    monkeypatch.setattr(server, "_buscar_lista", lambda lineas, formato, identidades=None: [[] for _ in lineas])
    d = TestClient(server.app).get("/api/parecidas_lista", params={"titulo": "Semilla", "artista": "A"}).json()
    assert (d["exito"], d["total"], d["encontradas"], d["grupos"]) == (True, 7, 0, [])
    assert (d["motivo"], d["mensaje"], d["detalle"]) == (
        "sin_plataformas",
        "Encontré 7 temas parecidos a «Semilla», pero no encontré ninguno en YouTube, SoundCloud ni MP3",
        "No aparecieron (o no eran el mismo tema): Otro - Tema 0, Otro - Tema 1, Otro - Tema 2, "
        "Otro - Tema 3, Otro - Tema 4 y 2 más.")


def test_una_lista_con_resultados_no_trae_motivo(server, monkeypatch):
    """El motivo es solo para la lista vacía: con UN parecido encontrado la respuesta es la de
    siempre (el front arma la lista y no muestra ningún cartel)."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(similares, "construir_playlist", lambda *a, **k: {
        "exito": True, "seed": {"titulo": "Semilla", "artista": "A"},
        "canciones": [{"titulo": "Uno", "artista": "B"}, {"titulo": "Dos", "artista": "B"}], "relacionados": 1})
    opcion = {"fuente": "youtube", "titulo": "B - Uno"}
    monkeypatch.setattr(server, "_buscar_lista",
                        lambda lineas, formato, identidades=None: [[opcion], []])
    d = TestClient(server.app).get("/api/parecidas_lista", params={"titulo": "Semilla", "artista": "A"}).json()
    assert (d["encontradas"], d["no_encontradas"]) == (1, ["B - Dos"])
    assert not {"motivo", "mensaje", "detalle"} & d.keys(), d
