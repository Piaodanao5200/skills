@echo off
rem Launcher for mount.ps1. Kept ASCII-only on purpose: cmd.exe reads this
rem file with the system code page, and chcp below changes it mid-file.
chcp 65001 >nul
setlocal
set "PS1=%~dp0mount.ps1"
if not exist "%PS1%" goto missing
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [WARN] finished with problems, exit code %RC%
pause
exit /b %RC%

:missing
echo [ERR] mount.ps1 not found next to this file.
pause
exit /b 2
