"""Detección de BPM.

`beat_track` de librosa cuantiza a la grilla de frames: con hop=512 el tempo salta en
pasos discretos y a 150 BPM el error llega a ~3 BPM (spec / proyecto-web-djs-motor-radio.md).
Se refina autocorrelando la envolvente de onsets y afinando el pico con interpolación
parabólica sub-frame. Devuelve BPM con decimal (redondear a entero es mentir — spec §6).
"""
import librosa
import numpy as np

HOP = 512


def bpm_beat_track(y: np.ndarray, sr: int, hop: int = HOP) -> float:
    """Baseline: el tempo de librosa.beat.beat_track, tal cual (cuantizado)."""
    tempo = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop)[0]
    return float(np.atleast_1d(tempo)[0])


def bpm_refinado(y: np.ndarray, sr: int, hop: int = HOP) -> float:
    """Refina el tempo por autocorrelación de la envolvente de onsets + interpolación
    parabólica del pico (resolución sub-frame). Mantiene la octava de beat_track."""
    tempo0 = bpm_beat_track(y, sr, hop)
    if tempo0 <= 0:
        return tempo0

    oe = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    ac = librosa.autocorrelate(oe)

    # Lag (en frames) del período del beat según beat_track.
    lag0 = (60.0 / tempo0) * sr / hop
    lo = max(2, int(lag0 * 0.85))
    hi = min(len(ac) - 2, int(lag0 * 1.15))
    if hi <= lo:
        return tempo0

    # Pico de la autocorrelación en la ventana alrededor de lag0.
    lag = lo + int(np.argmax(ac[lo:hi]))

    # Interpolación parabólica (sub-frame) con los vecinos del pico.
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
    denom = a - 2 * b + c
    offset = 0.5 * (a - c) / denom if denom != 0 else 0.0
    lag_ref = lag + float(np.clip(offset, -1.0, 1.0))

    return 60.0 / (lag_ref * hop / sr)
