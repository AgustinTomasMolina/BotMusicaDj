"""Detección de BPM.

`beat_track` de librosa cuantiza a la grilla de frames: con hop=512 el tempo salta en
pasos discretos y a 150 BPM el error llega a ~3 BPM (spec / proyecto-web-djs-motor-radio.md).
Se refina autocorrelando la envolvente de onsets y afinando el pico con interpolación
parabólica sub-frame. Devuelve BPM con decimal (redondear a entero es mentir — spec §6).
"""
import librosa
import numpy as np

HOP = 512
# Hop fino SOLO para la envolvente de onsets / autocorrelación: con hop=512 la grilla de
# frames (23 ms) es demasiado gruesa y la parábola sub-frame no alcanza (p95 ~0.61 BPM).
# Con hop=128 el período se resuelve mucho mejor: p95 baja a ~0.12 BPM (auditoría Fable I2).
HOP_AC = 128


def bpm_beat_track(y: np.ndarray, sr: int, hop: int = HOP) -> float:
    """Baseline: el tempo de librosa.beat.beat_track, tal cual (cuantizado)."""
    tempo = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop)[0]
    return float(np.atleast_1d(tempo)[0])


def bpm_refinado(y: np.ndarray, sr: int, hop: int = HOP, hop_ac: int = HOP_AC) -> float:
    """Refina el tempo por autocorrelación de la envolvente de onsets + interpolación
    parabólica del pico (resolución sub-frame). Mantiene la octava de beat_track.
    La autocorrelación usa `hop_ac` (fino) para no perder resolución del período."""
    tempo0 = bpm_beat_track(y, sr, hop)
    if tempo0 <= 0:
        return tempo0

    oe = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_ac)
    ac = librosa.autocorrelate(oe)

    # Lag (en frames de hop_ac) del período del beat según beat_track.
    lag0 = (60.0 / tempo0) * sr / hop_ac
    lo = max(2, int(lag0 * 0.85))
    hi = min(len(ac) - 2, int(lag0 * 1.15))
    if hi <= lo:
        return tempo0

    # Pico de la autocorrelación en la ventana alrededor de lag0.
    lag = lo + int(np.argmax(ac[lo:hi]))

    # Interpolación parabólica (sub-frame) con los vecinos del pico. Solo si el vértice
    # es un máximo (denom < 0); en un borde/mínimo no se interpola (auditoría Fable, menor).
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
    denom = a - 2 * b + c
    offset = 0.5 * (a - c) / denom if denom < 0 else 0.0
    lag_ref = lag + float(np.clip(offset, -1.0, 1.0))
    bpm_ref = 60.0 / (lag_ref * hop_ac / sr)

    # Resolver el NIVEL MÉTRICO por evidencia (arregla la confusión de tresillo, ratio 2/3:
    # a un tema de 170 BPM beat_track a veces le pega 113 = 170×2/3). Se elige el múltiplo
    # cuyo "peine" de autocorrelación (el período y sus múltiplos) tiene más soporte: el
    # tempo real alinea con la energía del beat y del compás; el 2/3 falso no. NO es ampliar
    # la tolerancia — un choque de tresillo en la mezcla es real; acá se corrige la detección.
    return _resolver_metrica(ac, lag_ref, bpm_ref)


# Niveles métricos a considerar. Medido sobre el ground truth real: TODOS los fallos de
# beat_track eran del tipo "lento" (detecta 2/3 del tempo real, ej. 170→113 por el tresillo).
# Por eso el único candidato es ×3/2 (acelerar). Incluir 2/3, 4/3 o la octava rompía tracks
# ya correctos sin arreglar ninguno nuevo (la octava, además, la tolera la métrica §4).
_MULTIPLOS = (1.0, 3 / 2)

# El ×3/2 solo se aplica si su evidencia supera a la del tempo detectado por este margen
# (evita flips por ruido de la autocorrelación en tracks que ya estaban bien).
_MARGEN = 1.03


def _peine(ac: np.ndarray, lag: float, k: int = 4) -> float:
    """Suma la autocorrelación en `lag` y sus primeros `k` múltiplos (energía del beat
    y del compás). Mide cuánta evidencia hay de que ESE período sea el real."""
    s = 0.0
    for m in range(1, k + 1):
        idx = int(round(m * lag))
        if idx < len(ac):
            s += max(float(ac[idx]), 0.0)
    return s


def _resolver_metrica(ac: np.ndarray, lag_ref: float, bpm_ref: float,
                      margen: float = _MARGEN) -> float:
    """Devuelve bpm_ref reinterpretado en el nivel métrico con más soporte de
    autocorrelación (dentro de un rango de baile). Solo overridea el tempo detectado si
    otro nivel lo supera por `margen` — así una ventaja marginal por ruido no lo mueve."""
    base = _peine(ac, lag_ref)
    mejor_bpm, mejor_score = bpm_ref, base
    for mult in _MULTIPLOS[1:]:
        bpm_c = bpm_ref * mult
        if not (60.0 <= bpm_c <= 210.0):     # fuera de rango de baile → descartar
            continue
        score = _peine(ac, lag_ref / mult)
        if score > mejor_score and score > base * margen:
            mejor_bpm, mejor_score = bpm_c, score
    return mejor_bpm
