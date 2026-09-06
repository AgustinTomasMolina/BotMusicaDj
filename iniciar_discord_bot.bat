@echo off
REM Iniciar Discord Bot del Bot de Música

echo.
echo ════════════════════════════════════════════════════════════════
echo   🤖 BOT DE MÚSICA - DISCORD BOT
echo ════════════════════════════════════════════════════════════════
echo.

cd /d "c:\Users\AgusT\OneDrive\Escritorio\Bot MUSICA"

echo Verificando dependencias...
python -c "import discord" 2>nul
if errorlevel 1 (
    echo ⚠️  Instalando Discord.py...
    python -m pip install discord.py -q
)

echo.
echo ✅ Iniciando Discord Bot
echo.
echo Comandos disponibles:
echo   /buscar
echo   /genero
echo   /descargar
echo   /descargas
echo   /info
echo   /ayuda
echo.
echo Presiona Ctrl+C para detener
echo.

python discord_bot.py
