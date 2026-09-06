"""
Estructura de Frontend con Node.js y React
Archivo de configuración para el proyecto Frontend
"""

FRONTEND_STRUCTURE = {
    "root": "frontend/",
    "directorios": {
        "src": {
            "components": ["SearchBar.jsx", "CancionCard.jsx", "RecommendationList.jsx"],
            "pages": ["Home.jsx", "Search.jsx", "Downloads.jsx"],
            "services": ["api.js", "websocket.js"],
            "hooks": ["useSearch.js", "useDownload.js"],
            "styles": ["index.css", "components.css"],
            "App.jsx": None,
            "index.js": None
        },
        "public": ["index.html", "favicon.ico"],
        "config": ["discord_bot.js", "env.example"]
    },
    "archivos_raiz": ["package.json", ".env.example", "README.md"]
}

# Comandos para setup

SETUP_COMMANDS = {
    "crear_proyecto": "npx create-react-app frontend",
    "instalar_deps": "npm install axios react-icons react-router-dom dotenv",
    "instalar_discord": "npm install discord.js",
    "dev": "npm start",
    "build": "npm run build",
    "deploy": "npm run build && serve -s build"
}

# Dependencias de package.json

PACKAGE_JSON = {
    "name": "music-bot-frontend",
    "version": "1.0.0",
    "description": "Frontend Web para Music Bot",
    "private": True,
    "dependencies": {
        "react": "^18.2.0",
        "react-dom": "^18.2.0",
        "react-icons": "^4.11.0",
        "react-router-dom": "^6.20.0",
        "axios": "^1.6.0",
        "dotenv": "^16.3.1"
    },
    "devDependencies": {
        "react-scripts": "5.0.1"
    },
    "scripts": {
        "start": "react-scripts start",
        "build": "react-scripts build",
        "test": "react-scripts test",
        "eject": "react-scripts eject"
    }
}

print("""
🎵 ESTRUCTURA DE FRONTEND LISTA

Para crear el Frontend Web:

1. Navegar a la carpeta del proyecto:
   cd Bot MUSICA

2. Crear proyecto React:
   npx create-react-app frontend

3. Instalar dependencias:
   cd frontend
   npm install axios react-icons react-router-dom

4. Crear estructura de carpetas:
   mkdir src/components src/pages src/services src/hooks src/styles

5. Copiar archivos del frontend

6. Ejecutar en desarrollo:
   npm start

Accesible en: http://localhost:3000
""")
