"""Tests de la CLI (`python -m motor`): lo que cada comando DICE y lo que deja en la base.

El audio son clicks sintéticos escritos como WAV en carpetas temporales: el BPM y la
tonalidad de cada archivo los fija el generador y viajan en el nombre del archivo, así el
valor esperado no sale del código bajo prueba (spec §5).

La biblioteca compartida se analiza UNA vez por módulo (fixture `biblioteca`); los tests
que la leen trabajan sobre una copia de la base cuando la podrían modificar.
"""
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import soundfile as sf  # noqa: E402

from motor.cli import main  # noqa: E402
from motor.radio import bpm_delta_pct, key_relation  # noqa: E402
from motor.sintetico import click_track  # noqa: E402
from motor.store import Store  # noqa: E402

LICENCIA = "CC0-1.0"
ORIGEN = "motor/sintetico.py"

# (bpm, nota, modo) del generador. La key clásica esperada se escribe a mano: es teoría,
# no una salida del motor.
CATALOGO = [
    (120.0, "A", "min"), (124.0, "A", "min"), (126.0, "C", "maj"), (128.0, "G", "maj"),
    (130.0, "E", "min"), (134.0, "B", "min"), (170.0, "F#", "min"),
]


def _nombre(bpm, nota, modo):
    return f"click_{bpm:.0f}_{nota}{modo}.wav"


def _clasica(nota, modo):
    return nota + ("m" if modo == "min" else "")


def _escribir(carpeta: Path, bpm, nota, modo, dur=10.0, seed=0, ganancia=1.0) -> Path:
    y, sr = click_track(bpm, dur=dur, nota=nota, modo=modo, seed=seed)
    ruta = carpeta / _nombre(bpm, nota, modo)
    sf.write(str(ruta), (y * ganancia).astype(np.float32), sr, subtype="FLOAT")
    return ruta


def _correr(capsys, *argv) -> tuple[int, str, str]:
    codigo = main([str(a) for a in argv])
    salida = capsys.readouterr()
    return codigo, salida.out, salida.err


def _scan(db, carpeta, *extra):
    return ["--db", db, "scan", carpeta, "--licencia", LICENCIA, "--origen", ORIGEN, *extra]


@pytest.fixture(scope="module")
def biblioteca(tmp_path_factory):
    """Carpeta con el catálogo + base ya escaneada. Devuelve (carpeta, db, spec por nombre)."""
    raiz = tmp_path_factory.mktemp("biblio")
    carpeta = raiz / "crate"
    carpeta.mkdir()
    spec = {}
    for i, (bpm, nota, modo) in enumerate(CATALOGO):
        # Ganancia distinta por track: la energía no puede empatar en toda la biblioteca.
        ruta = _escribir(carpeta, bpm, nota, modo, seed=i, ganancia=0.3 + 0.1 * ((i * 3) % 7))
        spec[ruta.name] = (bpm, nota, modo)
    db = raiz / "biblio.sqlite"
    assert main([str(a) for a in _scan(db, carpeta)]) == 0, "el scan de la biblioteca falló"
    return carpeta.resolve(), db, spec


# --- scan ------------------------------------------------------------------------------


def _analyzed_at(db) -> dict[str, str]:
    con = sqlite3.connect(str(db))
    filas = dict(con.execute("SELECT path, analyzed_at FROM tracks").fetchall())
    con.close()
    return {Path(p).name: v for p, v in filas.items()}


RESUMEN = re.compile(r"nuevos (\d+) · actualizados (\d+) · sin cambios (\d+) · "
                     r"borrados (\d+) · fallidos (\d+)")


def _resumen(out: str) -> tuple[int, ...]:
    m = RESUMEN.search(out)
    assert m, f"el scan no imprimió el resumen:\n{out}"
    return tuple(int(x) for x in m.groups())


def test_scan_punta_a_punta(tmp_path, capsys):
    """Primera corrida analiza todo; la segunda nada; un archivo cambiado se reanaliza
    solo; uno borrado sale de la base. Se verifica la DECISIÓN en la base (analyzed_at y el
    BPM guardado), no solo lo que dice el resumen."""
    carpeta = tmp_path / "crate"
    carpeta.mkdir()
    rutas = [_escribir(carpeta, bpm, n, m, dur=6.0, seed=i)
             for i, (bpm, n, m) in enumerate(CATALOGO[:3])]
    db = tmp_path / "db.sqlite"

    codigo, out, _ = _correr(capsys, *_scan(db, carpeta))
    assert codigo == 0, out
    assert _resumen(out) == (3, 0, 0, 0, 0), f"primera corrida:\n{out}"
    with Store(db) as store:
        assert sorted(p.name for p in store.paths()) == sorted(r.name for r in rutas)
        for ruta, (bpm, _, _) in zip(rutas, CATALOGO[:3], strict=True):
            t = store.get(ruta.resolve())
            assert abs(t.bpm - bpm) <= 1.0, f"{ruta.name}: {t.bpm} BPM y el generador hizo {bpm}"
            assert (t.license, t.source_url) == (LICENCIA, ORIGEN), (t.license, t.source_url)
            assert t.artist is None and t.title is None, \
                f"{ruta.name} no tiene tags y quedó artista={t.artist!r} título={t.title!r}"
    antes = _analyzed_at(db)

    time.sleep(0.01)   # analyzed_at tiene resolución de microsegundos; que no empate por azar
    codigo, out, _ = _correr(capsys, *_scan(db, carpeta))
    assert _resumen(out) == (0, 0, 3, 0, 0), f"segunda corrida sin cambios:\n{out}"
    assert _analyzed_at(db) == antes, "la segunda corrida reanalizó archivos que no cambiaron"

    # Cambia UNO: otro audio (otro BPM del generador) y otro mtime.
    cambiado = rutas[1]
    y, sr = click_track(140.0, dur=6.0, nota="A", modo="min", seed=9)
    sf.write(str(cambiado), y, sr, subtype="FLOAT")
    os.utime(cambiado, (1_000_000_000, 1_000_000_000))
    codigo, out, _ = _correr(capsys, *_scan(db, carpeta))
    assert _resumen(out) == (0, 1, 2, 0, 0), f"tercera corrida, un archivo cambiado:\n{out}"
    despues = _analyzed_at(db)
    assert [n for n in antes if despues[n] != antes[n]] == [cambiado.name], \
        f"se reanalizó otra cosa que {cambiado.name}: {antes} -> {despues}"
    with Store(db) as store:
        assert abs(store.get(cambiado.resolve()).bpm - 140.0) <= 1.0, \
            "el archivo cambiado conservó el análisis viejo"

    # Borra UNO: tiene que desaparecer de la base.
    borrado = rutas[0]
    borrado.unlink()
    codigo, out, _ = _correr(capsys, *_scan(db, carpeta))
    assert _resumen(out) == (0, 0, 2, 1, 0), f"cuarta corrida, un archivo borrado:\n{out}"
    assert str(borrado.resolve()) in out, "no dijo cuál borró"
    with Store(db) as store:
        assert sorted(p.name for p in store.paths()) == sorted(r.name for r in rutas[1:]), \
            "el archivo borrado sigue en la base"

    # Escanear OTRA carpeta no borra lo de esta (el pendrive que hoy no está enchufado).
    otra = tmp_path / "otra"
    otra.mkdir()
    codigo, out, _ = _correr(capsys, *_scan(db, otra))
    assert _resumen(out)[3] == 0, f"escanear otra carpeta borró tracks:\n{out}"
    with Store(db) as store:
        assert store.count() == 2, f"quedaron {store.count()} tracks"


