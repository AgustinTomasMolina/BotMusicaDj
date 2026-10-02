"""Funciones-job que ejecuta el worker (RQ). Importan la lógica de server/similares
de forma lazy (dentro de cada función) para no crear import circular al cargar el
módulo — el worker importa `tasks`, y cada job importa lo que necesita al correr.
"""


def descargar_job(payload: dict) -> dict:
    """Descarga completa (bajar → calidad → tags → historial/crate)."""
    import server
    return server.procesar_descarga(payload)


def descargar_item_job(pid: int, item_id: int, formato: str) -> dict:
    """Descarga de un tema guardado en una playlist (f41): el item dice qué bajar.
    Suelta la reserva que tomó el endpoint al encolar (server._clave_item), pase lo que pase."""
    import jobs
    import server
    try:
        return server.descargar_item_playlist(pid, item_id, formato)
    finally:
        jobs.liberar(server._clave_item(item_id))


def calidad_job(titulo: str, artista: str, fuente: str, url: str):
    """Nota de calidad (A/B/C/D/F) del audio real — cómputo pesado (yt-dlp/análisis)."""
    import server
    return server._calidad_preview(titulo, artista, fuente, url)


def spectro_job(titulo: str, artista: str, fuente: str, url: str):
    """Espectrograma (PNG en bytes) — cómputo pesado (ffmpeg)."""
    import server
    return server._spectrograma(titulo, artista, fuente, url)


def meta_job(titulo: str, artista: str) -> dict:
    """BPM + género (Deezer + fallback librosa)."""
    import similares
    return similares.meta_de(titulo, artista, True)
