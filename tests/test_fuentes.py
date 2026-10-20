"""f64: fuentes externas enchufables (`fuentes/`): registro, Deezer (preview para medir) y
Jamendo (búsqueda y descarga, solo con clave).

Sin red: las respuestas de Deezer y Jamendo están armadas a mano con la FORMA de sus APIs
(`fixtures/fuentes_externas.json`), con artistas y títulos ficticios. Lo esperado de la
identidad sale de la regla escrita en `fuentes/registro.py` (misma versión, artista en común,
duración a ±max(5 s, 3 %)), no del código bajo prueba. El BPM del plan B de la medición sale
del generador sintético (`motor.sintetico.click_track`), medido por el camino real.
"""
from __future__ import annotations

import io
import json
import logging
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import soundcloud_station as sc  # noqa: E402
import station_mezcla as sm  # noqa: E402
from fuentes import base, registro  # noqa: E402
from fuentes.deezer import DeezerProvider  # noqa: E402
from fuentes.jamendo import JamendoProvider  # noqa: E402

FX = json.loads((Path(__file__).parent / "fixtures" / "fuentes_externas.json").read_text(encoding="utf-8"))
CLAVE = "CLAVE_JAMENDO_SECRETA_0123456789"


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeDeezer:
    """`requests.get` de api.deezer.com: la respuesta grabada por consulta exacta."""

    def __init__(self, por_q=None, error=None):
        self.por_q = FX["deezer_por_q"] if por_q is None else por_q
        self.error = error
        self.pedidos = []

    def __call__(self, url, params=None, timeout=None):
        self.pedidos.append((url, dict(params or {})))
        if self.error is not None:
            if isinstance(self.error, Exception):
                raise self.error
            return self.error
        return Resp(200, self.por_q.get((params or {}).get("q"), {"data": [], "total": 0}))


def reg_deezer(http=None):
    return registro.Registry([DeezerProvider(http_get=http or FakeDeezer())])


def lecturas(titulo, uploader):
    return sc._lecturas(titulo, uploader)


# --- identidad: medir el tema equivocado es mentir ------------------------------------------

def test_identidad_acepta_el_tema_correcto():
    m = reg_deezer().find_preview(lecturas("Faro Lento", "Nadia Vellum"), 313.4)
    assert m.track is not None, m.motivo
    assert (m.track.id, m.track.title, m.track.artist) == ("900000101", "Faro Lento", "Nadia Vellum")
    assert m.track.preview_url == "https://cdnt-preview.dzcdn.net/api/1/1/a/b/c/0/faro-lento.mp3"
    assert m.motivo is None


def test_identidad_acepta_el_remix_cuando_se_pide_el_remix():
    m = reg_deezer().find_preview(lecturas("Faro Lento (Kessler Remix)", "Nadia Vellum"), 317.0)
    assert m.track is not None and m.track.id == "900000102", m.motivo


def test_identidad_rechaza_otro_artista():
    m = reg_deezer().find_preview(lecturas("Luz de Puerto", "Tomas Brisa"), 280.0)
    assert m.track is None
    assert m.motivo == "Deezer no tiene este tema"


def test_identidad_rechaza_remix_cuando_se_pide_el_original():
    m = reg_deezer().find_preview(lecturas("Hilo Rojo", "Nadia Vellum"), 401.0)
    assert m.track is None, "Deezer solo tiene el remix: medirlo en nombre del original es mentir"
    assert m.motivo == "Deezer solo tiene otra versión de este tema"


def test_identidad_rechaza_el_original_cuando_se_pide_el_extended():
    m = reg_deezer().find_preview(lecturas("Faro Lento (Extended Mix)", "Nadia Vellum"), 312.0)
    assert m.track is None
    assert m.motivo == "Deezer solo tiene otra versión de este tema"


def test_identidad_rechaza_si_la_duracion_no_cuadra():
    # 262 s contra 240 s: 9 %, dentro de `duraciones_cuadran` (±max(10 s, 10 %)) pero fuera de la
    # tolerancia de la medición (±max(5 s, 3 %) = ±7,9 s).
    m = reg_deezer().find_preview(lecturas("Sal", "Nadia Vellum"), 262.0)
    assert m.track is None
    assert m.motivo == "Deezer tiene un tema con ese nombre pero dura distinto (240 s contra 262 s): no lo mido"


