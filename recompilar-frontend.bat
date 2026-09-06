@echo off
REM Recompila el frontend React (Vite) y deja el build en frontend\dist,
REM que es lo que sirve FastAPI en http://127.0.0.1:8000/
cd /d "%~dp0frontend"
echo ============================================
echo   Recompilando el frontend React (MusiFlix)
echo ============================================
call npm run build
echo.
if %errorlevel%==0 (
  echo LISTO. Refresca el navegador con Ctrl+Shift+R en http://127.0.0.1:8000/
) else (
  echo HUBO UN ERROR al compilar. Revisa el mensaje de arriba.
)
echo.
pause
