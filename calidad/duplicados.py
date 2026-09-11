"""Tarea 5.2 — detección de duplicados en la biblioteca.

Agrupa archivos que contienen EL MISMO AUDIO aunque se llamen distinto, y propone cuál
conservar según la calidad REALMENTE MEDIDA. No borra ni mueve nada: propone.

    python -m calidad.duplicados --audio <carpeta>

Qué es y qué NO es un duplicado
-------------------------------
Duplicado = el mismo audio en otro archivo (otro formato, otra descarga, otra copia).
NO son duplicados: original vs edit, master v1 vs v2, un remix, dos tomas distintas.
Por eso hay dos compuertas y tienen que pasar las dos:

  1. DURACIÓN parecida. Si difieren más que la tolerancia, es otra versión y ni se
     compara la huella. Barato y corta de entrada la mayoría de los falsos positivos.
  2. HUELLA de audio muy parecida.

Por qué esta huella y no chromaprint
------------------------------------
Se probó primero lo barato, que es el DSP que ya usa `calidad.corte_espectral`
(`analizar_calidad._espectro_db`), y alcanzó — así que NO se agrega chromaprint ni
ninguna dependencia nueva (regla §5: nada pesado sin justificar).

Medido sobre 56 archivos reales (4 pares duplicados conocidos, 1535 pares distintos):

    normalización        duplicados(mín)  distintos(máx)  separación
    global, hasta 10 kHz     0.8877          0.9643        -0.077  se solapa
    global, hasta  6 kHz     0.9792          0.9684        +0.011  se solapa
    POR BANDA, hasta 6 kHz   0.9348          0.7874        +0.147  <- la elegida
    banda y tramo, 6 kHz     0.9766          0.9043        +0.072

Las dos claves fueron:
  - Restar la media POR BANDA. Sin eso la correlación queda dominada por la inclinación
    espectral común a todo el material (graves fuertes, agudos débiles) y dos temas
    distintos del mismo género dan 0.96.
  - Cortar en 6 kHz. Queda por debajo de cualquier corte de codec (un MP3 128 corta en
    16 kHz) y también por debajo del Nyquist de fuentes de 16 kHz, así que el mismo audio
    en MP3, WAV u OGG da la misma huella.
"""
import argparse
import csv
import datetime
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np
from analizar_calidad import _espectro_db

from calidad.corte_espectral import EXTS
from calidad.corte_espectral import analizar_uno as medir_calidad

# Sample rate de la huella. Fijo y BAJO a propósito: por debajo del corte de cualquier
# codec, así el mismo audio en MP3 y en WAV produce la misma huella.
SR_HUELLA = 22050
F_MIN, F_MAX = 60.0, 6000.0
N_TRAMOS, N_BANDAS = 24, 24

# Ventana analizada. Desde 30 s para saltear la intro; absoluta (no proporcional) para que
# dos archivos del mismo audio queden alineados aunque uno tenga cola de más.
DESDE_S, DURACION_S = 30.0, 60.0

# Umbral de similitud. Medido: duplicados ≥ 0.9348, distintos ≤ 0.7874. Se elige 0.90,
# más cerca del piso de los duplicados que del techo de los distintos: agrupar de más
# propone descartar un track que NO es duplicado, que es el error caro.
UMBRAL = 0.90

# Tolerancia de duración. Dos archivos del mismo audio difieren milisegundos (padding del
# encoder); una versión distinta difiere segundos.
TOL_DURACION_S = 2.0
TOL_DURACION_REL = 0.02

CONSERVAR = "conservar"
DESCARTAR = "descartar"

AVISO = (
    "Esto PROPONE, no ejecuta: no se borró ni se movió ningún archivo. Revisá los grupos "
    "antes de tocar nada — dos tomas distintas del mismo tema pueden parecerse mucho."
)


@dataclass
class Ficha:
    """Lo que se sabe de un archivo antes de agrupar."""

    ruta: str
    archivo: str
    duracion_s: float
    huella: np.ndarray | None


