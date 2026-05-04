#Requires -Version 5.1
<#
.SYNOPSIS
  Record a py-spy flame graph of the Voxium Windows client during PTT activity.

.DESCRIPTION
  Two modes:

  * Attach (default): finds the running .venv client PID and runs py-spy record -p …
    Windows venv + console-script clients are flaky; the script auto-retries in order:
    plain attach, then --nonblocking, then --subprocesses (skipped if -NoSubprocesses).
    If all attempts fail, try Administrator PowerShell, pip install -U py-spy, or -Spawn.
    -Native performs a single attach with --native only (cannot combine with --subprocesses
    on Windows).

  * Spawn (-Spawn): runs py-spy record -- .venv\Scripts\python.exe -m voxium … so py-spy
    owns the process from startup. Use this when attach keeps failing. You will get a
    second Voxium client. On Windows the single-instance mutex usually blocks a second
    run while your main client is up — close the main client first, or rely on attach mode.

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

# Exit code from py-spy (set in attach / spawn branches below).
$code = 1

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

Write-Host "Repo:  $RepoRoot"
Write-Host "py-spy: $PySpy"
if ($Spawn) {
    Write-Host "Mode:  spawn (py-spy starts python -m voxium)"
}
else {
    Write-Host "PID:   $targetPid"
}
Write-Host "Out:   $OutputPath"
Write-Host "Duration: ${Duration}s  Rate: ${Rate}/s  Native: $($Native.IsPresent)  NoSubprocesses: $($NoSubprocesses.IsPresent)  Spawn: $($Spawn.IsPresent)"
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
    $code = $LASTEXITCODE
}
else {
    $baseArgs = @(
        "record",
        "-o", $OutputPath,
        "-p", "$targetPid",
        "-d", "$Duration",
        "-r", "$Rate"
    )
    if ($Native) {
        $spyArgs = $baseArgs + @("--native")
        Write-Host "py-spy attach (--native) …" -ForegroundColor Cyan
        & $PySpy @spyArgs
        $code = $LASTEXITCODE
    }
    else {
        $chain = @(
            @{ Label = "attach (plain)"; Extra = @() },
            @{ Label = "attach (--nonblocking)"; Extra = @("--nonblocking") }
        )
        if (-not $NoSubprocesses) {
            $chain += @{ Label = "attach (--subprocesses)"; Extra = @("--subprocesses") }
        }
        $code = 1
        foreach ($step in $chain) {
            Write-Host "Trying $($step.Label) …" -ForegroundColor Cyan
            $spyArgs = $baseArgs + $step.Extra
            & $PySpy @spyArgs
            $code = $LASTEXITCODE
            if ($code -eq 0) {
                break
            }
            Write-Host "  exit code $code" -ForegroundColor DarkYellow
        }
    }
}

if ($code -ne 0) {
    Write-Host ""
    Write-Host "py-spy exited with code $code (all attach strategies failed)." -ForegroundColor Red
    Write-Host "Next steps: Administrator PowerShell;  pip install -U py-spy;" -ForegroundColor Yellow
    Write-Host "  or -Spawn (close the main client first on Windows — single-instance mutex)." -ForegroundColor Yellow
    exit $code
}

Write-Host ""
Write-Host "Wrote: $OutputPath" -ForegroundColor Green
Write-Host "Open the SVG in a browser (Edge/Chrome) to inspect the flame graph."
