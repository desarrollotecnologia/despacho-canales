@echo off
cd /d "%~dp0"
call venv\Scripts\activate
echo Iniciando Despacho de Canales (modo consola) en http://localhost:8000
start "" http://localhost:8000
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
