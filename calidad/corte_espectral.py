"""Tarea 5.1 — verificación de calidad real del archivo por corte espectral.

    python -m calidad.corte_espectral --audio <carpeta>
    python -m calidad.corte_espectral --validar <csv ya marcado a mano>

DOS PREGUNTAS DISTINTAS, UN CRITERIO PARA CADA UNA (tarea 5.15)
---------------------------------------------------------------
Mezclarlas fue el error de la primera versión: hacía caer masters propios como
sospechosos por compararlos contra un bitrate que en un WAV no significa nada.

LOSSY (mp3, m4a, ogg…) → ¿el bitrate declarado está inflado?
    Se compara el corte MEDIDO contra el que corresponde al bitrate del tag. Un 320 real
    llega a ~20 kHz; uno que dice 320 y corta en 16 salió de un 128.
    Dos precisiones que importan:
      · Cortar POR ARRIBA de lo esperado NO es sospechoso. Un LAME VBR V0 sin lowpass
        llega a 22 kHz declarando ~255 kbps: es el mejor MP3 posible, no uno dudoso.
      · La AUSENCIA de muro es evidencia A FAVOR, no un dato neutro. Sin muro no hay
        firma de codec: la caída gradual es de la música, no del encoder.

LOSSLESS (wav, flac, aiff) → ¿este archivo fue lossy ANTES de envolverse?
    Acá NO hay bitrate contra el cual comparar: el de un WAV es aritmética del formato
    (sample rate × bits × canales), no una afirmación sobre la fuente. Por eso el margen
    queda vacío en el CSV, y se juzga solo por la FORMA: un codec deja un muro angosto y
    profundo, un master oscuro cae gradual.

El `motivo` del CSV arranca con la etiqueta de la pregunta que se respondió
([bitrate] / [bitrate inflado] / [forma] / [pudo ser lossy antes]), para que quien lo lee
no confunda "te vendieron un 128 como 320" con "a tu master le entró un sample lossy".

LA BANDERA NO ES UN VEREDICTO. Marca archivos PARA REVISAR, nada más. Hay masters con el
corte bajo a propósito, rips de vinilo, grabaciones de campo y material viejo que dan
"sospechoso" siendo legítimos; y un transcode bien hecho puede pasar como "ok". El oído y
Spek deciden, esto solo ordena la cola de revisión.

NO escribe ni mueve los archivos originales: solo los lee (regla del proyecto).

Qué se reutiliza y qué no
------------------------
- SÍ: el análisis espectral de `analizar_calidad.py` (espectro promediado tipo Welch,
  corte relativo al pico, fuerza del muro). Es la parte difícil y ya está probada.
- NO la carga de audio de la etapa A del benchmark: `benchmark.analizar` carga a 22050 Hz,
  o sea Nyquist 11 kHz. Un corte en 16 o en 20 kHz es literalmente invisible ahí. Acá se
  carga a sample rate NATIVO, que es justo lo que `analizar_calidad._sample_rate` explica
  en su docstring ("NO resampleamos: perderíamos los agudos").
- NO el warm-up de la etapa A: sirve para la compilación JIT de numba, y este camino es
  numpy puro (FFT) sin numba. Además acá no se mide tiempo, así que no habría qué proteger.
- SÍ el patrón de import perezoso: librosa se importa dentro de la función que carga audio,
  para que el modo `--validar` (que es CSV contra CSV) no arrastre el stack de audio.
"""
import argparse
import csv
import datetime
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# Se reutiliza el análisis espectral ya escrito y probado. Son privados porque nadie fuera
# de ese módulo debería usarlos; esta es una herramienta del mismo repo, no un consumidor
# externo. Importarlo NO invoca ffmpeg: solo resuelve rutas al importar.
from analizar_calidad import MURO_DB, _analizar_espectro, _espectro_db

EXTS = (".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg", ".opus")

# Formatos sin pérdida: se espera banda completa. Un lossless que corta bajo es un
# transcode disfrazado, que es el caso más valioso de detectar.
LOSSLESS = (".wav", ".flac", ".aiff", ".aif")

# Ventana de análisis (segundos). Igual criterio que analizar_calidad: un tramo del medio,
# no la intro, que suele tener menos agudos.
DESDE_S, DURACION_S = 45.0, 30.0

