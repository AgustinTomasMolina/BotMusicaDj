"""Análisis en segundo plano de los temas de una playlist con el motor (f53).

Una playlist importada (o con temas de la home) tiene archivos que el motor todavía no midió:
sin ese análisis no hay BPM/key medidos ni se puede abrir el editor de cues. Este módulo los
analiza en UN hilo, de a UN archivo, con el mismo camino que `python -m motor scan`
(`motor.cli.analizar_para_guardar` + `guardar_analisis`), así el BPM no depende de por dónde
entró el tema a la base. Licencia y origen quedan `no declarado` (CLAUDE.md, 2026-10-09).

Decisiones:
- Una cola para todo el proceso: pedir el análisis de dos playlists no lanza dos hilos (es CPU
  pura: dos a la vez solo tardan el doble). Un archivo pedido por dos playlists se analiza una
  vez y cuenta en el progreso de las dos.
- Los fallos se recuerdan por (archivo, mtime): el mismo archivo sin cambios no se reintenta
  en cada pedido (su motivo se muestra); si el archivo cambia, sí. En memoria: reiniciar el
  server los reintenta, que es lo que se quiere después de arreglar el archivo.
- Nada queda "analizando" colgado: el estado vive en memoria (al reiniciar no hay nada
  corriendo), un fallo a mitad se cuenta como fallo de ESE archivo y sigue con el próximo, y
  `detener()` (lifespan del server) corta después del archivo en curso.
- Los motivos de fallo no llevan rutas: se reemplaza la ruta por el nombre del archivo.

Lógica sin `server` ni `db`: el server inyecta `analizar(ruta)` y `al_guardar(ruta)`.
"""
from __future__ import annotations

import logging
import os
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("bot_web")


def clave(ruta: str) -> str:
    return os.path.normcase(os.path.abspath(ruta))


def mtime(ruta: str) -> float | None:
    try:
        return os.stat(ruta).st_mtime
    except OSError:
        return None


def sin_rutas(texto: str, ruta: str) -> str:
    """El motivo de un fallo sin la ruta del archivo (queda el nombre)."""
    nombre = Path(ruta).name
    for forma in {ruta, os.path.abspath(ruta), ruta.replace("\\", "/"),
                  os.path.abspath(ruta).replace("\\", "/")}:
        texto = texto.replace(forma, nombre)
    return texto[:300]


@dataclass
class Tarea:
    ruta: str
    etiqueta: str
    pids: set[int] = field(default_factory=set)
    items: dict[int, int] = field(default_factory=dict)      # pid → item_id


@dataclass
class _Progreso:
    total: int = 0
    hechos: int = 0
    fallidos: list[dict] = field(default_factory=list)


