"""¿La `confianza` de `motor.tonalidad.tono` predice que la tonalidad esté bien?

El problema: sobre audio real la confianza mediana da ~0.51 (baja) pero contra las pocas
referencias independientes que hay la tonalidad acierta. Las dos señales no pueden ser
ciertas a la vez, y con 2-3 referencias externas no alcanza para decidir cuál miente.

Este módulo agrega una señal que NO necesita ground truth: **estabilidad entre ventanas**.
La tonalidad de un track no cambia, así que si dos tramos distintos del mismo track dan
keys distintas, la detección es poco confiable — sin importar qué diga el número. Y al
revés: si todos los tramos coinciden, la detección es estable aunque la correlación sea baja.

Se comparan dos candidatos a "confianza":
  - `confianza`: la correlación del mejor perfil (lo que devuelve hoy `tono`).
  - `margen`:    diferencia entre la mejor y la segunda mejor correlación. La hipótesis es
                 que discrimina mejor: una correlación baja con margen amplio es una
                 decisión clara; una correlación alta con margen nulo es un empate.

    python -m benchmark.calibracion_confianza --audio ~/Downloads

NO modifica los audios: solo los lee.
"""
import argparse
import os
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Se leen los perfiles y la ventana del PROPIO motor para que no puedan divergir: si mañana
# se cambian ahí, esta calibración mide los nuevos. Son privados porque nadie más debería
# usarlos; esta es una herramienta de análisis del mismo repo, no un consumidor externo.
from motor.tonalidad import _CAMELOT, _MAJ, _MIN, _VENTANA_S, NOTAS, tono

EXTS = (".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg")

# Ventana de cada tramo de prueba. Más corta que la del motor a propósito: son 3 tramos por
# track y el costo lo domina el HPSS, que escala con la duración.
_VENTANA_TRAMO_S = 45


@dataclass
class Medicion:
    archivo: str
    duracion_s: float
    camelot: str            # lo que dice el motor sobre el track
    confianza: float
    margen: float
    keys_tramos: list[str]
    estabilidad: float      # fracción de tramos que coinciden con la key modal


