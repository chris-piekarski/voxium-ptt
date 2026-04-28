@echo off
rem Run Voxium from the **repository root** (this file sits next to pyproject.toml).
rem Double-click this, or make your shortcut point HERE — not to a copy in WSL-Workspaces.
cd /d "%~dp0" || (echo [ERROR] could not cd & pause & exit /b 1)
if not exist "pyproject.toml" (
  echo [ERROR] pyproject.toml not found in: %CD%
  echo This Voxium.cmd must stay in the root of the Voxium git clone.
  echo.
  pause
  exit /b 1
)
call "%~dp0scripts\windows\Voxium.cmd" %*
