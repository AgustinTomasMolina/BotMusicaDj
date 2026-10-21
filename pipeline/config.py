"""Destinos del pipeline. TODOS salen de configuración, ninguno hardcodeado.

La tarea 5.16 existe por esto: `analizar_calidad.py:25-31` tiene clavada la ruta de ffmpeg
de OTRA máquina (`C:\\Users\\AgusT\\...`), y los .bat tenían el mismo problema. Una ruta
hardcodeada funciona en una sola computadora y falla en silencio en el resto.

Variables (mismo prefijo que usan `server.py:46` y `db.py:24`):

    MUSIFLIX_STAGING    Dónde quedan las copias procesadas a la espera de revisión.
                        Tiene default: es interno del pipeline y no le importa a nadie más.
    MUSIFLIX_ITUNES     La carpeta "Añadir automáticamente a iTunes". SIN default: es la
                        carpeta de otra persona y adivinarla es peor que fallar.
    MUSIFLIX_REKORDBOX_XML  Dónde escribir el XML importable. Default: dentro del staging.
    MUSIC_WATCH_FOLDER  Carpeta vigilada (f65): lo nuevo que aparece ahí entra solo al
                        pipeline (`pipeline.vigilar`). Local, disco montado o UNC
                        (\\\\NAS\\musica). SIN default: vacía o ausente = vigilancia apagada.
    MUSIC_WATCH_INTERVAL_S  Cada cuántos segundos se mira la carpeta (default 60, mínimo 10).
    MUSIC_WATCH_TIMEOUT_S   Tope por archivo en segundos (default 300, mínimo 10). Pasado el
                        tope se mata el proceso que lo analiza y el archivo queda fallido.

Por qué iTunes no tiene default: si `aplicar` inventara una carpeta, copiaría música a un
lugar que nadie pidió, o peor, al directorio actual. Falla con un mensaje que dice qué
variable definir.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

VAR_STAGING = "MUSIFLIX_STAGING"
VAR_ITUNES = "MUSIFLIX_ITUNES"
VAR_REKORDBOX = "MUSIFLIX_REKORDBOX_XML"
VAR_WATCH = "MUSIC_WATCH_FOLDER"
VAR_WATCH_INTERVALO = "MUSIC_WATCH_INTERVAL_S"
VAR_WATCH_TIMEOUT = "MUSIC_WATCH_TIMEOUT_S"

WATCH_INTERVALO_DEFAULT_S = 60.0
WATCH_INTERVALO_MINIMO_S = 10.0
WATCH_TIMEOUT_DEFAULT_S = 300.0
WATCH_TIMEOUT_MINIMO_S = 10.0


class DestinoNoConfigurado(RuntimeError):
    """El destino no está configurado o no existe. Nunca se inventa uno."""


def staging_dir() -> Path:
    """Destino intermedio. Se crea si no existe: es interno del pipeline."""
    ruta = Path(os.getenv(VAR_STAGING, str(BASE_DIR / "pipeline" / "staging")))
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def itunes_dir(crear: bool = False) -> Path:
    """La carpeta de iTunes. Falla si no está configurada o no existe.

    NO se crea sola salvo que se pida: si la carpeta no existe suele ser porque la variable
    apunta mal, y crearla dejaría la música en un lugar que iTunes no mira.
    """
    valor = os.getenv(VAR_ITUNES, "").strip()
    if not valor:
        raise DestinoNoConfigurado(
            f"No está configurado {VAR_ITUNES}.\n"
            f"  Es la carpeta 'Añadir automáticamente a iTunes', que en Windows suele ser:\n"
            f"    C:\\Users\\<vos>\\Music\\iTunes\\iTunes Media\\Automatically Add to iTunes\n"
            f"  Definila en el .env o en el entorno:\n"
            f"    {VAR_ITUNES}=<esa ruta>\n"
            f"  No se inventa una carpeta ni se escribe en el directorio actual.")
    ruta = Path(valor)
    if not ruta.exists():
        if not crear:
            raise DestinoNoConfigurado(
                f"{VAR_ITUNES} apunta a una carpeta que no existe:\n    {ruta}\n"
                f"  Si la ruta es correcta, creala primero. Si no, corregí la variable.")
        ruta.mkdir(parents=True, exist_ok=True)
    if ruta.exists() and not ruta.is_dir():
        raise DestinoNoConfigurado(f"{VAR_ITUNES} no es una carpeta: {ruta}")
    return ruta


def rekordbox_xml(sello: str = "") -> Path:
    """Dónde escribir el XML importable por Rekordbox.

    NO va dentro del staging. El staging se pisa entero en cada corrida de `revisar`, y
    este XML es la ÚNICA vía por la que van a viajar los cues (#5.4): dejarlo ahí es
    guardar lo menos reemplazable en la carpeta más volátil. Va al lado del reporte y con
    fecha, para poder volver a una tanda anterior.
    """
    valor = os.getenv(VAR_REKORDBOX, "").strip()
    if valor:
        return Path(valor)
    sufijo = f"_{sello}" if sello else ""
    return staging_dir().parent / f"rekordbox_musiflix{sufijo}.xml"


def watch_folder() -> Path | None:
    """La carpeta vigilada, o None si la vigilancia está apagada (variable vacía o ausente).

    NO verifica que exista: una carpeta de red puede no estar montada ahora y sí en el próximo
    ciclo. Eso lo resuelve el vigilante (lo anota y reintenta), no la configuración.
    """
    valor = os.getenv(VAR_WATCH, "").strip().strip('"').strip()
    return Path(valor) if valor else None


def _segundos(var: str, default: float, minimo: float) -> float:
    """Segundos leídos del entorno. Mal escrito = el default; por debajo del mínimo = el
    mínimo: un typo en el .env no puede dejar al server mirando la red cada 0 s."""
    texto = os.getenv(var, "").strip()
    if not texto:
        return default
    try:
        valor = float(texto)
    except ValueError:
        return default
    if valor != valor or valor in (float("inf"), float("-inf")):   # NaN / infinito
        return default
    return max(valor, minimo)


def watch_interval_s() -> float:
    return _segundos(VAR_WATCH_INTERVALO, WATCH_INTERVALO_DEFAULT_S, WATCH_INTERVALO_MINIMO_S)


def watch_timeout_s() -> float:
    return _segundos(VAR_WATCH_TIMEOUT, WATCH_TIMEOUT_DEFAULT_S, WATCH_TIMEOUT_MINIMO_S)
