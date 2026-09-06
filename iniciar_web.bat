@echo off
chcp 65001 >nul
title Bot de Musica - Servidor Web
cd /d "%~dp0"

echo ============================================================
echo    BOT DE MUSICA - Iniciando servidor web...
echo ============================================================
echo.

REM Comprobar dependencias minimas
py -c "import fastapi, uvicorn, yt_dlp, websockets" 2>nul
if errorlevel 1 (
  echo Instalando dependencias necesarias...
  py -m pip install fastapi uvicorn websockets yt-dlp spotipy python-dotenv
  echo.
)

REM Abrir el navegador despues de un momento
start "" /min cmd /c "timeout /t 3 >nul & start http://localhost:8000"

echo Servidor en: http://localhost:8000
echo (Se abrira el navegador solo. Para cerrar: Ctrl+C aqui)
echo.
py server.py

pause