def test_identidad_acepta_la_duracion_dentro_de_la_tolerancia():
    m = reg_deezer().find_preview(lecturas("Sal", "Nadia Vellum"), 246.5)
    assert m.track is not None and m.track.id == "900000401", m.motivo


def test_identidad_sin_resultados():
    m = reg_deezer().find_preview(lecturas("Tema Que No Existe", "Nadie Nunca"), 200.0)
    assert (m.track, m.motivo) == (None, "Deezer no tiene este tema")


def test_la_busqueda_de_deezer_es_la_estricta():
    http = FakeDeezer()
    reg_deezer(http).find_preview(lecturas("Faro Lento", "Nadia Vellum"), 312.0)
    assert [(u, p["q"]) for u, p in http.pedidos] == [("https://api.deezer.com/search", 'artist:"Nadia Vellum" track:"faro lento"')]


# --- cada falla con su motivo real ---------------------------------------------------------

@pytest.mark.parametrize("error, motivo, kind", [
    (requests.ReadTimeout("lento"), "Deezer no contestó", "timeout"),
    (requests.ConnectTimeout("lento"), "Deezer no contestó", "timeout"),
    (Resp(498, {}), "Deezer bloqueó (HTTP 498)", "bloqueo"),
    (Resp(429, {}), "Deezer bloqueó (HTTP 429)", "bloqueo"),
    (Resp(404, {}), "Deezer no tiene este tema", "no_encontrado"),
    (Resp(200, FX["deezer_por_q"]["cuota"]), "Deezer bloqueó (cuota de pedidos agotada)", "bloqueo"),
    (requests.ConnectionError("sin red"), "Deezer falló (ConnectionError)", "error"),
    (Resp(200, ValueError("no json")), "Deezer falló (respuesta que no es JSON)", "error"),
])
def test_cada_falla_de_deezer_tiene_su_motivo(error, motivo, kind):
    with pytest.raises(base.ProviderError) as e:
        reg_deezer(FakeDeezer(error=error)).search("deezer", "x")
    assert (e.value.motivo, e.value.kind) == (motivo, kind)


def test_un_timeout_no_se_cuenta_como_no_lo_tiene_en_el_preview():
    m = reg_deezer(FakeDeezer(error=requests.ReadTimeout("lento"))).find_preview(lecturas("Faro Lento", "Nadia Vellum"), 312.0)
    assert (m.track, m.motivo) == (None, "Deezer no contestó")


def test_un_498_en_el_preview_dice_bloqueo():
    m = reg_deezer(FakeDeezer(error=Resp(498, {}))).find_preview(lecturas("Faro Lento", "Nadia Vellum"), 312.0)
    assert (m.track, m.motivo) == (None, "Deezer bloqueó (HTTP 498)")


class Rota(base.ExternalAudioSourceProvider):
    name, display, capabilities = "rota", "Rota", frozenset({"search"})

    def __init__(self, exc):
        self.exc = exc

    def search(self, query, limit=10):
        raise self.exc

    def get_details(self, track_id):
        raise self.exc


@pytest.mark.parametrize("exc, motivo", [
    (requests.ReadTimeout("x"), "Rota no contestó"),
    (TimeoutError(), "Rota no contestó"),
    (KeyError("data"), "Rota falló (KeyError)"),
])
def test_el_registro_normaliza_fallas_de_un_proveedor_cualquiera(exc, motivo):
    with pytest.raises(base.ProviderError) as e:
        registro.Registry([Rota(exc)]).search("rota", "x")
    assert e.value.motivo == motivo


def test_fuente_desconocida():
    with pytest.raises(base.ProviderError) as e:
        registro.Registry([]).search("napster", "x")
    assert e.value.motivo == "napster falló (fuente desconocida)"


# --- Deezer no baja nada --------------------------------------------------------------------

