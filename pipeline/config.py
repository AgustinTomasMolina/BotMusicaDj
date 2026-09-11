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


def rekordbox_xml() -> Path:
    """Dónde escribir el XML importable. Default dentro del staging, no en el cwd."""
    valor = os.getenv(VAR_REKORDBOX, "").strip()
    return Path(valor) if valor else staging_dir() / "rekordbox_musiflix.xml"
