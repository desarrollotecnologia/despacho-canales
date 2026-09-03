@echo off
echo === Estado Gestor de Canales ===
tasklist | findstr uvicorn && (echo ACTIVO) || (echo DETENIDO)
echo.
echo Ultimas lineas del log:
powershell -command "if (Test-Path 'logs\server.log') { Get-Content 'logs\server.log' -Tail 20 } else { Write-Host 'Sin log todavia.' }"
pause
