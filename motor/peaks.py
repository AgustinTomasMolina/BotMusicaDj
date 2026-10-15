"""Forma de onda de un track para el editor de cues (f48): los picos del audio REAL.

Un pico por bin: el máximo de |muestra| en ese tramo del archivo, en escala de la muestra
(0..1, 1 = fondo de escala), sobre todos los canales. No se normaliza al máximo del tema: un
master bajo se ve bajo, que es lo que es (§6).

Memoria: el archivo se lee POR BLOQUES (`BLOCK_FRAMES` frames, float32) y de cada bloque solo
queda su aporte a los `bins` picos. Un tema de 10 minutos en estéreo a 44.1 kHz cargado entero
en float64 son ~420 MB; acá el pico es del orden del bloque (~1 MB), sin importar lo que dure.
Medido: ver el informe de la rama f48 y `motor/tests/test_peaks.py`.

Decodificación: `soundfile` (libsndfile: WAV, AIFF, FLAC, OGG y MP3) en streaming; si no lo
abre (M4A/AAC), ffmpeg en streaming (`motor.decodificar`, el mismo camino que usa el análisis:
lo que el motor pudo analizar, acá se puede dibujar). El archivo se abre SOLO para leer: el
original nunca se toca.

Caché: un JSON por track en la carpeta que se pase (el server usa `<MUSIFLIX_DATA_DIR>/peaks`),
con la ruta, el tamaño y el mtime del archivo. Si alguno cambió, se recalcula; si no, se lee.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PEAK_BINS = 1000
BLOCK_FRAMES = 65536
# Subirla si cambia CÓMO se calcula un pico: invalida todas las cachés viejas.
PEAKS_VERSION = 1


class UnreadableAudio(Exception):
    """El archivo existe pero no se pudo decodificar (formato sin backend, archivo roto)."""


@dataclass(frozen=True)
class Peaks:
    # (bins,), 0..1. `compute_peaks` da float32 (lo que leyó); `cached_peaks` da float64 con
    # 5 decimales, los MISMOS valores la primera vez y desde la caché (un float32 de 0.30875
    # escrito en JSON sería 0.30875000357627869).
    peaks: np.ndarray
    duration_s: float          # duración del audio DECODIFICADO (frames leídos / sr)
    sample_rate: int
    channels: int


def _acumular(out: np.ndarray, amp: np.ndarray, pos: int, total: int) -> None:
    """Suma al máximo de cada bin el aporte de un bloque que empieza en el frame `pos`.

    El bin del frame `i` es `i * bins // total`: los índices de un bloque son crecientes, así
    que `reduceat` saca el máximo de cada tramo sin un bucle de Python por muestra. Un frame
    más allá de `total` (un MP3 que declara de menos) cae en el último bin."""
    bins = out.size
    idx = (np.arange(pos, pos + amp.size, dtype=np.int64) * bins) // total
    np.minimum(idx, bins - 1, out=idx)
    uniq, starts = np.unique(idx, return_index=True)
    out[uniq] = np.maximum(out[uniq], np.maximum.reduceat(amp, starts))


def _declara_otro_largo(leidos: int, declarados: int) -> bool:
    """¿El decodificador entregó una cantidad de frames distinta de la que declaró? Pasa con
    ffmpeg (MP3/M4A: la duración es una estimación). Los bins se reparten sobre
    lo DECLARADO: si falta un pedazo, la cola
    de la onda quedaría en cero y se dibujaría como silencio un audio que no existe. Con más
    de un 0.1 % de diferencia se vuelve a leer repartiendo sobre lo que de verdad hay."""
    return leidos > 0 and abs(leidos - declarados) > max(1, declarados // 1000)


def _con_soundfile(path: str, bins: int, block_frames: int) -> Peaks | None:
    import soundfile as sf

    try:
        f = sf.SoundFile(path, mode="r")
    except (RuntimeError, TypeError, ValueError):     # LibsndfileError es un RuntimeError
        return None
    with f:
        total, sr, ch = int(f.frames), int(f.samplerate), int(f.channels)
        if total <= 0 or sr <= 0 or ch <= 0:
            raise UnreadableAudio("el archivo no tiene audio")
        out = np.zeros(bins, dtype=np.float32)
        pos = 0
        # libsndfile abre bien un FLAC truncado o con basura en el medio y falla RECIÉN al
        # decodificar ese tramo (LibsndfileError, un RuntimeError): sin este try era un 500.
        # El try envuelve SOLO la lectura del decodificador: un error del cálculo de acá
        # (`_acumular`) es un bug propio y no se puede contar como "el archivo está dañado".
        bloques = f.blocks(blocksize=block_frames, dtype="float32", always_2d=True)
        while True:
            try:
                bloque = next(bloques)
            except StopIteration:
                break
            except (RuntimeError, ValueError) as e:
                raise UnreadableAudio(f"el archivo se corta o está dañado a los {pos / sr:.1f} s "
                                      f"({type(e).__name__}: {e})") from None
            if not len(bloque):
                break
            _acumular(out, np.abs(bloque).max(axis=1), pos, total)
            pos += len(bloque)
    if pos == 0:
        raise UnreadableAudio("el archivo no tiene audio")
    # Acá no hace falta releer como en `_con_ffmpeg`: libsndfile corrige solo el largo de
    # un WAV truncado y, si un FLAC declara de más, falla al leer (UnreadableAudio, arriba).
    return Peaks(out, pos / sr, sr, ch)


def _con_ffmpeg(path: str, bins: int, total_real: int | None = None) -> Peaks:
    from motor.decodificar import LectorPCM

    def ilegible(e: Exception) -> UnreadableAudio:
        return UnreadableAudio(f"no se pudo decodificar el audio ({type(e).__name__}: {e})")

    # Como en `_con_soundfile`, los try envuelven SOLO lo que hace el decodificador (abrir con
    # ffprobe/ffmpeg, leer lo que declara, entregar el próximo pedazo; `LectorPCM` tira
    # `DecodificacionError` al abrir y al iterar): un error del cálculo propio no es "audio que
    # no se pudo decodificar".
    try:
        f = LectorPCM(path)
    except Exception as e:  # noqa: BLE001 — sin ffmpeg, pista rota o lo que tire el pipe
        raise ilegible(e) from None
    with f:
        try:
            sr, ch = int(f.samplerate), int(f.channels)
            # Sin duración declarada (ffprobe no la informa) el total es provisorio (1): lo
            # leído no coincide, y `_declara_otro_largo` manda a releer repartiendo sobre el
            # largo real (abajo). El primer pase solo sirve para medirlo.
            total = total_real or (int(round(float(f.duration) * sr)) if f.duration else 1)
            pedazos = iter(f)        # PCM 16 bit little-endian, intercalado, de a pedazos
        except Exception as e:  # noqa: BLE001
            raise ilegible(e) from None
        if total <= 0 or sr <= 0 or ch <= 0:
            raise UnreadableAudio("el archivo no tiene audio")
        out = np.zeros(bins, dtype=np.float32)
        pos = 0
        resto = b""
        while True:
            try:
                buf = next(pedazos)
            except StopIteration:
                break
            except Exception as e:  # noqa: BLE001 — DecodificacionError, o lo que tire el pipe
                raise ilegible(e) from None
            datos = resto + buf
            util = len(datos) - len(datos) % (2 * ch)
            resto = datos[util:]
            if not util:
                continue
            muestras = np.frombuffer(datos[:util], dtype="<i2").reshape(-1, ch)
            amp = np.abs(muestras.astype(np.float32)).max(axis=1) / 32768.0
            _acumular(out, amp, pos, total)
            pos += len(amp)
    if pos == 0:
        raise UnreadableAudio("el archivo no tiene audio")
    if total_real is None and _declara_otro_largo(pos, total):
        return _con_ffmpeg(path, bins, total_real=pos)
    return Peaks(out, pos / sr, sr, ch)


def compute_peaks(path: Path | str, bins: int = PEAK_BINS,
                  block_frames: int = BLOCK_FRAMES) -> Peaks:
    """Los `bins` picos del archivo, leyéndolo por bloques. `UnreadableAudio` si no se puede
    decodificar; `FileNotFoundError` si no existe."""
    ruta = os.fspath(path)
    if not os.path.isfile(ruta):
        raise FileNotFoundError(ruta)
    res = _con_soundfile(ruta, bins, block_frames)
    res = res if res is not None else _con_ffmpeg(ruta, bins)
    # Un WAV en float puede traer NaN o infinitos: no son audio, y en el JSON de la API
    # serían un 500 (NaN no es JSON válido). Se dice que el archivo está dañado.
    if not np.all(np.isfinite(res.peaks)):
        raise UnreadableAudio("el archivo tiene muestras inválidas (NaN o infinitas)")
    return res


# Tope de la caché: ~9 KB por track, así que 5000 archivos son ~45 MB. Pasado el tope se
# borran los MENOS USADOS (mtime más viejo; leer de la caché le actualiza el mtime) hasta
# quedar en el 90 %. Una biblioteca de 10k tracks que se editan todos recalcula los viejos,
# que cuesta ~1.5 s por tema (medido en f48).
CACHE_MAX_ARCHIVOS = 5000


def _archivo_cache(cache_dir: Path, clave: str) -> Path:
    return cache_dir / f"{hashlib.sha256(clave.encode('utf-8')).hexdigest()[:32]}.json"


def _tope_bytes(bins: int, clave: str) -> int:
    """El tamaño más grande que puede tener una caché escrita por `cached_peaks`, con margen:
    `json.dump` escapa lo que no es ASCII (hasta 12 bytes por carácter de la ruta, un par
    sustituto), un pico redondeado ocupa como mucho ~24 bytes con su ", " (3.4028234663852886e+38)
    y el resto de los campos no llega a 300. Un archivo más grande no lo escribimos nosotros."""
    return 2048 + 32 * bins + 12 * len(clave)


def _leer_cache(archivo: Path, tope_bytes: int) -> object:
    """El JSON de la caché, o None si no está, no se puede leer o no es un JSON razonable. Se
    lee como mucho `tope_bytes` + 1: un archivo enorme no se carga entero en memoria. Un JSON
    de miles de niveles (`[[[[...`) hace que `json.loads` tire RecursionError, que no es un
    ValueError: sin capturarlo subía hasta la API."""
    try:
        with open(archivo, "rb") as f:
            crudo = f.read(tope_bytes + 1)
        if len(crudo) > tope_bytes:
            return None
        return json.loads(crudo.decode("utf-8"))
    except (OSError, ValueError, RecursionError):        # UnicodeDecodeError es ValueError
        return None


def _desde_cache(guardado: object, firma: dict, bins: int) -> Peaks | None:
    """Los picos de una caché VÁLIDA, o None (y se rehace). Se valida la forma entera: un JSON
    que parsea pero no es lo que se escribió (null, [], picos que no son números finitos,
    listas anidadas) no puede terminar en un 500 ni, peor, en una onda inventada. Tampoco en
    un 422: un entero de 400 dígitos es un JSON válido y `math.isfinite` sobre él tira
    OverflowError; eso es una caché inválida, no un audio ilegible."""
    try:
        return _validar_cache(guardado, firma, bins)
    except (OverflowError, RecursionError):
        return None


def _validar_cache(guardado: object, firma: dict, bins: int) -> Peaks | None:
    if not isinstance(guardado, dict) or {k: guardado.get(k) for k in firma} != firma:
        return None
    picos, dur = guardado.get("peaks"), guardado.get("duration_s")
    sr, ch = guardado.get("sample_rate"), guardado.get("channels")
    if not (isinstance(picos, list) and len(picos) == bins
            and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in picos)):
        return None
    if not (type(dur) in (int, float) and math.isfinite(dur) and dur > 0):
        return None
    if not (type(sr) is int and sr > 0 and type(ch) is int and ch > 0):
        return None
    return Peaks(np.asarray(picos, dtype=np.float64), float(dur), sr, ch)


def _podar(cache_dir: Path, tope: int, patron: str = "*.json") -> None:
    """Si hay más de `tope` archivos `patron`, borra los de mtime más viejo hasta quedar en el
    90 %. Nunca falla hacia afuera: es limpieza, no puede tirar abajo un pedido. `patron` deja
    que las bandas de f52 (`motor/bandas.py`) poden lo suyo en la misma carpeta sin tocar los
    picos, y al revés."""
    try:
        archivos = [(p.stat().st_mtime_ns, p) for p in cache_dir.glob(patron)]
        if len(archivos) <= tope:
            return
        archivos.sort()
        for _, p in archivos[: len(archivos) - int(tope * 0.9)]:
            p.unlink(missing_ok=True)
    except OSError:
        pass


# En Windows, `os.replace` sobre un JSON que otro hilo tiene abierto para leer falla con
# PermissionError (el lector no comparte el borrado). La lectura dura nada: se reintenta un
# par de veces con una espera corta antes de rendirse.
REPLACE_INTENTOS = 3
REPLACE_ESPERA_S = 0.05


def _escribir_cache(cache_dir: Path, archivo: Path, datos: dict) -> bool:
    """Escribe la caché de forma atómica (un lector nunca ve un JSON a medio escribir). Devuelve
    si quedó escrita. NUNCA falla hacia afuera por un OSError: los picos ya están calculados y
    no guardarlos solo cuesta recalcularlos la próxima vez; tirar el pedido por eso, no. El
    `.tmp` no queda tirado en ningún caso."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        for intento in range(1, REPLACE_INTENTOS + 1):
            try:
                os.replace(tmp, archivo)
                return True
            except PermissionError:
                if intento == REPLACE_INTENTOS:
                    raise
                time.sleep(REPLACE_ESPERA_S)
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            Path(tmp).unlink(missing_ok=True)            # tras un replace exitoso ya no está
    return False                                         # pragma: no cover — el for siempre sale


