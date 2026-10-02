"""Duración de un MP3 remoto sin bajarlo (f40-r2): `server._duracion_audio` (ffprobe) y
`server._duracion_mp3_cabecera` (cabecera Xing/Info), contra un servidor HTTP LOCAL que sirve
MP3 sintéticos como los sirve HitPlayer: por chunked, sin Content-Length.

El hallazgo de la auditoría: 0 de 88 MP3 de HitPlayer se pudieron medir con ffprobe en
producción. Acá se fija POR QUÉ con el ffprobe real: sobre un stream chunked ffprobe no da la
duración ni aunque el MP3 traiga la cabecera Info (imprime "N/A"); con Content-Length sí. La
duración esperada sale del generador (12 s de seno, `sine=duration=12`) más el relleno de LAME
(el último frame se completa: 12 s caen en 461,25 frames → 462 frames de 1152 muestras = 12,07 s
como máximo), no del código bajo prueba.

Necesita ffmpeg con libmp3lame (el mismo que usa el server); sin eso, se saltea.
"""
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from analizar_calidad import FFMPEG

SEGUNDOS = 12.0
TOL_LAME = 0.1          # relleno del último frame + retardo del encoder (< 2 frames de 26 ms)


def _mp3(tmp, nombre, *extra) -> bytes:
    p = tmp / nombre
    try:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={SEGUNDOS:g}",
                        "-ac", "2", "-c:a", "libmp3lame", "-b:a", "128k", *extra, str(p)],
                       check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        pytest.skip(f"sin ffmpeg con libmp3lame: {type(e).__name__}")
    return p.read_bytes()


@pytest.fixture(scope="module")
def mp3s(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mp3")
    con = _mp3(tmp, "con_info.mp3")                       # LAME CBR escribe la cabecera "Info"
    sin = _mp3(tmp, "sin_xing.mp3", "-write_xing", "0")   # sin cabecera: como un MP3 pelado
    # Que los sintéticos sean lo que dicen ser (si no, los tests de abajo no prueban nada).
    assert b"Info" in con[:1024] and b"Xing" not in con[:1024]
    assert b"Info" not in sin[:1024] and b"Xing" not in sin[:1024]
    return {"con": con, "sin": sin}


@pytest.fixture(scope="module")
def http(mp3s):
    """http://127.0.0.1:<puerto>/{con|sin}/{chunked|largo}.mp3 — chunked = sin Content-Length."""
    pedidos = []

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            pedidos.append(self.path)
            if self.path.startswith("/redir/"):
                # Un sitio scrapeado que redirige: en la vida real el destino sería un host interno.
                self.send_response(302)
                self.send_header("Location", "/con/chunked.mp3?via=redir")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path.startswith("/id3/"):
                # Cabecera ID3v2.4 que dice medir 1 MB (tamaño "syncsafe": 7 bits por byte),
                # seguida del MP3 entero (~190 KB): el primer frame nunca llega.
                n = 1_000_000
                data = b"ID3\x04\x00\x00" + bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F]) + mp3s["con"]
            else:
                data = mp3s["con" if self.path.startswith("/con/") else "sin"]
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            try:
                if "chunked" in self.path:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for i in range(0, len(data), 8192):
                        c = data[i:i + 8192]
                        self.wfile.write(b"%x\r\n" % len(c) + c + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
            except OSError:          # el cliente cortó (la cabecera ya alcanzaba): es lo esperado
                pass

    class Srv(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass             # el cliente que corta la conexión a propósito no es un error

    srv = Srv(("127.0.0.1", 0), H)
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", pedidos
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """El server con datos en un temporal (como tests/test_station_versiones.py)."""
    import importlib
    import sys
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


def test_ffprobe_no_mide_un_mp3_chunked_sin_xing(server, http):
    base, _ = http
    assert server._duracion_audio(f"{base}/sin/chunked.mp3", "", 15, scraper=True) == 0.0


def test_ffprobe_tampoco_mide_un_mp3_chunked_con_info(server, http):
    # Lo que pasa con HitPlayer: el MP3 trae la cabecera Info y ffprobe igual da "N/A" sobre
    # un stream sin largo. Por eso la medición de versiones ya no usa ffprobe.
    base, _ = http
    assert server._duracion_audio(f"{base}/con/chunked.mp3", "", 15, scraper=True) == 0.0


def test_ffprobe_con_content_length_y_whitelist_si_mide(server, http):
    # El whitelist de protocolos (scraper=True) no rompe una URL http legítima.
    base, _ = http
    assert server._duracion_audio(f"{base}/sin/largo.mp3", "", 15, scraper=True) == pytest.approx(SEGUNDOS, abs=TOL_LAME)


def test_la_cabecera_info_da_la_duracion_de_un_mp3_chunked(server, http):
    base, _ = http
    assert server._duracion_mp3_cabecera(f"{base}/con/chunked.mp3", "", 5) == pytest.approx(SEGUNDOS, abs=TOL_LAME)


def test_sin_cabecera_la_duracion_es_desconocida(server, http):
    base, _ = http
    assert server._duracion_mp3_cabecera(f"{base}/sin/chunked.mp3", "", 5) == 0.0


def test_sin_cabecera_se_decide_con_los_primeros_kb(server, http, monkeypatch):
    base, _ = http
    leidos = []
    real = server._duracion_xing

    def espiar(buf):
        leidos.append(len(buf))
        return real(buf)
    monkeypatch.setattr(server, "_duracion_xing", espiar)
    assert server._duracion_mp3_cabecera(f"{base}/sin/chunked.mp3", "", 5) == 0.0
    assert max(leidos) <= 32 * 1024, f"leyó {max(leidos)} bytes para ver que no hay cabecera"


def test_con_un_id3_enorme_se_deja_de_leer_al_tope(server, http, monkeypatch):
    # Un ID3 que dice medir 1 MB (una carátula enorme): no se llega al primer frame dentro del
    # tope y no se sigue bajando el tema. Con un tope de 64 KB y un archivo de ~190 KB se ve
    # que corta antes del final.
    base, _ = http
    leidos = []
    real = server._duracion_xing

    def espiar(buf):
        leidos.append(len(buf))
        return real(buf)
    monkeypatch.setattr(server, "_MP3_CABECERA_MAX", 64 * 1024)
    monkeypatch.setattr(server, "_duracion_xing", espiar)
    assert server._duracion_mp3_cabecera(f"{base}/id3/chunked.mp3", "", 5) == 0.0
    assert 64 * 1024 <= max(leidos) < 100 * 1024, f"leyó {max(leidos)} bytes de un tope de 64 KB"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "concat:a.mp3|b.mp3", "C:/Windows/win.ini", "ftp://x/y.mp3", ""])