@pytest.mark.parametrize("sin", [("--licencia",), ("--origen",), ("--licencia", "--origen")])
def test_scan_sin_licencia_u_origen_falla_y_no_escribe(tmp_path, capsys, biblioteca, sin):
    carpeta_bib, db_bib, _ = biblioteca
    carpeta = tmp_path / "crate"
    carpeta.mkdir()
    _escribir(carpeta, *CATALOGO[0], dur=3.0)

    argv = ["scan", str(carpeta)]
    if "--licencia" not in sin:
        argv += ["--licencia", LICENCIA]
    if "--origen" not in sin:
        argv += ["--origen", ORIGEN]

    # Base nueva: ni siquiera se crea el archivo.
    db_nueva = tmp_path / "nueva.sqlite"
    codigo, out, err = _correr(capsys, "--db", db_nueva, *argv)
    assert codigo == 2, f"salió {codigo}"
    for flag in sin:
        assert flag in err, f"el error no nombra {flag}:\n{err}"
    assert "Ejemplo" in err and "biblioteca personal" in err, f"no explica ni da ejemplo:\n{err}"
    assert not db_nueva.exists(), "sin licencia/origen igual se creó la base"

    # Base existente: queda exactamente como estaba.
    db = tmp_path / "copia.sqlite"
    shutil.copy(db_bib, db)
    antes = _analyzed_at(db)
    codigo, _, _ = _correr(capsys, "--db", db, *argv)
    assert codigo == 2
    assert _analyzed_at(db) == antes, "sin licencia/origen igual se escribió en la base"


def test_scan_licencia_en_blanco_cuenta_como_faltante(tmp_path, capsys):
    carpeta = tmp_path / "crate"
    carpeta.mkdir()
    db = tmp_path / "db.sqlite"
    codigo, _, err = _correr(capsys, "--db", db, "scan", carpeta,
                             "--licencia", "   ", "--origen", ORIGEN)
    assert codigo == 2 and "--licencia" in err, err
    assert not db.exists(), "una licencia en blanco igual abrió la base"


# --- list / info -----------------------------------------------------------------------

# El grupo 4 es la MARCA de confianza de la key: "?" si la detección es dudosa, ausente si
# los tramos del track votaron todos lo mismo (spec §6, tarea 17).
FILA_LIST = re.compile(
    r"^\s*(\d+\.\d) BPM\s+(\d{1,2}[AB]|\?)\s+(\S+)\s{0,3}(\?)?\s+(\d+)\s+(\S.*)$")


def test_list_muestra_lo_que_hay_en_la_base(capsys, biblioteca):
    carpeta, db, spec = biblioteca
    codigo, out, _ = _correr(capsys, "--db", db, "list")
    assert codigo == 0, out

    with Store(db) as store:
        biblio = store.load_library()
    lineas = [linea for linea in out.splitlines() if linea.rstrip().endswith(
        tuple(t.label for t in biblio))]
    assert [linea.rstrip().rsplit(None, 1)[-1] for linea in lineas] == [t.label for t in biblio], \
        f"list no tiene los tracks de la base en orden de ruta:\n{out}"
    filas = []
    for linea in lineas:
        m = FILA_LIST.match(linea)
        assert m, f"fila sin el formato BPM con UN decimal · Camelot · clásica · energía: {linea!r}"
        filas.append(m)

    for m, t in zip(filas, biblio, strict=True):
        bpm_txt, camelot, clasica, marca, energia, _ = m.groups()
        bpm_gen, nota, modo = spec[t.path.name]
        assert bpm_txt == f"{t.bpm:.1f}", f"{t.path.name}: imprimió {bpm_txt} y la base tiene {t.bpm}"
        assert abs(float(bpm_txt) - bpm_gen) <= 1.0, f"{t.path.name}: {bpm_txt} vs generador {bpm_gen}"
        assert camelot == t.key, f"{t.path.name}: Camelot {camelot} y la base {t.key}"
        assert clasica == _clasica(nota, modo), \
            f"{t.path.name}: clásica {clasica} y el generador hizo {nota} {modo}"
        assert int(energia) == round(t.energy * 100), \
            f"{t.path.name}: energía {energia} y el percentil en la base es {t.energy}"
        # Los tracks del catálogo duran 10 s: no dan para tres tramos disjuntos, así que el
        # acuerdo guardado es "0/0" y la key NO se puede mostrar como segura (§6).
        assert t.key_acuerdo == "0/0", \
            f"{t.path.name}: el acuerdo en la base es {t.key_acuerdo!r} y el caso necesita 0/0"
        assert marca == "?", \
            f"{t.path.name}: la key salió sin marca de duda con acuerdo {t.key_acuerdo!r}"


def test_info_con_fragmento_ambiguo_no_elige(capsys, biblioteca):
    carpeta, db, spec = biblioteca
    esperados = sorted(str(carpeta / n) for n in spec if "click_12" in n)
    assert len(esperados) > 1, "el catálogo del test tiene que tener homónimos para 'click_12'"

    codigo, out, err = _correr(capsys, "--db", db, "info", "click_12")
    assert codigo == 2, f"salió {codigo}"
    listados = sorted(linea.strip() for linea in err.splitlines() if linea.strip().endswith(".wav"))
    assert listados == esperados, f"candidatos listados {listados} != {esperados}"
    assert "BPM" not in out, f"eligió uno igual e imprimió su info:\n{out}"


