@echo off
REM Helper comun de los scripts de MusiFlix.
REM   - se para en la raiz del repo (los scripts viven en scripts\, un nivel abajo)
REM   - resuelve el interprete de Python en %PY%, prefiriendo el venv del repo
REM Se usa desde los demas .bat con:
REM   call "%~dp0_entorno.bat"
REM   if errorlevel 1 exit /b 1

cd /d "%~dp0.."

set "PY="
call :probar ".venv\Scripts\python.exe"
if not defined PY call :probar "venv\Scripts\python.exe"
if not defined PY call :probar "py"
if not defined PY call :probar "python"
if not defined PY call :probar "python3"

if not defined PY (
  echo [ERROR] No se encontro un Python que funcione, ni en el PATH ni en un venv
  echo         del repo ^(.venv\ o venv\^).
  echo.
  echo         Instalalo desde https://www.python.org/downloads/ y marca
  echo         "Add python.exe to PATH" durante la instalacion.
  echo.
  echo         Nota: el "python" que trae Windows por defecto es solo un acceso
  echo         directo a la Microsoft Store y no sirve; por eso este script
  echo         verifica que el interprete realmente ejecute antes de usarlo.
  exit /b 1
)

exit /b 0

:probar
REM %~1 = candidato a interprete. Se acepta solo si REALMENTE ejecuta.
REM No alcanza con `where python`: el alias de la Microsoft Store aparece en el
REM PATH, responde a `where`, y despues falla con 9009 al invocarlo.
"%~1" -c "import sys" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0