def cached_peaks(path: Path | str, cache_dir: Path | str, bins: int = PEAK_BINS,
                 tope: int | None = None) -> tuple[Peaks, bool]:
    """`(picos, salió_de_la_caché)`. Recalcula si no hay caché, si el archivo cambió (tamaño
    o mtime), si la caché es de otra versión del cálculo o si no tiene la forma esperada.
    `tope`: cuántos archivos guarda la caché como mucho (default `CACHE_MAX_ARCHIVOS`)."""
    ruta = os.path.abspath(os.fspath(path))
    clave = os.path.normcase(ruta)
    st = os.stat(ruta)                                   # FileNotFoundError si no está
    if not os.path.isfile(ruta):
        raise FileNotFoundError(ruta)                    # un directorio no es un track
    firma = {"version": PEAKS_VERSION, "path_key": clave, "size": st.st_size,
             "mtime_ns": st.st_mtime_ns, "bins": bins}
    cache_dir = Path(cache_dir)
    archivo = _archivo_cache(cache_dir, clave)
    # Sin caché, ilegible o con otra forma: se rehace (y se reescribe).
    res = _desde_cache(_leer_cache(archivo, _tope_bytes(bins, clave)), firma, bins)
    if res is not None:
        with contextlib.suppress(OSError):
            os.utime(archivo)                            # usada: la poda la deja para el final
        return res, True

    res = compute_peaks(ruta, bins)
    datos = {**firma, "duration_s": res.duration_s, "sample_rate": res.sample_rate,
             "channels": res.channels,
             "peaks": [round(float(v), 5) for v in res.peaks]}
    if _escribir_cache(cache_dir, archivo, datos):
        _podar(cache_dir, CACHE_MAX_ARCHIVOS if tope is None else tope)
    # Los mismos valores que va a devolver la caché: la primera respuesta y las siguientes
    # tienen que ser idénticas.
    return Peaks(np.asarray(datos["peaks"], dtype=np.float64), res.duration_s,
                 res.sample_rate, res.channels), False
