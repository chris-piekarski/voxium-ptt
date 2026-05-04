#Requires -Version 5.1
<#
.SYNOPSIS
  Record a py-spy flame graph of the Voxium Windows client during PTT activity.

.DESCRIPTION
  Two modes:

  * Attach (default): finds the running .venv client PID and runs py-spy record -p …
    On Windows, --subprocesses is added by default (unless you pass -Native) because
    venv-launched clients often hit "Failed to find python version from target process"
    without it (see benfred/py-spy issues around Windows venvs and console scripts).
    -Native cannot be combined with --subprocesses on Windows; if you use -Native,
    attach may fail — try an elevated shell, pip install -U py-spy, or -Spawn.

  * Spawn (-Spawn): runs py-spy record -- .venv\Scripts\python.exe -m voxium … so py-spy
    owns the process from startup. Use this when attach keeps failing. You will get a
    second Voxium client — close your normal one first if you only want one instance.

  Same-OS rule: run on Windows when the Voxium client is Windows Python. See docs/profiling.md.

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1 -Duration 45 -OutputPath "$env:USERPROFILE\Desktop\voxium-ptt.svg"

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1 -ClientProcessId 52460 -Native

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1 -Spawn -SpawnArguments run,--server-device,cuda,--server-compute,float16

  (Prefer splitting arguments: -SpawnArguments run --server-device cuda --server-compute float16)
#>
param(
    [int] $Duration = 30,
    [int] $Rate = 200,
    [string] $OutputPath = "",
    [int] $ClientProcessId = 0,
    [switch] $Native,
    [switch] $Spawn,
    [string[]] $SpawnArguments = @("run"),
    [switch] $NoSubprocesses
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$PySpy = Join-Path $RepoRoot ".venv\Scripts\py-spy.exe"
if (-not (Test-Path -LiteralPath $PySpy)) {
    Write-Error "py-spy not found at $PySpy — run: pip install -e `".[dev]`" from the repo venv."
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $env:USERPROFILE "Desktop\voxium-ptt-$stamp.svg"
}

if ($Spawn) {
    $targetPid = 0
}
elseif ($ClientProcessId -gt 0) {
    $targetPid = $ClientProcessId
}
else {
    $candidates = Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -like "*.venv\Scripts\python.exe*voxium.exe*" -and
            $_.CommandLine -notlike "*voxium server*"
        } |
        Select-Object -First 1

    if (-not $candidates) {
        Write-Error @"
Could not find a Voxium client process (.venv\Scripts\python.exe ... voxium.exe, not 'voxium server').
Start the client first, pass -ClientProcessId <id>, or use -Spawn to profile a fresh python -m voxium process.
"@
    }
    $targetPid = [int]$candidates.ProcessId
}

$useSubprocesses = (-not $Spawn) -and (-not $Native) -and (-not $NoSubprocesses)

Write-Host "Repo:  $RepoRoot"
Write-Host "py-spy: $PySpy"
if ($Spawn) {
    Write-Host "Mode:  spawn (py-spy starts python -m voxium)"
}
else {
    Write-Host "PID:   $targetPid"
}
Write-Host "Out:   $OutputPath"
Write-Host "Duration: ${Duration}s  Rate: ${Rate}/s  Native: $($Native.IsPresent)  Subprocesses: $useSubprocesses  Spawn: $($Spawn.IsPresent)"
Write-Host ""
Write-Host "Start your PTT takes as soon as py-spy begins sampling." -ForegroundColor Yellow
Write-Host ""

if ($Spawn) {
    $pythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        Write-Error "Python not found at $pythonExe"
    }
    $spyArgs = @(
        "record",
        "-o", $OutputPath,
        "-d", "$Duration",
        "-r", "$Rate"
    )
    if ($Native) {
        $spyArgs += "--native"
    }
    $spyArgs += "--"
    $spyArgs += $pythonExe
    $spyArgs += "-m"
    $spyArgs += "voxium"
    foreach ($a in $SpawnArguments) {
        $spyArgs += $a
    }
    & $PySpy @spyArgs
}
else {
    $spyArgs = @(
        "record",
        "-o", $OutputPath,
        "-p", "$targetPid",
        "-d", "$Duration",
        "-r", "$Rate"
    )
    if ($Native) {
        $spyArgs += "--native"
    }
    elseif ($useSubprocesses) {
        $spyArgs += "--subprocesses"
    }
    & $PySpy @spyArgs
}

$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host ""
    Write-Host "py-spy exited with code $code." -ForegroundColor Red
    Write-Host "Try, in order: run this script again (default now uses --subprocesses on attach);" -ForegroundColor Yellow
    Write-Host "  Administrator PowerShell;  pip install -U py-spy;" -ForegroundColor Yellow
    Write-Host "  or spawn mode: add -Spawn (and -SpawnArguments to match your usual voxium flags)." -ForegroundColor Yellow
    exit $code
}

Write-Host ""
Write-Host "Wrote: $OutputPath" -ForegroundColor Green
Write-Host "Open the SVG in a browser (Edge/Chrome) to inspect the flame graph."