def test_deezer_no_tiene_descarga(tmp_path):
    with pytest.raises(NotImplementedError, match="Deezer no permite descargas"):
        DeezerProvider(http_get=FakeDeezer()).download_item("900000101", tmp_path)
    with pytest.raises(base.DownloadNotAllowed) as e:
        reg_deezer().download("deezer", "900000101", tmp_path)
    assert e.value.motivo == "Deezer no permite descargas"
    assert list(tmp_path.iterdir()) == []


def test_deezer_mapea_licencia_y_origen():
    t = reg_deezer().search("deezer", 'artist:"Nadia Vellum" track:"faro lento"')[0]
    assert (t.license, t.origin, t.downloadable, t.duration_s) == \
        ("no declarado", "https://www.deezer.com/track/900000101", False, 312.0)


# --- Jamendo ---------------------------------------------------------------------------------

class FakeJamendo:
    def __init__(self, body=None, error=None):
        self.body = FX["jamendo_busqueda"] if body is None else body
        self.error = error
        self.pedidos = []

    def __call__(self, url, params=None, timeout=None):
        self.pedidos.append((url, dict(params or {})))
        if isinstance(self.error, Exception):
            raise self.error
        if self.error is not None:
            return self.error
        p = params or {}
        if "id" in p:
            res = [r for r in self.body["results"] if r["id"] == p["id"]]
            return Resp(200, {"headers": {"status": "success", "code": 0}, "results": res})
        return Resp(200, self.body)


class Bajada:
    """El stream de la descarga: anota cada url y, mientras baja, qué hay en la carpeta."""

    def __init__(self, dest: Path, data=b"ID3-audio-de-prueba" * 100, frenar=None):
        self.dest, self.data, self.frenar = dest, data, frenar
        self.urls, self.durante = [], []
        self.en_vuelo = self.max_en_vuelo = 0
        self.lock = threading.Lock()

    def __call__(self, url):
        with self.lock:
            self.urls.append(url)
            self.en_vuelo += 1
            self.max_en_vuelo = max(self.max_en_vuelo, self.en_vuelo)
        try:
            if self.frenar is not None:
                self.frenar.wait(10)
            yield self.data[:10]
            # el .part tiene el buffer sin volcar: de él solo importa que existe; del final, que está vacío
            self.durante.append(sorted((p.name, None if p.name.endswith(".part") else p.stat().st_size)
                                       for p in self.dest.iterdir()))
            yield self.data[10:]
        finally:
            with self.lock:
                self.en_vuelo -= 1


def jamendo(http=None, stream=None, clave=CLAVE):
    return JamendoProvider(client_id=clave, http_get=http or FakeJamendo(), http_stream=stream)


def test_jamendo_sin_clave_no_rompe_y_lo_dice(caplog, monkeypatch):
    monkeypatch.delenv("JAMENDO_CLIENT_ID", raising=False)
    http = FakeJamendo()
    reg = registro.Registry([DeezerProvider(http_get=FakeDeezer()), JamendoProvider(http_get=http)])
    with caplog.at_level(logging.INFO, logger="bot_web"):
        assert [p.name for p in reg.enabled()] == ["deezer"]
        assert [p.name for p in reg.enabled("download")] == []
    assert "Jamendo: sin clave" in caplog.text
    with pytest.raises(base.MissingKey) as e:
        reg.search("jamendo", "campo")
    assert (e.value.motivo, e.value.kind) == ("Jamendo: sin clave", "sin_clave")
    assert http.pedidos == [], "sin clave no se le pregunta a Jamendo"


def test_jamendo_con_clave_desde_el_entorno(monkeypatch):
    monkeypatch.setenv("JAMENDO_CLIENT_ID", CLAVE)
    http = FakeJamendo()
    p = JamendoProvider(http_get=http)
    assert p.enabled() is True
    p.search("campo", 5)
    assert http.pedidos == [("https://api.jamendo.com/v3.0/tracks/",
                             {"client_id": CLAVE, "format": "json", "search": "campo", "limit": 5})]


