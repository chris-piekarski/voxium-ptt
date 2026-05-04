#Requires -Version 5.1
<#
.SYNOPSIS
  Record a py-spy flame graph of the Voxium Windows client during PTT activity.

.DESCRIPTION
  Resolves the repo-local .venv Python process that runs voxium.exe (not voxium server,
  not llama-server), then runs py-spy record for a fixed duration. Start PTT takes as
  soon as py-spy prints "Sampling process".

  Same-OS rule: run this on Windows when the Voxium client is Windows Python. See docs/profiling.md.

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1 -Duration 45 -OutputPath "$env:USERPROFILE\Desktop\voxium-ptt.svg"

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1 -ClientProcessId 52460 -Native
#>
param(
    [int] $Duration = 30,
    [int] $Rate = 200,
    [string] $OutputPath = "",
    [int] $ClientProcessId = 0,
    [switch] $Native
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

if ($ClientProcessId -gt 0) {
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
Start the client first, or pass -ClientProcessId <id> from Task Manager / Get-CimInstance Win32_Process.
"@
    }
    $targetPid = [int]$candidates.ProcessId
}

Write-Host "Repo:  $RepoRoot"
Write-Host "py-spy: $PySpy"
Write-Host "PID:   $targetPid"
Write-Host "Out:   $OutputPath"
Write-Host "Duration: ${Duration}s  Rate: ${Rate}/s  Native: $($Native.IsPresent)"
Write-Host ""
Write-Host "Start your PTT takes as soon as py-spy begins sampling." -ForegroundColor Yellow
Write-Host ""

$args = @(
    "record",
    "-o", $OutputPath,
    "-p", "$targetPid",
    "-d", "$Duration",
    "-r", "$Rate"
)
if ($Native) {
    $args += "--native"
}

& $PySpy @args
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host ""
    Write-Host "py-spy exited with code $code. If attach failed, try an elevated PowerShell or: pip install -U py-spy" -ForegroundColor Red
    exit $code
}

Write-Host ""
Write-Host "Wrote: $OutputPath" -ForegroundColor Green
Write-Host "Open the SVG in a browser (Edge/Chrome) to inspect the flame graph."
