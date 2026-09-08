@echo off
chcp 65001 >nul
title MusiFlix - Suite de pruebas

call "%~dp0_entorno.bat"
if errorlevel 1 exit /b 1

echo ============================================================
echo    MusiFlix - Suite de pruebas
echo ============================================================
echo.

echo Instalando dependencias si hace falta...
%PY% -m pip install -r requirements.txt -q

echo.
echo [1/2] Tests del motor (motor\tests, via pytest)...
echo.
%PY% -m pytest motor/tests
if errorlevel 1 (
  echo.
  echo [ERROR] Fallaron los tests del motor.
  pause
  exit /b 1
)

echo.
echo [2/2] Chequeos del bot (test_bot.py)...
echo.
%PY% test_bot.py

pause
