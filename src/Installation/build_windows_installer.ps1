<#
Build a Windows distributable for e-CALLISTO FITS Analyzer.

Usage:
  powershell -ExecutionPolicy Bypass -File .\src\Installation\build_windows_installer.ps1

Optional:
  powershell -ExecutionPolicy Bypass -File .\src\Installation\build_windows_installer.ps1 -Root "C:\path\to\repo" -Version "2.1"
  powershell -ExecutionPolicy Bypass -File .\src\Installation\build_windows_installer.ps1 -SkipInstaller
#>

[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$Version = "",
    [switch]$SkipInstaller
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

function Get-AppVersion {
    param([string]$RepoRoot)

    $VersionFile = Join-Path $RepoRoot "src\version.py"
    if (-not (Test-Path $VersionFile)) {
        throw "Missing version file: $VersionFile"
    }

    $Match = Select-String -Path $VersionFile -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $Match -or $Match.Matches.Count -eq 0) {
        throw "Could not parse APP_VERSION from: $VersionFile"
    }
    return $Match.Matches[0].Groups[1].Value
}

function Find-Iscc {
    $fromPath = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }

    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) {
            return $p
        }
    }
    return $null
}

function Get-PythonBootstrapCommand {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            Exe = $python.Source
            Args = @()
        }
    }

    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        return @{
            Exe = $py.Source
            Args = @("-3")
        }
    }

    throw "No Python launcher found. Install Python and ensure either 'python.exe' or 'py.exe' is on PATH."
}

$Root = Resolve-RepoRoot -RequestedRoot $Root
if (-not $Version -or $Version.Trim().Length -eq 0) {
    $Version = Get-AppVersion -RepoRoot $Root
}
$AppId = "e-callisto-fits-analyzer"
$SpecPath = Join-Path $Root "src\Installation\FITS_Analyzer_win.spec"
$RuntimeRequirements = Join-Path $Root "src\Installation\requirements-runtime.txt"
$BuildRequirements = Join-Path $Root "src\Installation\requirements-build.txt"
$IssPath = Join-Path $Root "src\Installation\FITS_Analyzer_InnoSetup.iss"
$DistAppDir = Join-Path $Root "dist\e-Callisto FITS Analyzer"
$OutputInstaller = Join-Path $Root ("dist\e-CALLISTO_FITS_Analyzer_v{0}_Setup.exe" -f $Version)

Write-Host "==> Project root: $Root"
Write-Host "==> Building version: $Version"

if (-not (Test-Path $SpecPath)) { throw "Missing spec file: $SpecPath" }
if (-not (Test-Path $RuntimeRequirements)) { throw "Missing runtime requirements file: $RuntimeRequirements" }
if (-not (Test-Path $BuildRequirements)) { throw "Missing build requirements file: $BuildRequirements" }
if (-not (Test-Path $IssPath) -and -not $SkipInstaller) { throw "Missing Inno Setup script: $IssPath" }

# 1) Build app folder with PyInstaller
$VenvDir = Join-Path $Root ".venv-build"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$BootstrapPython = Get-PythonBootstrapCommand

if (-not (Test-Path $VenvPython)) {
    & $BootstrapPython.Exe @($BootstrapPython.Args + @("-m", "venv", $VenvDir))
}
if (-not (Test-Path $VenvPython)) {
    throw "Python venv was not created correctly: $VenvPython"
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install --requirement $BuildRequirements
& $VenvPython -m pip install --requirement $RuntimeRequirements
& $VenvPython -m PyInstaller --clean --noconfirm $SpecPath

if (-not (Test-Path $DistAppDir)) {
    throw "PyInstaller output folder not found: $DistAppDir"
}

Write-Host "Built app folder: $DistAppDir"

# 2) Build installer with Inno Setup (optional)
if ($SkipInstaller) {
    Write-Host "Skipping installer build because -SkipInstaller was provided."
    exit 0
}

$IsccPath = Find-Iscc
if (-not $IsccPath) {
    throw "ISCC.exe not found. Install Inno Setup 6 and rerun, or use -SkipInstaller."
}

& $IsccPath ("/DRepoRoot={0}" -f $Root) ("/DAppVersion={0}" -f $Version) $IssPath

if (Test-Path $OutputInstaller) {
    Write-Host "Built installer: $OutputInstaller"
} else {
    Write-Warning "Inno Setup finished, but expected output not found at: $OutputInstaller"
    Write-Host "Check your dist folder under: $Root\dist"
}

Write-Host ("Run app: {0}" -f (Join-Path $DistAppDir "e-Callisto FITS Analyzer.exe"))
