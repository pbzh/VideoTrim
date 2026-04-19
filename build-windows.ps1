# Build VideoTrim.exe on Windows using PyInstaller.
#
# Requires:
#   pip install PyQt6 pyinstaller
#   ffmpeg and ffprobe on your PATH (e.g. via winget install ffmpeg)
#
# Produces: build\windows-python\dist\VideoTrim\VideoTrim.exe

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Change to the directory that contains this script
Set-Location -LiteralPath $PSScriptRoot

# Locate ffmpeg / ffprobe
$ffmpegCmd  = Get-Command ffmpeg  -ErrorAction SilentlyContinue
$ffprobeCmd = Get-Command ffprobe -ErrorAction SilentlyContinue

if (-not $ffmpegCmd -or -not $ffprobeCmd) {
    Write-Error "ffmpeg / ffprobe not found on PATH.`nInstall with: winget install ffmpeg"
    exit 1
}

$ffmpeg  = $ffmpegCmd.Source
$ffprobe = $ffprobeCmd.Source

Write-Host "Using ffmpeg  : $ffmpeg"
Write-Host "Using ffprobe : $ffprobe"

# Clean previous build artefacts
$distPath = "build\windows-python\dist"
$workPath = "build\windows-python\work"

if (Test-Path $distPath) { Remove-Item -Recurse -Force $distPath }
if (Test-Path $workPath)  { Remove-Item -Recurse -Force $workPath }

# Run PyInstaller
python -m PyInstaller `
    --noconfirm `
    --windowed `
    --onedir `
    --name VideoTrim `
    --distpath $distPath `
    --workpath $workPath `
    --specpath "build\windows-python" `
    --add-binary "${ffmpeg};ffmpeg" `
    --add-binary "${ffprobe};ffmpeg" `
    videotrim.py

Write-Host ""
Write-Host "Built: $distPath\VideoTrim\VideoTrim.exe"