def test_info_de_un_track(capsys, biblioteca):
    carpeta, db, spec = biblioteca
    codigo, out, _ = _correr(capsys, "--db", db, "info", "134")
    assert codigo == 0, out
    with Store(db) as store:
        t = store.get(carpeta / "click_134_Bmin.wav")
        f = store.get_features(t.path)
    campos = dict(re.findall(r"^  (\S+(?: \S+)?)\s{2,}(.+)$", out, flags=re.M))
    assert campos["archivo"] == str(t.path), campos
    assert campos["BPM"] == f"{t.bpm:.1f}", campos
    # 10 s de audio: `tono_consenso` no arma tramos y el acuerdo guardado es "0/0". La key
    # va con `?` y el renglón del acuerdo dice por qué (§6).
    assert f.key_acuerdo == "0/0", f"el caso necesita acuerdo 0/0, la base tiene {f.key_acuerdo!r}"
    assert campos["key"] == f"{t.key} (Bm) ?", campos
    assert campos["acuerdo key"].startswith("0/0 — el track no da para comparar tramos"), \
        campos["acuerdo key"]
    assert campos["onsets/s"] == f"{f.onset_rate:.2f}", campos
    assert campos["ratio percusivo"] == "no medido", campos
    assert (campos["licencia"], campos["origen"]) == (LICENCIA, ORIGEN), campos


# --- confianza de la key: el `?` de §6 (tarea 17) --------------------------------------


def _con_acuerdos(db_origen: Path, destino: Path, acuerdos: dict) -> Path:
    """Copia la base y le escribe a mano el acuerdo de cada track (por fragmento del nombre).

    El acuerdo se escribe con SQL y no se analiza audio de 140 s por caso: acá lo que se
    prueba es qué MUESTRA la CLI dado un acuerdo, no que el análisis lo mida bien (eso lo
    prueba `test_analisis.py`). El BPM y la key de la base siguen siendo los del scan real.
    """
    shutil.copy(db_origen, destino)
    con = sqlite3.connect(str(destino))
    for fragmento, (acuerdo, tramos) in acuerdos.items():
        n = con.execute("UPDATE tracks SET key_acuerdo = ?, key_tramos = ? "
                        "WHERE path LIKE ?", (acuerdo, tramos, f"%{fragmento}%")).rowcount
        assert n == 1, f"{fragmento!r} tocó {n} filas y el caso necesita exactamente una"
    con.commit()
    con.close()
    return destino


def test_list_deja_la_key_limpia_solo_con_acuerdo_unanime(tmp_path, capsys, biblioteca):
    """Los tres estados de la confianza en la misma tabla: unánime va limpia, no unánime y
    no medido van con `?`. Con un solo estado el test no distinguiría "marca bien" de
    "marca siempre" (§6: un dato que miente es peor que uno ausente)."""
    _, db_bib, _ = biblioteca
    db = _con_acuerdos(db_bib, tmp_path / "db.sqlite", {
        "click_120": ("3/3", "8A|8A|8A"),
        "click_124": ("2/3", "8A|3B|9A"),
        "click_126": (None, None),
    })

    codigo, out, _ = _correr(capsys, "--db", db, "list")
    assert codigo == 0, out

    marcas = {m.group(6).rstrip(): (m.group(4) or "")
              for m in (FILA_LIST.match(linea) for linea in out.splitlines()) if m}
    assert marcas["click_120_Amin"] == "", f"la key unánime salió marcada:\n{out}"
    assert marcas["click_124_Amin"] == "?", f"un acuerdo 2/3 salió sin marcar:\n{out}"
    assert marcas["click_126_Cmaj"] == "?", f"un acuerdo no medido salió sin marcar:\n{out}"
    assert "? junto a la key" in out, f"el `?` quedó sin explicar al pie:\n{out}"


def test_info_explica_por_que_la_key_es_dudosa(tmp_path, capsys, biblioteca):
    """`info` dice CUÁL de los dos "sin confianza" es, porque se arreglan distinto: el no
    medido se arregla volviendo a analizar el archivo, el 0/0 no se arregla con nada."""
    _, db_bib, _ = biblioteca
    db = _con_acuerdos(db_bib, tmp_path / "db.sqlite", {
        "click_120": ("3/3", "8A|8A|8A"),
        "click_124": ("2/3", "8A|3B|9A"),
        "click_126": (None, None),
    })

    campos = {}
    for consulta in ("click_120", "click_124", "click_126"):
        codigo, out, _ = _correr(capsys, "--db", db, "info", consulta)
        assert codigo == 0, out
        campos[consulta] = dict(re.findall(r"^  (\S+(?: \S+)?)\s{2,}(.+)$", out, flags=re.M))

    assert campos["click_120"]["key"] == "8A (Am)", campos["click_120"]["key"]
    assert campos["click_120"]["acuerdo key"] == \
        "3/3 — todos los tramos votaron la misma key (8A|8A|8A)", campos["click_120"]["acuerdo key"]

    assert campos["click_124"]["key"] == "8A (Am) ?", campos["click_124"]["key"]
    assert campos["click_124"]["acuerdo key"] == \
        "2/3 — los tramos no coinciden (8A|3B|9A): la key va con ?", \
        campos["click_124"]["acuerdo key"]

    assert campos["click_126"]["key"].endswith(" ?"), campos["click_126"]["key"]
    assert campos["click_126"]["acuerdo key"].startswith("no medido —"), \
        campos["click_126"]["acuerdo key"]
    assert "vuelva a analizar el archivo" in campos["click_126"]["acuerdo key"], \
        campos["click_126"]["acuerdo key"]