def test_jamendo_mapea_licencia_origen_y_descargable():
    ts = registro.Registry([jamendo()]).search("jamendo", "campo")
    assert [(t.id, t.title, t.artist, t.license, t.origin, t.downloadable, t.duration_s) for t in ts] == [
        ("1500001", "Campo Abierto", "Coro de Niebla", "http://creativecommons.org/licenses/by-sa/3.0/",
         "https://www.jamendo.com/track/1500001", True, 254.0),
        ("1500002", "Campo Cerrado", "Coro de Niebla", "http://creativecommons.org/licenses/by-nc-nd/3.0/",
         "https://www.jamendo.com/track/1500002", False, 198.0),
        # Sin licencia ni página: "no declarado"; "true" como texto NO es permiso de descarga.
        ("1500003", "Campo Sin Datos", "Coro de Niebla", "no declarado", "no declarado", False, None),
    ]


def test_jamendo_nunca_muestra_la_clave(caplog):
    url_con_clave = f"https://api.jamendo.com/v3.0/tracks/?client_id={CLAVE}"
    for err in (requests.ConnectionError(f"Max retries exceeded with url: {url_con_clave}"), Resp(500, {}),
                Resp(200, {"headers": {"status": "failed", "code": 5, "error_message": f"bad client_id {CLAVE}"}})):
        with caplog.at_level(logging.DEBUG), pytest.raises(base.ProviderError) as e:
            registro.Registry([jamendo(FakeJamendo(error=err))]).search("jamendo", "x")
        assert CLAVE not in e.value.motivo and CLAVE not in str(e.value)
    assert CLAVE not in caplog.text
    assert e.value.motivo == "Jamendo bloqueó (código 5)"


def test_jamendo_baja_a_part_y_renombra(tmp_path):
    bajada = Bajada(tmp_path)
    ruta = registro.Registry([jamendo(stream=bajada)]).download("jamendo", "1500001", tmp_path, stem="Campo Abierto - Coro de Niebla")
    assert ruta == tmp_path / "Campo Abierto - Coro de Niebla.mp3"
    assert ruta.read_bytes() == bajada.data
    assert bajada.urls == ["https://prod-1.storage.jamendo.com/download/track/1500001/mp32/"]
    # Mientras bajaba: el .part con lo bajado y el nombre final reservado vacío.
    assert bajada.durante == [[("Campo Abierto - Coro de Niebla.mp3", 0), ("Campo Abierto - Coro de Niebla.mp3.part", None)]]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Campo Abierto - Coro de Niebla.mp3"], "quedó un .part"


def test_jamendo_no_pisa_un_archivo_que_ya_estaba(tmp_path):
    previo = tmp_path / "Campo Abierto.mp3"
    previo.write_bytes(b"el archivo del usuario")
    bajada = Bajada(tmp_path)
    ruta = jamendo(stream=bajada).download_item("1500001", tmp_path, stem="Campo Abierto")
    assert previo.read_bytes() == b"el archivo del usuario"
    assert ruta.name == "Campo Abierto (2).mp3" and ruta.read_bytes() == bajada.data


def test_jamendo_no_descargable_nunca_baja(tmp_path):
    bajada = Bajada(tmp_path)
    with pytest.raises(base.DownloadNotAllowed) as e:
        registro.Registry([jamendo(stream=bajada)]).download("jamendo", "1500002", tmp_path)
    assert e.value.motivo == "Jamendo no permite descargar este tema (lo decidió el autor)"
    assert bajada.urls == [] and list(tmp_path.iterdir()) == []


def test_jamendo_descargable_como_texto_tampoco_baja(tmp_path):
    bajada = Bajada(tmp_path)
    with pytest.raises(base.DownloadNotAllowed):
        jamendo(stream=bajada).download_item("1500003", tmp_path)
    assert bajada.urls == []


def test_jamendo_no_baja_de_otro_host(tmp_path):
    body = json.loads(json.dumps(FX["jamendo_busqueda"]))
    body["results"][0]["audiodownload"] = "https://127.0.0.1/download/track/1500001/mp32/"
    bajada = Bajada(tmp_path)
    with pytest.raises(base.DownloadNotAllowed) as e:
        jamendo(FakeJamendo(body), stream=bajada).download_item("1500001", tmp_path)
    assert e.value.motivo == "Jamendo no dio un link de descarga válido para este tema"
    assert bajada.urls == [] and list(tmp_path.iterdir()) == []


