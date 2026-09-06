"""
Agente de Búsqueda - Responsable de buscar canciones en múltiples fuentes
"""

import logging
import json
import re
from typing import List, Dict, Optional
try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    SPOTIFY_AVAILABLE = True
except ImportError:
    SPOTIFY_AVAILABLE = False

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

logger = logging.getLogger(__name__)


class SearchAgent:
    """
    Agente inteligente para búsqueda de canciones
    Integra múltiples fuentes: Spotify, YouTube, SoundCloud (web scraping)
    """

    def __init__(self, spotify_client_id='', spotify_client_secret='', soundcloud_client_id=''):
        self.spotify_client = self._init_spotify(spotify_client_id, spotify_client_secret)
        self.soundcloud_client_id = soundcloud_client_id
        self.fuentes_disponibles = ['spotify', 'youtube', 'soundcloud']
        logger.info("🔍 Search Agent inicializado con 3 fuentes (SoundCloud vía web scraping)")

    def _init_spotify(self, client_id, client_secret):
        """Inicializar cliente de Spotify"""
        try:
            if not SPOTIFY_AVAILABLE:
                logger.warning("⚠️ spotipy no está instalado")
                return None
            
            if client_id and client_secret:
                auth_manager = SpotifyClientCredentials(
                    client_id=client_id,
                    client_secret=client_secret
                )
                return spotipy.Spotify(auth_manager=auth_manager)
            else:
                logger.warning("⚠️ Credenciales de Spotify no configuradas")
                return None
        except Exception as e:
            logger.error(f"❌ Error al conectar con Spotify: {e}")
            return None

    def buscar_en_spotify(self, query: str, tipo: str = 'track', limit: int = 10) -> List[Dict]:
        """Buscar canciones en Spotify"""
        try:
            if not self.spotify_client:
                logger.warning("⚠️ Cliente de Spotify no disponible")
                return []

            resultados = self.spotify_client.search(q=query, type=tipo, limit=limit)
            
            canciones = []
            if tipo == 'track':
                for track in resultados['tracks']['items']:
                    try:
                        cancion = {
                            'titulo': track['name'],
                            'artista': ', '.join([a['name'] for a in track['artists']]),
                            'duracion': track['duration_ms'] // 1000,
                            'popularidad': track.get('popularity', 0),
                            'url': track['external_urls']['spotify'],
                            'fuente': 'spotify',
                            'preview_url': track.get('preview_url'),
                            'id': track['id'],
                            'thumbnail': (track.get('album', {}).get('images') or [{}])[0].get('url'),
                        }
                        canciones.append(cancion)
                    except KeyError as e:
                        logger.debug(f"ℹ️ Track incompleto en Spotify, saltando: {e}")
                        continue
            
            logger.info(f"✅ Encontradas {len(canciones)} canciones en Spotify")
            return canciones
        
        except Exception as e:
            logger.error(f"❌ Error al buscar en Spotify: {e}")
            return []

    def buscar_en_youtube(self, query: str, limit: int = 10) -> List[Dict]:
        """Buscar canciones en YouTube (usando yt-dlp)"""
        try:
            if not YTDLP_AVAILABLE:
                logger.warning("⚠️ yt-dlp no está instalado")
                return []
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                # Que un video roto/restringido no tumbe la búsqueda entera, y el
                # cliente 'android' esquiva el age-gate ("Sign in to confirm your age").
                'ignoreerrors': True,
                'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                resultados = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

            canciones = []
            for video in (resultados or {}).get('entries') or []:
                if not video:  # ignoreerrors deja None en los que fallaron
                    continue
                if len(canciones) >= limit:
                    break
                cancion = {
                    'titulo': video['title'],
                    'artista': video.get('uploader', 'Desconocido'),
                    'duracion': video.get('duration', 0),
                    'url': video['webpage_url'],
                    'fuente': 'youtube',
                    'video_id': video['id'],
                    'thumbnail': video.get('thumbnail') or f"https://i.ytimg.com/vi/{video['id']}/hqdefault.jpg",
                }
                canciones.append(cancion)
            
            logger.info(f"✅ Encontradas {len(canciones)} canciones en YouTube")
            return canciones
        
        except Exception as e:
            logger.error(f"❌ Error al buscar en YouTube: {e}")
            return []

    def buscar_en_soundcloud(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Buscar canciones en SoundCloud usando yt-dlp
        No requiere Artist Pro account - yt-dlp maneja SoundCloud automáticamente
        """
        try:
            if not YTDLP_AVAILABLE:
                logger.warning("⚠️ yt-dlp no está instalado, saltando SoundCloud")
                return []
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': 'in_playlist',
            }
            
            # yt-dlp puede buscar directamente en SoundCloud
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    # Buscar en SoundCloud usando URL de búsqueda
                    search_url = f"scsearch{limit}:{query}"
                    resultados = ydl.extract_info(search_url, download=False)
                except:
                    logger.debug(f"ℹ️ yt-dlp search no disponible, intentando API...")
                    return []
            
            canciones = []
            if 'entries' in resultados:
                for video in resultados['entries'][:limit]:
                    if video and 'title' in video:
                        cancion = {
                            'titulo': video['title'],
                            'artista': video.get('uploader', 'Desconocido'),
                            'duracion': video.get('duration', 0),
                            'url': video.get('url') or video.get('webpage_url', ''),
                            'fuente': 'soundcloud',
                            'video_id': video.get('id', ''),
                            'thumbnail': video.get('thumbnail'),
                        }
                        if cancion['url']:  # Solo agregar si tiene URL válida
                            canciones.append(cancion)
            
            if canciones:
                logger.info(f"✅ Encontradas {len(canciones)} canciones en SoundCloud vía yt-dlp")
            return canciones
        
        except Exception as e:
            logger.debug(f"ℹ️ SoundCloud search con yt-dlp no disponible: {e}")
            return []

    def _scrape_soundcloud_web(self, query: str, limit: int = 10) -> List[Dict]:
        """Web scraping alternativo de SoundCloud (deprecado - ahora usamos yt-dlp)"""
        logger.debug("ℹ️ Web scraping deprecado - usando yt-dlp para SoundCloud")
        return []

    def buscar_con_fallback(self, query: str, limit: int = 10) -> List[Dict]:
        """Buscar en múltiples fuentes con fallback automático"""
        logger.info(f"🔄 Iniciando búsqueda en 3 fuentes: {query}")
        
        todas_canciones = []
        
        # Intento 1: Spotify
        canciones_spotify = self.buscar_en_spotify(query, limit=limit)
        todas_canciones.extend(canciones_spotify)
        
        # Intento 2: SoundCloud (sin necesidad de Pro account)
        if len(todas_canciones) < limit:
            logger.info(f"⚠️ Insuficientes en Spotify, intentando SoundCloud...")
            canciones_sc = self.buscar_en_soundcloud(query, limit=limit - len(todas_canciones))
            todas_canciones.extend(canciones_sc)
        
        # Intento 3: YouTube
        if len(todas_canciones) < limit:
            logger.info(f"⚠️ Insuficientes, intentando YouTube...")
            canciones_youtube = self.buscar_en_youtube(query, limit=limit - len(todas_canciones))
            todas_canciones.extend(canciones_youtube)
        
        logger.info(f"✅ Búsqueda completada: {len(todas_canciones)} canciones encontradas")
        return todas_canciones[:limit]

    def buscar_por_genero(self, genero: str, limit: int = 15) -> List[Dict]:
        """Buscar canciones por género"""
        logger.info(f"🎵 Buscando canciones del género: {genero}")
        return self.buscar_con_fallback(genero, limit=limit)


# Instancia global
search_agent = SearchAgent()

