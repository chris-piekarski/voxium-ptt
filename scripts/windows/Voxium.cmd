@echo off
rem Voxium Windows launcher. Double-click, or: cmd /c scripts\windows\Voxium.cmd run
rem
rem If the window only flashes: the process failed. This script pauses on error so you can
rem read the message. Set VOXIUM_NO_PAUSE=1 to skip the pause (for automation).
rem First time: run scripts\windows\venv_bootstrap.cmd to create .venv\Scripts\voxium.exe
rem
cd /d "%~dp0..\.." || (echo Could not change to the repo root.&pause&exit /b 1)
where pwsh >nul 2>&1
if not errorlevel 1 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0Voxium.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Voxium.ps1" %*
)
set "VEXIT=%ERRORLEVEL%"
if "%VEXIT%"=="" set "VEXIT=0"
if not "%VEXIT%"=="0" if not "%VEXIT%"=="130" if not defined VOXIUM_NO_PAUSE (
  echo.
  if not exist ".venv\Scripts\voxium.exe" if not exist ".venv\Scripts\python.exe" (
    echo [Hint] No .venv in this folder. Create it with:  scripts\windows\venv_bootstrap.cmd
    echo.
  ) else (
    echo [Voxium] Exited with code %VEXIT% — read the message above. For a full log, open
    echo        cmd.exe in this folder and run:  scripts\windows\Voxium.cmd run
    echo.
  )
  pause
)
exit /b %VEXIT%
