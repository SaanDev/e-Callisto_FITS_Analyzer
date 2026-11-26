<#
Build the Windows on ARM (ARM64) installer of e-CALLISTO FITS Analyzer Lite.
The full app has its own script: packaging\windows\build_windows_arm64.ps1.

Output:
  lite\dist\e-CALLISTO_FITS_Analyzer_v<version>_arm64_Setup.exe
  (version as in lite\Cargo.toml, e.g. 3.3.0-lite)

Usage, from anywhere, on an ARM64 PC or an ordinary x64 PC (Rust
cross-compiles, so either works):
  powershell -ExecutionPolicy Bypass -File .\lite\packaging\windows\build_windows_arm64.ps1
Optional:
  -Version 3.3.0-lite  the version the installer shows (default: Lite's Cargo version)
  -SkipInstaller       build and stage only, without Inno Setup

What the PC needs (all free):
  - Rust from https://rustup.rs, plus the ARM64 target:
      rustup target add aarch64-pc-windows-msvc
    (on an ARM64 PC with the ARM64 rustup-init.exe it is already there).
  - Visual Studio 2022 Build Tools with "Desktop development with C++",
    "MSVC v143 - VS 2022 C++ ARM64/ARM64EC build tools" (component
    Microsoft.VisualStudio.Component.VC.Tools.ARM64) and a Windows 11 SDK:
    the ARM64 linker and libraries.
  - Inno Setup 6.3 or newer.

The build itself is build_windows_installer.ps1 -Arch arm64, as for x64;
this script checks the tools first and the result after.
#>

[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }

# A native command's output lines, without Windows PowerShell 5.1 turning its
# stderr into a terminating error; nothing if it fails. Call as @(...).
function Get-NativeOutput([string]$Exe, [string[]]$Arguments) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $Exe @Arguments 2>$null
        if ($LASTEXITCODE -eq 0) { $out }
    } catch {
    } finally {
        $ErrorActionPreference = $saved
    }
}

# The machine's own CPU; the registry value is not redirected for emulated processes.
function Get-NativeArch {
    try {
        $key = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
        $value = (Get-ItemProperty -Path $key -Name PROCESSOR_ARCHITECTURE).PROCESSOR_ARCHITECTURE
        if ($value) { return $value.ToUpperInvariant() }
    } catch { }
    return "$env:PROCESSOR_ARCHITECTURE".ToUpperInvariant()
}

# The CPU a PE file is built for.
function Get-PeMachine([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $reader = New-Object System.IO.BinaryReader($stream)
        [void]$stream.Seek(0x3C, [System.IO.SeekOrigin]::Begin)
        $peOffset = $reader.ReadInt32()
        [void]$stream.Seek($peOffset + 4, [System.IO.SeekOrigin]::Begin)
        $machine = $reader.ReadUInt16()
    } finally {
        $stream.Dispose()
    }
    switch ($machine) {
        0xAA64 { return "arm64" }
        0x8664 { return "x64" }
        0x014C { return "x86" }
        default { return ("0x{0:X4}" -f $machine) }
    }
}

$Lite = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $Version) {
    $match = Select-String -Path (Join-Path $Lite "Cargo.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $match) { throw "Could not read the version from lite\Cargo.toml" }
    $Version = $match.Matches[0].Groups[1].Value
}
$Triple = "aarch64-pc-windows-msvc"
# build_windows_installer.ps1 builds into lite\target (its -TargetDir default).
$Exe = Join-Path $Lite "target\$Triple\release\callisto-lite.exe"
$Installer = Join-Path $Lite ("dist\e-CALLISTO_FITS_Analyzer_v{0}_arm64_Setup.exe" -f $Version)

Write-Step "e-CALLISTO FITS Analyzer Lite $Version for Windows ARM64 (this PC: $(Get-NativeArch))"

# --- Preflight ---------------------------------------------------------------
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    $cargoHome = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path (Join-Path $cargoHome "cargo.exe")) {
        $env:PATH = "$cargoHome;$env:PATH"   # rustup installed, shell not reopened yet
    } else {
        throw "Rust (cargo) not found. Install it from https://rustup.rs, open a new PowerShell, and run this again."
    }
}
if (Get-Command rustup -ErrorAction SilentlyContinue) {
    $installed = @(Get-NativeOutput "rustup" @("target", "list", "--installed"))
    if ($installed.Count -gt 0 -and -not ($installed -contains $Triple)) {
        throw "Rust target $Triple is missing. Run: rustup target add $Triple"
    }
}
$rustc = @(Get-NativeOutput "rustc" @("--version"))
if ($rustc.Count -gt 0) { Write-Host "Rust: $($rustc[0])" }

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$vs = @()
if (Test-Path $vswhere) {
    $vs = @(Get-NativeOutput $vswhere @("-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.ARM64", "-property", "installationPath"))
}
if ($vs.Count -eq 0 -or -not "$($vs[0])".Trim()) {
    throw ("Visual Studio's ARM64 C++ tools were not found (needed to link Lite for ARM64). In the Visual Studio " +
           "Installer, modify Build Tools 2022 and tick 'MSVC v143 - VS 2022 C++ ARM64/ARM64EC build tools (Latest)' " +
           "and a Windows 11 SDK.")
}
Write-Host "Visual Studio: $("$($vs[0])".Trim())"

if (-not $SkipInstaller) {
    $iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
    $found = [bool]$iscc
    foreach ($p in @("C:\Program Files (x86)\Inno Setup 6\ISCC.exe", "C:\Program Files\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $p) { $found = $true }
    }
    if (-not $found) { throw "Inno Setup 6 (ISCC.exe) was not found. Install it from https://jrsoftware.org/isinfo.php, or use -SkipInstaller." }
}

# --- Build ---------------------------------------------------------------------
Write-Step "cargo and Inno Setup (build_windows_installer.ps1 -Arch arm64)"
$build = Join-Path $PSScriptRoot "build_windows_installer.ps1"
if ($SkipInstaller) {
    & $build -Arch arm64 -Version $Version -SkipInstaller
} else {
    & $build -Arch arm64 -Version $Version
}

# --- Checks --------------------------------------------------------------------
Write-Step "Checking the result"
if (-not (Test-Path $Exe)) { throw "The build finished, but $Exe is missing." }
$exeArch = Get-PeMachine $Exe
Write-Host "Executable: $Exe ($exeArch)"
if ($exeArch -ne "arm64") { throw "Lite's executable is $exeArch, not arm64." }
if (-not $SkipInstaller -and -not (Test-Path $Installer)) { throw "Inno Setup finished, but $Installer is missing." }

Write-Step "Done"
if ($SkipInstaller) {
    Write-Host "Lite (staged, no installer): $(Join-Path $Lite 'dist\stage-arm64')"
} else {
    Write-Host "Lite: $Installer" -ForegroundColor Green
}
