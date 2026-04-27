# Launch Voxium. Tab title is set by the app (SetConsoleTitleW + VT); optional: $env:VOXIUM_WINDOW_TITLE = "My title"
# Usage: pwsh -File scripts\windows\Voxium.ps1 run
# Double-click: use scripts\windows\Voxium.cmd so the window pauses on errors.
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $VoxiumArgs
)
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$Vox = Join-Path $RepoRoot ".venv\Scripts\voxium.exe"
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $Vox) {
    if ($VoxiumArgs) {
        & $Vox @VoxiumArgs
    } else {
        & $Vox
    }
    exit $LASTEXITCODE
}
if (Test-Path $Py) {
    if ($VoxiumArgs) {
        & $Py -m voxium @VoxiumArgs
    } else {
        & $Py -m voxium
    }
    exit $LASTEXITCODE
}
Write-Host ""
Write-Host "Voxium not found: missing .venv\Scripts\voxium.exe and .venv\Scripts\python.exe" -ForegroundColor Red
Write-Host "  Fix:  run scripts\windows\venv_bootstrap.cmd  in this folder (one-time venv + pip install -e .)" -ForegroundColor Yellow
Write-Host "  Then:  scripts\windows\Voxium.cmd" -ForegroundColor Yellow
Write-Host ""
exit 1
