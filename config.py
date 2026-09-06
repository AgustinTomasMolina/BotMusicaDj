"""
Music Bot - Proyecto Principal
Sistema automatizado para buscar, descargar y recomendar música
"""

import os
from pathlib import Path
import logging
from dotenv import load_dotenv

# Configuración de variables de entorno
load_dotenv()

# Rutas base
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
LOGS_DIR = BASE_DIR / "logs"
CONFIG_DIR = BASE_DIR / "config"

# Crear directorios si no existen
DOWNLOADS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_DIR / 'bot.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Configuración de APIs
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET', '')
SOUNDCLOUD_API_KEY = os.getenv('SOUNDCLOUD_API_KEY', '')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY', '')

# Configuración de descargas
DOWNLOAD_FORMATS = ['wav', 'aiff', 'flac', 'mp3']
FORMAT_PRIORITY = {
    'wav': 1,
    'aiff': 2,
    'flac': 3,
    'mp3': 4
}

# Database
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./music_bot.db')

# Discord Bot
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN', '')

logger.info("✅ Configuración cargada correctamente")
