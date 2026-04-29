#Requires -Version 5.1
# Launch Voxium. Tab title is set by the app (SetConsoleTitleW + VT); optional: $env:VOXIUM_WINDOW_TITLE = "My title"
# Usage: pwsh -File scripts\windows\Voxium.ps1 run
# Double-click: use Voxium.cmd in the repo root (or scripts\windows\Voxium.cmd) so the window pauses on errors.
#
# Do not call Set-ExecutionPolicy here: the .cmd wrapper already passes -ExecutionPolicy Bypass, and
# Set-ExecutionPolicy -Scope Process can throw under strict Group Policy. With $ErrorActionPreference
# Stop, that would exit this script before voxium runs.
#
# If .venv is missing, invalid, or the voxium package is not installed (pip install -e .), we can
# run scripts\windows\Setup-Voxium.ps1 -SkipPolish unless VOXIUM_NO_AUTO_SETUP=1.
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $VoxiumArgs
)
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

# When unset, seed the same default STT model as voxium.model_registry.DEFAULT_MODEL_NAME
# (English, ~2GB VRAM). Override: set WHISPER_MODEL, or transcription.model in config.yaml.
if (-not $env:WHISPER_MODEL) {
    $env:WHISPER_MODEL = "small.en"
}

# Re-encode (local llama.cpp /polish) matches CLI default: on. Set VOXIUM_POLISH_ENABLED=0 to disable.
if (-not $env:VOXIUM_POLISH_ENABLED) {
    $env:VOXIUM_POLISH_ENABLED = "1"
}

# Gemma UX chatter is **on** by default (`voxium run`). To disable: `voxium run --no-ux-chatter`, or
#   ux_chatter: { enabled: false }  in  %USERPROFILE%\.config\voxium\config.yaml , or  VOXIUM_UX_CHATTER=0 .
# First-time:  voxium models --pull-ux-chatter  and auto-start (or a llama-server on 127.0.0.1:11436 on the
# *same* OS as this voxium — Windows vs WSL are different loopbacks).

$PyProject = Join-Path $RepoRoot "pyproject.toml"
if (-not (Test-Path -LiteralPath $PyProject)) {
    Write-Host ""
    Write-Host "[ERROR] pyproject.toml not found at: $RepoRoot" -ForegroundColor Red
    Write-Host "Voxium.ps1 must live in the clone at:  <repo>\scripts\windows\" -ForegroundColor Yellow
    Write-Host "Do not move or copy this script out of the repository." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

if ($env:VOXIUM_WINDOWS_DEBUG -eq "1") {
    Write-Host "[debug] Repo root: $RepoRoot" -ForegroundColor DarkGray
}

$Vox = Join-Path $RepoRoot ".venv\Scripts\voxium.exe"
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvCfg = Join-Path $VenvDir "pyvenv.cfg"
$SetupScript = Join-Path $PSScriptRoot "Setup-Voxium.ps1"

function Test-ValidWindowsVenv {
    if (-not (Test-Path -LiteralPath $Py)) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $VenvCfg)) {
        return $false
    }
    $smoke = & $Py -c "import sys; assert sys.prefix" 2>&1
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    if ("$smoke" -match "No pyvenv\.cfg") {
        return $false
    }
    return $true
}

function Test-VoxiumPackageImportable {
    if (-not (Test-Path -LiteralPath $Py)) {
        return $false
    }
    $null = & $Py -c "import voxium" 2>&1
    return $LASTEXITCODE -eq 0
}

