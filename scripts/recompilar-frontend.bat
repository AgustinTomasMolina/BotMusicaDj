@echo off
chcp 65001 >nul
title MusiFlix - Recompilar frontend

REM Recompila el frontend React (Vite). El build queda en frontend\dist,
REM que es lo que sirve FastAPI en http://127.0.0.1:8000/
cd /d "%~dp0..\frontend"

echo ============================================================
echo    Recompilando el frontend React (MusiFlix)
echo ============================================================

call npm run build
echo.
if %errorlevel%==0 (
  echo LISTO. Refresca con Ctrl+Shift+R en http://127.0.0.1:8000/
) else (
  echo HUBO UN ERROR al compilar. Revisa el mensaje de arriba.
)
echo.
pause
