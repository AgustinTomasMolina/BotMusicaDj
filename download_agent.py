"""
Agente de Descarga - Responsable de descargar canciones en múltiples formatos
"""

import logging
import os
from typing import List, Dict, Optional
from pathlib import Path
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class DownloadAgent:
    """
    Agente inteligente para descarga de canciones
    Maneja múltiples formatos con prioridad automática
    """

    # Prioridad de formatos: menor número = mayor prioridad
    FORMATO_PRIORIDAD = {
        'wav': 1,
        'aiff': 2,
        'flac': 3,
        'mp3': 4
    }

    def __init__(self, download_dir: str = "./downloads"):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True, parents=True)
        self.max_workers = 5  # Descargas paralelas
        logger.info(f"📥 Download Agent inicializado - Directorio: {self.download_dir}")

    def _get_mejor_formato(self, formatos_disponibles: List[str]) -> Optional[str]:
        """
        Selecciona el mejor formato disponible según prioridad
        
        Args:
            formatos_disponibles: Lista de formatos disponibles
        
        Returns:
            Formato seleccionado o None
        """
        formatos_validos = [f for f in formatos_disponibles if f.lower() in self.FORMATO_PRIORIDAD]
        
        if not formatos_validos:
            return None
        
        # Ordena por prioridad y retorna el primero
        mejor = min(formatos_validos, key=lambda x: self.FORMATO_PRIORIDAD[x.lower()])
        logger.info(f"✅ Formato seleccionado: {mejor} (de {len(formatos_validos)} opciones)")
        return mejor

    def descargar_youtube(self, video_url: str, formato: str = 'mp3', titulo: str = "cancion") -> Optional[str]:
        """
        Descargar canción de YouTube
        
        Args:
            video_url: URL de YouTube
            formato: Formato de salida
            titulo: Nombre del archivo
        
        Returns:
            Ruta del archivo descargado o None
        """
        try:
            import yt_dlp
            
            # Configurar opciones según formato
            audio_format = {
                'wav': 'wav',
                'aiff': 'aiff',
                'flac': 'flac',
                'mp3': 'mp3'
            }.get(formato.lower(), 'mp3')
            
            output_path = self.download_dir / f"{titulo}.%(ext)s"
            
            ydl_opts = {
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': audio_format,
                    'preferredquality': '192',
                }],
                'outtmpl': str(output_path),
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"⬇️  Descargando: {titulo} ({formato})...")
                ydl.download([video_url])
            
            archivo_final = self.download_dir / f"{titulo}.{audio_format}"
            if archivo_final.exists():
                logger.info(f"✅ Descargado exitosamente: {archivo_final}")
                return str(archivo_final)
            else:
                logger.error(f"❌ No se encontró archivo descargado")
                return None
        
        except Exception as e:
            logger.error(f"❌ Error descargando de YouTube: {e}")
            return None

    def descargar_spotify(self, track_id: str, titulo: str = "cancion") -> Optional[str]:
        """
        Descargar desde Spotify (usando yt-dlp como fallback)
        Nota: Spotify tiene restricciones de DRM, usar search + YouTube
        """
        logger.warning("⚠️ Spotify tiene DRM. Buscando en YouTube...")
        return None

    async def descargar_multiples(self, canciones: List[Dict], formato: str = 'mp3', limit: int = 15) -> List[Dict]:
        """
        Descargar múltiples canciones en paralelo
        
        Args:
            canciones: Lista de canciones con URL y metadatos
            formato: Formato preferido
            limit: Cantidad máxima de descargas
        
        Returns:
            Lista de canciones descargadas con rutas
        """
        logger.info(f"📦 Iniciando descarga de {min(len(canciones), limit)} canciones...")
        
        descargadas = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            
            for idx, cancion in enumerate(canciones[:limit]):
                if cancion['fuente'] == 'youtube':
                    future = executor.submit(
                        self.descargar_youtube,
                        cancion['url'],
                        formato,
                        f"{idx+1:02d}_{cancion['titulo'][:30]}"
                    )
                    futures.append((future, cancion))
            
            # Recopilar resultados
            for future, cancion in futures:
                try:
                    ruta = future.result(timeout=300)  # 5 minutos máximo por descarga
                    if ruta:
                        cancion['ruta_local'] = ruta
                        cancion['estado'] = 'completada'
                        descargadas.append(cancion)
                    else:
                        cancion['estado'] = 'fallida'
                except Exception as e:
                    logger.error(f"❌ Error en descarga: {e}")
                    cancion['estado'] = 'fallida'
        
        logger.info(f"✅ Descarga completada: {len(descargadas)} canciones exitosas")
        return descargadas

    def listar_descargas(self) -> List[Dict]:
        """Listar todos los archivos descargados"""
        archivos = []
        
        try:
            for archivo in self.download_dir.glob('*'):
                if archivo.is_file():
                    info = {
                        'nombre': archivo.name,
                        'tamaño_mb': archivo.stat().st_size / (1024 * 1024),
                        'ruta': str(archivo),
                        'extension': archivo.suffix[1:]
                    }
                    archivos.append(info)
            
            logger.info(f"📊 Total de archivos descargados: {len(archivos)}")
            return archivos
        
        except Exception as e:
            logger.error(f"❌ Error listando descargas: {e}")
            return []


# Instancia global
download_agent = DownloadAgent()
