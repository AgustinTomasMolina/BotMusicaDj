"""
Script Principal - Inicializa y ejecuta el Bot de Música
"""

import logging
import os
from dotenv import load_dotenv
from search_agent import SearchAgent
from download_agent import DownloadAgent
from recommendation_agent import RecommendationAgent

# Cargar variables de entorno
load_dotenv()

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MusicBot:
    """
    Bot principal que orquesta los agentes de búsqueda, descarga y recomendación
    """

    def __init__(self):
        logger.info("🚀 Inicializando Music Bot...")
        
        # Inicializar agentes
        self.search_agent = SearchAgent(
            os.getenv('SPOTIFY_CLIENT_ID', ''),
            os.getenv('SPOTIFY_CLIENT_SECRET', '')
        )
        self.download_agent = DownloadAgent(download_dir="./downloads")
        self.recommendation_agent = RecommendationAgent()
        
        logger.info("✅ Music Bot listo!")

    def buscar_y_recomendar(self, genero: str, cantidad: int = 15) -> list:
        """
        Busca canciones de un género y devuelve las mejores recomendaciones
        
        Args:
            genero: Género musical
            cantidad: Cantidad de canciones a recomendar
        
        Returns:
            Lista de canciones recomendadas
        """
        logger.info(f"\n📀 INICIANDO BÚSQUEDA Y RECOMENDACIÓN: {genero.upper()}")
        logger.info(f"   Cantidad solicitada: {cantidad}\n")
        
        # 1. Búsqueda
        logger.info("📍 Fase 1: Buscando canciones...")
        canciones = self.search_agent.buscar_por_genero(genero, limit=cantidad * 2)
        
        if not canciones:
            logger.warning("❌ No se encontraron canciones")
            return []
        
        # 2. Recomendación
        logger.info(f"\n📍 Fase 2: Analizando y recomendando ({len(canciones)} candidatos)...")
        recomendadas = self.recommendation_agent.generar_recomendaciones(
            genero, canciones, cantidad
        )
        
        if recomendadas:
            # Mostrar reporte
            reporte = self.recommendation_agent.generar_reporte(recomendadas)
            logger.info(reporte)
        
        return recomendadas

    async def descargar_recomendadas(self, genero: str, cantidad: int = 15, formato: str = 'mp3'):
        """
        Descarga canciones recomendadas
        
        Args:
            genero: Género musical
            cantidad: Cantidad a descargar
            formato: Formato preferido
        """
        logger.info(f"\n📀 DESCARGANDO {cantidad} CANCIONES DE {genero.upper()}\n")
        
        # Obtener recomendaciones
        recomendadas = self.buscar_y_recomendar(genero, cantidad)
        
        if not recomendadas:
            logger.error("❌ No hay canciones para descargar")
            return
        
        # 3. Descarga
        logger.info(f"\n📍 Fase 3: Descargando canciones ({formato})...")
        
        # Convertir a async
        import asyncio
        descargadas = await self.download_agent.descargar_multiples(
            recomendadas, formato=formato, limit=cantidad
        )
        
        logger.info(f"\n✅ PROCESO COMPLETADO")
        logger.info(f"   ✓ {len(descargadas)} canciones descargadas")
        logger.info(f"   📁 Ubicación: {self.download_agent.download_dir}")

    def listar_descargas(self):
        """Lista las canciones descargadas"""
        logger.info("\n📂 ARCHIVOS DESCARGADOS:\n")
        
        archivos = self.download_agent.listar_descargas()
        
        if not archivos:
            logger.info("   (No hay descargas)")
            return
        
        total_mb = 0
        for idx, archivo in enumerate(archivos, 1):
            logger.info(f"{idx:2d}. {archivo['nombre']}")
            logger.info(f"    💾 {archivo['tamaño_mb']:.2f} MB\n")
            total_mb += archivo['tamaño_mb']
        
        logger.info(f"   Total: {total_mb:.2f} MB")


# Ejemplo de uso
def main():
    """Función principal de demostración"""
    bot = MusicBot()
    
    # Ejemplo 1: Buscar y recomendar
    logger.info("\n" + "="*80)
    logger.info("DEMOSTRACIÓN: Bot de Música")
    logger.info("="*80)
    
    # Buscar 15 canciones de Techno Trance
    recomendadas = bot.buscar_y_recomendar("Techno Trance", cantidad=5)
    
    logger.info("\n✅ Fase 1 completada: Setup Backend Python + Agentes")


if __name__ == "__main__":
    main()
