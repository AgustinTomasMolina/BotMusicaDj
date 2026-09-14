"""Capa 1 completa — un archivo de audio → `TrackFeatures`.

Es lo único que el motor sabe hacer con un archivo: cargarlo, medirlo y devolver las
features crudas. Persistir es trabajo del store; ponerlo en contexto de la biblioteca,
también.

UN SOLO CAMINO DE MEDICIÓN (tarea 5.66 del roadmap). El pipeline llegó a calcular la
tonalidad por otro camino que el benchmark y hubo DOS keys distintas para el mismo track.
Por eso la carga y la medición de BPM y tonalidad viven acá, en `cargar` y
`medir_bpm_y_tono`, y `benchmark.analizar.analizar_uno` las llama en vez de tener su copia.
Lo que el benchmark aprueba es, por construcción, lo que el motor usa.
`motor/tests/test_analisis.py::test_motor_y_benchmark_miden_igual` falla si se separan.

Qué se mide y qué NO:

- BPM (`bpm_refinado`) y tonalidad (`tono`, o `tono_consenso` con `consenso=True`, el mismo
  flag y el mismo default que el benchmark y el pipeline).
- `energy_raw` y `rms`: RMS de la señal completa (`energia_rms`). Son el mismo número a
  propósito: `energy_raw` es el escalar que el store convierte en percentil, y armarlo
  combinando RMS con onsets o ratio percusivo sería inventar pesos sin benchmark que los
  respalde (spec §5: todo cambio de scoring viene con el número antes y después).
- `onset_rate`: onsets por segundo de `librosa.onset.onset_detect` sobre la señal completa.
  Verificado contra un kick sintético de decaimiento completo (el test da la tolerancia).
  OJO: sobre `sintetico.click_track` da ~2× el pulso, porque ese kick se corta de golpe a
  los 120 ms y el corte ES un transitorio real — no es un error de la medición.
- `embedding`: `embeddings.embed` sobre la MISMA ventana central que usa `tono`.
- `percussive_ratio`: **no se mide** y queda en `None`. Necesita HPSS, y con HPSS el
  análisis costaba ~20 s/track contra los ≤10 s del §4 (medido, `motor/tonalidad.py`).

NO modifica el archivo: solo lo lee (regla del proyecto).
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from motor.bpm import bpm_refinado
from motor.embeddings import embed
from motor.energia import energia_rms
from motor.modelos import TrackFeatures
from motor.tonalidad import _MAJ, _MIN, NOTAS, tono, tono_consenso, ventana_central

SR = 22050


def cargar(ruta: str | Path, sr: int = SR) -> np.ndarray | None:
    """Audio mono a `sr`. `None` si no se pudo decodificar o dura menos de 1 s.

    Es LA carga del motor y del benchmark: si uno cargara a otro sample rate o en estéreo,
    medirían audios distintos.
    """
    import librosa

    try:
        y, _ = librosa.load(str(ruta), sr=sr, mono=True)
    except Exception:  # noqa: BLE001 — cualquier archivo que no decodifica es "no cargó"
        return None
    if y.size < sr:  # < 1 s de audio: no sirve
        return None
    return y


def medir_bpm_y_tono(y: np.ndarray, sr: int = SR, consenso: bool = False) -> tuple[float, dict]:
    """BPM y tonalidad, por el único camino que mide el benchmark.

    Devuelve `(bpm, detección)`, donde la detección es el dict de `tono` / `tono_consenso`
    tal cual (`camelot`, `nota`, `modo`, `confianza`, y con consenso `acuerdo` y `tramos`).
    """
    bpm = float(bpm_refinado(y, sr))
    det = tono_consenso(y, sr) if consenso else tono(y, sr)
    return bpm, det


def _perfil_de_la_key(nota: str | None, modo: str | None) -> np.ndarray | None:
    """Un perfil cromático cuya mejor correlación Krumhansl es exactamente (nota, modo).

    Para qué: `embed` rota el chroma a la tónica que sale de `ranking_chroma(chroma)[0]`, y
    tiene que ser la MISMA tónica que reportó la tonalidad. `tono` no devuelve su chroma, así
    que se le pasa el propio perfil Krumhansl de la key detectada, que correlaciona 1.0
    consigo mismo y gana siempre. Con `consenso=True` esto importa: la key sale de un voto
    entre tramos y no de la ventana central, y derivarla de nuevo podría dar otra.

    `None` si no hubo key (silencio): `embed` sabe qué hacer y no se inventa una tónica.
    """
    if nota not in NOTAS or modo not in ("maj", "min"):
        return None
    return np.roll(_MAJ if modo == "maj" else _MIN, NOTAS.index(nota))


def onsets_por_segundo(y: np.ndarray, sr: int = SR) -> float:
    """Onsets detectados por segundo de audio, sobre la señal completa."""
    import librosa

    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="frames")
    return float(len(onsets)) / (y.size / sr)


def analizar_senal(y: np.ndarray, sr: int = SR, consenso: bool = False) -> TrackFeatures:
    """Features de una señal ya cargada. `analizar_archivo` es esto más la carga."""
    bpm, det = medir_bpm_y_tono(y, sr, consenso=consenso)
    rms = energia_rms(y)
    return TrackFeatures(
        bpm=bpm,
        key=det["camelot"],
        energy_raw=rms,
        embedding=embed(ventana_central(y, sr), sr,
                        chroma=_perfil_de_la_key(det.get("nota"), det.get("modo"))),
        rms=rms,
        onset_rate=onsets_por_segundo(y, sr),
        percussive_ratio=None,  # no medido: ver el docstring del módulo
    )


def analizar_archivo(ruta: str | Path, sr: int = SR,
                     consenso: bool = False) -> tuple[TrackFeatures, float] | None:
    """Analiza un archivo: `(features, duración en segundos)`.

    `None` si no carga o dura < 1 s, igual que `benchmark.analizar.analizar_uno`.
    """
    y = cargar(ruta, sr)
    if y is None:
        return None
    return analizar_senal(y, sr, consenso=consenso), y.size / sr


def calentar(sr: int = SR) -> float:
    """Fuerza la compilación JIT de numba sobre TODO el camino de `analizar_senal`.

    `benchmark.tiempo_analisis.calentar` solo calienta BPM y tonalidad, que es lo que ese
    módulo cronometra; el análisis del motor además pasa por MFCC, tonnetz, contraste y
    onsets, y sin calentarlos el primer track del scan paga la compilación y su tiempo
    miente. Devuelve lo que tardó.
    """
    from motor.sintetico import click_track

    t0 = time.perf_counter()
    y, _ = click_track(128.0, dur=10.0, sr=sr, nota="A", modo="min")
    analizar_senal(y, sr)
    return time.perf_counter() - t0