# Corte esperado (kHz) según el bitrate declarado (kbps). Los valores salen de la tabla
# del docstring de analizar_calidad.py: 128k ≈ 16 · 192k ≈ 18-19 · 256k ≈ 19-20 · 320k ≈ 20-20.5
TABLA_ESPERADO: list[tuple[int, float]] = [
    (320, 20.0),
    (256, 19.5),
    (192, 18.5),
    (160, 16.5),
    (128, 16.0),
    (96, 15.0),
    (0, 13.0),
]
ESPERADO_LOSSLESS = 20.0

# Cuánto puede quedar por debajo de lo esperado antes de marcarlo. 1 kHz es holgado a
# propósito: preferimos no llenar la cola de revisión con falsos positivos.
TOLERANCIA_KHZ = 1.0

# Para un LOSSLESS no hay bitrate contra el cual comparar. Solo se sospecha si hay muro de
# codec Y el corte queda por debajo de banda completa: un muro a 20+ kHz es el filtro del
# propio equipo de grabacion, no un transcode.
UMBRAL_LOSSLESS_KHZ = 19.0

OK = "ok"
SOSPECHOSO = "sospechoso"
SIN_DATOS = "sin-datos"

AVISO = (
    "La bandera NO es un veredicto: marca archivos PARA REVISAR. Hay masters con corte "
    "bajo a propósito, rips de vinilo y material viejo que dan 'sospechoso' siendo "
    "legítimos. Confirmá con el oído o con Spek antes de descartar nada."
)


@dataclass
class FilaCalidad:
    """Una fila del CSV. `marca_manual` viene vacía para que la completes vos."""

    archivo: str
    ruta: str
    formato: str
    lossless: str              # si | no
    bitrate_declarado_kbps: int
    sample_rate_hz: int
    corte_medido_khz: float
    criterio: str              # lossy (vs bitrate) | lossless (solo forma)
    corte_esperado_khz: float | None   # None en lossless: no hay bitrate contra que comparar
    margen_khz: float | None           # None en lossless: el margen no significaria nada
    muro_db: float             # caída en la transición; muro alto = corte duro de codec
    es_muro: str               # si | no
    bandera: str               # ok | sospechoso | sin-datos
    motivo: str
    marca_manual: str          # <- lo llenás vos: bueno | trucho


COLUMNAS = [f.name for f in fields(FilaCalidad)]


def corte_esperado_khz(bitrate_kbps: int, lossless: bool) -> float:
    """Corte que debería tener un archivo con ese bitrate declarado."""
    if lossless:
        return ESPERADO_LOSSLESS
    for umbral, esperado in TABLA_ESPERADO:
        if bitrate_kbps >= umbral:
            return esperado
    return TABLA_ESPERADO[-1][1]


def bitrate_implicito_kbps(corte_khz: float) -> int:
    """Bitrate que sugiere un corte medido. Es la tabla al revés.

    Sirve para el mensaje: no es lo mismo decir "corta bajo" que "ese muro es de un 128".
    """
    for kbps, esperado in TABLA_ESPERADO:
        if corte_khz >= esperado - 0.25:
            return kbps
    return 0


def clasificar_lossy(corte_khz: float, esperado_khz: float, es_muro: bool,
                     bitrate_kbps: int, tolerancia: float = TOLERANCIA_KHZ) -> tuple[str, str]:
    """Pregunta que responde: ¿el bitrate declarado está inflado?

    Solo tiene sentido con un bitrate contra el cual comparar. Cortar POR ARRIBA de lo
    esperado no es sospechoso: un LAME VBR V0 sin lowpass llega a 22 kHz declarando ~255
    kbps y es el mejor MP3 posible, no uno dudoso.
    """
    margen = corte_khz - esperado_khz
    if margen >= -tolerancia:
        return OK, (f"[bitrate] corta en {corte_khz:.1f} kHz, a la altura de los "
                    f"{bitrate_kbps}k declarados (esperado {esperado_khz:.1f})")

    falta = abs(margen)
    if es_muro:
        implicito = bitrate_implicito_kbps(corte_khz)
        # Un corte por debajo del escalón más bajo de la tabla no tiene bitrate asignable:
        # decir "~0k" sería inventar un número.
        cual = f"de un ~{implicito}k" if implicito else "más bajo que cualquier escalón de la tabla"
        return SOSPECHOSO, (f"[bitrate inflado] muro de codec a {corte_khz:.1f} kHz, que es "
                            f"{cual}, pero declara {bitrate_kbps}k "
                            f"({falta:.1f} kHz por debajo)")

    # Sin muro no hay firma de codec: la caída gradual es de la música, no del encoder.
    # La AUSENCIA de muro es evidencia A FAVOR, no un dato neutro.
    return OK, (f"[bitrate] corta en {corte_khz:.1f} kHz, por debajo de los {bitrate_kbps}k "
                f"declarados, pero SIN muro: caída gradual, no firma de codec "
                f"(master oscuro o fuente limitada)")


