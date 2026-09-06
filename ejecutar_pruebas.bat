@echo off
REM Ejecutar pruebas del Bot

echo.
echo ════════════════════════════════════════════════════════════════
echo   🧪 BOT DE MÚSICA - SUITE DE PRUEBAS
echo ════════════════════════════════════════════════════════════════
echo.

cd /d "c:\Users\AgusT\OneDrive\Escritorio\Bot MUSICA"

echo Instalando dependencias si es necesario...
python -m pip install -r requirements.txt -q

echo.
echo Ejecutando pruebas...
echo.

python test_bot.py

pause
