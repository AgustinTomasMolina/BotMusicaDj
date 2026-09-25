"""Tests de /api/radio/* (la pantalla de radio de MusiFlix sobre el motor de DJ Radio).

La biblioteca es una base del motor construida en `tmp_path` con `Store.upsert`, como los
tests de /api/biblioteca arman su XML: nada de la base real del dueño ni de rutas
personales. Los audios son WAVs de 0.25 s generados con la stdlib.

Dos aclaraciones sobre los datos, porque parecen chocar con reglas del proyecto:

- El BPM y la key NO se inventan como resultado de un análisis: se ESCRIBEN en la base y el
  test verifica que el endpoint devuelva exactamente eso. Lo que se prueba acá es la capa
  que sirve la biblioteca, no la que la mide (esa se prueba contra ground truth en
  `motor/tests`). La única traducción que el test escribe a mano es Camelot → clásica
  ('8A' → 'Am'), que es teoría musical, no una salida del motor.
- La `duration` que guarda la base es la declarada en el `upsert`, no la del WAV: los
  archivos son de 0.25 s para que la suite no escriba megabytes. La radio decide con la
  duración de la BASE, que es lo que se está probando.
"""
import importlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("httpx")  # lo necesita el TestClient de Starlette
from fastapi.testclient import TestClient  # noqa: E402

# El CATALOGO y los WAVs viven en tests/sinteticos.py: el E2E del front (frontend/e2e) arma
# la misma base del motor y compara la pantalla contra esta API.
from sinteticos import CATALOGO, armar_base_radio  # noqa: E402

from motor.cli import (  # noqa: E402
    aviso_fragmentos,
    key_dudosa,
    motivo_semilla_no_track,
    percentil_energia,
    titular_corte,
)
from motor.modelos import DURACION_MINIMA_TRACK_S  # noqa: E402
from motor.radio import RadioConfig, build_set  # noqa: E402
from motor.store import Store  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """El módulo `server` importado con sus efectos de carga neutralizados (mismo motivo y
    misma receta que `tests/test_server_biblioteca.py`)."""
    datos = tmp_path_factory.mktemp("server-radio-datos")
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


@pytest.fixture(autouse=True)
def indice_limpio(server, monkeypatch):
    """El índice id→ruta es global del módulo: sin limpiarlo, un test serviría el archivo
    que dejó cacheado otro."""
    monkeypatch.setattr(server, "_radio_audio", {})


@pytest.fixture
def biblioteca(tmp_path, monkeypatch):
    """Base del motor con el CATALOGO + los WAVs. Devuelve rutas, bytes y la base."""
    raiz = tmp_path / "musica"
    db = tmp_path / "djradio" / "biblioteca.sqlite"
    audios, rutas = armar_base_radio(raiz, db)
    monkeypatch.setenv("DJRADIO_DB", str(db))
    return {"raiz": raiz, "db": db, "audios": audios, "rutas": rutas}


def _tracks_del_motor(db) -> dict:
    """La biblioteca leída con el motor, por nombre de archivo. Es la fuente contra la que
    se comparan las respuestas: si el endpoint recalcula algo, deja de coincidir."""
    with Store(db) as store:
        return {t.path.name: t for t in store.load_library()}


# --------------------------------------------------------------- degradación sin config

def test_radio_sin_base_degrada_sin_500(client, tmp_path, monkeypatch):
    """La base configurada no existe: 200, vacía y con el motivo diciendo dónde buscó.

    También verifica que NO se haya creado la base: `Store(ruta)` crea el archivo, así que
    un endpoint que abriera sin preguntar dejaría una base vacía nueva y el próximo pedido
    diría "vacía" en vez de "no existe" (el mismo cuidado que `cli._abrir_existente`).
    """
    fantasma = tmp_path / "no-existe" / "biblioteca.sqlite"
    monkeypatch.setenv("DJRADIO_DB", str(fantasma))

    r = client.get("/api/radio/biblioteca")
    assert r.status_code == 200
    d = r.json()
    assert (d["configurada"], d["estado"], d["total"], d["tracks"]) == (False, "sin-base", 0, [])
    assert str(fantasma) in d["motivo"], "el motivo no dice qué base buscó: no hay qué arreglar"
    assert not fantasma.exists(), "el endpoint creó la base que no existía"

    s = client.get("/api/radio/set", params={"track": "lo que sea"}).json()
    assert (s["configurada"], s["estado"], s["pasos"], s["semilla"]) == (False, "sin-base", [], None)


def test_radio_sin_el_paquete_motor_degrada_sin_500(client, biblioteca, monkeypatch, caplog):
    """Como en una imagen Docker que no copió motor/: las variables están y el import falla.
    Un None en sys.modules hace que `from motor.store import ...` tire ImportError."""
    monkeypatch.setitem(sys.modules, "motor.store", None)
    with caplog.at_level("WARNING", logger="bot_web"):
        d = client.get("/api/radio/biblioteca").json()
    assert (d["configurada"], d["estado"], d["total"]) == (False, "sin-motor", 0)
    assert "motor" in d["motivo"], "el front tiene que poder explicar por qué está vacía"
    assert any("motor" in m and "desactivada" in m for m in caplog.messages), \
        "sin warning en el log nadie se entera de que falta el motor"


