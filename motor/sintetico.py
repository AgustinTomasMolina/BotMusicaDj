"""Audio sintético con BPM y tonalidad CONOCIDOS.

Sirve para verificar el motor sin depender de archivos reales — la spec §5 es explícita:
"nunca inventar BPM ni tonalidad en un test: salen del ground truth o del generador de
audio sintético". Este es ese generador.
"""
import numpy as np

# Frecuencia (Hz) de la fundamental de cada nota, octava 2 (bajos de techno).
_NOTA_HZ = {
    "C": 65.41, "C#": 69.30, "D": 73.42, "D#": 77.78, "E": 82.41, "F": 87.31,
    "F#": 92.50, "G": 98.00, "G#": 103.83, "A": 110.00, "A#": 116.54, "B": 123.47,
}


def nota_hz(nota: str) -> float:
    return _NOTA_HZ[nota]


def click_track(bpm: float, dur: float = 30.0, sr: int = 22050,
                nota: str | None = None, seed: int = 0) -> tuple[np.ndarray, int]:
    """Genera un 'four-on-the-floor' sintético: un kick por beat a `bpm` exactos.
    Si se da `nota`, agrega una capa tonal en esa fundamental (para tests de tonalidad).
    Devuelve (y, sr). El BPM es EXACTO por construcción → sirve de ground truth."""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    y = np.zeros(n, dtype=np.float32)

    # Kick sintético: seno de 55 Hz con envolvente exponencial (~120 ms).
    kdur = int(0.12 * sr)
    tk = np.arange(kdur) / sr
    kick = (np.sin(2 * np.pi * 55 * tk) * np.exp(-tk * 28)).astype(np.float32)

    periodo = 60.0 / bpm
    t = 0.0
    while t < dur:
        i = int(round(t * sr))
        if i + kdur <= n:
            y[i:i + kdur] += kick
        t += periodo

    if nota is not None:  # capa tonal (fundamental + quinta) para detectar tonalidad
        tt = np.arange(n) / sr
        f0 = nota_hz(nota)
        y += (0.15 * np.sin(2 * np.pi * f0 * tt)).astype(np.float32)
        y += (0.08 * np.sin(2 * np.pi * f0 * 1.5 * tt)).astype(np.float32)

    y += (0.004 * rng.standard_normal(n)).astype(np.float32)  # ruido leve, realismo
    return y, sr
