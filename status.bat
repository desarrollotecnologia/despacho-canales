@echo off
cd /d "%~dp0"
set "APP_PORT=8012"
set "OFFICIAL_HOST=192.168.20.205"
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="APP_PORT" set "APP_PORT=%%B"
    if /I "%%A"=="OFFICIAL_HOST" set "OFFICIAL_HOST=%%B"
  )
)
echo === Estado Despacho de Canales ===
echo URL fija: http://%OFFICIAL_HOST%:%APP_PORT%
netstat -ano | findstr /R /C:":%APP_PORT% .*LISTENING" >nul
if errorlevel 1 (echo DETENIDO) else (echo ACTIVO en puerto %APP_PORT%)
echo.
echo Ultimas lineas del log:
powershell -NoProfile -Command "if (Test-Path 'logs\server.log') { Get-Content 'logs\server.log' -Tail 20 } else { Write-Host 'Sin log todavia.' }"
pause
