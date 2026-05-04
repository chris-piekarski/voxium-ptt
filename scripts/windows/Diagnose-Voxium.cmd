@echo off
setlocal EnableExtensions
title Voxium — diagnose
rem Writes %TEMP%\voxium-diagnose.log and shows it. Run from the repo (scripts\windows\...).
cd /d "%~dp0..\.." || (echo [ERROR] cd to repo & pause & exit /b 1)

set "LOG=%TEMP%\voxium-diagnose.log"
echo Voxium Diagnose  %date% %time% > "%LOG%"
echo CD=%CD%>> "%LOG%"
echo.>> "%LOG%"

echo.
echo === Where is Python? ===
echo.>> "%LOG%"
echo === where py / python ===>> "%LOG%"
where py >> "%LOG%" 2>&1
where python >> "%LOG%" 2>&1
where pwsh >> "%LOG%" 2>&1
where llama-server >> "%LOG%" 2>&1
type "%LOG%"

echo.
echo === py -0p (installed Pythons) ===
echo.>> "%LOG%"
echo === py -0p ===>> "%LOG%"
py -0p >> "%LOG%" 2>&1
py -0p 2>nul

echo.>> "%LOG%"
echo === py -3 -V ===>> "%LOG%"
py -3 -V >> "%LOG%" 2>&1
py -3 -V
echo.>> "%LOG%"
echo === python -V ===>> "%LOG%"
python -V >> "%LOG%" 2>&1
python -V 2>nul

echo.>> "%LOG%"
echo === pyproject.toml? ===>> "%LOG%"
if exist "pyproject.toml" (echo yes>> "%LOG%" & echo pyproject.toml: yes) else (echo NO>> "%LOG%" & echo pyproject.toml: MISSING - not a Voxium repo root!)

echo.>> "%LOG%"
echo === .venv\pyvenv.cfg ===>> "%LOG%"
if exist ".venv\pyvenv.cfg" (echo found>> "%LOG%" & echo .venv\pyvenv.cfg: found) else (echo MISSING - invalid venv, remove .venv and run Setup-Voxium>> "%LOG%" & echo .venv\pyvenv.cfg: MISSING - invalid venv)
echo.>> "%LOG%"
echo === .venv\Scripts\python.exe ===>> "%LOG%"
if exist ".venv\Scripts\python.exe" (
  echo found>> "%LOG%"
  echo .venv: found
  echo.>> "%LOG%"
  echo --- sys.version --- >> "%LOG%"
  ".venv\Scripts\python.exe" -c "import sys; print(sys.version); print(sys.executable)" >> "%LOG%" 2>&1
  ".venv\Scripts\python.exe" -c "import sys; print(sys.version); print(sys.executable)"
  echo.>> "%LOG%"
  echo --- voxium package --- >> "%LOG%"
  ".venv\Scripts\python.exe" -m pip show voxium 2>&1 | findstr /b /c:"Name:" /c:"Version:" /c:"Summary:" /c:"Location:" /c:"Editable project location:" /c:"Requires:" >> "%LOG%"
  ".venv\Scripts\python.exe" -m pip show voxium 2>&1 | findstr /b /c:"Name:" /c:"Version:" /c:"Summary:" /c:"Location:" /c:"Editable project location:" /c:"Requires:"
  echo.>> "%LOG%"
  echo --- import voxium --- >> "%LOG%"
  ".venv\Scripts\python.exe" -c "import voxium; print('voxium:', voxium.__file__)" >> "%LOG%" 2>&1
  ".venv\Scripts\python.exe" -c "import voxium; print('voxium:', voxium.__file__)" 2>&1
  echo.>> "%LOG%"
  echo --- import sounddevice --- >> "%LOG%"
  ".venv\Scripts\python.exe" -c "import sounddevice as s; print('sounddevice OK', len(s.query_devices()))" >> "%LOG%" 2>&1
  ".venv\Scripts\python.exe" -c "import sounddevice as s; print('sounddevice OK', len(s.query_devices()))" 2>&1
) else (
  echo not found - run Setup-Voxium.cmd first>> "%LOG%"
  echo .venv: NOT FOUND — run scripts\windows\Setup-Voxium.cmd
)

echo.
echo Full log: %LOG%
echo.
notepad "%LOG%"
echo.
pause
