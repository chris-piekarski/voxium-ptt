@echo off
setlocal EnableExtensions
rem One-time: .venv + pip install -e .
rem If the window closes: read logs\venv_bootstrap.log in the repo, or run from an open cmd.exe.
title Voxium — venv bootstrap
cd /d "%~dp0..\.." || (echo [ERROR] Could not cd to repo root.&goto :epicfail)
if not exist "pyproject.toml" (echo [ERROR] pyproject.toml missing — use the full clone, not a copied .cmd only.&goto :epicfail)

if not exist "logs" mkdir "logs" 2>nul
set "LOG=%CD%\logs\venv_bootstrap.log"
echo Voxium venv_bootstrap> "%LOG%"
echo CD=%CD%>> "%LOG%"
echo.>> "%LOG%"

where py >nul 2>&1
if not errorlevel 1 (
  echo Running: py -3 -m venv .venv
  echo === py -3 -m venv .venv>> "%LOG%"
  py -3 -m venv .venv >> "%LOG%" 2>&1
  if errorlevel 1 (echo [ERROR] py -3 -m venv failed. See %LOG%&goto :epicfail)
) else (
  where python >nul 2>&1
  if not errorlevel 1 (
    echo Running: python -m venv .venv
    echo === python -m venv .venv>> "%LOG%"
    python -m venv .venv >> "%LOG%" 2>&1
    if errorlevel 1 (echo [ERROR] python -m venv failed. See %LOG%&goto :epicfail)
  ) else (
    echo [ERROR] Python not on PATH. Install 3.10+ from https://www.python.org/
    echo Python not on PATH>> "%LOG%"
    goto :epicfail
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Missing .venv\Scripts\python.exe
  echo missing python.exe in venv>> "%LOG%"
  goto :epicfail
)

echo.
echo Upgrading pip…
echo === pip install -U pip setuptools wheel>> "%LOG%"
".venv\Scripts\python.exe" -m pip install -U pip setuptools wheel >> "%LOG%" 2>&1
if errorlevel 1 (echo [ERROR] pip upgrade step failed. See %LOG%&goto :epicfail)

echo.
echo Installing Voxium (editable)…
echo === pip install -e .>> "%LOG%"
".venv\Scripts\python.exe" -m pip install -e . >> "%LOG%" 2>&1
if errorlevel 1 (echo [ERROR] pip install -e . failed. See %LOG%&goto :epicfail)

echo.
echo OK: venv + install finished. Log: %LOG%
echo Next: scripts\windows\Setup-Voxium.cmd
echo.
pause
exit /b 0

:epicfail
echo.
echo ---- Last log file (%LOG%^) ----
if exist "%LOG%" type "%LOG%"
echo ---- end log ----
echo.
echo To copy errors:   notepad "%LOG%"
echo.
pause
exit /b 1
