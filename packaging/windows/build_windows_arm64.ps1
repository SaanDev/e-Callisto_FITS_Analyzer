<#
Build the Windows on ARM (ARM64) installer of e-CALLISTO FITS Analyzer (the
full app). Lite has its own script:
lite\packaging\windows\build_windows_arm64.ps1.

Output:
  dist\e-CALLISTO_FITS_Analyzer_v<version>_arm64_Setup.exe
  (the app folder it installs: dist\win-arm64\e-Callisto FITS Analyzer\)

Usage, on a Windows 11 ARM64 PC (or VM), from anywhere:
  powershell -ExecutionPolicy Bypass -File .\packaging\windows\build_windows_arm64.ps1
Optional:
  -Python <path>    the ARM64 python.exe to build with (default: py -V:3.13-arm64, then 3.12)
  -Version 3.3.0    the full app's version (default: src\version.py)
  -SkipSmokeTest    do not start the built app to check that it opens
  -SkipInstaller    stop after the app folders, without Inno Setup

What the PC needs (all free):
  - Python 3.12 or 3.13, the "Windows installer (ARM64)" from python.org.
    PyInstaller cannot cross-build, so an x64 Python running under emulation
    would produce an x64 app; the script refuses one.
  - Visual Studio 2022 Build Tools with "Desktop development with C++" and
    "MSVC v143 - VS 2022 C++ ARM64/ARM64EC build tools" (component
    Microsoft.VisualStudio.Component.VC.Tools.ARM64) plus a Windows 11 SDK.
  - Rust from https://rustup.rs (the ARM64 rustup-init.exe), for streamtracer
    (PFSS field lines), which PyPI has no ARM64 wheel of.
  - Inno Setup 6.3 or newer (it runs under x64 emulation, which is fine).

Why it differs from build_windows_installer.ps1 (checked against PyPI on
2026-11-26, for Python 3.13 / win_arm64):
  - PyPI has no Windows ARM64 wheel of pandas 2.3.3, scikit-learn 1.7.2,
    reproject, streamtracer, pyerfa, astropy-healpix, numcodecs or
    google-crc32c, so pip compiles them here with MSVC and Rust. The first
    run takes 20 to 40 minutes; pip keeps the wheels it built in its cache,
    so later runs are quick.
  - Two pins move up, because their pinned version has no ARM64 build and
    one cannot be made here: netCDF4 1.7.3 -> 1.7.4 (1.7.3 would need HDF5
    and netCDF-C compiled for ARM64) and imageio-ffmpeg 0.6.0 -> 0.8.0 (the
    first that ships an ARM64 ffmpeg.exe). See $PinOverrides below.
  - PySide6's Windows ARM64 wheels have no QtWebEngine. The CME movie helper
    window cannot start, and the app opens CME movies in the web browser
    instead (launch_movie_with_fallback in src\ui\cme\soho_lasco_viewer.py).
    Everything else is the same app.
#>

[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$Version = "",
    [string]$Python = "",
    [switch]$SkipSmokeTest,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Pins in requirements-runtime.txt replaced for this build only, and why.
$PinOverrides = [ordered]@{
    "netCDF4"        = "1.7.4"   # first netCDF4 with a Windows ARM64 wheel
    "imageio-ffmpeg" = "0.8.0"   # first imageio-ffmpeg that ships ffmpeg-win-aarch64
}

function Write-Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Description failed with exit code $LASTEXITCODE." }
}

# Run a native command for its output lines, without Windows PowerShell 5.1
# turning its stderr into a terminating error. Outputs nothing if it fails;
# call it as @(Get-NativeOutput ...) so one line still has a Count.
# (Arguments with double quotes in them reach native programs mangled in 5.1,
# so none are passed here.)
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

# The machine's own CPU. The registry value is not redirected for emulated
# processes, unlike $env:PROCESSOR_ARCHITECTURE in an x64 PowerShell.
function Get-NativeArch {
    try {
        $key = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
        $value = (Get-ItemProperty -Path $key -Name PROCESSOR_ARCHITECTURE).PROCESSOR_ARCHITECTURE
        if ($value) { return $value.ToUpperInvariant() }
    } catch { }
    if ($env:PROCESSOR_ARCHITEW6432) { return $env:PROCESSOR_ARCHITEW6432.ToUpperInvariant() }
    return "$env:PROCESSOR_ARCHITECTURE".ToUpperInvariant()
}

