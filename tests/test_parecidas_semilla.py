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


def fila_deezer(t: dict) -> dict:
    """Una fila grabada con la forma de la API de Deezer (artist como objeto)."""
    return {"id": t["id"], "title": t["title"], "artist": {"id": t["artist_id"], "name": t["artist"]},
            "duration": t["duration"], "album": {"id": None}}


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


def resolver(monkeypatch, n: int, con_isrc: bool = True):
    e = E[n]
    dz = DeezerGrabado(e)
    monkeypatch.setattr(similares, "_get", dz)
    seed, motivo = similares.resolver_seed_detalle(e["titulo"], e["artista"], e["isrc"] if con_isrc else None)
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
    assert malos == [], "\n".join(malos)
    assert ahora == {"ok": 39, "rechazo_mal": 0, "sustitucion": 0, "unknown": 1}


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
