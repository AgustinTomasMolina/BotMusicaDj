"""Fixture reproducible: genera versiones recortadas de un track, tipo "radio edit".

Sirve para probar el caso peligroso de la detección de duplicados (tarea 5.25): MISMO
AUDIO con DISTINTA DURACIÓN. Un edit y el original comparten casi toda la señal, así que
la huella los va a ver igualísimos — la única defensa es la compuerta de duración, y su
umbral hay que validarlo con números.

Se recorta DEL MEDIO, no del final, a propósito: sacar del final cambia menos la huella
(que se calcula sobre una ventana fija cerca del principio) y daría un resultado
optimista. Un edit real suele acortar justamente el desarrollo del medio.

Como fixture:

    from calidad.tests.recortes import recortar_del_medio, escribir_recorte

Como barrido reproducible, para ver dónde deja de separar la compuerta:

    python -m calidad.tests.recortes --audio "ruta/al/track.wav"
"""
import argparse
import sys
from pathlib import Path

import numpy as np

from calidad.duplicados import SR_HUELLA, cargar_ficha, mismo_audio

# Recortes por defecto del barrido, en segundos. Cubren desde "casi nada" hasta un edit
# agresivo, pasando por el rango realista de un radio edit (30 s a 2 min).
CORTES_S = (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 15.0, 30.0, 60.0, 90.0, 120.0)


def recortar_del_medio(y: np.ndarray, sr: int, quitar_s: float,
                       posicion: float = 0.5) -> np.ndarray:
    """Saca `quitar_s` segundos del medio de `y` y pega las dos mitades.

    `posicion` es dónde se hace el corte (0.5 = la mitad exacta del track).
    """
    n_quitar = int(quitar_s * sr)
    if n_quitar <= 0 or n_quitar >= y.size:
        return y.copy()
    centro = int(y.size * posicion)
    ini = max(0, centro - n_quitar // 2)
    fin = min(y.size, ini + n_quitar)
    return np.concatenate([y[:ini], y[fin:]])


def escribir_recorte(y: np.ndarray, sr: int, destino: Path) -> Path:
    """Escribe un recorte a disco. Sobre una COPIA en memoria: nunca toca el original."""
    import soundfile as sf

    destino.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destino, y, sr)
    return destino


def generar_barrido(ruta_origen: str, destino_dir: Path, cortes_s=CORTES_S,
                    sr: int = SR_HUELLA, posicion: float = 0.5) -> list[tuple[float, Path]]:
    """Genera un archivo por cada recorte. Devuelve [(segundos quitados, ruta)]."""
    import librosa

    y, _ = librosa.load(ruta_origen, sr=sr, mono=True)
    salidas = []
    for q in cortes_s:
        recorte = recortar_del_medio(y, sr, q, posicion=posicion)
        destino = destino_dir / f"recorte_{q:06.1f}s{Path(ruta_origen).suffix or '.wav'}"
        if destino.suffix.lower() != ".wav":
            destino = destino.with_suffix(".wav")
        escribir_recorte(recorte, sr, destino)
        salidas.append((q, destino))
    return salidas


def barrido(ruta_origen: str, destino_dir: Path, cortes_s=CORTES_S,
            posicion: float = 0.5) -> list[dict]:
    """Compara el original contra cada recorte usando el MISMO camino que la herramienta."""
    original = cargar_ficha(ruta_origen)
    filas = []
    for quitar_s, ruta in generar_barrido(ruta_origen, destino_dir, cortes_s,
                                          posicion=posicion):
        recorte = cargar_ficha(str(ruta))
        agrupa, sim = mismo_audio(original, recorte)
        # La similitud cruda, sin la compuerta de duración, para ver qué ve la huella sola.
        from calidad.duplicados import similitud
        sim_huella = similitud(original.huella, recorte.huella)
        filas.append({
            "quitar_s": quitar_s,
            "dur_original": original.duracion_s,
            "dur_recorte": recorte.duracion_s,
            "dif_duracion": abs(original.duracion_s - recorte.duracion_s),
            "sim_huella": sim_huella,
            "sim_con_compuerta": sim,
            "agrupa": agrupa,
        })
    return filas


def _imprimir(filas: list[dict], origen: str) -> None:
    print(f"\nOriginal: {Path(origen).name}  ({filas[0]['dur_original']:.1f} s)")
    print(f"{'quitado':>9} {'dur recorte':>12} {'dif dur':>9} {'sim huella':>12} "
          f"{'¿agrupa?':>10}   veredicto")
    print("-" * 82)
    for f in filas:
        estado = "AGRUPA" if f["agrupa"] else "separa"
        juicio = "<- FALSO POSITIVO" if f["agrupa"] else "ok"
        print(f"{f['quitar_s']:8.1f}s {f['dur_recorte']:11.1f}s {f['dif_duracion']:8.1f}s "
              f"{f['sim_huella']:12.4f} {estado:>10}   {juicio}")

    agrupados = [f for f in filas if f["agrupa"]]
    if agrupados:
        peor = max(agrupados, key=lambda f: f["dif_duracion"])
        print(f"\nLa compuerta deja de separar hasta {peor['dif_duracion']:.1f} s de "
              f"diferencia (recorte de {peor['quitar_s']:.1f} s).")
    else:
        print("\nNingún recorte quedó agrupado: la compuerta de duración los separó a todos.")
    sims = [f["sim_huella"] for f in filas]
    print(f"Similitud de la huella sola: min {min(sims):.4f}  max {max(sims):.4f}")
    print("Si la huella se mantiene alta en todo el barrido, la huella NO distingue un edit "
          "del original\ny la única defensa es la duración.")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="calidad.tests.recortes",
        description="Barrido de recortes del medio para validar la compuerta de duración.")
    ap.add_argument("--audio", required=True, help="Track de origen (no se modifica).")
    ap.add_argument("--out", type=Path, default=None,
                    help="Dónde dejar los recortes (default: carpeta temporal del sistema).")
    ap.add_argument("--cortes", type=float, nargs="+", default=list(CORTES_S))
    ap.add_argument("--posicion", type=float, default=0.5,
                    help="Dónde se corta, 0..1 (0.5 = la mitad). Bajarlo mete el corte "
                         "DENTRO de la ventana que mira la huella, para aislar su efecto.")
    args = ap.parse_args(argv)

    destino = args.out
    if destino is None:
        import tempfile
        destino = Path(tempfile.mkdtemp(prefix="recortes_"))
    filas = barrido(args.audio, destino, tuple(args.cortes), posicion=args.posicion)
    _imprimir(filas, args.audio)
    print(f"\nRecortes en: {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
