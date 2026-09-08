"""Tests del análisis de BPM contra audio sintético (BPM conocido — no inventado).

Corré:  py -m motor.tests.test_bpm     (o pytest)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402
from motor.sintetico import click_track  # noqa: E402
from motor.bpm import bpm_beat_track, bpm_refinado  # noqa: E402

BPMS = [120, 124, 128, 138, 144, 150]


def test_refinado_cumple_umbral():
    """El refinamiento por autocorrelación cumple el umbral de la spec §4 (p95 ≤ 1.0)."""
    errs = [abs(bpm_refinado(*click_track(float(b), dur=30)) - b) for b in BPMS]
    p95 = float(np.percentile(errs, 95))
    assert p95 <= 1.0, f"error p95 {p95:.2f} BPM > 1.0"


def test_refinado_mejor_que_beat_track():
    """El refinado erra menos que beat_track pelado (la premisa del diseño)."""
    e_pel, e_ref = [], []
    for b in BPMS:
        y, sr = click_track(float(b), dur=30)
        e_pel.append(abs(bpm_beat_track(y, sr) - b))
        e_ref.append(abs(bpm_refinado(y, sr) - b))
    assert np.mean(e_ref) < np.mean(e_pel)


def test_determinismo():
    """Misma entrada → mismo BPM (la spec exige determinismo)."""
    y, sr = click_track(150.0, dur=20, seed=7)
    assert bpm_refinado(y, sr) == bpm_refinado(y, sr)


if __name__ == "__main__":
    test_refinado_cumple_umbral()
    test_refinado_mejor_que_beat_track()
    test_determinismo()
    print("OK — todos los tests de BPM pasaron")
