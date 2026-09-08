@echo off
chcp 65001 >nul
title MusiFlix - Instalar dependencias

call "%~dp0_entorno.bat"
if errorlevel 1 exit /b 1

echo ============================================================
echo    MusiFlix - Instalando dependencias
echo ============================================================
echo.

%PY% -m pip install --upgrade pip

echo.
echo Instalando desde requirements.txt...
%PY% -m pip install -r requirements.txt

echo.
echo ============================================================
echo    Listo.
echo ============================================================
echo.
echo Para instalar ademas el motor en modo editable (motor/, benchmark/,
echo ground_truth/):  %PY% -m pip install -e .
echo.
pause
