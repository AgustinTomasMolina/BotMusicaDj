"""Tonalidad (key) + Camelot + compatibilidad armónica.

Krumhansl-Schmuckler: correlación del perfil cromático (chroma CQT sobre la ventana
central) contra los 24 perfiles de tonalidad.

HPSS: se probó separar lo armónico antes del chroma (la idea era "sacar el kick que
ensucia el chroma"). Medido contra el ground truth de Rekordbox (45 tracks reales), NO
ayuda: acuerdo exacto 40.0%→46.7% y compatible 51.1%→60.0% AL SACARLO, estabilidad entre
tramos 73.5%→76.5%, y 11× más rápido (p95 6.99 s→0.82 s, clave para el §4 ≤10 s). El
chroma sobre 90 s ya promedia el kick; el `harmonic()` remueve transitorios tonales que
ayudaban. Queda como opción (`hpss=True`) pero apagado por defecto.
"""
from collections import Counter

import numpy as np

# librosa se importa DENTRO de `ranking` (lo único que lo usa): así `compat_camelot` y el
# resto de la lógica de Camelot se pueden importar sin librosa instalado. La etapa B del
# benchmark (`benchmark.evaluar`) depende de eso para correr en máquinas sin el stack de
# audio — es CSV contra CSV, no tiene por qué arrastrar numba.

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


# Se analiza la ventana central: la tonalidad de un track de techno no cambia, y el chroma
# sobre el tema completo rompe el umbral de tiempo (§4 ≤10 s/track); 90 s da el mismo
# resultado mucho más rápido (auditoría Fable C1).
_VENTANA_S = 90


