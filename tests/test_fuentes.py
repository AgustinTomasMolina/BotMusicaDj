"""f64: fuentes externas enchufables (`fuentes/`): registro, Deezer (preview para medir) y
Jamendo (búsqueda y descarga, solo con clave).

Sin red: las respuestas de Deezer y Jamendo están armadas a mano con la FORMA de sus APIs
(`fixtures/fuentes_externas.json`), con artistas y títulos ficticios. Lo esperado de la
identidad sale de la regla escrita en `fuentes/registro.py` (misma versión, artista en común,
las dos duraciones conocidas, y el upload hasta max(5 s, 3 %) más largo o max(5 s, 1 %) más
corto que la fuente), no del código bajo prueba. El BPM del plan B de la medición sale
del generador sintético (`motor.sintetico.click_track`), medido por el camino real.
"""
from __future__ import annotations

import io
import json
import logging
import os
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


def test_identidad_rechaza_la_radio_edit_aunque_dure_igual():
    # `pick_track_evidencia` la acepta por la regla de la Radio Edit por duración (210 s = 210,4 s);
    # para MEDIR no: el tema pedido no dice Radio Edit y Deezer sí.
    import track_identity
    pool = [{"title": "Marea Baja (Radio Edit)", "artist": {"name": "Nadia Vellum"}, "duration": 210}]
    elegido, _ = track_identity.pick_track_evidencia(lecturas("Marea Baja", "Nadia Vellum")[0], pool, 210.4, audio_exacto=True)
    assert elegido is pool[0], "precondición: la regla de la Radio Edit la acepta; si no, este test no prueba nada"
    m = reg_deezer().find_preview(lecturas("Marea Baja", "Nadia Vellum"), 210.4)
    assert (m.track, m.motivo) == (None, "Deezer solo tiene otra versión de este tema")


def test_identidad_rechaza_si_la_duracion_no_cuadra():
    # 262 s contra 240 s: 9 %, dentro de `duraciones_cuadran` (±max(10 s, 10 %)) pero fuera de la
    # tolerancia de la medición (±max(5 s, 3 %) = ±7,9 s).
    m = reg_deezer().find_preview(lecturas("Sal", "Nadia Vellum"), 262.0)
    assert m.track is None
    assert m.motivo == "Deezer tiene un tema con ese nombre pero dura distinto (240 s contra 262 s): no lo mido"


def test_identidad_acepta_la_duracion_dentro_de_la_tolerancia():
    # Deezer 240 s. Más LARGO: hasta max(5 s, 3 %) = 7,2 s (silencio al final).
    m = reg_deezer().find_preview(lecturas("Sal", "Nadia Vellum"), 246.5)
    assert m.track is not None and m.track.id == "900000401", m.motivo
    # Más CORTO: solo max(5 s, 1 %) = 5 s (redondeo, silencio recortado); 236 s entra, 234 s no.
    assert reg_deezer().find_preview(lecturas("Sal", "Nadia Vellum"), 236.0).track.id == "900000401"
    m = reg_deezer().find_preview(lecturas("Sal", "Nadia Vellum"), 234.0)
    assert (m.track, m.motivo) == (None, "Deezer tiene un tema con ese nombre pero dura distinto (240 s contra 234 s): no lo mido")


def test_identidad_rechaza_un_upload_acelerado():
    # 293 s contra 300 s: −2,3 %, el mismo master acelerado (~+3 BPM a 128). La regla simétrica
    # vieja, ±max(5 s, 3 % de 293) = ±8,8 s, lo aceptaba y se medía el BPM de otra cosa; más corto
    # ahora solo tolera max(5 s, 1 % de 300) = 5 s.
    m = reg_deezer().find_preview(lecturas("Ola Quieta", "Nadia Vellum"), 293.0)
    assert (m.track, m.motivo) == (None, "Deezer tiene un tema con ese nombre pero dura distinto (300 s contra 293 s): no lo mido")
    assert reg_deezer().find_preview(lecturas("Ola Quieta", "Nadia Vellum"), 308.0).track.id == "900000701", \
        "el mismo tema 8 s más largo (silencio al final) sí"


@pytest.mark.parametrize("titulo, dur", [("Ola Quieta", None), ("Ola Quieta", 0), ("Ola Quieta", float("nan")),
                                         ("Ola Quieta", 30.0), ("Sin Reloj", 250.0)])
