"""El `server:app` de siempre, con la descarga doblada: el E2E nunca baja nada de internet.

run.mjs levanta `uvicorn server_doblado:app` en vez de `server:app`. Solo se reemplazan las
piezas que salen a la red al DESCARGAR (f41: bajar desde la playlist); el resto del server es
el mismo módulo, sin tocar:

- `_descargar_sync` (yt-dlp): escribe un archivo en downloads/ y devuelve la calidad como la
  da yt-dlp (Opus 160k). Tarda ~0.8 s a propósito, para que la pantalla muestre "Bajando…" y
  el progreso de la tanda. Una url con "e2e-falla" tira el error que tira yt-dlp con un video
  que no existe: el caso del motivo real;
- `_descargar_directo` (MP3 directo) y la búsqueda del equivalente en YouTube: si un caso
  llegara ahí, falla con un mensaje que lo dice, en vez de salir a internet;
- `similares.meta_de` (Deezer, para completar BPM/género al taguear): devuelve vacío.
"""
import time

import server
import similares
from server import app  # noqa: F401  (lo que levanta uvicorn)

ESPERA_S = 0.8


def _descargar_sync(url: str, titulo: str, formato: str) -> dict:
    time.sleep(ESPERA_S)
    if "e2e-falla" in url:
        raise RuntimeError("ERROR: [youtube] e2e-falla: Video unavailable")
    archivo = f"{server._safe_name(titulo)}.{formato}"
    (server.DOWNLOADS_DIR / archivo).write_bytes(b"audio doblado del E2E")
    return {"ok": True, "archivo": archivo,
            "calidad": server._calidad_desde_ydl({"acodec": "opus", "abr": 160, "ext": "webm"})}


def _sin_red(*_a, **_k):
    raise RuntimeError("E2E: este camino saldría a internet y no está doblado")


server._descargar_sync = _descargar_sync
server._descargar_directo = _sin_red
server.search_agent.buscar_en_youtube = _sin_red
similares.meta_de = lambda *a, **k: {}
