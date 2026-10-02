@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\activate.bat" (
  echo Falta el entorno virtual. Ejecuta setup.bat primero.
  pause
  exit /b 1
)
call venv\Scripts\activate
set "APP_PORT=8012"
set "OFFICIAL_HOST=192.168.20.205"
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="APP_PORT" set "APP_PORT=%%B"
    if /I "%%A"=="OFFICIAL_HOST" set "OFFICIAL_HOST=%%B"
  )
)
echo Iniciando Despacho de Canales (modo consola) en http://%OFFICIAL_HOST%:%APP_PORT%
start "" "http://%OFFICIAL_HOST%:%APP_PORT%"
uvicorn main:app --host 0.0.0.0 --port %APP_PORT% --reload
pause
