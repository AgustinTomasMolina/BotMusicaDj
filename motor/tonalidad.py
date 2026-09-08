"""Tonalidad (key) + Camelot + compatibilidad armónica.

Krumhansl-Schmuckler: correlación del perfil cromático contra los 24 perfiles de
tonalidad. Mejora para techno (tareas F1 #9/#10): HPSS previo — saca el kick que
ensucia el chroma — y chroma CQT. La precisión real se mide contra el ground truth de
Rekordbox (tarea #9, bloqueada por los archivos); acá está la implementación.
"""
import librosa
import numpy as np

NOTAS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Perfiles Krumhansl-Schmuckler (mayor / menor).
_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# (nota, modo) → código Camelot (rueda de mezcla armónica).
_CAMELOT = {
    ("C", "maj"): "8B", ("G", "maj"): "9B", ("D", "maj"): "10B", ("A", "maj"): "11B",
    ("E", "maj"): "12B", ("B", "maj"): "1B", ("F#", "maj"): "2B", ("C#", "maj"): "3B",
    ("G#", "maj"): "4B", ("D#", "maj"): "5B", ("A#", "maj"): "6B", ("F", "maj"): "7B",
    ("A", "min"): "8A", ("E", "min"): "9A", ("B", "min"): "10A", ("F#", "min"): "11A",
    ("C#", "min"): "12A", ("G#", "min"): "1A", ("D#", "min"): "2A", ("A#", "min"): "3A",
    ("F", "min"): "4A", ("C", "min"): "5A", ("G", "min"): "6A", ("D", "min"): "7A",
}


# Se analiza la ventana central: la tonalidad de un track de techno no cambia, y HPSS+CQT
# sobre el tema completo rompe el umbral de tiempo (§4 ≤10 s/track: 41 s medidos → 4.7 s
# con 90 s, mismo resultado — auditoría Fable C1).
_VENTANA_S = 90


def tono(y: np.ndarray, sr: int, hpss: bool = True) -> dict:
    """Devuelve {nota, modo, camelot, confianza}. `confianza` = correlación del mejor
    perfil (0..1); baja confianza → mostrar atenuado o con '?' en la UI (spec §6).
    Sin señal armónica (silencio) devuelve nota/modo None y camelot '?'."""
    win = int(_VENTANA_S * sr)
    if len(y) > win:
        mid = len(y) // 2
        y = y[mid - win // 2: mid + win // 2]
    if hpss:
        y = librosa.effects.harmonic(y)  # separa lo armónico del percusivo (kick)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)

    mejor = None  # (corr, nota, modo)
    for i in range(12):
        for perfil, modo in ((_MAJ, "maj"), (_MIN, "min")):
            corr = float(np.corrcoef(np.roll(perfil, i), chroma)[0, 1])
            if np.isfinite(corr) and (mejor is None or corr > mejor[0]):
                mejor = (corr, NOTAS[i], modo)

    if mejor is None:  # chroma constante (silencio) → corrcoef NaN para todo
        return {"nota": None, "modo": None, "camelot": "?", "confianza": 0.0}

    corr, nota, modo = mejor
    return {"nota": nota, "modo": modo,
            "camelot": _CAMELOT.get((nota, modo), "?"),
            "confianza": round(max(0.0, corr), 3)}


def _parse_camelot(c: str):
    if not c or c == "?":
        return None
    try:
        n, lado = int(c[:-1]), c[-1].upper()   # (número 1..12, lado 'A'/'B')
    except (ValueError, IndexError):
        return None
    if not (1 <= n <= 12 and lado in ("A", "B")):
        return None                            # "8C", "13A", "0A" → basura, no key
    return n, lado


def compat_camelot(a: str, b: str) -> float:
    """Compatibilidad armónica de dos keys Camelot, 0..1.
    misma = 1.0 · vecina (±1 mismo lado) o relativo (mismo n°, otro lado) = 0.8 ·
    ±2 mismo lado = 0.5 · resto = 0.2 (piso: un choque de key se tapa con EQ, NO anula la mezcla —
    por eso la key nunca es compuerta; la compuerta dura es el BPM). Key desconocida = 0.5
    (neutro: no penalizamos por falta de dato — 'un dato que miente es peor que ausente')."""
    pa, pb = _parse_camelot(a), _parse_camelot(b)
    if pa is None or pb is None:
        return 0.5
    na, la = pa
    nb, lb = pb
    if na == nb and la == lb:
        return 1.0
    dist = min((na - nb) % 12, (nb - na) % 12)   # distancia en la rueda
    if la == lb and dist == 1:
        return 0.8                                # vecina, mismo lado
    if na == nb and la != lb:
        return 0.8                                # relativo mayor/menor
    if la == lb and dist == 2:
        return 0.5                                # ±2 en el mismo lado
    return 0.2                                    # resto (incluye cruces de lado y diagonal) → piso
