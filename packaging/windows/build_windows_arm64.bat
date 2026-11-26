@echo off
rem Windows ARM64 installers of the full app and Lite; see build_windows_arm64.ps1.
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build_windows_arm64.ps1" %*
exit /b %ERRORLEVEL%
