"""Onda de 3 bandas para el editor grande (f52): graves, medios y agudos del audio REAL, a
resolución fija de 10 ms, y la grilla de beats ESTIMADA.

El problema que resuelve: en la onda de una banda (`motor/peaks.py`) todo es del mismo color y
el kick no se distingue del resto. Separando el audio en tres bandas se ve cada golpe: el kick
en graves, el hi-hat en agudos.

Bandas (filtros Butterworth de orden 4 en SOS, aplicados ida y vuelta: FASE CERO, o sea que un
golpe queda en su lugar y no corrido unos milisegundos como con un filtro causal):

- graves  = pasabajos a 150 Hz. La fundamental del kick de techno está entre ~45 y ~100 Hz y el
  sub-bajo debajo; 150 Hz deja entrar el «punch» del kick (~80-120 Hz) sin llegar al cuerpo
  del clap o el snare (~200 Hz para arriba).
- agudos  = pasaaltos a 2,5 kHz. Hats, rides, platillos y el ruido del clap viven arriba de
  ~3 kHz; desde 2,5 kHz se los agarra enteros sin meter la fundamental de voces y sintes.
- medios  = la señal MENOS graves MENOS agudos. Con filtros de ida y vuelta, la respuesta del
  pasabajos es |H|² y 1 − |H_pb(f)|² es exactamente un pasaaltos Butterworth de la misma
  frecuencia: los medios son un pasabanda 150 Hz–2,5 kHz y las tres bandas SUMAN la señal
  original (como un crossover Linkwitz-Riley, pero sin corrimiento de fase). Además sale gratis:
  dos filtros en vez de tres. La pendiente efectiva es de 48 dB/octava. Medido (ver el informe
  de la rama f52): con orden 6 u 8 la separación de un kick sintético, un hat y un stab era la
  misma y el pre-ringing del pasabajos 10 veces mayor; por eso orden 4.

Envolvente: por cada cuadro de 10 ms, el máximo de |banda| en ese tramo (el mismo criterio de
«pico» que la onda de una banda), sobre la mezcla mono de los canales (`mezcla_mono` dice por
qué). 100 cuadros por segundo alcanzan para que un kick sea un pico y no un manchón (a 128 BPM
hay 47 cuadros entre kick y kick).

Normalización (relativa DENTRO del track, no volumen absoluto; la respuesta de la API lo dice):
cada banda se divide por su referencia, el percentil 99 de su envolvente en ESTE track, sobre
los cuadros con sonido (los golpes más fuertes valen ~1 y el 1 % más alto queda en 1). Así los
hats se ven aunque suenen 20 dB debajo del kick. Dos pisos para no agrandar lo que no está,
porque estirar una banda casi vacía dibujaría golpes que no se oyen (§6):
  1. una banda nunca se estira más de `RANGO_MAX_DB` (40 dB) respecto de la más fuerte del
     track: más abajo hay fuga del filtro o ruido, y se ve chico, como es (un sub-bajo puro
     deja ~−70 dB de fuga en los medios: con su propio p99 se dibujaría a tope);
  2. nada por debajo de `PISO_DBFS` (−40 dBFS) se estira: un archivo en silencio con dither no
     se dibuja como ruido a tope. Un tema masterizado de club tiene sus bandas muy arriba de
     eso (el kick ~−3 dBFS, los hats ~−20), así que el piso solo actúa sobre casi-silencio.

Memoria: el archivo se lee POR BLOQUES y se filtra por SEGMENTOS de `SEGMENTO_FRAMES` con
`MARGEN_S` de contexto real a cada lado (el filtro de ida y vuelta necesita ver un poco antes y
un poco después; el transitorio del filtro decae en ~3 ms, 100 ms sobran por mucho). El
resultado es el mismo que filtrar el archivo entero de una vez (lo prueba
`motor/tests/test_bandas.py` contra ese oráculo) pero la memoria no crece con la duración.

Caché: un archivo binario por track en la MISMA carpeta que los picos (`<datos>/peaks`), con la
misma clave (ruta + tamaño + mtime) y el mismo tope de archivos (`peaks.CACHE_MAX_ARCHIVOS`),
cuantizado a uint8 y comprimido con zlib: un tema de 6 min son 108.000 bytes sin comprimir.

Grilla: `estimar_grilla` (ver su docstring). Es ESTIMADA: más adelante una grilla importada de
Rekordbox la reemplaza.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from motor.peaks import CACHE_MAX_ARCHIVOS, UnreadableAudio, _archivo_cache, _podar

BANDAS = ("graves", "medios", "agudos")
TASA_HZ = 100                    # cuadros por segundo (10 ms)
CORTE_GRAVES_HZ = 150.0
CORTE_AGUDOS_HZ = 2500.0
ORDEN_FILTRO = 4
PERCENTIL_REFERENCIA = 99.0
RANGO_MAX_DB = 40.0
PISO_DBFS = -40.0

BLOCK_FRAMES = 65536             # lectura del archivo
SEGMENTO_FRAMES = 1 << 17        # filtrado (~3 s a 44,1 kHz)
MARGEN_S = 0.1                   # contexto a cada lado de un segmento

# Subirla si cambia CÓMO se calculan las bandas (cortes, orden, normalización, tasa):
# invalida todas las cachés viejas.
BANDAS_VERSION = 1
CACHE_SUFIJO = ".bandas3"
_MAGIA = b"DJB3"


@dataclass(frozen=True)
class Bandas:
    # (3, cuadros) uint8 en el orden de `BANDAS`: 0..255 ↔ 0..1 de la referencia de la banda.
    # Lo que calcula `compute_bandas` y lo que lee la caché son los MISMOS bytes.
    q: np.ndarray
    referencias: tuple[float, float, float]   # la amplitud (escala de la muestra) que vale 1
    duration_s: float                          # duración del audio DECODIFICADO
    sample_rate: int
    channels: int

    @property
    def cuadros(self) -> int:
        return int(self.q.shape[1])

    def valores(self) -> np.ndarray:
        """(3, cuadros) float64 en 0..1."""
        return self.q.astype(np.float64) / 255.0


# --- decodificación por bloques -----------------------------------------------------------
#
# Los mismos backends que `motor.peaks` (y que `librosa.load`): soundfile en streaming y, si no
# abre el archivo, audioread. Lo que la onda de una banda puede dibujar, esto también.

def _abrir(ruta: str, block_frames: int):
    """`(archivo, sr, canales, generador de bloques float32 (n, canales))`. El archivo lo cierra
    el que llama, con `with`, también si nunca llegó a leer (un `sr` que no sirve). Los errores
    de decodificación salen como `UnreadableAudio`, también los que aparecen a mitad del
    archivo."""
    import soundfile as sf

    try:
        f = sf.SoundFile(ruta, mode="r")
    except (RuntimeError, TypeError, ValueError):     # LibsndfileError es un RuntimeError
        return _abrir_audioread(ruta)
    sr, ch, total = int(f.samplerate), int(f.channels), int(f.frames)
    if total <= 0 or sr <= 0 or ch <= 0:
        f.close()
        raise UnreadableAudio("el archivo no tiene audio")

    def bloques():
        pos = 0
        # libsndfile abre bien un FLAC truncado y falla RECIÉN al decodificar ese tramo (el
        # mismo caso que `peaks._con_soundfile`).
        try:
            for b in f.blocks(blocksize=block_frames, dtype="float32", always_2d=True):
                if not len(b):
                    break
                pos += len(b)
                yield b
        except (RuntimeError, ValueError) as e:
            raise UnreadableAudio(f"el archivo se corta o está dañado a los {pos / sr:.1f} s "
                                  f"({type(e).__name__}: {e})") from None

    return f, sr, ch, bloques()


def _abrir_audioread(ruta: str):
    try:
        import audioread
    except ImportError as e:      # pragma: no cover — viene con librosa
        raise UnreadableAudio(f"no hay con qué decodificar este formato ({e})") from None
    try:
        f = audioread.audio_open(ruta)
    except Exception as e:  # noqa: BLE001 — audioread tira lo que tire su backend
        raise UnreadableAudio(f"no se pudo decodificar el audio ({type(e).__name__}: {e})") from None
    sr, ch = int(f.samplerate), int(f.channels)
    if sr <= 0 or ch <= 0:
        f.close()
        raise UnreadableAudio("el archivo no tiene audio")

    def bloques():
        try:
            resto = b""
            for buf in f:            # PCM 16 bit little-endian, intercalado, de a pedazos
                datos = resto + buf
                util = len(datos) - len(datos) % (2 * ch)
                resto = datos[util:]
                if util:
                    yield np.frombuffer(datos[:util], dtype="<i2").reshape(-1, ch) \
                        .astype(np.float32) / np.float32(32768.0)
        except Exception as e:  # noqa: BLE001 — ídem
            raise UnreadableAudio(f"no se pudo decodificar el audio ({type(e).__name__}: {e})") \
                from None

    return f, sr, ch, bloques()


# --- envolventes -------------------------------------------------------------------------

def filtros(sr: int):
    """Los SOS del pasabajos de graves y del pasaaltos de agudos para esta frecuencia de
    muestreo."""
    from scipy.signal import butter

    nyq = sr / 2
    if nyq <= CORTE_AGUDOS_HZ:
        # Un archivo a 4 kHz de muestreo no tiene agudos que mostrar: se dice que no se puede.
        raise UnreadableAudio(f"la frecuencia de muestreo ({sr} Hz) es muy baja para separar "
                              f"los agudos (>{CORTE_AGUDOS_HZ:g} Hz)")
    sos_g = butter(ORDEN_FILTRO, CORTE_GRAVES_HZ, "lowpass", fs=sr, output="sos")
    sos_a = butter(ORDEN_FILTRO, CORTE_AGUDOS_HZ, "highpass", fs=sr, output="sos")
    return sos_g, sos_a


def mezcla_mono(bloque: np.ndarray) -> np.ndarray:
    """(n, canales) → (n,): el promedio de los canales, lo que suena en un equipo mono.

    Por qué mono y no el pico de cada canal como la onda de una banda: filtrar cada canal
    cuesta el doble (medido en f52: 2,0 s contra ~1 s para 6 min estéreo, y el tope es 2 s), y
    el kick del techno es mono. Lo que se pierde es lo que está en contrafase entre canales
    (un ensanchador estéreo exagerado), que en un equipo mono tampoco suena."""
    return bloque[:, 0] if bloque.shape[1] == 1 else bloque.mean(axis=1, dtype=np.float32)


class _Envolventes:
    """Acumula las 3 envolventes de un archivo que llega por bloques (ver el docstring del
    módulo: segmentos con contexto real a cada lado, para que el filtro de ida y vuelta dé lo
    mismo que sobre el archivo entero)."""

    def __init__(self, sr: int, segmento: int):
        self.sr, self.seg = sr, max(1, int(segmento))
        self.m = max(1, math.ceil(MARGEN_S * sr))
        self.sos_g, self.sos_a = filtros(sr)
        # Contexto izquierdo del primer segmento: lo que hay antes del archivo es silencio.
        self.pend = [np.zeros(self.m, dtype=np.float32)]
        self.n_pend = self.m
        self.pos = 0                 # muestra absoluta del primer frame sin procesar
        self.total = 0
        self.env = np.zeros((3, 1024), dtype=np.float32)

    def agregar(self, bloque: np.ndarray) -> None:
        """`bloque`: (n,) mono, float32."""
        self.total += len(bloque)
        self.pend.append(bloque)
        self.n_pend += len(bloque)
        largo = 2 * self.m + self.seg
        if self.n_pend < largo:
            return
        buf = np.concatenate(self.pend)
        ini = 0
        while len(buf) - ini >= largo:
            self._procesar(buf[ini:ini + largo], self.seg)
            ini += self.seg
        # Se copia el resto: si no, la vista retendría el buffer entero en memoria.
        self.pend = [buf[ini:].copy()]
        self.n_pend = len(self.pend[0])

    def terminar(self) -> np.ndarray:
        """(3, cuadros) float32, en escala de la muestra."""
        # Contexto derecho del último segmento: después del archivo, silencio.
        buf = np.concatenate([*self.pend, np.zeros(self.m, dtype=np.float32)])
        n = len(buf) - 2 * self.m
        if n > 0:
            self._procesar(buf, n)
        cuadros = -(-self.total * TASA_HZ // self.sr)
        if self.env.shape[1] < cuadros:
            self._crecer(cuadros)
        return self.env[:, :cuadros]

    def _crecer(self, minimo: int) -> None:
        nuevo = np.zeros((3, max(minimo, 2 * self.env.shape[1])), dtype=np.float32)
        nuevo[:, :self.env.shape[1]] = self.env
        self.env = nuevo

    def _procesar(self, ext: np.ndarray, n: int) -> None:
        """`ext` = margen + `n` muestras centrales + margen; las centrales son las absolutas
        [pos, pos + n)."""
        from scipy.signal import sosfiltfilt

        x = ext.astype(np.float64)
        c = slice(self.m, self.m + n)
        # padtype=None: el contexto REAL hace de relleno (el `odd` de scipy inventaría audio).
        graves = sosfiltfilt(self.sos_g, x, padtype=None)[c]
        agudos = sosfiltfilt(self.sos_a, x, padtype=None)[c]
        medios = x[c] - graves
        medios -= agudos
        # El cuadro de la muestra i es i·TASA // sr (sr no tiene por qué ser múltiplo de 100):
        # el cuadro k empieza en la muestra ⌈k·sr/TASA⌉.
        k0 = self.pos * TASA_HZ // self.sr
        k1 = (self.pos + n - 1) * TASA_HZ // self.sr
        ks = np.arange(k0 + 1, k1 + 1, dtype=np.int64)
        inicios = np.concatenate(([0], -(-ks * self.sr // TASA_HZ) - self.pos))
        if k1 >= self.env.shape[1]:
            self._crecer(k1 + 1)
        for j, y in enumerate((graves, medios, agudos)):
            red = np.maximum.reduceat(np.abs(y, out=y), inicios)
            np.maximum(self.env[j, k0:k1 + 1], red, out=self.env[j, k0:k1 + 1])
        self.pos += n


def normalizar(env: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`(q uint8 (3, n), referencias (3,))` según el criterio del docstring del módulo."""
    piso = 10 ** (PISO_DBFS / 20)
    p = np.zeros(env.shape[0])
    for i, banda in enumerate(env.astype(np.float64)):
        # El percentil se toma sobre los cuadros CON SONIDO (arriba del piso): en un tema con
        # un solo golpe en un minuto de silencio, el p99 de todos los cuadros es 0 y el
        # pre-ringing y la cola del golpe se dibujarían a tope, como un manchón.
        activos = banda[banda > piso]
        p[i] = np.percentile(activos, PERCENTIL_REFERENCIA) if activos.size else 0.0
    ref = np.maximum(p, max(float(p.max()) * 10 ** (-RANGO_MAX_DB / 20), piso))
    q = np.round(np.minimum(env / ref[:, None], 1.0) * 255.0).astype(np.uint8)
    return q, ref


