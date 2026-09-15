"""Escucha de discrepancias de tonalidad: el material servido para resolver a oído.

El "acierto" de tonalidad del benchmark mide COINCIDENCIA CON REKORDBOX, y Rekordbox también
se equivoca. Lo único que separa "el motor falla" de "la referencia falla" es escuchar. Esta
herramienta elige los tracks donde vale la pena hacerlo y deja cortados los fragmentos que
el motor analizó, con un índice para anotar el veredicto.

    python -m ground_truth.escucha --analisis gt_out/analisis_sin-consenso.csv \\
        --acuerdo-de gt_out/analisis_con-consenso.csv \\
        --ground-truth gt_out/rekordbox_tracks.csv --salida escucha/ -n 10

QUÉ TRACKS. Los que el motor da por SEGUROS y aun así no coinciden con Rekordbox: acuerdo
UNÁNIME entre los tramos de `tono_consenso` y key del motor (`key_est`, la de `tono()`)
distinta de la de Rekordbox. Sin referencia en el GT, fuera. Si hay más que `-n`, muestra
determinista con la semilla de `benchmark.analizar` (misma entrada → mismos tracks).

`--acuerdo-de`. Los CSV de sesiones anteriores a `aabd83d` tienen la key de `tono()` en la
pasada sin consenso pero con `acuerdo` VACÍO, y el acuerdo en la pasada con consenso, cuya
key es la del voto (NO la del motor). `--acuerdo-de` toma `acuerdo` y `tramos` de esa otra
corrida cruzando POR RUTA (dos archivos con el mismo nombre en carpetas distintas son dos
tracks). Es equivalente a reanalizar: `tono_consenso` es determinista sobre el mismo audio,
así que el acuerdo de la pasada con consenso es el que daría hoy la etapa A.

QUÉ PRODUCE, por track elegido, en `--salida` (que tiene que NO existir o estar VACÍA):
  - `NN_<nombre>_ventana_central.wav`: EXACTAMENTE la ventana que analiza `tono()`.
  - `NN_<nombre>_tramo1..N.wav`: los tramos disjuntos que vota `tono_consenso()`.
  - `escucha.md`: qué escuchar y, por track, dónde anotar `veredicto` y `notas` a mano.
  - `escucha.csv`: los mismos datos para leer o filtrar. Es SOLO DE LECTURA: no tiene
    columnas para el oído, porque guardarlo desde Excel lo corrompe (convierte el acuerdo
    `3/3` en fecha y `00:25` en hora).

Los límites de cada fragmento NO se recalculan acá: se le pregunta a la propia función del
motor qué muestras toma (ver `limites_analizados`) y se pasan a segundos. Así, si el motor
cambia la ventana, el fragmento sigue siendo el que se analizó. El corte se hace sobre el
audio original a su sample rate y con sus canales, y se escribe en WAV PCM 16 bits
(~40 MB por track estéreo a 44.1 kHz: 90 s de ventana central + 3 × 45 s de tramos).

NUNCA modifica los audios originales: los lee y escribe copias en `--salida`. Tampoco pisa
nada en `--salida`: si la carpeta tiene algo, corta antes de escribir.
NO INVENTA DATOS: si falta el acuerdo, un CSV o el audio, lo dice y no rellena.
"""
import argparse
import contextlib
import csv
import inspect
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from benchmark.analizar import METODO_CONSENSO, SEMILLA, muestrear
from benchmark.evaluar import _indexar, acuerdo_unanime, cruzar, leer_csv
from motor.radio import key_relation
from motor.tonalidad import _tramos_disjuntos, camelot_a_clasica, tono_consenso, ventana_central

# Sample rate al que analiza el motor (`motor.analisis.SR`, el default de
# `benchmark.analizar.analizar_uno`). Se importa perezoso en `exportar_track` porque
# `motor.analisis` arrastra librosa, y la selección tiene que poder probarse sin audio.
AUDIO_NO_ENCONTRADO = "audio no encontrado"
AUDIO_NO_DECODIFICA = "audio no decodificó"
OK = "ok"

