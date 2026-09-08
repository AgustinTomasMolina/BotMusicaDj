@echo off
chcp 65001 >nul
title MusiFlix - Panel de control

REM Menu que agrupa los demas scripts. Vive en scripts\ junto a ellos, asi que
REM los llama con %~dp0 (ruta absoluta de esta carpeta), no con rutas relativas:
REM cada script hace su propio "cd" a la raiz del repo.
set "AQUI=%~dp0"
set "RAIZ=%~dp0.."

:menu
cls
echo.
echo ============================================================
echo    MusiFlix - Panel de control
echo ============================================================
echo.
echo   1. Instalacion completa (primera vez)
echo   2. Servidor web + navegador  (http://localhost:8000)
echo   3. Solo la API REST          (uvicorn server:app)
echo   4. Discord Bot
echo   5. Demo interactivo
echo   6. Ejecutar pruebas
echo   7. Recompilar el frontend
echo   8. Abrir la carpeta de descargas
echo   9. Ver la documentacion (README)
echo   0. Salir
echo.
set "opcion="
set /p opcion="Elegi una opcion (0-9): "

if "%opcion%"=="1" goto install
if "%opcion%"=="2" goto web
if "%opcion%"=="3" goto api
if "%opcion%"=="4" goto discord
if "%opcion%"=="5" goto demo
if "%opcion%"=="6" goto tests
if "%opcion%"=="7" goto frontend
if "%opcion%"=="8" goto downloads
if "%opcion%"=="9" goto docs
if "%opcion%"=="0" goto salir
goto menu

:install
cls
call "%AQUI%setup_y_ejecutar.bat"
goto menu

:web
cls
call "%AQUI%iniciar_web.bat"
goto menu

:api
cls
call "%AQUI%iniciar_api.bat"
goto menu

:discord
cls
call "%AQUI%iniciar_discord_bot.bat"
goto menu

:demo
cls
call "%AQUI%demo.bat"
goto menu

:tests
cls
call "%AQUI%ejecutar_pruebas.bat"
goto menu

:frontend
cls
call "%AQUI%recompilar-frontend.bat"
goto menu

:downloads
cls
if not exist "%RAIZ%\downloads" mkdir "%RAIZ%\downloads"
start "" explorer "%RAIZ%\downloads"
timeout /t 2 /nobreak >nul
goto menu

:docs
cls
start "" notepad "%RAIZ%\README.md"
timeout /t 2 /nobreak >nul
goto menu

:salir
echo.
echo Hasta luego.
echo.
exit /b 0