def clasificar_lossless(corte_khz: float, es_muro: bool, muro_db: float,
                        umbral_khz: float = None) -> tuple[str, str]:
    """Pregunta que responde: ¿este archivo fue lossy ANTES de envolverse?

    Acá no hay bitrate contra el cual comparar —el de un WAV es aritmética del formato, no
    una afirmación sobre la fuente— así que el margen no significa nada. Lo único que
    delata un transcode es la FORMA: un codec deja un muro angosto y profundo; un master
    oscuro cae gradual.
    """
    umbral_khz = UMBRAL_LOSSLESS_KHZ if umbral_khz is None else umbral_khz
    if es_muro and corte_khz < umbral_khz:
        return SOSPECHOSO, (f"[pudo ser lossy antes] archivo sin pérdida con muro de codec "
                            f"de {muro_db:.0f} dB a {corte_khz:.1f} kHz — un master propio "
                            f"no tiene esa forma")
    if es_muro:
        return OK, (f"[forma] muro de {muro_db:.0f} dB pero a {corte_khz:.1f} kHz: banda "
                    f"completa, probablemente el filtro del propio equipo")
    return OK, (f"[forma] corta en {corte_khz:.1f} kHz sin muro (caída de {muro_db:.0f} dB): "
                f"roll-off gradual, sin firma de codec")


def clasificar(corte_khz: float, esperado_khz: float, es_muro: bool, lossless: bool,
               tolerancia: float = TOLERANCIA_KHZ, muro_db: float = 0.0,
               bitrate_kbps: int = 0) -> tuple[str, str]:
    """Despacha a la pregunta que corresponde según el formato.

    Son DOS preguntas distintas y mezclarlas fue el error original: en un lossless el
    margen contra el bitrate no significa nada, y por eso masters propios caían como
    sospechosos.
    """
    if lossless:
        return clasificar_lossless(corte_khz, es_muro, muro_db)
    return clasificar_lossy(corte_khz, esperado_khz, es_muro, bitrate_kbps, tolerancia)


def _metadatos(ruta: str) -> tuple[str, int]:
    """(formato, bitrate declarado en kbps). 0 si el tag no lo dice."""
    from mutagen import File as MFile  # perezoso: --validar no necesita mutagen

    try:
        m = MFile(ruta)
    except Exception:  # noqa: BLE001
        return "?", 0
    if m is None or not getattr(m, "info", None):
        return Path(ruta).suffix.lstrip(".").lower() or "?", 0
    formato = type(m).__name__.lower()
    bitrate = int(getattr(m.info, "bitrate", 0) or 0) // 1000
    return formato, bitrate


def _cargar_nativo(ruta: str):
    """Carga un tramo a sample rate NATIVO. Devuelve (samples, sr) o (None, 0).

    Nativo es innegociable: resamplear a 22050 deja Nyquist en 11 kHz y el corte que se
    quiere medir (16-20 kHz) desaparece.
    """
    import librosa  # perezoso: --validar es CSV contra CSV y no necesita audio

    # Si el track es más corto que la ventana, se reintenta desde el inicio. Con un offset
    # más allá del final librosa no devuelve vacío: TIRA NoBackendError. Por eso el except
    # sigue al próximo intento en vez de abandonar — si no, todo archivo corto (un sample,
    # un loop) quedaba marcado "sin datos" siendo perfectamente legible.
    for desde in (DESDE_S, 0.0):
        try:
            y, sr = librosa.load(ruta, sr=None, mono=True, offset=desde, duration=DURACION_S)
        except Exception:  # noqa: BLE001
            continue
        if y.size:
            return y, int(sr)
    return None, 0