# Los parámetros de los tramos son LOS DEFAULTS DE `tono_consenso`, leídos de su firma: el
# acuerdo del CSV salió de llamarla sin argumentos, y copiar los números acá los separaría
# el día que alguien los cambie en el motor.
_FIRMA_CONSENSO = inspect.signature(tono_consenso).parameters
N_TRAMOS = _FIRMA_CONSENSO["n_tramos"].default
VENTANA_TRAMO_S = _FIRMA_CONSENSO["ventana_s"].default

INDICE_CSV = "escucha.csv"
INDICE_MD = "escucha.md"

COLUMNAS_INDICE = [
    "n", "nombre", "ruta", "estado",
    "key_motor_camelot", "key_motor_clasica",
    "key_rekordbox_camelot", "key_rekordbox_tonality",
    "relacion", "error_de_modo", "acuerdo", "tramos",
    "ventana_inicio", "ventana_fin", "ventana_inicio_s", "ventana_fin_s",
    "archivo_ventana_central", "archivos_tramos",
]
# Sin `veredicto` ni `notas`: se anotan en `escucha.md`. Ningún otro módulo lee este CSV
# (verificado con `git grep`), y dos columnas vacías en un archivo que no se tiene que editar
# invitan justo a abrirlo en Excel y guardarlo, que es lo que lo rompe.

FRAGMENTO_SUBTYPE = "PCM_16"


class EscuchaIncompleta(RuntimeError):
    """Falta algo para poder armar la escucha. Nunca se rellena con datos inventados."""


# --- Entradas ---------------------------------------------------------------------------


def _hay_acuerdo(filas: list[dict]) -> bool:
    return any((f.get("acuerdo") or "").strip() for f in filas)


def _rechazar_rutas_repetidas(filas: list[dict], etiqueta: str) -> None:
    """Una ruta repetida es un CSV roto: no hay cómo saber cuál de las dos filas vale, y
    quedarse con una en silencio pierde un track. Las filas sin ruta no cuentan."""
    vistas: set[str] = set()
    for f in filas:
        ruta = (f.get("ruta") or "").strip()
        if not ruta:
            continue
        if ruta in vistas:
            raise EscuchaIncompleta(f"Ruta repetida en el CSV de {etiqueta}: {ruta}")
        vistas.add(ruta)


def combinar_acuerdo(analisis: list[dict], con_consenso: list[dict]) -> tuple[list[dict], list[str]]:
    """Pone en cada fila de `analisis` el `acuerdo` y los `tramos` de `con_consenso`.

    El cruce es POR `ruta`, exacta como la escribió la etapa A (las dos pasadas de una sesión
    salen de la misma lista de rutas resueltas). No por basename: dos archivos con el mismo
    nombre en carpetas distintas son dos tracks, y el acuerdo de uno no es el del otro.

    `key_est` y el resto de la fila quedan los de `analisis`: la key de la pasada con
    consenso es la del voto y no la del motor.

    Devuelve `(combinadas, sin_par)`: las rutas de `analisis` que no tienen fila en
    `con_consenso` quedan FUERA y se devuelven para contarlas, no se rellenan.

    Falla si `con_consenso` no es una corrida con consenso (ninguna fila con
    `metodo == tono_consenso` ni acuerdo), o si repite una ruta (CSV roto: no hay cómo
    saber cuál de las dos filas vale).
    """
    es_consenso = any((f.get("metodo") or "").strip() == METODO_CONSENSO for f in con_consenso)
    if not (es_consenso or _hay_acuerdo(con_consenso)):
        raise EscuchaIncompleta(
            "El CSV de --acuerdo-de no es una corrida con consenso: ninguna fila tiene "
            f"metodo={METODO_CONSENSO} ni la columna acuerdo llena.\n"
            "  Pasá la pasada CON consenso de la sesión (gt_out/analisis_con-consenso.csv).")

    _rechazar_rutas_repetidas(con_consenso, "--acuerdo-de")
    por_ruta = {r: f for f in con_consenso if (r := (f.get("ruta") or "").strip())}

    combinadas, sin_par = [], []
    for f in analisis:
        ruta = (f.get("ruta") or "").strip()
        par = por_ruta.get(ruta)
        if par is None:
            sin_par.append(ruta)
            continue
        combinadas.append({**f, "acuerdo": par.get("acuerdo", ""),
                           "tramos": par.get("tramos", "")})
    return combinadas, sin_par


