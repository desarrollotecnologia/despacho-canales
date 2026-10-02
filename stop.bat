@echo off
cd /d "%~dp0"
set "APP_PORT=8012"
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="APP_PORT" set "APP_PORT=%%B"
  )
)
echo Deteniendo Despacho de Canales en puerto %APP_PORT%...
powershell -NoProfile -Command ^
  "$p=Get-NetTCPConnection -LocalPort %APP_PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; if($p){ $p | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } ; Write-Host 'Proceso detenido.' } else { Write-Host 'No habia proceso escuchando.' }"
echo Servidor detenido.
if /I "%~1"=="nopause" goto :eof
pause
