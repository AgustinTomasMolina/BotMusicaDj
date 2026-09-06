"""Análisis de audio con librosa: BPM (tempo) y tono/key musical.

El tono se estima con el método Krumhansl-Schmuckler (correlación del perfil
cromático contra perfiles de tonalidades mayores/menores). Se mapea al código
Camelot para mezcla armónica estilo DJ.
"""
NOTAS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Perfiles Krumhansl-Schmuckler (mayor / menor)
_PERFIL_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_PERFIL_MIN = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

# Tonalidad -> código Camelot (rueda de mezcla armónica)
_CAMELOT = {
    ("C", "maj"): "8B", ("G", "maj"): "9B", ("D", "maj"): "10B", ("A", "maj"): "11B",
    ("E", "maj"): "12B", ("B", "maj"): "1B", ("F#", "maj"): "2B", ("C#", "maj"): "3B",
    ("G#", "maj"): "4B", ("D#", "maj"): "5B", ("A#", "maj"): "6B", ("F", "maj"): "7B",
    ("A", "min"): "8A", ("E", "min"): "9A", ("B", "min"): "10A", ("F#", "min"): "11A",
    ("C#", "min"): "12A", ("G#", "min"): "1A", ("D#", "min"): "2A", ("A#", "min"): "3A",
    ("F", "min"): "4A", ("C", "min"): "5A", ("G", "min"): "6A", ("D", "min"): "7A",
}


def bpm_rapido(archivo: str, dur: int = 30) -> int:
    """Solo BPM (sin tono) — más rápido. Para enriquecer muchos temas."""
    import numpy as np
    import librosa
    y, sr = librosa.load(archivo, mono=True, duration=dur)
    tempo = librosa.beat.beat_track(y=y, sr=sr)[0]
    return round(float(np.atleast_1d(tempo)[0]))


def bpm_y_tono(archivo: str, dur: int = 60) -> dict:
    """Devuelve {bpm, tono, camelot} analizando hasta `dur` segundos del audio."""
    import numpy as np
    import librosa

    y, sr = librosa.load(archivo, mono=True, duration=dur)

    tempo = librosa.beat.beat_track(y=y, sr=sr)[0]
    bpm = round(float(np.atleast_1d(tempo)[0]))

    chroma = librosa.feature.chroma_stft(y=y, sr=sr).mean(axis=1)
    maj = np.array(_PERFIL_MAJ)
    minp = np.array(_PERFIL_MIN)
    mejor = None  # (corr, nota, modo)
    for i in range(12):
        for perfil, modo in ((maj, "maj"), (minp, "min")):
            corr = float(np.corrcoef(np.roll(perfil, i), chroma)[0, 1])
            if mejor is None or corr > mejor[0]:
                mejor = (corr, NOTAS[i], modo)

    nota, modo = mejor[1], mejor[2]
    tono = f"{nota} {'menor' if modo == 'min' else 'mayor'}"
    return {"bpm": bpm, "tono": tono, "camelot": _CAMELOT.get((nota, modo), "?")}


if __name__ == "__main__":
    import sys
    r = bpm_y_tono(sys.argv[1])
    print(f"BPM: {r['bpm']}   Tono: {r['tono']} ({r['camelot']})")