class AnalizadorFondo:
    def __init__(self, analizar: Callable[[str], str | None],
                 al_guardar: Callable[[str], None] | None = None) -> None:
        """`analizar(ruta)` analiza y GUARDA en la base; devuelve None si anduvo o el motivo.
        `al_guardar(ruta)` se llama después de cada análisis que anduvo."""
        self._analizar = analizar
        self._al_guardar = al_guardar
        self._lock = threading.Lock()
        self._cola: deque[Tarea] = deque()
        self._en_cola: dict[str, Tarea] = {}
        self._actual: Tarea | None = None
        self._hilo: threading.Thread | None = None
        self._corriendo = False
        self._parar = threading.Event()
        self._progreso: dict[int, _Progreso] = {}
        self._fallos: dict[tuple[str, float], str] = {}

    # -- consultas ------------------------------------------------------------------------

    def motivo_fallo(self, ruta: str) -> str | None:
        """Por qué falló el análisis de este archivo TAL COMO ESTÁ HOY, o None."""
        m = mtime(ruta)
        if m is None:
            return None
        with self._lock:
            return self._fallos.get((clave(ruta), m))

    def estado_de(self, ruta: str) -> str | None:
        """'analizando' (es el archivo en curso), 'en-cola' o None."""
        k = clave(ruta)
        with self._lock:
            if self._actual is not None and clave(self._actual.ruta) == k:
                return "analizando"
            return "en-cola" if k in self._en_cola else None

    def progreso(self, pid: int) -> dict:
        with self._lock:
            p = self._progreso.get(pid) or _Progreso()
            actual = self._actual if self._actual and pid in self._actual.pids else None
            pendientes = sum(1 for t in self._en_cola.values() if pid in t.pids)
            corriendo = actual is not None or pendientes > 0
            return {"corriendo": corriendo, "hechos": p.hechos, "total": p.total,
                    "actual": actual.etiqueta if actual else None,
                    "fallidos": list(p.fallidos)}

    # -- encolar --------------------------------------------------------------------------

    def encolar(self, pid: int, tareas: list[tuple[int, str, str]]) -> dict:
        """Encola `(item_id, ruta, etiqueta)` de la playlist `pid`. Idempotente: un archivo
        que ya está en la cola o analizándose no se duplica, y uno que falló sin cambiar desde
        entonces no se reintenta. Arranca el hilo si hace falta. Devuelve el progreso y
        `encolados`: cuántos de ESTA playlist se sumaron de verdad."""
        with self._lock:
            # Si la corrida anterior de esta playlist terminó, el progreso arranca de cero; si
            # sigue (otro pedido mientras analiza), se le suma lo nuevo.
            prog = self._progreso.get(pid)
            sigue = (any(pid in t.pids for t in self._en_cola.values())
                     or (self._actual is not None and pid in self._actual.pids))
            if prog is None or not sigue:
                prog = self._progreso[pid] = _Progreso()
            nuevos = 0
            for item_id, ruta, etiqueta in tareas:
                k = clave(ruta)
                m = mtime(ruta)
                if m is not None and (k, m) in self._fallos:
                    continue
                if self._actual is not None and clave(self._actual.ruta) == k:
                    t = self._actual
                elif k in self._en_cola:
                    t = self._en_cola[k]
                else:
                    t = Tarea(ruta, etiqueta)
                    self._cola.append(t)
                    self._en_cola[k] = t
                if pid not in t.pids:
                    t.pids.add(pid)
                    t.items[pid] = item_id
                    prog.total += 1
                    nuevos += 1
            # `_corriendo` y no `is_alive()`: el hilo que vació la cola baja la bandera con el
            # lock tomado, pero puede seguir "vivo" un instante después; mirando is_alive() una
            # tarea encolada justo ahí quedaba esperando para siempre.
            if self._cola and not self._corriendo:
                self._corriendo = True
                self._parar.clear()
                self._hilo = threading.Thread(target=self._correr, name="analisis-playlists",
                                              daemon=True)
                self._hilo.start()
        return {**self.progreso(pid), "encolados": nuevos}

    # -- hilo -----------------------------------------------------------------------------

    def _correr(self) -> None:
        try:
            self._bucle()
        finally:
            with self._lock:
                self._actual = None
                self._corriendo = False

    def _bucle(self) -> None:
        while not self._parar.is_set():
            with self._lock:
                if not self._cola:
                    self._actual = None
                    self._corriendo = False
                    return
                t = self._cola.popleft()
                self._en_cola.pop(clave(t.ruta), None)
                self._actual = t
            try:
                motivo = self._analizar(t.ruta)
            except Exception as e:  # noqa: BLE001 — un archivo raro no frena la cola
                motivo = f"{type(e).__name__}: {e}"
            if not motivo and self._al_guardar is not None:
                try:
                    self._al_guardar(t.ruta)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"⚠️ Análisis: después de guardar: {type(e).__name__}: {e}")
            m = mtime(t.ruta)
            with self._lock:
                k = clave(t.ruta)
                if motivo:
                    motivo = sin_rutas(str(motivo), t.ruta)
                    logger.warning(f"⚠️ Análisis: no pude analizar «{t.etiqueta}»: {motivo}")
                    if m is not None:
                        self._fallos[(k, m)] = motivo
                else:
                    self._fallos = {c: v for c, v in self._fallos.items() if c[0] != k}
                for pid in t.pids:
                    p = self._progreso.setdefault(pid, _Progreso())
                    p.hechos += 1
                    if motivo:
                        p.fallidos.append({"item_id": t.items.get(pid), "titulo": t.etiqueta,
                                           "motivo": motivo})
                self._actual = None

    def detener(self, timeout: float | None = 5.0) -> None:
        """Corta después del archivo en curso (apagado del server). La cola se descarta."""
        self._parar.set()
        with self._lock:
            self._cola.clear()
            self._en_cola.clear()
        hilo = self._hilo
        if hilo is not None and hilo is not threading.current_thread():
            hilo.join(timeout)

    def esperar(self, timeout: float | None = None) -> bool:
        """Espera a que la cola termine (para los tests). True si terminó."""
        hilo = self._hilo
        if hilo is not None:
            hilo.join(timeout)
        with self._lock:
            return self._actual is None and not self._cola
