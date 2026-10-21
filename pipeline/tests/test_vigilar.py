"""Tests de la carpeta vigilada (f65, `pipeline/vigilar.py`).

Todo en carpetas temporales. Donde hace falta audio de verdad sale de `motor.sintetico`
(BPM conocido por construcción); el resto usa archivos con bytes cualquiera, porque lo que
se prueba ahí es QUÉ llega al pipeline, no qué mide. Sin red.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pipeline import config, vigilar
from pipeline.vigilar import (
    FALLIDO,
    PROCESADO,
    CarpetaInalcanzable,
    Registro,
    ResultadoLote,
    Vigilante,
    descubrir,
    estables,
)


def _escribir(ruta: Path, contenido: bytes = b"audio-falso" * 100) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(contenido)
    return ruta


def _sha(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _nombres(fotos) -> list[str]:
    return sorted(Path(f.ruta).name for f in fotos)


class _ProcesadorFalso:
    """Hace de `procesar_aparte`: anota cada tanda y devuelve una fila por archivo."""

    def __init__(self, falla_si=None):
        self.lotes: list[list[str]] = []
        self.falla_si = falla_si or (lambda rutas: False)
        self.en_curso = 0
        self.max_simultaneos = 0
        self._lock = threading.Lock()

    def __call__(self, rutas, staging, reporte, timeout_s, cancelar=None):
        with self._lock:
            self.en_curso += 1
            self.max_simultaneos = max(self.max_simultaneos, self.en_curso)
        try:
            time.sleep(0.05)
            self.lotes.append([Path(r).name for r in rutas])
            if self.falla_si(rutas):
                return ResultadoLote(False, motivo=f"se cayó con {Path(rutas[0])}")
            return ResultadoLote(True, filas=[
                {"ruta_original": r, "bpm": 0.0, "camelot": "?", "estado": "pendiente",
                 "bandera": "ok"} for r in rutas], reporte=str(reporte))
        finally:
            with self._lock:
                self.en_curso -= 1


def _vigilante(tmp_path, carpeta, procesar, **kw):
    kw.setdefault("espera_estable_s", 0)
    kw.setdefault("dormir", lambda s: None)
    return Vigilante(carpeta, staging=tmp_path / "stg", registro=Registro(tmp_path / "reg.json"),
                     procesar=procesar, **kw)


# --- configuración ---------------------------------------------------------------------


def test_apagada_si_la_variable_no_esta(monkeypatch):
    monkeypatch.delenv(config.VAR_WATCH, raising=False)
    assert config.watch_folder() is None
    assert vigilar.desde_entorno() is None, "sin MUSIC_WATCH_FOLDER no tiene que haber vigilante"


def test_apagada_si_la_variable_esta_vacia(monkeypatch):
    monkeypatch.setenv(config.VAR_WATCH, "   ")
    assert config.watch_folder() is None
    assert vigilar.desde_entorno() is None


def test_prendida_con_la_carpeta_configurada(monkeypatch, tmp_path):
    monkeypatch.setenv(config.VAR_WATCH, str(tmp_path / "entrada"))
    monkeypatch.setenv(config.VAR_STAGING, str(tmp_path / "stg"))
    monkeypatch.setenv("MUSIFLIX_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.delenv(config.VAR_ITUNES, raising=False)
    vig = vigilar.desde_entorno()
    assert vig.carpeta == tmp_path / "entrada"
    assert vig.registro.ruta == tmp_path / "datos" / vigilar.NOMBRE_REGISTRO
    assert vig.staging == tmp_path / "stg"


def test_intervalo_con_default_minimo_y_typo(monkeypatch):
    monkeypatch.delenv(config.VAR_WATCH_INTERVALO, raising=False)
    assert config.watch_interval_s() == 60.0
    monkeypatch.setenv(config.VAR_WATCH_INTERVALO, "1")
    assert config.watch_interval_s() == 10.0, "por debajo del mínimo se usa el mínimo"
    monkeypatch.setenv(config.VAR_WATCH_INTERVALO, "sesenta")
    assert config.watch_interval_s() == 60.0, "un valor mal escrito vuelve al default"
    monkeypatch.setenv(config.VAR_WATCH_INTERVALO, "120")
    assert config.watch_interval_s() == 120.0


def test_la_cli_apagada_sale_con_error_de_uso(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(config.VAR_WATCH, raising=False)
    monkeypatch.setattr(vigilar, "ENV_FILE", tmp_path / "no-existe.env")
    assert vigilar.main([]) == vigilar.USO
    assert "MUSIC_WATCH_FOLDER" in capsys.readouterr().out


# --- descubrir ---------------------------------------------------------------------------


def test_descubre_recursivo_solo_audio(tmp_path):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "a.mp3")
    _escribir(raiz / "sub" / "b.WAV")
    _escribir(raiz / "sub" / "mas" / "c.flac")
    _escribir(raiz / "notas.txt")
    _escribir(raiz / "tapa.jpg")
    assert _nombres(descubrir(raiz)) == ["a.mp3", "b.WAV", "c.flac"]


def test_ignora_descargas_en_curso_y_ocultos(tmp_path):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "listo.mp3")
    _escribir(raiz / "bajando.mp3.part")
    _escribir(raiz / "bajando.part")
    _escribir(raiz / "otro.mp3.crdownload")
    _escribir(raiz / "temporal.wav.tmp")
    _escribir(raiz / "temporal.tmp")
    _escribir(raiz / "convirtiendo.temp.mp3")     # yt-dlp mientras convierte
    _escribir(raiz / "bajando.part.wav")
    _escribir(raiz / ".oculto.mp3")
    _escribir(raiz / ".carpeta-oculta" / "x.mp3")
    _escribir(raiz / "~$bloqueo.mp3")
    assert _nombres(descubrir(raiz)) == ["listo.mp3"]


@pytest.mark.skipif(os.name != "nt", reason="atributos de Windows")
def test_ignora_archivos_ocultos_y_de_sistema_de_windows(tmp_path):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "visible.mp3")
    oculto = _escribir(raiz / "oculto.mp3")
    sistema = _escribir(raiz / "sistema.mp3")
    carpeta_oculta = raiz / "escondida"
    _escribir(carpeta_oculta / "adentro.mp3")
    subprocess.run(["attrib", "+H", str(oculto)], check=True)
    subprocess.run(["attrib", "+S", str(sistema)], check=True)
    subprocess.run(["attrib", "+H", str(carpeta_oculta)], check=True)
    try:
        assert _nombres(descubrir(raiz)) == ["visible.mp3"]
    finally:
        for p in (oculto, sistema, carpeta_oculta):
            subprocess.run(["attrib", "-H", "-S", str(p)], check=False)


def test_no_recorre_el_staging_aunque_este_adentro(tmp_path):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "nuevo.mp3")
    _escribir(raiz / "stg" / "copia_procesada.mp3")
    assert _nombres(descubrir(raiz, excluir=[raiz / "stg"])) == ["nuevo.mp3"]


def test_carpeta_inexistente_es_inalcanzable(tmp_path):
    with pytest.raises(CarpetaInalcanzable, match="no-montada"):
        descubrir(tmp_path / "no-montada")


def test_la_red_que_se_corta_al_listar_es_inalcanzable_no_carpeta_vacia(tmp_path, monkeypatch):
    """El isdir dio bien y el listado de la raíz falla (la red se cortó en el medio): no puede
    pasar como "carpeta vacía"."""
    raiz = tmp_path / "nas"
    raiz.mkdir()

    def walk_cortado(top, onerror=None, **kw):
        onerror(OSError(64, "El nombre de red especificado ya no está disponible", str(top)))
        return iter(())

    monkeypatch.setattr(vigilar.os, "walk", walk_cortado)
    with pytest.raises(CarpetaInalcanzable, match="se cortó la red"):
        descubrir(raiz)


# --- archivos que todavía se escriben -----------------------------------------------------


def test_el_que_crece_entre_dos_miradas_queda_afuera(tmp_path):
    quieto = _escribir(tmp_path / "quieto.mp3")
    creciendo = _escribir(tmp_path / "creciendo.mp3")
    fotos = descubrir(tmp_path)

    def dormir(_s):
        with open(creciendo, "ab") as f:
            f.write(b"mas-bytes")

    listos, cambiando = estables(fotos, 5, dormir)
    assert _nombres(listos) == [quieto.name]
    assert _nombres(cambiando) == [creciendo.name]


def test_el_tocado_sin_crecer_tambien_queda_afuera(tmp_path):
    tocado = _escribir(tmp_path / "tocado.mp3")
    fotos = descubrir(tmp_path)
    st = os.stat(tocado)

    def dormir(_s):
        os.utime(tocado, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))

    listos, cambiando = estables(fotos, 5, dormir)
    assert listos == [] and _nombres(cambiando) == ["tocado.mp3"]


def test_un_archivo_vacio_no_esta_listo(tmp_path):
    _escribir(tmp_path / "vacio.mp3", b"")
    listos, cambiando = estables(descubrir(tmp_path), 0, lambda s: None)
    assert listos == [] and _nombres(cambiando) == ["vacio.mp3"]


def test_el_ciclo_no_procesa_el_que_sigue_creciendo(tmp_path):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "quieto.mp3")
    creciendo = _escribir(raiz / "creciendo.mp3")

    def dormir(_s):
        with open(creciendo, "ab") as f:
            f.write(b"x")

    proc = _ProcesadorFalso()
    vig = _vigilante(tmp_path, raiz, proc, dormir=dormir)
    estado = vig.escanear_ahora()
    assert proc.lotes == [["quieto.mp3"]]
    assert estado["creciendo"] == 1


# --- lo nuevo y lo ya procesado ----------------------------------------------------------


def test_no_reprocesa_lo_que_ya_proceso(tmp_path):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "a.mp3")
    _escribir(raiz / "sub" / "b.mp3")
    proc = _ProcesadorFalso()
    vig = _vigilante(tmp_path, raiz, proc)
    vig.escanear_ahora()
    assert proc.lotes == [["a.mp3", "b.mp3"]]

    vig.escanear_ahora()
    assert proc.lotes == [["a.mp3", "b.mp3"]], "el segundo ciclo volvió a procesar lo mismo"

    # Otro vigilante con el MISMO registro (= el server reiniciado): tampoco.
    vig2 = _vigilante(tmp_path, raiz, proc)
    vig2.escanear_ahora()
    assert proc.lotes == [["a.mp3", "b.mp3"]], "después de reiniciar volvió a procesar lo mismo"

    _escribir(raiz / "c.mp3")
    vig2.escanear_ahora()
    assert proc.lotes[-1] == ["c.mp3"], "lo nuevo tiene que entrar solo, sin lo de antes"


def test_un_archivo_que_cambia_se_vuelve_a_procesar(tmp_path):
    raiz = tmp_path / "entrada"
    a = _escribir(raiz / "a.mp3")
    proc = _ProcesadorFalso()
    vig = _vigilante(tmp_path, raiz, proc)
    vig.escanear_ahora()
    _escribir(a, b"otra-version" * 300)
    vig.escanear_ahora()
    assert proc.lotes == [["a.mp3"], ["a.mp3"]]


def test_el_registro_guarda_estado_y_licencia_no_declarada(tmp_path):
    raiz = tmp_path / "entrada"
    a = _escribir(raiz / "a.mp3")
    vig = _vigilante(tmp_path, raiz, _ProcesadorFalso())
    vig.escanear_ahora()
    e = Registro(tmp_path / "reg.json").entrada(str(a))
    assert e["estado"] == PROCESADO
    assert e["tamano"] == a.stat().st_size
    assert e["licencia"] == "no declarado" and e["origen"] == "no declarado"
    assert e["camelot"] == "?" and e["bpm"] is None, "BPM 0 no se guarda como un BPM"


def test_un_registro_ilegible_se_aparta_y_no_se_pisa(tmp_path):
    reg = tmp_path / "reg.json"
    reg.write_text("{esto no es json", encoding="utf-8")
    r = Registro(reg)
    assert r.contar() == {PROCESADO: 0, FALLIDO: 0}
    apartados = list(tmp_path.glob("reg.json.ilegible-*"))
    assert len(apartados) == 1 and apartados[0].read_text(encoding="utf-8") == "{esto no es json"


def test_tanda_acotada_lo_que_sobra_queda_para_el_proximo_ciclo(tmp_path):
    raiz = tmp_path / "entrada"
    for i in range(5):
        _escribir(raiz / f"t{i}.mp3")
    proc = _ProcesadorFalso()
    vig = _vigilante(tmp_path, raiz, proc, max_por_ciclo=2)
    estado = vig.escanear_ahora()
    assert proc.lotes == [["t0.mp3", "t1.mp3"]]
    assert estado["pendientes"] == 3
    vig.escanear_ahora()
    vig.escanear_ahora()
    assert proc.lotes == [["t0.mp3", "t1.mp3"], ["t2.mp3", "t3.mp3"], ["t4.mp3"]]


# --- fallos ------------------------------------------------------------------------------


def test_una_tanda_que_falla_se_reintenta_de_a_uno(tmp_path):
    raiz = tmp_path / "entrada"
    for n in ("bueno1.mp3", "malo.mp3", "bueno2.mp3"):
        _escribir(raiz / n)
    proc = _ProcesadorFalso(falla_si=lambda rutas: any("malo" in r for r in rutas))
    vig = _vigilante(tmp_path, raiz, proc)
    vig.escanear_ahora()          # la tanda entera falla
    vig.escanear_ahora()          # de a uno
    assert proc.lotes == [["bueno1.mp3", "bueno2.mp3", "malo.mp3"],
                          ["bueno1.mp3"], ["bueno2.mp3"], ["malo.mp3"]]
    reg = Registro(tmp_path / "reg.json")
    assert reg.entrada(str(raiz / "bueno1.mp3"))["estado"] == PROCESADO
    malo = reg.entrada(str(raiz / "malo.mp3"))
    assert malo["estado"] == FALLIDO
    assert str(raiz) not in malo["motivo"], "el motivo no puede llevar la ruta de la PC"
    vig.escanear_ahora()
    assert len(proc.lotes) == 4, "el fallido sin cambios no se reintenta en cada ciclo"


def test_carpeta_que_no_esta_no_rompe_y_vuelve(tmp_path):
    raiz = tmp_path / "nas"
    proc = _ProcesadorFalso()
    vig = _vigilante(tmp_path, raiz, proc)
    estado = vig.escanear_ahora()
    assert estado["alcanzable"] is False
    assert "nas" in estado["ultimo_error"] and str(tmp_path) not in estado["ultimo_error"]
    _escribir(raiz / "a.mp3")     # se montó
    estado = vig.escanear_ahora()
    assert estado["alcanzable"] is True and proc.lotes == [["a.mp3"]]


def test_el_hilo_sobrevive_a_la_carpeta_caida(tmp_path):
    proc = _ProcesadorFalso()
    vig = _vigilante(tmp_path, tmp_path / "nas", proc, intervalo_s=0.05)
    vig.iniciar()
    try:
        time.sleep(0.4)
        assert vig.estado()["corriendo"] is True, "el hilo se murió con la carpeta caída"
        _escribir(tmp_path / "nas" / "a.mp3")
        limite = time.monotonic() + 5
        while not proc.lotes and time.monotonic() < limite:
            time.sleep(0.05)
        assert proc.lotes == [["a.mp3"]]
    finally:
        vig.detener()
    assert vig.estado()["corriendo"] is False


def test_un_procesador_que_levanta_no_tira_el_hilo(tmp_path):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "a.mp3")

    def explota(*a, **k):
        raise RuntimeError("boom")

    vig = _vigilante(tmp_path, raiz, explota)
    vig.escanear_ahora()
    e = Registro(tmp_path / "reg.json").entrada(str(raiz / "a.mp3"))
    assert e["estado"] == FALLIDO and "boom" in e["motivo"]


# --- de a uno ----------------------------------------------------------------------------


def test_nunca_dos_tandas_a_la_vez(tmp_path):
    """El hilo y varios "escanear ya" simultáneos: un solo proceso de análisis por vez."""
    raiz = tmp_path / "entrada"
    for i in range(6):
        _escribir(raiz / f"t{i}.mp3")
    proc = _ProcesadorFalso(falla_si=lambda rutas: len(rutas) > 1)   # fuerza 6 tandas de 1
    vig = _vigilante(tmp_path, raiz, proc)
    hilos = [threading.Thread(target=vig.escanear_ahora) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(10)
    vig.escanear_ahora()
    assert proc.max_simultaneos == 1
    procesadas = sorted(n for lote in proc.lotes if len(lote) == 1 for n in lote)
    assert procesadas == [f"t{i}.mp3" for i in range(6)], "cada archivo una sola vez"


# --- el proceso aparte -------------------------------------------------------------------


def test_el_tope_de_tiempo_mata_el_proceso(tmp_path):
    marca = tmp_path / "sigue-vivo.txt"
    script = ("import sys, time, pathlib\n"
              "time.sleep(4)\n"
              f"pathlib.Path(r'{marca}').write_text('vivo')\n")
    t0 = time.monotonic()
    res = vigilar.procesar_aparte([str(tmp_path / "x.mp3")], tmp_path / "stg",
                                  tmp_path / "r.html", 1.0,
                                  comando=[sys.executable, "-c", script])
    assert res.ok is False and "tope" in res.motivo
    assert time.monotonic() - t0 < 4, "esperó al proceso en vez de matarlo"
    time.sleep(4.5)
    assert not marca.exists(), "el proceso siguió corriendo después del tope"


def test_cancelar_mata_el_proceso(tmp_path):
    """Se cancela con el hijo ya corriendo: tiene que morir, no seguir hasta el final."""
    vivo, marca = tmp_path / "arranco.txt", tmp_path / "termino.txt"
    script = ("import time, pathlib\n"
              f"pathlib.Path(r'{vivo}').write_text('si')\n"
              "time.sleep(4)\n"
              f"pathlib.Path(r'{marca}').write_text('si')\n")
    cancelar = threading.Event()

    def cancelar_cuando_arranque():
        limite = time.monotonic() + 20
        while not vivo.exists() and time.monotonic() < limite:
            time.sleep(0.05)
        cancelar.set()

    threading.Thread(target=cancelar_cuando_arranque, daemon=True).start()
    t0 = time.monotonic()
    res = vigilar.procesar_aparte([], tmp_path / "stg", tmp_path / "r.html", 60,
                                  cancelar=cancelar, comando=[sys.executable, "-c", script])
    assert vivo.exists(), "el hijo nunca arrancó: el test no probó nada"
    assert res.cancelado is True and res.ok is False
    assert time.monotonic() - t0 < 4, "esperó al proceso en vez de matarlo"
    time.sleep(4.5)
    assert not marca.exists(), "el proceso siguió corriendo después de cancelar"


def test_el_fallo_del_proceso_trae_el_motivo_sin_rutas(tmp_path):
    ruta = str(tmp_path / "tema.mp3")
    script = f"import sys; sys.exit('no pude con ' + {ruta!r})"
    res = vigilar.procesar_aparte([ruta], tmp_path / "stg", tmp_path / "r.html", 30,
                                  comando=[sys.executable, "-c", script])
    assert res.ok is False
    assert res.motivo == "el proceso falló: no pude con tema.mp3"


# --- de punta a punta con el pipeline real -----------------------------------------------


def test_de_punta_a_punta_con_el_pipeline_real_y_originales_intactos(tmp_path):
    """El proceso aparte corre `pipeline.revisar.procesar` de verdad sobre audio sintético:
    copia en el staging, reporte, BPM del motor contra el BPM con que se generó, y el original
    bit a bit igual."""
    import soundfile as sf

    from motor.sintetico import click_track

    raiz = tmp_path / "entrada" / "sub"
    raiz.mkdir(parents=True)
    y, sr = click_track(128.0, dur=40.0, nota="A", modo="min")
    original = raiz / "Artista - Tema.wav"
    sf.write(original, y, sr)
    antes = _sha(original)
    mtime_antes = original.stat().st_mtime_ns

    vig = Vigilante(tmp_path / "entrada", staging=tmp_path / "salida" / "stg",
                    registro=Registro(tmp_path / "reg.json"), espera_estable_s=0.5,
                    timeout_por_archivo_s=300)
    estado = vig.escanear_ahora()

    assert _sha(original) == antes, "el original cambió"
    assert original.stat().st_mtime_ns == mtime_antes, "el original fue tocado"
    assert sorted(p.name for p in raiz.iterdir()) == ["Artista - Tema.wav"]
    e = Registro(tmp_path / "reg.json").entrada(str(original))
    assert e["estado"] == PROCESADO, e
    assert e["bpm"] == pytest.approx(128.0, abs=1.0), "el BPM no es el del generador"
    copias = list((tmp_path / "salida" / "stg").glob("tanda_*/Artista - Tema.wav"))
    assert len(copias) == 1, "la copia tiene que quedar en la subcarpeta de su tanda"
    copia = copias[0]
    import mutagen.wave
    assert mutagen.wave.WAVE(str(copia)).info.sample_rate == sr, "la copia no es un WAV legible"
    reporte = tmp_path / "salida" / estado["ultimo_reporte"]
    assert reporte.name.startswith("revision_vigilada_")
    assert "Tema" in reporte.read_text(encoding="utf-8")


def test_el_resultado_del_proceso_hijo_vuelve_entero(tmp_path):
    """Lo que el hijo escribe en --salida es lo que recibe el vigilante, fila por fila."""
    fila = {"ruta_original": "x.wav", "bpm": 128.0, "camelot": "8A"}
    script = ("import sys, json; a = sys.argv; "
              "open(a[a.index('--salida') + 1], 'w').write(json.dumps("
              f"{{'filas': [{fila!r}], 'reporte': 'r.html'}}))")
    res = vigilar.procesar_aparte([], tmp_path / "stg", tmp_path / "r.html", 30,
                                  comando=[sys.executable, "-c", script])
    assert res.ok is True and res.reporte == "r.html"
    assert res.filas == [fila]


# --- auditoría f65: enlaces, exclusión por identidad -------------------------------------


def _junction(enlace: Path, destino: Path) -> None:
    import _winapi
    _winapi.CreateJunction(str(destino), str(enlace))


@pytest.mark.skipif(os.name != "nt", reason="junctions de Windows")
def test_una_junction_a_un_ancestro_no_cambia_el_resultado(tmp_path):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "a.mp3")
    _escribir(raiz / "sub" / "b.mp3")
    _escribir(raiz / "stg" / "copia.mp3")
    sin = _nombres(descubrir(raiz, excluir=[raiz / "stg"]))
    _junction(raiz / "sub" / "enlace", raiz)          # sub/enlace → la raíz
    _junction(raiz / "afuera", tmp_path)              # y otra a un ancestro de la raíz
    try:
        # En un hilo con tope: si entrara en las junctions el recorrido no termina nunca, y
        # el test tiene que fallar, no colgarse.
        hecho: list = []
        hilo = threading.Thread(target=lambda: hecho.append(
            descubrir(raiz, excluir=[raiz / "stg"])), daemon=True)
        hilo.start()
        hilo.join(30)
        assert hecho, "el recorrido no terminó en 30 s: está entrando en las junctions"
        con = hecho[0]
        assert _nombres(con) == sin == ["a.mp3", "b.mp3"]
        assert all("enlace" not in f.ruta and "afuera" not in f.ruta for f in con)
    finally:
        os.rmdir(raiz / "sub" / "enlace")             # borra la junction, no el destino
        os.rmdir(raiz / "afuera")


def _nombre_corto(ruta: Path) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(ruta), buf, 1024)
    return buf.value if n else str(ruta)


@pytest.mark.skipif(os.name != "nt", reason="nombres 8.3 de Windows")
def test_el_staging_se_excluye_aunque_se_lo_nombre_con_8_3(tmp_path):
    raiz = tmp_path / "entrada"
    stg = raiz / "Staging Con Nombre Largo"
    _escribir(raiz / "nuevo.mp3")
    _escribir(stg / "copia_procesada.mp3")
    corto = _nombre_corto(stg)
    if os.path.normcase(corto) == vigilar.clave(stg):
        pytest.skip("este volumen no genera nombres 8.3 (fsutil 8dot3name): no se puede probar")
    assert _nombres(descubrir(raiz, excluir=[Path(corto)])) == ["nuevo.mp3"]


# --- auditoría f65: nombres repetidos y subcarpeta por tanda ------------------------------


class _ProcesadorQueCopia(_ProcesadorFalso):
    """Como `revisar.procesar`: la copia se llama como el archivo, dentro del staging dado."""

    def __init__(self):
        super().__init__()
        self.filas: list[dict] = []

    def __call__(self, rutas, staging, reporte, timeout_s, cancelar=None):
        import shutil
        self.lotes.append([Path(r).name for r in rutas])
        staging.mkdir(parents=True, exist_ok=True)
        filas = []
        for r in rutas:
            copia = staging / Path(r).name
            shutil.copy2(r, copia)
            filas.append({"ruta_original": r, "ruta_staging": str(copia), "archivo": copia.name,
                          "artista": "A", "titulo": Path(r).parent.name, "bpm": 0.0,
                          "camelot": "?", "estado": "aprobado", "bandera": "ok"})
        self.filas += filas
        return ResultadoLote(True, filas=filas, reporte=str(reporte))


def test_dos_archivos_con_el_mismo_nombre_tienen_cada_uno_su_copia(tmp_path):
    from pipeline.aplicar import aplicar

    raiz = tmp_path / "entrada"
    uno = _escribir(raiz / "sub1" / "tema.mp3", b"version-uno" * 50)
    dos = _escribir(raiz / "sub2" / "tema.mp3", b"version-dos" * 70)
    stg = raiz / "stg"                                # el staging ADENTRO de la vigilada
    proc = _ProcesadorQueCopia()
    vig = Vigilante(raiz, staging=stg, registro=Registro(tmp_path / "reg.json"),
                    procesar=proc, espera_estable_s=0, dormir=lambda s: None)
    vig.escanear_ahora()

    assert proc.lotes == [["tema.mp3"], ["tema.mp3"]], "los dos homónimos fueron juntos"
    por_original = {f["ruta_original"]: Path(f["ruta_staging"]) for f in proc.filas}
    c1, c2 = por_original[str(uno)], por_original[str(dos)]
    assert c1 != c2, "las dos filas apuntan a la misma copia"
    assert c1.parent.parent == stg and c1.parent.name.startswith("tanda_")
    assert c1.read_bytes() == uno.read_bytes() and c2.read_bytes() == dos.read_bytes()

    # Las subcarpetas del staging quedan excluidas: el próximo ciclo no reingiere las copias.
    vig.escanear_ahora()
    assert len(proc.lotes) == 2

    # `aplicar` con la fila tal cual (apunta a la subcarpeta) manda el audio correcto.
    itunes = tmp_path / "itunes"
    itunes.mkdir()
    fila_dos = next(f for f in proc.filas if f["ruta_original"] == str(dos))
    res = aplicar([fila_dos], itunes, tmp_path / "rb.xml")
    assert res.faltantes == [] and (itunes / "tema.mp3").read_bytes() == dos.read_bytes()


def test_partir_por_nombre_no_junta_homonimos_y_respeta_el_orden(tmp_path):
    fotos = [vigilar.Foto(str(tmp_path / d / n), 1, 1) for d, n in
             [("a", "x.mp3"), ("b", "X.mp3"), ("a", "y.mp3"), ("c", "x.mp3")]]
    tandas = vigilar.partir_por_nombre(fotos)
    nombres = [[f"{Path(f.ruta).parent.name}/{Path(f.ruta).name}" for f in t] for t in tandas]
    esperado = ([["a/x.mp3", "a/y.mp3"], ["b/X.mp3"], ["c/x.mp3"]] if os.name == "nt"
                else [["a/x.mp3", "b/X.mp3", "a/y.mp3"], ["c/x.mp3"]])
    assert nombres == esperado


# --- auditoría f65: CLI y server a la vez -----------------------------------------------


def test_otro_proceso_con_el_cerrojo_hace_saltear_el_ciclo(tmp_path, monkeypatch, capsys):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "a.mp3")
    reg = tmp_path / "datos" / vigilar.NOMBRE_REGISTRO
    lock = reg.with_name(reg.name + ".lock")
    listo = tmp_path / "tengo-el-cerrojo"
    script = ("import sys, time, pathlib\n"
              f"sys.path.insert(0, r'{vigilar.BASE_DIR}')\n"
              "from pipeline.vigilar import CerrojoArchivo\n"
              f"c = CerrojoArchivo(pathlib.Path(r'{lock}'))\n"
              "assert c.tomar()\n"
              f"pathlib.Path(r'{listo}').write_text('si')\n"
              "time.sleep(6)\n"
              "c.soltar()\n")
    otro = subprocess.Popen([sys.executable, "-c", script], cwd=str(vigilar.BASE_DIR))
    try:
        limite = time.monotonic() + 60
        while not listo.exists() and time.monotonic() < limite:
            time.sleep(0.05)
        assert listo.exists(), "el otro proceso no tomó el cerrojo"
        proc = _ProcesadorFalso()
        vig = Vigilante(raiz, staging=tmp_path / "stg", registro=Registro(reg),
                        procesar=proc, espera_estable_s=0, dormir=lambda s: None)
        e = vig.escanear_ahora()
        assert e["ocupado"] is True and proc.lotes == [], "procesó con otro proceso en curso"

        # La CLI en el mismo momento: sale con error y lo dice.
        monkeypatch.setenv("MUSIFLIX_DATA_DIR", str(tmp_path / "datos"))
        monkeypatch.setenv(config.VAR_STAGING, str(tmp_path / "stg"))
        monkeypatch.delenv(config.VAR_ITUNES, raising=False)
        monkeypatch.setattr(vigilar, "ENV_FILE", tmp_path / "no-existe.env")
        assert vigilar.main(["--carpeta", str(raiz)]) == vigilar.FALLO
        assert "Otro proceso" in capsys.readouterr().out
    finally:
        otro.wait(60)
    e = vig.escanear_ahora()
    assert e["ocupado"] is False and proc.lotes == [["a.mp3"]], "soltó el cerrojo y no siguió"


def test_dos_vigilantes_sobre_el_mismo_registro_no_pierden_nada(tmp_path):
    """Como la CLI y el server: dos `Registro` cargados ANTES de que el otro escriba."""
    raiz = tmp_path / "entrada"
    for i in range(4):
        _escribir(raiz / f"t{i}.mp3")
    reg = tmp_path / "reg.json"
    proc = _ProcesadorFalso()
    a = Vigilante(raiz, staging=tmp_path / "stg", registro=Registro(reg), procesar=proc,
                  espera_estable_s=0, dormir=lambda s: None, max_por_ciclo=1)
    b = Vigilante(raiz, staging=tmp_path / "stg", registro=Registro(reg), procesar=proc,
                  espera_estable_s=0, dormir=lambda s: None, max_por_ciclo=1)
    # Primero turnándose (determinista): cada uno tiene que ver lo que escribió el otro.
    for _ in range(2):
        a.escanear_ahora()
        b.escanear_ahora()
    hechos = sorted(n for lote in proc.lotes for n in lote)
    assert hechos == ["t0.mp3", "t1.mp3", "t2.mp3", "t3.mp3"], "alguno se procesó dos veces"
    final = Registro(reg)
    assert all(final.entrada(str(raiz / f"t{i}.mp3"))["estado"] == PROCESADO for i in range(4)), \
        "se perdieron entradas del registro"

    # Después a la vez, con archivos nuevos: nunca dos tandas simultáneas.
    for i in range(4, 8):
        _escribir(raiz / f"t{i}.mp3")
    barrera = threading.Barrier(2)

    def correr(v):
        barrera.wait()
        for _ in range(40):
            v.escanear_ahora()

    hilos = [threading.Thread(target=correr, args=(v,)) for v in (a, b)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)
    assert proc.max_simultaneos == 1, "los dos procesaron a la vez"
    hechos = sorted(n for lote in proc.lotes for n in lote)
    assert hechos == [f"t{i}.mp3" for i in range(8)], "alguno se procesó dos veces o ninguna"


# --- auditoría f65: fallos pasajeros y fallos del pipeline --------------------------------


class _Reloj:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _falla_siempre(tipo):
    def procesar(rutas, staging, reporte, timeout_s, cancelar=None):
        procesar.llamadas += 1
        return ResultadoLote(False, motivo=f"falló ({tipo})", tipo=tipo)
    procesar.llamadas = 0
    return procesar


def test_un_tope_de_tiempo_se_reintenta_hasta_3_veces_con_espera(tmp_path):
    raiz = tmp_path / "entrada"
    a = _escribir(raiz / "a.mp3")
    reloj = _Reloj()
    proc = _falla_siempre(vigilar.TIMEOUT)
    vig = _vigilante(tmp_path, raiz, proc, reloj=reloj)
    vig.escanear_ahora()
    e = Registro(tmp_path / "reg.json").entrada(str(a))
    assert (e["intentos"], e["motivo_tipo"]) == (1, "timeout")
    reloj.t += vigilar.REINTENTO_BASE_S - 1
    vig.escanear_ahora()
    assert proc.llamadas == 1, "se reintentó antes de la espera"
    reloj.t += 2
    vig.escanear_ahora()
    assert proc.llamadas == 2
    reloj.t += 2 * vigilar.REINTENTO_BASE_S - 2        # la espera se duplica: todavía no
    vig.escanear_ahora()
    assert proc.llamadas == 2, "la segunda espera no se duplicó"
    reloj.t += 3
    vig.escanear_ahora()
    assert proc.llamadas == 3
    e = Registro(tmp_path / "reg.json").entrada(str(a))
    assert e["intentos"] == 3 and e["proximo_intento"] is None
    reloj.t += 10 * vigilar.REINTENTO_BASE_S
    vig.escanear_ahora()
    assert proc.llamadas == 3, "pasó de 3 intentos"


def test_un_error_de_io_tambien_se_reintenta(tmp_path):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "a.mp3")
    reloj = _Reloj()
    proc = _falla_siempre(vigilar.IO)
    vig = _vigilante(tmp_path, raiz, proc, reloj=reloj)
    vig.escanear_ahora()
    reloj.t += vigilar.REINTENTO_BASE_S + 1
    vig.escanear_ahora()
    assert proc.llamadas == 2


def test_un_error_del_pipeline_no_se_reintenta_hasta_que_cambie(tmp_path):
    raiz = tmp_path / "entrada"
    a = _escribir(raiz / "a.mp3")
    reloj = _Reloj()
    proc = _falla_siempre(vigilar.PIPELINE)
    vig = _vigilante(tmp_path, raiz, proc, reloj=reloj)
    vig.escanear_ahora()
    reloj.t += 100 * vigilar.REINTENTO_BASE_S
    vig.escanear_ahora()
    assert proc.llamadas == 1
    _escribir(a, b"arreglado" * 200)
    vig.escanear_ahora()
    assert proc.llamadas == 2, "cambió el archivo y no se reintentó"
    e = Registro(tmp_path / "reg.json").entrada(str(a))
    assert e["intentos"] == 1, "los intentos son por contenido: el archivo nuevo arranca de 1"


def test_los_reintentos_de_a_uno_sobreviven_a_un_reinicio(tmp_path):
    raiz = tmp_path / "entrada"
    for n in ("a.mp3", "b.mp3"):
        _escribir(raiz / n)
    proc = _ProcesadorFalso(falla_si=lambda rutas: len(rutas) > 1)
    vig = _vigilante(tmp_path, raiz, proc)
    # La tanda falla y el server se corta antes del ciclo siguiente.
    vig._procesar_lote(descubrir(raiz))
    assert proc.lotes == [["a.mp3", "b.mp3"]]
    reiniciado = _vigilante(tmp_path, raiz, proc)       # registro leído de disco
    reiniciado.escanear_ahora()
    assert proc.lotes[1:] == [["a.mp3"], ["b.mp3"]], "después de reiniciar los volvió a juntar"


def test_el_estado_cuenta_fallidos_y_nombra_los_ultimos_sin_rutas(tmp_path):
    raiz = tmp_path / "entrada"
    for n in ("uno.mp3", "dos.mp3", "tres.mp3"):
        _escribir(raiz / n)
    proc = _ProcesadorFalso(falla_si=lambda rutas: True)
    vig = _vigilante(tmp_path, raiz, proc)
    vig.escanear_ahora()
    e = vig.escanear_ahora()
    assert e["fallidos"] == 3
    assert sorted(e["ultimos_fallidos"]) == ["dos.mp3", "tres.mp3", "uno.mp3"]
    assert str(tmp_path) not in json.dumps(e)


# --- auditoría f65: motivos sin rutas, filas raras, contadores ----------------------------


@pytest.mark.parametrize("como", ["open", "raise"])
def test_el_motivo_del_hijo_no_lleva_la_ruta_ni_en_forma_repr(tmp_path, como):
    ruta = str(tmp_path / "sub" / "tema.mp3")
    cuerpo = f"open({ruta!r})" if como == "open" else f"raise FileNotFoundError({ruta!r})"
    res = vigilar.procesar_aparte([ruta], tmp_path / "stg", tmp_path / "r.html", 30,
                                  comando=[sys.executable, "-c", cuerpo])
    assert res.ok is False and "tema.mp3" in res.motivo
    assert str(tmp_path) not in res.motivo
    assert str(tmp_path).replace("\\", "\\\\") not in res.motivo, res.motivo
    assert res.tipo == vigilar.IO, "un FileNotFoundError es de E/S"


def test_un_error_de_programa_del_hijo_es_del_pipeline(tmp_path):
    res = vigilar.procesar_aparte([], tmp_path / "stg", tmp_path / "r.html", 30,
                                  comando=[sys.executable, "-c", "raise ValueError('audio raro')"])
    assert res.ok is False and res.tipo == vigilar.PIPELINE
    assert res.motivo == "el proceso falló: ValueError: audio raro"


def test_filas_con_otra_forma_quedan_fallidas_y_el_registro_se_guarda(tmp_path):
    raiz = tmp_path / "entrada"
    rutas = {n: _escribir(raiz / n) for n in ("bien.mp3", "texto.mp3", "lista.mp3", "nada.mp3")}

    def procesar(rs, staging, reporte, timeout_s, cancelar=None):
        return ResultadoLote(True, reporte=str(reporte), filas=[
            "no-soy-una-fila", 7,
            {"ruta_original": str(rutas["bien.mp3"]), "bpm": 128.04, "camelot": "8A"},
            {"ruta_original": str(rutas["texto.mp3"]), "bpm": "no-es-un-número"},
            {"ruta_original": str(rutas["lista.mp3"]), "bpm": [128]},
            {"ruta_original": 42}])

    _vigilante(tmp_path, raiz, procesar).escanear_ahora()
    reg = Registro(tmp_path / "reg.json")                # de disco: se guardó
    bien = reg.entrada(str(rutas["bien.mp3"]))
    assert bien["estado"] == PROCESADO and bien["bpm"] == 128.0 and bien["camelot"] == "8A"
    for n in ("texto.mp3", "lista.mp3", "nada.mp3"):
        e = reg.entrada(str(rutas[n]))
        assert e["estado"] == FALLIDO and e["motivo_tipo"] == "pipeline", (n, e)
        assert "fila" in e["motivo"]


def test_el_estado_cuenta_los_que_estan_a_medio_bajar(tmp_path):
    raiz = tmp_path / "entrada"
    _escribir(raiz / "listo.mp3")
    _escribir(raiz / "a.mp3.part")
    _escribir(raiz / "b.temp.mp3")
    _escribir(raiz / "sub" / "c.crdownload")
    _escribir(raiz / ".oculto.mp3")                    # oculto no es "a medio bajar"
    e = _vigilante(tmp_path, raiz, _ProcesadorFalso()).escanear_ahora()
    assert e["ignorados"] == 3


def test_dos_escritores_del_registro_no_chocan_con_el_temporal(tmp_path):
    """Sin el cerrojo del ciclo (p. ej. el server guardando mientras la CLI arranca): cada
    escritor usa su propio temporal y ninguno revienta con PermissionError en Windows."""
    reg = tmp_path / "reg.json"
    errores = []

    def escribir(n):
        r = Registro(reg)
        r.marcar(vigilar.Foto(str(tmp_path / f"t{n}.mp3"), 1, 1), PROCESADO)
        for _ in range(60):
            try:
                r.guardar()
            except OSError as e:
                errores.append(repr(e))

    hilos = [threading.Thread(target=escribir, args=(n,)) for n in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(60)
    assert errores == []
    assert json.loads(reg.read_text(encoding="utf-8"))["version"] == 2


def test_un_registro_tomado_no_se_aparta_ni_se_pisa(tmp_path, monkeypatch):
    """Tomado por otro proceso (PermissionError que dura) no es "ilegible": no se renombra,
    y el ciclo no procesa ni escribe encima."""
    raiz = tmp_path / "entrada"
    _escribir(raiz / "a.mp3")
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"version": 2, "archivos": {}, "solos": []}), encoding="utf-8")
    antes = reg.read_bytes()
    proc = _ProcesadorFalso()
    vig = _vigilante(tmp_path, raiz, proc)

    def tomado(self):
        raise PermissionError(13, "Acceso denegado")

    monkeypatch.setattr(Registro, "_leer", tomado)
    monkeypatch.setattr(vigilar.time, "sleep", lambda s: None)
    e = vig.escanear_ahora()
    assert proc.lotes == [], "procesó sin poder leer el registro"
    assert reg.read_bytes() == antes, "el registro se pisó"
    assert list(tmp_path.glob("reg.json.ilegible-*")) == [], "un registro tomado se apartó"
    assert e["ultimo_error"] == "no pude leer el registro de lo ya procesado"
