#Requires -Version 5.1
<#
  Windows: .venv, pip install -e ., sounddevice check.
  Logs: <repo>\logs\voxium-windows-setup.log  and  %TEMP%\voxium-windows-setup.log
#>
param(
    [switch] $Dev
)
$ErrorActionPreference = "Continue"
# Not required: Setup-Voxium.cmd passes -ExecutionPolicy Bypass. Skipping Set-ExecutionPolicy avoids
# failures on GPO-locked systems (same rationale as Voxium.ps1).

function Write-SetupLog {
    param([string]$Line)
    foreach ($p in $script:SetupLogPaths) {
        try { $Line | Out-File -FilePath $p -Append -Encoding utf8 -ErrorAction SilentlyContinue } catch { }
    }
}

function Exit-SetupFail {
    param([string]$Message)
    Write-Host ""
    Write-Host $Message -ForegroundColor Red
    Write-SetupLog "ERROR: $Message"
    Write-Host "Logs: $($script:SetupLogPaths[0])" -ForegroundColor Yellow
    Write-Host "      $($script:SetupLogPaths[1])" -ForegroundColor Yellow
    exit 1
}

function Invoke-Pip {
    param(
        [string]$Python,
        [string[]]$PipArgs,
        [string]$Label
    )
    Write-SetupLog "=== $Label"
    Write-SetupLog ("  {0} -m pip {1}" -f $Python, ($PipArgs -join ' '))
    $out = & $Python -m pip @PipArgs 2>&1
    $ec = $LASTEXITCODE
    # Do not emit $out to the pipeline — that would become (part of) the function return value and break $ec.
    foreach ($line in @($out)) {
        Write-SetupLog "$line"
        Write-Host $line
    }
    return [int]$ec
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
$null = New-Item -ItemType Directory -Force -Path $LogDir
$script:SetupLogPaths = @(
    (Join-Path $LogDir "voxium-windows-setup.log"),
    (Join-Path $env:TEMP "voxium-windows-setup.log")
)
"=== Voxium setup $(Get-Date -Format o) ===" | ForEach-Object { Write-SetupLog $_ }
Write-SetupLog "Repo: $RepoRoot"
Write-Host "Logging to: $($script:SetupLogPaths[0])" -ForegroundColor DarkCyan
Write-Host "  (copy)   $($script:SetupLogPaths[1])" -ForegroundColor DarkGray

$PyProject = Join-Path $RepoRoot "pyproject.toml"
if (-not (Test-Path -LiteralPath $PyProject)) {
    Exit-SetupFail "pyproject.toml not found. Keep scripts under <repo>\scripts\windows\."
}
Write-Host "Repository root: $RepoRoot" -ForegroundColor DarkGray

$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvCfg = Join-Path $VenvDir "pyvenv.cfg"
$PipLog = Join-Path $LogDir "pip-editable-install.log"

$needNewVenv = $false
if (Test-Path -LiteralPath $VenvDir) {
    if (-not (Test-Path -LiteralPath $VenvCfg)) {
        Write-SetupLog "BROKEN .venv: missing pyvenv.cfg (WSL venv on Windows, or partial copy). Will remove and recreate."
        Write-Host "Removing broken .venv (no pyvenv.cfg)…" -ForegroundColor Yellow
        $needNewVenv = $true
    } elseif (Test-Path -LiteralPath $VenvPython) {
        $smoke = & $VenvPython -c "import sys; print(sys.prefix)" 2>&1
        if ($LASTEXITCODE -ne 0 -or "$smoke" -match "No pyvenv\.cfg") {
            Write-SetupLog "BROKEN .venv: python.exe failed: $smoke"
            Write-Host "Removing broken .venv (python cannot start)…" -ForegroundColor Yellow
            $needNewVenv = $true
        }
    } else {
        $needNewVenv = $true
    }
    if ($needNewVenv) {
        try {
            Remove-Item -LiteralPath $VenvDir -Recurse -Force -ErrorAction Stop
        } catch {
            Exit-SetupFail "Could not remove .venv: $_ — close programs using it, then delete the folder by hand and re-run."
        }
    }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $pyCmd) {
        Write-Host "Creating venv: py -3 -m venv .venv" -ForegroundColor Cyan
        Write-SetupLog "=== py -3 -m venv"
        $o = & $pyCmd.Source @("-3", "-m", "venv", $VenvDir) 2>&1
        foreach ($line in @($o)) { Write-SetupLog "$line"; Write-Host $line }
        if ($LASTEXITCODE -ne 0) { Exit-SetupFail "py -3 -m venv failed (exit $($LASTEXITCODE)). See logs." }
    } elseif ($null -ne (Get-Command python -ErrorAction SilentlyContinue)) {
        $pyc = (Get-Command python).Source
        Write-Host "Creating venv: python -m venv .venv" -ForegroundColor Cyan
        Write-SetupLog "=== python -m venv"
        $o = & $pyc -m venv $VenvDir 2>&1
        foreach ($line in @($o)) { Write-SetupLog "$line"; Write-Host $line }
        if ($LASTEXITCODE -ne 0) { Exit-SetupFail "python -m venv failed (exit $($LASTEXITCODE)). See logs." }
    } else {
        Exit-SetupFail "Python not on PATH. Install 3.10+ from https://www.python.org/ (Add to PATH)."
    }
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Exit-SetupFail "Venv missing: $VenvPython"
    }
}