def analizar_uno(ruta: str) -> FilaCalidad:
    formato, bitrate = _metadatos(ruta)
    lossless = Path(ruta).suffix.lower() in LOSSLESS
    base = dict(archivo=Path(ruta).name, ruta=ruta, formato=formato,
                lossless="si" if lossless else "no",
                bitrate_declarado_kbps=bitrate, marca_manual="")

    y, sr = _cargar_nativo(ruta)
    if y is None or sr <= 0:
        return FilaCalidad(**base, sample_rate_hz=0, corte_medido_khz=0.0,
                           criterio="lossless" if lossless else "lossy",
                           corte_esperado_khz=None, margen_khz=None, muro_db=0.0,
                           es_muro="no", bandera=SIN_DATOS, motivo="no se pudo leer el audio")

    freqs, db = _espectro_db(y, sr)
    m = _analizar_espectro(freqs, db, sr)
    corte_khz = m["corte"] / 1000.0
    muro_db = m["muro_db"]
    es_muro = muro_db >= MURO_DB

    if lossless:
        # Sin bitrate de referencia, esperado y margen quedan en blanco a propósito: poner
        # un número ahí sería exactamente el error que hacía caer masters propios.
        esperado = margen = None
        bandera, motivo = clasificar_lossless(corte_khz, es_muro, muro_db)
    else:
        esperado = corte_esperado_khz(bitrate, lossless)
        margen = round(corte_khz - esperado, 2)
        esperado = round(esperado, 2)
        bandera, motivo = clasificar_lossy(corte_khz, esperado, es_muro, bitrate)

    return FilaCalidad(**base, sample_rate_hz=sr,
                       corte_medido_khz=round(corte_khz, 2),
                       criterio="lossless" if lossless else "lossy",
                       corte_esperado_khz=esperado, margen_khz=margen,
                       muro_db=round(muro_db, 1), es_muro="si" if es_muro else "no",
                       bandera=bandera, motivo=motivo)


def rutas_de(carpeta: Path) -> list[str]:
    """Audios de la carpeta, recursivo y en orden estable.

    Se define acá y no se importa de `benchmark.analizar` a propósito: ese módulo importa
    `motor.bpm`, que trae librosa al importarse, y arruinaría el import perezoso del
    modo --validar.
    """
    return sorted(str(p) for p in carpeta.rglob("*") if p.suffix.lower() in EXTS)


def escribir_csv(filas: list[FilaCalidad], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = out_dir / f"calidad_{sello}.csv"
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS)
        w.writeheader()
        for f in filas:
            w.writerow(asdict(f))
    return destino


# --- Validación contra tu marca manual --------------------------------------------------

_BUENO = {"bueno", "buenos", "ok", "bien", "real", "legit", "legitimo", "legítimo"}
_TRUCHO = {"trucho", "truchos", "falso", "inflado", "fake", "malo", "transcode"}


def _normalizar_marca(v: str) -> str | None:
    t = (v or "").strip().lower()
    if t in _BUENO:
        return "bueno"
    if t in _TRUCHO:
        return "trucho"
    return None


def validar(filas: list[dict]) -> dict:
    """Cruza la bandera automática contra la marca manual. Matriz de confusión."""
    vp = fp = vn = fn = 0          # positivo = "trucho"
    sin_marca = 0
    desacuerdos: list[tuple[str, str, str, str]] = []
    for f in filas:
        marca = _normalizar_marca(f.get("marca_manual", ""))
        if marca is None:
            sin_marca += 1
            continue
        bandera = (f.get("bandera") or "").strip().lower()
        if bandera == SIN_DATOS:
            sin_marca += 1
            continue
        detecto = bandera == SOSPECHOSO
        if marca == "trucho" and detecto:
            vp += 1
        elif marca == "bueno" and detecto:
            fp += 1
            desacuerdos.append((f.get("archivo", "?"), "bueno", "sospechoso",
                                f.get("motivo", "")))
        elif marca == "trucho" and not detecto:
            fn += 1
            desacuerdos.append((f.get("archivo", "?"), "trucho", "ok", f.get("motivo", "")))
        else:
            vn += 1

    n = vp + fp + vn + fn
    precision = vp / (vp + fp) if (vp + fp) else None
    recall = vp / (vp + fn) if (vp + fn) else None
    return {"vp": vp, "fp": fp, "vn": vn, "fn": fn, "n": n, "sin_marca": sin_marca,
            "precision": precision, "recall": recall,
            "exactitud": (vp + vn) / n if n else None, "desacuerdos": desacuerdos}


