<#
Recreate the Windows virtual environment and reinstall the pinned runtime stack.

Usage from the project root:
  powershell -ExecutionPolicy Bypass -File .\src\Installation\repair_windows_venv.ps1
#>

[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$Venv = "venv",
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-RepoRoot {
    param([string]$RequestedRoot)

    if ($RequestedRoot -and $RequestedRoot.Trim().Length -gt 0) {
        return (Resolve-Path $RequestedRoot).Path
    }
    return (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

function Resolve-VenvPath {
    param(
        [string]$RepoRoot,
        [string]$RequestedVenv
    )

    if ([System.IO.Path]::IsPathRooted($RequestedVenv)) {
        return $RequestedVenv
    }
    return (Join-Path $RepoRoot $RequestedVenv)
}

function Resolve-PythonCommand {
    param([string]$RequestedVersion)

    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source "-$RequestedVersion" -c "import sys; print(sys.version)" *> $null
        if ($LASTEXITCODE -eq 0) {
            return @($py.Source, "-$RequestedVersion")
        }

        & $py.Source "-3" -c "import sys; print(sys.version)" *> $null
        if ($LASTEXITCODE -eq 0) {
            return @($py.Source, "-3")
        }
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }

    throw "No Python executable found. Install Python $RequestedVersion or add Python to PATH."
}

function Invoke-Native {
    param(
        [Parameter(Mandatory=$true)][string]$Exe,
        [string[]]$PrefixArgs = @(),
        [string[]]$NativeArgs = @(),
        [Parameter(Mandatory=$true)][string]$Description
    )

    & $Exe @PrefixArgs @NativeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Remove-VenvDirectory {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    if (Get-Command deactivate -ErrorAction SilentlyContinue) {
        try {
            deactivate
        } catch {
            Write-Warning "Could not deactivate the current shell automatically: $($_.Exception.Message)"
        }
    }

    Write-Host "==> Removing existing virtual environment: $Path"
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
    } catch {
        Write-Warning "PowerShell Remove-Item failed: $($_.Exception.Message)"
        Write-Host "==> Retrying with cmd.exe rmdir ..."
        & cmd.exe /d /c "rmdir /s /q `"$Path`""
        if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $Path)) {
            throw (
                "Could not remove '$Path'. Close running Python apps, VS Code terminals, "
                + "and file explorer windows opened inside the venv, then run this script again."
            )
        }
    }
}

$Root = Resolve-RepoRoot -RequestedRoot $Root
$VenvPath = Resolve-VenvPath -RepoRoot $Root -RequestedVenv $Venv
$RequirementsInstaller = Join-Path $Root "src\Installation\install_requirements.py"
$AppEntry = Join-Path $Root "src\UI\main.py"

if (-not (Test-Path -LiteralPath $RequirementsInstaller)) {
    throw "Missing installer script: $RequirementsInstaller"
}

Write-Host "==> Project root: $Root"
Write-Host "==> Virtual environment: $VenvPath"

Remove-VenvDirectory -Path $VenvPath

$PythonCommand = @(Resolve-PythonCommand -RequestedVersion $PythonVersion)
$PythonExe = $PythonCommand[0]
$PythonPrefixArgs = @($PythonCommand | Select-Object -Skip 1)

Write-Host "==> Python launcher: $PythonExe $($PythonPrefixArgs -join ' ')"

Write-Host "==> Creating virtual environment ..."
Invoke-Native -Exe $PythonExe -PrefixArgs $PythonPrefixArgs -NativeArgs @("-m", "venv", $VenvPath) -Description "venv creation"

$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Virtual environment was created, but python.exe is missing: $VenvPython"
}

Write-Host "==> Upgrading pip ..."
Invoke-Native -Exe $VenvPython -NativeArgs @("-m", "pip", "install", "--upgrade", "pip") -Description "pip upgrade"

Write-Host "==> Installing runtime requirements ..."
Invoke-Native -Exe $VenvPython -NativeArgs @($RequirementsInstaller) -Description "runtime dependency install"

Write-Host ""
Write-Host "Repair complete. Start the app with:"
Write-Host "  `"$VenvPython`" `"$AppEntry`""
