"""
Cliente HTTP para comunicación con API
"""

import axios
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class APIClient:
    """Cliente para comunicarse con la API REST"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = axios.Axios(base_url=base_url)
    
    async def buscar(self, query: str, limite: int = 10) -> Optional[Dict]:
        """Buscar canciones"""
        try:
            response = await self.client.post(
                "/api/v1/buscar",
                {"query": query, "limite": limite}
            )
            return response.data
        except Exception as e:
            logger.error(f"Error en búsqueda: {e}")
            return None
    
    async def buscar_genero(self, genero: str, cantidad: int = 15) -> Optional[Dict]:
        """Buscar por género"""
        try:
            response = await self.client.post(
                "/api/v1/buscar-genero",
                {"genero": genero, "cantidad": cantidad}
            )
            return response.data
        except Exception as e:
            logger.error(f"Error en búsqueda por género: {e}")
            return None
    
    async def recomendar(self, genero: str, cantidad: int = 15) -> Optional[Dict]:
        """Obtener recomendaciones"""
        try:
            response = await self.client.post(
                "/api/v1/recomendar",
                {"genero": genero, "cantidad": cantidad}
            )
            return response.data
        except Exception as e:
            logger.error(f"Error en recomendaciones: {e}")
            return None
    
    async def descargar(self, genero: str, cantidad: int = 15, formato: str = "mp3") -> Optional[Dict]:
        """Iniciar descarga"""
        try:
            response = await self.client.post(
                "/api/v1/descargar",
                {"genero": genero, "cantidad": cantidad, "formato": formato}
            )
            return response.data
        except Exception as e:
            logger.error(f"Error en descarga: {e}")
            return None
    
    async def listar_descargas(self) -> Optional[Dict]:
        """Listar descargas"""
        try:
            response = await self.client.get("/api/v1/descargas")
            return response.data
        except Exception as e:
            logger.error(f"Error listando descargas: {e}")
            return None


# Instancia global
api_client = APIClient()