# The CPU a PE file (.exe/.dll/.pyd) is built for.
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

function Find-Iscc {
    $fromPath = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($fromPath) { return $fromPath.Source }
    foreach ($p in @("C:\Program Files (x86)\Inno Setup 6\ISCC.exe", "C:\Program Files\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# An ARM64 CPython 3.12+: the one asked for, else the py launcher's
# 3.13-arm64 / 3.12-arm64, else python.exe on PATH.
function Resolve-ArmPython([string]$Requested) {
    $probe = "import sys, sysconfig; print(sysconfig.get_platform()); print('%d.%d' % sys.version_info[:2]); print(sys.executable)"
    $candidates = New-Object System.Collections.Generic.List[object]
    if ($Requested) {
        $candidates.Add(@($Requested))
    } else {
        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            $candidates.Add(@("py.exe", "-V:3.13-arm64"))
            $candidates.Add(@("py.exe", "-V:3.12-arm64"))
        }
        $onPath = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($onPath) { $candidates.Add(@($onPath.Source)) }
    }
    $seen = @()
    foreach ($cand in $candidates) {
        $exe = $cand[0]
        $prefix = @($cand | Select-Object -Skip 1)
        $out = @(Get-NativeOutput $exe ($prefix + @("-c", $probe)))
        if ($out.Count -lt 3) { continue }
        $plat = "$($out[0])".Trim(); $ver = [version]"$($out[1])".Trim(); $path = "$($out[2])".Trim()
        $seen += "$path ($plat, $ver)"
        if ($plat -eq "win-arm64" -and $ver -ge [version]"3.12") { return $path }
    }
    $found = if ($seen.Count) { $seen -join "; " } else { "none" }
    throw ("No ARM64 Python 3.12+ found (looked at: $found). Install the python.org " +
           "'Windows installer (ARM64)' for Python 3.13, or pass -Python C:\path\to\python.exe.")
}

# Bring the MSVC ARM64 compiler into this session, as a Developer Prompt would.
function Import-VsArm64Environment {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) {
        throw "Visual Studio Build Tools not found (no vswhere.exe). Install VS 2022 Build Tools with the C++ ARM64 build tools."
    }
    $vs = @(Get-NativeOutput $vswhere @("-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.ARM64", "-property", "installationPath"))
    if ($vs.Count -eq 0 -or -not "$($vs[0])".Trim()) {
        throw ("Visual Studio has no ARM64 C++ tools. In the Visual Studio Installer, modify Build Tools 2022 and tick " +
               "'MSVC v143 - VS 2022 C++ ARM64/ARM64EC build tools (Latest)' and a Windows 11 SDK.")
    }
    $vcvars = Join-Path "$($vs[0])".Trim() "VC\Auxiliary\Build\vcvarsall.bat"
    if (-not (Test-Path $vcvars)) { throw "Missing $vcvars" }
    # Native ARM64 tools on an ARM64 PC; the x64-hosted cross tools elsewhere.
    $vcArch = if ((Get-NativeArch) -eq "ARM64") { "arm64" } else { "x64_arm64" }
    $lines = & cmd.exe /s /c "`"$vcvars`" $vcArch >nul 2>&1 && set"
    if ($LASTEXITCODE -ne 0) { throw "vcvarsall.bat $vcArch failed ($LASTEXITCODE)." }
    foreach ($line in $lines) {
        if ($line -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process") }
    }
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw "cl.exe is not on PATH after vcvarsall.bat $vcArch." }
    Write-Host "MSVC: $vcvars ($vcArch)"
}

function Assert-RustArm64 {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        throw "Rust (cargo) not found. Install it from https://rustup.rs (ARM64 rustup-init.exe), then open a new PowerShell."
    }
    if (Get-Command rustup -ErrorAction SilentlyContinue) {
        $installed = @(Get-NativeOutput "rustup" @("target", "list", "--installed"))
        if ($installed.Count -gt 0 -and -not ($installed -contains "aarch64-pc-windows-msvc")) {
            throw "Rust target aarch64-pc-windows-msvc is missing. Run: rustup target add aarch64-pc-windows-msvc"
        }
    }
    $rustHost = @(Get-NativeOutput "rustc" @("-vV"))
    if ($rustHost.Count -gt 0) { Write-Host ("Rust: " + (@($rustHost | Where-Object { $_ -like "release:*" -or $_ -like "host:*" }) -join ", ")) }
}

# ---------------------------------------------------------------------------

$Root = if ($Root) { (Resolve-Path $Root).Path } else { (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path }
if (-not $Version) {
    $match = Select-String -Path (Join-Path $Root "src\version.py") -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $match) { throw "Could not read APP_VERSION from src\version.py" }
    $Version = $match.Matches[0].Groups[1].Value
}

$NativeArch = Get-NativeArch
Write-Step "e-CALLISTO FITS Analyzer $Version for Windows ARM64 (this PC: $NativeArch)"
Write-Host "Project root: $Root"

# Preflight: every tool is checked before the long steps start.
if ($NativeArch -ne "ARM64") {
    throw ("The full app must be built on an ARM64 PC: PyInstaller cannot cross-build, so this PC ($NativeArch) " +
           "would produce an x64 app. Build it on Windows 11 ARM64. (Lite can be cross-built here: " +
           "lite\packaging\windows\build_windows_arm64.ps1.)")
}
Import-VsArm64Environment
Assert-RustArm64
$Iscc = $null
if (-not $SkipInstaller) {
    $Iscc = Find-Iscc
    if (-not $Iscc) { throw "ISCC.exe not found. Install Inno Setup 6 (https://jrsoftware.org/isinfo.php), or use -SkipInstaller." }
}

$PythonExe = Resolve-ArmPython $Python
Write-Host "Python: $PythonExe"

$SpecPath = Join-Path $Root "packaging\pyinstaller\FITS_Analyzer_win.spec"
$RuntimeRequirements = Join-Path $Root "requirements\requirements-runtime.txt"
$BuildRequirements = Join-Path $Root "requirements\requirements-build.txt"
$IssPath = Join-Path $Root "packaging\windows\FITS_Analyzer_InnoSetup.iss"
$Venv = Join-Path $Root ".venv-build-win-arm64"
$WorkRoot = Join-Path $Root "build\win-arm64"
$DistRoot = Join-Path $Root "dist\win-arm64"
$AppDir = Join-Path $DistRoot "e-Callisto FITS Analyzer"
$AppExe = Join-Path $AppDir "e-Callisto FITS Analyzer.exe"
$Installer = Join-Path $Root ("dist\e-CALLISTO_FITS_Analyzer_v{0}_arm64_Setup.exe" -f $Version)
foreach ($p in @($SpecPath, $RuntimeRequirements, $BuildRequirements, $IssPath)) {
    if (-not (Test-Path $p)) { throw "Missing $p" }
}

# 1) A fresh build environment, as the Linux build does.
Write-Step "Creating the build environment $Venv"
if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
Invoke-Native $PythonExe @("-m", "venv", $Venv) "Creating the virtual environment"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

# 2) requirements-runtime.txt with the ARM64 pin overrides.
New-Item -ItemType Directory -Force $WorkRoot | Out-Null
$ArmRequirements = Join-Path $WorkRoot "requirements-runtime-win-arm64.txt"
$lines = Get-Content $RuntimeRequirements
foreach ($name in $PinOverrides.Keys) {
    $pattern = "^\s*" + [regex]::Escape($name) + "\s*=="
    $hits = @($lines | Where-Object { $_ -match $pattern })
    if ($hits.Count -ne 1) { throw "Expected one '$name==' line in requirements-runtime.txt, found $($hits.Count)." }
    $lines = $lines | ForEach-Object { if ($_ -match $pattern) { "$name==$($PinOverrides[$name])  # Windows ARM64 override" } else { $_ } }
    Write-Host ("Pin override: {0} -> {1}" -f $hits[0].Trim(), $PinOverrides[$name])
}
Set-Content -Path $ArmRequirements -Value $lines -Encoding ASCII

# 3) Dependencies. Packages without an ARM64 wheel compile here.
Write-Step "Installing dependencies (the first run compiles several packages: 20 to 40 minutes)"
$env:CRC32C_PURE_PYTHON = "1"                     # google-crc32c: pure Python, no crc32c C library to build
$env:CARGO_BUILD_TARGET = "aarch64-pc-windows-msvc"  # streamtracer (maturin) builds for ARM64 even with an x64 rustc
try {
    Invoke-Native $VenvPython @("-m", "pip", "install", "--upgrade", "pip") "pip upgrade"
    Invoke-Native $VenvPython @("-m", "pip", "install", "--requirement", $BuildRequirements) "Build dependency install"
    Invoke-Native $VenvPython @("-m", "pip", "install", "--prefer-binary", "--requirement", $ArmRequirements) "Runtime dependency install"
} finally {
    Remove-Item Env:\CARGO_BUILD_TARGET -ErrorAction SilentlyContinue
    Remove-Item Env:\CRC32C_PURE_PYTHON -ErrorAction SilentlyContinue
}

# 4) The compiled packages import, before the long PyInstaller run. From a
# file: Windows PowerShell 5.1 mangles double quotes in a -c argument.
Write-Step "Checking the compiled packages"
$checkFile = Join-Path $WorkRoot "check_imports.py"
Set-Content -Path $checkFile -Encoding ASCII -Value @'
import importlib, sysconfig
assert sysconfig.get_platform() == "win-arm64", sysconfig.get_platform()
mods = ["numpy", "scipy", "pandas", "sklearn", "astropy", "erfa", "astropy_healpix", "numcodecs",
        "reproject", "streamtracer", "netCDF4", "cftime", "skimage", "matplotlib", "lxml", "aiohttp",
        "PySide6.QtWidgets", "PySide6.QtSvg", "PySide6.QtMultimedia", "sunpy.map", "sunkit_magex.pfss", "aiapy.calibrate"]
for m in mods:
    importlib.import_module(m)
import imageio_ffmpeg, PySide6
print("imports ok; PySide6", PySide6.__version__, "; ffmpeg:", imageio_ffmpeg.get_ffmpeg_exe())
try:
    import PySide6.QtWebEngineWidgets
    print("QtWebEngine: present")
except ImportError:
    print("QtWebEngine: absent (expected on Windows ARM64; CME movies open in the browser)")
'@
Invoke-Native $VenvPython @($checkFile) "Import check"

# 5) The app folder.
Write-Step "PyInstaller"
if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
Invoke-Native $VenvPython @(
    "-m", "PyInstaller", "--clean", "--noconfirm",
    "--distpath", $DistRoot,
    "--workpath", (Join-Path $WorkRoot "pyinstaller"),
    $SpecPath
) "PyInstaller build"
if (-not (Test-Path $AppExe)) { throw "PyInstaller finished, but $AppExe is missing." }

$exeArch = Get-PeMachine $AppExe
$pyDll = Get-ChildItem -Path $AppDir -Recurse -Filter "python3*.dll" | Select-Object -First 1
$dllArch = if ($pyDll) { Get-PeMachine $pyDll.FullName } else { "missing" }
Write-Host "Built $AppExe (exe: $exeArch, $(if ($pyDll) { $pyDll.Name } else { 'python3*.dll' }): $dllArch)"
if ($exeArch -ne "arm64" -or $dllArch -ne "arm64") { throw "The app is not ARM64 throughout (exe $exeArch, Python DLL $dllArch)." }

# 6) It opens. Off screen, so nothing appears; the CME helper is not tried,
# as it cannot start without QtWebEngine.
if (-not $SkipSmokeTest) {
    Write-Step "Smoke test: starting the app for 10 seconds"
    $savedQpa = $env:QT_QPA_PLATFORM
    $env:QT_QPA_PLATFORM = "offscreen"
    try {
        $proc = Start-Process -FilePath $AppExe -PassThru
        Start-Sleep -Seconds 10
        if ($proc.HasExited) { throw "The app exited after start with code $($proc.ExitCode). Run $AppExe to see why." }
        Stop-Process -Id $proc.Id -Force
        Write-Host "The app started and kept running."
    } finally {
        $env:QT_QPA_PLATFORM = $savedQpa
    }
}

# 7) The installer.
if (-not $SkipInstaller) {
    Write-Step "Inno Setup"
    Invoke-Native $Iscc @(
        "/DRepoRoot=$Root",
        "/DAppVersion=$Version",
        "/DDistDir=$AppDir",
        "/DArch=arm64",
        $IssPath
    ) "Inno Setup build"
    if (-not (Test-Path $Installer)) { throw "Inno Setup finished, but $Installer is missing." }
    Write-Host "Built installer: $Installer"
}

Write-Step "Done"
Write-Host "Full app: $AppDir"
if (-not $SkipInstaller) { Write-Host "          $Installer" }
Write-Host "Lite is built on its own: lite\packaging\windows\build_windows_arm64.ps1"
