"""La grilla estimada (`motor.bandas.estimar_grilla`) contra las grillas de Rekordbox, con audio
REAL de la biblioteca del dueño. Marcado `slow`: tarda ~1 min y se saltea solo si no están el
XML de Rekordbox o los archivos (en CI, en otra PC, en un worktree sin el XML).

El XML se busca en `DJRADIO_REKORDBOX_XML` o en `Rekordbox.xml` de la raíz del repo. De ahí
salen TODO lo esperado: el archivo, el BPM (`TEMPO Bpm`, que hace de BPM de la base) y la fase
(`TEMPO Inizio`). Si los audios se movieron desde que se exportó el XML, `DJRADIO_AUDIO_RAICES`
(carpetas separadas por `os.pathsep`) los reubica con `ground_truth.resolver` (un homónimo
ambiguo se saltea, nunca se elige a la suerte). Nada inventado (spec §5). Solo WAV: en MP3 el decodificador de
Rekordbox y el de libsndfile quedan corridos 1 o 2 tramas (26 ms c/u) según el archivo, y la
fase de Rekordbox no es la de este audio (informe de f52-r2).

Los temas son de la calibración y de la validación de f52-r2:
- CON grilla: el estimador tiene que declararla y caer a ±25 ms del Inizio en todo el tema.
- SIN grilla: temas donde el máximo de ataques cae en el contratiempo (o el motor mide a mitad
  de tempo): declarar ahí sería dibujar la grilla medio beat corrida. Tienen que dar None.
Los 4 temas lossless donde hoy el estimador declara mal (Haus Headz, WORK IT OUT, DIRTY GAME,
TEACH ME HOW TO BOUNCE) NO están acá: son fallas conocidas y documentadas, no regresiones.
"""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

import pytest  # noqa: E402

import motor.bandas as mb  # noqa: E402
from ground_truth.resolver import construir_indice, resolver  # noqa: E402

pytestmark = pytest.mark.slow
TOL_S = 0.025

CON_GRILLA = [
    "Alan Fitzpatrick - Replicant (Extended Mix).wav",
    "1. Discip - The Way I Like It (Original Mix).wav",
    "03 - Skitz.wav",
    "01 - Drum Dealer.wav",
    "CVNSUMED - atöm.04 - 01 Fashion Victim.wav",
    "10. DJ Dextro - Element One (Original Mix).wav",
    "D GIO (MUECKE MASTER2).wav",
    "SYNEK, GIØ - Blaka (Original Mix).wav",
    "Braydon Terzo - DTD [DOTHADANCE].wav",
]
SIN_GRILLA = [
    "ASTRO-H035.wav",                                              # ataques a contratiempo
    "01. Angerfist & CARV - Shot To The Brain (Extended Mix).wav",  # ídem
]


def _xml() -> Path | None:
    p = Path(os.environ.get("DJRADIO_REKORDBOX_XML") or Path(RAIZ) / "Rekordbox.xml")
    return p if p.is_file() else None


@pytest.fixture(scope="module")
def rekordbox():
    """{nombre de archivo (normcase): (Location, Inizio, Bpm)} de los tracks con UNA grilla, y
    cómo reubicar los archivos."""
    xml = _xml()
    if xml is None:
        pytest.skip("no está el XML de Rekordbox (DJRADIO_REKORDBOX_XML o Rekordbox.xml)")
    out = {}
    for tr in ET.parse(xml).getroot().iter("TRACK"):
        tempos = [(float(t.get("Inizio")), float(t.get("Bpm"))) for t in tr.findall("TEMPO")]
        if not tempos or len({b for _, b in tempos}) != 1:
            continue
        ruta = os.path.normpath(unquote((tr.get("Location") or "").replace("file://localhost/", "")))
        out[os.path.normcase(Path(ruta).name)] = (ruta, *tempos[0])
    raices = [r for r in os.environ.get("DJRADIO_AUDIO_RAICES", "").split(os.pathsep) if r]
    return out, raices, construir_indice(raices)


def _caso(rekordbox, nombre):
    datos, raices, indice = rekordbox
    dato = datos.get(os.path.normcase(nombre))
    if dato is None:
        pytest.skip(f"{nombre} no está en el XML con una sola grilla")
    location, inizio, bpm = dato
    r = resolver(location, raices, indice)
    if not r:
        pytest.skip(f"no está el audio de {nombre} ({r.estado}; ver DJRADIO_AUDIO_RAICES)")
    ruta = r.ruta
    b = mb.compute_bandas(ruta)                      # solo lectura: el original no se toca
    return b, mb.estimar_grilla(b.valores(), bpm), inizio, bpm


def _circular(a, p):
    return (a + p / 2) % p - p / 2


@pytest.mark.parametrize("nombre", CON_GRILLA)
def test_la_grilla_cae_en_la_de_rekordbox(rekordbox, nombre):
    b, g, inizio, bpm = _caso(rekordbox, nombre)
    assert g["primer_beat_s"] is not None, f"no declaró grilla: {g}"
    p_rb = 60.0 / bpm
    # A lo largo de TODO el tema (10 %, 50 % y 90 %), no solo al principio.
    for frac in (0.1, 0.5, 0.9):
        t = frac * b.duration_s
        linea = g["primer_beat_s"] + round((t - g["primer_beat_s"]) / g["periodo_s"]) * g["periodo_s"]
        err = _circular(linea - inizio, p_rb)
        assert abs(err) <= TOL_S, \
            f"a los {t:.0f} s la línea está {err * 1000:+.1f} ms de la de Rekordbox"
    assert abs(g["bpm_afinado"] - bpm) <= 0.02, g


@pytest.mark.parametrize("nombre", SIN_GRILLA)
def test_a_contratiempo_no_hay_grilla(rekordbox, nombre):
    _, g, _, _ = _caso(rekordbox, nombre)
    assert g["primer_beat_s"] is None and g["motivo"], f"declaró una grilla dudosa: {g}"
