"""Forma de onda de un track para el editor de cues (f48): los picos del audio REAL.

Un pico por bin: el máximo de |muestra| en ese tramo del archivo, en escala de la muestra
(0..1, 1 = fondo de escala), sobre todos los canales. No se normaliza al máximo del tema: un
master bajo se ve bajo, que es lo que es (§6).

Memoria: el archivo se lee POR BLOQUES (`BLOCK_FRAMES` frames, float32) y de cada bloque solo
queda su aporte a los `bins` picos. Un tema de 10 minutos en estéreo a 44.1 kHz cargado entero
en float64 son ~420 MB; acá el pico es del orden del bloque (~1 MB), sin importar lo que dure.
Medido: ver el informe de la rama f48 y `motor/tests/test_peaks.py`.

Decodificación: `soundfile` (libsndfile: WAV, AIFF, FLAC, OGG y MP3) en streaming; si no lo
abre, `audioread` en streaming (los mismos backends que usa `librosa.load` para el análisis:
lo que el motor pudo analizar, acá se puede dibujar). El archivo se abre SOLO para leer: el
original nunca se toca.

Caché: un JSON por track en la carpeta que se pase (el server usa `<MUSIFLIX_DATA_DIR>/peaks`),
con la ruta, el tamaño y el mtime del archivo. Si alguno cambió, se recalcula; si no, se lee.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
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
    audioread (MP3/M4A vía ffmpeg: la duración es una estimación). Los bins se reparten sobre
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
        try:
            for bloque in f.blocks(blocksize=block_frames, dtype="float32", always_2d=True):
                if not len(bloque):
                    break
                _acumular(out, np.abs(bloque).max(axis=1), pos, total)
                pos += len(bloque)
        except (RuntimeError, ValueError) as e:
            raise UnreadableAudio(f"el archivo se corta o está dañado a los {pos / sr:.1f} s "
                                  f"({type(e).__name__}: {e})") from None
    if pos == 0:
        raise UnreadableAudio("el archivo no tiene audio")
    # Acá no hace falta releer como en `_con_audioread`: libsndfile corrige solo el largo de
    # un WAV truncado y, si un FLAC declara de más, falla al leer (UnreadableAudio, arriba).
    return Peaks(out, pos / sr, sr, ch)


def _con_audioread(path: str, bins: int, total_real: int | None = None) -> Peaks:
    try:
        import audioread
    except ImportError as e:      # pragma: no cover — viene con librosa
        raise UnreadableAudio(f"no hay con qué decodificar este formato ({e})") from None
    try:
        with audioread.audio_open(path) as f:
            sr, ch = int(f.samplerate), int(f.channels)
            total = total_real or int(round(float(f.duration) * sr))
            if total <= 0 or sr <= 0 or ch <= 0:
                raise UnreadableAudio("el archivo no tiene audio")
            out = np.zeros(bins, dtype=np.float32)
            pos = 0
            resto = b""
            for buf in f:            # PCM 16 bit little-endian, intercalado, de a pedazos
                datos = resto + buf
                util = len(datos) - len(datos) % (2 * ch)
                resto = datos[util:]
                if not util:
                    continue
                muestras = np.frombuffer(datos[:util], dtype="<i2").reshape(-1, ch)
                amp = np.abs(muestras.astype(np.float32)).max(axis=1) / 32768.0
                _acumular(out, amp, pos, total)
                pos += len(amp)
    except UnreadableAudio:
        raise
    except Exception as e:  # noqa: BLE001 — audioread tira lo que tire su backend
        raise UnreadableAudio(f"no se pudo decodificar el audio ({type(e).__name__}: {e})") from None
    if pos == 0:
        raise UnreadableAudio("el archivo no tiene audio")
    if total_real is None and _declara_otro_largo(pos, total):
        return _con_audioread(path, bins, total_real=pos)
    return Peaks(out, pos / sr, sr, ch)


def compute_peaks(path: Path | str, bins: int = PEAK_BINS,
                  block_frames: int = BLOCK_FRAMES) -> Peaks:
    """Los `bins` picos del archivo, leyéndolo por bloques. `UnreadableAudio` si no se puede
    decodificar; `FileNotFoundError` si no existe."""
    ruta = os.fspath(path)
    if not os.path.isfile(ruta):
        raise FileNotFoundError(ruta)
    res = _con_soundfile(ruta, bins, block_frames)
    res = res if res is not None else _con_audioread(ruta, bins)
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


def _desde_cache(guardado: object, firma: dict, bins: int) -> Peaks | None:
    """Los picos de una caché VÁLIDA, o None (y se rehace). Se valida la forma entera: un JSON
    que parsea pero no es lo que se escribió (null, [], picos que no son números finitos,
    listas anidadas) no puede terminar en un 500 ni, peor, en una onda inventada."""
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


def _podar(cache_dir: Path, tope: int) -> None:
    """Si hay más de `tope` archivos, borra los de mtime más viejo hasta quedar en el 90 %.
    Nunca falla hacia afuera: es limpieza, no puede tirar abajo un pedido."""
    try:
        archivos = [(p.stat().st_mtime_ns, p) for p in cache_dir.glob("*.json")]
        if len(archivos) <= tope:
            return
        archivos.sort()
        for _, p in archivos[: len(archivos) - int(tope * 0.9)]:
            p.unlink(missing_ok=True)
    except OSError:
        pass


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
    try:
        guardado = json.loads(archivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        guardado = None                                  # sin caché o ilegible: se rehace
    res = _desde_cache(guardado, firma, bins)
    if res is not None:
        try:
            os.utime(archivo)                            # usada: la poda la deja para el final
        except OSError:
            pass
        return res, True

    res = compute_peaks(ruta, bins)
    cache_dir.mkdir(parents=True, exist_ok=True)
    datos = {**firma, "duration_s": res.duration_s, "sample_rate": res.sample_rate,
             "channels": res.channels,
             "peaks": [round(float(v), 5) for v in res.peaks]}
    # Escritura atómica: un lector a la vez nunca ve un JSON a medio escribir.
    fd, tmp = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        os.replace(tmp, archivo)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _podar(cache_dir, CACHE_MAX_ARCHIVOS if tope is None else tope)
    # Los mismos valores que va a devolver la caché: la primera respuesta y las siguientes
    # tienen que ser idénticas.
    return Peaks(np.asarray(datos["peaks"], dtype=np.float64), res.duration_s,
                 res.sample_rate, res.channels), False