def test_identidad_sin_las_dos_duraciones_no_mide(titulo, dur):
    # Sin la duración del upload (o con el 30,0 de un Go+, que `duration_or_none` anula), o sin la
    # de Deezer ("Sin Reloj" viene con duration 0), no hay forma de descartar otra edición.
    m = reg_deezer().find_preview(lecturas(titulo, "Nadia Vellum"), dur)
    assert (m.track, m.motivo) == (None, "Deezer tiene el tema pero no puedo comparar la duración: no lo mido")


def test_identidad_en_un_tema_corto_manda_el_piso_de_5_s():
    # 124,5 s contra 120 s: 4,5 s, más que el 3 % (3,7 s) pero dentro del piso de 5 s
    # (redondeo de Deezer + silencio del upload); 126 s (6 s) ya no.
    assert reg_deezer().find_preview(lecturas("Chispa", "Nadia Vellum"), 124.5).track.id == "900000601"
    assert reg_deezer().find_preview(lecturas("Chispa", "Nadia Vellum"), 126.0).track is None


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


def test_jamendo_sin_clave_no_rompe_y_lo_dice(caplog):
    http = FakeJamendo()                       # sin JAMENDO_CLIENT_ID: lo saca el conftest
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
    bajada, http = Bajada(tmp_path), FakeJamendo()
    bajado = registro.Registry([jamendo(http, stream=bajada)]).download(
        "jamendo", "1500001", tmp_path, stem="Campo Abierto - Coro de Niebla")
    ruta = bajado.path
    assert ruta == tmp_path / "Campo Abierto - Coro de Niebla.mp3"
    # f66: con el archivo vuelve el tema de la MISMA respuesta con la que se validó el permiso
    # (un solo pedido a /tracks/): su licencia y su origen son los que se guardan.
    assert http.pedidos == [("https://api.jamendo.com/v3.0/tracks/",
                             {"client_id": CLAVE, "format": "json", "id": "1500001"})]
    assert (bajado.track.id, bajado.track.license, bajado.track.origin, bajado.track.downloadable) == (
        "1500001", "http://creativecommons.org/licenses/by-sa/3.0/", "https://www.jamendo.com/track/1500001", True)
    assert ruta.read_bytes() == bajada.data
    assert bajada.urls == ["https://prod-1.storage.jamendo.com/download/track/1500001/mp32/"]
    # Mientras bajaba: SOLO el .part (la reserva del nombre); ningún .mp3 vacío.
    assert bajada.durante == [[("Campo Abierto - Coro de Niebla.mp3.part", None)]]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Campo Abierto - Coro de Niebla.mp3"], "quedó un .part"


def test_jamendo_un_corte_no_deja_un_mp3_vacio_y_su_resto_no_se_toca(tmp_path):
    # Un proceso que murió a mitad dejó «Campo.mp3.part»: no es seguro que sea nuestro, no se
    # borra ni se reusa; la descarga nueva va a «Campo (2).mp3». Y en ningún momento hay un
    # «Campo.mp3» de 0 bytes que parezca un tema.
    resto = tmp_path / "Campo.mp3.part"
    resto.write_bytes(b"resto de una descarga cortada")
    bajada = Bajada(tmp_path)
    ruta = jamendo(stream=bajada).download_item("1500001", tmp_path, stem="Campo").path
    assert ruta.name == "Campo (2).mp3" and ruta.read_bytes() == bajada.data
    assert resto.read_bytes() == b"resto de una descarga cortada"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Campo (2).mp3", "Campo.mp3.part"]
    assert bajada.durante == [[("Campo (2).mp3.part", None), ("Campo.mp3.part", None)]]


def test_jamendo_si_aparece_el_nombre_mientras_baja_no_lo_pisa(tmp_path):
    # Otro programa crea «Campo.mp3» entre la reserva y el final: se publica en «Campo (2).mp3».
    class Intrusa(Bajada):
        def __call__(self, url):
            for trozo in super().__call__(url):
                if not (self.dest / "Campo.mp3").exists():
                    (self.dest / "Campo.mp3").write_bytes(b"de otro programa")
                yield trozo
    bajada = Intrusa(tmp_path)
    ruta = jamendo(stream=bajada).download_item("1500001", tmp_path, stem="Campo").path
    assert (tmp_path / "Campo.mp3").read_bytes() == b"de otro programa"
    assert ruta.name == "Campo (2).mp3" and ruta.read_bytes() == bajada.data
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Campo (2).mp3", "Campo.mp3"]


def test_jamendo_no_pisa_un_archivo_que_ya_estaba(tmp_path):
    previo = tmp_path / "Campo Abierto.mp3"
    previo.write_bytes(b"el archivo del usuario")
    bajada = Bajada(tmp_path)
    ruta = jamendo(stream=bajada).download_item("1500001", tmp_path, stem="Campo Abierto").path
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


