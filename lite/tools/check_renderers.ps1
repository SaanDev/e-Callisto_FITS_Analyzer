# Starts Lite with each drawing path and reports which one it used.
#
#   auto      - the normal choice (a real GPU when there is one)
#   software  - CALLISTO_LITE_GPU=software: wgpu's software adapter (WARP / llvmpipe)
#   opengl    - CALLISTO_LITE_GPU=opengl: eframe's OpenGL renderer
#   fallback  - WGPU_BACKEND set to a backend this OS lacks, so wgpu finds no
#               adapter and Lite must fall back to OpenGL by itself
#
# Each run opens the window for a few seconds, then closes it.
# usage: powershell -File lite/tools/check_renderers.ps1 [path\to\callisto-lite.exe]

param([string]$Exe = "$PSScriptRoot\..\target\debug\callisto-lite.exe")

$cases = @(
    @{ Name = "auto"; Env = @{} },
    @{ Name = "software"; Env = @{ CALLISTO_LITE_GPU = "software" } },
    @{ Name = "opengl"; Env = @{ CALLISTO_LITE_GPU = "opengl" } },
    @{ Name = "fallback"; Env = @{ WGPU_BACKEND = $(if ($IsMacOS) { "dx12" } else { "metal" }) } }
)

foreach ($case in $cases) {
    $log = [System.IO.Path]::GetTempFileName()
    $saved = @{}
    foreach ($k in $case.Env.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $case.Env[$k]) }
    $p = Start-Process -FilePath $Exe -PassThru -RedirectStandardError $log -WindowStyle Normal
    foreach ($k in $case.Env.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    Start-Sleep -Seconds 6
    $alive = -not $p.HasExited
    if ($alive) { Stop-Process -Id $p.Id -Force }
    $lines = Get-Content $log | Where-Object { $_ -match "callisto-lite:" }
    $chosen = ($lines | Where-Object { $_ -match "drawing with" }) -join "; "
    $status = if ($alive) { "running" } else { "exited ($($p.ExitCode))" }
    Write-Output ("{0,-9} {1,-14} {2}" -f $case.Name, $status, $chosen)
    $lines | Where-Object { $_ -notmatch "drawing with" } | ForEach-Object { Write-Output ("          $_") }
    Remove-Item $log -ErrorAction SilentlyContinue
}
