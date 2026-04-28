@echo off
setlocal EnableExtensions
rem Wrapper: runs Setup-Voxium.ps1. Log: <repo>\logs\voxium-windows-setup.log
title Voxium — Windows setup
cd /d "%~dp0..\.." || (echo [ERROR] Could not cd to repo root.&pause&exit /b 1)
if not exist "pyproject.toml" (echo [ERROR] Not the Voxium repo - missing pyproject.toml&pause&exit /b 1)

set "REPO=%CD%"
set "LOG=%REPO%\logs\voxium-windows-setup.log"
echo.
echo Repository: %REPO%
echo If this window closes, open:  %LOG%
echo.

where pwsh >nul 2>&1
if not errorlevel 1 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-Voxium.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-Voxium.ps1" %*
)
set "E=%ERRORLEVEL%"
echo.
if not "%E%"=="0" (
  echo [ERROR] Setup exited with code %E%
  if exist "%LOG%" (
    echo.
    echo ---- %LOG% ----
    type "%LOG%"
    echo ---- end ----
  )
  echo.
  echo To open the log:  notepad "%LOG%"
) else (
  echo Setup completed OK. Transcript: %LOG%
)
echo.
pause
exit /b %E%
