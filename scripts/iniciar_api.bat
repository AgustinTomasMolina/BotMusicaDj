@echo off
chcp 65001 >nul
title MusiFlix - API REST

call "%~dp0_entorno.bat"
if errorlevel 1 exit /b 1

echo ============================================================
echo    MusiFlix - API REST
echo ============================================================
echo.

%PY% -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
  echo Faltan dependencias. Instalando desde requirements.txt...
  %PY% -m pip install -r requirements.txt
  echo.
)

echo API en:        http://localhost:8000
echo Documentacion: http://localhost:8000/docs
echo ReDoc:         http://localhost:8000/redoc
echo.
echo Presiona Ctrl+C para detener
echo.

REM Mismo entrypoint que usa el Dockerfile: server:app
REM (no arranca el navegador; para eso esta iniciar_web.bat)
%PY% -m uvicorn server:app --host 127.0.0.1 --port 8000

pause
