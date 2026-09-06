"""
Configuración de API REST y utilidades
"""

from typing import Dict, List
import json

class APIConfig:
    """Configuración centralizada del API"""
    
    # Versión
    VERSION = "1.0.0"
    TITULO = "🎵 Music Bot API"
    DESCRIPCION = "API para búsqueda, descarga y recomendación de música inteligente"
    
    # Server
    HOST = "0.0.0.0"
    PORT = 8000
    
    # CORS
    CORS_ORIGINS = ["*"]
    
    # Límites
    LIMITE_BUSQUEDA_MIN = 1
    LIMITE_BUSQUEDA_MAX = 100
    LIMITE_RECOMENDACIONES_MIN = 1
    LIMITE_RECOMENDACIONES_MAX = 50
    
    # Formatos
    FORMATOS_SOPORTADOS = ["mp3", "wav", "flac", "aiff"]
    FORMATO_DEFECTO = "mp3"
    
    # Géneros
    GENEROS_POPULARES = [
        "House", "Techno", "Trance", "Ambient",
        "Deep House", "Tech House", "Progressive House",
        "Electro", "Downtempo", "Chill Out",
        "Drum & Bass", "Dubstep", "Garage",
        "Minimal", "Acid", "Industrial", "Synthwave",
        "Indie", "Rock", "Pop", "Hip Hop"
    ]


class RespuestaAPI:
    """Utilidades para respuestas consistentes"""
    
    @staticmethod
    def exito(mensaje: str, datos: Dict = None):
        return {
            "exito": True,
            "mensaje": mensaje,
            "datos": datos or {}
        }
    
    @staticmethod
    def error(mensaje: str, datos: Dict = None):
        return {
            "exito": False,
            "mensaje": mensaje,
            "datos": datos or {}
        }


class CacheAPI:
    """Cache simple para búsquedas"""
    
    def __init__(self):
        self._cache: Dict[str, Dict] = {}
    
    def obtener(self, clave: str):
        return self._cache.get(clave)
    
    def guardar(self, clave: str, valor: Dict):
        self._cache[clave] = valor
    
    def limpiar(self):
        self._cache.clear()


# Instancia global
cache_api = CacheAPI()
