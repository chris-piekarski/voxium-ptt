@echo off
setlocal EnableExtensions
rem Deletes .venv and builds a new Windows venv. Use when you see: No pyvenv.cfg file
title Voxium — recreate .venv
cd /d "%~dp0..\.." || (echo Bad path&pause&exit /b 1)
if not exist "pyproject.toml" (echo Not the Voxium repo root. & pause & exit /b 1)

echo Repository: %CD%
echo.
if exist ".venv" (
  echo Removing old .venv ...
  rmdir /s /q ".venv"
  if exist ".venv" (
    echo Could not delete .venv — close apps using it, or delete the folder in Explorer, then re-run.
    pause
    exit /b 1
  )
)

where py >nul 2>&1
if not errorlevel 1 (
  echo Creating venv: py -3 -m venv .venv
  py -3 -m venv .venv
) else (
  where python >nul 2>&1
  if not errorlevel 1 (
    echo Creating venv: python -m venv .venv
    python -m venv .venv
  ) else (
    echo Python not on PATH. Install from https://www.python.org/ ^(Add to PATH^)
    pause
    exit /b 1
  )
)

if not exist ".venv\pyvenv.cfg" (
  echo [ERROR] .venv\pyvenv.cfg still missing. Do not create venv from WSL for Windows use.
  pause
  exit /b 1
)

echo.
echo Upgrading pip ...
".venv\Scripts\python.exe" -m pip install -U pip setuptools wheel
if errorlevel 1 (
  echo pip failed.
  pause
  exit /b 1
)
echo.
echo OK. Next:  .venv\Scripts\python.exe -m pip install -e .
echo Or run:  scripts\windows\Setup-Voxium.cmd
echo.
pause
