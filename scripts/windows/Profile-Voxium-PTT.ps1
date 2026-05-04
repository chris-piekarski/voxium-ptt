#Requires -Version 5.1
# Windows-only: resolves PIDs via Win32_Process and invokes repo .venv\py-spy.exe.
# OS guard runs immediately after param (param must be first non-comment statement).

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

  Default SVG directory: first existing folder among GetFolderPath(Desktop), %USERPROFILE%\OneDrive\Desktop,
  %USERPROFILE%\Desktop, and %PUBLIC%\Desktop. If none exist, the script uses repo logs\py-spy\ (created
  if needed) so the path is always writable inside the clone.

  Attach retries use a short probe duration on the first two strategies so a triple failure does not burn
  three full Duration windows; the last strategy uses your full -Duration for a real PTT capture.

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1 -Duration 45 -OutputPath "C:\Temp\voxium-ptt.svg"

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1 -ClientProcessId 52460 -Native

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1 -Spawn -SpawnArguments run,--server-device,cuda,--server-compute,float16

  (Prefer splitting arguments: -SpawnArguments run --server-device cuda --server-compute float16)
#>
param(
    [ValidateRange(1, 3600)]
    [int] $Duration = 30,
    [ValidateRange(1, 1000)]
    [int] $Rate = 200,
    [string] $OutputPath = "",
    [ValidateRange(0, 2147483647)]
    [int] $ClientProcessId = 0,
    [switch] $Native,
    [switch] $Spawn,
    [string[]] $SpawnArguments = @("run"),
    [switch] $NoSubprocesses
)

if ($PSVersionTable.PSVersion.Major -ge 6) {
    if (-not $IsWindows) {
        Write-Error "Profile-Voxium-PTT.ps1 must run on Windows (same OS as the Voxium client). See docs/profiling.md."
        exit 2
    }
}

# Exit code from py-spy (set in attach / spawn branches below).
$code = 1

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$PySpy = Join-Path $RepoRoot ".venv\Scripts\py-spy.exe"
if (-not (Test-Path -LiteralPath $PySpy)) {
    throw "py-spy not found at $PySpy — from repo root run: .\.venv\Scripts\python.exe -m pip install -e `".[dev]`""
}

function Get-VoxiumPySpyOutDirectory {
    param([string]$RepoRoot)
    $candidates = @(
        [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop),
        (Join-Path $env:USERPROFILE "OneDrive\Desktop"),
        (Join-Path $env:USERPROFILE "Desktop"),
        (Join-Path $env:PUBLIC "Desktop")
    )
    foreach ($d in $candidates) {
        if ([string]::IsNullOrWhiteSpace($d)) {
            continue
        }
        if (Test-Path -LiteralPath $d -PathType Container) {
            return (Resolve-Path -LiteralPath $d).Path
        }
    }
    $fallback = Join-Path $RepoRoot "logs\py-spy"
    New-Item -ItemType Directory -Force -Path $fallback | Out-Null
    return (Resolve-Path -LiteralPath $fallback).Path
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $outDir = Get-VoxiumPySpyOutDirectory -RepoRoot $RepoRoot
    if ($outDir -match 'logs[/\\]py-spy') {
        Write-Host "No Desktop folder found; using: $outDir" -ForegroundColor Yellow
    }
    $OutputPath = Join-Path $outDir "voxium-ptt-$stamp.svg"
}

# Resolve relative -OutputPath against repo root so GetDirectoryName and py-spy see a full path.
if (-not [string]::IsNullOrWhiteSpace($OutputPath) -and -not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $RepoRoot $OutputPath
}

# Avoid Split-Path -LiteralPath -Parent (parameter set issues on some pwsh builds).
$outParent = [string]::Empty
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $outParent = [System.IO.Path]::GetDirectoryName($OutputPath)
}
if (-not [string]::IsNullOrWhiteSpace($outParent) -and -not (Test-Path -LiteralPath $outParent)) {
    New-Item -ItemType Directory -Force -Path $outParent | Out-Null
    Write-Host "Created output directory: $outParent" -ForegroundColor Yellow
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
            $cl = $_.CommandLine
            $cl -and
            $cl -notlike "*voxium server*" -and
            (
                ($cl -like "*.venv\Scripts\python.exe*voxium.exe*") -or
                ($cl -match "-m\s+voxium\s+run\b")
            )
        } |
        Select-Object -First 1

    if (-not $candidates) {
        throw @"
Could not find a running Voxium client (python ... voxium.exe under .venv\Scripts, or python -m voxium run).
Start the client first, pass -ClientProcessId, or use -Spawn.
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
        throw "Python not found at $pythonExe"
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
        $probeSeconds = [Math]::Min(5, $Duration)
        $chain = @(
            @{ Label = "attach (plain)"; Extra = @(); ProbeOnly = $true },
            @{ Label = "attach (--nonblocking)"; Extra = @("--nonblocking"); ProbeOnly = $true }
        )
        if (-not $NoSubprocesses) {
            $chain += @{ Label = "attach (--subprocesses)"; Extra = @("--subprocesses"); ProbeOnly = $false }
        }
        else {
            $chain += @{ Label = "attach (plain, full duration)"; Extra = @(); ProbeOnly = $false }
        }
        $code = 1
        $attempt = 0
        foreach ($step in $chain) {
            $attempt++
            $useDur = if ($step.ProbeOnly) { $probeSeconds } else { $Duration }
            Write-Host "Trying $($step.Label) for ${useDur}s (attempt $attempt/$($chain.Count))..." -ForegroundColor Cyan
            $spyArgs = @(
                "record",
                "-o", $OutputPath,
                "-p", "$targetPid",
                "-d", "$useDur",
                "-r", "$Rate"
            ) + $step.Extra
            & $PySpy @spyArgs
            $code = $LASTEXITCODE
            if ($code -eq 0 -and $step.ProbeOnly) {
                Write-Host "Probe OK; capturing full ${Duration}s with the same flags for PTT..." -ForegroundColor Cyan
                $spyArgs = @(
                    "record",
                    "-o", $OutputPath,
                    "-p", "$targetPid",
                    "-d", "$Duration",
                    "-r", "$Rate"
                ) + $step.Extra
                & $PySpy @spyArgs
                $code = $LASTEXITCODE
            }
            if ($code -eq 0) {
                break
            }
            Write-Host "  exit code $code" -ForegroundColor DarkYellow
        }
    }
}

if ($code -ne 0) {
    Write-Host ""
    if ($Spawn) {
        Write-Host "py-spy exited with code $code (spawn mode)." -ForegroundColor Red
    }
    else {
        Write-Host "py-spy exited with code $code (all attach strategies failed)." -ForegroundColor Red
    }
    Write-Host "Next steps: Administrator PowerShell;  pip install -U py-spy;" -ForegroundColor Yellow
    Write-Host "  or -Spawn (close the main client first on Windows — single-instance mutex)." -ForegroundColor Yellow
    exit $code
}

Write-Host ""
Write-Host "Wrote: $OutputPath" -ForegroundColor Green
Write-Host "Open the SVG in a browser (Edge/Chrome) to inspect the flame graph."
