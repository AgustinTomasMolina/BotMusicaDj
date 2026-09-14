"""Umbrales de calidad del motor — el CONTRATO de la spec §4.

Son textuales del documento maestro (`claude/spec-dj-radio.md`). Un cambio que rompe
cualquiera de estos no entra. El benchmark (`python -m benchmark`) compara las métricas
medidas contra esta tabla y devuelve exit code ≠ 0 si se rompe alguno.

Umbrales "a calibrar"
---------------------
Una métrica puede estar en el contrato antes de tener número: la spec la define, pero el
límite sale de escuchar (regla de oro: el oído gana). Se escribe con ``limite=None`` y
``calibrar`` diciendo DÓNDE se calibra:

    Umbral("energia_desvio_curva", "...", None, "<=", calibrar="tarea 14")

Una fila así se mide y se reporta ("umbral a calibrar (tarea 14)"), pero NO entra al
veredicto: ni lo rompe ni cuenta como "falta medir" (si no, el exit code del benchmark
quedaría en 2 para siempre). Cuando se calibra se cambia UNA línea — el ``None`` por el
número y se borra ``calibrar`` — y desde ahí bloquea como cualquier otra. `Umbral`
rechaza las dos combinaciones inconsistentes (número y ``calibrar`` a la vez, o ninguno),
así una calibración a medias no pasa en silencio.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Umbral:
    clave: str             # nombre de la métrica en el dict de resultados
    nombre: str            # etiqueta legible
    limite: float | None   # None = a calibrar (ver `calibrar`)
    op: str                # "<=", ">=", "<"
    unidad: str = ""
    calibrar: str = ""     # dónde se calibra un umbral sin número, p.ej. "tarea 14"

    def __post_init__(self):
        if self.limite is None and not self.calibrar:
            raise ValueError(f"{self.clave}: un umbral sin límite tiene que decir dónde se calibra")
        if self.limite is not None and self.calibrar:
            raise ValueError(f"{self.clave}: tiene límite {self.limite:g} y a la vez "
                             f"'a calibrar ({self.calibrar})' — calibrado a medias")

    @property
    def a_calibrar(self) -> bool:
        return self.limite is None

    def objetivo(self) -> str:
        """El objetivo legible: '<= 1 BPM', o 'a calibrar (tarea 14)'."""
        if self.a_calibrar:
            return f"a calibrar ({self.calibrar})"
        u = f" {self.unidad}" if self.unidad else ""
        return f"{self.op} {self.limite:g}{u}"


# Tabla de la spec §4 — el orden es el del documento.
UMBRALES: list[Umbral] = [
    Umbral("bpm_error_p95",        "Error de BPM (p95)",                 1.0, "<=", "BPM"),
    # Tonalidad: medida SOLO sobre los tracks con acuerdo unánime de tono_consenso
    # (spec §4, cambio 2026-09-14). La cobertura de ese subconjunto la reporta evaluar.
    Umbral("tonalidad_exacta",     "Tonalidad exacta (unánimes)",       85.0, ">=", "%"),
    Umbral("tonalidad_compatible", "Tonalidad compatible (unánimes)",   95.0, ">=", "%"),
    Umbral("transiciones_fuera_bpm", "Transiciones fuera de ±8% de BPM", 0.0, "<=", "%"),
    Umbral("choques_armonicos",    "Choques armónicos (compat < 0.4)",  10.0, "<=", "%"),
    Umbral("energia_desvio_curva", "Curva de energía (desvío medio)",   None, "<=",
           calibrar="tarea 14"),
    Umbral("energia_spearman_ascendente", "Curva de energía (Spearman ↑)", 0.5, ">="),
    Umbral("tiempo_analisis_s",    "Tiempo de análisis",                10.0, "<=", "s/track"),
    Umbral("latencia_radio_ms",    "Latencia de la radio (10k tracks)", 200.0, "<", "ms"),
]


def _cumple(valor: float, op: str, limite: float) -> bool:
    if op == "<=":
        return valor <= limite
    if op == ">=":
        return valor >= limite
    if op == "<":
        return valor < limite
    raise ValueError(f"operador desconocido: {op}")


@dataclass
class Fila:
    umbral: Umbral
    valor: float | None      # None = la métrica no vino medida
    ok: bool | None          # None = sin medir, o umbral a calibrar (no hay contra qué)


def evaluar(metricas: dict) -> list[Fila]:
    """Compara un dict {clave: valor} contra los umbrales. Las métricas ausentes
    quedan como 'sin medir' (ok=None), no como cumplidas. Las de umbral a calibrar
    llevan su valor pero ok=None: no hay límite contra el cual compararlas."""
    filas = []
    for u in UMBRALES:
        v = metricas.get(u.clave)
        ok = None if v is None or u.a_calibrar else _cumple(float(v), u.op, u.limite)
        filas.append(Fila(u, v, ok))
    return filas


def veredicto(filas: list[Fila]) -> int:
    """Exit code: 1 si algún umbral se rompió, 2 si falta medir algo, 0 si todo OK.

    Los umbrales a calibrar no participan: se reportan, pero no rompen ni faltan."""
    contrato = [f for f in filas if not f.umbral.a_calibrar]
    if any(f.ok is False for f in contrato):
        return 1
    if any(f.ok is None for f in contrato):
        return 2
    return 0