def test_radio_base_vacia_dice_que_escanear(client, tmp_path, monkeypatch):
    """La base existe pero no tiene tracks: configurada SÍ (el usuario apuntó a algo) y el
    motivo dice cuál es el paso siguiente."""
    db = tmp_path / "vacia.sqlite"
    Store(db).close()
    monkeypatch.setenv("DJRADIO_DB", str(db))

    d = client.get("/api/radio/biblioteca").json()
    assert (d["configurada"], d["estado"], d["total"]) == (True, "base-vacia", 0)
    assert "scan" in d["motivo"], f"no dice cómo llenarla: {d['motivo']}"


def test_radio_base_ocupada_no_congela_la_pantalla(server, client, biblioteca, monkeypatch):
    """Con un scan corriendo, la base está tomada. La CLI espera 30 s porque ahí esperar es
    lo correcto; un GET no puede colgar la pantalla medio minuto, y "database is locked" en
    crudo no le dice al DJ que lo único que tiene que hacer es reintentar."""
    import sqlite3
    import time as reloj

    from motor.store import ESPERA_BLOQUEO_S

    otro = sqlite3.connect(str(biblioteca["db"]), timeout=0.1)
    otro.execute("BEGIN EXCLUSIVE")          # como un scan a mitad de camino
    try:
        t0 = reloj.monotonic()
        d = client.get("/api/radio/biblioteca").json()
        tardo = reloj.monotonic() - t0
    finally:
        otro.rollback()
        otro.close()

    assert d["estado"] == "base-ocupada", f"la clasificó mal: {d['estado']} / {d['motivo']}"
    assert "ocupada" in d["motivo"] and "eintentá" in d["motivo"], d["motivo"]
    assert "locked" not in d["motivo"], f"el motivo es el crudo de SQLite: {d['motivo']}"
    assert tardo < ESPERA_BLOQUEO_S / 2, \
        f"esperó {tardo:.1f} s: está usando la espera de la CLI ({ESPERA_BLOQUEO_S:g} s)"


def test_radio_base_ilegible_degrada_sin_500(client, tmp_path, monkeypatch, caplog):
    """Un archivo que no es SQLite (un .sqlite a medio copiar, por ejemplo)."""
    db = tmp_path / "rota.sqlite"
    db.write_bytes(b"esto no es una base de datos" * 20)
    monkeypatch.setenv("DJRADIO_DB", str(db))

    with caplog.at_level("WARNING", logger="bot_web"):
        d = client.get("/api/radio/biblioteca").json()
    assert (d["configurada"], d["estado"], d["total"]) == (True, "base-ilegible", 0)
    assert str(db) in d["motivo"], f"no dice qué base no pudo leer: {d['motivo']}"


# --------------------------------------------------------------- /api/radio/biblioteca

def test_biblioteca_del_motor_dice_lo_que_tiene_la_base(server, client, biblioteca):
    """Cada track con lo que pide §6: BPM con un decimal, las DOS notaciones de key, el `?`
    de confianza y la energía. Y lo que no es un track sigue listado, marcado."""
    d = client.get("/api/radio/biblioteca").json()
    assert (d["configurada"], d["estado"], d["motivo"]) == (True, "ok", None)
    assert d["total"] == len(CATALOGO), "la biblioteca lista todo lo de la base, también el loop"

    tracks = _tracks_del_motor(biblioteca["db"])
    por_id = {t["id"]: t for t in d["tracks"]}

    uno = por_id[server._radio_id(biblioteca["rutas"]["uno.wav"])]
    assert uno == {
        "id": server._radio_id(biblioteca["rutas"]["uno.wav"]),
        "label": "Artista A — Uno", "titulo": "Uno", "artista": "Artista A",
        "bpm": 128.4, "camelot": "8A", "tonalidad": "Am", "key_dudosa": False,
        "energia": round(float(tracks["uno.wav"].energy), 3),
        "energia_pct": percentil_energia(tracks["uno.wav"].energy),
        "dur": 240.0, "es_track": True,
        "audio": f"/api/radio/audio/{server._radio_id(biblioteca['rutas']['uno.wav'])}",
    }

    # El `?` de la key: unánime (3/3) no lo lleva, 2/3 y "no se midió" sí. Es la regla de
    # `cli.key_dudosa`, y se compara contra ELLA para que moverla mueva el test.
    dudosas = {t["label"]: t["key_dudosa"] for t in d["tracks"]}
    assert dudosas == {"Artista A — Uno": key_dudosa("3/3"), "Artista B — Dos": key_dudosa("2/3"),
                       "Artista C — Tres": key_dudosa(None), "Artista D — Cuatro": key_dudosa("0/0"),
                       "Artista E — Loop": key_dudosa("3/3"), "Artista F — Cinco": key_dudosa("3/3")}
    assert (dudosas["Artista A — Uno"], dudosas["Artista B — Dos"]) == (False, True), \
        "la regla del `?` se invirtió o dejó de distinguir el acuerdo unánime"

    # Una key que no es Camelot no se traduce ni se dibuja: dato ausente (§6).
    cuatro = por_id[server._radio_id(biblioteca["rutas"]["cuatro.wav"])]
    assert (cuatro["camelot"], cuatro["tonalidad"]) == (None, None)

    loop = por_id[server._radio_id(biblioteca["rutas"]["loop.wav"])]
    assert (loop["dur"], loop["es_track"]) == (10.0, False), \
        "el loop tiene que listarse marcado como lo que no es un track"