def envolventes(path: Path | str, block_frames: int = BLOCK_FRAMES,
                segmento: int = SEGMENTO_FRAMES) -> tuple[np.ndarray, int, int, int]:
    """`(env (3, cuadros) float32 en escala de la muestra, sr, canales, frames)`: las
    envolventes SIN normalizar, leyendo el archivo por bloques. `UnreadableAudio` si no se
    puede decodificar; `FileNotFoundError` si no existe."""
    ruta = os.fspath(path)
    if not os.path.isfile(ruta):
        raise FileNotFoundError(ruta)
    f, sr, ch, bloques = _abrir(ruta, block_frames)
    with f:                          # solo lectura: el original nunca se toca
        acc = _Envolventes(sr, segmento)
        for b in bloques:
            acc.agregar(mezcla_mono(b))
    if acc.total == 0:
        raise UnreadableAudio("el archivo no tiene audio")
    env = acc.terminar()
    # Un WAV en float puede traer NaN o infinitos: no son audio (mismo criterio que la onda).
    if not np.all(np.isfinite(env)):
        raise UnreadableAudio("el archivo tiene muestras inválidas (NaN o infinitas)")
    return env, sr, ch, acc.total


def compute_bandas(path: Path | str, block_frames: int = BLOCK_FRAMES,
                   segmento: int = SEGMENTO_FRAMES) -> Bandas:
    """Las 3 bandas del archivo, normalizadas y cuantizadas (ver el docstring del módulo)."""
    env, sr, ch, total = envolventes(path, block_frames, segmento)
    q, ref = normalizar(env)
    return Bandas(q, tuple(float(r) for r in ref), total / sr, sr, ch)


