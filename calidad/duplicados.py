"""Tarea 5.2 — detección de duplicados en la biblioteca.

Agrupa archivos que contienen EL MISMO AUDIO aunque se llamen distinto, y propone cuál
conservar según la calidad REALMENTE MEDIDA. No borra ni mueve nada: propone.

    python -m calidad.duplicados --audio <carpeta>
        [--rekordbox-xml <colección.xml>] [--itunes-xml <Library.xml>]

Con esos XML (tarea 5.26), entre dos copias de calidad medida equivalente se conserva la
que Rekordbox o iTunes ya referencian: descartarla rompería sus cues, su beatgrid y su play
count, y la otra copia no tiene nada de eso. Es SOLO un desempate: nunca compensa una peor
calidad, y el motivo de cada fila dice cuándo decidió la referencia.

Qué es y qué NO es un duplicado
-------------------------------
Duplicado = el mismo audio en otro archivo (otro formato, otra descarga, otra copia).
NO son duplicados: original vs edit, master v1 vs v2, un remix, dos tomas distintas.
Por eso hay dos compuertas y tienen que pasar las dos:

  1. DURACIÓN parecida. Si difieren más que la tolerancia, es otra versión y ni se
     compara la huella. Barato y corta de entrada la mayoría de los falsos positivos.
  2. HUELLA de audio muy parecida. Mira 24 tramos de 2,5 s (60 s en total) repartidos por el
     track desde la intro salteada (30 s) hasta cerca del final, no un bloque contiguo
     (tarea 5.27; `ventanas_huella` dice dónde mira).

     LÍMITE REAL (medido): entre un tramo y el siguiente queda un hueco SIN MIRAR de
     `paso - 2,5 s` (≈ 4,5 s en un track de 200 s, ≈ 10,5 s en uno de 346 s, y crece con la
     duración). Una diferencia de contenido más corta que ~2 pasos (≈ 14-30 s en tracks de
     200-350 s) puede caer entera en esos huecos o tocar un solo tramo, y el par se agrupa
     si la duración es compatible. Reemplazar 4 s de un track de 200 s justo entre dos
     tramos da similitud > 0,999 (lo fija un test). Sobre 8 tracks reales de 200-480 s,
     pegando a la MISMA duración audio de otro tema en el medio, la huella siguió agrupando
     el par en 18 de 24 casos con 10 s distintos, 12 de 24 con 20 s y 10 de 24 con 30 s
     (la ventana contigua vieja: 21, 20 y 20 de 24). Y un reemplazo de 20 s que caía entre
     los 40 y los 60 s, que la ventana vieja detectaba (medición de la auditoría), hoy puede
     pasar. Lo que
     mejora: cualquier diferencia fuera de los 30-90 s, que antes no se veía de ningún
     modo. Más cobertura exigiría más tramos (más decodificación) o un umbral distinto: es
     decisión de producto, no está hecha.

     La grilla de tramos sale de la duración que da el archivo mismo (libsndfile: frames /
     samplerate). Si ese dato no coincide con el audio (VBR sin cabecera), el último tramo
     viene corto o no existe y se lee en una pasada secuencial con la duración real. Una
     discrepancia chica que deje al último tramo entero (< ~0,25 s de audio faltante) no se
     detecta: la grilla queda corrida esa fracción.

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
import logging
import math
import os
import plistlib
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np
from analizar_calidad import _espectro_db

from calidad.corte_espectral import EXTS
from calidad.corte_espectral import analizar_uno as medir_calidad

log = logging.getLogger(__name__)

# Sample rate de la huella. Fijo y BAJO a propósito: por debajo del corte de cualquier
# codec, así el mismo audio en MP3 y en WAV produce la misma huella.
SR_HUELLA = 22050
F_MIN, F_MAX = 60.0, 6000.0
N_TRAMOS, N_BANDAS = 24, 24

# Audio analizado (tarea 5.27): DURACION_S segundos en total, repartidos en N_TRAMOS tramos
# de DURACION_S / N_TRAMOS a lo largo de TODO el track (desde DESDE_S para saltear la intro).
# Antes era un bloque contiguo de 60 s (30-90 s): en un track de 346 s la huella era ciega al
# 83% y dos archivos de duración casi igual y distinto contenido en el medio quedaban
# agrupados. El costo de decodificación es el mismo (60 s), y la huella sigue siendo
# N_TRAMOS x N_BANDAS: lo que cambia es DÓNDE se mira.
# Ubicación de los tramos: el primero en DESDE_S y los demás cada `paso` segundos, con `paso`
# CUANTIZADO a PASO_CUANTO_S (ver `ventanas_huella`). NO proporcional a la duración exacta:
# medido sobre audio real, dos MP3 del mismo tema que difieren 0.34 s de duración daban
# similitud 0.9978 con la ventana absoluta de antes y 0.65 con tramos proporcionales,
# porque la huella es muy sensible a que el tramo se corra una fracción de segundo.
# Cuantizar el paso hace que dos copias del mismo audio (milisegundos de diferencia) tengan
# EXACTAMENTE los mismos tramos, salvo que su duración cruce un múltiplo de N_TRAMOS x
# PASO_CUANTO_S (12 s): en ese caso la grilla difiere, y `mismo_audio` las compara sobre la
# grilla de la más corta (`_huellas_comparables`) en vez de contra tramos desalineados.
DESDE_S, DURACION_S = 30.0, 60.0
PASO_CUANTO_S = 0.5
# Un track de más de esto se analiza desde DESDE_S; uno más corto, desde 0.
MIN_PARA_SALTEAR_INTRO_S = 100.0

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
    # Los tramos con que se calculó la huella (`ventanas_huella`): dos copias compatibles por
    # duración pero con tramos distintos se comparan sobre los mismos (`mismo_audio`).
    # `None` = no se sabe (una ficha armada a mano): se compara la huella tal cual.
    grilla: tuple[tuple[float, float], ...] | None = None


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


def _huellas_comparables(a: Ficha, b: Ficha, cache: dict | None) -> tuple:
    """Las dos huellas SOBRE LOS MISMOS TRAMOS.

    Si las grillas coinciden (lo normal: la grilla cambia solo si la duración cruza un
    múltiplo de N_TRAMOS x PASO_CUANTO_S = 12 s) son las huellas de cada ficha, sin tocar
    nada. Si difieren, miran cosas distintas y la similitud cae a ~0 aunque sea el mismo
    audio (95 s vs 96 s, o la cola recortada 5 s en 346 s). Entonces la ficha MÁS LARGA se
    recalcula sobre la grilla de la más corta: sus tramos entran seguro en las dos, y el
    resultado no depende del orden en que se pasan (desempata la ruta si duran igual).
    ~60 s de decodificación por par afectado; con `cache` no se repite para el mismo archivo.
    Si no se puede recalcular, se avisa y se compara tal cual."""
    if (a.huella is None or b.huella is None or a.grilla is None or b.grilla is None
            or a.grilla == b.grilla):
        return a.huella, b.huella
    larga, corta = (a, b) if (a.duracion_s, a.ruta) > (b.duracion_s, b.ruta) else (b, a)
    clave = (larga.ruta, corta.grilla)
    if cache is not None and clave in cache:
        h = cache[clave]
    else:
        try:
            h = huella_en_grilla(larga.ruta, corta.grilla)
        except Exception as e:  # noqa: BLE001
            log.warning("huella: no se pudo comparar %s con %s sobre los mismos tramos: %s",
                        larga.archivo, corta.archivo, e)
            h = None
        if cache is not None:
            cache[clave] = h
    if h is None:
        return a.huella, b.huella
    return (h, b.huella) if larga is a else (a.huella, h)


def mismo_audio(a: Ficha, b: Ficha, umbral: float = UMBRAL,
                cache: dict | None = None) -> tuple[bool, float]:
    """Las dos compuertas. Devuelve (es_duplicado, similitud)."""
    if not duracion_compatible(a.duracion_s, b.duracion_s):
        return False, 0.0
    s = similitud(*_huellas_comparables(a, b, cache))
    return s >= umbral, s


def ventanas_huella(duracion_s: float) -> list[tuple[float, float]]:
    """Dónde mira la huella: `[(offset_s, duracion_s)]` en orden, un tramo por elemento.

    Con espacio de sobra (≥ DURACION_S desde DESDE_S): N_TRAMOS tramos de
    DURACION_S / N_TRAMOS, el primero en `desde` y los demás cada `paso`, donde `paso` es
    la porción que le toca a cada tramo (lo analizable / N_TRAMOS) REDONDEADA PARA ABAJO a
    un múltiplo de PASO_CUANTO_S, y nunca menos que el largo del tramo. Redondear para
    abajo asegura que el último tramo entra; queda sin mirar la cola (como mucho
    N_TRAMOS x PASO_CUANTO_S = 12 s más lo que sobre de la última porción), que es donde
    dos copias del mismo tema más suelen diferir (silencio final, fade).
    Un track muy corto (menos de DURACION_S analizables) devuelve UNA ventana con todo lo
    que hay: ya queda cubierto entero y `huella` lo parte en N_TRAMOS."""
    desde = DESDE_S if duracion_s > MIN_PARA_SALTEAR_INTRO_S else 0.0
    util = duracion_s - desde
    if util <= 0:
        return []
    if util < DURACION_S:
        return [(desde, util)]
    largo = DURACION_S / N_TRAMOS
    paso = max(largo, math.floor(util / N_TRAMOS / PASO_CUANTO_S) * PASO_CUANTO_S)
    return [(desde + k * paso, largo) for k in range(N_TRAMOS)]


def muestra_para_huella(y: np.ndarray, sr: int = SR_HUELLA) -> np.ndarray:
    """Las muestras que mira la huella, sacadas de un track ya decodificado ENTERO: los
    tramos de `ventanas_huella` pegados uno tras otro. `huella(muestra_para_huella(y))` es
    la huella del track completo; `cargar_ficha` lee lo mismo sin decodificar todo."""
    partes = []
    for off, dur in ventanas_huella(y.size / sr):
        i = int(round(off * sr))
        partes.append(y[i:i + int(round(dur * sr))])
    return np.concatenate(partes) if partes else y[:0]


def _muestra_de_tramos(tramos: list[np.ndarray], ventanas: list[tuple[float, float]]
                       ) -> np.ndarray | None:
    """Los tramos pegados, cada uno con su largo EXACTO: `huella` parte la muestra en
    N_TRAMOS iguales, y un tramo de una muestra de más o de menos (el resample no siempre
    cierra justo) correría todos los siguientes. Un tramo apenas corto (< 0,25 s) se completa
    con silencio; uno más corto (o ausente) devuelve `None`: la duración declarada no
    coincide con el audio y la grilla no sirve."""
    partes = []
    for y, (_, largo) in zip(tramos, ventanas, strict=True):
        n = int(round(largo * SR_HUELLA))
        if y.size < n - SR_HUELLA // 4:
            return None
        partes.append(np.pad(y[:n], (0, max(0, n - y.size))))
    return np.concatenate(partes) if partes else None


def _leer_muestra(ruta: str, ventanas_de) -> tuple[float, list[tuple[float, float]], np.ndarray]:
    """`(duración, ventanas, muestra)` de un archivo, leyendo SOLO los tramos.

    Camino normal: UNA apertura con libsndfile y un seek por tramo. Si libsndfile no lo abre,
    o algún tramo no está (la duración declarada no coincide con el audio real, típico de un
    VBR sin cabecera), una pasada secuencial que se queda con los tramos y arma la grilla con
    la duración REAL (`cargar_tramos_en_streaming`: memoria acotada, ffmpeg si hace falta).
    Lanza si no se pudo leer."""
    from motor import decodificar as dec  # perezoso, igual que en corte_espectral

    try:
        dur, ventanas, tramos = dec.cargar_tramos(ruta, SR_HUELLA, ventanas_de)
        muestra = _muestra_de_tramos(tramos, ventanas)
        if muestra is not None:
            return dur, ventanas, muestra
    except Exception:  # noqa: BLE001  # libsndfile no lo abre, o buscar pasado el final
        pass
    dur, ventanas, tramos = dec.cargar_tramos_en_streaming(ruta, SR_HUELLA, ventanas_de)
    muestra = _muestra_de_tramos(tramos, ventanas)
    if muestra is None:
        raise ValueError("el archivo trae menos audio del que hace falta para la huella")
    return dur, ventanas, muestra


def cargar_ficha(ruta: str) -> Ficha:
    nombre = Path(ruta).name
    try:
        dur, ventanas, muestra = _leer_muestra(ruta, ventanas_huella)
    except Exception as e:  # noqa: BLE001
        # Sin huella el archivo queda afuera del agrupamiento: que al menos diga por qué.
        log.warning("huella: %s queda sin huella: %s", nombre, e)
        try:
            from motor.decodificar import duracion

            return Ficha(ruta, nombre, float(duracion(ruta)), None)
        except Exception:  # noqa: BLE001
            return Ficha(ruta, nombre, 0.0, None)
    return Ficha(ruta, nombre, dur, huella(muestra, SR_HUELLA), tuple(ventanas))


def huella_en_grilla(ruta: str, grilla) -> np.ndarray | None:
    """La huella de `ruta` mirando EXACTAMENTE los tramos de `grilla` (ver `Ficha.grilla`).
    Lanza si no se pudo leer. ~60 s de decodificación."""
    ventanas = list(grilla)
    _, _, muestra = _leer_muestra(ruta, lambda _d: ventanas)
    return huella(muestra, SR_HUELLA)


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
    recalculadas: dict = {}         # huellas sobre la grilla de otro, para no repetir la lectura
    for i in range(len(fichas)):
        if fichas[i].huella is None:
            continue
        for j in range(i + 1, len(fichas)):
            if fichas[j].huella is None:
                continue
            es_dup, s = mismo_audio(fichas[i], fichas[j], umbral, recalculadas)
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


# --- Copias ya referenciadas por Rekordbox / iTunes (tarea 5.26) -----------------------------


def normalizar_ruta(ruta: str) -> str:
    """Clave para comparar rutas entre fuentes, SIN tocar el disco: absoluta (una relativa
    se cuelga del directorio de trabajo, igual que `motor.cli.clave_lexica`), normalizada y
    con las mayúsculas del SO. Sin lo primero, `--carpeta descargas` nunca coincidiría con
    las `Location` absolutas del XML y el desempate quedaría apagado sin avisar.

    Límites conocidos (no se resuelven acá): rutas UNC, NFC/NFD, symlinks, nombres 8.3.
    """
    return os.path.normcase(os.path.normpath(os.path.join(os.getcwd(), str(ruta))))


def _ruta_de_xml(ruta: str) -> str:
    """La ruta que devuelven los parsers de Location, con su '/' inicial repuesta en POSIX.

    `ground_truth.rekordbox._ruta_local` hace `lstrip("/")` y así 'file://localhost/C:/x'
    queda bien, pero '/Users/x' (macOS/Linux) pierde la raíz y se leería como relativa.
    Una ruta con unidad ('C:/x') no se toca.
    """
    if ruta and not re.match(r"^[A-Za-z]:", ruta) and not ruta.startswith(("/", "\\")):
        return "/" + ruta
    return ruta


def _referencias_de_ubicaciones(ubicaciones, origen: str) -> dict[str, str]:
    return {normalizar_ruta(_ruta_de_xml(u)): origen for u in ubicaciones if u}


def referencias_rekordbox(xml_path: Path) -> dict[str, str]:
    """Rutas que el XML de Rekordbox ya referencia (cues, beatgrid, play count).

    Devuelve {ruta normalizada: "Rekordbox"}. Falla si el XML no es de Rekordbox: ignorar
    el error propondría descartar justo la copia que tiene el trabajo encima.
    """
    from ground_truth.rekordbox import parsear  # perezoso: calidad no depende de ground_truth

    return _referencias_de_ubicaciones((t["location"] for t in parsear(Path(xml_path))),
                                       "Rekordbox")


def referencias_itunes(xml_path: Path) -> dict[str, str]:
    """Rutas que la biblioteca de iTunes ya referencia (el 'Library.xml' exportado, un plist).

    Devuelve {ruta normalizada: "iTunes"}. Falla si no es una biblioteca de iTunes.
    """
    from ground_truth.rekordbox import _ruta_local  # mismo 'file://localhost/C:/...'

    with Path(xml_path).open("rb") as fh:
        try:
            biblioteca = plistlib.load(fh)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"No pude leer {xml_path} como biblioteca de iTunes: {e}") from e
    pistas = biblioteca.get("Tracks") if isinstance(biblioteca, dict) else None
    if pistas is None:
        raise ValueError("No encontré 'Tracks' — ¿es el Library.xml de iTunes?")
    try:
        ubicaciones = [_ruta_local(p.get("Location", "")) for p in pistas.values()]
    except (AttributeError, TypeError) as e:
        raise ValueError("'Tracks' no tiene la forma de un Library.xml de iTunes "
                         "(se esperaba un diccionario de pistas con 'Location').") from e
    return _referencias_de_ubicaciones(ubicaciones, "iTunes")


def unir_referencias(*fuentes: dict[str, str]) -> dict[str, str]:
    """Junta fuentes. Una ruta que está en las dos queda como 'Rekordbox e iTunes'."""
    out: dict[str, str] = {}
    for fuente in fuentes:
        for ruta, origen in fuente.items():
            previo = out.get(ruta)
            if not previo:
                out[ruta] = origen
            elif origen not in previo.split(" e "):
                out[ruta] = f"{previo} e {origen}"
    return out


def referencias_de_xml(rekordbox_xml: Path | None = None,
                       itunes_xml: Path | None = None) -> dict[str, str]:
    """Las referencias de las fuentes que se hayan pasado; {} si no se pasó ninguna."""
    fuentes = []
    if rekordbox_xml:
        fuentes.append(referencias_rekordbox(rekordbox_xml))
    if itunes_xml:
        fuentes.append(referencias_itunes(itunes_xml))
    return unir_referencias(*fuentes)


def decidir(grupo: list[int], fichas: list[Ficha], calidades: dict,
            referencias: dict[str, str] | None = None) -> list[FilaDuplicado]:
    """Elige cuál conservar dentro de un grupo y arma las filas del CSV.

    `referencias` ({ruta normalizada: origen}, ver `referencias_rekordbox` / `_itunes`) es
    SOLO un desempate: entre copias de calidad medida equivalente (mismo `_puntaje_calidad`)
    gana la que ya está referenciada, porque descartarla rompe sus cues, su beatgrid y su
    play count, y la otra copia no tiene nada de eso. Nunca compensa una diferencia de
    calidad. Sin `referencias` el comportamiento es el de siempre.

    "Equivalente" es la misma tupla de `_puntaje_calidad`: corte medido REDONDEADO a 0.1 kHz,
    sin muro y lossless. Dos cortes que caen a un lado y otro de un redondeo (20.04 y 20.06)
    NO empatan aunque difieran 0.02 kHz: gana el de mejor puntaje y la referencia no entra.
    """
    refs = referencias or {}

    def origen(i: int) -> str:
        return refs.get(normalizar_ruta(fichas[i].ruta), "")

    # El orden es el de siempre (estable: los empatados quedan en el orden del grupo). La
    # referencia SOLO elige el ganador entre los empatados en el mejor puntaje; el resto de
    # las filas sigue en el orden que tenían.
    por_calidad = sorted(grupo, key=lambda i: _puntaje_calidad(calidades[i]), reverse=True)
    mejor_puntaje = _puntaje_calidad(calidades[por_calidad[0]])
    cabeza = [i for i in por_calidad if _puntaje_calidad(calidades[i]) == mejor_puntaje]
    ganador = next((i for i in cabeza if origen(i)), cabeza[0])
    ordenados = [ganador] + [i for i in por_calidad if i != ganador]
    cal_g = calidades[ganador]
    puntaje_g = mejor_puntaje
    empatados = [i for i in grupo
                 if i != ganador and _puntaje_calidad(calidades[i]) == puntaje_g]
    desempata_la_referencia = bool(origen(ganador)) and any(not origen(i) for i in empatados)

    filas = []
    for i in ordenados:
        cal = calidades[i]
        if i == ganador:
            mejor = (f"corte {cal.corte_medido_khz:.1f} kHz"
                     + (" sin muro" if cal.es_muro == "no" else " con muro"))
            if desempata_la_referencia:
                motivo = (f"calidad equivalente a la de otra copia; se conserva esta porque ya "
                          f"está referenciada por {origen(i)} (descartarla rompe sus cues y "
                          f"su beatgrid): {mejor}")
            else:
                motivo = f"mejor calidad medida del grupo: {mejor}"
            accion = CONSERVAR
        else:
            dif = cal_g.corte_medido_khz - cal.corte_medido_khz
            empata = _puntaje_calidad(cal) == puntaje_g
            if empata and origen(ganador) and not origen(i):
                motivo = (f"copia equivalente del mismo audio; se conserva la que ya está "
                          f"referenciada por {origen(ganador)} y esta no")
            elif dif > 0.05:
                motivo = (f"corta {dif:.1f} kHz más abajo que el que se conserva "
                          f"({cal.corte_medido_khz:.1f} vs {cal_g.corte_medido_khz:.1f})")
            elif cal.es_muro == "si" and cal_g.es_muro == "no":
                motivo = "mismo corte pero con muro de codec; el conservado es natural"
            elif cal.lossless == "no" and cal_g.lossless == "si":
                motivo = "misma calidad medida; se conserva el sin pérdida"
            elif origen(i) and not empata:
                # Pierde por calidad (aunque sea por el borde del redondeo): no es un empate
                # y la referencia no desempató, así que no se puede decir "equivalente".
                motivo = (f"calidad medida algo menor que la del que se conserva "
                          f"({cal.corte_medido_khz:.2f} vs {cal_g.corte_medido_khz:.2f} kHz): "
                          f"no es un empate, la referencia no desempata")
            else:
                motivo = "copia equivalente del mismo audio"
            if origen(i):
                # Descartar una copia referenciada rompe lo que su biblioteca guarda de ella
                # (pasa si pierde por calidad, o si las dos lo están y el empate no se puede
                # resolver con este criterio): que quien revisa lo vea en el motivo.
                motivo += (f". OJO: esta copia está referenciada por {origen(i)}; "
                           f"descartarla rompe sus cues, su beatgrid y su play count")
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
    ap.add_argument("--rekordbox-xml", type=Path, default=None,
                    help="XML de Rekordbox: entre copias equivalentes se conserva la que "
                         "ya referencia (cues, beatgrid).")
    ap.add_argument("--itunes-xml", type=Path, default=None,
                    help="Library.xml de iTunes: mismo desempate para sus pistas.")
    args = ap.parse_args(argv)

    try:
        referencias = referencias_de_xml(args.rekordbox_xml, args.itunes_xml)
    except (OSError, ValueError, ET.ParseError) as e:
        print(f"No pude leer la biblioteca de referencia: {e}")
        return 2

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
        del_grupo = decidir(g, fichas, calidades, referencias)
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
