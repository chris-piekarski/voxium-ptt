@echo off
rem Double-click or: cmd /c scripts\windows\Voxium.cmd run
cd /d "%~dp0..\.." || exit /b 1
where pwsh >nul 2>&1
if not errorlevel 1 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0Voxium.ps1" %*
  exit /b %ERRORLEVEL%
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Voxium.ps1" %*
exit /b %ERRORLEVEL%
