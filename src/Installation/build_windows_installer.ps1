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

function Get-PythonCommand {
    $pythonExe = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonExe) {
        return $pythonExe.Source
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    throw "No Python executable found on PATH. Activate your virtual environment first."
}

function Invoke-Native {
    param(
        [Parameter(Mandatory=$true)][string]$Exe,
        [Parameter(Mandatory=$true)][string[]]$Args,
        [Parameter(Mandatory=$true)][string]$Description
    )

    & $Exe @Args
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
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
$BuildDir = Join-Path $Root "build"
$DistDir = Join-Path $Root "dist"
$OutputInstaller = Join-Path $Root ("dist\e-CALLISTO_FITS_Analyzer_v{0}_Setup.exe" -f $Version)
$PythonExe = Get-PythonCommand

Write-Host "==> Project root: $Root"
Write-Host "==> Building version: $Version"
if ($env:VIRTUAL_ENV) {
    Write-Host "==> Using active virtual environment: $env:VIRTUAL_ENV"
}
Write-Host "==> Using Python: $PythonExe"

if (-not (Test-Path $SpecPath)) { throw "Missing spec file: $SpecPath" }
if (-not (Test-Path $RuntimeRequirements)) { throw "Missing runtime requirements file: $RuntimeRequirements" }
if (-not (Test-Path $BuildRequirements)) { throw "Missing build requirements file: $BuildRequirements" }
if (-not (Test-Path $IssPath) -and -not $SkipInstaller) { throw "Missing Inno Setup script: $IssPath" }

# 1) Build app folder with PyInstaller (inside project-root build/dist)
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

Invoke-Native -Exe $PythonExe -Args @("-m", "pip", "install", "--upgrade", "pip") -Description "pip upgrade"
Invoke-Native -Exe $PythonExe -Args @("-m", "pip", "install", "--requirement", $BuildRequirements) -Description "build dependency install"
Invoke-Native -Exe $PythonExe -Args @("-m", "pip", "install", "--requirement", $RuntimeRequirements) -Description "runtime dependency install"
Invoke-Native -Exe $PythonExe -Args @(
    "-m", "PyInstaller",
    "--clean",
    "--noconfirm",
    "--distpath", $DistDir,
    "--workpath", $BuildDir,
    $SpecPath
) -Description "PyInstaller build"

$DistCandidates = @(
    (Join-Path $DistDir "e-Callisto FITS Analyzer"),
    (Join-Path $DistDir "e-callisto-fits-analyzer")
)
$DistAppDir = $DistCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not (Test-Path $DistAppDir)) {
    $found = @()
    if (Test-Path $DistDir) {
        $found = @(Get-ChildItem -Path $DistDir -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    }
    $foundText = if ($found.Count -gt 0) { ($found -join "; ") } else { "(none)" }
    throw "PyInstaller output folder not found under $DistDir. Expected one of: $($DistCandidates -join ', '). Found: $foundText"
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

Invoke-Native -Exe $IsccPath -Args @(
    ("/DRepoRoot={0}" -f $Root),
    ("/DAppVersion={0}" -f $Version),
    ("/DDistDir={0}" -f $DistAppDir),
    $IssPath
) -Description "Inno Setup build"

if (Test-Path $OutputInstaller) {
    Write-Host "Built installer: $OutputInstaller"
} else {
    Write-Warning "Inno Setup finished, but expected output not found at: $OutputInstaller"
    Write-Host "Check your dist folder under: $Root\dist"
}

Write-Host ("Run app: {0}" -f (Join-Path $DistAppDir "e-Callisto FITS Analyzer.exe"))