# --- Selección --------------------------------------------------------------------------


@dataclass
class Candidato:
    """Un track donde el motor está seguro y Rekordbox dice otra cosa."""

    ruta: str
    archivo: str
    nombre: str          # "artista — título" del GT si hay; si no, el archivo
    key_motor: str       # Camelot, de tono()
    key_rekordbox: str   # Camelot, mapeado por ground_truth.rekordbox
    tonality: str        # el campo Tonality original del XML
    acuerdo: str
    tramos: str


def _nombre(artista: str, titulo: str, archivo: str) -> str:
    artista, titulo = (artista or "").strip(), (titulo or "").strip()
    if artista and titulo:
        return f"{artista} — {titulo}"
    return titulo or artista or archivo


def seleccionar(analisis: list[dict], gt: list[dict]) -> dict:
    """Candidatos a escuchar y el recuento de por qué quedó fuera cada track.

    Usa `benchmark.evaluar.cruzar` (el mismo cruce por basename que la etapa B, con sus
    ambiguos fuera) y `acuerdo_unanime` para no reinterpretar el "g/t".

    Una key del motor que no es Camelot válido ('?', silencio) no es un desacuerdo con
    Rekordbox sino ausencia de dato: se cuenta aparte en `sin_key_motor`.
    """
    res = cruzar(analisis, gt)
    fila_a, _ = _indexar(analisis, "archivo")
    fila_g, _ = _indexar(gt, "location")

    conteo = {"cruzados": len(res["cruces"]), "ambiguos": len(res["ambiguos"]),
              "sin_gt": len(res["solo_analisis"]), "sin_referencia": 0, "sin_acuerdo": 0,
              "no_unanime": 0, "misma_key": 0, "sin_key_motor": 0}
    candidatos: list[Candidato] = []
    for c in res["cruces"]:
        if c.key_exacta == "sin-referencia":
            conteo["sin_referencia"] += 1
            continue
        unanime = acuerdo_unanime(c.acuerdo)
        if unanime is None:
            conteo["sin_acuerdo"] += 1
            continue
        if not unanime:
            conteo["no_unanime"] += 1
            continue
        if c.key_exacta == "si":
            conteo["misma_key"] += 1
            continue
        if not camelot_a_clasica(c.key_est):
            conteo["sin_key_motor"] += 1
            continue
        base = Path(c.archivo.replace("\\", "/")).name.lower()
        fa, fg = fila_a[base], fila_g[base]
        candidatos.append(Candidato(
            ruta=(fa.get("ruta") or "").strip(), archivo=c.archivo,
            nombre=_nombre(fg.get("artist", ""), fg.get("name", ""), c.archivo),
            key_motor=c.key_est, key_rekordbox=c.key_ref,
            tonality=(fg.get("tonality") or "").strip(),
            acuerdo=c.acuerdo, tramos=c.tramos))
    return {"candidatos": candidatos, "conteo": conteo}


def elegir(candidatos: list[Candidato], n: int, semilla: int = SEMILLA) -> list[Candidato]:
    """Hasta `n` candidatos, con la muestra de `benchmark.analizar.muestrear`.

    Se muestrea sobre las rutas ORDENADAS: el orden de las filas del CSV no cambia la
    elección. Salida ordenada por ruta (es lo que devuelve `muestrear`).
    """
    por_ruta = {c.ruta: c for c in candidatos}
    return [por_ruta[r] for r in muestrear(sorted(por_ruta), n, semilla)]


