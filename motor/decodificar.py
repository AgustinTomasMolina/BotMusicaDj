"""Leer audio SIN `audioread`: soundfile para lo que libsndfile abre y ffmpeg para el resto.

Por qué existe (tarea 5.71 del roadmap). `librosa.load` abre con soundfile y, si libsndfile no
reconoce el formato (M4A/AAC, MP4, WMA, Opus en WebM…), cae a `audioread`. Eso es un camino con
DOS relojes corriendo:

- `audioread.rawread` importa `aifc`, `audioop` y `sunau`, que desaparecen en Python 3.13
  (PEP 594). Y `librosa.load` llama a `audioread.available_backends()` en CADA carga, no solo
  en el fallback: con un audioread viejo en 3.13 no se abriría ni un WAV.
- librosa marca el fallback como deprecado desde 0.10 y lo elimina en 1.0.

Peor que romperse es romperse callado: `motor.analisis.cargar` devuelve `None` ante cualquier
error, así que el día que el fallback muera el reporte diría «no se pudo analizar» sobre
archivos que hoy andan. Medido en la biblioteca real: son los M4A (libsndfile 1.2.2:
«Format not recognised»); los WAV, MP3, FLAC y OGG/Opus los abre soundfile directo.

La decisión: los formatos que soundfile no abre los decodifica ffmpeg, el MISMO ffmpeg que ya
usa el resto del repo (`analizar_calidad._resolver_bin`: `FFMPEG_BIN`/`FFPROBE_BIN`, el PATH
o `FFMPEG_DIR`). audioread buscaba «ffmpeg» pelado en el PATH e ignoraba esas variables.

Lo que este módulo garantiza:

- `cargar` y `duracion` dan lo mismo que `librosa.load` / `librosa.get_duration(path=)`: por el
  camino de soundfile es el mismo código (mismo `to_mono` y `resample` de librosa) y por el de
  ffmpeg es el mismo PCM de 16 bits que entregaba audioread, escalado igual.
- `LectorPCM` es el reemplazo de `audioread.audio_open` para quien lee en streaming
  (`motor.peaks`, `motor.bandas`): `samplerate`, `channels`, `duration`, iterable de bloques de
  PCM 16 bit little-endian intercalado, `close()` y `with`.
- Si falta ffmpeg o ffprobe, o el archivo no tiene pista de audio, sale `DecodificacionError`
  con el motivo; nunca un audio inventado.

NO modifica el archivo: ffmpeg lo abre solo para leer y escribe a un pipe.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile

import numpy as np

BLOQUE_BYTES = 1 << 16
TIMEOUT_SONDEO_S = 60
# Tope de lo que se acepta leer del pipe de ffmpeg, para que uno que no termina nunca no llene la
# memoria o el disco. La política:
#   tope = max(FACTOR_TOPE x el PCM que declara el archivo + HOLGURA_TOPE_BYTES, PISO_TOPE_BYTES)
# El piso (512 MiB, ~50 min de estéreo a 44,1 kHz) existe porque la duración declarada puede
# quedarse MUY corta de lo real (MP3 concatenados, un Xing ajeno) y ese audio es legítimo: antes
# se leía entero. Sin duración declarada, un tope absoluto (TOPE_SIN_DURACION_BYTES, ~3 h de
# estéreo a 44,1 kHz). NO cubre un ffmpeg que se cuelga SIN escribir nada: eso pediría un hilo
# lector con timeout y queda dicho en vez de hecho a medias.
FACTOR_TOPE = 4
HOLGURA_TOPE_BYTES = 1 << 20
PISO_TOPE_BYTES = 512 << 20
TOPE_SIN_DURACION_BYTES = 2 << 30

MSG_INSTALAR = ("instalalo o definí FFMPEG_BIN y FFPROBE_BIN "
                "(o FFMPEG_DIR con la carpeta que los tiene)")
MSG_SIN_FFMPEG = "no se encontró ffmpeg/ffprobe: " + MSG_INSTALAR


class DecodificacionError(Exception):
    """No se pudo decodificar el archivo (sin ffmpeg, sin pista de audio, archivo roto)."""


def _binario(nombre: str) -> str:
    """ffmpeg/ffprobe por el mismo camino que el resto del repo (#5.16). Se resuelve en cada
    llamada, no al importar, para que cambiar `FFMPEG_BIN` surta efecto."""
    from analizar_calidad import _resolver_bin

    return _resolver_bin(nombre, f"{nombre.upper()}_BIN", "FFMPEG_DIR")


def _entrada(ruta: str | os.PathLike) -> str:
    """La ruta como la espera ffmpeg. El prefijo `file:` evita que se interprete como un
    protocolo (`concat:`, `subfile:`…) o como una opción si el nombre empieza con «-»."""
    return "file:" + os.path.abspath(os.fspath(ruta))


def _positivo(valor: object) -> float | None:
    try:
        v = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def sondear(ruta: str | os.PathLike) -> dict:
    """`{"samplerate", "channels", "duration"}` de la primera pista de audio, con ffprobe.
    `duration` es `None` si el contenedor no la declara."""
    cmd = [_binario("ffprobe"), "-v", "error", "-select_streams", "a:0",
           "-show_entries", "stream=sample_rate,channels,duration:format=duration",
           "-of", "json", _entrada(ruta)]
    try:
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=TIMEOUT_SONDEO_S)
    except FileNotFoundError:
        raise DecodificacionError(MSG_SIN_FFMPEG) from None
    except subprocess.TimeoutExpired:
        raise DecodificacionError("ffprobe no contestó a tiempo") from None
    if r.returncode != 0:
        raise DecodificacionError(
            "ffprobe no pudo leer el archivo: " + r.stderr.decode("utf-8", "replace").strip()[-300:])
    try:
        info = json.loads(r.stdout or b"{}")
        pista = (info.get("streams") or [None])[0]
    except (ValueError, AttributeError):
        pista = None
    if not pista:
        raise DecodificacionError("el archivo no tiene pista de audio")
    sr, ch = _positivo(pista.get("sample_rate")), _positivo(pista.get("channels"))
    if sr is None or ch is None:
        raise DecodificacionError("el archivo no declara frecuencia de muestreo o canales")
    dur = _positivo(pista.get("duration")) or _positivo((info.get("format") or {}).get("duration"))
    return {"samplerate": int(sr), "channels": int(ch), "duration": dur}


class LectorPCM:
    """Streaming de PCM 16 bit little-endian intercalado, decodificado por ffmpeg.

    Mismo contrato que lo que devolvía `audioread.audio_open` para `peaks` y `bandas`. Cerrar
    siempre (`with`): si se deja de leer a medias, `close` mata el ffmpeg."""

    def __init__(self, ruta: str | os.PathLike):
        info = sondear(ruta)
        self.samplerate: int = info["samplerate"]
        self.channels: int = info["channels"]
        # `None` si el contenedor no la declara: leer no la necesita, solo quien reparte el
        # audio en bins sobre el total (`motor.peaks`), que en ese caso mide leyendo.
        self.duration: float | None = info["duration"]
        self._tope = (TOPE_SIN_DURACION_BYTES if self.duration is None else
                      max(int(self.duration * self.samplerate * self.channels * 2 * FACTOR_TOPE)
                          + HOLGURA_TOPE_BYTES, PISO_TOPE_BYTES))
        self._leidos = 0
        # `-ac`/`-ar` con lo que SONDEÓ ffprobe: la salida tiene por construcción los canales
        # y la frecuencia con que el llamador interpreta el PCM, aunque el decodificador
        # entregue otros.
        cmd = [_binario("ffmpeg"), "-hide_banner", "-nostats", "-nostdin", "-v", "error",
               "-i", _entrada(ruta), "-map", "0:a:0", "-vn", "-sn", "-dn",
               "-ac", str(self.channels), "-ar", str(self.samplerate),
               "-f", "s16le", "-acodec", "pcm_s16le", "-"]
        self._err = tempfile.TemporaryFile()  # noqa: SIM115 — se cierra en `close`
        try:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=self._err)
        except FileNotFoundError:
            self._err.close()
            raise DecodificacionError(MSG_SIN_FFMPEG) from None

    def __iter__(self):
        while True:
            buf = self._proc.stdout.read(BLOQUE_BYTES)
            if not buf:
                break
            self._leidos += len(buf)
            if self._leidos > self._tope:
                self._proc.kill()
                raise DecodificacionError(
                    f"ffmpeg entregó mucho más audio que el declarado (más de {self._tope} bytes)")
            yield buf
        # Se llegó al final: si ffmpeg no terminó bien, lo que se leyó no es todo el audio.
        if self._proc.wait() != 0:
            raise DecodificacionError("ffmpeg no pudo decodificar el archivo: " + self._stderr())

    def _stderr(self) -> str:
        try:
            self._err.seek(0)
            return self._err.read().decode("utf-8", "replace").strip()[-300:]
        except (OSError, ValueError):
            return ""

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()
        self._proc.stdout.close()
        self._err.close()

    def __enter__(self) -> LectorPCM:
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


def _a_float(pcm: bytes, canales: int) -> np.ndarray:
    """PCM 16 bit → float32 en (canales, muestras), con el escalado de `librosa.util.buf_to_float`
    (÷ 32768). Un canal queda en 1-D, como lo deja `librosa.load`."""
    y = np.frombuffer(pcm, dtype="<i2").astype(np.float32) * np.float32(1.0 / 32768.0)
    return y.reshape(-1, canales).T if canales > 1 else y


def _leer_soundfile(ruta: str, offset: float, duration: float | None):
    """`((y, sr_nativo), None)` por libsndfile, o `(None, motivo)` si no lo abre o falla al
    leerlo. Es `librosa.core.audio.__soundfile_load` (no es público, por eso la copia)."""
    import soundfile as sf

    try:
        with sf.SoundFile(ruta) as f:
            sr = f.samplerate
            if offset:
                f.seek(int(offset * sr))
            frames = int(duration * sr) if duration is not None else -1
            y = f.read(frames=frames, dtype="float32", always_2d=False).T
    except RuntimeError as e:   # LibsndfileError es un RuntimeError
        return None, str(e)
    return (y, sr), None


def _sin_ffmpeg_o_fallo(motivo: str, e: DecodificacionError) -> DecodificacionError:
    """El error final cuando libsndfile no abrió el archivo Y ffmpeg tampoco pudo: lleva el
    motivo de libsndfile. Un WAV dañado o un offset fuera del archivo no es «instalá ffmpeg»."""
    if str(e) == MSG_SIN_FFMPEG:
        return DecodificacionError(f"libsndfile no lo abre ({motivo}) y no hay ffmpeg para "
                                   f"intentarlo: {MSG_INSTALAR}")
    return DecodificacionError(f"libsndfile no lo abre ({motivo}) y ffmpeg tampoco: {e}")


def _leer_ffmpeg(ruta: str, offset: float, duration: float | None):
    """`(y, sr_nativo)` decodificando con ffmpeg, recortando `offset`/`duration` sobre las
    muestras como lo hacía el fallback de librosa (para tener las mismas)."""
    with LectorPCM(ruta) as f:
        sr, ch = f.samplerate, f.channels
        ini = int(sr * offset) * ch
        fin = None if duration is None else ini + int(sr * duration) * ch
        trozos: list[bytes] = []
        n = 0                                       # muestras (todas las de todos los canales)
        for buf in f:
            nuevo = n + len(buf) // 2
            if nuevo > ini:
                a = max(0, ini - n) * 2
                b = len(buf) if fin is None else max(0, min(len(buf), (fin - n) * 2))
                trozos.append(buf[a:b])
            n = nuevo
            if fin is not None and n >= fin:
                break
    pcm = b"".join(trozos)
    pcm = pcm[: len(pcm) - len(pcm) % (2 * ch)]     # un frame partido no es audio
    return (_a_float(pcm, ch) if pcm else np.empty(0, dtype=np.float32)), sr


def cargar(ruta: str | os.PathLike, sr: int | None, mono: bool = True, offset: float = 0.0,
           duration: float | None = None) -> tuple[np.ndarray, int]:
    """Reemplazo de `librosa.load(ruta, sr=sr, mono=mono, offset=offset, duration=duration)`.

    `sr` es OBLIGATORIO (sin default): `None` es el sample rate nativo. librosa tiene 22050
    por default y olvidarse de pasarlo cambiaba lo que se medía.

    Lanza `DecodificacionError` si ffmpeg hace falta y no está o el archivo no se lee. El
    llamador decide qué hacer con eso; acá nunca se traga."""
    import librosa

    ruta = os.fspath(ruta)
    leido, motivo = _leer_soundfile(ruta, offset, duration)
    if leido is None:
        try:
            leido = _leer_ffmpeg(ruta, offset, duration)
        except DecodificacionError as e:
            raise _sin_ffmpeg_o_fallo(motivo, e) from None
    y, sr_nativo = leido
    if mono:
        y = librosa.to_mono(y)
    if sr is None:
        return y, sr_nativo
    return librosa.resample(y, orig_sr=sr_nativo, target_sr=sr), sr


def duracion(ruta: str | os.PathLike) -> float:
    """Reemplazo de `librosa.get_duration(path=ruta)`: segundos de audio del archivo."""
    import soundfile as sf

    ruta = os.fspath(ruta)
    try:
        return float(sf.info(ruta).duration)
    except RuntimeError as e_sf:
        try:
            d = sondear(ruta)["duration"]
        except DecodificacionError as e:
            raise _sin_ffmpeg_o_fallo(str(e_sf), e) from None
        if d is None:
            raise DecodificacionError("ffprobe no informa la duración del archivo") from None
        return float(d)
