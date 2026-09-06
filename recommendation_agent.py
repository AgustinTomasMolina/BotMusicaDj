"""
Agente Recomendador - Análisis inteligente y filtrado de canciones
"""

import logging
from typing import List, Dict, Optional
import json

logger = logging.getLogger(__name__)


class RecommendationAgent:
    """
    Agente inteligente para recomendaciones de canciones
    Utiliza análisis de metadatos, popularidad y similitud
    """

    def __init__(self):
        self.generos_cache = {}
        logger.info("🎯 Recommendation Agent inicializado")

    def calcular_puntuacion(self, cancion: Dict) -> float:
        """
        Calcula puntuación de calidad de una canción
        Basada en: popularidad, likes, comentarios
        
        Args:
            cancion: Diccionario con metadatos de la canción
        
        Returns:
            Puntuación de 0 a 100
        """
        puntuacion = 0.0
        
        # Popularidad (0-40 puntos)
        if 'popularidad' in cancion:
            puntuacion += (cancion['popularidad'] / 100) * 40
        
        # Likes (0-30 puntos)
        if 'likes' in cancion:
            likes_norm = min(cancion['likes'] / 10000, 1.0)
            puntuacion += likes_norm * 30
        
        # Comentarios (0-20 puntos)
        if 'comentarios' in cancion:
            comentarios_norm = min(cancion['comentarios'] / 1000, 1.0)
            puntuacion += comentarios_norm * 20
        
        # Bonus por fuente confiable
        if cancion.get('fuente') == 'spotify':
            puntuacion += 10
        
        return min(puntuacion, 100)

    def filtrar_por_genero(self, canciones: List[Dict], genero: str, similitud_min: float = 0.6) -> List[Dict]:
        """
        Filtra canciones similares a un género específico
        
        Args:
            canciones: Lista de canciones
            genero: Género de referencia
            similitud_min: Umbral mínimo de similitud (0-1)
        
        Returns:
            Canciones filtradas
        """
        logger.info(f"🎵 Filtrando canciones del género: {genero}")
        
        filtradas = []
        for cancion in canciones:
            # Aquí iría lógica ML más avanzada
            # Por ahora, búsqueda simple
            cancion_genero = cancion.get('genero', '').lower()
            
            if genero.lower() in cancion_genero or cancion_genero in genero.lower():
                cancion['similitud_genero'] = 1.0
                filtradas.append(cancion)
            elif genero.lower() in cancion.get('titulo', '').lower():
                cancion['similitud_genero'] = 0.7
                filtradas.append(cancion)
        
        logger.info(f"✅ Encontradas {len(filtradas)} canciones similares")
        return filtradas

    def rankear_canciones(self, canciones: List[Dict], limite: int = 15) -> List[Dict]:
        """
        Rankea canciones por calidad y retorna las mejores
        
        Args:
            canciones: Lista de canciones
            limite: Cantidad máxima a retornar
        
        Returns:
            Canciones rankeadas ordenadas por puntuación
        """
        logger.info(f"🏆 Rankeando {len(canciones)} canciones...")
        
        for cancion in canciones:
            cancion['puntuacion'] = self.calcular_puntuacion(cancion)
        
        # Ordenar por puntuación descendente
        rankeadas = sorted(canciones, key=lambda x: x['puntuacion'], reverse=True)
        
        logger.info(f"✅ Top {min(len(rankeadas), limite)} canciones seleccionadas")
        return rankeadas[:limite]

    def generar_recomendaciones(self, genero: str, canciones_disponibles: List[Dict], cantidad: int = 15) -> List[Dict]:
        """
        Genera lista de recomendaciones personalizada
        
        Args:
            genero: Género musical
            canciones_disponibles: Canciones para analizar
            cantidad: Cantidad de recomendaciones
        
        Returns:
            Lista de canciones recomendadas
        """
        logger.info(f"💡 Generando {cantidad} recomendaciones de {genero}...")
        
        # 1. Filtrar por género
        filtradas = self.filtrar_por_genero(canciones_disponibles, genero)
        
        if not filtradas:
            logger.warning("⚠️ No se encontraron canciones del género especificado")
            return []
        
        # 2. Rankear por calidad
        recomendadas = self.rankear_canciones(filtradas, limite=cantidad)
        
        logger.info(f"✅ {len(recomendadas)} recomendaciones generadas")
        return recomendadas

    def crear_preview(self, cancion: Dict) -> Dict:
        """
        Crea un preview formateado de una canción
        
        Args:
            cancion: Datos de la canción
        
        Returns:
            Preview formateado
        """
        return {
            'titulo': cancion.get('titulo', 'Desconocido'),
            'artista': cancion.get('artista', 'Desconocido'),
            'puntuacion': round(cancion.get('puntuacion', 0), 2),
            'popularidad': cancion.get('popularidad', 0),
            'fuente': cancion.get('fuente', 'Desconocida'),
            'duracion_min': f"{cancion.get('duracion', 0) // 60}:{cancion.get('duracion', 0) % 60:02d}",
        }

    def generar_reporte(self, canciones: List[Dict]) -> str:
        """
        Genera un reporte de canciones en formato texto
        
        Args:
            canciones: Lista de canciones
        
        Returns:
            Reporte formateado
        """
        reporte = "\n" + "="*80 + "\n"
        reporte += f"📊 REPORTE DE CANCIONES ({len(canciones)} total)\n"
        reporte += "="*80 + "\n\n"
        
        for idx, cancion in enumerate(canciones, 1):
            preview = self.crear_preview(cancion)
            reporte += f"{idx:2d}. {preview['titulo']}\n"
            reporte += f"    🎤 {preview['artista']}\n"
            reporte += f"    ⭐ Puntuación: {preview['puntuacion']}/100 | "
            reporte += f"Popularidad: {preview['popularidad']}/100\n"
            reporte += f"    📍 Fuente: {preview['fuente']} | ⏱️ Duración: {preview['duracion_min']}\n"
            reporte += "\n"
        
        reporte += "="*80 + "\n"
        return reporte


# Instancia global
recommendation_agent = RecommendationAgent()
