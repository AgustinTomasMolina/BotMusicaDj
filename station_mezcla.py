"""Orden "Para mezclar" de la Station de SoundCloud (f45).

La Station llega en el orden de SoundCloud y ese sigue siendo el default. Este módulo agrega
un SEGUNDO orden, opt-in: el set que arma el motor de la radio (`motor.radio.build_set`) a
partir de la semilla, con la compuerta de BPM ±8 % con octava y el "por qué" de cada salto.
No hay acá un scoring nuevo: es el modo "mezclar" del mismo motor (revisión de arquitectura,
§9: "dos ordenamientos, un motor").

Dos partes:

1. `analizar(sc_id)` — mide UN tema de SoundCloud: BPM, key con su acuerdo entre tramos y
   embedding, por el único camino de medición del motor (`motor.analisis.analizar_senal`).
   El audio es el mismo que abre la barra (`source_audio`: yt-dlp + CDN validada), pero NO
   entero: se pide por HTTP Range un tramo central de `TRAMO_S` segundos del MP3 progresivo.

   Por qué 140 s y no 60-90 s: la confianza de la key es el acuerdo de `tono_consenso`, que
   necesita 3 ventanas disjuntas de 45 s (`motor/tonalidad.py:153-165`). Con 60-90 s el
   acuerdo sale siempre "0/0" y TODAS las keys se mostrarían con "?", que es verdad pero no
   sirve. 140 s de MP3 a 128 kbps son ~2,2 MB. El BPM con 60 s alcanzaría (medido en el
   informe de f45), pero una sola descarga para las dos cosas es más simple.

   Sin ffmpeg en la PC: se decodifica con soundfile (libsndfile trae MP3). Un MP3 cortado
   a mitad de archivo no arranca en un frame, así que se busca el primer encabezado de
   frame válido (dos seguidos que encadenan) antes de decodificar. Si SoundCloud entrega
   otro formato (m4a/opus), el tema queda "no analizado" con el motivo: nunca se inventa.

   Un tema Go+ solo da el preview de 30 s: se analiza eso y se marca `preview`. Que el BPM
   y la key de un preview valgan lo mismo que los del tema entero es UNKNOWN (revisión de
   arquitectura); la pantalla lo dice.

   Caché en memoria por id de SoundCloud (también los fallos, `_FALLO_TTL_S`), y un pool
   propio de `WORKERS` hilos: hilos y no procesos porque cada proceso cargaría su propio
   librosa/numba (~200 MB), y la PC tiene poca memoria. El costo medido está en el informe.

2. `ordenar(semilla, items)` — arma el orden con lo que ya está en la caché:
   - el set de `build_set` (semilla primero, después cada tema compatible con el anterior);
   - después, en el orden de SoundCloud, los temas analizados que no entraron en ninguna
     posición, con su motivo ("fuera de rango de BPM"): no se descartan;
   - al final, los que no se pudieron analizar: BPM y key "?", nunca inventados.

   Parámetros del motor (decisiones, ver el informe):
   - `curve="flat"` y `w_energy=0`: la energía de la radio es un percentil de la biblioteca
     y el RMS de 49 tramos (o previews) de SoundCloud no es comparable entre sí (revisión,
     §8/§18). Con peso 0 no decide nada; el campo va en 0.5 porque `Track` lo exige.
   - `artist_gap=0`: la Station repite uploaders a propósito y cortar el set por eso
     mandaría temas mezclables al fondo con un motivo que no es de mezcla.
   - `randomness=0`: determinista (spec §5).
   - La key pesa lo que pesa en la radio (`mezclabilidad = bpm^1 × key^0.6`, key
     desconocida = 0.5 neutro): compatibilidad, nunca compuerta.
"""
from __future__ import annotations

import io
import logging
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np

logger = logging.getLogger("bot_web")

TRAMO_S = 140.0          # ver docstring: 3 tramos de 45 s para el acuerdo de la key
WORKERS = 2              # análisis simultáneos (memoria: ~50 MB de PCM por análisis en vuelo)
MAX_ITEMS = 100          # una Station trae ~50; más que esto es un pedido raro
_CACHE_MAX = 600
_FALLO_TTL_S = 10 * 60   # un fallo se recuerda 10 min: no se re-pide 49 veces a SoundCloud
_BYTES_MAX = 6 * 1024 * 1024
_SR_DESTINO = 22050      # el de `motor.analisis.SR`