@dataclass
class FilaDuplicado:
    grupo_id: int
    archivo: str
    ruta: str
    duracion_s: float
    corte_khz: float          # calidad REAL medida, no el bitrate declarado
    muro_db: float
    es_muro: str
    bandera_calidad: str
    lossless: str
    bitrate_declarado_kbps: int   # informativo — NO decide
    similitud_con_grupo: float
    accion: str               # conservar | descartar
    motivo: str


COLUMNAS = [f.name for f in fields(FilaDuplicado)]


def huella(y: np.ndarray, sr: int = SR_HUELLA) -> np.ndarray | None:
    """Matriz tramos × bandas en dB, normalizada por banda y aplanada.

    Reutiliza `_espectro_db` de analizar_calidad por TRAMO (no sobre el track entero): un
    espectro promediado de todo el tema no distingue dos temas del mismo género, porque
    pierde cómo evoluciona en el tiempo.
    """
    if y.size < sr * 2:
        return None
    bordes = np.logspace(np.log10(F_MIN), np.log10(F_MAX), N_BANDAS + 1)
    largo = y.size // N_TRAMOS
    if largo < 512:
        return None

    filas = []
    for k in range(N_TRAMOS):
        tramo = y[k * largo:(k + 1) * largo]
        freqs, db = _espectro_db(tramo, sr)
        fila = []
        for i in range(N_BANDAS):
            m = (freqs >= bordes[i]) & (freqs < bordes[i + 1])
            fila.append(float(db[m].mean()) if m.any() else -120.0)
        filas.append(fila)

    h = np.array(filas, dtype=np.float64)
    # Restar la media POR BANDA (columna): saca la inclinación espectral que comparten
    # todos los tracks y deja solo cómo varía cada banda en el tiempo, que es lo propio
    # de cada tema. Sin esto, dos temas distintos del mismo género correlacionan 0.96.
    h -= h.mean(axis=0, keepdims=True)
    s = h.std()
    return (h / s).ravel() if s > 1e-9 else None


