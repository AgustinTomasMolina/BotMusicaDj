"""
🎵 BOT DE MÚSICA - GUÍA RÁPIDA DE USO
Versión 1.0.0
"""

print("""
╔════════════════════════════════════════════════════════════════════════════╗
║                  🎵 BOT DE MÚSICA INTELIGENTE 🎵                          ║
║                     GUÍA RÁPIDA DE INICIO (5 MINS)                        ║
╚════════════════════════════════════════════════════════════════════════════╝

👋 ¡HOLA! Aquí te muestro cómo empezar:

┌─────────────────────────────────────────────────────────────────────────┐
│ OPCIÓN 1: PANEL DE CONTROL GRÁFICO (LO MÁS FÁCIL) ✅                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Abre:  PANEL_CONTROL.bat                                              │
│                                                                         │
│  Este archivo te abre un menú con todas las opciones:                  │
│  • Instalar dependencias                                               │
│  • Iniciar API REST                                                    │
│  • Iniciar Discord Bot                                                 │
│  • Ver pruebas                                                         │
│  • Abrir documentación                                                 │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ OPCIÓN 2: SCRIPTS INDIVIDUALES                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  setup_y_ejecutar.bat      → Instala TODO e inicia pruebas            │
│  iniciar_api.bat           → Inicia API REST (8000)                    │
│  iniciar_discord_bot.bat   → Inicia Discord Bot                        │
│  demo.bat                  → Demo interactivo                          │
│  ejecutar_pruebas.bat      → Ejecutar tests                            │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ OPCIÓN 3: COMANDO DIRECTO (Terminal)                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  # Instalar dependencias                                               │
│  pip install -r requirements.txt                                       │
│                                                                         │
│  # API REST                                                            │
│  python api.py                                                         │
│  → Acceder a: http://localhost:8000                                    │
│                                                                         │
│  # Discord Bot                                                         │
│  python discord_bot.py                                                 │
│                                                                         │
│  # Demo                                                                │
│  python quickstart.py                                                  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 PRIMEROS PASOS:

1️⃣  Haz doble-click en: PANEL_CONTROL.bat

2️⃣  Selecciona opción 1 (Instalación Completa)
    ↓
    Esto instala todas las dependencias y ejecuta pruebas

3️⃣  Una vez instalado, selecciona:
    • Opción 2: API REST (lo recomendado)
    O
    • Opción 3: Discord Bot

4️⃣  ¡Listo! El bot está corriendo

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📍 ¿QUÉ ACCESO TENGO UNA VEZ FUNCIONANDO?

🌐 API REST (si usas opción 2)
   └─ http://localhost:8000
      • Documentación interactiva: http://localhost:8000/docs
      • Prueba endpoints desde el navegador
      • Buscar, recomendar, descargar

🤖 Discord (si usas opción 3)
   └─ Comandos:
      /buscar query:Techno
      /genero genero:House cantidad:15
      /descargar genero:Trance cantidad:15 formato:wav
      /descargas
      /info
      /ayuda

📱 Frontend Web (próximamente)
   └─ http://localhost:3000 (una vez configurado)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ IMPORTANTE: Credenciales

El bot funciona mejor con credenciales reales de APIs:

1. SPOTIFY
   • Ir a: https://developer.spotify.com
   • Crear una aplicación
   • Copiar Client ID y Secret
   • Pegar en: .env

2. DISCORD (para Discord Bot)
   • Ir a: https://discord.com/developers
   • Crear aplicación
   • Add Bot
   • Copiar Token
   • Pegar en: .env

3. YOUTUBE (opcional)
   • Ir a: https://console.cloud.google.com
   • Crear API Key
   • Pegar en: .env

⚡ El bot funciona sin estas credenciales pero con funcionalidad limitada.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🆘 SOLUCIÓN DE PROBLEMAS

❌ Error: "Python no encontrado"
   → Instala Python 3.8+ desde: https://python.org

❌ Error: "Módulo no encontrado"
   → Ejecuta: pip install -r requirements.txt

❌ API no responde
   → Asegúrate de ejecutar: python api.py

❌ Discord Bot no conecta
   → Verifica DISCORD_TOKEN en .env

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📚 DOCUMENTACIÓN

Para documentación completa, ver:
  • README.md - Guía principal
  • API_DOCS.md - Endpoints del API
  • GUIA_INSTALACION.md - Instalación detallada
  • PROYECTO_SUMMARY.md - Resumen técnico

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎉 ¡LISTO! Ahora ejecuta: PANEL_CONTROL.bat

Cualquier duda, revisa los archivos de documentación.

Versión: 1.0.0
Status: ✅ Listo para usar

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")

# Mostrar archivos creados
print("\n✅ Archivos listos para usar:\n")

import os

archivos = [
    ("PANEL_CONTROL.bat", "Panel de control gráfico ⭐ RECOMENDADO"),
    ("setup_y_ejecutar.bat", "Instalar todo + pruebas"),
    ("iniciar_api.bat", "Iniciar API REST"),
    ("iniciar_discord_bot.bat", "Iniciar Discord Bot"),
    ("demo.bat", "Demo interactivo"),
    ("ejecutar_pruebas.bat", "Ejecutar pruebas"),
    ("README.md", "Documentación"),
    ("API_DOCS.md", "Documentación API"),
]

_RAIZ = os.path.dirname(os.path.abspath(__file__))

for archivo, descripcion in archivos:
    # La carpeta del propio repo. Antes estaba clavada la de otra máquina, así que este
    # listado salía vacío en cualquier computadora que no fuera esa (tarea #5.16).
    path = os.path.join(_RAIZ, archivo)
    if os.path.exists(path):
        print(f"   ✓ {archivo:30} - {descripcion}")

print("\n🎵 ¡A disfrutar la música! 🎵\n")

input("Presiona ENTER para continuar...")
