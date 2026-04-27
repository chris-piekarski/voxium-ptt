# Launch Voxium. Tab title is set by the app (SetConsoleTitleW + VT); optional: $env:VOXIUM_WINDOW_TITLE = "My title"
# Usage: pwsh -File scripts\windows\Voxium.ps1 run
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
    & $Vox @VoxiumArgs
    exit $LASTEXITCODE
}
if (Test-Path $Py) {
    & $Py -m voxium @VoxiumArgs
    exit $LASTEXITCODE
}
Write-Error "Voxium not found in .venv. Run scripts\windows\venv_bootstrap.cmd first."
exit 1
