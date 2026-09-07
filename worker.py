"""Worker de la cola de trabajos (RQ). Consume la cola `musiflix` de Redis y corre
los jobs de `tasks.py` (descargas, análisis de calidad, espectrogramas, metadatos).

Se ejecuta en su propio contenedor:  python -m worker
Necesita REDIS_URL apuntando al Redis del compose.
"""
import logging
import os

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | worker | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("worker")


def main():
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise SystemExit("REDIS_URL no está seteada; el worker necesita Redis.")

    from redis import Redis
    from rq import Queue, Worker

    conn = Redis.from_url(redis_url)
    conn.ping()
    queue = Queue("musiflix", connection=conn)
    logger.info(f"🧵 Worker escuchando la cola 'musiflix' en {redis_url}")
    Worker([queue], connection=conn).work(with_scheduler=False)


if __name__ == "__main__":
    main()
