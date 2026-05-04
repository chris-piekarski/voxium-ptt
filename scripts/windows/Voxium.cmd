@echo off
rem Voxium Windows launcher. Double-click, or: cmd /c scripts\windows\Voxium.cmd run
rem
rem If the window only flashes: the process failed. This script pauses on error so you can
rem read the message. Set VOXIUM_NO_PAUSE=1 to skip the pause (for automation).
rem First time: run scripts\windows\venv_bootstrap.cmd to create .venv\Scripts\voxium.exe
rem
cd /d "%~dp0..\.." || (echo Could not change to the repo root.&pause&exit /b 1)
if not exist "pyproject.toml" (
  echo.
  echo [ERROR] pyproject.toml not found in:
  echo   %CD%
  echo.
  echo This file must live at:  YOUR_CLONE\scripts\windows\Voxium.cmd
  echo   If you put Voxium.cmd in WSL-Workspaces only ^(above the clone^), that was wrong - use one of:
  echo   1^) YOUR_CLONE\Voxium.cmd  ^(in the repo root, next to pyproject.toml^)
  echo   2^) scripts\windows\Voxium-From-Parent-Folder.cmd  ^(in the parent of the clone; renames to Voxium.cmd ok^)
  echo.
  pause
  exit /b 1
)
rem Child script should not Read-Host on error: this .cmd already pauses below.
set "VOXIUM_SKIP_PS_PAUSE=1"
where pwsh >nul 2>&1
if not errorlevel 1 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0Voxium.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Voxium.ps1" %*
)
set "VOXIUM_SKIP_PS_PAUSE="
set "VEXIT=%ERRORLEVEL%"
if "%VEXIT%"=="" set "VEXIT=0"
if not "%VEXIT%"=="0" if not "%VEXIT%"=="130" if not defined VOXIUM_NO_PAUSE (
  echo.
  if not exist ".venv\Scripts\voxium.exe" if not exist ".venv\Scripts\python.exe" (
    echo [Hint] No .venv. One-time setup:  scripts\windows\Setup-Voxium.cmd
    echo.
  ) else (
    echo [Voxium] Exited with code %VEXIT% - read the message above. For a full log, open
    echo        cmd.exe in this folder and run:  scripts\windows\Voxium.cmd run
    echo.
  )
  pause
)
exit /b %VEXIT%
