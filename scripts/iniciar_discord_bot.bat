@echo off
chcp 65001 >nul
title MusiFlix - Discord Bot

call "%~dp0_entorno.bat"
if errorlevel 1 exit /b 1

echo ============================================================
echo    MusiFlix - Discord Bot
echo ============================================================
echo.

REM discord.py no esta en requirements.txt (el bot de Discord es opcional)
%PY% -c "import discord" 2>nul
if errorlevel 1 (
  echo Instalando discord.py...
  %PY% -m pip install discord.py
  echo.
)

echo Comandos disponibles:
echo   /buscar  /genero  /descargar  /descargas  /info  /ayuda
echo.
echo Presiona Ctrl+C para detener
echo.

%PY% discord_bot.py

pause