def test_radio_marca_la_key_dudosa_en_cada_paso(tmp_path, capsys, biblioteca):
    """El set también respeta §6: la key de cada paso va con `?` salvo acuerdo unánime. Es
    donde más importa, porque el paso de al lado dice '8A → 9A (vecino)'."""
    _, db_bib, _ = biblioteca
    db = _con_acuerdos(db_bib, tmp_path / "db.sqlite", {
        "click_126": ("3/3", "3B|3B|3B"),
        "click_128": ("1/3", "9B|8A|3B"),
    })

    codigo, out, _ = _correr(capsys, "--db", db, "radio", "click_126", "--largo", 6)
    assert codigo == 0, out
    marcas = _marcas_de_key(out)
    assert marcas.get("click_126_Cmaj") == "", f"la key unánime salió marcada:\n{out}"
    assert marcas.get("click_128_Gmaj") == "?", f"un acuerdo 1/3 salió sin marcar:\n{out}"
    otros = {k: v for k, v in marcas.items() if k not in ("click_126_Cmaj", "click_128_Gmaj")}
    assert set(otros.values()) == {"?"}, \
        f"tracks de 10 s (acuerdo 0/0) sin marcar: {otros}\n{out}"
    # El set es una salida que se lee sola (se exporta, se pega en un chat): sin la leyenda
    # el `?` es un símbolo mudo. Va DESPUÉS del aviso de set corto, que no se puede tapar.
    from motor.cli import LEYENDA_KEY
    assert out.rstrip().endswith(LEYENDA_KEY), \
        f"la radio no cierra con la leyenda del `?`:\n{out}"


# Renglón de progreso del `scan`. Grupos: 1 archivo · 2 BPM · 3 Camelot · 4 clásica ·
# 5 marca de duda de la key.
PROGRESO_SCAN = re.compile(
    r"^\s*\[\d+/\d+\]\s+(\S+)\s+(\d+\.\d) BPM\s+(\d{1,2}[AB]|\?)\s+(\S+)\s{0,2}(\?)?\s+"
    r"\d+\.\d\d s$")


def test_scan_de_un_track_largo_deja_la_key_sin_marca(tmp_path, capsys):
    """Punta a punta y sin tocar la base a mano. Dos archivos en la misma carpeta:

    - 140 s en La menor: da para tres tramos disjuntos, los tres votan 8A → acuerdo unánime
      y la key sale LIMPIA (el único caso donde no hay marca por el camino real);
    - 10 s: no da para tramos → "0/0" y la key sale con `?`.

    Se miran las dos salidas que imprimen una key: el progreso del propio `scan` y `list`.
    """
    carpeta = tmp_path / "crate"
    carpeta.mkdir()
    largo, _ = click_track(128.0, dur=140.0, nota="A", modo="min", seed=3)
    corto, sr = click_track(124.0, dur=10.0, nota="C", modo="maj", seed=4)
    sf.write(str(carpeta / "largo_128_Amin.wav"), largo, sr, subtype="FLOAT")
    sf.write(str(carpeta / "corto_124_Cmaj.wav"), corto, sr, subtype="FLOAT")

    db = tmp_path / "db.sqlite"
    codigo, out, _ = _correr(capsys, *_scan(db, carpeta))
    assert codigo == 0, out

    with Store(db) as store:
        f_largo = store.get_features(carpeta / "largo_128_Amin.wav")
        f_corto = store.get_features(carpeta / "corto_124_Cmaj.wav")
    assert (f_largo.key, f_largo.key_acuerdo, f_largo.key_tramos) == ("8A", "3/3", "8A|8A|8A"), \
        f"el scan midió key {f_largo.key} acuerdo {f_largo.key_acuerdo!r} tramos {f_largo.key_tramos!r}"
    assert (f_corto.key_acuerdo, f_corto.key_tramos) == ("0/0", ""), \
        f"el track corto midió acuerdo {f_corto.key_acuerdo!r} tramos {f_corto.key_tramos!r}"

    # El progreso del scan es la primera vez que el DJ ve una key: también respeta §6.
    progreso = {m.group(1): (m.group(5) or "")
                for m in (PROGRESO_SCAN.match(linea) for linea in out.splitlines()) if m}
    assert set(progreso) == {"largo_128_Amin.wav", "corto_124_Cmaj.wav"}, \
        f"el progreso del scan no imprimió los dos tracks con formato de key:\n{out}"
    assert progreso["largo_128_Amin.wav"] == "", \
        f"el scan marcó una key de acuerdo unánime:\n{out}"
    assert progreso["corto_124_Cmaj.wav"] == "?", \
        f"el scan no marcó una key con acuerdo 0/0:\n{out}"

    codigo, out, _ = _correr(capsys, "--db", db, "list")
    assert codigo == 0, out
    filas = {m.group(6).rstrip(): m for m in
             (FILA_LIST.match(linea) for linea in out.splitlines()) if m}
    fila = filas["largo_128_Amin"]
    assert (fila.group(2), fila.group(3)) == ("8A", "Am"), fila.groups()
    assert fila.group(4) is None, f"la key de acuerdo unánime salió marcada:\n{out}"
    assert filas["corto_124_Cmaj"].group(4) == "?", f"la key con 0/0 salió sin marcar:\n{out}"


def test_un_acuerdo_ilegible_no_pasa_por_confiable(tmp_path, capsys, biblioteca):
    """Una base tocada por fuera puede tener basura en `key_acuerdo` (`Store.upsert` no la
    deja entrar, pero un UPDATE a mano sí). Un acuerdo que no se puede leer NO es un acuerdo
    unánime: la key va con `?` y `info` dice que el dato está ilegible, en vez de tirar un
    traceback o —peor— mostrar la key como segura."""
    _, db_bib, _ = biblioteca
    db = _con_acuerdos(db_bib, tmp_path / "db.sqlite", {
        "click_120": ("3/3", "8A|8A|8A"),
        "click_124": ("abc", "8A|3B|9A"),
    })

    codigo, out, _ = _correr(capsys, "--db", db, "list")
    assert codigo == 0, out
    marcas = {m.group(6).rstrip(): (m.group(4) or "")
              for m in (FILA_LIST.match(linea) for linea in out.splitlines()) if m}
    assert marcas["click_124_Amin"] == "?", f"un acuerdo ilegible salió sin marcar:\n{out}"
    assert marcas["click_120_Amin"] == "", \
        f"precondición: la fila sana tiene que seguir saliendo limpia:\n{out}"

    codigo, out, _ = _correr(capsys, "--db", db, "info", "click_124")
    assert codigo == 0, out
    campos = dict(re.findall(r"^  (\S+(?: \S+)?)\s{2,}(.+)$", out, flags=re.M))
    assert campos["key"] == "8A (Am) ?", campos["key"]
    assert campos["acuerdo key"] == "ilegible ('abc') — la key va con ?", campos["acuerdo key"]