def test_jamendo_si_la_descarga_falla_no_deja_nada(tmp_path):
    def cortada(url):
        yield b"medio archivo"
        raise base.ProviderTimeout("Jamendo")
    with pytest.raises(base.ProviderTimeout):
        jamendo(stream=cortada).download_item("1500001", tmp_path, stem="Campo")
    assert list(tmp_path.iterdir()) == [], "quedó el .part o la reserva vacía"


def test_jamendo_como_mucho_dos_descargas_a_la_vez(tmp_path):
    frenar = threading.Event()
    bajada = Bajada(tmp_path, frenar=frenar)
    p = jamendo(stream=bajada)
    hilos = [threading.Thread(target=p.download_item, args=("1500001", tmp_path), kwargs={"stem": f"t{k}"})
             for k in range(4)]
    for h in hilos:
        h.start()
    t0 = time.monotonic()
    while bajada.en_vuelo < 2 and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    time.sleep(0.2)                              # si hubiera lugar para una tercera, ya habría entrado
    en_vuelo = bajada.en_vuelo
    frenar.set()
    for h in hilos:
        h.join(10)
    assert en_vuelo == 2 and bajada.max_en_vuelo == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["t0.mp3", "t1.mp3", "t2.mp3", "t3.mp3"]


# --- plan B de la medición de la Station ---------------------------------------------------

SC_ID = "4100000001"
META = {"video_id": SC_ID, "titulo": "Faro Lento", "artista": "Nadia Vellum", "duracion": 312.6}
MOTIVO_SC = "SoundCloud solo ofrece este tema en un formato que la barra no puede abrir."


def _mp3_sintetico(bpm: float, dur: float = 30.0) -> bytes:
    import soundfile as sf

    from motor.sintetico import click_track

    y, _ = click_track(bpm, dur=dur, sr=44100, nota="A", modo="min")
    buf = io.BytesIO()
    sf.write(buf, np.column_stack([y, y]), 44100, format="MP3")
    return buf.getvalue()


class Upstream:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.cerrado = data, status, False

    def iter_content(self, n):
        for k in range(0, len(self.data), n):
            yield self.data[k:k + n]

    def close(self):
        self.cerrado = True


@pytest.fixture
def plan_b(monkeypatch):
    import tempfile

    import source_audio as sa

    estado = {"pedidos": [], "temporales": [], "meta": dict(META), "data": None}

    def sin_audio(sc_id):
        raise sm.AnalisisError(MOTIVO_SC)

    def upstream(url, headers, suffixes=sa._ALLOWED_HOST_SUFFIXES):
        estado["pedidos"].append((url, suffixes))
        estado["up"] = Upstream(estado["data"])
        return estado["up"]

    real_mkstemp = tempfile.mkstemp

    def mkstemp(*a, **k):
        fd, ruta = real_mkstemp(*a, **k)
        estado["temporales"].append(Path(ruta))
        return fd, ruta

    monkeypatch.setattr(sm, "_bajar_tramo", sin_audio)
    monkeypatch.setattr(sc, "cached_track", lambda sid: estado["meta"] if sid == SC_ID else None)
    reg = reg_deezer()
    monkeypatch.setattr(sm, "_registro", lambda: reg)
    monkeypatch.setattr(sa, "_open_upstream", upstream)
    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    return estado


def test_plan_b_mide_el_preview_de_deezer_y_lo_marca(plan_b):
    plan_b["data"] = _mp3_sintetico(128.0)
    r = sm._analizar_sync(SC_ID)
    assert r["ok"] is True, r.get("motivo")
    assert abs(r["bpm"] - 128.0) <= 1.0, f"medido {r['bpm']}, generado 128.0"
    assert (r["preview"], r["preview_fuente"], r["preview_ref"]) == (True, "Deezer", "deezer:900000101")
    assert r["motivo_preview"] == f"Medido en el preview de 30 s de Deezer («Nadia Vellum — Faro Lento»): {MOTIVO_SC}"
    assert r["key_dudosa"] is True, "30 s no alcanzan para el acuerdo de la key: va con '?'"
    assert plan_b["pedidos"] == [("https://cdnt-preview.dzcdn.net/api/1/1/a/b/c/0/faro-lento.mp3", (".dzcdn.net",))]
    assert plan_b["up"].cerrado is True
    assert len(plan_b["temporales"]) == 1 and not plan_b["temporales"][0].exists(), "el temporal del preview quedó en disco"


