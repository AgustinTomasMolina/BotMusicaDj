@echo off
REM Iniciar API REST del Bot de Música

echo.
echo ════════════════════════════════════════════════════════════════
echo   🎵 BOT DE MÚSICA - API REST
echo ════════════════════════════════════════════════════════════════
echo.

cd /d "c:\Users\AgusT\OneDrive\Escritorio\Bot MUSICA"

echo Verificando dependencias...
python -c "import fastapi; import uvicorn" 2>nul
if errorlevel 1 (
    echo ⚠️  Instalando dependencias...
    python -m pip install fastapi uvicorn -q
)

echo.
echo ✅ Iniciando API REST en http://localhost:8000
echo.
echo 📚 Documentación interactiva: http://localhost:8000/docs
echo 🔧 ReDoc: http://localhost:8000/redoc
echo.
echo Presiona Ctrl+C para detener
echo.

python api.py