def test_un_acuerdo_que_no_es_texto_no_tira_la_biblioteca(tmp_path, capsys, biblioteca):
    """La columna es TEXT, pero SQLite deja escribir un BLOB por fuera y vuelve como `bytes`.
    `acuerdo_unanime` hace `.strip()` sobre eso y levanta `TypeError`, que no es `ValueError`:
    antes UNA fila así tiraba `list` e `info` con traceback para TODA la biblioteca. Un dato
    corrupto es dudoso, no una excusa para dejar al DJ sin tabla."""
    _, db_bib, _ = biblioteca
    # La fila sana es la precondición: sin ella el test no distingue "marca la corrupta" de
    # "marca todo" (los clicks del fixture duran 12 s, así que su acuerdo real es "0/0").
    db = _con_acuerdos(db_bib, tmp_path / "db.sqlite", {"click_120": ("3/3", "8A|8A|8A")})
    con = sqlite3.connect(str(db))
    n = con.execute("UPDATE tracks SET key_acuerdo = ?, key_tramos = ? WHERE path LIKE ?",
                    (b"\x00\xff", "8A|3B|9A", "%click_124%")).rowcount
    assert n == 1, f"el UPDATE tocó {n} filas y el caso necesita exactamente una"
    con.commit()
    con.close()

    codigo, out, _ = _correr(capsys, "--db", db, "list")
    assert codigo == 0, f"una fila con BLOB tiró la tabla entera:\n{out}"
    marcas = {m.group(6).rstrip(): (m.group(4) or "")
              for m in (FILA_LIST.match(linea) for linea in out.splitlines()) if m}
    assert marcas["click_124_Amin"] == "?", f"un acuerdo no textual salió sin marcar:\n{out}"
    assert marcas["click_120_Amin"] == "", \
        f"precondición: las otras filas siguen limpias:\n{out}"

    codigo, out, _ = _correr(capsys, "--db", db, "info", "click_124")
    assert codigo == 0, f"info se cayó con la fila corrupta:\n{out}"
    campos = dict(re.findall(r"^  (\S+(?: \S+)?)\s{2,}(.+)$", out, flags=re.M))
    assert campos["key"] == "8A (Am) ?", campos["key"]
    assert campos["acuerdo key"].startswith("ilegible"), campos["acuerdo key"]


def test_info_no_narra_un_acuerdo_unanime_sin_votos(tmp_path, capsys, biblioteca):
    """"3/3" con los tramos vacíos es una fila incoherente: dice que tres tramos coincidieron
    y no guarda ninguno. `Store.upsert` la rechaza, así que solo puede venir de afuera, e
    `info` la tiene que denunciar — no decir "todos los tramos votaron la misma key" sin
    tener un solo voto que mostrar (§6)."""
    _, db_bib, _ = biblioteca
    db = _con_acuerdos(db_bib, tmp_path / "db.sqlite", {"click_120": ("3/3", "")})

    codigo, out, _ = _correr(capsys, "--db", db, "info", "click_120")
    assert codigo == 0, out
    campos = dict(re.findall(r"^  (\S+(?: \S+)?)\s{2,}(.+)$", out, flags=re.M))
    assert campos["acuerdo key"] == \
        "3/3 — INCOHERENTE: dice 3 tramos de acuerdo y no guarda ninguno; la fila no la " \
        "escribió djradio", campos["acuerdo key"]
    assert "votaron la misma key" not in campos["acuerdo key"], campos["acuerdo key"]


def test_base_inexistente_no_se_crea(tmp_path, capsys):
    db = tmp_path / "no_esta.sqlite"
    codigo, _, err = _correr(capsys, "--db", db, "list")
    assert codigo == 2 and "No existe la base" in err, err
    assert not db.exists(), "un list sobre una base inexistente la creó"


# --- radio -----------------------------------------------------------------------------

# Grupos: 1 nº de paso · 2 BPM · 3 Camelot · 4 clásica · 5 marca de duda de la key · 6 label.
PASO = re.compile(
    r"^\s*(\d+)\.\s+(\d+\.\d) BPM\s+(\S+)\s+(\S+)\s*(\?)?\s+energía\s+\d+\s+(\S.*)$")


def _pasos(out: str) -> list[tuple[str, str]]:
    """(label, motivo) de cada paso impreso: la línea del track y la del └ que la sigue."""
    lineas = out.splitlines()
    pasos = []
    for i, linea in enumerate(lineas):
        m = PASO.match(linea)
        if m:
            motivo = lineas[i + 1].strip()
            assert motivo.startswith("└ "), f"el paso {m.group(1)} no tiene motivo:\n{out}"
            pasos.append((m.group(6), motivo[2:]))
    return pasos


def _marcas_de_key(out: str) -> dict[str, str]:
    """{label: "?" o ""} por cada paso de la radio: cómo quedó marcada la key de cada track."""
    return {m.group(6): (m.group(5) or "")
            for m in (PASO.match(linea) for linea in out.splitlines()) if m}


def _dist_bpm(a, b):
    """±8% de §4 como pitch real: `1 - lento / rápido` en la mejor de las tres lecturas.

    Escrito acá y DE OTRA FORMA que `scoring`: la versión anterior copiaba la fórmula del
    motor (`min(|a-b|, |a-2b|, |a-b/2|) / max(a, b)`), que medía la mitad del salto real en
    los pares de octava, y el test heredaba el mismo error sin avisar."""
    return min(1.0 - min(a, y) / max(a, y) for y in (b, 2 * b, b / 2))