def _energias_que_imprime_la_cli(db, capsys, labels) -> dict:
    """El percentil de energía que la TERMINAL muestra para cada track, leído de la tabla
    que imprime `python -m motor list` sobre esa base.

    Se lee la salida real y no se llama a `percentil_energia`: lo que hay que proteger es
    que la pantalla y la consola digan el MISMO número, y un test que llamara a la misma
    función que llama el endpoint no se enteraría si la tabla de la CLI cambiara de cuenta.
    """
    from motor.cli import main

    assert main(["--db", str(db), "list"]) == 0
    impreso = capsys.readouterr().out
    # La fila termina en `{percentil:7d}  {label}`: se corta el label y se toma el último
    # número. El label lleva espacios y un guion largo, así que se busca por el final.
    leidos = {}
    for linea in impreso.splitlines():
        for label in labels:
            if linea.endswith(f"  {label}"):
                leidos[label] = int(linea[:-len(label)].split()[-1])
    assert len(leidos) == len(labels), f"no pude leer la tabla de `list`:\n{impreso}"
    return leidos


def test_la_energia_de_la_pantalla_es_la_que_imprime_la_terminal(server, client, biblioteca,
                                                                 capsys):
    """El percentil 0-100 de /api/radio/* contra el de `python -m motor list`, track por
    track, en la biblioteca y en cada paso del set (decisión del dueño 2026-09-21: en
    pantalla se muestra el percentil, no el 0..1 crudo)."""
    d = client.get("/api/radio/biblioteca").json()
    en_pantalla = {t["label"]: t["energia_pct"] for t in d["tracks"]}
    en_consola = _energias_que_imprime_la_cli(biblioteca["db"], capsys, list(en_pantalla))

    # 1) La terminal sigue imprimiendo lo de siempre. El valor esperado NO sale de
    #    `percentil_energia` —sería la función bajo prueba comparándose consigo misma— sino
    #    de la cuenta que estaba escrita a mano en las tres tablas de la CLI antes de que
    #    fuera una función: `format(energy * 100, '.0f')`. Si el redondeo cambia (un `int()`
    #    que trunca, por ejemplo), esto falla aunque el endpoint y la CLI sigan de acuerdo.
    tracks = _tracks_del_motor(biblioteca["db"])
    como_siempre = {t.label: int(format(t.energy * 100, ".0f")) for t in tracks.values()}
    assert en_consola == como_siempre, "la tabla de `list` cambió el redondeo de la energía"

    # 2) Y la pantalla dice exactamente ese número.
    assert en_pantalla == en_consola, "la pantalla muestra otro percentil que la terminal"

    # Que sea un percentil 0-100 y no el 0..1 disfrazado: con este catálogo el más
    # energético queda arriba de 50 y el más tranquilo en 0.
    assert (max(en_consola.values()) > 50 and min(en_consola.values()) == 0), en_consola

    # Y el mismo número en cada paso del set, que es donde se lee mientras se arma.
    id_uno = server._radio_id(biblioteca["rutas"]["uno.wav"])
    pasos = client.get("/api/radio/set", params={"track": id_uno}).json()["pasos"]
    assert pasos, "sin pasos no hay nada que comparar"
    assert {p["track"]["label"]: p["track"]["energia_pct"] for p in pasos} == \
        {p["track"]["label"]: en_consola[p["track"]["label"]] for p in pasos}


def test_biblioteca_trae_las_curvas_y_los_defaults_del_motor(client, biblioteca):
    """La pantalla dibuja los controles con esto: tiene que ser lo que dice el motor y no
    una copia en el server (mismo argumento que `test_set_sin_parametros_...`, y el mismo
    valor: `config_default` se compara contra el `config` que devuelve /api/radio/set)."""
    from motor.cli import LEYENDA_KEY
    from motor.energia import CURVES

    d = client.get("/api/radio/biblioteca").json()
    por_defecto = RadioConfig()
    assert d["opciones"] == {
        "curvas": list(CURVES),
        "config_default": {"largo": por_defecto.length, "curva": por_defecto.curve,
                           "artist_gap": por_defecto.artist_gap,
                           "mmr_lambda": por_defecto.mmr_lambda,
                           "semilla": por_defecto.seed,
                           "randomness": por_defecto.randomness},
        "leyenda_key": LEYENDA_KEY,
    }
    usado = client.get("/api/radio/set", params={"track": "uno"}).json()["config"]
    assert d["opciones"]["config_default"] == usado, \
        "los defaults que dibuja la pantalla no son los que el motor termina usando"


def test_biblioteca_sin_el_paquete_motor_no_inventa_curvas(client, biblioteca, monkeypatch):
    """Sin motor no hay curvas ni defaults que ofrecer: `null`, no un juego escrito a mano
    (§6 — un dato que miente es peor que uno ausente)."""
    monkeypatch.setitem(sys.modules, "motor.store", None)
    d = client.get("/api/radio/biblioteca").json()
    assert (d["estado"], d["opciones"]) == ("sin-motor", None)


def test_las_opciones_sin_el_paquete_motor_son_none(server, monkeypatch):
    """`_radio_opciones` con el import roto: None, ni explota ni inventa.

    Va directo a la función porque el endpoint no la llama cuando el estado ya es
    `sin-motor`, así que su `except ImportError` no lo ejecutaba ningún test: romperlo
    pasaba la suite entera. Se rompe `motor.energia` y no `motor.store` a propósito —
    `_radio_opciones` no importa `store`, y el caso real (una imagen sin `motor/`) los
    voltea a todos.
    """
    monkeypatch.setitem(sys.modules, "motor.energia", None)
    assert server._radio_opciones() is None


# --------------------------------------------------------------- /api/radio/set