def similitud(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def duracion_compatible(d1: float, d2: float, tol_s: float = TOL_DURACION_S,
                        tol_rel: float = TOL_DURACION_REL) -> bool:
    """¿Pueden ser el mismo audio, por duración? Si no, es otra versión y no se compara."""
    if d1 <= 0 or d2 <= 0:
        return False
    return abs(d1 - d2) <= max(tol_s, tol_rel * min(d1, d2))


def mismo_audio(a: Ficha, b: Ficha, umbral: float = UMBRAL) -> tuple[bool, float]:
    """Las dos compuertas. Devuelve (es_duplicado, similitud)."""
    if not duracion_compatible(a.duracion_s, b.duracion_s):
        return False, 0.0
    s = similitud(a.huella, b.huella)
    return s >= umbral, s


def cargar_ficha(ruta: str) -> Ficha:
    import librosa  # perezoso, igual que en corte_espectral

    try:
        dur = float(librosa.get_duration(path=ruta))
    except Exception:  # noqa: BLE001
        return Ficha(ruta, Path(ruta).name, 0.0, None)
    desde = DESDE_S if dur > 100 else 0.0
    try:
        y, _ = librosa.load(ruta, sr=SR_HUELLA, mono=True, offset=desde, duration=DURACION_S)
    except Exception:  # noqa: BLE001
        return Ficha(ruta, Path(ruta).name, dur, None)
    return Ficha(ruta, Path(ruta).name, dur, huella(y, SR_HUELLA))


def agrupar(fichas: list[Ficha], umbral: float = UMBRAL) -> tuple[list[list[int]], dict]:
    """Union-find sobre los pares que pasan las dos compuertas.

    Devuelve (grupos de 2+, similitudes por par unido).
    """
    padre = list(range(len(fichas)))

    def raiz(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    sims: dict[tuple[int, int], float] = {}
    for i in range(len(fichas)):
        if fichas[i].huella is None:
            continue
        for j in range(i + 1, len(fichas)):
            if fichas[j].huella is None:
                continue
            es_dup, s = mismo_audio(fichas[i], fichas[j], umbral)
            if es_dup:
                sims[(i, j)] = s
                ri, rj = raiz(i), raiz(j)
                if ri != rj:
                    padre[ri] = rj

    cubos: dict[int, list[int]] = {}
    for i in range(len(fichas)):
        if fichas[i].huella is not None:
            cubos.setdefault(raiz(i), []).append(i)
    grupos = [sorted(v) for v in cubos.values() if len(v) > 1]
    grupos.sort(key=lambda g: fichas[g[0]].archivo.lower())
    return grupos, sims


def _puntaje_calidad(cal) -> tuple:
    """Orden de preferencia para conservar. MÁS ALTO ES MEJOR.

    Manda el corte MEDIDO. El bitrate declarado no entra nunca: un WAV que corta en
    15 kHz con muro es un transcode y pierde contra un 320 que llega a 20 kHz de verdad.
    """
    sin_muro = 0 if cal.es_muro == "si" else 1     # espectro natural mejor que corte de codec
    es_lossless = 1 if cal.lossless == "si" else 0
    return (round(cal.corte_medido_khz, 1), sin_muro, es_lossless)


def decidir(grupo: list[int], fichas: list[Ficha], calidades: dict) -> list[FilaDuplicado]:
    """Elige cuál conservar dentro de un grupo y arma las filas del CSV."""
    ordenados = sorted(grupo, key=lambda i: _puntaje_calidad(calidades[i]), reverse=True)
    ganador = ordenados[0]
    cal_g = calidades[ganador]

    filas = []
    for i in ordenados:
        cal = calidades[i]
        if i == ganador:
            motivo = (f"mejor calidad medida del grupo: corte {cal.corte_medido_khz:.1f} kHz"
                      + (" sin muro" if cal.es_muro == "no" else " con muro"))
            accion = CONSERVAR
        else:
            dif = cal_g.corte_medido_khz - cal.corte_medido_khz
            if dif > 0.05:
                motivo = (f"corta {dif:.1f} kHz más abajo que el que se conserva "
                          f"({cal.corte_medido_khz:.1f} vs {cal_g.corte_medido_khz:.1f})")
            elif cal.es_muro == "si" and cal_g.es_muro == "no":
                motivo = "mismo corte pero con muro de codec; el conservado es natural"
            elif cal.lossless == "no" and cal_g.lossless == "si":
                motivo = "misma calidad medida; se conserva el sin pérdida"
            else:
                motivo = "copia equivalente del mismo audio"
            accion = DESCARTAR

        # grupo_id y similitud_con_grupo los completa el llamador, que es el que conoce
        # el número de grupo y los pares que lo unieron.
        filas.append(FilaDuplicado(
            grupo_id=0, archivo=fichas[i].archivo, ruta=fichas[i].ruta,
            duracion_s=round(fichas[i].duracion_s, 1),
            corte_khz=cal.corte_medido_khz, muro_db=cal.muro_db, es_muro=cal.es_muro,
            bandera_calidad=cal.bandera, lossless=cal.lossless,
            bitrate_declarado_kbps=cal.bitrate_declarado_kbps,
            similitud_con_grupo=0.0, accion=accion, motivo=motivo))
    return filas


# --- Puente con ground_truth.resolver ---------------------------------------------------


def desambiguar(candidatos: list[str], umbral: float = UMBRAL) -> str | None:
    """¿Los homónimos que el resolver marcó AMBIGUO son en realidad el mismo audio?

    `ground_truth.resolver` marca AMBIGUO cuando varios archivos comparten basename, y los
    excluye del benchmark porque analizar el equivocado se ve igual que un error de motor.
    Pero esa ambigüedad tiene dos causas muy distintas:

      - son el MISMO audio en dos copias  → da igual cuál se use; se puede desambiguar.
      - son versiones DISTINTAS           → la ambigüedad es real y hay que excluirlos.

    Esta función distingue una de otra. Devuelve una ruta utilizable si todos los
    candidatos son el mismo audio, o None si no (y entonces excluirlos era lo correcto).

    NO se llama desde el resolver a propósito: ese módulo hoy es lógica de rutas pura, sin
    numpy ni librosa, y meterle esto lo obligaría a decodificar audio — ~0.8 s por archivo
    en medio de una resolución de rutas. Además, para el benchmark excluir es la opción
    conservadora y solo cuesta tamaño de muestra. Queda como opt-in para quien lo quiera:

        r = resolver(loc, raices, indice)
        if r.estado == AMBIGUO:
            ruta = desambiguar(list(r.candidatos))
    """
    if not candidatos:
        return None
    if len(candidatos) == 1:
        return candidatos[0]

    fichas = [cargar_ficha(c) for c in candidatos]
    if any(f.huella is None for f in fichas):
        return None
    for i in range(len(fichas)):
        for j in range(i + 1, len(fichas)):
            es_dup, _ = mismo_audio(fichas[i], fichas[j], umbral)
            if not es_dup:
                return None            # hay al menos un par distinto: sigue siendo ambiguo
    return candidatos[0]               # todos el mismo audio: cualquiera sirve


def escribir_csv(filas: list[FilaDuplicado], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = out_dir / f"duplicados_{sello}.csv"
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS)
        w.writeheader()
        for f in filas:
            w.writerow(asdict(f))
    return destino


def rutas_de(carpeta: Path) -> list[str]:
    return sorted(str(p) for p in carpeta.rglob("*") if p.suffix.lower() in EXTS)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="calidad.duplicados",
        description="Agrupa el mismo audio en archivos distintos y propone cuál conservar.")
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--umbral", type=float, default=UMBRAL,
                    help=f"Similitud mínima para considerar duplicado (default {UMBRAL}).")
    ap.add_argument("--out", type=Path, default=Path("calidad/out"))
    args = ap.parse_args(argv)

    rutas = rutas_de(args.audio)
    if not rutas:
        print(f"No encontré audio en {args.audio}")
        return 1

    print(f"Huellas de {len(rutas)} archivos…")
    fichas = []
    for i, r in enumerate(rutas, 1):
        f = cargar_ficha(r)
        fichas.append(f)
        if f.huella is None:
            print(f"  [{i}/{len(rutas)}] (sin huella) {f.archivo[:50]}", flush=True)

    grupos, sims = agrupar(fichas, args.umbral)
    sin_huella = sum(1 for f in fichas if f.huella is None)
    if not grupos:
        print(f"\nNingún duplicado con umbral {args.umbral}. "
              f"({sin_huella} sin huella)\n{AVISO}")
        return 0

    # La calidad solo se mide para los que quedaron en algún grupo: es una segunda lectura
    # del audio a sample rate nativo y no tiene sentido pagarla por toda la biblioteca.
    print(f"\n{len(grupos)} grupo(s). Midiendo calidad real de los involucrados…")
    calidades = {}
    for g in grupos:
        for i in g:
            calidades[i] = medir_calidad(fichas[i].ruta)

    filas: list[FilaDuplicado] = []
    for gid, g in enumerate(grupos, 1):
        del_grupo = decidir(g, fichas, calidades)
        propias = [s for (a, b), s in sims.items() if a in g and b in g]
        peor = min(propias) if propias else 0.0
        for fila in del_grupo:
            fila.grupo_id = gid
            fila.similitud_con_grupo = round(peor, 4)
        filas.extend(del_grupo)

        print(f"\n  grupo {gid}  (similitud mínima {peor:.4f})")
        for fila in del_grupo:
            marca = "CONSERVAR" if fila.accion == CONSERVAR else "descartar"
            print(f"    {marca:10} {fila.archivo[:44]:44} {fila.duracion_s:7.1f}s  "
                  f"corte {fila.corte_khz:5.1f} kHz  muro {fila.muro_db:5.1f}  "
                  f"{fila.bitrate_declarado_kbps:>5}k")
            print(f"               └─ {fila.motivo}")

    destino = escribir_csv(filas, args.out)
    print(f"\n{len(filas)} archivos en {len(grupos)} grupos · "
          f"{sum(1 for f in filas if f.accion == DESCARTAR)} propuestos para descartar")
    if sin_huella:
        print(f"{sin_huella} archivo(s) sin huella (muy cortos o ilegibles): quedaron afuera")
    print(f"→ {destino}\n\n{AVISO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