# Lo que muestra la pantalla cuando un tema no se pudo medir (§6: "?" y nunca un número).
SIN_DATO = "?"


class AnalisisError(Exception):
    """El tema no se pudo analizar; el mensaje es el motivo que ve el usuario."""


# ---------------------------------------------------------------- audio: tramo de un MP3

_BR_KBPS = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_SR_MPEG1 = (44100, 48000, 32000)


def _largo_frame(b: bytes, i: int) -> int:
    """Largo en bytes del frame MPEG-1 Layer III que empieza en `i`, o 0 si no hay uno."""
    if i + 4 > len(b) or b[i] != 0xFF or (b[i + 1] & 0xFE) != 0xFA:
        return 0
    bi, si, pad = b[i + 2] >> 4, (b[i + 2] >> 2) & 3, (b[i + 2] >> 1) & 1
    if bi in (0, 15) or si == 3:
        return 0
    return 144000 * _BR_KBPS[bi] // _SR_MPEG1[si] + pad


def primer_frame_mp3(data: bytes, buscar: int = 64 * 1024) -> int:
    """Offset del primer frame MP3 válido: uno cuyo largo lleva a otro, y ese a otro.

    Tres frames encadenados y no uno: `0xFF 0xFB` aparece por azar adentro del audio
    comprimido, y decodificar desde ahí da ruido o un error. -1 si no hay en `buscar` bytes.
    """
    for k in range(min(len(data), buscar)):
        n1 = _largo_frame(data, k)
        if not n1:
            continue
        n2 = _largo_frame(data, k + n1)
        if n2 and _largo_frame(data, k + n1 + n2):
            return k
    return -1


def rango_tramo(duracion: float | None, tamano: int | None, tramo_s: float = TRAMO_S) -> tuple[int, int, float, float] | None:
    """`(byte_desde, byte_hasta, inicio_s, largo_s)` del tramo central, o None = todo el archivo.

    Se calcula con la duración y el tamaño reales (bytes/segundo promedio), no con el
    bitrate nominal: así también sirve si el MP3 no es exactamente CBR.
    """
    if not duracion or not tamano or duracion <= tramo_s + 10:
        return None
    bps = tamano / duracion
    inicio = max(0.0, duracion / 2 - tramo_s / 2)
    desde = int(inicio * bps)
    return desde, min(tamano - 1, int(desde + tramo_s * bps)), inicio, tramo_s


def decodificar_mp3(data: bytes) -> np.ndarray:
    """Bytes de MP3 (quizás cortados a mitad) → mono a `_SR_DESTINO`, como `analisis.cargar`.

    Mismo pasaje a mono y mismo resampleo que `librosa.load` (que es lo que usa el motor
    cuando lee un archivo): `librosa.to_mono` + `librosa.resample` con su default.
    """
    import librosa
    import soundfile as sf

    k = primer_frame_mp3(data)
    if k < 0:
        raise AnalisisError("El audio de SoundCloud no se pudo decodificar (no encontré un frame MP3).")
    try:
        y, sr = sf.read(io.BytesIO(data[k:]), dtype="float32", always_2d=True)
    except Exception as e:  # noqa: BLE001 — cualquier error de libsndfile es "no decodificó"
        raise AnalisisError("El audio de SoundCloud no se pudo decodificar.") from e
    y = librosa.to_mono(y.T)
    if sr != _SR_DESTINO:
        y = librosa.resample(y, orig_sr=sr, target_sr=_SR_DESTINO)
    if y.size < _SR_DESTINO:
        raise AnalisisError("El audio de SoundCloud dura menos de 1 s.")
    return np.ascontiguousarray(y, dtype=np.float32)