def test_set_sin_parametros_usa_los_defaults_del_motor(client, biblioteca):
    """Los defaults salen de `RadioConfig`, no de la firma del endpoint. Se comparan contra
    la dataclass: si alguien copia un 20 en server.py y el motor cambia, esto rompe."""
    d = client.get("/api/radio/set", params={"track": "uno"}).json()
    por_defecto = RadioConfig()
    assert d["config"] == {"largo": por_defecto.length, "curva": por_defecto.curve,
                           "artist_gap": por_defecto.artist_gap,
                           "mmr_lambda": por_defecto.mmr_lambda, "semilla": por_defecto.seed,
                           "randomness": por_defecto.randomness}
    assert d["pedidos"] == por_defecto.length


def test_set_completo_trae_el_porque_que_dio_el_motor(server, client, biblioteca):
    """El set, paso por paso, igual al que arma `build_set` con la misma config: mismos
    tracks, mismo orden y el MISMO motivo (§6). Nada se recalcula en el server."""
    tracks = _tracks_del_motor(biblioteca["db"])
    id_uno = server._radio_id(biblioteca["rutas"]["uno.wav"])
    d = client.get("/api/radio/set", params={"track": id_uno, "largo": 4}).json()

    esperado = build_set(tracks["uno.wav"], list(tracks.values()), RadioConfig(length=4))
    assert [p["track"]["label"] for p in d["pasos"]] == [t.label for t in esperado.tracks]
    assert [p["motivo"] for p in d["pasos"]] == esperado.reasons(), \
        "el motivo de la pantalla no es el que calculó el motor"
    assert (d["total"], d["completo"], d["corte"]) == (4, True, None)

    # Y el formato de §6 de verdad, no solo "igual a lo que devolvió build_set".
    assert d["pasos"][0]["motivo"] == "semilla | 8A | 128.4 BPM"
    assert d["pasos"][0]["es_semilla"] is True
    salto = d["pasos"][1]
    assert salto["es_semilla"] is False
    # La forma de §6: salto con signo y un decimal, y las dos keys con su relación. La
    # lectura de octava puede meterse en el medio ("(doble tiempo)"), por eso no se exige
    # el "|" pegado al BPM.
    assert (salto["motivo"].startswith(("+", "-")) and "% BPM" in salto["motivo"]
            and " → " in salto["motivo"] and salto["motivo"].endswith(")")), \
        f"el renglón del salto no tiene la forma '+1.8% BPM | 8A → 9A (vecino)': {salto['motivo']}"
    assert salto["transicion"]["from_bpm"] == 128.4
    assert salto["transicion"]["to_bpm"] == salto["track"]["bpm"]

    # `cinco` es la transición que distingue pedirle el motivo al motor de recalcularlo acá:
    # el motor la mide contra la lectura de octava que usó la compuerta de ±8% (64.2 × 2 =
    # 128.4 → "doble tiempo"), y una resta cruda de BPM daría -50%. Si el porcentaje que se
    # muestra sale de una cuenta distinta de la que decide si el track entra, la pantalla
    # termina diciendo un número sobre una transición que el motor midió con otro.
    a_cinco = next(p for p in d["pasos"] if p["track"]["titulo"] == "Cinco")
    assert "doble tiempo" in a_cinco["motivo"], \
        f"no dice que la transición se apoya en doble tiempo: {a_cinco['motivo']}"
    tr = a_cinco["transicion"]
    ingenuo = (tr["to_bpm"] - tr["from_bpm"]) / tr["from_bpm"] * 100
    assert abs(tr["bpm_delta_pct"] - ingenuo) > 8.0, \
        (f"el salto informado ({tr['bpm_delta_pct']:+.1f}%) es una resta cruda de BPM "
         f"({ingenuo:+.1f}%): se está recalculando en vez de usar el del motor")

    # El loop nunca se propone, y la resta se explica (es la única forma de entender por qué
    # la biblioteca tiene 5 archivos y el set eligió entre 3).
    assert "Loop" not in [p["track"]["titulo"] for p in d["pasos"]]
    assert d["fragmentos"] == 1
    assert d["aviso_fragmentos"] == aviso_fragmentos(1, "La radio ignoró")


def test_set_cortado_dice_el_motivo_y_el_titular_del_motor(server, client, biblioteca):
    """Se piden 6 y solo hay 4 mezclables: el corte tiene que ser el del motor, con el
    titular que ya escribe la CLI, y no un set estirado ni un 500."""
    tracks = _tracks_del_motor(biblioteca["db"])
    id_uno = server._radio_id(biblioteca["rutas"]["uno.wav"])
    d = client.get("/api/radio/set", params={"track": id_uno, "largo": 6}).json()

    esperado = build_set(tracks["uno.wav"], list(tracks.values()), RadioConfig(length=6))
    assert (d["total"], d["pedidos"], d["completo"]) == (4, 6, False)
    assert d["corte"] == {"codigo": esperado.stop, "titular": titular_corte(esperado.stop),
                          "detalle": esperado.stop_detail}
    # El código concreto importa: se arregla distinto que "artist_gap" o "biblioteca agotada".
    assert d["corte"]["codigo"] == "sin_candidatos_mezclables", \
        "quedaba un track sin usar (BPM 0, no mezcla): el corte no es 'biblioteca agotada'"
    assert d["corte"]["titular"] == ("NO HAY MÁS TRACKS COMPATIBLES: ninguno de los que "
                                     "quedan entra en la tolerancia de BPM")
    assert "8" in d["corte"]["detalle"], f"el detalle no dice la tolerancia: {d['corte']['detalle']}"


