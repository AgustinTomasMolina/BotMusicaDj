# 📋 GUÍA DE INSTALACIÓN Y USO

## 🎯 Resumen del Proyecto

Bot automatizado para **buscar, descargar y recomendar música** en múltiples formatos con:
- ✅ Backend Python (Agentes inteligentes)
- ✅ API REST (FastAPI)
- 🔄 Frontend Web (Node.js + React)
- 🔄 Discord Bot

---

## 📦 Requisitos Previos

- Python 3.8+
- Node.js 16+ (para frontend)
- pip y npm

---

## 🚀 Instalación Rápida

### 1. Backend Python

```bash
# Navegar a carpeta del proyecto
cd "Bot MUSICA"

# Crear ambiente virtual (opcional pero recomendado)
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Mac/Linux

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales
```

### 2. API REST

```bash
# Iniciar API
python api.py

# Accesible en: http://localhost:8000
# Docs: http://localhost:8000/docs
```

### 3. Discord Bot

```bash
# Editar .env con DISCORD_TOKEN

# Iniciar bot
python discord_bot.py
```

### 4. Frontend Web (Próximo)

```bash
# Crear proyecto React
npx create-react-app frontend

# Instalar dependencias
cd frontend
npm install axios react-icons react-router-dom

# Iniciar frontend
npm start
```

---

## 📝 Configuración de .env

```env
# APIs
SPOTIFY_CLIENT_ID=your_id_here
SPOTIFY_CLIENT_SECRET=your_secret_here
YOUTUBE_API_KEY=your_key_here

# Discord
DISCORD_TOKEN=your_discord_token_here

# Database
DATABASE_URL=sqlite:///./music_bot.db

# General
DEBUG=True
LOG_LEVEL=INFO
```

### Obtener Credenciales

#### Spotify
1. Ir a: https://developer.spotify.com
2. Crear aplicación
3. Copiar Client ID y Secret

#### YouTube
1. Console: https://console.cloud.google.com
2. Habilitar: YouTube Data API v3
3. Crear API Key

#### Discord
1. Discord Developer Portal: https://discord.com/developers/applications
2. Crear New Application
3. Ir a Bot → Add Bot
4. Copiar Token

---

## 🧪 Pruebas

```bash
# Ejecutar suite de pruebas
python test_bot.py

# Quick start interactivo
python quickstart.py
```

---

## 💻 Uso

### Desde Python

```python
from main import MusicBot

bot = MusicBot()

# Buscar
canciones = bot.buscar_y_recomendar("Techno", cantidad=15)

# Listar descargas
bot.listar_descargas()
```

### API REST

```bash
# Buscar
curl -X POST "http://localhost:8000/api/v1/buscar" \
  -H "Content-Type: application/json" \
  -d '{"query": "Techno", "limite": 10}'

# Recomendar
curl -X POST "http://localhost:8000/api/v1/recomendar" \
  -H "Content-Type: application/json" \
  -d '{"genero": "House", "cantidad": 15}'

# Descargar
curl -X POST "http://localhost:8000/api/v1/descargar" \
  -H "Content-Type: application/json" \
  -d '{"genero": "Trance", "cantidad": 15, "formato": "wav"}'
```

### Discord

```
/buscar query:Techno limite:10
/genero genero:House cantidad:15
/descargar genero:Trance cantidad:15 formato:wav
/descargas
/info
/ayuda
```

---

## 📂 Estructura del Proyecto

```
Bot MUSICA/
├── main.py                    # Bot principal
├── api.py                     # API REST FastAPI
├── discord_bot.py             # Discord Bot
├── search_agent.py            # Búsqueda
├── download_agent.py          # Descargas
├── recommendation_agent.py    # Recomendaciones
├── database.py                # Base de datos
├── config.py                  # Configuración
├── requirements.txt           # Dependencias Python
├── .env.example              # Variables de entorno
├── downloads/                # Canciones descargadas
├── logs/                     # Archivos de log
├── frontend/                 # Frontend React (próximamente)
└── README.md                 # Este archivo
```

---

## 🎵 Formatos Soportados

El bot descargará automáticamente en este orden de prioridad:

1. **WAV** ⭐⭐⭐ - Mejor calidad sin pérdida
2. **AIFF** ⭐⭐ - Formato profesional
3. **FLAC** ⭐⭐ - Comprimido sin pérdida
4. **MP3** ⭐ - Última opción con pérdida

Si no está disponible en WAV, intenta AIFF, luego FLAC, y finalmente MP3.

---

## 🐛 Solución de Problemas

### Error: "No module named 'spotipy'"
```bash
pip install spotipy
```

### Error: "No module named 'fastapi'"
```bash
pip install fastapi uvicorn
```

### Error: "Credenciales de Spotify inválidas"
- Verifica que SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET sean correctos
- Reinicia el programa

### Discord Bot no responde
- Verifica que DISCORD_TOKEN sea válido
- Comprueba permisos del bot en el servidor

### API no accesible
- Asegúrate de ejecutar: `python api.py`
- Accede a: http://localhost:8000
- Docs: http://localhost:8000/docs

---

## 📊 Funciones de los Agentes

### 🔍 Search Agent
- Busca en Spotify, YouTube, SoundCloud
- Fallback automático entre fuentes
- Deduplicación de resultados

### 📥 Download Agent
- Descargas paralelas (hasta 5 simultáneamente)
- Prioridad automática de formatos
- Gestión de almacenamiento

### 💡 Recommendation Agent
- Scoring basado en popularidad, likes, comentarios
- Análisis de similitud por género
- Ranking inteligente

---

## 🚀 Próximas Características

- [ ] Frontend Web completo
- [ ] Análisis ML avanzado
- [ ] Caché y persistencia
- [ ] Webhook para descargas
- [ ] Historial de usuario
- [ ] Playlists personalizadas

---

## 📄 Licencia

Open Source - Úsalo libremente

---

## 🤝 Soporte

¿Problemas o sugerencias? Contáctame

---

**Status**: ✅ Fase 1-2 Completadas | 🔄 Frontend en progreso

Última actualización: 2026-06-23