def _informe_validacion(r: dict) -> None:
    print(f"\n{'=' * 72}\nValidación contra tu marca manual — {r['n']} archivos marcados")
    if r["sin_marca"]:
        print(f"({r['sin_marca']} sin marcar o sin datos, quedan afuera)")
    print(f"{'=' * 72}")
    if not r["n"]:
        print("No hay filas con `marca_manual` completada.")
        print("Llená esa columna con  bueno  o  trucho  y volvé a correr --validar.")
        return
    print(f"{'':22}{'vos: trucho':>14}{'vos: bueno':>14}")
    print(f"{'bandera: sospechoso':22}{r['vp']:>14}{r['fp']:>14}")
    print(f"{'bandera: ok':22}{r['fn']:>14}{r['vn']:>14}")
    print()
    if r["precision"] is not None:
        print(f"  precisión (de los que marqué, cuántos eran truchos): {100 * r['precision']:.0f}%")
    if r["recall"] is not None:
        print(f"  cobertura (de los truchos, cuántos agarré):          {100 * r['recall']:.0f}%")
    if r["exactitud"] is not None:
        print(f"  exactitud global:                                    {100 * r['exactitud']:.0f}%")

    if r["desacuerdos"]:
        print(f"\n{'-' * 72}\nDonde no coincidimos ({len(r['desacuerdos'])}):")
        for archivo, tuyo, mio, motivo in r["desacuerdos"]:
            print(f"  {archivo[:40]:40} vos={tuyo:7} bandera={mio:11} {motivo[:60]}")
    print(f"\n{AVISO}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="calidad.corte_espectral",
        description="Corte espectral medido contra el bitrate declarado. Bandera para revisar.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--audio", type=Path, help="Carpeta con los audios a revisar.")
    g.add_argument("--validar", type=Path,
                   help="CSV ya marcado a mano en la columna marca_manual (bueno/trucho).")
    ap.add_argument("--out", type=Path, default=Path("calidad/out"))
    args = ap.parse_args(argv)

    if args.validar:
        with args.validar.open(encoding="utf-8-sig", newline="") as fh:
            _informe_validacion(validar(list(csv.DictReader(fh))))
        return 0

    rutas = rutas_de(args.audio)
    if not rutas:
        print(f"No encontré audio en {args.audio}")
        return 1

    print(f"Revisando {len(rutas)} archivos…\n")
    filas = []
    for i, r in enumerate(rutas, 1):
        fila = analizar_uno(r)
        filas.append(fila)
        marca = {OK: "  ok", SOSPECHOSO: "SOSPE", SIN_DATOS: "  s/d"}[fila.bandera]
        ref = ("lossless, solo forma" if fila.criterio == "lossless"
               else f"esperado {fila.corte_esperado_khz:4.1f}, margen {fila.margen_khz:+5.1f}")
        print(f"  [{i}/{len(rutas)}] {marca}  {fila.archivo[:38]:38} "
              f"{fila.bitrate_declarado_kbps:>5}k  corte {fila.corte_medido_khz:5.1f}  "
              f"muro {fila.muro_db:5.1f}  ({ref})", flush=True)

    destino = escribir_csv(filas, args.out)
    sospechosos = sum(1 for f in filas if f.bandera == SOSPECHOSO)
    print(f"\n{len(filas)} archivos · {sospechosos} para revisar · "
          f"{sum(1 for f in filas if f.bandera == SIN_DATOS)} sin datos")
    print(f"→ {destino}")
    print(f"\n{AVISO}")
    print("\nPara validar: completá la columna `marca_manual` con  bueno  o  trucho  y corré")
    print(f"  python -m calidad.corte_espectral --validar {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
