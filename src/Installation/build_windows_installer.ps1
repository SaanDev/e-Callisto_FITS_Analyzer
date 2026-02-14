<#
Build a Windows distributable for e-CALLISTO FITS Analyzer (v2.0).

Usage:
  powershell -ExecutionPolicy Bypass -File .\src\Installation\build_windows_installer.ps1

Optional:
  powershell -ExecutionPolicy Bypass -File .\src\Installation\build_windows_installer.ps1 -Root "C:\path\to\repo" -Version "2.0"
  powershell -ExecutionPolicy Bypass -File .\src\Installation\build_windows_installer.ps1 -SkipInstaller
#>

[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$Version = "2.0",
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

$Root = Resolve-RepoRoot -RequestedRoot $Root
$AppId = "e-callisto-fits-analyzer"
$SpecPath = Join-Path $Root "src\Installation\FITS_Analyzer_win.spec"
$InstallRequirements = Join-Path $Root "src\Installation\install_requirements.py"
$IssPath = Join-Path $Root "src\Installation\FITS_Analyzer_InnoSetup.iss"
$DistAppDir = Join-Path $Root "dist\e-Callisto FITS Analyzer"
$OutputInstaller = Join-Path $Root ("dist\e-CALLISTO_FITS_Analyzer_v{0}_Setup.exe" -f $Version)

Write-Host "==> Project root: $Root"
Write-Host "==> Building version: $Version"

if (-not (Test-Path $SpecPath)) { throw "Missing spec file: $SpecPath" }
if (-not (Test-Path $InstallRequirements)) { throw "Missing dependency script: $InstallRequirements" }
if (-not (Test-Path $IssPath) -and -not $SkipInstaller) { throw "Missing Inno Setup script: $IssPath" }

# 1) Build app folder with PyInstaller
$VenvDir = Join-Path $Root "venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    py -3 -m venv $VenvDir
}
if (-not (Test-Path $VenvPython)) {
    throw "Python venv was not created correctly: $VenvPython"
}

& $VenvPython -m pip install --upgrade pip wheel setuptools pyinstaller pyinstaller-hooks-contrib
& $VenvPython $InstallRequirements
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

& $IsccPath ("/DRepoRoot={0}" -f $Root) $IssPath

if (Test-Path $OutputInstaller) {
    Write-Host "Built installer: $OutputInstaller"
} else {
    Write-Warning "Inno Setup finished, but expected output not found at: $OutputInstaller"
    Write-Host "Check your dist folder under: $Root\dist"
}

Write-Host ("Run app: {0}" -f (Join-Path $DistAppDir "e-Callisto FITS Analyzer.exe"))
