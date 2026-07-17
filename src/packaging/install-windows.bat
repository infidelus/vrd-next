@echo off
REM ---------------------------------------------------------------------------
REM  VRD Next - Windows installer (Command Prompt friendly)
REM
REM  Double-click this file, or run it from a Command Prompt:
REM      install-windows.bat
REM
REM  It just launches install-windows.ps1 with the right flags so you don't
REM  have to deal with PowerShell's execution policy yourself - the -Bypass
REM  applies only to this single run and changes nothing permanently.  The
REM  PowerShell script pauses at the end itself, so this wrapper doesn't.
REM ---------------------------------------------------------------------------
setlocal
set "HERE=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HERE%install-windows.ps1"
