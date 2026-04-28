@echo off
setlocal EnableExtensions
rem Use this when the .cmd file lives in the folder **above** the clone, e.g.:
rem   C:\Users\you\Desktop\WSL-Workspaces\Voxium-From-Parent-Folder.cmd
rem   C:\Users\you\Desktop\WSL-Workspaces\voxium\pyproject.toml
rem
rem Copy or rename to Voxium.cmd in that parent folder, or keep this name and double-click it.
rem Do NOT use scripts\windows\Voxium.cmd one-liner here — that script expects to live *inside* the repo.
set "P=%~dp0"
if defined VOXIUM_CLONE if exist "%P%%VOXIUM_CLONE%\pyproject.toml" (
  cd /d "%P%%VOXIUM_CLONE%"
  goto :run
)
if exist "%P%voxium\pyproject.toml" (
  cd /d "%P%voxium"
  goto :run
)
if exist "%P%Voxium\pyproject.toml" (
  cd /d "%P%Voxium"
  goto :run
)
echo.
echo [ERROR] Could not find a Voxium clone. Looked for:
echo   %P%voxium\pyproject.toml
echo   %P%Voxium\pyproject.toml
echo.
echo Clone the repo so one of those paths exists, or edit this .cmd to set the folder name.
echo.
pause
exit /b 1

:run
call "%CD%\scripts\windows\Voxium.cmd" %*
endlocal
exit /b %ERRORLEVEL%