def _bajar_tramo(sc_id: str) -> tuple[np.ndarray, dict]:
    """El tramo de audio del tema y lo que se sabe de él. Usa la resolución de la barra."""
    import source_audio as sa

    url = sa.canonical_url("soundcloud", sc_id)
    if not url:
        raise AnalisisError("Identificador de SoundCloud inválido.")
    hit = sa._cache_get(url)
    if isinstance(hit, sa.SourceAudioError):
        raise AnalisisError(hit.message)
    res = hit
    if res is None:
        try:
            res = sa._resolve_sync(url, "soundcloud")
        except sa.SourceAudioError as e:
            sa._cache_put(url, e)
            raise AnalisisError(e.message) from e
        sa._cache_put(url, res)
    if res.ext != "mp3":
        raise AnalisisError(f"SoundCloud entrega este tema en {res.ext or 'un formato'} y acá solo se "
                            "decodifica MP3 (no hay ffmpeg): sin análisis.")
    rango = None if res.preview else rango_tramo(res.duration, res.size)
    headers = dict(res.headers)
    if rango:
        headers["Range"] = f"bytes={rango[0]}-{rango[1]}"
    up = sa._open_upstream(res.url, headers)
    try:
        if up.status_code not in (200, 206):
            raise AnalisisError(f"SoundCloud no entregó el audio (HTTP {up.status_code}).")
        partes, total = [], 0
        for trozo in up.iter_content(64 * 1024):
            partes.append(trozo)
            total += len(trozo)
            if total > _BYTES_MAX:
                break
        data = b"".join(partes)
    finally:
        up.close()
    y = decodificar_mp3(data)
    info = {
        "preview": bool(res.preview),
        "duracion": res.duration,
        "tramo_inicio_s": round(rango[2], 1) if rango else 0.0,
        "tramo_s": round(y.size / _SR_DESTINO, 1),
    }
    return y, info


# ---------------------------------------------------------------- análisis + caché

_cache: OrderedDict[str, dict] = OrderedDict()
_inflight: dict[str, Future] = {}
_lock = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="station-analisis")


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def analizar_senal(y: np.ndarray) -> dict:
    """BPM, key + acuerdo y embedding crudo de una señal mono a 22050 Hz.

    Es `motor.analisis.analizar_senal`, el único camino de medición del motor: lo mismo que
    mide el benchmark. Acá solo se traduce a lo que viaja por la API.
    """
    from motor.analisis import analizar_senal as medir
    from motor.cli import key_dudosa

    f = medir(y, _SR_DESTINO)
    bpm = float(f.bpm)
    ok_bpm = math.isfinite(bpm) and bpm > 0
    return {
        "bpm": round(bpm, 1) if ok_bpm else None,
        "bpm_crudo": bpm if ok_bpm else None,
        "key": f.key or None,
        "key_acuerdo": f.key_acuerdo,
        "key_dudosa": key_dudosa(f.key_acuerdo),
        "embedding": np.asarray(f.embedding, dtype=np.float64),
    }


def _analizar_sync(sc_id: str) -> dict:
    t0 = time.perf_counter()
    try:
        y, info = _bajar_tramo(sc_id)
        t1 = time.perf_counter()
        r = {**analizar_senal(y), **info, "ok": True,
             "segundos_audio": round(t1 - t0, 2), "segundos_medir": round(time.perf_counter() - t1, 2)}
        del y
    except AnalisisError as e:
        r = {"ok": False, "motivo": str(e)}
    except Exception as e:  # noqa: BLE001 — red, CDN, librosa: nunca un 500, siempre un motivo
        logger.warning(f"⚠️ Análisis de la Station: {sc_id} falló ({type(e).__name__}: {e})")
        r = {"ok": False, "motivo": "No pude analizar el audio de este tema."}
    if r["ok"] and r["bpm"] is None:
        r = {**r, "ok": False, "motivo": "No se pudo medir el BPM (¿audio sin pulso?)."}
    r["sc_id"] = sc_id
    r["segundos"] = round(time.perf_counter() - t0, 2)
    r["en"] = time.monotonic()
    return r


def _cache_get(sc_id: str) -> dict | None:
    with _lock:
        r = _cache.get(sc_id)
        if r is None:
            return None
        if not r["ok"] and time.monotonic() - r["en"] > _FALLO_TTL_S:
            _cache.pop(sc_id, None)
            return None
        _cache.move_to_end(sc_id)
        return r