def ventana_central(y: np.ndarray, sr: int, segundos: int = _VENTANA_S) -> np.ndarray:
    """Recorte central de `segundos`. Si el audio es más corto, lo devuelve entero."""
    win = int(segundos * sr)
    if len(y) <= win:
        return y
    mid = len(y) // 2
    return y[mid - win // 2: mid + win // 2]


def ranking(y: np.ndarray, sr: int, hpss: bool = False) -> list[tuple[float, str, str]]:
    """Las 24 correlaciones Krumhansl `(corr, nota, modo)`, de mejor a peor.

    Recibe el audio YA RECORTADO — no ventanea. Es el cálculo crudo que usan tanto `tono`
    como `tono_consenso`; está expuesto para que las herramientas de análisis no tengan que
    duplicarlo (si se duplica, tarde o temprano mide algo distinto del motor).
    Lista vacía si el chroma es constante (silencio): ahí corrcoef da NaN para todo.
    """
    import librosa  # perezoso: ver la nota de los imports del módulo

    if hpss:
        y = librosa.effects.harmonic(y)  # separa lo armónico del percusivo (kick)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)

    out = []
    for i in range(12):
        for perfil, modo in ((_MAJ, "maj"), (_MIN, "min")):
            corr = float(np.corrcoef(np.roll(perfil, i), chroma)[0, 1])
            if np.isfinite(corr):
                out.append((corr, NOTAS[i], modo))
    out.sort(key=lambda t: -t[0])
    return out


def tono(y: np.ndarray, sr: int, hpss: bool = False) -> dict:
    """Devuelve {nota, modo, camelot, confianza}. `confianza` = correlación del mejor
    perfil (0..1); baja confianza → mostrar atenuado o con '?' en la UI (spec §6).
    Sin señal armónica (silencio) devuelve nota/modo None y camelot '?'.
    `hpss=True` reactiva la separación armónica (medido: no ayuda; ver docstring del módulo).

    OJO con `confianza`: medida contra la estabilidad entre tramos NO predice nada
    (Pearson +0.02). Ver `tono_consenso`, cuya confianza sí es interpretable.
    """
    orden = ranking(ventana_central(y, sr), sr, hpss=hpss)
    if not orden:  # chroma constante (silencio)
        return {"nota": None, "modo": None, "camelot": "?", "confianza": 0.0}

    corr, nota, modo = orden[0]
    return {"nota": nota, "modo": modo,
            "camelot": _CAMELOT.get((nota, modo), "?"),
            "confianza": round(max(0.0, corr), 3)}


# Tramos del consenso. 3 × 45 s ≈ 1.6 s/track medidos, cómodo dentro del §4 (≤10 s).
# Con HPSS prendido esto costaba ~20 s y era inviable; por eso el consenso se pudo recién
# después de sacarlo.
_N_TRAMOS = 3
_VENTANA_TRAMO_S = 45


def _tramos_disjuntos(y: np.ndarray, sr: int, n: int, ventana_s: int) -> list[np.ndarray]:
    """Parte el audio en `n` bloques iguales y devuelve la ventana centrada de cada uno.

    Disjuntos a propósito: si se solaparan compartirían audio y el acuerdo entre tramos
    saldría inflado — y ese acuerdo es justamente la confianza que se reporta.
    Devuelve [] si el audio no da para `n` ventanas sin pisarse.
    """
    win = int(ventana_s * sr)
    if y.size <= win * n:
        return []
    bloque = y.size // n
    tramos = []
    for k in range(n):
        ini_bloque = k * bloque
        centro = ini_bloque + bloque // 2
        ini = min(max(ini_bloque, centro - win // 2), ini_bloque + bloque - win)
        tramos.append(y[ini:ini + win])
    return tramos


def tono_consenso(y: np.ndarray, sr: int, n_tramos: int = _N_TRAMOS,
                  ventana_s: int = _VENTANA_TRAMO_S, hpss: bool = False) -> dict:
    """Tonalidad por consenso entre tramos disjuntos del track.

    Por qué existe: la `confianza` de `tono` es la correlación del mejor perfil Krumhansl,
    y medida sobre 43 tracks reales NO predice nada — correlación de Pearson +0.02 contra
    la estabilidad entre tramos, +0.12 para el margen top1-top2. Un número que no informa
    es peor que no mostrarlo (spec §6). Acá la confianza es la fracción de tramos que
    coinciden: 1.0 = los 3 tramos dicen lo mismo, 0.33 = los 3 dicen cosas distintas. Eso
    sí es interpretable y accionable en la UI.

    Devuelve {nota, modo, camelot, confianza, acuerdo, tramos}. `acuerdo` es
    "cuántos de cuántos" en crudo; `tramos` las keys de cada tramo, para poder mostrar
    por qué la confianza es baja.

    Si el track es muy corto para `n_tramos` ventanas disjuntas, cae a `tono()` y marca
    confianza 0.0 con acuerdo (0, 0): no hay evidencia de consenso, y fingirla sería
    inventar el dato.
    """
    tramos = _tramos_disjuntos(y, sr, n_tramos, ventana_s)
    if not tramos:
        base = tono(y, sr, hpss=hpss)
        return {**base, "confianza": 0.0, "acuerdo": (0, 0), "tramos": []}

    # Voto duro sobre la key de cada tramo; se guarda la correlación para desempatar.
    votos: list[str] = []
    soporte: dict[str, float] = {}
    detalle: dict[str, tuple[str, str]] = {}
    for tr in tramos:
        orden = ranking(tr, sr, hpss=hpss)
        if not orden:
            votos.append("?")
            continue
        corr, nota, modo = orden[0]
        cam = _CAMELOT.get((nota, modo), "?")
        votos.append(cam)
        soporte[cam] = soporte.get(cam, 0.0) + max(0.0, corr)
        detalle[cam] = (nota, modo)

    reales = [v for v in votos if v != "?"]
    if not reales:
        return {"nota": None, "modo": None, "camelot": "?",
                "confianza": 0.0, "acuerdo": (0, len(votos)), "tramos": votos}

    # Ganador: más votos; empate se rompe por correlación acumulada, no por orden de
    # aparición (eso haría que el resultado dependiera de cómo se recorre el track).
    top = max(Counter(reales).values())
    empatados = [k for k, n in Counter(reales).items() if n == top]
    ganador = max(empatados, key=lambda k: soporte.get(k, 0.0))

    nota, modo = detalle[ganador]
    return {"nota": nota, "modo": modo, "camelot": ganador,
            "confianza": round(top / len(votos), 3),
            "acuerdo": (top, len(votos)), "tramos": votos}


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
