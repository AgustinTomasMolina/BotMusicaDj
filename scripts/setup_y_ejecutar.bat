@echo off
chcp 65001 >nul
title MusiFlix - Instalacion y arranque

call "%~dp0_entorno.bat"
if errorlevel 1 exit /b 1

echo ============================================================
echo    MusiFlix - Instalacion automatica
echo ============================================================
echo.

echo [1/4] Verificando Python...
%PY% --version
if errorlevel 1 (
    echo [ERROR] Python no esta disponible. Instala Python 3.11+
    pause
    exit /b 1
)
echo.

echo [2/4] Actualizando pip...
%PY% -m pip install --upgrade pip -q
echo.

echo [3/4] Instalando dependencias desde requirements.txt...
%PY% -m pip install -r requirements.txt
echo.

echo [4/4] Corriendo los tests del motor...
%PY% -m pytest motor/tests

echo.
echo ============================================================
echo    Instalacion completada
echo ============================================================
echo.
echo Como seguir:
echo   scripts\iniciar_web.bat           Servidor web + navegador
echo   scripts\iniciar_api.bat           Solo la API (uvicorn server:app)
echo   scripts\iniciar_discord_bot.bat   Bot de Discord
echo   scripts\demo.bat                  Demo interactivo
echo.
echo Documentacion: README.md
echo.
pause