def test_radio_punta_a_punta(tmp_path, capsys, biblioteca):
    """El criterio de hecho de la tarea 1: scan → radio. Transiciones dentro de ±8%, cada
    motivo corresponde a SUS dos tracks, determinismo con la misma semilla y el M3U8 en el
    orden del set."""
    carpeta, db_bib, _ = biblioteca
    db = tmp_path / "db.sqlite"
    shutil.copy(db_bib, db)
    m3u8 = tmp_path / "set.m3u8"

    # Se piden los 7 del catálogo: el de 170 BPM no mezcla con nada, así que si aparece en el
    # set es que alguien aflojó la compuerta de ±8%.
    codigo, out, _ = _correr(capsys, "--db", db, "radio", "click_126", "--largo", 7,
                             "--m3u8", m3u8)
    assert codigo == 0, out
    pasos = _pasos(out)
    with Store(db) as store:
        por_label = {t.label: t for t in store.load_library()}
    tracks = [por_label[label] for label, _ in pasos]

    assert len(tracks) >= 4, f"el set tiene {len(tracks)} tracks, pocas transiciones:\n{out}"
    assert [t.path for t in tracks] != sorted(t.path for t in tracks), \
        "el set salió en orden de ruta: así el test no distingue un M3U8 reordenado"
    assert tracks[0].path.name == "click_126_Cmaj.wav", f"no arrancó por la semilla:\n{out}"
    assert len({t.path for t in tracks}) == len(tracks), f"un track se repite:\n{out}"
    assert pasos[0][1] == f"semilla | {tracks[0].key} | {tracks[0].bpm:.1f} BPM", pasos[0]

    for (_, motivo), prev, cur in zip(pasos[1:], tracks[:-1], tracks[1:], strict=True):
        assert _dist_bpm(prev.bpm, cur.bpm) < 0.08, \
            f"{prev.label} ({prev.bpm}) → {cur.label} ({cur.bpm}) fuera de ±8% (§4)"
        pct, _ = bpm_delta_pct(prev.bpm, cur.bpm)
        esperado = (f"{pct:+.1f}% BPM | {prev.key} → {cur.key} "
                    f"({key_relation(prev.key, cur.key)})")
        assert motivo == esperado, f"el motivo no es el de {prev.label} → {cur.label}: {motivo!r}"

    rutas_m3u8 = [linea for linea in m3u8.read_text(encoding="utf-8").splitlines()
                  if linea and not linea.startswith("#")]
    assert rutas_m3u8 == [str(t.path) for t in tracks], f"el M3U8 no tiene el set en orden: {rutas_m3u8}"
    if len(tracks) < 7:
        assert f"SET CORTO: quedó en {len(tracks)} de 7." in out, \
            f"el set tiene {len(tracks)} de 7 y no lo dice:\n{out}"
    else:
        assert "SET CORTO" not in out, f"llegó al largo pedido y dice que quedó corto:\n{out}"


def test_radio_misma_semilla_misma_salida(tmp_path, capsys, biblioteca):
    """Spec §5, con azar prendido para que la semilla importe de verdad."""
    _, db_bib, _ = biblioteca
    db = tmp_path / "db.sqlite"
    shutil.copy(db_bib, db)
    argv = ("--db", db, "radio", "click_124", "--largo", 5, "--randomness", 1, "--semilla", 7)
    _, primera, _ = _correr(capsys, *argv)
    _, segunda, _ = _correr(capsys, *argv)
    assert len(_pasos(primera)) >= 2, f"el set no tiene transiciones que comparar:\n{primera}"
    assert primera == segunda, f"misma semilla, dos salidas:\n{primera}\n---\n{segunda}"


def test_radio_set_corto_dice_por_que(tmp_path, capsys, biblioteca):
    """170 BPM no tiene nada a ±8% en el catálogo: el set queda en 1 y lo tiene que decir."""
    _, db_bib, _ = biblioteca
    db = tmp_path / "db.sqlite"
    shutil.copy(db_bib, db)
    codigo, out, _ = _correr(capsys, "--db", db, "radio", "click_170", "--largo", 5)
    assert codigo == 0, out
    assert len(_pasos(out)) == 1, f"con 170 BPM no hay nada mezclable y el set tiene más:\n{out}"
    m = re.search(r"SET CORTO: quedó en (\d+) de (\d+)\. Motivo \((\w+)\): (.+)", out)
    assert m, f"el set quedó corto y no lo dijo:\n{out}"
    assert m.groups()[:3] == ("1", "5", "sin_candidatos_mezclables"), m.groups()
    assert "170.0 BPM" in m.group(4) and "±8%" in m.group(4), f"el motivo no explica: {m.group(4)}"


# --- dependencias y esquema: fallar ANTES de trabajar -----------------------------------


def test_scan_sin_mutagen_falla_antes_de_tocar_la_base(tmp_path, capsys, monkeypatch):
    """Sin mutagen, el scan viejo hacía warm-up, borraba de la base el archivo que ya no
    estaba, analizaba el nuevo y recién ahí se caía con ModuleNotFoundError. Tiene que salir
    con error de uso claro sin haber hecho nada de eso."""
    carpeta = tmp_path / "crate"
    carpeta.mkdir()
    quitado = _escribir(carpeta, *CATALOGO[0], dur=3.0, seed=0)
    _escribir(carpeta, *CATALOGO[1], dur=3.0, seed=1)
    db = tmp_path / "db.sqlite"
    assert _correr(capsys, *_scan(db, carpeta))[0] == 0
    quitado.unlink()                                   # uno que el scan borraría de la base
    _escribir(carpeta, *CATALOGO[2], dur=3.0, seed=2)  # y uno que tendría que analizar
    antes = db.read_bytes()

    monkeypatch.setitem(sys.modules, "mutagen", None)   # `import mutagen` → ImportError
    codigo, out, err = _correr(capsys, *_scan(db, carpeta))

    assert codigo == 2, f"salió {codigo}\n{out}\n{err}"
    assert "mutagen" in err and "pip install mutagen==1.48.1" in err, err
    assert "warm-up" not in out, f"trabajó antes de avisar que faltaba mutagen:\n{out}"
    assert db.read_bytes() == antes, "sin mutagen igual se modificó la base"


def test_base_de_esquema_futuro_se_rechaza_sin_tocarla(tmp_path, capsys, biblioteca):
    """Una base escrita por un djradio más nuevo (user_version mayor): error de uso claro en
    `list` y en `scan`, y la base queda byte a byte igual."""
    carpeta_bib, db_bib, _ = biblioteca
    db = tmp_path / "futura.sqlite"
    shutil.copy(db_bib, db)
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA user_version = 99")
    con.commit()
    con.close()
    antes = db.read_bytes()

    codigo, _, err = _correr(capsys, "--db", db, "list")
    assert codigo == 2 and "versión 99" in err and "No se tocó la base" in err, err
    codigo, out, err = _correr(capsys, *_scan(db, carpeta_bib))
    assert codigo == 2 and "versión 99" in err, f"{out}\n{err}"
    assert db.read_bytes() == antes, "una base de esquema desconocido se modificó igual"


