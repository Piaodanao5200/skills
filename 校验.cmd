@echo off
rem Launcher for check.ps1. ASCII-only on purpose, see mount.cmd.
chcp 65001 >nul
setlocal
set "PS1=%~dp0check.ps1"
if not exist "%PS1%" goto missing
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [WARN] finished with problems, exit code %RC%
pause
exit /b %RC%

:missing
echo [ERR] check.ps1 not found next to this file.
pause
exit /b 2
