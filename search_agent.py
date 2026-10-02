"""
Agente de Búsqueda - Responsable de buscar canciones en múltiples fuentes
"""

import logging
import json
import re
from typing import List, Dict, Optional

from fuente_errores import FuenteCaida, FuenteError, FuenteNoConfigurada

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


# Tamaños de carátula de SoundCloud, del preferido al último recurso. t500x500 alcanza para
# el tag del archivo descargado (tagger.py la embebe) y para la pantalla con densidad 2x.
_SC_THUMB_PREFERIDOS = ('t500x500', 't300x300')


def _thumbnail_soundcloud(video: dict) -> str | None:
    """Carátula de un resultado de SoundCloud.

    Con `extract_flat` yt-dlp deja `thumbnail` en None y la carátula viene solo en la lista
    `thumbnails` (mini, tiny, small, …, t300x300, t500x500, original). Leer solo `thumbnail`
    dejaba a TODOS los resultados de SoundCloud sin imagen. Medido con yt-dlp 2026.08.19.
    Sin ninguna → None (el front dibuja el placeholder, no una imagen inventada)."""
    if video.get('thumbnail'):
        return video['thumbnail']
    thumbs = [t for t in (video.get('thumbnails') or []) if isinstance(t, dict) and t.get('url')]
    if not thumbs:
        return None
    for preferido in _SC_THUMB_PREFERIDOS:
        for t in thumbs:
            if t.get('id') == preferido:
                return t['url']
    return max(thumbs, key=lambda t: t.get('width') or 0)['url']


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
        """Buscar canciones en Spotify. Sin cliente (sin credenciales o sin spotipy) lanza
        `FuenteNoConfigurada`; si Spotify falla, `FuenteCaida` (f40: antes las dos eran [], lo
        mismo que "Spotify no lo tiene")."""
        if not self.spotify_client:
            logger.debug("ℹ️ Cliente de Spotify no disponible (sin configurar)")
            raise FuenteNoConfigurada("Spotify sin credenciales")
        try:
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
            raise FuenteCaida(f"Spotify: {type(e).__name__}") from e

    def buscar_en_youtube(self, query: str, limit: int = 10) -> List[Dict]:
        """Buscar canciones en YouTube (usando yt-dlp). Sin yt-dlp lanza `FuenteNoConfigurada`;
        si la búsqueda falla, `FuenteCaida` (f40: antes devolvía [] como si no hubiera nada)."""
        if not YTDLP_AVAILABLE:
            logger.warning("⚠️ yt-dlp no está instalado")
            raise FuenteNoConfigurada("yt-dlp no está instalado")
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                # Que un video roto/restringido no tumbe la búsqueda entera, y el
                # cliente 'android' esquiva el age-gate ("Sign in to confirm your age").
                'ignoreerrors': True,
                'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
                # Búsqueda PLANA (f32): solo la página de resultados, sin abrir cada video.
                # Medido con red real: sin esto cada búsqueda resolvía los formatos de sus N
                # videos (8 s sola, 30-56 s con las 12 de parecidas en paralelo) y era la
                # mitad del tiempo de /api/parecidas_lista. Plana: ~1,3 s. Trae lo mismo que
                # se usa acá (id, título, canal, duración); la carátula sale del id.
                'extract_flat': 'in_playlist',
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                resultados = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            # Con `ignoreerrors` una búsqueda que falla entera (sin red, YouTube que no
            # contesta) no lanza: devuelve None. Una búsqueda sin resultados es un dict vacío.
            if resultados is None:
                raise FuenteCaida("YouTube no devolvió la página de resultados")

            canciones = []
            for video in (resultados or {}).get('entries') or []:
                if not video or not video.get('id') or not video.get('title'):
                    continue  # ignoreerrors deja None en los que fallaron
                if len(canciones) >= limit:
                    break
                cancion = {
                    'titulo': video['title'],
                    'artista': video.get('uploader') or video.get('channel') or 'Desconocido',
                    'duracion': video.get('duration') or 0,
                    # La entrada plana no trae webpage_url; la URL canónica sale del id (así
                    # un /shorts/ también queda como watch?v=, que es lo que entiende el resto).
                    'url': video.get('webpage_url') or f"https://www.youtube.com/watch?v={video['id']}",
                    'fuente': 'youtube',
                    'video_id': video['id'],
                    'thumbnail': video.get('thumbnail') or f"https://i.ytimg.com/vi/{video['id']}/hqdefault.jpg",
                }
                canciones.append(cancion)
            
            logger.info(f"✅ Encontradas {len(canciones)} canciones en YouTube")
            return canciones
        
        except FuenteError:
            logger.error("❌ YouTube no devolvió la página de resultados")
            raise
        except Exception as e:
            logger.error(f"❌ Error al buscar en YouTube: {e}")
            raise FuenteCaida(f"YouTube: {type(e).__name__}") from e

    def buscar_en_soundcloud(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Buscar canciones en SoundCloud usando yt-dlp
        No requiere Artist Pro account - yt-dlp maneja SoundCloud automáticamente
        """
        if not YTDLP_AVAILABLE:
            logger.warning("⚠️ yt-dlp no está instalado, saltando SoundCloud")
            raise FuenteNoConfigurada("yt-dlp no está instalado")
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': 'in_playlist',
            }
            
            # yt-dlp puede buscar directamente en SoundCloud
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Buscar en SoundCloud usando URL de búsqueda (una falla sube: es una caída)
                search_url = f"scsearch{limit}:{query}"
                resultados = ydl.extract_info(search_url, download=False)

            canciones = []
            if 'entries' in (resultados or {}):
                for video in resultados['entries'][:limit]:
                    if video and 'title' in video:
                        cancion = {
                            'titulo': video['title'],
                            'artista': video.get('uploader', 'Desconocido'),
                            'duracion': video.get('duration', 0),
                            'url': video.get('url') or video.get('webpage_url', ''),
                            'fuente': 'soundcloud',
                            'video_id': video.get('id', ''),
                            'thumbnail': _thumbnail_soundcloud(video),
                        }
                        if cancion['url']:  # Solo agregar si tiene URL válida
                            canciones.append(cancion)
            
            if canciones:
                logger.info(f"✅ Encontradas {len(canciones)} canciones en SoundCloud vía yt-dlp")
            return canciones
        
        except Exception as e:
            logger.warning(f"⚠️ SoundCloud search con yt-dlp falló: {type(e).__name__}")
            raise FuenteCaida(f"SoundCloud: {type(e).__name__}") from e

    def _scrape_soundcloud_web(self, query: str, limit: int = 10) -> List[Dict]:
        """Web scraping alternativo de SoundCloud (deprecado - ahora usamos yt-dlp)"""
        logger.debug("ℹ️ Web scraping deprecado - usando yt-dlp para SoundCloud")
        return []

    def buscar_con_fallback(self, query: str, limit: int = 10) -> List[Dict]:
        """Buscar en múltiples fuentes con fallback automático"""
        logger.info(f"🔄 Iniciando búsqueda en 3 fuentes: {query}")
        
        todas_canciones = []

        def _o_vacio(buscar, **kw):
            # Fallback "lo que haya": una fuente que no pudo buscar cuenta como vacía y se
            # pasa a la siguiente (el que necesita distinguirlo llama a buscar_en_* directo).
            try:
                return buscar(query, **kw)
            except FuenteError as e:
                logger.info(f"ℹ️ {e}: sigo con la próxima fuente")
                return []

        # Intento 1: Spotify
        todas_canciones.extend(_o_vacio(self.buscar_en_spotify, limit=limit))

        # Intento 2: SoundCloud (sin necesidad de Pro account)
        if len(todas_canciones) < limit:
            logger.info("⚠️ Insuficientes en Spotify, intentando SoundCloud...")
            todas_canciones.extend(_o_vacio(self.buscar_en_soundcloud, limit=limit - len(todas_canciones)))

        # Intento 3: YouTube
        if len(todas_canciones) < limit:
            logger.info("⚠️ Insuficientes, intentando YouTube...")
            todas_canciones.extend(_o_vacio(self.buscar_en_youtube, limit=limit - len(todas_canciones)))
        
        logger.info(f"✅ Búsqueda completada: {len(todas_canciones)} canciones encontradas")
        return todas_canciones[:limit]

    def buscar_por_genero(self, genero: str, limit: int = 15) -> List[Dict]:
        """Buscar canciones por género"""
        logger.info(f"🎵 Buscando canciones del género: {genero}")
        return self.buscar_con_fallback(genero, limit=limit)


# Instancia global
search_agent = SearchAgent()

