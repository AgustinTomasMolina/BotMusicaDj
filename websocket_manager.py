"""
Configuración de integración con Frontend
Facilita comunicación entre Node.js y Python
"""

from fastapi import WebSocket
from typing import Dict, List
import json
import logging

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Gestiona conexiones WebSocket para actualizaciones en tiempo real"""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"✓ Cliente conectado. Total: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"✓ Cliente desconectado. Total: {len(self.active_connections)}")
    
    async def broadcast(self, message: Dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"❌ Error enviando mensaje: {e}")
    
    async def enviar_progreso(self, usuario_id: str, progreso: Dict):
        mensaje = {
            "tipo": "progreso",
            "usuario_id": usuario_id,
            "datos": progreso
        }
        await self.broadcast(mensaje)


# Instancia global
ws_manager = WebSocketManager()


# Modelos para integración

class EventoDescarga:
    """Evento de descarga para enviar a frontend"""
    
    def __init__(self, cancion_id: str, titulo: str, progreso: int):
        self.cancion_id = cancion_id
        self.titulo = titulo
        self.progreso = progreso
    
    def to_dict(self) -> Dict:
        return {
            "tipo": "descarga",
            "cancion_id": self.cancion_id,
            "titulo": self.titulo,
            "progreso": self.progreso  # 0-100
        }


class EventoRecomendacion:
    """Evento de recomendación para enviar a frontend"""
    
    def __init__(self, genero: str, canciones: List[Dict]):
        self.genero = genero
        self.canciones = canciones
    
    def to_dict(self) -> Dict:
        return {
            "tipo": "recomendacion",
            "genero": self.genero,
            "canciones": self.canciones
        }
