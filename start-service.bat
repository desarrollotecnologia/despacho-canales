@echo off
cd /d "%~dp0"
if not exist "logs" mkdir logs
if not exist "venv\Scripts\activate.bat" exit /b 1
call venv\Scripts\activate
set "APP_PORT=8012"
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="APP_PORT" set "APP_PORT=%%B"
  )
)
uvicorn main:app --host 0.0.0.0 --port %APP_PORT% >> logs\server.log 2>&1