# --- caché ------------------------------------------------------------------------------
#
# Formato: `DJB3` + largo del encabezado (uint32) + encabezado JSON + zlib(q.tobytes()).
# Cualquier cosa que no sea exactamente eso se recalcula: ni un 500 ni una onda inventada.

def _leer_cache(archivo: Path, firma: dict) -> Bandas | None:
    try:
        datos = archivo.read_bytes()
    except OSError:
        return None
    try:
        if datos[:4] != _MAGIA:
            return None
        (largo,) = struct.unpack("<I", datos[4:8])
        enc = json.loads(datos[8:8 + largo].decode("utf-8"))
        if not isinstance(enc, dict) or {k: enc.get(k) for k in firma} != firma:
            return None
        cuadros, dur = enc.get("cuadros"), enc.get("duration_s")
        sr, ch, refs = enc.get("sample_rate"), enc.get("channels"), enc.get("referencias")
        if not (type(cuadros) is int and cuadros > 0 and type(sr) is int and sr > 0
                and type(ch) is int and ch > 0):
            return None
        if not (type(dur) in (int, float) and math.isfinite(dur) and dur > 0
                and abs(dur * TASA_HZ - cuadros) < 1.0 + 1e-6):
            return None
        if not (isinstance(refs, list) and len(refs) == 3
                and all(type(r) in (int, float) and math.isfinite(r) and r > 0 for r in refs)):
            return None
        crudo = zlib.decompress(datos[8 + largo:])
    except (ValueError, struct.error, zlib.error, UnicodeDecodeError):
        return None
    if len(crudo) != 3 * cuadros:
        return None
    q = np.frombuffer(crudo, dtype=np.uint8).reshape(3, cuadros).copy()
    return Bandas(q, tuple(float(r) for r in refs), float(dur), sr, ch)


