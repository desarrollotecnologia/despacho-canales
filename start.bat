@echo off
cd /d "%~dp0"
call venv\Scripts\activate
echo Iniciando Gestor de Canales en http://localhost:8000 ...
start "" http://localhost:8000
uvicorn main:app --host 0.0.0.0 --port 8000 >> logs\server.log 2>&1