def error_de_modo(key_a: str, key_b: str) -> bool:
    """¿Misma tónica y modo distinto? (A menor vs A mayor = 8A vs 11B).

    Se decide por la notación clásica que da `camelot_a_clasica` ('Am' / 'A'), no por el
    número de Camelot: 8A (Am) y 8B (C) comparten número pero NO tónica — eso es el
    RELATIVO, y ya lo nombra la columna `relacion`.
    """
    a, b = camelot_a_clasica(key_a), camelot_a_clasica(key_b)
    if not a or not b:
        return False
    return a.removesuffix("m") == b.removesuffix("m") and a.endswith("m") != b.endswith("m")


# --- Fragmentos -------------------------------------------------------------------------


def _extremos(indices: np.ndarray) -> tuple[int, int]:
    """[inicio, fin) de un recorte de `np.arange`. Falla si el recorte no es contiguo:
    ahí 'la ventana' dejaría de ser un fragmento de audio que se pueda escuchar."""
    if indices.size == 0:
        raise ValueError("la función del motor devolvió un recorte vacío")
    ini, fin = int(indices[0]), int(indices[-1]) + 1
    if fin - ini != indices.size:
        raise ValueError("la función del motor devolvió un recorte no contiguo")
    return ini, fin


def limites_analizados(n_muestras: int, sr: int) -> tuple[tuple[int, int], list[tuple[int, int]]]:
    """Qué muestras analiza el motor en un audio de `n_muestras` a `sr`.

    No reimplementa la aritmética: le pasa a `ventana_central` y a `_tramos_disjuntos` (con
    los defaults de `tono_consenso`) un `np.arange(n_muestras)` en lugar del audio. Las dos
    solo recortan, así que lo que devuelven SON los índices que tomarían del audio real.

    Devuelve `((ini, fin), [(ini, fin), ...])` en muestras a `sr`, fin excluido. La lista de
    tramos es vacía si el audio no da para los tramos disjuntos (igual que en el motor).
    """
    indices = np.arange(n_muestras, dtype=np.int64)
    central = _extremos(ventana_central(indices, sr))
    tramos = [_extremos(t) for t in _tramos_disjuntos(indices, sr, N_TRAMOS, VENTANA_TRAMO_S)]
    return central, tramos


