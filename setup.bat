@echo off
echo ============================================
echo  Despacho de Canales - Colbeef
echo  Primera instalacion
echo ============================================
cd /d "%~dp0"
python -m venv venv
call venv\Scripts\activate
pip install -r requirements.txt
echo.
echo Instalacion completada. Ejecuta start.bat para iniciar.
pause
