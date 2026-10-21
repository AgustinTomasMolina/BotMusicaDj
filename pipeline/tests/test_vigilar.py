"""Tests de la carpeta vigilada (f65, `pipeline/vigilar.py`).

Todo en carpetas temporales. Donde hace falta audio de verdad sale de `motor.sintetico`
(BPM conocido por construcción); el resto usa archivos con bytes cualquiera, porque lo que
se prueba ahí es QUÉ llega al pipeline, no qué mide. Sin red.
"""
import hashlib
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
    cancelar = threading.Event()
    cancelar.set()
    res = vigilar.procesar_aparte([], tmp_path / "stg", tmp_path / "r.html", 60,
                                  cancelar=cancelar,
                                  comando=[sys.executable, "-c", "import time; time.sleep(30)"])
    assert res.cancelado is True and res.ok is False


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
    copia = tmp_path / "salida" / "stg" / "Artista - Tema.wav"
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