if (-not (Test-ValidWindowsVenv)) {
    $noAuto = $env:VOXIUM_NO_AUTO_SETUP -eq "1"
    $missingDir = -not (Test-Path -LiteralPath $VenvDir)
    $missingPy = -not (Test-Path -LiteralPath $Py)
    $missingCfg = (Test-Path -LiteralPath $VenvDir) -and -not (Test-Path -LiteralPath $VenvCfg)
    if ($noAuto) {
        Write-Host ""
        if ($missingDir -or $missingPy) {
            Write-Host "Voxium is not set up: .venv\Scripts\python.exe is missing." -ForegroundColor Red
        } elseif ($missingCfg) {
            Write-Host "Voxium: .venv is invalid (missing .venv\pyvenv.cfg) — do not use a WSL venv on Windows." -ForegroundColor Red
        } else {
            Write-Host "Voxium: .venv\Scripts\python.exe does not work (No pyvenv.cfg or broken venv)." -ForegroundColor Red
        }
        Write-Host "  Run (once):  pwsh -ExecutionPolicy Bypass -File .\scripts\windows\Setup-Voxium.ps1" -ForegroundColor Cyan
        Write-Host "  Or double-click:  scripts\windows\Setup-Voxium.cmd" -ForegroundColor Cyan
        Write-Host "  (Unset VOXIUM_NO_AUTO_SETUP to allow automatic repair on launch.)" -ForegroundColor DarkGray
        Write-Host ""
        exit 1
    }
    if ((Test-Path -LiteralPath $VenvDir) -and -not (Test-Path -LiteralPath $VenvCfg)) {
        Write-Host "" 
        Write-Host "Voxium: invalid .venv (missing pyvenv.cfg — often a Linux/WSL venv on Windows). Repairing…" -ForegroundColor Yellow
    } elseif ($missingDir -or $missingPy) {
        Write-Host ""
        Write-Host "Voxium: no usable .venv. Running first-time setup (pip install, skip heavy polish pull)…" -ForegroundColor Yellow
    } else {
        Write-Host ""
        Write-Host "Voxium: .venv\Scripts\python.exe is not a working venv. Re-running setup…" -ForegroundColor Yellow
    }
    & $SetupScript -SkipPolish
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    if (-not (Test-ValidWindowsVenv)) {
        Write-Host "[ERROR] Setup finished but .venv is still not usable. See logs\voxium-windows-setup.log" -ForegroundColor Red
        exit 1
    }
}

if ((Test-ValidWindowsVenv) -and -not (Test-VoxiumPackageImportable)) {
    $noAuto = $env:VOXIUM_NO_AUTO_SETUP -eq "1"
    if ($noAuto) {
        Write-Host ""
        Write-Host "Voxium: editable install missing in .venv (No module named 'voxium')." -ForegroundColor Red
        Write-Host "  From repo root, run:" -ForegroundColor Yellow
        Write-Host "    .\.venv\Scripts\python -m pip install -e ." -ForegroundColor Cyan
        Write-Host "  Or:  pwsh -ExecutionPolicy Bypass -File .\scripts\windows\Setup-Voxium.ps1" -ForegroundColor Cyan
        Write-Host ""
        exit 1
    }
    Write-Host ""
    Write-Host "Voxium: package not installed in .venv. Running setup (pip install -e ., skip heavy polish pull)…" -ForegroundColor Yellow
    & $SetupScript -SkipPolish
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    if (-not (Test-VoxiumPackageImportable)) {
        Write-Host "[ERROR] import voxium still fails after setup. See logs\voxium-windows-setup.log" -ForegroundColor Red
        exit 1
    }
}

# Prefer voxium.exe when present; otherwise python -m voxium
if (Test-Path -LiteralPath $Vox) {
    if ($VoxiumArgs) {
        & $Vox @VoxiumArgs
    } else {
        & $Vox
    }
    exit $LASTEXITCODE
}
if (Test-Path -LiteralPath $Py) {
    if ($VoxiumArgs) {
        & $Py -m voxium @VoxiumArgs
    } else {
        & $Py -m voxium
    }
    exit $LASTEXITCODE
}
Write-Host ""
Write-Host "Voxium is not set up: .venv\Scripts\python.exe is missing after setup." -ForegroundColor Red
Write-Host "  One-time fix (PowerShell, from this repo root):" -ForegroundColor Yellow
Write-Host "    pwsh -ExecutionPolicy Bypass -File .\scripts\windows\Setup-Voxium.ps1" -ForegroundColor Cyan
Write-Host "  Or double-click:  scripts\windows\Setup-Voxium.cmd" -ForegroundColor Cyan
Write-Host "  (Legacy) cmd:  scripts\windows\venv_bootstrap.cmd" -ForegroundColor DarkGray
Write-Host ""
exit 1
