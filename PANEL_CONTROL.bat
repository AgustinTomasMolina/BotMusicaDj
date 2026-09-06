@echo off
REM 🎵 BOT DE MÚSICA - PANEL DE CONTROL PRINCIPAL

:menu
cls
echo.
echo ════════════════════════════════════════════════════════════════
echo   🎵 BOT DE MÚSICA INTELIGENTE - PANEL DE CONTROL
echo ════════════════════════════════════════════════════════════════
echo.
echo Selecciona una opción:
echo.
echo   1. 🚀 Instalación Completa (primera vez)
echo   2. 📚 API REST (http://localhost:8000)
echo   3. 🤖 Discord Bot
echo   4. 🎮 Demo Interactivo
echo   5. 🧪 Ejecutar Pruebas
echo   6. 📂 Abrir carpeta descargas
echo   7. 📖 Documentación
echo   8. ❌ Salir
echo.
set /p opcion="Ingresa tu opción (1-8): "

if "%opcion%"=="1" goto install
if "%opcion%"=="2" goto api
if "%opcion%"=="3" goto discord
if "%opcion%"=="4" goto demo
if "%opcion%"=="5" goto tests
if "%opcion%"=="6" goto downloads
if "%opcion%"=="7" goto docs
if "%opcion%"=="8" goto exit
goto menu

:install
cls
echo.
echo Iniciando instalación completa...
echo.
call setup_y_ejecutar.bat
goto menu

:api
cls
echo.
echo Iniciando API REST...
echo.
call iniciar_api.bat
goto menu

:discord
cls
echo.
echo Iniciando Discord Bot...
echo.
call iniciar_discord_bot.bat
goto menu

:demo
cls
echo.
echo Iniciando Demo...
echo.
call demo.bat
goto menu

:tests
cls
echo.
echo Ejecutando pruebas...
echo.
call ejecutar_pruebas.bat
goto menu

:downloads
cls
echo.
start explorer downloads
timeout /t 2 /nobreak
goto menu

:docs
cls
echo.
echo Abriendo documentación...
echo.
start notepad README.md
timeout /t 2 /nobreak
goto menu

:exit
echo.
echo ¡Hasta luego! 🎵
echo.
pause
exit /b 0
