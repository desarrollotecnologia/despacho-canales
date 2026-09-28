@echo off
call "%~dp0stop.bat" nopause
timeout /t 2 /nobreak >nul
call "%~dp0start.bat"