# --- `_stream` real (con `requests.get` de juguete) y la validación de host -----------------

class RespStream:
    def __init__(self, status=200, chunks=(), headers=None):
        self.status_code, self.chunks, self.headers = status, list(chunks), headers or {}

    def iter_content(self, n):
        yield from self.chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _requests_de_juguete(monkeypatch, respuestas):
    pedidos = []

    def get(url, **k):
        pedidos.append(url)
        return respuestas.pop(0)
    monkeypatch.setattr(requests, "get", get)
    return pedidos


URL_JAM = "https://prod-1.storage.jamendo.com/download/track/1500001/mp32/"


def test_stream_real_baja_y_sigue_una_redireccion_valida(monkeypatch):
    pedidos = _requests_de_juguete(monkeypatch, [
        RespStream(302, headers={"Location": "https://prod-2.storage.jamendo.com/x.mp3"}),
        RespStream(200, [b"ab", b"cd"])])
    assert b"".join(jamendo()._stream(URL_JAM)) == b"abcd"
    assert pedidos == [URL_JAM, "https://prod-2.storage.jamendo.com/x.mp3"]


def test_stream_real_un_403_es_bloqueo(monkeypatch):
    _requests_de_juguete(monkeypatch, [RespStream(403)])
    with pytest.raises(base.ProviderBlocked) as e:
        list(jamendo()._stream(URL_JAM))
    assert e.value.motivo == "Jamendo bloqueó (HTTP 403)"


def test_stream_real_no_sigue_una_redireccion_a_la_red_local(monkeypatch):
    pedidos = _requests_de_juguete(monkeypatch, [RespStream(302, headers={"Location": "https://127.0.0.1/robar"})])
    with pytest.raises(base.DownloadNotAllowed) as e:
        list(jamendo()._stream(URL_JAM))
    assert e.value.motivo == "Jamendo redirigió la descarga a un lugar que no reconozco"
    assert pedidos == [URL_JAM], "se pidió la URL de la red local"


@pytest.mark.parametrize("url, ok", [
    ("https://cdnt-preview.dzcdn.net/api/1/x.mp3", True),
    ("https://cdns-preview-d.dzcdn.net/stream/x.mp3", True),
    ("https://evil-dzcdn.net/x.mp3", False),          # termina en "dzcdn.net" pero no es subdominio
    ("https://dzcdn.net.evil.com/x.mp3", False),
    ("https://cdns-preview.dzcdn.net:8443/x.mp3", False),
    ("https://dzcdn.net/x.mp3", False),                # el sufijo solo, sin subdominio
    ("http://cdnt-preview.dzcdn.net/x.mp3", False),
    ("https://user@cdnt-preview.dzcdn.net/x.mp3", False),
])
def test_host_allowed_con_sufijos_de_deezer(url, ok):
    import source_audio as sa
    assert sa.host_allowed(url, DeezerProvider.preview_hosts) is ok
    assert sa.host_allowed(url) is False, "la CDN de Deezer no entra en las de la barra"


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
    def __init__(self, data, status=200, corte=None):
        self.data, self.status_code, self.cerrado, self.corte = data, status, False, corte
        self.leidos = 0

    def iter_content(self, n):
        for k in range(0, len(self.data), n):
            if self.corte is not None and k:
                raise self.corte
            self.leidos += 1
            yield self.data[k:k + n]

    def close(self):
        self.cerrado = True


@pytest.fixture
def plan_b(monkeypatch):
    import tempfile

    import source_audio as sa

    estado = {"pedidos": [], "temporales": [], "meta": dict(META), "data": None, "error": None, "corte": None}

    def sin_audio(sc_id):
        raise sm.AnalisisError(MOTIVO_SC)

    def upstream(url, headers, suffixes=sa._ALLOWED_HOST_SUFFIXES):
        estado["pedidos"].append((url, suffixes))
        if estado["error"] is not None:
            raise estado["error"]
        estado["up"] = Upstream(estado["data"], corte=estado["corte"])
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
    assert plan_b["temporales"] == [], "el preview se baja en memoria: ningún temporal en disco"
    assert r["tramo_inicio_s"] is None, "de qué parte del tema sale un preview no se sabe: no es 0,0"
    assert r["duracion"] == META["duracion"]


def test_plan_b_sin_duracion_del_tema_no_mide(plan_b):
    plan_b["meta"] = {k: v for k, v in META.items() if k != "duracion"}
    r = sm._analizar_sync(SC_ID)
    assert (r["ok"], r["motivo"]) == (False, f"{MOTIVO_SC} Deezer tiene el tema pero no puedo comparar la duración: no lo mido.")
    assert plan_b["pedidos"] == []