def test_set_rechaza_una_semilla_que_no_es_un_track(server, client, biblioteca):
    """Pedir la radio DESDE el loop es un error, no un set: todo el set se arma contra el
    BPM y la key de la semilla, y 10 s de audio no son una medición (§6)."""
    id_loop = server._radio_id(biblioteca["rutas"]["loop.wav"])
    r = client.get("/api/radio/set", params={"track": id_loop, "largo": 5})
    assert r.status_code == 400, f"armó un set desde un loop de 10 s: {r.json()}"
    d = r.json()
    assert "pasos" not in d, "devolvió pasos de un set que no tenía que armar"
    assert d["error"] == motivo_semilla_no_track(_tracks_del_motor(biblioteca["db"])["loop.wav"]), \
        "el motivo no es el del motor"
    assert f"{DURACION_MINIMA_TRACK_S:.0f} s" in d["error"], "no dice cuánto hace falta"
    assert "10.0 s" in d["error"], "no dice cuánto dura el archivo"


def test_set_resuelve_la_semilla_por_su_ruta_real(client, biblioteca):
    """Pasar la ruta del archivo tiene que seguir funcionando (es la forma que usa la CLI),
    aunque la API no consulte el disco para resolverla."""
    ruta = biblioteca["rutas"]["uno.wav"]
    formas = [str(ruta), str(ruta).replace("\\", "/")]
    if os.name == "nt":
        formas.append(str(ruta).upper())   # en Windows la caja no cambia de qué track hablás
    for forma in formas:
        r = client.get("/api/radio/set", params={"track": forma, "largo": 2})
        assert r.status_code == 200, f"la ruta {forma!r} no resolvió: {r.json()}"
        assert r.json()["semilla"]["label"] == "Artista A — Uno", \
            f"la ruta {forma!r} resolvió a otro track: {r.json()['semilla']}"


def test_set_no_toca_el_disco_con_la_ruta_que_le_manden(server, client, biblioteca, monkeypatch):
    """`track` viene de afuera: el endpoint no tiene CORS y escucha en localhost, así que
    cualquier página abierta en el navegador puede disparar este GET.

    Dos cosas que no pueden pasar, y las dos se verifican por separado:

    1. Que la respuesta distinga un archivo que EXISTE en el host de uno que no: eso es un
       oráculo de existencia de archivos, servido a quien sea.
    2. Que el server le pegue al filesystem con esa cadena. Con una UNC (`\\\\host\\share\\x`)
       un `stat` en Windows es un intento de conexión SMB saliente, o sea que la página de
       enfrente elige a qué host se conecta el server.
    """
    # Las sondas se arman ANTES de poner los espías: `Path.resolve()` también le pega al
    # disco, y si no, el test se acusa a sí mismo.
    existe = str(Path(server.__file__).resolve())          # un archivo que SÍ está en el host
    no_existe = str(Path(existe).parent / "no-existe-zz.ini")
    unc = r"\\127.0.0.1\share\x.wav"
    assert Path(existe).is_file() and not Path(no_existe).exists(), \
        "las sondas no son lo que el test cree: una tiene que existir y la otra no"

    espiados: list[str] = []
    is_file_real, stat_real = Path.is_file, os.stat

    def espiar_is_file(self, *a, **k):
        espiados.append(str(self))
        return is_file_real(self, *a, **k)

    def espiar_stat(ruta, *a, **k):
        espiados.append(str(ruta))
        return stat_real(ruta, *a, **k)

    monkeypatch.setattr(Path, "is_file", espiar_is_file)
    monkeypatch.setattr(os, "stat", espiar_stat)

    errores = {}
    for sonda in (existe, no_existe, unc):
        r = client.get("/api/radio/set", params={"track": sonda})
        assert r.status_code == 400, f"{sonda!r} no tenía que resolver a nada"
        errores[sonda] = r.json()["error"]
        assert any(m in errores[sonda] for m in ("Ningún track", "no elijo uno")), \
            f"{sonda!r} contestó algo que habla del disco: {errores[sonda]}"

    def sin_la_sonda(mensaje: str, sonda: str) -> str:
        """El mensaje sin la ruta que se preguntó, en crudo o escapada por `!r`."""
        return mensaje.replace(repr(sonda), "<sonda>").replace(sonda, "<sonda>")

    assert sin_la_sonda(errores[existe], existe) == sin_la_sonda(errores[no_existe], no_existe), \
        ("la respuesta distingue un archivo que existe de uno que no: eso es un oráculo de "
         f"existencia\n  existe:    {errores[existe]}\n  no existe: {errores[no_existe]}")

    for sonda in (existe, no_existe, unc):
        tocados = [p for p in espiados if sonda.casefold() in p.casefold()]
        assert not tocados, f"el server le pegó al disco con {sonda!r}: {tocados}"


def test_set_con_semilla_inexistente_o_ausente_no_inventa_una(client, biblioteca):
    r = client.get("/api/radio/set", params={"track": "no-esta-en-la-base"})
    assert r.status_code == 400
    assert "no-esta-en-la-base" in r.json()["error"] and "Ningún track" in r.json()["error"]

    r = client.get("/api/radio/set", params={"track": "   "})
    assert r.status_code == 400
    assert "semilla" in r.json()["error"].lower(), r.json()

    # Un fragmento que coincide con varios no elige uno: la regla de `cli.resolver_track`.
    r = client.get("/api/radio/set", params={"track": ".wav"})
    assert r.status_code == 400
    assert "no elijo uno" in r.json()["error"], r.json()