def test_base_tomada_por_otra_instancia_es_error_de_uso(tmp_path, capsys, biblioteca, monkeypatch):
    """H2 (tarea 1.2): si otro proceso tiene la base tomada más de lo que el store espera,
    SQLite tira `OperationalError: database is locked`. La CLI lo tiene que decir como lo
    que es — hay otra instancia, reintentá — y no como un traceback. La espera se acorta a
    0.2 s para que el test no tarde los 30 s reales."""
    from motor import store as modulo_store

    _, db_bib, _ = biblioteca
    db = tmp_path / "tomada.sqlite"
    shutil.copy(db_bib, db)
    monkeypatch.setattr(modulo_store, "ESPERA_BLOQUEO_S", 0.2)
    otra = sqlite3.connect(str(db))
    otra.execute("BEGIN EXCLUSIVE")                  # la "otra instancia"
    try:
        t0 = time.perf_counter()
        codigo, out, err = _correr(capsys, "--db", db, "list")
        espera = time.perf_counter() - t0
    finally:
        otra.rollback()
        otra.close()
    assert codigo == 2, f"código {codigo}\n{out}\n{err}"
    assert "está ocupada" in err and "otra instancia" in err and "reintentá" in err, err
    assert "Traceback" not in err, err
    assert 0.2 <= espera < 4.0, (f"esperó {espera:.3f} s: no es la espera del store (0.2 s) "
                                 f"— ¿se abrió sin `ESPERA_BLOQUEO_S` (default de sqlite3: 5 s)?")
    codigo, out, _ = _correr(capsys, "--db", db, "list")
    assert codigo == 0, "suelta la otra instancia, la base tiene que volver a abrirse"


def test_archivo_que_cambia_y_no_se_analiza_cuenta_como_borrado(tmp_path, capsys):
    """Un archivo analizado que cambia y ya no decodifica: la fila vieja se quita (describe
    OTRO audio) y el resumen tiene que decirlo. Antes decía `borrados 0 · fallidos 1` con
    una fila menos en la base."""
    carpeta = tmp_path / "crate"
    carpeta.mkdir()
    ruta = _escribir(carpeta, *CATALOGO[0], dur=3.0)
    db = tmp_path / "db.sqlite"
    codigo, out, _ = _correr(capsys, *_scan(db, carpeta))
    assert _resumen(out) == (1, 0, 0, 0, 0), out

    ruta.write_bytes(b"ya no es audio")
    os.utime(ruta, (1_000_000_000, 1_000_000_000))
    codigo, out, _ = _correr(capsys, *_scan(db, carpeta))

    assert codigo == 1, out
    assert _resumen(out) == (0, 0, 0, 1, 1), f"el borrado del análisis viejo no se contó:\n{out}"
    assert ("borrado (cambió y la versión nueva no se pudo analizar: se quitó el análisis "
            f"anterior): {ruta.resolve()}") in out, out
    with Store(db) as store:
        assert store.count() == 0, "el análisis del audio viejo sigue en la base"


# --- radio: la curva con pocos tracks ----------------------------------------------------


def test_spearman_de_un_set_chico_se_marca_orientativo(tmp_path, capsys, biblioteca):
    _, db_bib, _ = biblioteca
    db = tmp_path / "db.sqlite"
    shutil.copy(db_bib, db)
    codigo, out, _ = _correr(capsys, "--db", db, "radio", "click_124", "--largo", 3)
    assert codigo == 0, out
    n = len(_pasos(out))
    assert n >= 2, f"con menos de 2 tracks no hay Spearman que marcar:\n{out}"
    linea = next(linea for linea in out.splitlines() if "Spearman" in linea)
    # Curva peak (default) con --largo 3: t = 0, 0.5, 1 → solo las posiciones 0 y 1 suben.
    # Con n = 2 o 3 tracks sonados, el tramo ascendente tiene 2 puntos.
    assert "ORIENTATIVO: con 2 puntos en el tramo ascendente" in linea, \
        f"un Spearman de 2 puntos sin aviso: {linea}"
    from benchmark.umbrales import UMBRALES
    limite = next(u.limite for u in UMBRALES if u.clave == "energia_desvio_curva")
    assert re.search(rf"desvío medio \d\.\d{{3}} \(§4: la mediana de muchos sets ≤ {limite:g}; "
                     r"un set suelto no se aprueba\)", linea), \
        f"la radio no imprime el desvío de la curva: {linea}"


RENGLON_CURVA = re.compile(r"^(\d+) de (\d+) tracks pedidos · curva de energía \((\w+)\) · "
                           r"desvío medio (\d\.\d{3}) ")


@pytest.mark.parametrize("curva", ["warmup", "flat"])
def test_radio_set_cortado_mide_la_curva_pedida(tmp_path, capsys, biblioteca, curva):
    """La CLI le pasa a `linea_curva` la curva y el largo PEDIDOS, no los que sonaron.

    Se piden 12 tracks: el catálogo tiene 6 mezclables entre sí (el de 170 BPM no entra), así
    que el set queda cortado. El desvío esperado se calcula con `energy_curve_deviation`
    sobre las energías de los tracks impresos (leídas de la base), la curva pedida y el
    largo pedido. La precondición verifica que con el largo real, o con la curva "peak",
    el número impreso sería otro: si no, el test no distinguiría el error."""
    from motor.energia import energy_curve_deviation

    _, db_bib, _ = biblioteca
    db = tmp_path / "db.sqlite"
    shutil.copy(db_bib, db)
    pedido = 12
    codigo, out, _ = _correr(capsys, "--db", db, "radio", "click_126", "--largo", pedido,
                             "--curva", curva)
    assert codigo == 0, out
    with Store(db) as store:
        por_label = {t.label: t for t in store.load_library()}
    energias = [por_label[label].energy for label, _ in _pasos(out)]
    assert 2 <= len(energias) < pedido, f"el set no quedó cortado ({len(energias)}):\n{out}"

    renglon = next((linea for linea in out.splitlines() if RENGLON_CURVA.match(linea)), None)
    assert renglon, f"no está el renglón de la curva:\n{out}"
    n, n_pedido, nombre, desvio = RENGLON_CURVA.match(renglon).groups()
    assert (int(n), int(n_pedido), nombre) == (len(energias), pedido, curva), renglon

    esperado = f"{energy_curve_deviation(energias, curva, pedido):.3f}"
    con_largo_real = f"{energy_curve_deviation(energias, curva, len(energias)):.3f}"
    con_peak = f"{energy_curve_deviation(energias, 'peak', pedido):.3f}"
    assert esperado != con_peak, "precondición: peak y la curva pedida dan el mismo desvío"
    if curva != "flat":   # flat es constante: el largo no cambia su objetivo
        assert esperado != con_largo_real, "precondición: el largo pedido no cambia el desvío"
    assert desvio == esperado, \
        f"desvío {desvio} ≠ {esperado} (curva {curva}, largo {pedido}): {renglon}"
    if curva == "flat":
        assert renglon.endswith("no definido: la curva flat no tiene tramo que suba"), renglon