Write-Host "Upgrading pip / setuptools / wheel…" -ForegroundColor Cyan
$ec = Invoke-Pip -Python $VenvPython -PipArgs @("install", "-U", "pip", "setuptools", "wheel") -Label "pip upgrade"
if ($ec -ne 0) { Exit-SetupFail "pip upgrade failed (exit $ec). See logs." }

Write-Host "Installing Voxium (editable)…" -ForegroundColor Cyan
if ($Dev) {
    $ec = Invoke-Pip -Python $VenvPython -PipArgs @("install", "-e", ".[dev]", "-v", "--log", $PipLog) -Label "pip install -e .[dev]"
} else {
    $ec = Invoke-Pip -Python $VenvPython -PipArgs @("install", "-e", ".", "-v", "--log", $PipLog) -Label "pip install -e ."
}
if ($ec -ne 0) {
    Exit-SetupFail "pip install -e . failed (exit $ec). Open: $PipLog  (OneDrive: pause sync or use C:\src\…)"
}

Write-Host "Checking sounddevice…" -ForegroundColor Cyan
$probe = "import sounddevice as s; s.query_devices()"
$po = & $VenvPython -c $probe 2>&1
$ec = $LASTEXITCODE
foreach ($line in @($po)) { Write-SetupLog "$line"; Write-Host $line }
if ($ec -ne 0) {
    Write-Host "Reinstalling sounddevice…" -ForegroundColor Yellow
    $ec2 = Invoke-Pip -Python $VenvPython -PipArgs @("install", "--force-reinstall", "--no-cache-dir", "sounddevice>=0.4.6") -Label "reinstall sounddevice"
    if ($ec2 -ne 0) { Exit-SetupFail "Could not reinstall sounddevice (exit $ec2)." }
    $po2 = & $VenvPython -c $probe 2>&1
    $ec3 = $LASTEXITCODE
    foreach ($line in @($po2)) { Write-SetupLog "$line"; Write-Host $line }
    if ($ec3 -ne 0) {
        Write-SetupLog "PortAudio still failing"
        Write-Host "PortAudio still fails. Install VC++ x64 redist; set a default microphone." -ForegroundColor Yellow
        exit 1
    }
}

Write-Host ""
Write-Host "Setup finished." -ForegroundColor Green
Write-Host "  .\.venv\Scripts\Activate.ps1  then  voxium run" -ForegroundColor Cyan
Write-Host "  or  .\scripts\windows\Voxium.cmd run" -ForegroundColor Cyan
Write-SetupLog "=== success ==="
Write-Host ""