def test_una_url_que_no_es_http_no_llega_a_ffprobe_ni_a_la_red(server, monkeypatch, url):
    llamadas = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: llamadas.append(a) or None)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: llamadas.append(a) or None)
    assert server._duracion_audio(url, "", 5, scraper=True) == 0.0
    assert server._duracion_mp3_cabecera(url, "", 5) == 0.0
    assert llamadas == []


def test_una_cabecera_info_que_no_cuadra_con_los_bytes_no_se_cree(server, mp3s):
    # Info (CBR) trae frames Y bytes: si los bytes / bitrate no dan la misma duración, la
    # cabecera miente (un MP3 recortado o pegado sin reescribirla) y no se usa.
    buf = bytearray(mp3s["con"])
    p = buf.find(b"Info")
    assert server._duracion_xing(bytes(buf)) == pytest.approx(SEGUNDOS, abs=TOL_LAME)
    frames = int.from_bytes(buf[p + 8:p + 12], "big")
    buf[p + 8:p + 12] = (frames * 2).to_bytes(4, "big")          # dice el doble de frames
    assert server._duracion_xing(bytes(buf)) == 0.0


def test_no_sigue_redirecciones(server, http):
    """Auditoría final de f40: `_url_http` valida solo la URL inicial; si se siguiera un 302, un
    sitio scrapeado podría hacer que el server pida un host interno. Un 302 = duración
    desconocida, y el destino no se pide."""
    base, pedidos = http
    assert server._duracion_mp3_cabecera(f"{base}/redir/chunked.mp3", "", 5) == 0.0
    assert not any("via=redir" in p for p in pedidos), f"se siguió la redirección: {pedidos}"