def test_linea_curva_el_minimo_cuenta_puntos_del_tramo_ascendente():
    """El borde de `MIN_PUNTOS_SPEARMAN` (12) se mide en PUNTOS del tramo ascendente, no en
    tracks. Con "warmup" todo el set sube: 11 puntos orientativo, 12 no. Energías que suben
    de a una → Spearman esperado exactamente +1.00."""
    from motor.cli import MIN_PUNTOS_SPEARMAN, linea_curva

    assert MIN_PUNTOS_SPEARMAN == 12
    doce = linea_curva([i / 11 for i in range(12)], "warmup")
    once = linea_curva([i / 10 for i in range(11)], "warmup")
    assert doce.endswith("§4 pide ≥ 0.5): +1.00"), doce
    assert "ORIENTATIVO: con 11 puntos" in once and "+1.00" in once, once


def test_linea_curva_peak_de_12_tracks_es_orientativa():
    """Con "peak" un set de 12 tracks aporta 9 puntos ascendentes (t = i/11 ≤ 0.75 ⇔ i ≤ 8):
    orientativo aunque tenga 12 tracks. Recién con 16 tracks llega a 12 puntos
    (i/15 ≤ 0.75 ⇔ i ≤ 11). Las energías siguen la curva exacta → desvío 0.000, Spearman 1."""
    from motor.cli import linea_curva
    from motor.energia import energy_target

    doce = linea_curva([energy_target(i, 12) for i in range(12)], "peak")
    assert "ORIENTATIVO: con 9 puntos en el tramo ascendente" in doce and "+1.00" in doce, doce
    assert "desvío medio 0.000" in doce, doce
    dieciseis = linea_curva([energy_target(i, 16) for i in range(16)], "peak")
    assert dieciseis.endswith("§4 pide ≥ 0.5): +1.00"), dieciseis


def test_linea_curva_flat_no_inventa_spearman():
    from motor.cli import linea_curva

    linea = linea_curva([0.5] * 20, "flat")
    assert linea.endswith("no definido: la curva flat no tiene tramo que suba"), linea
    assert "desvío medio 0.000" in linea, linea


# --- resolver_track ----------------------------------------------------------------------


def test_info_con_carpeta_homonima_en_el_cwd_busca_el_fragmento(tmp_path, capsys, biblioteca,
                                                               monkeypatch):
    """`info 134` con una carpeta `134/` en el directorio de trabajo: es un fragmento del
    nombre, no una ruta a un track (un track es un archivo)."""
    _, db, _ = biblioteca
    monkeypatch.chdir(tmp_path)
    (tmp_path / "134").mkdir()
    codigo, out, err = _correr(capsys, "--db", db, "info", "134")
    assert codigo == 0, f"la carpeta se tomó como ruta:\n{err}"
    campos = dict(re.findall(r"^  (\S+(?: \S+)?)\s{2,}(.+)$", out, flags=re.M))
    assert Path(campos["archivo"]).name == "click_134_Bmin.wav", campos


# --- tags reales ---------------------------------------------------------------------------


def test_scan_lee_artista_y_titulo_de_tags_reales(tmp_path, capsys):
    """Ningún otro fixture tiene tags: romper la lectura de artista/título pasaba todo. Acá
    los tags se escriben con mutagen — Vorbis comments en un FLAC e ID3 en un WAV, las dos
    familias — y `list` / `info` tienen que mostrar ESOS valores."""
    from mutagen.flac import FLAC
    from mutagen.id3 import TIT2, TPE1
    from mutagen.wave import WAVE

    carpeta = tmp_path / "crate"
    carpeta.mkdir()
    y, sr = click_track(128.0, dur=3.0, nota="A", modo="min", seed=4)
    flac = carpeta / "uno.flac"
    sf.write(str(flac), y, sr, format="FLAC")
    f = FLAC(str(flac))
    f["artist"], f["title"] = "Rosa Pistola", "Canción Flac"
    f.save()

    y, sr = click_track(130.0, dur=3.0, nota="E", modo="min", seed=5)
    wav = carpeta / "dos.wav"
    sf.write(str(wav), y, sr, subtype="PCM_16")
    w = WAVE(str(wav))
    w.add_tags()
    w.tags.add(TPE1(encoding=3, text="Dax J"))
    w.tags.add(TIT2(encoding=3, text="Tema Wav"))
    w.save()

    db = tmp_path / "db.sqlite"
    codigo, out, _ = _correr(capsys, *_scan(db, carpeta))
    assert codigo == 0 and _resumen(out)[0] == 2, f"los archivos con tags no se analizaron:\n{out}"

    codigo, out, _ = _correr(capsys, "--db", db, "list")
    assert codigo == 0, out
    filas = [FILA_LIST.match(linea) for linea in out.splitlines()]
    assert [m.group(6).rstrip() for m in filas if m] == \
        ["Dax J — Tema Wav", "Rosa Pistola — Canción Flac"], f"list no muestra los tags:\n{out}"

    for consulta, artista, titulo in (("uno.flac", "Rosa Pistola", "Canción Flac"),
                                      ("dos.wav", "Dax J", "Tema Wav")):
        codigo, out, _ = _correr(capsys, "--db", db, "info", consulta)
        assert codigo == 0, out
        campos = dict(re.findall(r"^  (\S+(?: \S+)?)\s{2,}(.+)$", out, flags=re.M))
        assert (campos["artista"], campos["título"]) == (artista, titulo), campos