def analizar(sc_id: str) -> dict:
    """Análisis cacheado de un tema (bloqueante). Dos pedidos del mismo id comparten uno."""
    hit = _cache_get(sc_id)
    if hit is not None:
        return hit
    with _lock:
        fut = _inflight.get(sc_id)
        mio = fut is None
        if mio:
            fut = _POOL.submit(_analizar_sync, sc_id)
            _inflight[sc_id] = fut
    r = fut.result()                     # `_analizar_sync` no lanza: todo termina en un dict
    if mio:
        with _lock:
            _inflight.pop(sc_id, None)
            _cache[sc_id] = r
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
    return r


def publico(r: dict | None) -> dict:
    """Lo que viaja al front: sin el embedding (es interno del orden) ni campos de caché."""
    if r is None:
        return {"ok": False, "motivo": "Todavía no se analizó."}
    return {k: v for k, v in r.items() if k not in ("embedding", "bpm_crudo", "en")}


# ---------------------------------------------------------------- el orden

def _track(sc_id: str, r: dict, item: dict, embedding: np.ndarray):
    from motor.modelos import Track

    return Track(
        path=Path("soundcloud") / sc_id,
        duration=float(item.get("duracion") or r.get("duracion") or 0.0),
        bpm=float(r["bpm_crudo"]),
        key=r.get("key") or SIN_DATO,
        energy=0.5,                       # w_energy=0: no decide (ver docstring)
        embedding=embedding,
        license="sin verificar (SoundCloud)",
        source_url=item.get("url") or f"https://api.soundcloud.com/tracks/{sc_id}",
        artist=item.get("artista") or None,
        title=item.get("titulo") or None,
        key_acuerdo=r.get("key_acuerdo"),
    )


def _key_mostrada(key: str | None, dudosa: bool) -> str:
    if not key or key == SIN_DATO:
        return SIN_DATO
    return f"{key}{SIN_DATO if dudosa else ''}"


def razon(transicion, desde_dudosa: bool, hasta_dudosa: bool) -> str:
    """El renglón de §6 (`Transition.reason`, `+1.8% BPM | 8A → 9A (vecino)`), con la key
    marcada "?" cuando su acuerdo no es unánime: la misma regla que la CLI (`key_dudosa`)."""
    t = replace(transicion,
                from_key=None if transicion.from_key is None else _key_mostrada(transicion.from_key, desde_dudosa),
                to_key=_key_mostrada(transicion.to_key, hasta_dudosa))
    return t.reason()


def config_mezcla(n_pool: int):
    from motor.radio import RadioConfig

    return RadioConfig(length=n_pool + 1, curve="flat", w_energy=0.0, artist_gap=0,
                       randomness=0.0, seed=0)


