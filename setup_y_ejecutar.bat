@echo off
REM Bot de Música - Script de instalación y ejecución automatizado

echo.
echo ════════════════════════════════════════════════════════════════
echo   🎵 BOT DE MÚSICA INTELIGENTE - INSTALACIÓN AUTOMÁTICA
echo ════════════════════════════════════════════════════════════════
echo.

cd /d "c:\Users\AgusT\OneDrive\Escritorio\Bot MUSICA"

echo [1/5] Verificando Python...
python --version
if errorlevel 1 (
    echo ❌ Python no está instalado. Por favor instala Python 3.8+
    pause
    exit /b 1
)

echo ✓ Python encontrado
echo.

echo [2/5] Actualizando pip...
python -m pip install --upgrade pip -q
echo ✓ pip actualizado

echo.
echo [3/5] Instalando dependencias principales...
python -m pip install python-dotenv requests -q
echo ✓ Dependencias base instaladas

echo.
echo [4/5] Instalando APIs de música...
python -m pip install spotipy yt-dlp -q
echo ✓ APIs instaladas

echo.
echo [5/5] Instalando FastAPI y utilidades...
python -m pip install fastapi uvicorn sqlalchemy pydantic asyncio-contextmanager colorama tqdm -q
echo ✓ Todas las dependencias instaladas

echo.
echo ════════════════════════════════════════════════════════════════
echo   ✅ INSTALACIÓN COMPLETADA
echo ════════════════════════════════════════════════════════════════
echo.

echo Ejecutando pruebas del sistema...
echo.

python test_bot.py

echo.
echo ════════════════════════════════════════════════════════════════
echo   🚀 LISTO PARA USAR
echo ════════════════════════════════════════════════════════════════
echo.
echo Opciones:
echo   1. python api.py              → Iniciar API REST
echo   2. python discord_bot.py      → Iniciar Discord Bot
echo   3. python quickstart.py       → Demo interactivo
echo   4. python main.py             → Bot principal
echo.
echo Documentación: README.md
echo.
pause
