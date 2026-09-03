@echo off
echo Deteniendo Gestor de Canales...
taskkill /F /IM uvicorn.exe 2>nul
taskkill /F /FI "WINDOWTITLE eq Gestor Canales*" 2>nul
echo Servidor detenido.
pause
