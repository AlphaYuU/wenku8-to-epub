@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
set "run_status=%ERRORLEVEL%"
if not "%run_status%"=="0" (
    echo.
    pause
)
exit /b %run_status%
