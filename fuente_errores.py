"""Errores de una fuente de búsqueda (YouTube, SoundCloud, Spotify, los MP3 directos).

Antes cada `buscar_*` tragaba la excepción y devolvía []: "la plataforma se cayó" y "la
plataforma no lo tiene" eran la misma lista vacía, y la Station decía "No lo encontré en otras
plataformas" cuando en realidad nadie había contestado (auditoría f40). Ahora una fuente que no
pudo buscar lo SEÑALA con una de estas, y el que junta las fuentes (`server._mix_fuentes`) las
cuenta aparte: caída → "no contestó"; sin configurar → no se buscó ahí.

Módulo propio (sin dependencias) para que `search_agent` y `scrapers` las compartan sin
importarse entre sí."""


class FuenteError(Exception):
    """La fuente no pudo buscar. El mensaje nunca lleva credenciales ni URLs firmadas."""


class FuenteCaida(FuenteError):
    """La fuente no contestó o contestó un error (red, HTTP, el extractor roto)."""


class FuenteNoConfigurada(FuenteError):
    """La fuente no se puede usar con esta instalación (sin credenciales o sin la librería):
    no es que se cayó ni que no tenga el tema; no se buscó ahí."""
