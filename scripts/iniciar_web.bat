@echo off
chcp 65001 >nul
title MusiFlix - Servidor web

call "%~dp0_entorno.bat"
if errorlevel 1 exit /b 1

echo ============================================================
echo    MusiFlix - Iniciando el servidor web...
echo ============================================================
echo.

REM Dependencias minimas para levantar el server
%PY% -c "import fastapi, uvicorn, yt_dlp" 2>nul
if errorlevel 1 (
  echo Faltan dependencias. Instalando desde requirements.txt...
  %PY% -m pip install -r requirements.txt
  echo.
)

REM Abrir el navegador unos segundos despues de arrancar
start "" /min cmd /c "timeout /t 3 >nul & start http://localhost:8000"

echo Servidor en: http://localhost:8000
echo (Se abre el navegador solo. Para cerrar: Ctrl+C en esta ventana)
echo.

%PY% -m uvicorn server:app --host 127.0.0.1 --port 8000

pause