def ordenar_con(semilla_id: str, items: list[dict], analisis: dict[str, dict]) -> dict:
    """El orden "Para mezclar" a partir de análisis ya hechos (puro: sin red, testeable).

    `items`: los temas de la Station EN EL ORDEN DE SOUNDCLOUD, cada uno con `video_id`
    (id de SoundCloud), `titulo`, `artista`, `duracion`, `url`. `analisis[id]`: lo que
    devuelve `analizar` (o nada, si no se analizó).
    """
    from motor.embeddings import normalize_matrix
    from motor.modelos import es_track
    from motor.radio import build_set
    from motor.scoring import TOLERANCIA_BPM, bpm_score

    def ok(sid):
        r = analisis.get(sid)
        return r is not None and r.get("ok") and r.get("bpm_crudo") is not None

    # Un id repetido es una sola fila del orden (el front ubica el resto al final).
    por_id = {str(it.get("video_id")): it for it in reversed(items) if it.get("video_id")}
    ids = list(dict.fromkeys(str(it.get("video_id")) for it in items if it.get("video_id")))
    analizados = [sid for sid in ids if ok(sid)]
    semilla_ok = ok(semilla_id)
    aviso = None
    arranque = semilla_id if semilla_ok else (analizados[0] if analizados else None)
    if arranque is None:
        return {"exito": False, "mensaje": "No se pudo analizar ningún tema: no hay orden para mezclar."}
    if not semilla_ok:
        aviso = ("No se pudo analizar la semilla: el orden para mezclar arranca por el primer tema "
                 "analizado de la Station.")

    pool_ids = [sid for sid in analizados if sid != arranque]
    todos = [arranque, *pool_ids]
    emb, _, _ = normalize_matrix(np.vstack([analisis[sid]["embedding"] for sid in todos]))
    tracks = {sid: _track(sid, analisis[sid], por_id.get(sid, {}), emb[k]) for k, sid in enumerate(todos)}

    rset = build_set(tracks[arranque], [tracks[s] for s in pool_ids], config_mezcla(len(pool_ids)))
    en_set = [step.track.path.name for step in rset.steps]
    dudosa = {sid: bool(analisis[sid].get("key_dudosa", True)) for sid in todos}

    filas = []
    for k, step in enumerate(rset.steps):
        sid = en_set[k]
        if k == 0 and sid == semilla_id:
            continue                                   # la semilla no es una fila de la Station
        # k == 0 con otra semilla: el primer tema analizado hizo de semilla y SÍ es una fila.
        previo_dudoso = dudosa[en_set[k - 1]] if k else False
        filas.append({"video_id": sid, "grupo": "set",
                      "razon": razon(step.transition, previo_dudoso, dudosa[sid])})

    ultimo = rset.steps[-1].track
    quedaron = [sid for sid in pool_ids if sid not in set(en_set)]
    for sid in quedaron:
        t = tracks[sid]
        if not es_track(t.duration):
            motivo = "Dura menos de 90 s: el motor no lo propone como tema de un set."
        else:
            con = sum(1 for s in en_set if bpm_score(tracks[s].bpm, t.bpm) > 0)
            if con == 0:
                motivo = (f"Fuera de rango de BPM: {t.bpm:.1f} BPM no está a ±{TOLERANCIA_BPM:.0%} de "
                          f"ningún tema del set (con medio/doble tiempo).")
            else:
                motivo = (f"Fuera de rango de BPM: el set se cortó en {ultimo.bpm:.1f} BPM y este tema "
                          f"({t.bpm:.1f} BPM) no entra a ±{TOLERANCIA_BPM:.0%} de ahí.")
        filas.append({"video_id": sid, "grupo": "fuera", "motivo": motivo})
    for sid in ids:
        if not ok(sid):
            r = analisis.get(sid) or {}
            filas.append({"video_id": sid, "grupo": "sin_analisis",
                          "motivo": r.get("motivo") or "Todavía no se analizó."})

    return {"exito": True, "semilla_analizada": semilla_ok, "aviso": aviso, "orden": filas,
            "en_set": len([f for f in filas if f["grupo"] == "set"]),
            "fuera": len(quedaron), "sin_analisis": len(ids) - len(analizados),
            "corte": rset.stop_detail or None}


def metricas(secuencia: list[dict]) -> dict:
    """Las métricas de §4 sobre una secuencia de análisis EN ORDEN (para el informe).

    Cuenta solo transiciones entre dos temas medidos: un "?" no es un salto medible.
    `fuera_8`: `bpm_score == 0` (la compuerta de la radio); `choques`: `compat_camelot < 0.4`.
    """
    from motor.scoring import bpm_score
    from motor.tonalidad import compat_camelot

    pares = [(a, b) for a, b in zip(secuencia, secuencia[1:], strict=False)
             if a and b and a.get("bpm_crudo") and b.get("bpm_crudo")]
    fuera = sum(1 for a, b in pares if bpm_score(a["bpm_crudo"], b["bpm_crudo"]) == 0.0)
    choques = sum(1 for a, b in pares if compat_camelot(a.get("key") or SIN_DATO, b.get("key") or SIN_DATO) < 0.4)
    n = len(pares)
    return {"transiciones": n, "fuera_8": fuera, "choques": choques,
            "pct_fuera_8": round(100 * fuera / n, 1) if n else None,
            "pct_choques": round(100 * choques / n, 1) if n else None}


def ordenar(semilla_id: str, items: list[dict]) -> dict:
    """`ordenar_con` con los análisis de la caché (no dispara análisis nuevos)."""
    ids = {str(it.get("video_id") or "") for it in items} | {semilla_id}
    return ordenar_con(semilla_id, items, {sid: r for sid in ids if (r := _cache_get(sid)) is not None})
