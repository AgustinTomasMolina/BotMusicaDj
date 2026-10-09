"""Tests de la cola de análisis en segundo plano (`playlist_analisis.py`, f53).

Con un `analizar` de mentira que registra qué y cuándo se analizó: lo que se prueba acá es la
cola (uno a la vez, progreso, dedupe, fallos, cortar), no la medición. La medición real (BPM
del generador sintético) está en `tests/test_server_importar.py`.
"""
import os
import threading
import time

from playlist_analisis import AnalizadorFondo


class Doble:
    """`analizar` falso: tarda un poco, cuenta cuántos corren a la vez y falla a pedido."""

    def __init__(self, fallar=(), explotar=(), espera=0.02):
        self.fallar, self.explotar, self.espera = set(fallar), set(explotar), espera
        self.orden, self.max_juntos, self._juntos = [], 0, 0
        self._lock = threading.Lock()
        self.liberar = threading.Event()
        self.liberar.set()

    def __call__(self, ruta):
        with self._lock:
            self._juntos += 1
            self.max_juntos = max(self.max_juntos, self._juntos)
        try:
            self.liberar.wait(5)
            time.sleep(self.espera)
            self.orden.append(os.path.basename(ruta))
            if os.path.basename(ruta) in self.explotar:
                raise RuntimeError(f"no pude abrir {ruta}")
            if os.path.basename(ruta) in self.fallar:
                return f"ValueError: archivo raro en {ruta}"
            return None
        finally:
            with self._lock:
                self._juntos -= 1


def _archivos(tmp_path, *nombres):
    out = []
    for n in nombres:
        p = tmp_path / n
        p.write_bytes(b"x")
        out.append(str(p))
    return out


def test_uno_a_la_vez_en_orden_y_con_progreso(tmp_path):
    rutas = _archivos(tmp_path, "a.wav", "b.wav", "c.wav")
    doble = Doble()
    guardados = []
    an = AnalizadorFondo(doble, guardados.append)
    # De a un pedido por tema, mientras el primero se analiza: cada pedido no lanza otro hilo.
    for i, ruta in enumerate(rutas):
        r = an.encolar(1, [(10 + i, ruta, f"tema {i}")])
    assert r["encolados"] == 1 and r["total"] == 3
    assert an.esperar(10)
    assert doble.orden == ["a.wav", "b.wav", "c.wav"]
    assert doble.max_juntos == 1, "dos análisis a la vez"
    assert guardados == rutas, "al_guardar se llama con cada archivo que anduvo"
    p = an.progreso(1)
    assert (p["corriendo"], p["hechos"], p["total"], p["actual"], p["fallidos"]) == \
        (False, 3, 3, None, [])


def test_dos_playlists_comparten_la_cola_y_no_duplican(tmp_path):
    a, b = _archivos(tmp_path, "a.wav", "b.wav")
    doble = Doble()
    doble.liberar.clear()                       # que no termine antes del segundo pedido
    an = AnalizadorFondo(doble)
    an.encolar(1, [(1, a, "A"), (2, b, "B")])
    r = an.encolar(2, [(7, b, "B")])
    assert r["encolados"] == 1 and r["corriendo"]
    assert an.encolar(1, [(1, a, "A")])["encolados"] == 0, "pedir de nuevo no duplica"
    doble.liberar.set()
    assert an.esperar(10)
    assert sorted(doble.orden) == ["a.wav", "b.wav"], "el archivo pedido por dos se analiza una vez"
    assert (an.progreso(1)["hechos"], an.progreso(2)["hechos"]) == (2, 1)


def test_estado_de_cada_archivo(tmp_path):
    a, b = _archivos(tmp_path, "a.wav", "b.wav")
    doble = Doble()
    doble.liberar.clear()
    an = AnalizadorFondo(doble)
    an.encolar(1, [(1, a, "A"), (2, b, "B")])
    for _ in range(200):
        if an.estado_de(a) == "analizando":
            break
        time.sleep(0.01)
    assert (an.estado_de(a), an.estado_de(b)) == ("analizando", "en-cola")
    assert an.progreso(1)["actual"] == "A"
    doble.liberar.set()
    assert an.esperar(10)
    assert (an.estado_de(a), an.estado_de(b)) == (None, None)


def test_un_fallo_no_cuelga_la_cola_y_el_motivo_no_lleva_la_ruta(tmp_path):
    a, b, c = _archivos(tmp_path, "a.wav", "roto.wav", "explota.wav")
    an = AnalizadorFondo(Doble(fallar={"roto.wav"}, explotar={"explota.wav"}))
    an.encolar(1, [(1, a, "A"), (2, b, "Roto"), (3, c, "Explota")])
    assert an.esperar(10)
    p = an.progreso(1)
    assert (p["corriendo"], p["hechos"]) == (False, 3)
    assert [(f["item_id"], f["titulo"]) for f in p["fallidos"]] == [(2, "Roto"), (3, "Explota")]
    assert p["fallidos"][0]["motivo"] == "ValueError: archivo raro en roto.wav"
    assert p["fallidos"][1]["motivo"] == "RuntimeError: no pude abrir explota.wav"
    assert str(tmp_path) not in repr(p), "un motivo con la ruta de la PC"
    assert an.motivo_fallo(b) == "ValueError: archivo raro en roto.wav"


def test_un_fallo_no_se_reintenta_hasta_que_el_archivo_cambia(tmp_path):
    (b,) = _archivos(tmp_path, "roto.wav")
    doble = Doble(fallar={"roto.wav"})
    an = AnalizadorFondo(doble)
    an.encolar(1, [(1, b, "Roto")])
    assert an.esperar(10)
    assert an.encolar(1, [(1, b, "Roto")])["encolados"] == 0
    os.utime(b, (1_000_000_000, 1_000_000_000))      # el archivo cambió
    assert an.motivo_fallo(b) is None
    assert an.encolar(1, [(1, b, "Roto")])["encolados"] == 1
    assert an.esperar(10)
    assert doble.orden == ["roto.wav", "roto.wav"]


def test_detener_corta_despues_del_archivo_en_curso(tmp_path):
    rutas = _archivos(tmp_path, "a.wav", "b.wav", "c.wav")
    doble = Doble(espera=0.2)
    an = AnalizadorFondo(doble)
    an.encolar(1, [(i, r, str(i)) for i, r in enumerate(rutas)])
    time.sleep(0.05)
    an.detener(5)
    assert an.esperar(5)
    assert doble.orden == ["a.wav"], doble.orden
    assert an.progreso(1)["corriendo"] is False, "quedó 'analizando' colgado"
    # Y se puede volver a pedir.
    an.encolar(1, [(9, rutas[2], "c")])
    assert an.esperar(5) and doble.orden[-1] == "c.wav"