def _ventana(y: np.ndarray, sr: int, segundos: int) -> np.ndarray:
    """Recorte central de `segundos`, igual que hace `tono()` (motor/tonalidad.py:38-41)."""
    win = int(segundos * sr)
    if len(y) <= win:
        return y
    mid = len(y) // 2
    return y[mid - win // 2: mid + win // 2]


def _correlaciones(y_ventana: np.ndarray, sr: int) -> list[tuple[float, str, str]]:
    """Las 24 correlaciones Krumhansl, de mejor a peor, sobre un array YA RECORTADO.

    Réplica exacta del cálculo de `tono()` (mismo HPSS, mismo chroma_cqt, mismos perfiles).
    Recibe la ventana ya hecha a propósito: el HPSS es lo caro y hacerlo sobre el track
    entero cuesta un orden de magnitud más que sobre la ventana que el motor realmente usa.
    """
    import librosa

    if y_ventana.size < sr:
        return []
    y_arm = librosa.effects.harmonic(y_ventana)
    chroma = librosa.feature.chroma_cqt(y=y_arm, sr=sr).mean(axis=1)
    out = []
    for i in range(12):
        for perfil, modo in ((_MAJ, "maj"), (_MIN, "min")):
            c = float(np.corrcoef(np.roll(perfil, i), chroma)[0, 1])
            if np.isfinite(c):
                out.append((c, NOTAS[i], modo))
    out.sort(key=lambda t: -t[0])
    return out


def _camelot_de(corr: list[tuple[float, str, str]]) -> str:
    if not corr:
        return "?"
    _, nota, modo = corr[0]
    return _CAMELOT.get((nota, modo), "?")


def medir_archivo(ruta: str, sr: int = 22050, n_tramos: int = 3,
                  ventana_tramo_s: int = _VENTANA_TRAMO_S) -> Medicion | None:
    import librosa

    try:
        y, _ = librosa.load(ruta, sr=sr, mono=True)
    except Exception:  # noqa: BLE001
        return None
    if y.size < sr * 5:
        return None

    # La respuesta del motor: misma ventana que usa `tono()`, una sola pasada de HPSS.
    corr = _correlaciones(_ventana(y, sr, _VENTANA_S), sr)
    if not corr:
        return None
    camelot = _camelot_de(corr)
    confianza = round(max(0.0, corr[0][0]), 3)          # igual que tono()
    margen = round(float(corr[0][0] - corr[1][0]), 3) if len(corr) > 1 else 0.0

    # Tramos DISJUNTOS: se parte el track en `n_tramos` bloques iguales y se toma la
    # ventana centrada dentro de cada bloque. Con la guarda de abajo cada bloque mide al
    # menos `win`, así que las ventanas no se pisan. Si se solaparan, compartirían audio y
    # el acuerdo entre tramos saldría inflado — justo la métrica que se quiere medir.
    keys = []
    win = ventana_tramo_s * sr
    if y.size > win * n_tramos:
        bloque = y.size // n_tramos
        for k in range(n_tramos):
            ini_bloque = k * bloque
            centro = ini_bloque + bloque // 2
            ini = min(max(ini_bloque, centro - win // 2), ini_bloque + bloque - win)
            keys.append(_camelot_de(_correlaciones(y[ini:ini + win], sr)))
    if keys:
        _, n = Counter(keys).most_common(1)[0]
        estabilidad = n / len(keys)
    else:
        estabilidad = float("nan")

    return Medicion(os.path.basename(ruta), round(y.size / sr, 1), camelot,
                    confianza, margen, keys, round(estabilidad, 3))


def verificar_replica(ruta: str, sr: int = 22050) -> tuple[str, str, float, float] | None:
    """Comprueba que `_correlaciones` reproduce exactamente lo que devuelve `tono()`.

    Si esto no coincide, todo el informe está midiendo otra cosa que el motor.
    """
    import librosa

    try:
        y, _ = librosa.load(ruta, sr=sr, mono=True)
    except Exception:  # noqa: BLE001
        return None
    oficial = tono(y, sr)
    corr = _correlaciones(_ventana(y, sr, _VENTANA_S), sr)
    if not corr:
        return None
    return (oficial["camelot"], _camelot_de(corr),
            float(oficial["confianza"]), round(max(0.0, corr[0][0]), 3))


def _corr(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return float("nan")
    return float(np.corrcoef(xs, ys)[0, 1])


def informe(meds: list[Medicion]) -> None:
    utiles = [m for m in meds if m.estabilidad == m.estabilidad]  # descarta NaN
    print(f"\n{'=' * 78}")
    print(f"Calibración de la confianza de tonalidad — {len(utiles)} tracks con tramos")
    print(f"{'=' * 78}")
    if not utiles:
        print("Sin tracks suficientemente largos para ventanear.")
        return

    conf = [m.confianza for m in utiles]
    marg = [m.margen for m in utiles]
    est = [m.estabilidad for m in utiles]
    print(f"confianza  : mediana {statistics.median(conf):.3f}  "
          f"min {min(conf):.3f}  max {max(conf):.3f}")
    print(f"margen     : mediana {statistics.median(marg):.3f}  "
          f"min {min(marg):.3f}  max {max(marg):.3f}")
    print(f"estabilidad: {100 * statistics.mean(est):.1f}% promedio  ·  "
          f"{sum(1 for e in est if e == 1.0)}/{len(est)} tracks con todos los tramos de acuerdo")

    print(f"\n{'-' * 78}\n¿Predicen la estabilidad?  (correlación de Pearson)")
    print(f"  confianza vs estabilidad : {_corr(conf, est):+.3f}")
    print(f"  margen    vs estabilidad : {_corr(marg, est):+.3f}")

    print(f"\n{'-' * 78}\nEstabilidad por tramo de confianza:")
    for lo, hi in ((0.0, 0.4), (0.4, 0.55), (0.55, 0.7), (0.7, 1.01)):
        g = [m for m in utiles if lo <= m.confianza < hi]
        if g:
            e = 100 * statistics.mean([m.estabilidad for m in g])
            print(f"  confianza [{lo:.2f}, {hi:.2f}) : {len(g):3} tracks · estabilidad {e:5.1f}%")

    print(f"\n{'-' * 78}\nEstabilidad por tercil de margen:")
    ms = sorted(marg)
    cortes = [ms[0], ms[len(ms) // 3], ms[2 * len(ms) // 3], ms[-1] + 1e-9]
    for lo, hi in zip(cortes, cortes[1:], strict=False):   # cortes[1:] es 1 más corto
        g = [m for m in utiles if lo <= m.margen < hi]
        if g:
            e = 100 * statistics.mean([m.estabilidad for m in g])
            print(f"  margen [{lo:.3f}, {hi:.3f}) : {len(g):3} tracks · estabilidad {e:5.1f}%")

    inest = [m for m in utiles if m.estabilidad < 1.0]
    if inest:
        print(f"\n{'-' * 78}\nTracks donde los tramos NO coinciden ({len(inest)}):")
        for m in sorted(inest, key=lambda m: m.estabilidad)[:15]:
            print(f"  {m.archivo[:42]:42} motor {m.camelot:>3} conf {m.confianza:.2f} "
                  f"margen {m.margen:.3f}  tramos {m.keys_tramos}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="benchmark.calibracion_confianza")
    ap.add_argument("--audio", required=True, type=Path, help="Carpeta con audios.")
    ap.add_argument("--tramos", type=int, default=3)
    ap.add_argument("--ventana-tramo", type=int, default=_VENTANA_TRAMO_S,
                    help=f"Segundos de cada tramo (default {_VENTANA_TRAMO_S}). Subirlo a "
                         f"{_VENTANA_S} usa la misma ventana que el motor, para descartar "
                         "que la inestabilidad sea artefacto de tramos cortos.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--verificar", type=int, default=3,
                    help="Cuántos tracks contrastar contra tono() para probar que la réplica coincide.")
    args = ap.parse_args(argv)

    rutas = sorted(str(p) for p in args.audio.rglob("*") if p.suffix.lower() in EXTS)
    if args.limit:
        rutas = rutas[:args.limit]

    if args.verificar:
        print(f"Verificando que la réplica coincida con tono() en {args.verificar} tracks:")
        for r in rutas[:args.verificar]:
            v = verificar_replica(r)
            if v is None:
                continue
            k_of, k_re, c_of, c_re = v
            ok = "OK" if (k_of == k_re and abs(c_of - c_re) < 1e-6) else "DISTINTO"
            print(f"  {os.path.basename(r)[:40]:40} tono()={k_of}/{c_of:.3f} "
                  f"replica={k_re}/{c_re:.3f}  {ok}", flush=True)

    meds = []
    for i, r in enumerate(rutas, 1):
        m = medir_archivo(r, n_tramos=args.tramos, ventana_tramo_s=args.ventana_tramo)
        if m is None:
            continue
        meds.append(m)
        print(f"  [{i}/{len(rutas)}] {m.archivo[:40]:40} {m.camelot:>3} "
              f"conf {m.confianza:.2f} margen {m.margen:.3f} "
              f"tramos {m.keys_tramos} estab {m.estabilidad:.2f}", flush=True)

    informe(meds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