def test_plan_b_con_otro_tema_deja_signo_de_pregunta(plan_b):
    plan_b["meta"] = {**META, "titulo": "Hilo Rojo", "duracion": 401.0}
    r = sm._analizar_sync(SC_ID)
    assert r["ok"] is False and "bpm" not in r
    assert r["motivo"] == f"{MOTIVO_SC} Deezer solo tiene otra versión de este tema."
    assert plan_b["pedidos"] == [], "no se bajó el preview de otro tema"
    assert sm.publico(r)["motivo"] == r["motivo"]


def test_plan_b_sin_el_tema_en_deezer(plan_b):
    plan_b["meta"] = {**META, "titulo": "Tema Que No Existe", "artista": "Nadie Nunca"}
    r = sm._analizar_sync(SC_ID)
    assert (r["ok"], r["motivo"]) == (False, f"{MOTIVO_SC} Deezer no tiene este tema.")


def test_plan_b_sin_datos_del_tema_no_busca(plan_b, monkeypatch):
    monkeypatch.setattr(sc, "cached_track", lambda sid: None)
    r = sm._analizar_sync(SC_ID)
    assert (r["ok"], r["motivo"]) == (False, MOTIVO_SC)
    assert plan_b["pedidos"] == []


def test_plan_b_preview_ilegible_borra_el_temporal(plan_b):
    plan_b["data"] = bytes(range(256)) * 40
    r = sm._analizar_sync(SC_ID)
    assert (r["ok"], r["motivo"]) == (False, f"{MOTIVO_SC} El preview de Deezer no se pudo decodificar.")
    assert len(plan_b["temporales"]) == 1 and not plan_b["temporales"][0].exists()


def test_cached_track_lee_la_station_en_cache(monkeypatch):
    monkeypatch.setattr(sc, "_cache", {"999": (time.monotonic(), None, [dict(META)])})
    assert sc.cached_track(SC_ID) == META
    assert sc.cached_track("4100000002") is None
    monkeypatch.setattr(sc, "_cache", {"999": (time.monotonic() - sc.CACHE_TTL_S - 1, None, [dict(META)])})
    assert sc.cached_track(SC_ID) is None, "una Station vencida no es dato"


# --- server: Jamendo como una plataforma más y su descarga --------------------------------

