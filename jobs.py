"""Cola de trabajos (Redis + RQ) para mover el trabajo pesado a un worker aparte.

OPCIONAL: si `REDIS_URL` no está seteada o Redis no responde, `queue_disponible()`
devuelve False y el server corre todo local (como siempre). Así la app funciona con
y sin worker — el modo `py server.py` de siempre no se rompe.
"""
import logging
import os
import time

logger = logging.getLogger("bot_web")

REDIS_URL = os.getenv("REDIS_URL")
QUEUE_NAME = "musiflix"

_redis = None
_queue = None

if REDIS_URL:
    try:
        from redis import Redis
        from rq import Queue
        _redis = Redis.from_url(REDIS_URL)
        _redis.ping()
        _queue = Queue(QUEUE_NAME, connection=_redis)
        logger.info(f"🧵 Cola de trabajos conectada a Redis ({REDIS_URL}).")
    except Exception as e:
        logger.warning(f"⚠️ Redis no disponible ({e}); corriendo sin cola (modo local).")
        _redis = None
        _queue = None


def queue_disponible() -> bool:
    return _queue is not None


def encolar(func, *args, timeout: int = 900):
    """Encola una función-job. Devuelve el objeto job (tiene .id)."""
    return _queue.enqueue(func, *args, job_timeout=timeout, result_ttl=3600)


def _valor_resultado(job):
    # RQ nuevo: return_value(); viejo: .result
    getter = getattr(job, "return_value", None)
    return getter() if callable(getter) else getattr(job, "result", None)


def estado_job(job_id: str) -> dict:
    """Estado de un job: queued | started | finished | failed | unknown."""
    try:
        from rq.job import Job
        job = Job.fetch(job_id, connection=_redis)
    except Exception:
        return {"estado": "unknown"}
    st = job.get_status(refresh=True)
    out = {"estado": st}
    if st == "finished":
        out["resultado"] = _valor_resultado(job)
    elif st == "failed":
        out["error"] = (job.exc_info or "El trabajo falló.").strip().splitlines()[-1][:300]
    return out


def esperar_resultado(job, timeout: int = 120, intervalo: float = 0.4):
    """Poolea un job hasta que termina y devuelve su resultado (o lanza si falla).
    Se usa para análisis (calidad/spectro/meta): el cómputo pesado corre en el worker,
    pero el endpoint mantiene su contrato (espera y devuelve el resultado)."""
    from rq.job import Job
    fin = time.time() + timeout
    while time.time() < fin:
        st = job.get_status(refresh=True)
        if st == "finished":
            return _valor_resultado(job)
        if st == "failed":
            raise RuntimeError((job.exc_info or "job failed").strip().splitlines()[-1][:300])
        time.sleep(intervalo)
    raise TimeoutError(f"El trabajo {job.id} no terminó en {timeout}s")
