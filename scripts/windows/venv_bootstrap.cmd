@echo off
setlocal EnableExtensions
rem One-time: create a Windows .venv in the repo root and install Voxium (editable).
rem Run from File Explorer (double-click) or: cmd /c scripts\windows\venv_bootstrap.cmd
cd /d "%~dp0..\.." || (echo Could not find repo root & pause & exit /b 1)
if not exist "pyproject.toml" (echo This folder is not the Voxium repo - missing pyproject.toml&pause&exit /b 1)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -m venv .venv
) else (
  where python >nul 2>&1
  if not errorlevel 1 (python -m venv .venv) else (echo Python not on PATH. Install Python 3.10+ and retry.&pause&exit /b 1)
)
if not exist ".venv\Scripts\python.exe" (echo venv create failed.&pause&exit /b 1)
".venv\Scripts\python.exe" -m pip install -U pip setuptools wheel
".venv\Scripts\python.exe" -m pip install -e .
echo.
echo Done. From repo: scripts\windows\Voxium.cmd  or:  .venv\Scripts\voxium run
echo.
pause
