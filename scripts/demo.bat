@echo off
chcp 65001 >nul
title MusiFlix - Demo interactivo

call "%~dp0_entorno.bat"
if errorlevel 1 exit /b 1

echo ============================================================
echo    MusiFlix - Demo interactivo
echo ============================================================
echo.

%PY% quickstart.py

pause
