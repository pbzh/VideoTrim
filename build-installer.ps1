# Build the VideoTrim Windows installer end-to-end:
#   1. PyInstaller onedir build (build-windows.ps1)
#   2. Inno Setup compile (installer.iss)
#
# Requires:
#   pip install PyQt6 pyinstaller
#   ffmpeg / ffprobe on PATH (winget install ffmpeg)
#   Inno Setup 6  (winget install JRSoftware.InnoSetup)
#
# Produces: build\installer\VideoTrim-Setup-<version>.exe

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -LiteralPath $PSScriptRoot

# 1. Generate icons (idempotent) then build the app folder.
Write-Host "== Generating icons ==" -ForegroundColor Cyan
python assets\make_icon.py

Write-Host "== Building app (PyInstaller) ==" -ForegroundColor Cyan
& "$PSScriptRoot\build-windows.ps1"

# 2. Locate the Inno Setup compiler (iscc.exe).
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    Write-Error "Inno Setup compiler (ISCC.exe) not found.`nInstall with: winget install JRSoftware.InnoSetup"
    exit 1
}

Write-Host "== Compiling installer (Inno Setup) ==" -ForegroundColor Cyan
Write-Host "Using ISCC: $iscc"
& $iscc "installer.iss"

Write-Host ""
Write-Host "Installer written to: build\installer\" -ForegroundColor Green
Get-ChildItem "build\installer\*.exe" | Select-Object Name, Length