def a_muestra_nativa(muestra: int, sr_analisis: int, sr_nativo: int) -> int:
    """Muestra a `sr_analisis` → la muestra del mismo instante a `sr_nativo`, redondeando.

    El redondeo es la única fuente de diferencia con el corte "ideal": como mucho ±1 muestra
    nativa (menos de 0.03 ms a 44100 Hz). Aritmética entera para no depender del float.
    """
    return (muestra * sr_nativo + sr_analisis // 2) // sr_analisis


_INVALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def nombre_seguro(texto: str, largo: int = 60) -> str:
    """Nombre de archivo válido en Windows: sin `<>:"/\\|?*` ni control, sin espacios o
    puntos al final (Windows los borra y el archivo no se encuentra), recortado a `largo`.
    Los nombres reservados (CON, NUL…) no son problema: siempre van con el prefijo `NN_`."""
    limpio = re.sub(r"\s+", " ", _INVALIDOS.sub("_", texto or "")).strip(" .")
    limpio = limpio[:largo].rstrip(" .")
    return limpio or "track"


@dataclass
class Fragmentos:
    estado: str
    inicio_s: float | None = None
    fin_s: float | None = None
    central: str = ""
    tramos: list[str] = field(default_factory=list)


def exportar_track(ruta: str, prefijo: str, salida: Path) -> Fragmentos:
    """Corta la ventana central y los tramos del audio original y los escribe en `salida`.

    1. Carga el audio COMO LO CARGA EL MOTOR (`motor.analisis.cargar`, 22050 Hz mono) solo
       para saber cuántas muestras ve el análisis, y de ahí `limites_analizados`.
    2. Pasa esos límites a segundos y los corta del original cargado a su sample rate nativo
       y con sus canales (`librosa.load(sr=None, mono=False)`).
    3. Escribe WAV PCM 16 bits (decisión del dueño, por tamaño: la mitad que float32). Eso
       recuantiza: soundfile escala por 32768 y redondea sin dither (error ≤ 1/32768), y
       recorta a ±1.0 lo que se pase (un MP3 decodificado puede tener picos por encima).

    Si el archivo no existe no se inventa nada: estado `AUDIO_NO_ENCONTRADO`.
    """
    origen = Path(ruta)
    if not origen.is_file():
        return Fragmentos(estado=AUDIO_NO_ENCONTRADO)

    import librosa
    import soundfile as sf

    from motor.analisis import SR, cargar

    y = cargar(origen, SR)
    if y is None:
        return Fragmentos(estado=AUDIO_NO_DECODIFICA)
    (c_ini, c_fin), tramos = limites_analizados(y.size, SR)
    del y

    nativo, sr_nativo = librosa.load(str(origen), sr=None, mono=False)
    nativo = np.atleast_2d(nativo)          # (canales, muestras), también para mono
    total = nativo.shape[1]

    def cortar(ini: int, fin: int, sufijo: str) -> str:
        destino = salida / f"{prefijo}_{sufijo}.wav"
        if destino.resolve() == origen.resolve():   # nunca pisar el original
            raise EscuchaIncompleta(f"El fragmento pisaría el audio original: {origen}")
        a = min(a_muestra_nativa(ini, SR, sr_nativo), total)
        b = min(a_muestra_nativa(fin, SR, sr_nativo), total)
        # soundfile espera (muestras, canales): la traspuesta de lo que da librosa.
        sf.write(str(destino), nativo[:, a:b].T, sr_nativo, subtype=FRAGMENTO_SUBTYPE)
        return destino.name

    return Fragmentos(
        estado=OK, inicio_s=c_ini / SR, fin_s=c_fin / SR,
        central=cortar(c_ini, c_fin, "ventana_central"),
        tramos=[cortar(i, f, f"tramo{k}") for k, (i, f) in enumerate(tramos, 1)])


# --- Índice -----------------------------------------------------------------------------


def _mmss(segundos: float | None) -> str:
    if segundos is None:
        return ""
    s = int(segundos)
    return f"{s // 60:02d}:{s % 60:02d}"


def fila_indice(n: int, c: Candidato, fr: Fragmentos) -> dict:
    return {
        "n": n, "nombre": c.nombre, "ruta": c.ruta, "estado": fr.estado,
        "key_motor_camelot": c.key_motor, "key_motor_clasica": camelot_a_clasica(c.key_motor),
        "key_rekordbox_camelot": c.key_rekordbox, "key_rekordbox_tonality": c.tonality,
        "relacion": key_relation(c.key_motor, c.key_rekordbox),
        "error_de_modo": "si" if error_de_modo(c.key_motor, c.key_rekordbox) else "no",
        "acuerdo": c.acuerdo, "tramos": c.tramos,
        "ventana_inicio": _mmss(fr.inicio_s), "ventana_fin": _mmss(fr.fin_s),
        "ventana_inicio_s": "" if fr.inicio_s is None else f"{fr.inicio_s:.2f}",
        "ventana_fin_s": "" if fr.fin_s is None else f"{fr.fin_s:.2f}",
        "archivo_ventana_central": fr.central, "archivos_tramos": "|".join(fr.tramos),
    }


def escribir_indice_csv(filas: list[dict], destino: Path) -> None:
    """UTF-8 CON BOM (`utf-8-sig`): sin el BOM, Excel en Windows abre el CSV como ANSI y los
    nombres con acentos, el "—" y el "±2" salen rotos. El resto del repo lee con
    `utf-8-sig` (`benchmark.evaluar.leer_csv`), así que el BOM no molesta al releerlo."""
    with destino.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS_INDICE)
        w.writeheader()
        w.writerows(filas)


def _mb_por_track_estereo_44k() -> float:
    """Tamaño de los fragmentos de un track estéreo a 44.1 kHz en 16 bits (4 bytes por
    instante), con las duraciones de los defaults del motor."""
    segundos = inspect.signature(ventana_central).parameters["segundos"].default
    return (segundos + N_TRAMOS * VENTANA_TRAMO_S) * 44100 * 4 / 1e6


