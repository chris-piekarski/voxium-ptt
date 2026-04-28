#Requires -Version 5.1
# Launch Voxium. Tab title is set by the app (SetConsoleTitleW + VT); optional: $env:VOXIUM_WINDOW_TITLE = "My title"
# Usage: pwsh -File scripts\windows\Voxium.ps1 run
# Double-click: use Voxium.cmd in the repo root (or scripts\windows\Voxium.cmd) so the window pauses on errors.
#
# Do not call Set-ExecutionPolicy here: the .cmd wrapper already passes -ExecutionPolicy Bypass, and
# Set-ExecutionPolicy -Scope Process can throw under strict Group Policy. With $ErrorActionPreference
# Stop, that would exit this script before voxium runs.
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $VoxiumArgs
)
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

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
Write-Host "Voxium is not set up: .venv\Scripts\python.exe is missing." -ForegroundColor Red
Write-Host "  One-time fix (PowerShell, from this repo root):" -ForegroundColor Yellow
Write-Host "    pwsh -ExecutionPolicy Bypass -File .\scripts\windows\Setup-Voxium.ps1" -ForegroundColor Cyan
Write-Host "  Or double-click:  scripts\windows\Setup-Voxium.cmd" -ForegroundColor Cyan
Write-Host "  (Legacy) cmd:  scripts\windows\venv_bootstrap.cmd" -ForegroundColor DarkGray
Write-Host ""
exit 1