@pytest.mark.parametrize("donde, error", [
    ("abrir", requests.ConnectionError("sin red")),
    ("abrir", requests.ReadTimeout("lento")),
    ("abrir", OSError("disco")),
    ("leer", requests.ConnectionError("cortó")),
    ("leer", requests.exceptions.ChunkedEncodingError("cortó")),
])
def test_plan_b_una_falla_de_red_dice_que_deezer_no_entrego(plan_b, donde, error):
    plan_b["data"] = _mp3_sintetico(128.0)            # varios trozos de 64 KB: el corte cae a mitad
    plan_b["error" if donde == "abrir" else "corte"] = error
    r = sm._analizar_sync(SC_ID)
    assert (r["ok"], r["motivo"]) == (False, f"{MOTIVO_SC} Deezer no entregó el preview.")
    if donde == "leer":
        assert plan_b["up"].leidos == 1 and plan_b["up"].cerrado is True


def test_plan_b_host_rechazado_por_la_validacion_real(monkeypatch):
    # El camino `SourceAudioError` con el `_open_upstream` REAL (sin el fixture que lo reemplaza):
    # la url del preview es de otro host.
    import source_audio as sa
    monkeypatch.setattr(sm, "_bajar_tramo", lambda sid: (_ for _ in ()).throw(sm.AnalisisError(MOTIVO_SC)))
    monkeypatch.setattr(sc, "cached_track", lambda sid: dict(META))
    otra = {"artist:\"Nadia Vellum\" track:\"faro lento\"": json.loads(json.dumps(FX["deezer_por_q"]['artist:"Nadia Vellum" track:"faro lento"']))}
    otra['artist:"Nadia Vellum" track:"faro lento"']["data"][0]["preview"] = "https://evil-dzcdn.net/faro.mp3"
    reg = reg_deezer(FakeDeezer(por_q=otra))
    monkeypatch.setattr(sm, "_registro", lambda: reg)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("se pidió el preview de un host no validado"))
    r = sm._analizar_sync(SC_ID)
    assert (r["ok"], r["motivo"]) == (False, f"{MOTIVO_SC} Deezer: La fuente redirigió el audio a un lugar que no reconozco; no lo sigo.")
    assert sa.host_allowed("https://evil-dzcdn.net/faro.mp3", (".dzcdn.net",)) is False


def test_plan_b_respeta_el_tope_de_bytes(plan_b, monkeypatch):
    plan_b["data"] = _mp3_sintetico(128.0)
    monkeypatch.setattr(sm, "_BYTES_MAX", 64 * 1024)
    vistos = {}
    real = sm.decodificar_mp3
    monkeypatch.setattr(sm, "decodificar_mp3", lambda data: (vistos.setdefault("n", len(data)), real(data))[1])
    sm._analizar_sync(SC_ID)
    assert vistos["n"] <= 64 * 1024, f"se juntaron {vistos['n']} bytes con un tope de {64 * 1024}"


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
    assert plan_b["temporales"] == []


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
    bajada, http = Bajada(tmp_path), FakeJamendo()
    con_registro(monkeypatch, srv, jamendo(http, stream=bajada))
    res = srv._descargar_jamendo("https://www.jamendo.com/track/1500001", "Campo Abierto - Coro de Niebla")
    # f66: con la licencia y el origen que declara Jamendo para ese id (van al historial).
    assert res == {"ok": True, "archivo": "Campo Abierto - Coro de Niebla.mp3",
                   "licencia": "http://creativecommons.org/licenses/by-sa/3.0/",
                   "origen": "https://www.jamendo.com/track/1500001"}
    assert (tmp_path / res["archivo"]).read_bytes() == bajada.data
    # Y salen de la misma respuesta con la que se validó el permiso: un solo pedido a /tracks/.
    assert [p for _, p in http.pedidos] == [{"client_id": CLAVE, "format": "json", "id": "1500001"}]


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


def test_server_jamendo_bloqueado_sin_codigo_no_es_no_contesto(srv, monkeypatch):
    # "status: failed" de Jamendo: ProviderBlocked SIN código HTTP. Contestó y rechazó.
    cuerpo = {"headers": {"status": "failed", "code": 5, "error_message": "x"}}
    con_registro(monkeypatch, srv, jamendo(FakeJamendo(error=Resp(200, cuerpo))))
    _, fallidas, _ = srv._mix_fuentes("campo", 12)
    assert [(f, f.http, f.bloqueo, f.detalle) for f in fallidas] == [("jamendo", None, True, "código 5")]
    assert srv._motivos_fallidas(fallidas) == ["Jamendo bloqueó (código 5)"]