def _explicacion_md() -> str:
    """Cabecera de `escucha.md`. Función y no constante: los nombres de los tramos salen de
    `N_TRAMOS` en el momento de escribir, no de un texto fijo."""
    tramos = ", ".join(f"`tramo{k}`" for k in range(2, N_TRAMOS + 1))
    return f"""# Escucha de discrepancias de tonalidad

Tracks donde el motor está **seguro** (los {N_TRAMOS} tramos del consenso votaron la misma key) y
**no coincide** con Rekordbox. Rekordbox también se equivoca: acá se decide a oído quién tiene
razón.

**Los veredictos se anotan en ESTE archivo**: cada track tiene abajo sus líneas `veredicto` y
`notas`; escribí después de los dos puntos (con el Bloc de notas o cualquier editor de texto).

**`{INDICE_CSV}` es solo de lectura: no lo abras y guardes con Excel.** Excel convierte el
acuerdo `3/3` en una fecha y los tiempos como `00:25` en una hora, y al guardar el CSV queda
corrompido. Si querés mirarlo en Excel, cerralo sin guardar.

## Qué es cada archivo

- `NN_<nombre>_ventana_central.wav` — exactamente el fragmento del que `tono()` saca la key
  del motor (la ventana central del track). Es el más importante: la key del motor sale
  solo de esto.
- `NN_<nombre>_tramo1.wav`{", " + tramos if tramos else ""} — los {N_TRAMOS} fragmentos de
  {VENTANA_TRAMO_S} s, uno por cada uno de {N_TRAMOS} bloques iguales del track, que votaron el
  acuerdo. Sirven para oír si la tonalidad cambia a lo largo del tema.

Todos son copias al sample rate y con los canales del archivo original, en WAV de 16 bits
(unos {_mb_por_track_estereo_44k():.0f} MB por track estéreo a 44.1 kHz); el original no se toca.

El análisis de `tono()` usa el **promedio de los canales** (mono), pero el fragmento se exporta
en estéreo: lo que suene solo en contrafase entre izquierda y derecha no llegó al análisis.

## Qué escuchar

- Poné una nota o acorde de referencia de cada key (un teclado, una app de piano) sobre la
  ventana central, y fijate cuál "asienta": la tónica que suena a casa.
- **Error de modo** (misma tónica, mayor contra menor, p.ej. Am contra A): escuchá la
  **tercera** sobre la tónica. Tercera menor (3 semitonos) = modo menor, suena más oscuro;
  tercera mayor (4 semitonos) = modo mayor, más brillante.
- **Relativo** (p.ej. Am contra C): comparten las mismas notas; la diferencia es cuál se
  siente como tónica. Fijate dónde "descansa" el bajo.
- Si los tramos suenan en keys distintas entre sí, anotalo en `notas`: puede que las dos
  referencias tengan parte de razón.

`veredicto`: `motor` / `rekordbox` / `ninguna` / `no sé`.
"""


def escribir_indice_md(filas: list[dict], destino: Path) -> None:
    lineas = [_explicacion_md(), "## Tracks", "",
              "| # | track | motor | rekordbox | relación | error de modo | acuerdo | ventana central | estado |",
              "|---|---|---|---|---|---|---|---|---|"]
    for f in filas:
        ventana = f"{f['ventana_inicio']}–{f['ventana_fin']}" if f["ventana_inicio"] else "—"
        lineas.append(
            f"| {f['n']} | {f['nombre'].replace('|', '/')} "
            f"| {f['key_motor_camelot']} ({f['key_motor_clasica']}) "
            f"| {f['key_rekordbox_camelot']} ({f['key_rekordbox_tonality']}) "
            f"| {f['relacion']} | {f['error_de_modo']} | {f['acuerdo']} "
            f"| {ventana} | {f['estado']} |")
    lineas.append("")
    for f in filas:
        lineas += [f"### {f['n']}. {f['nombre']}", "",
                   f"- ruta original: `{f['ruta']}`",
                   f"- tramos del consenso: {f['tramos'] or '—'}"]
        if f["estado"] == OK:
            lineas += [f"- ventana central: {f['ventana_inicio']} a {f['ventana_fin']} "
                       f"({f['ventana_inicio_s']} s a {f['ventana_fin_s']} s) → "
                       f"`{f['archivo_ventana_central']}`",
                       f"- tramos: {', '.join(f'`{t}`' for t in f['archivos_tramos'].split('|') if t) or '—'}"]
        else:
            lineas.append(f"- **{f['estado']}**: no se generaron fragmentos")
        lineas += ["- veredicto (`motor` / `rekordbox` / `ninguna` / `no sé`): ", "- notas: ", ""]
    destino.write_text("\n".join(lineas), encoding="utf-8")