def _escribir_cache(archivo: Path, firma: dict, b: Bandas) -> None:
    enc = {**firma, "cuadros": b.cuadros, "duration_s": b.duration_s,
           "sample_rate": b.sample_rate, "channels": b.channels,
           "referencias": list(b.referencias)}
    cab = json.dumps(enc).encode("utf-8")
    datos = _MAGIA + struct.pack("<I", len(cab)) + cab + zlib.compress(b.q.tobytes(), 6)
    # Escritura atómica: un lector a la vez nunca ve un archivo a medio escribir.
    fd, tmp = tempfile.mkstemp(dir=archivo.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(datos)
        os.replace(tmp, archivo)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def cached_bandas(path: Path | str, cache_dir: Path | str,
                  tope: int | None = None) -> tuple[Bandas, bool]:
    """`(bandas, salió_de_la_caché)`. Recalcula si no hay caché, si el archivo cambió (tamaño o
    mtime), si la caché es de otra versión del cálculo o si no tiene la forma esperada.
    `tope`: cuántos archivos de bandas guarda la caché como mucho (default el de los picos)."""
    ruta = os.path.abspath(os.fspath(path))
    clave = os.path.normcase(ruta)
    st = os.stat(ruta)                                   # FileNotFoundError si no está
    if not os.path.isfile(ruta):
        raise FileNotFoundError(ruta)                    # un directorio no es un track
    firma = {"version": BANDAS_VERSION, "path_key": clave, "size": st.st_size,
             "mtime_ns": st.st_mtime_ns, "tasa_hz": TASA_HZ}
    cache_dir = Path(cache_dir)
    archivo = _archivo_cache(cache_dir, clave).with_suffix(CACHE_SUFIJO)
    res = _leer_cache(archivo, firma)
    if res is not None:
        with contextlib.suppress(OSError):
            os.utime(archivo)                            # usada: la poda la deja para el final
        return res, True
    res = compute_bandas(ruta)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _escribir_cache(archivo, firma, res)
    _podar(cache_dir, CACHE_MAX_ARCHIVOS if tope is None else tope, patron=f"*{CACHE_SUFIJO}")
    return res, False


# --- tramos -------------------------------------------------------------------------------

def reducir_max(x: np.ndarray, puntos: int) -> np.ndarray:
    """Reduce la última dimensión a `puntos` tomando el MÁXIMO de cada grupo (no el promedio:
    un kick no puede desaparecer al achicar). El punto i junta los cuadros
    [⌊i·m/puntos⌋, ⌊(i+1)·m/puntos⌋). Si hay menos cuadros que puntos se devuelven los cuadros
    tal cual: no se inventa resolución."""
    m = x.shape[-1]
    if m <= puntos:
        return x.copy()
    inicios = (np.arange(puntos, dtype=np.int64) * m) // puntos
    return np.maximum.reduceat(x, inicios, axis=-1)


def tramo(b: Bandas, desde: float, hasta: float, puntos: int) -> tuple[float, float, np.ndarray]:
    """`(desde_real, hasta_real, q (3, ≤puntos))` del tramo [desde, hasta] en segundos. Los
    bordes reales son los de los cuadros de 10 ms que cubren el pedido (el primero contiene a
    `desde`), y `hasta_real` nunca pasa del final del audio. El que llama valida el rango."""
    k0 = min(max(0, math.floor(desde * TASA_HZ + 1e-9)), b.cuadros - 1)
    k1 = min(max(k0 + 1, math.ceil(hasta * TASA_HZ - 1e-9)), b.cuadros)
    return k0 / TASA_HZ, min(k1 / TASA_HZ, b.duration_s), reducir_max(b.q[:, k0:k1], puntos)


# --- grilla -------------------------------------------------------------------------------

PASO_FASE_S = 0.001              # barrido de la fase: 1 ms
VENTANA_BPM = 0.5                # el BPM de la grilla se busca en ± esto alrededor del de la base
PASO_BPM_GRUESO = 0.01
PASO_BPM_FINO = 0.001
TOLERANCIA_AFINADO = 0.02        # el BPM de la base se mantiene si calza a menos de un 2 %
CONFIANZA_MIN = 0.3              # debajo, no se dibuja grilla (ver `estimar_grilla`)
VENTANA_LINEA_S = 0.025          # una subida a ±25 ms de una línea «cae en la grilla»
GRILLA_MAX_S = 20 * 60           # más largo que esto es un mix: no tiene un BPM fijo
BEATS_MIN = 8
BEATS_POR_COMPAS = 4
CONFIANZA_COMPAS_MIN = 0.5
EVENTO_COMPAS_MIN = 0.05         # una subida de energía por beat menor que esto es ruido
EVENTOS_COMPAS_MIN = 2


def _subidas(graves: np.ndarray, tasa_hz: int) -> tuple[np.ndarray, np.ndarray]:
    """`(instantes, pesos)` de las subidas de la envolvente de graves: cuánto creció del cuadro
    k−1 al k, ubicado en el borde entre los dos (k / tasa). Solo las positivas."""
    g = np.asarray(graves, dtype=np.float64)
    d = np.diff(g, prepend=g[:1] if g.size else g)
    k = np.flatnonzero(d > 0)
    return k / tasa_hz, d[k]


def _curva_fase(t: np.ndarray, w: np.ndarray, periodo: float, beats: float,
                tasa_hz: int) -> np.ndarray:
    """La subida de graves PROMEDIO por beat en cada fase de una grilla de `periodo`, con la
    fase barrida de a ~1 ms. Es lo mismo que interpolar linealmente la subida en las posiciones
    φ, φ+P, φ+2P… y promediar: cada subida se reparte entre los dos casilleros de fase vecinos
    y después se convoluciona (circular) con un triángulo de un cuadro de semiancho."""
    nf = max(1, round(periodo / PASO_FASE_S))
    ancho = periodo / nf
    ph = (t % periodo) / ancho
    i0 = np.floor(ph).astype(np.int64)
    fr = ph - i0
    h = (np.bincount(i0 % nf, w * (1 - fr), nf) + np.bincount((i0 + 1) % nf, w * fr, nf))
    semi = max(1, round(1.0 / tasa_hz / ancho))
    tri = 1.0 - np.abs(np.arange(-semi + 1, semi)) / semi
    ext = np.concatenate([h[-(semi - 1):] if semi > 1 else h[:0], h, h[:semi - 1]])
    return np.convolve(ext, tri, mode="valid") / max(beats, 1.0)


def estimar_grilla(graves: np.ndarray, bpm: float, tasa_hz: int = TASA_HZ) -> dict:
    """La grilla de beats que mejor calza con los kicks: `{bpm, bpm_base, periodo_s,
    primer_beat_s, confianza, motivo}`.

    - La fase: la que maximiza la SUBIDA de energía de graves en las posiciones de una grilla
      de período 60/bpm (barrido de 1 ms). Subida y no energía: un beat es donde el kick ATACA;
      la energía del kick llega a su máximo unos ms después (en el kick sintético, 4,5 ms) y
      la grilla quedaría corrida hacia la cola. Medido en f52: con energía el error era de +0,5
      a +7,9 ms; con la subida, de −7,1 a 0 ms.
    - El BPM: el de la base del motor se afina en ±`VENTANA_BPM` (de a 0,01 y después de a
      0,001). Hace falta: el motor mide con un error p95 de ~0,12 BPM y con 0,1 BPM de error
      una grilla fija se corre 280 ms en 6 minutos. Afinarlo no cambia el BPM del track: dice
      con qué período calza la grilla (`bpm`) al lado del medido (`bpm_base`). Si el de la base
      calza casi igual (a menos de `TOLERANCIA_AFINADO`), queda el de la base.
    - La confianza: qué tan marcado es el máximo contra el resto, el producto de dos cosas en
      0..1. (1) El margen contra la MEJOR fase alternativa (a más de un octavo de beat):
      1 − alternativa / máximo; un bajo a contratiempo tan fuerte como el kick lo deja en ~0.
      (2) La concentración: qué parte de TODA la subida de graves del tema cae a
      ±`VENTANA_LINEA_S` de una línea. Hace falta la segunda: con un BPM que no calza, los
      kicks se corren a lo largo del tema y, si el corrimiento total es apenas más de un beat,
      el pedazo que se pisa dos veces arma un máximo «marcado» (medido: margen 0,49 con la
      grilla corriéndose medio segundo en 6 min); la concentración ahí es baja. Un kick limpio
      da ~1; ruido, silencio o un pad dan ~0. Debajo de `CONFIANZA_MIN`, `primer_beat_s` va
      None: la pantalla no dibuja una grilla que miente.

    `primer_beat_s` es la primera línea de la grilla en o después del 0 (la fase, en [0, P))."""
    res = {"bpm": None, "bpm_base": None, "periodo_s": None, "primer_beat_s": None,
           "confianza": 0.0, "motivo": None}
    if bpm is None or not math.isfinite(bpm) or bpm <= 0:
        res["motivo"] = "el track no tiene BPM medido"
        return res
    res["bpm_base"] = res["bpm"] = float(bpm)
    res["periodo_s"] = 60.0 / bpm
    dur = len(graves) / tasa_hz
    if dur > GRILLA_MAX_S:
        res["motivo"] = (f"dura más de {GRILLA_MAX_S // 60} min: un mix no tiene un BPM fijo "
                         f"y una grilla fija mentiría")
        return res
    if dur < BEATS_MIN * 60.0 / bpm:
        res["motivo"] = f"muy corto: menos de {BEATS_MIN} beats"
        return res
    t, w = _subidas(graves, tasa_hz)
    if not w.size:
        res["motivo"] = "no hay golpes en los graves"
        return res

    def mejor(cands):
        best = None
        for b in cands:
            if b <= 0:
                continue
            p = 60.0 / b
            curva = _curva_fase(t, w, p, dur / p, tasa_hz)
            i = int(np.argmax(curva))
            if best is None or curva[i] > best[0]:
                best = (float(curva[i]), float(b), p, curva, i)
        return best

    gruesos = bpm + np.arange(-VENTANA_BPM, VENTANA_BPM + 1e-9, PASO_BPM_GRUESO)
    _, b0, *_ = mejor(gruesos)
    finos = b0 + np.arange(-PASO_BPM_GRUESO, PASO_BPM_GRUESO + 1e-9, PASO_BPM_FINO)
    smax, b1, p, curva, i = mejor(finos)
    # El de la base gana salvo que otro calce CLARAMENTE mejor: en un tema corto la diferencia
    # entre 128,000 y 127,987 no se puede medir con cuadros de 10 ms, y cambiarlo sería ruido.
    base = mejor([bpm])
    if base is not None and base[0] >= (1 - TOLERANCIA_AFINADO) * smax:
        smax, b1, p, curva, i = base
    if smax <= 0:
        res["motivo"] = "no hay golpes en los graves"
        return res
    nf = curva.size
    fase = (i * p / nf) % p
    dist = np.abs((np.arange(nf) - i + nf / 2) % nf - nf / 2) * (p / nf)
    resto = curva[dist > p / 8]
    alternativa = float(resto.max()) if resto.size else 0.0
    margen = max(0.0, 1.0 - alternativa / smax)
    lejos = np.abs((t - fase + p / 2) % p - p / 2) > VENTANA_LINEA_S
    concentracion = 1.0 - float(w[lejos].sum() / w.sum())
    conf = margen * concentracion
    res["confianza"] = round(conf, 3)
    if conf < CONFIANZA_MIN:
        res["motivo"] = ("la fase del beat no se distingue: los golpes de graves no caen "
                         "marcados en cada beat (ruido, sin kick, un bajo a contratiempo tan "
                         "fuerte como el kick, o un BPM que no calza en todo el tema)")
        return res
    res["bpm"] = b1
    res["periodo_s"] = p
    res["primer_beat_s"] = fase
    return res


def estimar_compas(valores: np.ndarray, primer_beat_s: float, periodo_s: float,
                   tasa_hz: int = TASA_HZ) -> dict:
    """¿Cuál de cada 4 beats es el 1 del compás? `{compas_ref, compas_confianza}`.

    En techno los 4 kicks de un compás son iguales: el kick solo no dice cuál es el 1. Lo dicen
    los CAMBIOS de arreglo (entra el hat, vuelve el kick después del break, entra un stab), que
    caen en el 1. Por beat j se mide la energía media de cada banda en su tramo (ver abajo) y
    la subida respecto del beat anterior (sumada sobre las bandas); a esa subida se le resta la
    del MISMO beat del compás anterior: un patrón que se repite compás a compás (un clap en el
    2 y el 4, un acento del bajo en el 3) se cancela, y queda lo NUEVO. Cada beat con algo nuevo
    (al menos `EVENTO_COMPAS_MIN`) vota por su posición (j mod 4) con lo que subió.

    `compas_ref` (el instante del primer 1, en [primer_beat, primer_beat + 3P]) va None si no
    hay al menos `EVENTOS_COMPAS_MIN` cambios, o si la posición ganadora no le saca a la segunda
    al menos `CONFIANZA_COMPAS_MIN` del total de votos. No se inventa: un loop que no cambia
    nunca no tiene compás que decir. Es una heurística probada con arreglos sintéticos, no
    contra un ground truth de downbeats reales (el repo no tiene uno): por eso el umbral es
    alto y ante la duda va None."""
    res = {"compas_ref": None, "compas_confianza": 0.0,
           "compas_motivo": "no se puede saber cuál beat es el 1: no hay cambios de arreglo "
                            "que caigan siempre en el mismo beat del compás"}
    n = valores.shape[1]
    nb = int((n / tasa_hz - primer_beat_s) // periodo_s)       # beats enteros dentro del audio
    if nb < 2 * BEATS_POR_COMPAS + 2:
        return res
    # El tramo de cada beat arranca un octavo de beat ANTES de la línea: el kick cae a ±5 ms de
    # la línea y, con el borde justo en la línea, unos beats lo agarraban y otros no (la
    # energía saltaba un 20 % de beat a beat sin que la música cambie). Lo que va en las
    # semicorcheas del beat queda adentro de su tramo.
    bordes = np.ceil((primer_beat_s - periodo_s / 8 + periodo_s * np.arange(nb + 1)) * tasa_hz
                     - 1e-9).astype(np.int64)
    bordes = np.clip(bordes, 0, n)
    acum = np.concatenate([np.zeros((valores.shape[0], 1)), np.cumsum(valores, axis=1)], axis=1)
    largo = np.maximum(bordes[1:] - bordes[:-1], 1)
    energia = (acum[:, bordes[1:]] - acum[:, bordes[:-1]]) / largo        # (bandas, nb)
    sube = np.zeros(nb)
    sube[1:] = np.maximum(0.0, np.diff(energia, axis=1)).sum(axis=0)
    nuevo = np.zeros(nb)
    nuevo[BEATS_POR_COMPAS:] = np.maximum(0.0, sube[BEATS_POR_COMPAS:] - sube[:-BEATS_POR_COMPAS])
    nuevo[:BEATS_POR_COMPAS + 1] = 0.0        # sin un compás anterior completo no hay con qué comparar
    eventos = np.flatnonzero(nuevo >= EVENTO_COMPAS_MIN)
    if eventos.size < EVENTOS_COMPAS_MIN:
        return res
    votos = np.bincount(eventos % BEATS_POR_COMPAS, nuevo[eventos], BEATS_POR_COMPAS)
    orden = np.argsort(votos)[::-1]
    total = float(votos.sum())
    conf = (float(votos[orden[0]]) - float(votos[orden[1]])) / total if total > 0 else 0.0
    res["compas_confianza"] = round(conf, 3)
    if conf >= CONFIANZA_COMPAS_MIN:
        res["compas_ref"] = primer_beat_s + int(orden[0]) * periodo_s
        res["compas_motivo"] = None
    return res


def _redondo(x: float | None, decimales: int) -> float | None:
    return None if x is None else round(float(x), decimales)


def grilla(b: Bandas, bpm: float | None) -> dict:
    """La grilla estimada completa, lista para JSON (floats de Python, tiempos en ms). Ver
    `estimar_grilla` y `estimar_compas`. `estimada: True` siempre: más adelante una grilla
    importada de Rekordbox (TEMPO) la reemplaza."""
    v = b.valores()
    g = estimar_grilla(v[0], bpm if bpm is not None else float("nan"))
    out = {"estimada": True, "bpm": _redondo(g["bpm"], 3), "bpm_base": _redondo(g["bpm_base"], 3),
           # El período va con 6 decimales: con 3, en 6 minutos la grilla se correría ~0,4 s.
           "periodo_s": _redondo(g["periodo_s"], 6),
           "primer_beat_s": _redondo(g["primer_beat_s"], 3), "confianza": g["confianza"],
           "motivo": g["motivo"], "beats_por_compas": BEATS_POR_COMPAS, "compas_ref": None,
           "compas_confianza": 0.0, "compas_motivo": None}
    if g["primer_beat_s"] is not None:
        c = estimar_compas(v, g["primer_beat_s"], g["periodo_s"])
        out.update(compas_ref=_redondo(c["compas_ref"], 3), compas_confianza=c["compas_confianza"],
                   compas_motivo=c["compas_motivo"])
    return out


def normalizacion(b: Bandas) -> dict:
    """Cómo se normalizaron las bandas de este track, para que la pantalla no lo venda como
    volumen absoluto (ver el docstring del módulo)."""
    return {"criterio": f"cada banda dividida por el percentil {PERCENTIL_REFERENCIA:g} de su "
                        f"envolvente en este track (1 = sus golpes más fuertes): relativa dentro "
                        f"del track, no volumen absoluto ni comparable entre bandas o temas",
            "relativa_al_track": True, "percentil": PERCENTIL_REFERENCIA,
            "rango_max_db": RANGO_MAX_DB, "piso_dbfs": PISO_DBFS,
            # La amplitud (escala de la muestra, 1 = fondo de escala) que vale 1 en cada banda.
            "referencias": {n: round(r, 6) for n, r in zip(BANDAS, b.referencias, strict=True)},
            "cortes_hz": {"graves_hasta": CORTE_GRAVES_HZ, "agudos_desde": CORTE_AGUDOS_HZ}}