@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import importlib
    datos = tmp_path_factory.mktemp("server-datos")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MUSIFLIX_DATA_DIR", str(datos))
        mp.setenv("MUSIFLIX_DOWNLOADS", str(datos / "downloads"))
        mp.setenv("MUSIFLIX_SIN_CALENTAR", "1")
        mp.delenv("REDIS_URL", raising=False)
        mp.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        for mod in ("server", "jobs", "db"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
    return srv


class SinOtras:
    """Las demás plataformas, de juguete: contestan [] (y anotan si se les preguntó)."""

    def __init__(self):
        self.pedidos = []

    def _r(self, f):
        def buscar(q, limit=10):
            self.pedidos.append(f)
            return []
        return buscar

    def __getattr__(self, n):
        if n.startswith("buscar_en_"):
            return self._r(n.removeprefix("buscar_en_"))
        raise AttributeError(n)


@pytest.fixture
def srv(server, monkeypatch, tmp_path):
    otras = SinOtras()
    monkeypatch.setattr(server, "search_agent", otras)
    monkeypatch.setattr(server, "FUENTES_SCRAPER", [("ligaudio", otras._r("ligaudio")), ("hitplayer", otras._r("hitplayer"))])
    monkeypatch.setattr(server, "DOWNLOADS_DIR", tmp_path)
    server._MIX_CACHE.clear()
    server.otras = otras
    yield server
    server._MIX_CACHE.clear()


def con_registro(monkeypatch, server, *providers):
    reg = registro.Registry(providers)
    monkeypatch.setattr(server, "_fuentes_externas", lambda: reg)
    return reg


def test_server_jamendo_sin_clave_queda_sin_configurar(srv, monkeypatch):
    con_registro(monkeypatch, srv, JamendoProvider(client_id="", http_get=FakeJamendo()))
    mezcla, fallidas, sin_config = srv._mix_fuentes("campo", 12)
    assert (mezcla, fallidas, sin_config) == ([], [], ["jamendo"]), "sin clave no es una caída ni rompe la búsqueda"


def test_server_jamendo_aparece_como_una_plataforma_mas(srv, monkeypatch):
    con_registro(monkeypatch, srv, jamendo())
    mezcla, fallidas, _ = srv._mix_fuentes("campo", 12)
    assert fallidas == []
    assert mezcla[0] == {
        "titulo": "Campo Abierto", "artista": "Coro de Niebla", "duracion": 254.0,
        "url": "https://www.jamendo.com/track/1500001",
        "stream_url": "https://prod-1.storage.jamendo.com/?trackid=1500001&format=mp31",
        "fuente": "jamendo", "id": "1500001", "thumbnail": None,
        "licencia": "http://creativecommons.org/licenses/by-sa/3.0/",
        "origen": "https://www.jamendo.com/track/1500001", "descargable": True}
    assert [(c["id"], c["descargable"], c["licencia"]) for c in mezcla[1:]] == [
        ("1500002", False, "http://creativecommons.org/licenses/by-nc-nd/3.0/"), ("1500003", False, "no declarado")]


def test_server_jamendo_bloqueado_cuenta_el_codigo(srv, monkeypatch):
    con_registro(monkeypatch, srv, jamendo(FakeJamendo(error=Resp(498, {}))))
    _, fallidas, _ = srv._mix_fuentes("campo", 12)
    assert [(f, f.http) for f in fallidas] == [("jamendo", 498)]


def test_server_descarga_de_jamendo(srv, monkeypatch, tmp_path):
    bajada = Bajada(tmp_path)
    con_registro(monkeypatch, srv, jamendo(stream=bajada))
    res = srv._descargar_jamendo("https://www.jamendo.com/track/1500001", "Campo Abierto - Coro de Niebla")
    assert res == {"ok": True, "archivo": "Campo Abierto - Coro de Niebla.mp3"}
    assert (tmp_path / res["archivo"]).read_bytes() == bajada.data


@pytest.mark.parametrize("url", ["https://evil.example/track/1500001", "https://www.jamendo.com/track/abc",
                                 "http://www.jamendo.com/track/1500001", ""])
def test_server_descarga_de_jamendo_con_link_invalido(srv, monkeypatch, tmp_path, url):
    bajada = Bajada(tmp_path)
    con_registro(monkeypatch, srv, jamendo(stream=bajada))
    assert srv._descargar_jamendo(url, "x") == {"ok": False, "mensaje": "No sé qué tema de Jamendo es (link inválido)."}
    assert bajada.urls == []


def test_server_jamendo_no_descargable_no_baja_ni_busca_en_youtube(srv, monkeypatch, tmp_path):
    bajada = Bajada(tmp_path)
    con_registro(monkeypatch, srv, jamendo(stream=bajada))
    r = srv.procesar_descarga({"titulo": "Campo Cerrado", "artista": "Coro de Niebla", "fuente": "jamendo",
                               "url": "https://www.jamendo.com/track/1500002", "formato": "mp3"})
    assert r == {"exito": False, "mensaje": "Jamendo no permite descargar este tema (lo decidió el autor)."}
    assert bajada.urls == [] and list(tmp_path.iterdir()) == []
    assert srv.otras.pedidos == [], "un tema de Jamendo no se va a buscar a YouTube"


def test_server_jamendo_sin_clave_al_bajar(srv, monkeypatch, tmp_path):
    con_registro(monkeypatch, srv, JamendoProvider(client_id="", http_get=FakeJamendo()))
    r = srv.procesar_descarga({"titulo": "Campo Abierto", "artista": "Coro de Niebla", "fuente": "jamendo",
                               "url": "https://www.jamendo.com/track/1500001", "formato": "mp3"})
    assert r == {"exito": False, "mensaje": "Jamendo: sin clave."}