# --- Corrida ----------------------------------------------------------------------------


def _exigir_salida_vacia(salida: Path) -> None:
    """`--salida` tiene que no existir o estar vacía. Se valida antes de escribir nada.

    Los fragmentos tienen nombres fijos (`NN_<nombre>_...wav`): en una carpeta con cosas
    pisarían un WAV del mismo nombre sin aviso (p.ej. si `--salida` es la carpeta de la
    música), y al regenerar con otro `-n` o `--seed` dejarían WAVs viejos que no figuran en el
    índice nuevo. Y un `escucha.md` anterior puede tener veredictos cargados.
    """
    if not salida.exists():
        return
    if not salida.is_dir():
        raise EscuchaIncompleta(f"--salida existe y no es una carpeta: {salida}")
    contenido = sorted(p.name for p in salida.iterdir())
    if contenido:
        raise EscuchaIncompleta(
            f"--salida ya tiene {len(contenido)} elemento(s) (p.ej. {contenido[0]}): {salida}\n"
            "  Tiene que no existir o estar vacía: los fragmentos pisarían archivos con el mismo "
            "nombre o quedarían mezclados con los de otra corrida, y un escucha.md anterior "
            "puede tener veredictos cargados.\n  Usá una carpeta nueva.")


def correr(analisis_csv: Path, gt_csv: Path, salida: Path, acuerdo_de: Path | None = None,
           n: int = 10, semilla: int = SEMILLA) -> dict:
    for etiqueta, p in (("--analisis", analisis_csv), ("--ground-truth", gt_csv),
                        ("--acuerdo-de", acuerdo_de)):
        if p is not None and not p.is_file():
            raise EscuchaIncompleta(f"No existe el CSV de {etiqueta}: {p}")
    if n < 1:
        raise EscuchaIncompleta(f"-n tiene que ser al menos 1 (recibí {n})")
    _exigir_salida_vacia(salida)

    analisis = leer_csv(analisis_csv)
    if any((f.get("metodo") or "").strip() == METODO_CONSENSO for f in analisis):
        raise EscuchaIncompleta(
            "El CSV de --analisis es una corrida con consenso: su key_est es la del voto, "
            "no la de tono().\n  Pasá la pasada SIN consenso en --analisis y esta en --acuerdo-de.")
    _rechazar_rutas_repetidas(analisis, "--analisis")

    sin_par: list[str] = []
    if acuerdo_de is not None:
        con_consenso = leer_csv(acuerdo_de)
        combinadas, sin_par = combinar_acuerdo(analisis, con_consenso)
        if not combinadas:
            ej_a = next((r for f in analisis if (r := (f.get("ruta") or "").strip())), "—")
            ej_c = next((r for f in con_consenso if (r := (f.get("ruta") or "").strip())), "—")
            raise EscuchaIncompleta(
                f"Ninguna ruta coincidió entre las dos pasadas: las {len(analisis)} filas de "
                f"--analisis y las {len(con_consenso)} de --acuerdo-de no comparten ni una ruta "
                "(el cruce es por ruta exacta).\n"
                "  Causa probable: las rutas están escritas distinto (letra de unidad, / contra \\, "
                "mayúsculas) o los dos CSV son de sesiones distintas.\n"
                f"  Ejemplo en --analisis:   {ej_a}\n"
                f"  Ejemplo en --acuerdo-de: {ej_c}")
        analisis = combinadas
        print(f"Acuerdo tomado de {acuerdo_de.name} (cruce por ruta): "
              f"{len(analisis)} con par · {len(sin_par)} sin par (quedan fuera)")
    elif not _hay_acuerdo(analisis):
        raise EscuchaIncompleta(
            "Ninguna fila de --analisis trae la columna acuerdo: es un CSV anterior a aabd83d.\n"
            "  Pasá también la pasada con consenso de la misma sesión, que sí la tiene:\n"
            "    --acuerdo-de gt_out/analisis_con-consenso.csv\n"
            "  (o reanalizá con la etapa A actual, que mide el acuerdo siempre).")

    sel = seleccionar(analisis, leer_csv(gt_csv))
    candidatos, k = sel["candidatos"], sel["conteo"]
    elegidos = elegir(candidatos, n, semilla)
    print(f"Cruzados con el GT: {k['cruzados']} · fuera: {k['sin_referencia']} sin key de "
          f"referencia · {k['sin_acuerdo']} sin acuerdo · {k['no_unanime']} no unánimes · "
          f"{k['misma_key']} misma key · {k['sin_key_motor']} sin key del motor · "
          f"{k['ambiguos']} ambiguos (nombre repetido) · {k['sin_gt']} sin GT")
    print(f"Candidatos (unánime y distinta de Rekordbox): {len(candidatos)} · "
          f"elegidos: {len(elegidos)}"
          + (f" (muestra con semilla {semilla})" if len(elegidos) < len(candidatos) else ""))
    if not elegidos:
        print("No hay nada para escuchar: no se escribió ninguna salida.")
        return {"candidatos": len(candidatos), "elegidos": 0, "sin_par": len(sin_par),
                "filas": [], "conteo": k}

    salida.mkdir(parents=True, exist_ok=True)

    ancho = max(2, len(str(len(elegidos))))
    filas = []
    for i, c in enumerate(elegidos, 1):
        prefijo = f"{i:0{ancho}d}_{nombre_seguro(Path(c.archivo).stem)}"
        fr = exportar_track(c.ruta, prefijo, salida)
        filas.append(fila_indice(i, c, fr))
        print(f"  [{i}/{len(elegidos)}] {c.nombre[:44]:44} motor {c.key_motor:>3} · "
              f"rekordbox {c.key_rekordbox:>3} · {fr.estado}", flush=True)

    escribir_indice_csv(filas, salida / INDICE_CSV)
    escribir_indice_md(filas, salida / INDICE_MD)
    print(f"→ {salida / INDICE_CSV}\n→ {salida / INDICE_MD}")
    return {"candidatos": len(candidatos), "elegidos": len(elegidos), "sin_par": len(sin_par),
            "filas": filas, "conteo": k}


def main(argv=None) -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="ground_truth.escucha",
        description="Fragmentos para resolver a oído los tracks donde el motor está seguro "
                    "y no coincide con Rekordbox.")
    ap.add_argument("--analisis", required=True, type=Path,
                    help="CSV de la etapa A SIN consenso (la key de tono()).")
    ap.add_argument("--acuerdo-de", type=Path, default=None,
                    help="CSV de la etapa A CON consenso, de donde tomar acuerdo y tramos "
                         "(cruce por ruta). Hace falta si --analisis es anterior a aabd83d.")
    ap.add_argument("--ground-truth", required=True, type=Path,
                    help="CSV de ground_truth.rekordbox (rekordbox_tracks.csv).")
    ap.add_argument("--salida", type=Path, default=Path("escucha"))
    ap.add_argument("-n", type=int, default=10, help="Cuántos tracks elegir (default 10).")
    ap.add_argument("--seed", type=int, default=SEMILLA,
                    help=f"Semilla de la muestra (default {SEMILLA}, la de benchmark.analizar).")
    args = ap.parse_args(argv)

    try:
        correr(args.analisis, args.ground_truth, args.salida, args.acuerdo_de, args.n, args.seed)
    except EscuchaIncompleta as e:
        print(f"\n{e}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
