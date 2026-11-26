@echo off
rem Windows ARM64 installer of the full app; see build_windows_arm64.ps1.
rem Lite: lite\packaging\windows\build_windows_arm64.bat
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build_windows_arm64.ps1" %*
exit /b %ERRORLEVEL%
