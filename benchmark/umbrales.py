"""Umbrales de calidad del motor — el CONTRATO de la spec §4.

Son textuales del documento maestro (`claude/spec-dj-radio.md`). Un cambio que rompe
cualquiera de estos no entra. El benchmark (`python -m benchmark`) compara las métricas
medidas contra esta tabla y devuelve exit code ≠ 0 si se rompe alguno.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Umbral:
    clave: str        # nombre de la métrica en el dict de resultados
    nombre: str       # etiqueta legible
    limite: float
    op: str           # "<=", ">=", "<"
    unidad: str = ""


# Tabla de la spec §4 — el orden es el del documento.
UMBRALES: list[Umbral] = [
    Umbral("bpm_error_p95",        "Error de BPM (p95)",                 1.0, "<=", "BPM"),
    Umbral("tonalidad_exacta",     "Tonalidad exacta",                  85.0, ">=", "%"),
    Umbral("tonalidad_compatible", "Tonalidad compatible",              95.0, ">=", "%"),
    Umbral("transiciones_fuera_bpm", "Transiciones fuera de ±8% de BPM", 0.0, "<=", "%"),
    Umbral("choques_armonicos",    "Choques armónicos (compat < 0.4)",  10.0, "<=", "%"),
    Umbral("energia_spearman",     "Curva de energía (Spearman)",        0.5, ">="),
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
    ok: bool | None          # None = sin medir


def evaluar(metricas: dict) -> list[Fila]:
    """Compara un dict {clave: valor} contra los umbrales. Las métricas ausentes
    quedan como 'sin medir' (ok=None), no como cumplidas."""
    filas = []
    for u in UMBRALES:
        v = metricas.get(u.clave)
        ok = None if v is None else _cumple(float(v), u.op, u.limite)
        filas.append(Fila(u, v, ok))
    return filas


def veredicto(filas: list[Fila]) -> int:
    """Exit code: 1 si algún umbral se rompió, 2 si falta medir algo, 0 si todo OK."""
    if any(f.ok is False for f in filas):
        return 1
    if any(f.ok is None for f in filas):
        return 2
    return 0
