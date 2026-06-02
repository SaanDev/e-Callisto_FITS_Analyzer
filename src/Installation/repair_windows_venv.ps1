<#
Recreate the Windows virtual environment and reinstall the pinned runtime stack.

Usage from the project root:
  powershell -ExecutionPolicy Bypass -File .\src\Installation\repair_windows_venv.ps1
#>

param(
    [string]$Root = "",
    [string]$Venv = "venv",
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
} else {
    $Root = (Resolve-Path $Root).Path
}

if ([System.IO.Path]::IsPathRooted($Venv)) {
    $VenvPath = $Venv
} else {
    $VenvPath = Join-Path $Root $Venv
}

$RequirementsInstaller = Join-Path $Root "src\Installation\install_requirements.py"
$AppEntry = Join-Path $Root "src\UI\main.py"

if (-not (Test-Path -LiteralPath $RequirementsInstaller)) {
    throw "Missing installer script: $RequirementsInstaller"
}

Write-Host "==> Project root: $Root"
Write-Host "==> Virtual environment: $VenvPath"

$deactivateCommand = Get-Command deactivate -ErrorAction SilentlyContinue
if ($deactivateCommand) {
    try {
        deactivate
    } catch {
        Write-Warning "Could not deactivate the current shell automatically."
    }
}

if (Test-Path -LiteralPath $VenvPath) {
    Write-Host "==> Removing existing virtual environment ..."
    $removed = $false

    try {
        Remove-Item -LiteralPath $VenvPath -Recurse -Force -ErrorAction Stop
        $removed = $true
    } catch {
        Write-Warning "PowerShell Remove-Item failed. Retrying with cmd.exe rmdir."
    }

    if (-not $removed) {
        $rmdirCommand = 'rmdir /s /q "' + $VenvPath + '"'
        & cmd.exe /d /c $rmdirCommand
    }

    if (Test-Path -LiteralPath $VenvPath) {
        throw "Could not remove '$VenvPath'. Close Python, VS Code terminals, and file explorer windows opened inside the venv, then run this script again."
    }
}

Write-Host "==> Creating virtual environment ..."
$created = $false
$pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue

if ($pyLauncher) {
    $pyExe = $pyLauncher.Source
    & $pyExe "-$PythonVersion" -m venv $VenvPath
    if ($LASTEXITCODE -eq 0) {
        $created = $true
    }

    if (-not $created) {
        & $pyExe -3 -m venv $VenvPath
        if ($LASTEXITCODE -eq 0) {
            $created = $true
        }
    }
}

if (-not $created) {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "No Python executable found. Install Python $PythonVersion or add Python to PATH."
    }

    $pythonExe = $python.Source
    & $pythonExe -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Virtual environment creation failed with exit code $LASTEXITCODE."
    }
}

$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Virtual environment was created, but python.exe is missing: $VenvPython"
}

Write-Host "==> Upgrading pip ..."
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed with exit code $LASTEXITCODE."
}

Write-Host "==> Installing runtime requirements ..."
& $VenvPython $RequirementsInstaller
if ($LASTEXITCODE -ne 0) {
    throw "runtime dependency install failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Repair complete. Start the app with:"
Write-Host "  `"$VenvPython`" `"$AppEntry`""