def test_server_motivos_separan_caida_codigo_y_bloqueo_sin_codigo(srv):
    F = srv._Fallida
    fallidas = ["youtube", F("ligaudio"), F("spotify", 429), F("jamendo", None, True, "cuota de pedidos agotada")]
    assert srv._motivos_fallidas(fallidas) == [
        "No contestó a tiempo: YouTube, MP3", "Spotify bloqueó (HTTP 429)", "Jamendo bloqueó (cuota de pedidos agotada)"]
    assert srv._motivos_fallidas([F("jamendo", None, True)]) == ["Jamendo bloqueó"]
    assert srv._motivos_fallidas([]) == []


def test_server_registro_sin_jamendo_es_sin_configurar(srv, monkeypatch):
    con_registro(monkeypatch, srv)                     # registro vacío: no hay Jamendo
    assert srv._mix_fuentes("campo", 12) == ([], [], ["jamendo"])


# --- el registro de la app -----------------------------------------------------------------

def test_los_tests_no_ven_el_registro_real_ni_la_clave():
    import os
    assert registro.default().names() == [], "el conftest deja el registro de la app vacío en cada test"
    assert "JAMENDO_CLIENT_ID" not in os.environ


def test_default_arma_un_solo_registro_con_dos_hilos_a_la_vez(monkeypatch):
    monkeypatch.setattr(registro, "_default", None)
    armados, real = [], registro.Registry.__init__

    def lento(self, providers=()):
        armados.append(self)
        time.sleep(0.2)                                # la ventana en la que entraría el segundo hilo
        real(self, [])
    monkeypatch.setattr(registro.Registry, "__init__", lento)
    vistos = []
    hilos = [threading.Thread(target=lambda: vistos.append(registro.default())) for _ in range(3)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(5)
    assert len(armados) == 1, f"se armaron {len(armados)} registros"
    assert len(vistos) == 3 and all(v is vistos[0] for v in vistos)


def test_default_registra_deezer_y_jamendo(monkeypatch):
    # La composición REAL de la app (sin el registro vacío del conftest). Jamendo se registra
    # siempre; sin clave queda registrado pero apagado.
    monkeypatch.setattr(registro, "_default", None)
    reg = registro.default()
    assert reg.names() == ["deezer", "jamendo"]
    assert [p.name for p in reg.enabled()] == ["deezer"], "sin JAMENDO_CLIENT_ID, Jamendo no está habilitado"


def test_publicar_con_link_y_un_part_que_no_se_puede_borrar(tmp_path, monkeypatch, caplog):
    # Camino POSIX (`os.link` + borrar el .part), también en Windows/NTFS, que tiene hard links.
    # Si el link salió y borrar el .part falla, el tema está publicado y entero: no es un error.
    import fuentes.jamendo as jm
    monkeypatch.setattr(jm, "_RENAME_SIN_PISAR", False)
    part = tmp_path / "Campo.mp3.part"
    part.write_bytes(b"audio completo")
    borrar_de_verdad = os.unlink

    def unlink_que_falla(p, *a, **k):
        if Path(p) == part:
            raise PermissionError("en uso")
        return borrar_de_verdad(p, *a, **k)
    monkeypatch.setattr(os, "unlink", unlink_que_falla)
    with caplog.at_level(logging.WARNING, logger="bot_web"):
        final = jm._publicar(part, tmp_path, "Campo", ".mp3")
    assert final == tmp_path / "Campo.mp3" and final.read_bytes() == b"audio completo"
    assert part.exists(), "el .part que no se pudo borrar queda (no se borra otra cosa en su lugar)"
    assert "no pude borrar «Campo.mp3.part»" in caplog.text


def test_publicar_con_link_borra_el_part_y_no_pisa(tmp_path, monkeypatch):
    import fuentes.jamendo as jm
    monkeypatch.setattr(jm, "_RENAME_SIN_PISAR", False)
    (tmp_path / "Campo.mp3").write_bytes(b"del usuario")
    part = tmp_path / "Campo.mp3.part"
    part.write_bytes(b"audio completo")
    final = jm._publicar(part, tmp_path, "Campo", ".mp3")
    assert (final.name, final.read_bytes()) == ("Campo (2).mp3", b"audio completo")
    assert (tmp_path / "Campo.mp3").read_bytes() == b"del usuario"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Campo (2).mp3", "Campo.mp3"]