def test_set_con_una_curva_que_no_existe_lo_dice_el_motor(client, biblioteca):
    r = client.get("/api/radio/set", params={"track": "uno", "curva": "montaña rusa"})
    assert r.status_code == 400
    assert "curva desconocida" in r.json()["error"], r.json()


# --------------------------------------------------------------- /api/radio/audio/{id}

def test_audio_sirve_el_archivo_de_ese_track(server, client, biblioteca):
    for nombre in ("dos.wav", "cuatro.wav"):   # ninguno es el primero: un "sirve cualquiera" no pasa
        tid = server._radio_id(biblioteca["rutas"][nombre])
        r = client.get(f"/api/radio/audio/{tid}")
        assert r.status_code == 200
        assert r.content == biblioteca["audios"][nombre], f"sirvió otro archivo que el de {nombre}"


def test_audio_responde_a_range(server, client, biblioteca):
    completo = biblioteca["audios"]["uno.wav"]
    tid = server._radio_id(biblioteca["rutas"]["uno.wav"])
    r = client.get(f"/api/radio/audio/{tid}", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 100-199/{len(completo)}"
    assert r.content == completo[100:200]


def test_audio_id_inexistente_da_404(client, biblioteca):
    for tid in ("0" * 16, "12345", "0"):
        r = client.get(f"/api/radio/audio/{tid}")
        assert r.status_code == 404, f"id {tid!r} no debería servir nada"
        assert r.json() == {"error": "track no encontrado"}


def test_audio_de_un_archivo_movido_no_se_confunde_con_un_id_invalido(server, client, biblioteca):
    """El track está en la base y el archivo no está: es otra cosa que "no encontrado", y el
    DJ la arregla distinto (en Docker es la carpeta que no se montó)."""
    tid = server._radio_id(biblioteca["rutas"]["tres.wav"])
    biblioteca["rutas"]["tres.wav"].unlink()
    r = client.get(f"/api/radio/audio/{tid}")
    assert r.status_code == 404
    assert r.json()["error"] != "track no encontrado"
    assert "scan" in r.json()["error"], r.json()


def test_audio_deja_de_servir_un_id_de_una_base_que_ya_no_esta(server, client, biblioteca,
                                                               tmp_path, monkeypatch):
    """Si la carga de la biblioteca FALLA, el índice id→ruta se reemplaza igual (queda
    vacío). Si no, el server seguiría sirviendo audio de una base que ya no es la
    configurada: los archivos siguen en disco, así que el `exists()` no salva nada.
    """
    tid = server._radio_id(biblioteca["rutas"]["uno.wav"])
    assert client.get(f"/api/radio/audio/{tid}").status_code == 200, "no cargó el índice"

    # La base configurada desaparece (o DJRADIO_DB pasa a apuntar a otra cosa).
    monkeypatch.setenv("DJRADIO_DB", str(tmp_path / "otra" / "biblioteca.sqlite"))
    assert client.get("/api/radio/biblioteca").json()["estado"] == "sin-base"

    assert biblioteca["rutas"]["uno.wav"].exists(), "el archivo sigue en disco: ese no es el filtro"
    r = client.get(f"/api/radio/audio/{tid}")
    assert r.status_code == 404, "sirvió audio de una base que ya no está configurada"
    assert r.json() == {"error": "track no encontrado"}


def test_un_valor_no_finito_viaja_como_null_y_no_como_NaN(server, biblioteca):
    """`json.dumps` escribe `NaN`/`Infinity`, que son JSON inválido: `JSON.parse` en el
    front explota con la respuesta entera. Y §6 pide el dato ausente antes que el que
    miente — `bpm_delta_pct` es NaN justo cuando el BPM no se pudo medir.

    Se prueba sobre el serializador, no solo sobre `_num`: `build_set` hoy no produce una
    transición con NaN (la compuerta de ±8% saca a los de BPM inválido antes), así que el
    caso no aparece armando un set, pero la forma del dato sí puede llegar de ahí.
    """
    from motor.radio import SetStep, Transition

    t = _tracks_del_motor(biblioteca["db"])["uno.wav"]
    tr = Transition(from_bpm=0.0, to_bpm=128.4, bpm_delta_pct=float("nan"), bpm_octave="",
                    from_key="8A", to_key="8A", key_relation="mismo", key_compat=1.0,
                    mixability=float("inf"), musical_fit=0.5, total=float("-inf"),
                    energy=0.5, energy_goal=0.35)
    paso = server._radio_paso(1, SetStep(t, tr))

    assert paso["transicion"]["bpm_delta_pct"] is None, "un NaN llegó al JSON como NaN"
    assert paso["transicion"]["mezclabilidad"] is None, "un inf llegó al JSON como Infinity"
    assert paso["transicion"]["score"] is None, "un -inf llegó al JSON como -Infinity"
    assert paso["transicion"]["encaje_musical"] == 0.5, "se comió un número que sí era válido"
    # Y el JSON resultante tiene que ser JSON de verdad: un parser estricto no acepta NaN.
    json.loads(json.dumps(paso),
               parse_constant=lambda c: pytest.fail(f"el JSON trae la constante {c}"))


def test_audio_no_sirve_archivos_fuera_de_la_biblioteca(server, client, biblioteca):
    """El id NO es una ruta: ni relativa ni absoluta, ni aunque el archivo exista y hasta
    aunque ESTÉ en la biblioteca — a esos se llega por su id, no por su ruta."""
    servidor = Path(server.__file__).resolve()
    audio_real = biblioteca["rutas"]["uno.wav"]
    base = biblioteca["db"]
    intentos = [
        "server.py",
        "..%2Fserver.py",
        "%2E%2E%2F%2E%2E%2Fserver.py",
        quote(str(servidor), safe=""),
        quote(str(audio_real), safe=""),
        quote(audio_real.as_posix(), safe=""),
        quote(str(base), safe=""),
    ]
    prohibidos = (servidor.read_bytes(), biblioteca["audios"]["uno.wav"], base.read_bytes())
    for intento in intentos:
        r = client.get(f"/api/radio/audio/{intento}")
        assert r.status_code == 404, f"{intento!r} respondió {r.status_code}"
        assert r.content not in prohibidos, f"{intento!r} filtró un archivo"


# ------------------------------------------------------ export del set (/api/radio/set.m3u8)
#
# El .m3u8 es lo que el DJ se lleva a Rekordbox: tiene que ser EL set que la pantalla
# mostró, en ese orden y con rutas que resuelvan en su PC. Los esperados se escriben acá a
# mano (renglón por renglón, con CRLF) a partir de lo que armó `build_set` y de las rutas
# reales del fixture — no con `motor.export.m3u8_text`, que es el código bajo prueba.


def _m3u8_esperado(tracks) -> bytes:
    """El archivo que tiene que salir para esos tracks, escrito a mano: cabecera, y por track
    EXTINF (segundos enteros, label), DJRADIO (BPM a un decimal) y la ruta ABSOLUTA."""
    renglones = ["#EXTM3U"]
    for t in tracks:
        renglones += [f"#EXTINF:{round(t.duration)},{t.artist} — {t.title}",
                      f"#DJRADIO:bpm={t.bpm:.1f} key={t.key} energy={t.energy:.2f}",
                      str(t.path)]
    return "".join(f"{r}\r\n" for r in renglones).encode("utf-8")


def test_m3u8_es_el_set_de_la_pantalla_byte_a_byte(server, client, biblioteca):
    """Mismo set que /api/radio/set, en el mismo orden, con CRLF, sin BOM y con las rutas
    absolutas del scan, que existen en disco (lo que pide la #15: sin archivos rotos)."""
    tracks = _tracks_del_motor(biblioteca["db"])
    id_uno = server._radio_id(biblioteca["rutas"]["uno.wav"])
    params = {"track": id_uno, "largo": 4, "curva": "warmup"}

    pantalla = client.get("/api/radio/set", params=params).json()
    r = client.get("/api/radio/set.m3u8", params=params)
    assert r.status_code == 200, r.text

    orden = [p["track"]["id"] for p in pantalla["pasos"]]
    por_id = {server._radio_id(t.path): t for t in tracks.values()}
    armado = [por_id[i] for i in orden]
    assert [t.path.name for t in armado] != sorted(t.path.name for t in armado), \
        "el set salió en orden alfabético: así el test no ve un export que reordena"

    assert r.content == _m3u8_esperado(armado), \
        f"el .m3u8 no es el set de la pantalla:\n{r.content.decode('utf-8')}"
    assert not r.content.startswith(b"\xef\xbb\xbf"), "BOM pegado al #EXTM3U"
    rutas = [ln for ln in r.content.decode("utf-8").split("\r\n") if ln and not ln.startswith("#")]
    assert all(Path(ruta).is_absolute() and Path(ruta).is_file() for ruta in rutas), \
        f"hay rutas que no resuelven a un archivo: {rutas}"


def test_m3u8_es_el_mismo_archivo_que_escribe_la_cli(server, client, biblioteca, tmp_path,
                                                    capsys):
    """`python -m motor radio --m3u8` y el botón de la pantalla tienen que dar el mismo
    archivo para el mismo set: si no, el DJ tiene dos exports que dicen cosas distintas."""
    from motor.cli import main

    salida = tmp_path / "cli.m3u8"
    ruta_uno = biblioteca["rutas"]["uno.wav"]
    codigo = main(["--db", str(biblioteca["db"]), "radio", str(ruta_uno), "--largo", "4",
                   "--m3u8", str(salida)])
    assert codigo == 0, capsys.readouterr().out

    r = client.get("/api/radio/set.m3u8",
                   params={"track": server._radio_id(ruta_uno), "largo": 4})
    assert r.status_code == 200, r.text
    assert r.content == salida.read_bytes(), \
        (f"la API y la CLI exportan distinto.\nAPI:\n{r.content!r}\n"
         f"CLI:\n{salida.read_bytes()!r}")


def test_m3u8_cabeceras_de_descarga(server, client, biblioteca):
    """Texto UTF-8 (no se interpreta), que el navegador lo BAJE con un nombre que diga qué
    set es, y que no quede cacheado: el mismo pedido puede dar otro set tras un scan."""
    id_uno = server._radio_id(biblioteca["rutas"]["uno.wav"])
    r = client.get("/api/radio/set.m3u8", params={"track": id_uno, "curva": "flat"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.headers["content-disposition"] == (
        "attachment; filename=\"DJ Radio - Artista A - Uno - flat.m3u8\"; "
        "filename*=UTF-8''DJ%20Radio%20-%20Artista%20A%20%E2%80%94%20Uno%20-%20flat.m3u8")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("label, esperado", [
    # Todo lo que Windows no acepta en un nombre se va, y la barra NO arma una carpeta.
    ('AC/DC: "Back" <In> Black?*|\\x', "DJ Radio - AC DC Back In Black x - peak.m3u8"),
    # Un salto de línea en el título no llega al nombre ni a la cabecera HTTP.
    ("Uno\r\nDos", "DJ Radio - Uno Dos - peak.m3u8"),
    # Rutas relativas disfrazadas de título: sin separadores no hay a dónde subir.
    ("../../Windows/system32", "DJ Radio - .. .. Windows system32 - peak.m3u8"),
    # Unicode se conserva: es un nombre válido en Windows y es el título real.
    ("Señor Coconut", "DJ Radio - Señor Coconut - peak.m3u8"),
])
def test_nombre_del_m3u8_es_seguro_en_windows(server, label, esperado):
    assert server._nombre_m3u8(label, "peak") == esperado


def test_nombre_del_m3u8_sin_punto_final_ni_largo_infinito(server):
    """Windows le borra al nombre el punto o espacio final (el archivo "cambia de nombre"
    solo) y no acepta rutas más largas que MAX_PATH."""
    assert server._nombre_m3u8("x", "peak. .") == "DJ Radio - x - peak.m3u8"
    nombre = server._nombre_m3u8("y" * 500, "peak")
    assert nombre == "DJ Radio - " + "y" * (server._NOMBRE_MAX - len("DJ Radio - ")) + ".m3u8"


def test_content_disposition_con_unicode_tiene_las_dos_formas(server):
    """`filename` ASCII para clientes viejos, `filename*` UTF-8 con el nombre real."""
    cd = server._content_disposition("DJ Radio - Señor Coconut - peak.m3u8")
    assert cd == ("attachment; filename=\"DJ Radio - Senor Coconut - peak.m3u8\"; "
                  "filename*=UTF-8''DJ%20Radio%20-%20Se%C3%B1or%20Coconut%20-%20peak.m3u8")


def test_content_disposition_ascii_no_trae_caracteres_que_windows_rechaza(server):
    """"＜" y "：" de ancho completo son válidos en un nombre de Windows, pero NFKD los vuelve
    "<" y ":". El nombre de respaldo ASCII se sacaba antes de normalizar y los dejaba pasar."""
    nombre = server._nombre_m3u8("＜x＞ ：y", "peak")
    cd = server._content_disposition(nombre)
    ascii_ = cd.split('filename="', 1)[1].split('"', 1)[0]
    assert ascii_ == "DJ Radio - x y - peak.m3u8", ascii_
    assert not set(ascii_) & set('<>:"/\\|?*'), ascii_


def test_m3u8_semilla_que_no_es_track_es_400_con_el_motivo_del_motor(server, client,
                                                                     biblioteca):
    id_loop = server._radio_id(biblioteca["rutas"]["loop.wav"])
    r = client.get("/api/radio/set.m3u8", params={"track": id_loop})
    assert r.status_code == 400, f"exportó un set armado desde un loop: {r.text}"
    assert r.json()["error"] == motivo_semilla_no_track(
        _tracks_del_motor(biblioteca["db"])["loop.wav"]), "el motivo no es el del motor"
    assert "content-disposition" not in r.headers, "un error no se baja como archivo"


def test_m3u8_pedidos_invalidos_son_400_y_no_un_archivo(client, biblioteca):
    for params, pista in (({}, "semilla"), ({"track": "no-existe-este-track"}, None),
                          ({"track": "Uno", "curva": "montaña"}, "montaña")):
        r = client.get("/api/radio/set.m3u8", params=params)
        assert r.status_code == 400, f"{params}: {r.status_code} {r.text}"
        assert r.json()["error"], f"{params}: 400 sin motivo"
        if pista:
            assert pista in r.json()["error"], f"{params}: el motivo no dice qué falló"
        assert "content-disposition" not in r.headers


def test_m3u8_sin_base_es_409_con_el_motivo_y_no_un_200(client, tmp_path, monkeypatch):
    """Sin base no hay set: un 200 con JSON el navegador lo guardaría como `.m3u8`."""
    fantasma = tmp_path / "no-existe" / "biblioteca.sqlite"
    monkeypatch.setenv("DJRADIO_DB", str(fantasma))
    r = client.get("/api/radio/set.m3u8", params={"track": "Uno"})
    assert r.status_code == 409, r.text
    d = r.json()
    assert (d["configurada"], d["estado"]) == (False, "sin-base")
    assert str(fantasma) in d["error"] and d["error"] == d["motivo"], d
    assert "content-disposition" not in r.headers


def test_m3u8_se_niega_si_el_set_ya_no_es_el_de_la_pantalla(server, client, biblioteca):
    """`esperado` son los ids que la pantalla muestra. Si el re-armado da otro set (un scan
    entre medio), 409: bajar otro set con el mismo nombre sería un dato que miente (§6)."""
    id_uno = server._radio_id(biblioteca["rutas"]["uno.wav"])
    params = {"track": id_uno, "largo": 3}
    ids = [p["track"]["id"] for p in client.get("/api/radio/set", params=params).json()["pasos"]]

    ok = client.get("/api/radio/set.m3u8", params={**params, "esperado": ",".join(ids)})
    assert ok.status_code == 200, ok.text

    otro = [ids[0], ids[2], ids[1]]
    r = client.get("/api/radio/set.m3u8", params={**params, "esperado": ",".join(otro)})
    assert r.status_code == 409, "exportó un set distinto del que se le dijo que se mostró"
    assert (r.json()["esperado"], r.json()["armado"]) == (otro, ids)
    assert "content-disposition" not in r.headers
