<#
Build the Windows installer for e-CALLISTO FITS Analyzer Lite.

Builds the release executable, stages it as "e-CALLISTO FITS Analyzer Lite.exe"
with the licences, and compiles Lite_InnoSetup.iss with Inno Setup 6. The
installer lands in lite\dist.

Usage (from anywhere):
  powershell -ExecutionPolicy Bypass -File lite\packaging\windows\build_windows_installer.ps1
Optional:
  -Version 3.2.0-lite the version shown by the installer (default: Lite's Cargo version)
  -TargetDir <dir>    cargo's target folder (default: lite\target)
  -SkipInstaller      stage only
#>

[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$TargetDir = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Lite = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Repo = (Resolve-Path (Join-Path $Lite "..")).Path

if (-not $Version) {
    $match = Select-String -Path (Join-Path $Lite "Cargo.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $match) { throw "Could not read the version from lite\Cargo.toml" }
    $Version = $match.Matches[0].Groups[1].Value
}
if (-not $TargetDir) { $TargetDir = Join-Path $Lite "target" }

Write-Host "Building e-CALLISTO FITS Analyzer Lite $Version"
Push-Location $Lite
try {
    $env:CARGO_TARGET_DIR = $TargetDir
    & cargo build --release -p callisto-lite
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}

$Stage = Join-Path $Lite "dist\stage"
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force $Stage | Out-Null
Copy-Item (Join-Path $TargetDir "release\callisto-lite.exe") (Join-Path $Stage "e-CALLISTO FITS Analyzer Lite.exe")
Copy-Item (Join-Path $Repo "LICENSE") (Join-Path $Stage "LICENSE.txt")
Copy-Item (Join-Path $Lite "crates\callisto-core\assets\fonts\LICENSE_DEJAVU") (Join-Path $Stage "LICENSE_DEJAVU_FONTS.txt")

if ($SkipInstaller) {
    Write-Host "Staged in $Stage"
    return
}

$Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
if (-not $Iscc) {
    foreach ($p in @("C:\Program Files (x86)\Inno Setup 6\ISCC.exe", "C:\Program Files\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $p) { $Iscc = $p; break }
    }
}
if (-not $Iscc) { throw "Inno Setup 6 (ISCC.exe) was not found. Install it from https://jrsoftware.org/isinfo.php" }

$Numeric = ($Version -split '-')[0]
& $Iscc "/DAppVersion=$Version" "/DNumericVersion=$Numeric" "/DStageDir=$Stage" "/DRepoRoot=$Repo" (Join-Path $PSScriptRoot "Lite_InnoSetup.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed ($LASTEXITCODE)" }
Write-Host "Installer: $(Join-Path $Lite "dist\e-CALLISTO_FITS_Analyzer_v$($Version)_Setup.exe")"
