<#
Build the Windows installer for e-CALLISTO FITS Analyzer Lite.

Builds the release executable, stages it as "e-CALLISTO FITS Analyzer Lite.exe"
with the licences, and compiles Lite_InnoSetup.iss with Inno Setup 6. The
installer lands in lite\dist.

Usage (from anywhere):
  powershell -ExecutionPolicy Bypass -File lite\packaging\windows\build_windows_installer.ps1
Optional:
  -Version 3.3.0-lite the version shown by the installer (default: Lite's Cargo version)
  -TargetDir <dir>    cargo's target folder (default: lite\target)
  -Arch arm64         build for Windows on ARM: e-CALLISTO_FITS_Analyzer_v<version>_arm64_Setup.exe.
                      Works on an ARM64 PC or cross-compiles from an x64 one; either way it needs
                      `rustup target add aarch64-pc-windows-msvc` and the Visual Studio
                      "MSVC v143 - VS 2022 C++ ARM64 build tools" component.
  -SkipInstaller      stage only
#>

[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$TargetDir = "",
    [ValidateSet("x64", "arm64")]
    [string]$Arch = "x64",
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

# x64 keeps the host build (target\release), as before. Anything else names
# its target triple, so an ARM64 PC building x64 (or the reverse) gets the
# CPU the installer says it is for.
# The machine's own CPU (AMD64 or ARM64): the registry value is not
# redirected for emulated processes, unlike $env:PROCESSOR_ARCHITECTURE.
$HostArch = "$env:PROCESSOR_ARCHITECTURE"
try {
    $HostArch = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" -Name PROCESSOR_ARCHITECTURE).PROCESSOR_ARCHITECTURE
} catch { }
$Triple = ""
if ($Arch -eq "arm64") {
    $Triple = "aarch64-pc-windows-msvc"
} elseif ($HostArch -ne "AMD64") {
    $Triple = "x86_64-pc-windows-msvc"
}
$CargoArgs = @("build", "--release", "-p", "callisto-lite")
$ReleaseDir = Join-Path $TargetDir "release"
if ($Triple) {
    if (Get-Command rustup -ErrorAction SilentlyContinue) {
        $installed = @(& rustup target list --installed)
        if ($LASTEXITCODE -eq 0 -and -not ($installed -contains $Triple)) {
            throw "Rust target $Triple is not installed. Run: rustup target add $Triple"
        }
    }
    $CargoArgs += @("--target", $Triple)
    $ReleaseDir = Join-Path $TargetDir "$Triple\release"
}

Write-Host "Building e-CALLISTO FITS Analyzer Lite $Version ($Arch)"
Push-Location $Lite
try {
    $env:CARGO_TARGET_DIR = $TargetDir
    & cargo @CargoArgs
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}

$Stage = Join-Path $Lite "dist\stage"
if ($Arch -ne "x64") { $Stage = Join-Path $Lite "dist\stage-$Arch" }
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force $Stage | Out-Null
Copy-Item (Join-Path $ReleaseDir "callisto-lite.exe") (Join-Path $Stage "e-CALLISTO FITS Analyzer Lite.exe")
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
& $Iscc "/DAppVersion=$Version" "/DNumericVersion=$Numeric" "/DStageDir=$Stage" "/DRepoRoot=$Repo" "/DArch=$Arch" (Join-Path $PSScriptRoot "Lite_InnoSetup.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed ($LASTEXITCODE)" }
$Suffix = if ($Arch -eq "x64") { "" } else { "_$Arch" }
Write-Host "Installer: $(Join-Path $Lite "dist\e-CALLISTO_FITS_Analyzer_v$($Version)$($Suffix)_Setup.exe")"
